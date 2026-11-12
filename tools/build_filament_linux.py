"""Build a pinned GLX-only Filament SDK with the container's C++ runtime."""

import argparse
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

# The Filament release of every filly build (FILAMENT_VERSION at the repository root). The
# hashes below belong to that release; change them together.
VERSION = (Path(__file__).resolve().parents[1] / "FILAMENT_VERSION").read_text().strip()
SOURCE_SHA256 = "89525d8e74a5bc4884ad8879ba5a740196c614b9115a41c4e8d3cb692d7cff83"
SDK_SHA256 = "c2f46496ba5707605f0459d8714d34c73296fbfd7fcfdbe4c1a00b76607da193"
LIBRARIES = "gltfio_core filament-iblprefilter filament backend filabridge filaflat geometry ibl utils bluegl smol-v stb dracodec meshoptimizer mikktspace uberzlib zstd basis_transcoder ktxreader image abseil".split()
# Host tools for filly's material archive. The release archive's tools need a newer glibc than
# manylinux_2_28, so they are built here with the libraries.
TOOLS = ["matc", "uberz", "matinfo"]


def fetch(url, path, expected):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".download")
        urllib.request.urlretrieve(url, temporary)
        temporary.replace(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected:
        raise RuntimeError(f"SHA-256 mismatch: {path}")
    return path


def replace_once(path, original, patched):
    contents = path.read_text()
    if patched in contents:
        return
    if original in contents:
        if contents.count(original) != 1:
            raise RuntimeError(f"Unexpected pinned source: {path}")
        path.write_text(contents.replace(original, patched))
    else:
        raise RuntimeError(f"Pinned Filament patch no longer applies: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=Path(".deps/linux-build"))
    parser.add_argument("--output", type=Path, default=Path(".deps/filament-linux"))
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if not hasattr(tarfile, "data_filter") or platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("Use Python 3.12+ on Linux x86_64")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    work, output = args.work.resolve(), args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    source_archive = fetch(f"https://codeload.github.com/google/filament/tar.gz/refs/tags/v{VERSION}",
                           work / "source.tar.gz", SOURCE_SHA256)
    source = work / f"filament-{VERSION}"
    if not (source / "CMakeLists.txt").exists():
        with tarfile.open(source_archive) as bundle:
            bundle.extractall(work, filter="data")
    # Fence waits in the manylinux libstdc++ build. glibc 2.28 lacks pthread_cond_clockwait, so
    # wait_until() converts the steady-clock deadline to the system clock. A zero timeout can
    # then stall when the producer has no more commands, and the time_point::max() deadline of
    # FENCE_WAIT_FOR_EVER overflows: wait_until() returns at once, and the waiting thread spins
    # on the fence mutex and starves the driver thread that would signal the fence.
    fence_header = source / "filament/backend/src/DriverBase.h"
    original = "if (mFenceCondition.wait_until(lock, until) == std::cv_status::timeout) {"
    patched = ("if (until == std::chrono::steady_clock::time_point::max()) {\n"
               "                mFenceCondition.wait(lock);\n"
               "            } else if (std::chrono::steady_clock::now() >= until ||\n"
               "                    mFenceCondition.wait_until(lock, until) == std::cv_status::timeout) {")
    replace_once(fence_header, original, patched)
    # Mesa shared contexts must use the host's display connection and driver screen.
    glx_header = source / "filament/backend/include/backend/platforms/PlatformGLX.h"
    glx_source = source / "filament/backend/src/opengl/platforms/PlatformGLX.cpp"
    replace_once(glx_header, "protected:\n", "public:\n"
        "    explicit PlatformGLX(Display* display = nullptr) noexcept\n"
        "            : mGLXDisplay(display), mOwnsDisplay(display == nullptr) {}\n"
        "    ~PlatformGLX() noexcept override;\n\nprotected:\n")
    replace_once(glx_header, "    Display* mGLXDisplay;", "    Display* mGLXDisplay;\n    bool mOwnsDisplay;")
    replace_once(glx_source, "    mGLXDisplay = g_x11.openDisplay(NULL);",
        "    if (mOwnsDisplay) mGLXDisplay = g_x11.openDisplay(NULL);")
    replace_once(glx_source, "    g_x11.closeDisplay(mGLXDisplay);",
        "    // Keep driver libraries loaded through the driver thread's TLS destructors.")
    replace_once(glx_source, "void PlatformGLX::terminate() noexcept {",
        "PlatformGLX::~PlatformGLX() noexcept {\n"
        "    if (mOwnsDisplay && mGLXDisplay) g_x11.closeDisplay(mGLXDisplay);\n"
        "}\n\nvoid PlatformGLX::terminate() noexcept {")
    # Since 1.80, PlatformGLX asks for GL 4.6 first and counts on a null return to try the next
    # version down. Mesa also sends GLXBadFBConfig for a version it lacks (llvmpipe has 4.5), and
    # Xlib's default handler then exits the process: Python and Octave died with no output. The
    # attempts ignore X errors; XSync delivers the error before the host's handler is restored.
    replace_once(glx_source, "    X11_FREE free;\n    void* library;\n} g_x11;",
        "    X11_FREE free;\n"
        "    decltype(&XSetErrorHandler) setErrorHandler;\n"
        "    decltype(&XSync) sync;\n"
        "    void* library;\n} g_x11;\n\n"
        "static int ignoreXError(Display*, XErrorEvent*) { return 0; }\n\n"
        "static GLXContext createContextQuietly(Display* display, GLXFBConfig config,\n"
        "        GLXContext share, const int* attribs) {\n"
        "    auto previous = g_x11.setErrorHandler(ignoreXError);\n"
        "    GLXContext context = g_glx.createContext(display, config, share, True, attribs);\n"
        "    g_x11.sync(display, False);\n"
        "    g_x11.setErrorHandler(previous);\n"
        "    return context;\n}")
    replace_once(glx_source,
        '    g_x11.free = (X11_FREE)dlsym(g_x11.library, "XFree");\n',
        '    g_x11.free = (X11_FREE)dlsym(g_x11.library, "XFree");\n'
        '    g_x11.setErrorHandler = (decltype(&XSetErrorHandler))dlsym(g_x11.library, "XSetErrorHandler");\n'
        '    g_x11.sync = (decltype(&XSync))dlsym(g_x11.library, "XSync");\n')
    replace_once(glx_source,
        "mGLXContext = g_glx.createContext(mGLXDisplay, mGLXConfig,\n"
        "                (GLXContext)sharedGLContext, True, contextAttribs);",
        "mGLXContext = createContextQuietly(mGLXDisplay, mGLXConfig,\n"
        "                (GLXContext)sharedGLContext, contextAttribs);")
    replace_once(glx_source,
        "context = g_glx.createContext(mGLXDisplay, mGLXConfig,\n"
        "                mGLXContext, True, contextAttribs);",
        "context = createContextQuietly(mGLXDisplay, mGLXConfig,\n"
        "                mGLXContext, contextAttribs);")
    build = work / "build"
    subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
                    "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
                    "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
                    # libstdc++ mutexes do not carry libc++'s Clang capability annotations.
                    "-DCMAKE_CXX_FLAGS=-Wno-error=thread-safety-attributes -Wno-error=thread-safety-analysis",
                    "-DUSE_STATIC_LIBCXX=OFF", "-DFILAMENT_ENABLE_RTTI=ON", "-DFILAMENT_ENABLE_EXCEPTIONS=ON",
                    # matc needs filamat; the module does not link it.
                    "-DFILAMENT_SKIP_SAMPLES=ON", "-DFILAMENT_SKIP_SDL2=ON", "-DFILAMENT_BUILD_FILAMAT=ON",
                    "-DFILAMENT_SUPPORTS_VULKAN=OFF", "-DFILAMENT_SUPPORTS_WEBGPU=OFF",
                    "-DFILAMENT_SUPPORTS_XCB=OFF", "-DFILAMENT_SUPPORTS_XLIB=ON",
                    "-DFILAMENT_SUPPORTS_EGL_ON_LINUX=OFF",
                    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"], check=True)
    targets = ["filament-abseil" if name == "abseil" else name for name in LIBRARIES] + TOOLS
    subprocess.run(["cmake", "--build", str(build), "--parallel", str(args.jobs), "--target", *targets], check=True)
    # The release archive supplies public headers only. All linked libraries are built above.
    sdk = fetch(f"https://github.com/google/filament/releases/download/v{VERSION}/filament-v{VERSION}-linux.tgz",
                work / "headers.tgz", SDK_SHA256)
    with tarfile.open(sdk) as bundle:
        for member in bundle.getmembers():
            if member.name.startswith("filament/include/") or member.name == "filament/LICENSE":
                member.name = member.name.removeprefix("filament/")
                bundle.extract(member, output, filter="data")
    library_dir = output / "lib/x86_64"
    library_dir.mkdir(parents=True, exist_ok=True)
    for name in LIBRARIES:
        matches = list(build.rglob(f"lib{name}_combined.a")) or list(build.rglob(f"lib{name}.a"))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one archive for {name}, found: {matches}")
        shutil.copy2(matches[0], library_dir / f"lib{name}.a")
    tool_dir = output / "bin"
    tool_dir.mkdir(parents=True, exist_ok=True)
    for name in TOOLS:
        matches = [path for path in build.rglob(name) if path.is_file() and path.parent.name == name]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one {name} executable, found: {matches}")
        shutil.copy2(matches[0], tool_dir / name)
    shutil.copy2(glx_header, output / "include/backend/platforms/PlatformGLX.h")
    # PlatformGLX's public header includes BlueGL, which the release SDK omits.
    shutil.copytree(source / "libs/bluegl/include/bluegl", output / "include/bluegl", dirs_exist_ok=True)
    (output / "build-info.json").write_text(json.dumps({"version": VERSION, "source_sha256": SOURCE_SHA256,
        "headers_sha256": SDK_SHA256, "backend": "OpenGL/GLX", "cxx_runtime": "libstdc++", "tools": TOOLS,
        "patches": ["nonblocking-expired-fence-wait", "untimed-unbounded-fence-wait","borrow-host-glx-display", "close-display-after-driver-thread"],
        "compiler": subprocess.check_output(["clang++", "--version"], text=True)}, indent=2))


if __name__ == "__main__":
    main()
