"""Profile filly frames: GPU time per pass and CPU time per phase.

Subcommands:

    list                  Print the scenarios.
    cpu SCENARIO          Render in this process. Print CPU time per frame phase.
    renderdoc SCENARIO    Capture one frame with RenderDoc, replay it, print GPU time per pass.
    replay CAPTURE        Replay an existing RenderDoc capture and print GPU time per pass.
    nsys SCENARIO         Trace frames with Nsight Systems on the NVIDIA GPU. Print GPU time
                          per frame, GL driver-thread time, and the CPU phases.

See docs/how-to/profile.md.
"""

import argparse
import bisect
import contextlib
import csv
import ctypes
import json
import math
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / ".deps" / "sample-audit" / "assets" / "Models"
ASSETS = {
    "helmet": "DamagedHelmet",
    "clearcoat": "ClearCoatCarPaint",
    "sheen": "SheenChair",
    # Half of each Compare* asset has the extension; the other half is the plain material.
    "aniso": "CompareAnisotropy",
    "specular": "CompareSpecular",
}
# output_path None keeps the build's default, so that builds of earlier revisions ("graded")
# and of the current one ("exact") run the same scenarios.
DEFAULTS = dict(asset=None, host="offscreen", width=1920, height=1080, output_path=None,
                msaa=1, antialiasing="none", shadows=False, tone_mapping=None)
SCENARIOS = {
    "empty": dict(),
    "empty-direct": dict(output_path="direct"),
    "helmet": dict(asset="helmet"),
    "helmet-direct": dict(asset="helmet", output_path="direct"),
    "helmet-shadows": dict(asset="helmet", shadows=True),
    "helmet-msaa4": dict(asset="helmet", msaa=4),
    "helmet-fxaa": dict(asset="helmet", antialiasing="fxaa"),
    # gltf_viewer's default tone mapper, which mixes channels.
    "helmet-aces": dict(asset="helmet", tone_mapping="aces_legacy"),
    "helmet-512": dict(asset="helmet", width=512, height=512),
    "clearcoat": dict(asset="clearcoat"),
    "sheen": dict(asset="sheen"),
    "aniso": dict(asset="aniso"),
    "specular": dict(asset="specular"),
}
# Windows OpenGL on an Optimus laptop: this variable selects the adapter for a process that does
# not export NvOptimusEnablement. python.exe does not export it.
GPU_ENV = {"intel": "0x800000000", "nvidia": "0x800000001"}
RENDERDOC_DIR = Path(os.environ.get("RENDERDOC_DIR", r"C:\Program Files\RenderDoc"))
NSYS = Path(os.environ.get("NSYS", r"C:\Program Files\NVIDIA Corporation\Nsight Systems 2025.3.2"
                           r"\target-windows-x64\nsys.exe"))
REPLAY_PORT = 39921
# RenderDoc replays these actions differently from the live frame: it fills invalidated
# attachments with a pattern, and it times glFinish and presents as actions.
REPLAY_ONLY = ("glInvalidateFramebuffer", "glInvalidateSubFramebuffer", "glFinish", "glFlush",
               "SwapBuffers", "End of Capture", "glDiscardFramebufferEXT")


# ------------------------------------------------------------------------------------------------
# Scenarios


def scenario_spec(args):
    spec = dict(DEFAULTS)
    spec.update(SCENARIOS[args.scenario])
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            spec[key] = value
    if args.size:
        spec["width"], spec["height"] = args.size
    return spec


def asset_path(asset):
    if asset is None:
        return None
    if asset in ASSETS:
        name = ASSETS[asset]
        return MODELS / name / "glTF-Binary" / f"{name}.glb"
    return Path(asset)


def studio_environment():
    import numpy as np

    panorama = np.full((64, 128, 3), 0.12, dtype=np.float32)
    panorama[8:30, 12:28] = (3.0, 2.8, 2.5)
    panorama[12:40, 84:100] = (1.5, 1.8, 2.2)
    return panorama


def build_scene(renderer, spec):
    import numpy as np

    scene = renderer.create_scene()
    scene.background = (0.02, 0.02, 0.03, 1)
    scene.antialiasing = spec["antialiasing"]
    scene.msaa = spec["msaa"]
    scene.shadows = spec["shadows"]
    if spec["output_path"] is not None:
        scene.output_path = spec["output_path"]
    if spec["tone_mapping"] is not None:
        scene.tone_mapping = spec["tone_mapping"]
    model = None
    center = np.zeros(3)
    path = asset_path(spec["asset"])
    if path is not None:
        if not path.is_file():
            raise SystemExit(f"Asset not found: {path}. See docs/how-to/profile.md.")
        model = scene.load(path)
        center = np.asarray(model.bounds, dtype=float).mean(axis=0)
    camera = scene.create_camera()
    aspect = spec["width"] / spec["height"]
    box = np.asarray(model.bounds if model is not None else [[-1, -1, -1], [1, 1, 1]], dtype=float)
    if hasattr(camera, "frame"):
        camera.set_perspective(fov_y=45, near=1, far=2)
        # Fill 1 puts the camera 2.61 sphere radii from the center; results before September 30,
        # 2026 used 2.6 radii and planes at 0.01 and 10 radii.
        camera.frame(box, fill=1.0, direction=(0, 0, -1), aspect=aspect)
    else:
        # Builds without Camera.frame(): the same pose and planes, computed as frame() does.
        radius = float(np.linalg.norm(box[1] - box[0]) / 2)
        t = np.tan(np.radians(22.5)) * min(1.0, aspect)
        distance = radius * np.sqrt(1 + 1 / t**2)
        near, far = max(distance - 1.1 * radius, 0.5 * (distance - radius)), distance + 1.1 * radius
        camera.set_perspective(fov_y=45, near=near, far=far)
        camera.position = tuple(box.mean(axis=0) + (0, 0, distance))
        camera.look_at(tuple(box.mean(axis=0)))
    scene.camera = camera
    light = scene.add_directional_light(direction=(-1, -1, -2), intensity=100000)
    light.casts_shadows = spec["shadows"]
    scene.set_environment(studio_environment(), intensity=30000)
    return scene, model, center


# ------------------------------------------------------------------------------------------------
# Markers: NVTX ranges for Nsight Systems, KHR_debug groups for host GL work


class Markers:
    """NVTX ranges if the nvtx package is importable, and GL debug groups in the host context.

    Filament renders on its own driver thread and context, so these markers cover only the
    Python thread. The GL groups need the host context current.
    """

    def __init__(self, nvtx_enabled, gl_enabled):
        self.nvtx = None
        if nvtx_enabled:
            try:
                import nvtx

                self.nvtx = nvtx
                self.domain = nvtx.get_domain("filly")
            except ImportError:
                print("nvtx is not importable; no NVTX ranges. See docs/how-to/profile.md.",
                      file=sys.stderr)
        self.push_group = self.pop_group = None
        self.gl_enabled = gl_enabled

    def bind_gl(self):
        """Load glPushDebugGroup from the current context. pyglet 1.4 has no binding for it."""
        if not self.gl_enabled or sys.platform != "win32":
            return
        opengl32 = ctypes.WinDLL("opengl32")
        opengl32.wglGetProcAddress.restype = ctypes.c_void_p
        opengl32.wglGetProcAddress.argtypes = [ctypes.c_char_p]
        push = opengl32.wglGetProcAddress(b"glPushDebugGroup")
        pop = opengl32.wglGetProcAddress(b"glPopDebugGroup")
        if push and pop:
            self.push_group = ctypes.WINFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
                                                 ctypes.c_char_p)(push)
            self.pop_group = ctypes.WINFUNCTYPE(None)(pop)

    @contextlib.contextmanager
    def range(self, name, gl=False):
        if self.nvtx:
            self.domain.push_range(self.domain.get_event_attributes(message=name))
        if gl and self.push_group:
            self.push_group(0x824A, 0, -1, name.encode())  # GL_DEBUG_SOURCE_APPLICATION
        try:
            yield
        finally:
            if gl and self.pop_group:
                self.pop_group()
            if self.nvtx:
                self.domain.pop_range()


# ------------------------------------------------------------------------------------------------
# GL timestamps in the host context


class HostGpuTimer:
    """GL_TIMESTAMP queries in the host context at three points of each frame.

    ARB_timer_query is core since GL 3.3 and works in compatibility contexts, such as those of
    PsychoPy and pyglet 1.4 (4.6 compatibility on the tested Intel driver). Results are read
    SLOTS - 1 frames later and only if available, so the queries never stall the pipeline.

    Points: "start" before render(), "acquired" after acquire() enters, "drawn" after the host
    draw. The GPU records a timestamp when it executes the query, so "start" is flushed at once;
    the others are flushed by the release fence. acquire() makes the host queue wait
    (glWaitSync) for Filament's fence, so the GPU reaches "acquired" only after Filament's frame
    completes. start -> acquired is therefore Filament's frame on the GPU timeline, including
    driver-thread submission latency and GPU queueing: an upper bound on Filament's GPU time.
    acquired -> drawn is the GPU time of the host draw.
    """

    SLOTS = 4
    POINTS = ("start", "acquired", "drawn")

    def __init__(self):
        from pyglet import gl

        self.gl = gl
        count = self.SLOTS * len(self.POINTS)
        self.queries = (gl.GLuint * count)()
        gl.glGenQueries(count, self.queries)
        self.frame = 0
        self.pending = {}
        self.results = {}
        self.missed = 0

    def mark(self, point):
        slot = self.frame % self.SLOTS
        self.gl.glQueryCounter(self.queries[slot * len(self.POINTS) + self.POINTS.index(point)],
                               self.gl.GL_TIMESTAMP)
        if point == "start":
            self.gl.glFlush()

    def end_frame(self, frame_index):
        gl = self.gl
        self.pending[self.frame % self.SLOTS] = frame_index
        self.frame += 1
        slot = self.frame % self.SLOTS  # the oldest slot, reused next frame
        if slot not in self.pending:
            return
        base = slot * len(self.POINTS)
        available = gl.GLuint(0)
        gl.glGetQueryObjectuiv(self.queries[base + len(self.POINTS) - 1], gl.GL_QUERY_RESULT_AVAILABLE,
                               ctypes.byref(available))
        index = self.pending.pop(slot)
        if not available.value:
            self.missed += 1
            return
        stamps = []
        for i in range(len(self.POINTS)):
            value = gl.GLuint64(0)
            gl.glGetQueryObjectui64v(self.queries[base + i], gl.GL_QUERY_RESULT, ctypes.byref(value))
            stamps.append(value.value)
        self.results[index] = {
            "gpu_filament_frame": (stamps[1] - stamps[0]) * 1e-6,
            "gpu_host_draw": (stamps[2] - stamps[1]) * 1e-6,
        }

    def close(self):
        self.gl.glDeleteQueries(self.SLOTS * len(self.POINTS), self.queries)


# ------------------------------------------------------------------------------------------------
# RenderDoc in-application API


class RenderDocApi:
    """The subset of RENDERDOC_API_1_6_0 that frame capture needs.

    It works only when RenderDoc launched or injected this process. StartFrameCapture(NULL, NULL)
    matches any context. Filament renders on its driver thread, so the caller waits for that
    thread (Renderer.finish()) before each call.
    """

    def __init__(self, template):
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        if not kernel32.GetModuleHandleW("renderdoc.dll"):
            raise SystemExit("renderdoc.dll is not loaded. Use the renderdoc subcommand.")
        dll = ctypes.WinDLL("renderdoc.dll")
        pointer = ctypes.c_void_p()
        if not dll.RENDERDOC_GetAPI(10600, ctypes.byref(pointer)):
            raise SystemExit("RENDERDOC_GetAPI(1.6.0) failed")
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p * 27)).contents

        def function(index, result, *arguments):
            return ctypes.CFUNCTYPE(result, *arguments)(table[index])

        function(11, None, ctypes.c_char_p)(str(template).encode())  # SetCaptureFilePathTemplate
        self.start = lambda: function(19, None, ctypes.c_void_p, ctypes.c_void_p)(None, None)
        self._end = function(21, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)
        self._count = function(13, ctypes.c_uint32)
        self._get = function(14, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p,
                             ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64))

    def end(self):
        if not self._end(None, None):
            raise RuntimeError("RenderDoc EndFrameCapture failed")
        length = ctypes.c_uint32(0)
        index = self._count() - 1
        self._get(index, None, ctypes.byref(length), None)
        path = ctypes.create_string_buffer(length.value)
        self._get(index, path, ctypes.byref(length), None)
        return path.value.decode()


# ------------------------------------------------------------------------------------------------
# Hosts


class OffscreenHost:
    shared = False

    def __init__(self, spec, args):
        import filly

        self.renderer = filly.Renderer()
        self.target = self.renderer.create_render_target(width=spec["width"], height=spec["height"])
        self.period = 1 / args.rate if args.rate else 0
        self.deadline = None
        self.gl_renderer = "Filament context (adapter per SHIM_MCCOMPAT or driver default)"

    def make_current(self):
        pass

    def present(self):
        # Offscreen rendering has no display to pace it. Sleep to the next period, as a display
        # at this rate would, so that the CPU does not run ahead of the GPU.
        if not self.period:
            return
        now = time.perf_counter()
        self.deadline = now if self.deadline is None else self.deadline + self.period
        if self.deadline > now:
            time.sleep(self.deadline - now)
        else:
            self.deadline = now

    def close(self):
        self.target.close()
        self.renderer.close()


def gl_renderer_string():
    from pyglet import gl

    return ctypes.cast(gl.glGetString(gl.GL_RENDERER), ctypes.c_char_p).value.decode()


class PygletHost:
    shared = True

    def __init__(self, spec, args):
        import pyglet
        from filly.integrations.pyglet import SharedTarget, create_renderer

        self.window = pyglet.window.Window(spec["width"], spec["height"], caption="filly profile",
                                           vsync=args.vsync)
        self.window.switch_to()
        self.gl_renderer = gl_renderer_string()
        self.renderer = create_renderer(self.window)
        self.target = SharedTarget(self.renderer, self.window, spec["width"], spec["height"])

    def make_current(self):
        self.window.switch_to()

    def draw(self):
        self.target.draw()

    def present(self):
        self.window.flip()
        self.window.dispatch_events()

    def close(self):
        self.window.switch_to()
        self.target.close()
        self.renderer.close()
        self.window.close()


class PsychopyHost:
    shared = True

    def __init__(self, spec, args):
        from psychopy import visual
        from filly.integrations.psychopy import SharedTarget, create_renderer

        self.win = visual.Window(size=(spec["width"], spec["height"]), units="pix", winType="pyglet",
                                 useFBO=False, checkTiming=False, autoLog=False,
                                 waitBlanking=args.vsync)
        self.win.flip()
        self.gl_renderer = gl_renderer_string()
        self.renderer = create_renderer(self.win)
        self.target = SharedTarget(self.renderer, self.win, spec["width"], spec["height"])
        self.stimulus = self.target.as_psychopy_texture(self.win)

    def make_current(self):
        self.win.winHandle.switch_to()

    def draw(self):
        self.stimulus.draw()

    def present(self):
        self.win.flip()

    def close(self):
        self.target.close()
        self.renderer.close()
        self.win.close()


HOSTS = {"offscreen": OffscreenHost, "pyglet": PygletHost, "psychopy": PsychopyHost}


# ------------------------------------------------------------------------------------------------
# The frame loop and the CPU breakdown

PHASES = (
    ("update", "update (Python scene edits)"),
    ("render", "render() wall"),
    ("submit", "  native submit (Stats.cpu_submit_ms)"),
    ("binding", "  outside native submit"),
    ("acquire", "acquire() enter wall"),
    ("host_wait", "  host wait (Stats.host_wait_ms)"),
    ("draw", "host draw"),
    ("release", "acquire() exit wall"),
    ("host_release", "  host release (Stats.host_release_ms)"),
    ("present", "present (flip or pacing sleep)"),
    ("finish", "finish() (GPU completion wait)"),
    ("busy", "CPU busy (frame minus present/finish)"),
    ("interval", "frame interval"),
    ("gpu_filament_frame", "GPU: start -> acquired (Filament frame)"),
    ("gpu_host_draw", "GPU: host draw"),
)
GPU_KEYS = ("gpu_filament_frame", "gpu_host_draw")


def run_frames(args):
    import filly

    spec = scenario_spec(args)
    filly.set_log_level(args.log_level)
    markers = Markers(args.nvtx, gl_enabled=True)
    host = HOSTS[spec["host"]](spec, args)
    timer = None
    if host.shared:
        markers.bind_gl()
        if args.gl_timers:
            timer = HostGpuTimer()
    renderdoc = RenderDocApi(Path(args.renderdoc_capture)) if args.renderdoc_capture else None
    scene, model, center = build_scene(host.renderer, spec)
    renderer, target = host.renderer, host.target
    rows, captures = [], []
    clock = time.perf_counter
    total = args.warmup + args.frames
    previous = None
    mark = timer.mark if timer else (lambda point: None)
    frame_info = {} if args.frame_info else None
    if args.frame_info and not hasattr(renderer, "_frame_info_history"):
        raise SystemExit("--frame-info needs a build with Renderer._frame_info_history "
                         "(tools/profile_tracy.py build).")
    try:
        for index in range(total):
            capture = renderdoc is not None and index == args.warmup
            if capture:
                # Only this frame's commands belong to the capture.
                renderer.finish()
                renderdoc.start()
            # The timestamp and its glFlush stay outside the timed phases.
            mark("start")
            with markers.range("frame"):
                t0 = clock()
                with markers.range("update"):
                    if model is not None:
                        model.rotation_euler_deg = (0, (index * args.spin) % 360, 0)
                t1 = clock()
                with markers.range("render"):
                    renderer.render(scene, target)
                t2 = clock()
                if frame_info is not None:
                    # Filament reports a frame one or more frames later; keep the newest value.
                    for entry in renderer._frame_info_history():
                        if entry[1] > 0:
                            frame_info[entry[0]] = entry[1] * 1e-6
                stats = renderer.stats
                submit = stats.cpu_submit_ms
                t3 = t4 = t5 = t2
                if host.shared:
                    with markers.range("acquire"):
                        acquisition = target.acquire()
                        acquisition.__enter__()
                    t3 = clock()
                    mark("acquired")
                    with markers.range("host draw", gl=True):
                        host.draw()
                    mark("drawn")
                    t4 = clock()
                    with markers.range("release"):
                        acquisition.__exit__(None, None, None)
                    t5 = clock()
                    stats = renderer.stats
                with markers.range("present", gl=host.shared):
                    host.present()
                t6 = clock()
                if timer:
                    timer.end_frame(index)
                if args.sync == "finish" or capture:
                    with markers.range("finish"):
                        renderer.finish()
                t7 = clock()
            if capture:
                captures.append(renderdoc.end())
            if index >= args.warmup and not capture:
                ms = 1e3
                rows.append({
                    "frame": index,
                    "update": (t1 - t0) * ms,
                    "render": (t2 - t1) * ms,
                    "submit": submit,
                    "binding": (t2 - t1) * ms - submit,
                    "acquire": (t3 - t2) * ms if host.shared else math.nan,
                    "host_wait": stats.host_wait_ms if host.shared else math.nan,
                    "draw": (t4 - t3) * ms if host.shared else math.nan,
                    "release": (t5 - t4) * ms if host.shared else math.nan,
                    "host_release": stats.host_release_ms if host.shared else math.nan,
                    "present": (t6 - t5) * ms,
                    "finish": (t7 - t6) * ms if args.sync == "finish" else math.nan,
                    "busy": (t5 - t0) * ms,
                    "interval": (t0 - previous) * ms if previous is not None else math.nan,
                })
            # The interval after a capture includes RenderDoc's serialization.
            previous = None if capture else t0
    finally:
        if timer:
            host.make_current()
            timer.close()
        host.close()
    for row in rows:
        row.update(dict.fromkeys(GPU_KEYS, math.nan))
        if timer and row["frame"] in timer.results:
            row.update(timer.results[row["frame"]])
    host.timer_missed = timer.missed if timer else 0
    # Filament's frame ids count beginFrame() calls from 1, one per render() here.
    host.frame_info = [ms for frame_id, ms in sorted((frame_info or {}).items())
                       if frame_id > args.warmup]
    return spec, host, rows, captures


def percentile(values, q):
    values = sorted(v for v in values if not math.isnan(v))
    if not values:
        return math.nan
    position = (len(values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def describe(spec, args):
    return (f"{args.scenario}: host={spec['host']} {spec['width']}x{spec['height']} "
            f"output_path={spec['output_path'] or 'default'} msaa={spec['msaa']} "
            f"antialiasing={spec['antialiasing']} shadows={spec['shadows']} "
            f"tone_mapping={spec['tone_mapping'] or 'default'} "
            f"asset={spec['asset'] or 'none'}")


def cpu_table(spec, host, rows, args):
    lines = [describe(spec, args),
             f"GL renderer: {host.gl_renderer}; frames={len(rows)} after warmup={args.warmup}; "
             f"sync={args.sync}; " + (f"rate={args.rate} Hz" if spec["host"] == "offscreen"
                                      else f"vsync={args.vsync}"),
             "", f"{'phase (ms)':40s} {'p50':>8s} {'p95':>8s} {'p99':>8s} {'max':>8s}"]
    for key, label in PHASES:
        values = [row[key] for row in rows]
        if all(math.isnan(v) for v in values):
            continue
        cells = [percentile(values, q) for q in (0.5, 0.95, 0.99, 1.0)]
        lines.append(f"{label:40s} " + " ".join(f"{c:8.3f}" for c in cells))
    if spec["host"] != "offscreen":
        lines.append(f"GPU rows: host-context GL_TIMESTAMP queries; frames without results: "
                     f"{host.timer_missed}")
    if getattr(host, "frame_info", None):
        values = host.frame_info
        lines.append(f"{'GPU: Filament FrameInfo gpuFrameDuration':40s} "
                     + " ".join(f"{percentile(values, q):8.3f}" for q in (0.5, 0.95, 0.99, 1.0))
                     + f"  ({len(values)} frames)")
    elif args.frame_info:
        lines.append("GPU: Filament FrameInfo reported no frames (unthrottled loop?)")
    intervals = [row["interval"] for row in rows if not math.isnan(row["interval"])]
    if intervals:
        typical = statistics.median(intervals)
        late = sum(1 for v in intervals if v > 1.5 * typical)
        lines.append(f"\nframe intervals above 1.5 x median ({typical:.3f} ms): {late} of {len(intervals)}")
    return "\n".join(lines)


def cmd_cpu(args):
    if args.nvtx_site:
        sys.path.insert(0, args.nvtx_site)
    spec, host, rows, captures = run_frames(args)
    table = cpu_table(spec, host, rows, args)
    if args.summary_json:
        summary = {"spec": spec, "gl_renderer": host.gl_renderer, "frames": len(rows),
                   "timer_missed": host.timer_missed, "phases": {}}
        for key, _ in PHASES:
            values = [row[key] for row in rows]
            if not all(math.isnan(v) for v in values):
                summary["phases"][key] = {f"p{int(q * 100)}": percentile(values, q) for q in (0.5, 0.95, 0.99)}
                summary["phases"][key]["max"] = percentile(values, 1.0)
        if getattr(host, "frame_info", None):
            summary["phases"]["frame_info"] = {f"p{int(q * 100)}": percentile(host.frame_info, q)
                                               for q in (0.5, 0.95, 0.99)}
            summary["phases"]["frame_info"]["n"] = len(host.frame_info)
        intervals = [row["interval"] for row in rows if not math.isnan(row["interval"])]
        if intervals:
            typical = statistics.median(intervals)
            summary["late_frames"] = sum(1 for v in intervals if v > 1.5 * typical)
        Path(args.summary_json).write_text(json.dumps(summary, indent=1))
    print(table)
    if args.report:
        Path(args.report).write_text(table + "\n")
    if args.csv:
        with open(args.csv, "w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    for path in captures:
        print(f"RenderDoc capture: {path}")


# ------------------------------------------------------------------------------------------------
# RenderDoc: capture in a child process, replay in qrenderdoc


def scenario_argv(args):
    """Rebuild the scenario and loop options for a child process."""
    argv = [args.scenario, "--frames", str(args.frames), "--warmup", str(args.warmup),
            "--rate", str(args.rate), "--sync", args.sync, "--spin", str(args.spin),
            "--log-level", args.log_level]
    for key in ("asset", "host", "output_path", "msaa", "antialiasing", "tone_mapping"):
        value = getattr(args, key)
        if value is not None:
            argv += ["--" + key.replace("_", "-"), str(value)]
    if args.shadows is not None:
        argv.append("--shadows" if args.shadows else "--no-shadows")
    if args.size:
        argv += ["--size", f"{args.size[0]}x{args.size[1]}"]
    if not args.vsync:
        argv.append("--no-vsync")
    if not args.gl_timers:
        argv.append("--no-gl-timers")
    return argv


def gpu_env(gpu):
    env = dict(os.environ)
    if gpu in GPU_ENV:
        env["SHIM_MCCOMPAT"] = GPU_ENV[gpu]
    return env


def output_dir(args, kind):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "filly-profile"
    path = base / f"{kind}-{args.scenario}-{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def replay_server_exe(tools_dir):
    """A renamed copy of renderdoccmd.

    The NVIDIA driver profile for renderdoccmd.exe, like qrenderdoc's NvOptimusEnablement export,
    moves the replay to the NVIDIA GPU. A copy under another name gets the adapter that
    SHIM_MCCOMPAT or the driver default selects.
    """
    tools_dir.mkdir(parents=True, exist_ok=True)
    exe = tools_dir / "filly-rdc-replay.exe"
    source = RENDERDOC_DIR / "renderdoccmd.exe"
    if not exe.exists() or exe.stat().st_mtime < source.stat().st_mtime:
        shutil.copy2(source, exe)
        for item in RENDERDOC_DIR.iterdir():
            if item.suffix.lower() in (".dll", ".json", ".yes"):
                shutil.copy2(item, tools_dir / item.name)
        if (RENDERDOC_DIR / "plugins").is_dir():
            shutil.copytree(RENDERDOC_DIR / "plugins", tools_dir / "plugins", dirs_exist_ok=True)
    return exe


def replay(capture, out_dir, args):
    """Replay a capture on the chosen GPU. Return the parsed JSON."""
    json_path = out_dir / (Path(capture).stem + ".replay.json")
    env = dict(os.environ, FILLY_RDC_CAPTURE=str(capture), FILLY_RDC_JSON=str(json_path),
               FILLY_RDC_REPEAT=str(args.repeat))
    server = None
    if args.replay_gpu != "qrenderdoc":
        exe = replay_server_exe(Path(args.tools_dir))
        server = subprocess.Popen([str(exe), "remoteserver", "-h", "localhost", "-p", str(REPLAY_PORT)],
                                  env=gpu_env(args.replay_gpu), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        env["FILLY_RDC_REMOTE"] = f"localhost:{REPLAY_PORT}"
        time.sleep(1.5)
    try:
        subprocess.run([str(RENDERDOC_DIR / "qrenderdoc.exe"), "--python",
                        str(ROOT / "tools" / "profile_rdc_replay.py")], env=env, timeout=args.timeout)
    finally:
        if server is not None:
            server.kill()
            server.wait()
            # The server keeps a copy of every capture it opened; a capture can be 100 MB.
            for copy in (Path(tempfile.gettempdir()) / "RenderDoc").glob("filly-rdc-replay_*_remotecopy_*.rdc"):
                with contextlib.suppress(OSError):
                    copy.unlink()
    error = Path(str(json_path) + ".error")
    if error.exists():
        raise SystemExit("Replay failed:\n" + error.read_text())
    if not json_path.exists():
        raise SystemExit(f"Replay wrote no result: {json_path}")
    result = json.loads(json_path.read_text())
    result["replay_gpu"] = args.replay_gpu
    return result


def is_replay_only(action):
    return action["name"].startswith(REPLAY_ONLY) or "Present" in action["flags"]


def pass_key(action):
    """Group by Filament's frame-graph pass, one entry per Filament frame."""
    path, events = action["path"], action.get("path_events", [])
    if path and path[0] == "FrameGraph":
        frame = events[0] if events else 0
        return frame, path[1] if len(path) > 1 else "FrameGraph"
    return (events[0] if events else action["event"]), "/".join(path) or "(outside debug groups)"


def gpu_tables(result, detail):
    actions = result["actions"]
    timed = [a for a in actions if a["gpu_ms"] is not None and not is_replay_only(a)]
    frames = sorted({pass_key(a)[0] for a in timed if a["path"][:1] == ["FrameGraph"]})
    frame_number = {event: i for i, event in enumerate(frames)}
    passes, order = {}, []
    for action in timed:
        frame, name = pass_key(action)
        label = f"F{frame_number[frame]} {name}" if frame in frame_number else name
        entry = passes.get(label)
        if entry is None:
            entry = passes[label] = {"ms": 0.0, "draws": 0, "other": 0, "targets": set()}
            order.append(label)
        entry["ms"] += action["gpu_ms"]
        if "Drawcall" in action["flags"]:
            entry["draws"] += 1
        else:
            entry["other"] += 1
        if action["target"]:
            entry["targets"].add(action["target"])
        elif action["depth"]:
            entry["targets"].add("depth " + action["depth"])
    total =sum(p["ms"] for p in passes.values()) or math.nan
    lines = [f"Replay: {result['repeat']} timed replays, median per action; replay GPU "
             f"{result['replay_gpu']} ({'remote server' if result.get('remote') else 'qrenderdoc'})",
             f"Capture: {result['capture']}", "",
             f"{'pass (F<n> = Filament frame)':34s} {'GPU ms':>8s} {'share':>6s} {'draws':>5s} "
             f"{'other':>5s}  targets"]
    for label in order:
        p = passes[label]
        lines.append(f"{label[:34]:34s} {p['ms']:8.3f} {100 * p['ms'] / total:5.1f}% {p['draws']:5d} "
                     f"{p['other']:5d}  {'; '.join(sorted(p['targets']))[:70]}")
    lines.append(f"{'total (sum of timed actions)':34s} {total:8.3f}")
    skipped = [a for a in actions if a["gpu_ms"] is not None and is_replay_only(a)]
    if skipped:
        lines.append(f"excluded replay-only actions: {len(skipped)} ("
                     + ", ".join(sorted({a['name'].split('(')[0] for a in skipped})) + ")")
    shaders = {}
    for action in timed:
        if action.get("shader"):
            key = (action["shader"], action["shader_label"])
            ms, count = shaders.get(key, (0.0, 0))
            shaders[key] = (ms + action["gpu_ms"], count + 1)
    if shaders:
        lines += ["", f"{'pixel shader':>12s} {'GPU ms':>8s} {'draws':>5s}  features [samplers]"]
        for (shader, label), (ms, count) in sorted(shaders.items(), key=lambda item: -item[1][0]):
            lines.append(f"{shader:>12s} {ms:8.3f} {count:5d}  {label[:110]}")
    if detail:
        lines += ["", f"{'event':>6s} {'GPU ms':>8s}  {'pass':24s} {'action':28s} target"]
        for action in actions:
            if action["gpu_ms"] is None:
                continue
            mark = " (replay only)" if is_replay_only(action) else ""
            lines.append(f"{action['event']:6d} {action['gpu_ms']:8.3f}  {pass_key(action)[1][:24]:24s} "
                         f"{action['name'][:28]:28s} {action['target']}{mark}")
    return "\n".join(lines)


def cmd_renderdoc(args):
    host = scenario_spec(args)["host"]
    if host == "psychopy":
        raise SystemExit("RenderDoc cannot capture in a PsychoPy process: StartFrameCapture does not "
                         "start (tested with RenderDoc 1.39). Profile PsychoPy with the cpu or nsys "
                         "subcommands, and Filament's passes with an offscreen capture.")
    if host != "offscreen":
        print("warning: RenderDoc supports only core-profile GL contexts, and the host context is "
              "a compatibility context. A pyglet capture worked in validation; it is not "
              "guaranteed. Filament's passes are the same offscreen.", file=sys.stderr)
    out_dir = output_dir(args, "renderdoc")
    template = out_dir / "frame"
    child = [sys.executable, str(Path(__file__).resolve()), "cpu", *scenario_argv(args),
             "--renderdoc-capture", str(template)]
    capture_cmd = [str(RENDERDOC_DIR / "renderdoccmd.exe"), "capture", "--opt-hook-children", "-w",
                   "-d", str(ROOT), "-c", str(template), *child]
    print("capture:", subprocess.list2cmdline(capture_cmd), flush=True)
    log = out_dir / "capture.log"
    with open(log, "w") as stream:
        completed = subprocess.run(capture_cmd, env=gpu_env(args.gpu), stdout=stream,
                                   stderr=subprocess.STDOUT, timeout=args.timeout)
    captures = sorted(out_dir.glob("frame*.rdc"), key=lambda p: p.stat().st_mtime)
    if completed.returncode or not captures:
        raise SystemExit(f"Capture failed (exit {completed.returncode}); see {log}")
    text = log.read_text(errors="replace")
    cpu = text[text.find(args.scenario + ": host="):] if args.scenario + ": host=" in text else ""
    report = gpu_tables(replay(captures[-1], out_dir, args), args.detail)
    report = describe(scenario_spec(args), args) + f"; capture GPU {args.gpu}\n" + report
    report += ("\n\nCPU phases of the same run, with RenderDoc attached (overhead included):\n"
               + cpu.split("RenderDoc capture:")[0].strip()) if cpu else ""
    (out_dir / "report.txt").write_text(report)
    print(report)
    print(f"\nWrote {out_dir}")


def cmd_replay(args):
    out_dir = Path(args.capture).resolve().parent
    print(gpu_tables(replay(Path(args.capture).resolve(), out_dir, args), args.detail))


# ------------------------------------------------------------------------------------------------
# Nsight Systems


def nsys_stats(report, base, names):
    """Run `nsys stats` for each report and return {name: rows}."""
    subprocess.run([str(NSYS), "stats", "--force-export=true", "--format", "csv", "--output", str(base),
                    "--report", ",".join(names), str(report)], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    tables = {}
    for name in names:
        path = Path(f"{base}_{name}.csv")
        if path.exists():
            with open(path, newline="") as stream:
                tables[name] = list(csv.DictReader(stream))
    return tables


def nsys_tables(sqlite_path, tables, frames):
    """Summarize the GL trace of the last `frames` Filament frames from the SQLite export.

    Without GL debug groups (the drivers do not expose GL_EXT_debug_marker, so Filament emits
    none), nsys brackets GPU work by the GL calls around it. GPU ranges of Filament's context
    are grouped into frames by the wglMakeCurrent that starts each frame on the driver thread.
    """
    db = sqlite3.connect(str(sqlite_path))
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    text = dict(db.execute("SELECT id, value FROM StringIds"))
    threads = dict((tid, text.get(name, "")) for name, tid in db.execute(
        "SELECT nameId, globalTid FROM ThreadNames")) if "ThreadNames" in names else {}
    if "NVTX_EVENTS" in names:
        for (tid,) in db.execute("SELECT DISTINCT globalTid FROM NVTX_EVENTS"):
            threads.setdefault(tid, "Python main thread")
    ms = 1e-6
    lines = []
    if "OPENGL_WORKLOAD" not in names or "OPENGL_API" not in names:
        return "No OpenGL trace in the report. Did the process render on the NVIDIA GPU?"
    # The CPU time of the GL call that opens each GPU range.
    workload = list(db.execute(
        "SELECT w.start, w.end, w.nameId, w.endNameId, w.contextId, w.globalTid, a.start "
        "FROM OPENGL_WORKLOAD w LEFT JOIN OPENGL_API a ON a.correlationId = w.correlationId "
        "ORDER BY w.start"))
    driver_calls = [(start, text.get(name)) for start, name, tid in db.execute(
        "SELECT start, nameId, globalTid FROM OPENGL_API ORDER BY start") if threads.get(tid) == "FEngine::loop"]
    # Filament's driver thread makes the swap chain current at the start of every frame
    # (wglMakeCurrent), so that call splits its GPU ranges into frames, whether the frame
    # ends in SwapBuffers or, with the headless-swap fix, in glFlush. Host contexts are split
    # at their own SwapBuffers.
    frame_starts = [t for t, name in driver_calls if name == "wglMakeCurrent"]
    filament_end = "SwapBuffers" if any(name == "SwapBuffers" for _, name in driver_calls) else "glFlush"
    contexts = {}
    for start, end, first, last, context, tid, api_start in workload:
        thread = threads.get(tid, "host (Python thread)")
        entry = contexts.setdefault(context, {"thread": thread, "groups": [], "current": [], "frame": None})
        item = (start, end, text.get(first, "?"), text.get(last, "?"))
        if thread == "FEngine::loop":
            frame = bisect.bisect_right(frame_starts, api_start if api_start is not None else start)
            if entry["frame"] is not None and frame != entry["frame"] and entry["current"]:
                entry["groups"].append(entry["current"])
                entry["current"] = []
            entry["frame"] = frame
            entry["current"].append(item)
        else:
            entry["current"].append(item)
            if item[3] == "SwapBuffers":
                entry["groups"].append(entry["current"])
                entry["current"] = []
    filament = [c for c in contexts.values() if c["thread"] == "FEngine::loop"]
    if not filament or not filament[0]["groups"]:
        return f"No GPU ranges from Filament's driver thread that end at {filament_end}."
    lines.append(f"Filament frames end at {filament_end} on the driver thread.")
    groups = []
    for context in sorted(contexts.values(), key=lambda c: c["thread"] != "FEngine::loop"):
        context_groups = context["groups"][-frames:]
        if not context_groups:
            continue
        if context["thread"] == "FEngine::loop":
            groups = context_groups
        busy = [sum(e - s for s, e, _, _ in g) * ms for g in context_groups]
        span = [(g[-1][1] - g[0][0]) * ms for g in context_groups]
        lines.append(f"GPU (NVIDIA workload trace), context of {context['thread']}, "
                     f"last {len(context_groups)} frames:")
        lines.append(f"  {'metric (ms)':38s} {'p50':>8s} {'p95':>8s} {'max':>8s}")
        for label, values in (("GPU busy per frame (sum of ranges)", busy),
                              ("GPU span per frame (first to last)", span)):
            lines.append(f"  {label:38s} {percentile(values, 0.5):8.3f} {percentile(values, 0.95):8.3f} "
                         f"{max(values):8.3f}")
        # Ranges by position in the frame stand in for passes. The GL calls that bracket a
        # range name its first and last command, for example a draw and a framebuffer switch.
        counts = {}
        for g in context_groups:
            counts[len(g)] = counts.get(len(g), 0) + 1
        modal = max(counts, key=counts.get)
        same = [g for g in context_groups if len(g) == modal]
        lines.append(f"  GPU ranges by position ({len(same)} of {len(context_groups)} frames have "
                     f"{modal} ranges):")
        for i in range(modal):
            values = [(g[i][1] - g[i][0]) * ms for g in same]
            lines.append(f"  {i:3d} {percentile(values, 0.5):8.3f} ms  {same[0][i][2]} -> {same[0][i][3]}")
        lines.append("")
    # CPU: GL calls per thread inside the measured window.
    window = (groups[0][0][0], groups[-1][-1][1])
    calls = list(db.execute("SELECT start, end, nameId, globalTid FROM OPENGL_API WHERE start >= ? AND end <= ?",
                            window))
    per_thread = {}
    for start, end, name, tid in calls:
        entry = per_thread.setdefault(tid, {})
        total, count, worst = entry.get(text.get(name, "?"), (0, 0, 0))
        entry[text.get(name, "?")] = (total + end - start, count + 1, max(worst, end - start))
    lines.append(f"\nGL calls per thread in the measured window, per frame (mean of {len(groups)} frames):")
    for tid, entry in sorted(per_thread.items(), key=lambda item: -sum(v[0] for v in item[1].values())):
        total = sum(v[0] for v in entry.values()) * ms / len(groups)
        lines.append(f"  thread {threads.get(tid, hex(tid))}: {total:.3f} ms per frame in GL calls")
        for name, (t, c, worst) in sorted(entry.items(), key=lambda item: -item[1][0])[:8]:
            lines.append(f"    {name:28s} {t * ms / len(groups):8.3f} ms  {c / len(groups):6.1f} calls  "
                         f"max {worst * ms:7.3f} ms")
    # Latency from the end of render() on the Python thread to the next SwapBuffers on
    # Filament's driver thread, which ends that frame.
    if "NVTX_EVENTS" in names:
        renders = [end for start, end, message in db.execute(
            "SELECT start, end, COALESCE(text, (SELECT value FROM StringIds WHERE id = textId)) "
            "FROM NVTX_EVENTS WHERE end IS NOT NULL ORDER BY start") if message == "render"]
        swaps = sorted(end for start, end, name, tid in db.execute(
            "SELECT start, end, nameId, globalTid FROM OPENGL_API")
            if text.get(name) == filament_end and threads.get(tid) == "FEngine::loop")
        lags = []
        for end in renders[-frames:]:
            later = [s for s in swaps if s >= end]
            if later:
                lags.append((later[0] - end) * ms)
        if lags:
            lines.append(f"\nrender() return -> driver-thread {filament_end} end (ms): "
                         f"p50 {percentile(lags, 0.5):.3f}  p95 {percentile(lags, 0.95):.3f}  "
                         f"max {max(lags):.3f}")
    db.close()
    for name, rows in tables.items():
        if not rows:
            continue
        lines.append(f"\n{name}:")
        keys = [k for k in rows[0] if k in ("Range", "Name", "Time (%)", "Total Time (ns)", "Instances",
                                             "Med (ns)", "Max (ns)")]
        lines.append("  " + " | ".join(keys))
        for row in rows[:12]:
            lines.append("  " + " | ".join(row[k] for k in keys))
    return "\n".join(lines)


def cmd_nsys(args):
    out_dir = output_dir(args, "nsys")
    base = out_dir / "trace"
    cpu_report = out_dir / "cpu.txt"
    child = [sys.executable, str(Path(__file__).resolve()), "cpu", *scenario_argv(args), "--nvtx",
             "--report", str(cpu_report), "--csv", str(out_dir / "cpu.csv")]
    env = gpu_env(args.gpu)
    if args.nvtx_site:
        env["PYTHONPATH"] = args.nvtx_site + os.pathsep + env.get("PYTHONPATH", "")
    command = [str(NSYS), "profile", "--trace=" + args.nsys_trace, "--opengl-gpu-workload=true",
               "--sample=none", "--cpuctxsw=none", "--force-overwrite=true", "-o", str(base), *child]
    print("trace:", subprocess.list2cmdline(command), flush=True)
    log = out_dir / "trace.log"
    with open(log, "w") as stream:
        completed = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                   timeout=args.timeout)
    report = base.with_suffix(".nsys-rep")
    if not report.exists():
        raise SystemExit(f"nsys failed (exit {completed.returncode}); see {log}")
    if completed.returncode:
        # The PsychoPy process crashed at exit under nsys (0xC0000005) after writing its
        # report; the trace is complete.
        print(f"warning: exit code {completed.returncode:#x}; see {log}", file=sys.stderr)
    stats = nsys_stats(report, base, ["nvtx_pushpop_sum", "opengl_khr_gpu_range_sum"])
    summary = nsys_tables(base.with_suffix(".sqlite"), stats, args.frames)
    cpu = cpu_report.read_text() if cpu_report.exists() else "(the child wrote no CPU report)"
    output = (describe(scenario_spec(args), args) + f"; GPU {args.gpu}\n" + summary
              + "\n\nCPU phases (NVTX and nsys overhead included):\n" + cpu)
    (out_dir / "report.txt").write_text(output)
    print(output)
    print(f"\nWrote {out_dir}")


def size(value):
    width, _, height = value.lower().partition("x")
    return int(width), int(height)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="print the scenarios")

    scenario = argparse.ArgumentParser(add_help=False)
    scenario.add_argument("scenario", choices=sorted(SCENARIOS))
    scenario.add_argument("--asset", help="helmet, clearcoat, sheen, or a glTF path (overrides the scenario)")
    scenario.add_argument("--host", choices=sorted(HOSTS))
    scenario.add_argument("--size", type=size, help="WIDTHxHEIGHT of the target")
    scenario.add_argument("--output-path", choices=("exact", "direct", "graded"),
                          help="graded exists only in builds before September 30, 2026")
    scenario.add_argument("--msaa", type=int, choices=(1, 2, 4, 8))
    scenario.add_argument("--antialiasing", choices=("none", "fxaa"))
    scenario.add_argument("--tone-mapping", help="Scene tone mapper, for example aces_legacy")
    scenario.add_argument("--shadows", action=argparse.BooleanOptionalAction, default=None)
    scenario.add_argument("--frames", type=int, default=300, help="measured frames (default 300)")
    scenario.add_argument("--warmup", type=int, default=60, help="unmeasured frames first (default 60)")
    scenario.add_argument("--rate", type=float, default=60,
                          help="offscreen pacing in Hz; 0 renders unthrottled (default 60)")
    scenario.add_argument("--no-vsync", dest="vsync", action="store_false", help="hosts: swap without vsync")
    scenario.add_argument("--sync", choices=("none", "finish"), default="none",
                          help="finish: call Renderer.finish() after every frame and time it")
    scenario.add_argument("--gl-timers", action=argparse.BooleanOptionalAction, default=True,
                          help="shared hosts: GL_TIMESTAMP queries in the host context (default on)")
    scenario.add_argument("--spin", type=float, default=0.5, help="model rotation per frame in degrees")
    scenario.add_argument("--log-level", default="warning")
    scenario.add_argument("--out", help="output directory (default: %%TEMP%%\\filly-profile)")
    scenario.add_argument("--timeout", type=float, default=600)
    scenario.add_argument("--gpu", choices=("default", "intel", "nvidia"), default="default",
                          help="adapter for the rendering process, through SHIM_MCCOMPAT")
    scenario.add_argument("--nvtx-site", default=os.environ.get("FILLY_PROFILE_SITE"),
                          help="directory that contains the nvtx package (default: $FILLY_PROFILE_SITE)")

    replay_options = argparse.ArgumentParser(add_help=False)
    replay_options.add_argument("--repeat", type=int, default=5, help="timed replays (default 5)")
    replay_options.add_argument("--replay-gpu", choices=("intel", "nvidia", "default", "qrenderdoc"),
                                help="adapter for the replay (default: the capture adapter; intel if default)")
    replay_options.add_argument("--tools-dir", default=str(Path(tempfile.gettempdir()) / "filly-profile" / "renderdoc"),
                                help="where to keep the renamed replay server")
    replay_options.add_argument("--detail", action="store_true", help="also list every timed action")

    cpu = sub.add_parser("cpu", parents=[scenario], help="CPU time per frame phase")
    cpu.add_argument("--csv", help="write per-frame rows to this file")
    cpu.add_argument("--nvtx", action="store_true", help="emit NVTX ranges (for nsys)")
    cpu.add_argument("--report", help="also write the table to this file")
    cpu.add_argument("--summary-json", help="write percentiles per phase as JSON (for profile_matrix.py)")
    cpu.add_argument("--frame-info", action="store_true",
                     help="report Filament's getFrameInfoHistory() GPU time (instrumented build only)")
    cpu.add_argument("--renderdoc-capture", help=argparse.SUPPRESS)
    sub.add_parser("renderdoc", parents=[scenario, replay_options], help="GPU time per pass (RenderDoc)")
    replay_parser = sub.add_parser("replay", parents=[replay_options], help="replay an existing capture")
    replay_parser.add_argument("capture")
    replay_parser.add_argument("--timeout", type=float, default=600)
    nsys = sub.add_parser("nsys", parents=[scenario], help="GPU frame time and GL threads (Nsight Systems)")
    nsys.add_argument("--nsys-trace", default="opengl-annotations,nvtx",
                      help="nsys --trace value (default opengl-annotations,nvtx, which also records "
                           "KHR_debug groups; wddm needs an elevated shell)")
    args = parser.parse_args()

    if args.command == "list":
        for name, values in SCENARIOS.items():
            print(f"{name:16s} {values or '(defaults)'}")
        print(f"defaults: {DEFAULTS}")
        return
    if getattr(args, "replay_gpu", "x") is None:
        gpu = getattr(args, "gpu", "default")
        args.replay_gpu = "intel" if gpu == "default" else gpu
    {"cpu": cmd_cpu, "renderdoc": cmd_renderdoc, "replay": cmd_replay, "nsys": cmd_nsys}[args.command](args)


if __name__ == "__main__":
    main()
