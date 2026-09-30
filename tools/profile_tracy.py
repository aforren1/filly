"""Build a Tracy-instrumented scratch copy of filly and profile native CPU time with it.

filly's sources carry no Tracy zones. `build` copies the sources to a scratch directory, adds
zones there, and builds the copy into its own venv. The project tree, `.venv`, and
`.deps\\psychopy311` do not change.

    python tools/profile_tracy.py build
    python tools/profile_tracy.py run helmet [profile_frame.py scenario options]

Zones:

- Main thread, in Renderer::submit (the native part of render()): `submit`, and inside it
  `Filament::beginFrame`, `Filament::render` (the scene view), `filly output passes` (the encode
  and FXAA views), `Filament::endFrame`, `Engine::flush`. The rest of
  `submit` is filly's bookkeeping.
- Main thread: `ImportedTarget::acquire` and `wait_on_host` (the wait for the driver thread to
  publish Filament's fence).
- Filament's driver thread, from filly's platform hooks: `driver frame` (from the backend
  beginFrame to endFrame, which is the time the driver thread spends on a frame's commands),
  `driver commit` (the glFlush() that ends a frame on the headless swap chain), and
  `driver createSync`.

The copy also binds Filament's Renderer::getFrameInfoHistory() as
`Renderer._frame_info_history()`, for `profile_frame.py cpu --frame-info`.

Tracy 0.14.1 is pinned. The Windows binaries are checked against the SHA-256 digest that GitHub
publishes for the release asset. GitHub publishes no digest for the source archive; its
SHA-256 is recorded here and checked.

See docs/how-to/profile.md.
"""

import argparse
import csv
import hashlib
import io
import math
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACY_VERSION = "0.14.1"
TRACY_FILES = {
    # name: (url, sha256)
    "windows": (f"https://github.com/wolfpld/tracy/releases/download/v{TRACY_VERSION}/windows-{TRACY_VERSION}.zip",
                "f7499d74914aa3ba94a2c1ce72f36477d7b61d9d0f7c9790e05274c258c97fb5"),
    "source": (f"https://github.com/wolfpld/tracy/archive/refs/tags/v{TRACY_VERSION}.tar.gz",
               "bf4af567e9c7524d07f3caa745fad02fb33bd5694f11910750382d1efbb251c1"),
}
# Short paths: MSBuild fails on long ones.
WORK = Path(os.environ.get("FILLY_TRACY_WORK", r"C:\tmp\prof\tracy-work"))


def paths(work):
    return {
        "tracy": work / "tracy",
        "src": work / "src",
        "build": work / "b",
        "venv": work / "v",
        "python": work / "v" / "Scripts" / "python.exe",
    }


def fetch(work):
    tracy = paths(work)["tracy"]
    tracy.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in TRACY_FILES.items():
        archive = tracy / Path(url).name
        if not archive.exists():
            print(f"download {url}")
            urllib.request.urlretrieve(url, archive)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != digest:
            archive.unlink()
            raise SystemExit(f"SHA-256 mismatch for {archive.name}: {actual}, expected {digest}")
    if not (tracy / "bin" / "tracy-capture.exe").exists():
        with zipfile.ZipFile(tracy / f"windows-{TRACY_VERSION}.zip") as z:
            z.extractall(tracy / "bin")
    if not (tracy / f"tracy-{TRACY_VERSION}").exists():
        with tarfile.open(tracy / f"v{TRACY_VERSION}.tar.gz") as t:
            t.extractall(tracy, filter="data")
    return tracy / f"tracy-{TRACY_VERSION}", tracy / "bin"


def replace_once(text, old, new, where):
    if text.count(old) != 1:
        raise SystemExit(f"Cannot instrument {where}: expected one match of {old!r}, found "
                         f"{text.count(old)}. The native sources changed; update tools/profile_tracy.py.")
    return text.replace(old, new)


def instrument(src, tracy_source, tracy=True):
    """Add the frame-info binding, and optionally Tracy zones, to the scratch copy.

    Without `tracy`, the zone macros compile to nothing.
    """
    cmake = src / "CMakeLists.txt"
    text = cmake.read_text()
    text += f"""
# Added by tools/profile_tracy.py; scratch copy only.
target_include_directories(filly_core PUBLIC "{(tracy_source / 'public').as_posix()}")
"""
    if tracy:
        text += f"""target_sources(filly_core PRIVATE "{(tracy_source / 'public' / 'TracyClient.cpp').as_posix()}")
target_compile_definitions(filly_core PUBLIC TRACY_ENABLE)
target_link_libraries(filly_core PRIVATE ws2_32 dbghelp)
"""
    cmake.write_text(text)

    renderer = src / "native" / "renderer.cpp"
    text = renderer.read_text()
    text = replace_once(text, '#include "renderer.h"\n', '#include "renderer.h"\n#include <tracy/Tracy.hpp>\n',
                        "renderer.cpp include")
    text = replace_once(
        text,
        "void Renderer::submit(const Scene& scene, detail::TargetData& target, const RenderOptions& options) {\n",
        "void Renderer::submit(const Scene& scene, detail::TargetData& target, const RenderOptions& options) {\n"
        "    ZoneScopedN(\"submit\");\n", "Renderer::submit")
    for old, name in (("    state_->renderer->beginFrame(state_->frame_chain);\n", "Filament::beginFrame"),
                      ("    state_->renderer->render(view);\n", "Filament::render"),
                      ("    if (exact) detail::render_output(*state_, data, target, region);\n", "filly output passes"),
                      ("    state_->renderer->endFrame();\n", "Filament::endFrame"),
                      ("    if (!target.imported) state_->engine->flush();\n", "Engine::flush")):
        text = replace_once(text, old, f"    {{ ZoneScopedN(\"{name}\");\n    {old.strip()}\n    }}\n", name)
    text = replace_once(text, "    target.rendered = true;\n    ++state_->stats.frames_rendered;\n",
                        "    target.rendered = true;\n    ++state_->stats.frames_rendered;\n    FrameMarkNamed(\"render\");\n",
                        "FrameMark")
    # After the nested-acquire early return, so that the adapters' inner acquire() calls in
    # draw() do not add zero-length instances.
    text = replace_once(text, "    if (data_->held > 0) { ++data_->held; return; }\n",
                        "    if (data_->held > 0) { ++data_->held; return; }\n"
                        "    ZoneScopedN(\"ImportedTarget::acquire\");\n", "ImportedTarget::acquire")
    text = replace_once(text, "    if (data_->access == Access::SUBMITTED) interop.wait_on_host(data_->ready);\n",
                        "    if (data_->access == Access::SUBMITTED) { ZoneScopedN(\"wait_on_host\"); "
                        "interop.wait_on_host(data_->ready); }\n", "wait_on_host")
    renderer.write_text(text)

    # Renderer._frame_info_history(): Filament's getFrameInfoHistory() for profile_frame.py
    # --frame-info. Tuples of (frameId, gpuFrameDuration, denoised, beginFrame, endFrame,
    # backendBeginFrame, backendEndFrame, gpuFrameComplete), in nanoseconds.
    text = renderer.read_text()
    text += """
std::vector<std::array<int64_t, 8>> filly::Renderer::frame_info_history() const {
    std::vector<std::array<int64_t, 8>> out;
    auto history = state_->renderer->getFrameInfoHistory(state_->renderer->getMaxFrameHistorySize());
    for (const auto& f : history)
        out.push_back({int64_t(f.frameId), f.gpuFrameDuration, f.denoisedGpuFrameDuration, f.beginFrame,
                       f.endFrame, f.backendBeginFrame, f.backendEndFrame, f.gpuFrameComplete});
    return out;
}
"""
    renderer.write_text(text)
    header = src / "native" / "renderer.h"
    text = header.read_text()
    text = replace_once(text, "#pragma once\n", "#pragma once\n#include <array>\n#include <vector>\n", "renderer.h")
    text = replace_once(text, "    Stats stats() const;\n",
                        "    Stats stats() const;\n    std::vector<std::array<int64_t, 8>> frame_info_history() const;\n",
                        "Renderer::stats declaration")
    header.write_text(text)
    bindings = src / "native" / "bindings.cpp"
    text = bindings.read_text()
    text = replace_once(text, "#include <nanobind/nanobind.h>\n",
                        "#include <nanobind/nanobind.h>\n#include <nanobind/stl/array.h>\n#include <nanobind/stl/vector.h>\n",
                        "bindings.cpp include")
    finish = '        .def("finish", &Renderer::finish, nb::call_guard<nb::gil_scoped_release>())\n'
    text = replace_once(text, finish, finish + '        .def("_frame_info_history", &Renderer::frame_info_history)\n',
                        "Renderer.finish binding")
    bindings.write_text(text)

    interop = src / "native" / "gl_interop.cpp"
    text = interop.read_text()
    text = replace_once(text, '#include "gl_interop.h"\n',
                        '#include "gl_interop.h"\n#include <tracy/Tracy.hpp>\n#include <tracy/TracyC.h>\n',
                        "gl_interop.cpp include")
    text = replace_once(
        text,
        "    filament::backend::Platform::Sync* createSync() noexcept override { return queue.create_sync(); }\n",
        "    filament::backend::Platform::Sync* createSync() noexcept override {\n"
        "        ZoneScopedN(\"driver createSync\");\n        return queue.create_sync();\n    }\n"
        "    void beginFrame(int64_t vsync, int64_t interval, uint32_t id) noexcept override {\n"
        "#ifdef TRACY_ENABLE\n"
        "        TracyCZoneN(zone, \"driver frame\", 1);\n        frame_zone = zone;\n"
        "#endif\n"
        "        Base::beginFrame(vsync, interval, id);\n    }\n"
        "    void endFrame(uint32_t id) noexcept override {\n"
        "        Base::endFrame(id);\n        TracyCZoneEnd(frame_zone);\n    }\n"
        "    TracyCZoneCtx frame_zone{};\n",
        "SyncPlatform")
    text = replace_once(
        text,
        "    void commit(filament::backend::Platform::SwapChain*) noexcept override { glFlush(); }\n",
        "    void commit(filament::backend::Platform::SwapChain*) noexcept override {\n"
        "        ZoneScopedN(\"driver commit\");\n        glFlush();\n    }\n",
        "SyncPlatform::commit")
    interop.write_text(text)


def cmd_build(args):
    work = Path(args.work)
    p = paths(work)
    tracy_source, _ = fetch(work)
    source = Path(args.source) if args.source else ROOT
    if p["src"].exists():
        shutil.rmtree(p["src"])
    p["src"].mkdir(parents=True)
    for item in ("CMakeLists.txt", "pyproject.toml", "README.md"):
        shutil.copy2(source / item, p["src"] / item)
    for item in ("native", "src"):
        shutil.copytree(source / item, p["src"] / item, ignore=shutil.ignore_patterns("__pycache__"))
    instrument(p["src"], tracy_source, tracy=not args.no_tracy)
    if not p["python"].exists():
        subprocess.run(["uv", "venv", str(p["venv"]), "--python", args.python_version], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(p["python"]), "nanobind", "scikit-build-core",
                    "numpy", "ninja", "pyglet==1.4.11"], check=True)
    defines = [f"-Ccmake.define.FILAMENT_ROOT={(ROOT / '.deps').as_posix()}", f"-Cbuild-dir={p['build']}"]
    webp = next(ROOT.glob("build/*/_deps/libwebp-src"), None)
    if webp:
        defines.append(f"-Ccmake.define.FETCHCONTENT_SOURCE_DIR_LIBWEBP={webp.as_posix()}")
    defines += [f"-Ccmake.define.{d}" for d in args.define]
    env = dict(os.environ, CMAKE_BUILD_PARALLEL_LEVEL=str(args.jobs))
    command = ["uv", "pip", "install", "--python", str(p["python"]), "--no-build-isolation",
               "--reinstall-package", "filly", str(p["src"]), *defines]
    # Below-normal priority keeps the build from disturbing other measurements on the machine.
    flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 0
    subprocess.run(command, env=env, check=True, creationflags=flags)
    print(f"Instrumented build: {p['python']}")


def summarize(csv_text, frames):
    """Per-zone percentiles from `tracy-csvexport -u` (one row per zone instance)."""
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        return "The trace has no zones."
    start_key = next(k for k in rows[0] if "start" in k)
    time_key = next(k for k in rows[0] if "time" in k and k != start_key)
    zones = {}
    for row in sorted(rows, key=lambda r: int(r[start_key])):
        zones.setdefault(row["name"], []).append(int(row[time_key]))
    lines = [f"{'zone (ms)':28s} {'count':>6s} {'p50':>8s} {'p95':>8s} {'p99':>8s} {'max':>8s}"]

    def pct(values, q):
        values = sorted(values)
        position = (len(values) - 1) * q
        low, high = math.floor(position), math.ceil(position)
        return values[low] + (values[high] - values[low]) * (position - low)

    order = ["submit", "Filament::beginFrame", "Filament::render", "filly output passes", "Filament::endFrame", "Engine::flush",
             "ImportedTarget::acquire", "wait_on_host", "driver frame", "driver commit", "driver createSync"]
    for name in order + sorted(set(zones) - set(order)):
        values = zones.get(name)
        if not values:
            continue
        # The last `frames` instances are the measured frames; earlier ones are warmup.
        values = [v / 1e6 for v in values[-frames:]] if frames else [v / 1e6 for v in values]
        lines.append(f"{name:28s} {len(values):6d} " + " ".join(f"{pct(values, q):8.3f}" for q in (0.5, 0.95, 0.99, 1.0)))
    return "\n".join(lines)


def cmd_run(args, rest):
    work = Path(args.work)
    p = paths(work)
    _, bin_dir = fetch(work)
    if not p["python"].exists():
        raise SystemExit("No instrumented build. Run `python tools/profile_tracy.py build` first.")
    out_dir = Path(args.out) if args.out else work / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = out_dir / f"{args.scenario}-{time.strftime('%Y%m%d-%H%M%S')}.tracy"
    capture = subprocess.Popen([str(bin_dir / "tracy-capture.exe"), "-o", str(trace), "-f"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = dict(os.environ, TRACY_NO_EXIT="1")
    frames = args.frames
    command = [str(p["python"]), str(ROOT / "tools" / "profile_frame.py"), "cpu", args.scenario,
               "--frames", str(frames), *rest]
    print("run:", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, env=env, capture_output=True, text=True)
    print(completed.stdout)
    if completed.returncode:
        print(completed.stderr[-2000:], file=sys.stderr)
    try:
        capture.wait(timeout=60)
    except subprocess.TimeoutExpired:
        capture.kill()
        raise SystemExit("tracy-capture did not finish")
    exported = subprocess.run([str(bin_dir / "tracy-csvexport.exe"), "-u", str(trace)],
                              capture_output=True, text=True, check=True).stdout
    report = summarize(exported, frames)
    print("Native zones (Tracy), last %d instances of each zone:\n%s" % (frames, report))
    trace.with_suffix(".txt").write_text(completed.stdout + "\n" + report + "\n")
    print(f"\nWrote {trace} and {trace.with_suffix('.txt')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", default=str(WORK), help="scratch directory (short path)")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="copy, instrument, and build filly")
    build.add_argument("--jobs", type=int, default=2, help="parallel compile jobs (default 2)")
    build.add_argument("--python-version", default="3.12")
    build.add_argument("--no-tracy", action="store_true", help="frame-info binding only, no Tracy client")
    build.add_argument("--source", help="build this source tree instead of the project, for example a "
                       "snapshot of an earlier revision or a probe copy (needs CMakeLists.txt, "
                       "pyproject.toml, README.md, native, src)")
    build.add_argument("--define", action="append", default=[],
                       help="extra CMake definition, for example FILLY_MATERIALS=archive")
    run = sub.add_parser("run", help="profile a scenario; other options go to profile_frame.py cpu")
    run.add_argument("scenario")
    run.add_argument("--frames", type=int, default=300)
    run.add_argument("--out", help="directory for .tracy and .txt files")
    args, rest = parser.parse_known_args()
    if args.command == "build":
        if rest:
            parser.error(f"unknown options: {rest}")
        cmd_build(args)
    else:
        cmd_run(args, rest)


if __name__ == "__main__":
    main()
