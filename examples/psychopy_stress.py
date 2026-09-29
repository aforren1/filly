"""Repeat asset trials and report PsychoPy frame timing and resource lifetime."""

import argparse
import csv
from datetime import datetime, timezone
import gc
from importlib.metadata import version
import json
from pathlib import Path
import platform
from time import perf_counter

import numpy as np

import filly
from filly._memory import MEMORY_COLUMNS, MemorySampler
from filly._stress import FRAME_COLUMNS, memory_trend, summarize_frames
from filly.benchmark import positive_int
from screenshot import suzanne_glb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", nargs="*", type=Path, help="Alternate these local glTF/GLB assets; default: Suzanne")
    parser.add_argument("--cycles", type=positive_int, default=30)
    parser.add_argument("--frames", type=positive_int, default=600, help="Measured frames per cycle")
    parser.add_argument("--warmup", type=positive_int, default=60, help="Unmeasured frames after each load")
    parser.add_argument("--width", type=positive_int, default=960)
    parser.add_argument("--height", type=positive_int, default=640)
    parser.add_argument("--refresh-hz", type=float, help="Requested frame budget; default: observed flip rate")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--screen", type=int, default=0)
    parser.add_argument("--copies", type=int, default=1, help="Mesh nodes that get a node-local material each cycle")
    parser.add_argument("--transparent", action="store_true")
    parser.add_argument("--skip-memory-cycles", type=int, default=1, help="Initial closed samples excluded from memory trends")
    parser.add_argument("--output", type=Path, help="New report directory; default: .deps/stress-<UTC time>")
    args = parser.parse_args()
    if args.refresh_hz is not None and (not np.isfinite(args.refresh_hz) or args.refresh_hz <= 0):
        parser.error("--refresh-hz must be finite and positive")
    if min(args.copies, args.screen, args.skip_memory_cycles) < 0 or max(args.width, args.height) > 8192:
        parser.error("Counts and screen must be nonnegative; target dimensions must be at most 8192")
    for path in args.assets:
        if not path.is_file():
            parser.error(f"Asset not found: {path}")
    # Fetch the default asset before opening the window or measuring anything.
    sources = [p.resolve() for p in args.assets] or [suzanne_glb()]
    output = args.output or Path(".deps") / datetime.now(timezone.utc).strftime("stress-%Y%m%dT%H%M%S%fZ")
    if output.exists():
        parser.error(f"Report directory already exists: {output}")
    output.mkdir(parents=True)
    report = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "settings": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key != "assets"},
              "assets": [str(p) for p in args.assets] or ["Suzanne"],
              "environment": {"python": platform.python_version(), "platform": platform.platform(),
                              **{name: version(name) for name in ("filly", "psychopy-lib", "pyglet", "numpy")}},
              "cycles": [], "aborted": False, "error": None}
    try:
        run(args, sources, output, report)
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(f"Reports: {output.resolve()}")


def run(args, sources, output, report):
    from psychopy import event, visual
    from pyglet import gl
    from filly.integrations.psychopy import SharedTarget, create_renderer
    from psychopy_shared import studio_environment

    win = renderer = target = model = None
    sampler = MemorySampler()
    samples = []
    started = perf_counter()
    with (output / "frames.csv").open("w", newline="", encoding="utf-8") as frame_file, \
         (output / "memory.jsonl").open("w", encoding="utf-8") as memory_file:
        writer = csv.writer(frame_file)
        writer.writerow(FRAME_COLUMNS)

        def sample(cycle, phase):
            row = {"cycle": cycle, "phase": phase, "elapsed_s": perf_counter()-started, **sampler.sample()}
            if renderer is not None and not renderer.closed:
                stats = renderer.stats
                row.update({key: getattr(stats, key) for key in ("live_models", "live_lights", "material_copies")})
            samples.append(row)
            memory_file.write(json.dumps(row, allow_nan=False)+"\n")
            memory_file.flush()
            return row

        try:
            filly.set_log_level("warning")
            win = visual.Window(size=(args.width, args.height), fullscr=args.fullscreen, screen=args.screen,
                                units="pix", winType="pyglet", useFBO=False, waitBlanking=True,
                                checkTiming=False, autoLog=False)
            report["environment"].update(gl_vendor=gl.gl_info.get_vendor(), gl_renderer=gl.gl_info.get_renderer(),
                                         gl_version=gl.gl_info.get_version(), window_size=list(map(int, win.size)))
            label = visual.TextBox2(win, text="Filament stress benchmark", pos=(-args.width/2+20, args.height/2-20),
                                    letterHeight=24, anchor="top-left", alignment="left", autoLog=False)
            underlay = visual.GratingStim(win, size=(args.width, args.height), sf=0.012, contrast=0.15, autoLog=False)
            calibration = []
            previous = None
            for i in range(80):
                underlay.draw(); label.draw()
                flipped = win.flip()
                if i >= 20 and previous is not None:
                    calibration.append((flipped-previous)*1000)
                previous = flipped
            median = float(np.median(calibration))
            if not np.isfinite(median) or median <= 0:
                raise RuntimeError("Could not measure PsychoPy flip intervals")
            observed_hz = 1000 / median
            refresh_hz = args.refresh_hz or observed_hz
            report.update(observed_hz=observed_hz, refresh_hz=refresh_hz, calibration_intervals_ms=calibration)
            renderer = create_renderer(win)
            scene = renderer.create_scene()
            scene.background = (0, 0, 0, 0) if args.transparent else (0.025, 0.035, 0.055, 1)
            scene.refraction = True
            scene.antialiasing = "fxaa"
            scene.transparent = args.transparent
            camera = scene.create_camera()
            scene.camera = camera
            light = scene.add_directional_light(direction=(-1, -1, -2), intensity=100000)
            scene.set_environment(studio_environment(), intensity=30000)
            target = SharedTarget(renderer, win, args.width, args.height)
            stimulus = target.as_psychopy_texture(win)
            renderer.finish()
            baseline = sample(-1, "baseline")
            # Storage is reused so the report itself does not grow by one array per trial.
            rows = np.empty((args.frames, len(FRAME_COLUMNS)))
            for cycle in range(args.cycles):
                source = sources[cycle % len(sources)]
                load_started = perf_counter()
                model = scene.load(source)
                bounds = np.asarray(model.bounds)
                center = bounds.mean(axis=0)
                radius = float(np.linalg.norm(bounds[1]-bounds[0])/2)
                if not np.isfinite(radius) or radius <= 0:
                    raise ValueError("The asset must have finite, nonzero geometry bounds")
                half_height = radius * 1.15 * max(1, args.height / args.width)
                half_width = half_height * args.width / args.height
                camera.set_orthographic(left=-half_width, right=half_width, bottom=-half_height, top=half_height,
                                        near=max(radius*0.001, 0.0001), far=radius*10)
                camera.position = (0, 0, radius*3)
                camera.look_at((0, 0, 0))
                authored = bool(model.lights)
                light.intensity = 0 if authored else 100000
                camera.exposure = 0 if authored else 15
                scene.environment_intensity = 1 if authored else 30000
                if args.copies:
                    made = 0
                    for node in model.nodes:
                        try:
                            node.material()
                        except filly.AssetError:
                            continue
                        made += 1
                        if made == args.copies:
                            break
                    if not made:
                        raise ValueError("No mesh material found for --copies")
                    del node
                clips = model.animations
                load_ms = (perf_counter()-load_started)*1000
                sample(cycle, "loaded")
                previous = None
                count = 0
                for frame in range(-args.warmup, args.frames):
                    if event.getKeys(keyList=["escape"]):
                        report["aborted"] = True
                        break
                    begin = perf_counter()
                    seconds = (frame+args.warmup)/refresh_hz
                    model.rotation_euler_deg = (8, -24+seconds*30, 0) if not args.assets else (0, 0, 0)
                    model.position = -model.transform[:3, :3] @ center
                    if clips:
                        model.apply_animation(0, seconds)
                    submit = perf_counter()
                    renderer.render(scene, target)
                    draw = perf_counter()
                    underlay.draw(); stimulus.draw(); label.draw()
                    flip = perf_counter()
                    flipped = win.flip()
                    end = perf_counter()
                    stats = renderer.stats
                    if frame >= 0:
                        rows[count] = (cycle, frame, (submit-begin)*1000, stats.cpu_submit_ms,
                                       stats.host_wait_ms, stats.host_release_ms, (flip-draw)*1000,
                                       (end-flip)*1000, (flipped-previous)*1000 if previous is not None else np.nan)
                        count += 1
                    previous = flipped
                # Complete queued work only at trial boundaries, before memory observations.
                renderer.finish()
                sample(cycle, "rendered")
                close_started = perf_counter()
                model.close()
                model = None
                renderer.finish()
                gc.collect()
                close_ms = (perf_counter()-close_started)*1000
                closed = sample(cycle, "closed")
                clean = all(closed[key] == baseline[key] for key in ("live_models", "live_lights", "material_copies"))
                result = summarize_frames(rows[:count], refresh_hz, observed_hz)
                result.update(cycle=cycle, asset=str(source) if isinstance(source, Path) else "Suzanne",
                              load_ms=load_ms, close_ms=close_ms, resources_restored=clean)
                report["cycles"].append(result)
                writer.writerows(rows[:count]); frame_file.flush()
                (output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
                print(f"Cycle {cycle+1}/{args.cycles}: {count} frames, {result['late_intervals']} late intervals, resources restored={clean}")
                if not clean:
                    raise RuntimeError("Live resource counts did not return to baseline")
                if report["aborted"]:
                    break
        finally:
            try:
                if model is not None:
                    model.close()
                if target is not None:
                    target.close()
                if renderer is not None:
                    renderer.close()
                if win is not None:
                    win.close()
                sample(-1, "shutdown")
            finally:
                sampler.close()
                report["elapsed_s"] = perf_counter()-started
                report["completed_cycles"] = sum(row["frames"] == args.frames for row in report["cycles"])
                report["measured_frames"] = sum(row["frames"] for row in report["cycles"])
                report["late_intervals"] = sum(row["late_intervals"] for row in report["cycles"])
                missed = [row["estimated_missed_refreshes"] for row in report["cycles"]]
                report["estimated_missed_refreshes"] = sum(missed) if missed and all(value is not None for value in missed) else None
                report["memory_trends"] = {key: memory_trend(samples, key, args.skip_memory_cycles) for key in MEMORY_COLUMNS}


if __name__ == "__main__":
    main()
