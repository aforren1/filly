# Build and test

## Windows requirements

- Windows x64 with an OpenGL driver.
- Visual Studio 2022 with the C++ desktop workload and a Windows SDK.
- CMake 3.28 or later. CMake 3.29 was used.
- Network access for the first configure, which downloads libwebp. See [libwebp](#libwebp).
- uv and Python 3.12 for the development environment.
- Space for the Filament SDK. The compressed archive is about 813 MiB.

The extension requires Python 3.10 or later. Python 3.11, 3.12, and 3.14 have been tested.
The SDK download tool requires Python 3.12 or later. For PsychoPy, use the [Python 3.11 setup](psychopy.md) to avoid building
the input-hook dependency from source.

## Build on Windows

From the project directory, run:

```powershell
uv venv --python 3.12
uv pip install --python .venv\Scripts\python.exe nanobind scikit-build-core numpy pytest ninja
uv run --no-project tools/fetch_filament.py
uv pip install --python .venv\Scripts\python.exe --no-build-isolation -e .
```

The download tool checks the SHA-256 hash of Filament 1.77.1 before extraction.
It downloads an official SDK archive. It does not use Git.

To use a separate SDK directory, add this argument to the install command:

```powershell
-Ccmake.define.FILAMENT_ROOT=C:/path/to/filament
```

The SDK must match version 1.77.1 and contain `include` and `lib/x86_64/mt`.
The build uses the hybrid C runtime: the static C++ runtime and vcruntime (`/MT`) and the
dynamic Universal CRT. The module does not import `msvcp140.dll` or `vcruntime140*.dll`, so it
does not need the Visual C++ Redistributable. To check the imports of a module or a wheel, run:

```powershell
uv run --no-project tools/check_native_imports.py .venv\Lib\site-packages\filly\_native.pyd
```

The tool lists the imported DLLs. It exits with status 1 if a DLL is not in its allowed list.
`tests/test_native_imports.py` does the same check on the installed module.

## libwebp

The Filament SDK is built without WebP, so the build adds libwebp 1.5.0 for `EXT_texture_webp`.
CMake `FetchContent` downloads the release archive from `storage.googleapis.com/downloads.webmproject.org`
at the first configure and checks its SHA-256 hash (`7d6fab70...c2a5c92c`, in `CMakeLists.txt`).
The archive is signed with the WebP release key `6B0E 6B70 976D E303 EDF2 F601 F9C3 D6BD B823 2B5D`.
Only the static decoder library (`webpdecoder`) is built, with the same MSVC runtime (`/MT`) as the
extension on Windows and position-independent code on Linux. Its tools are not built or
installed. The wheel includes the libwebp license and patent grant.

For an offline build, extract the same release and pass its folder:

```powershell
-Ccmake.define.FETCHCONTENT_SOURCE_DIR_LIBWEBP=C:/path/to/libwebp-1.5.0
```

The hash check does not apply to a local folder. Use only the verified release.

## Material archive

glTF materials use filly's precompiled material archive. The build compiles it from
`native/materials` with the SDK's `matc` and `uberz` and embeds it in the module. The module
has no material compiler, so nothing is compiled at run time. The design and the entry list are
in [material precompilation](../explanation/material-precompilation.md).

The build also compiles the materials of filly's output passes (encode and FXAA) with `matc`.
The archive step also needs `uberz`, and optionally `matinfo`. All come from the same Filament
version as the SDK. The build looks for them in `FILAMENT_ROOT/bin`. The Windows SDK archive contains them.
The Linux SDK tool builds them (see [Build on Linux](#build-on-linux)). To use other copies, set
`-Ccmake.define.FILLY_MATERIAL_TOOLS=<directory>`. `matinfo` lets the build check that the
optimized refraction shader still contains filly's orthographic refraction hook; without it,
the check does not run.

`FILLY_MATC_FLAGS` sets the `matc` arguments. The default is
`-a;opengl;-p;desktop;-V;stereo,ssr,vsm`: desktop OpenGL only, without the stereo,
screen-space reflection, and VSM shadow variants that filly does not use. Add `-g` for
unoptimized shaders when you compare shader precision.

The archive is platform independent. A Linux build and a Windows build of the same sources give
the same entries. The Linux `matc` is built from source, so its output bytes can differ from the
Windows release tool.

## Build on Linux

Use Linux x86_64, Python 3.12 or later for the SDK tool, Clang, CMake 3.28 or later, Ninja, and the
X11, OpenGL, and EGL development packages. Filament 1.77.1 is built from source with libstdc++ and
position-independent code. The wrapper build downloads libwebp as on Windows. Reserve several GiB
for source and build files. The first SDK build takes about 15 minutes with 7 jobs.

On Ubuntu, install the system dependencies:

```bash
sudo apt-get update
sudo apt-get install clang cmake ninja-build libx11-dev libgl-dev libegl-dev libglu1-mesa xvfb xauth
```

Then run from the project directory:

```bash
uv venv --python 3.12
uv pip install nanobind scikit-build-core numpy pytest ninja
uv run --no-project tools/build_filament_linux.py --jobs 2
CXX=clang++ uv pip install --no-build-isolation -e . -Ccmake.define.FILAMENT_ROOT="$PWD/.deps/filament-linux"
```

The tool verifies the source and public-header archive hashes. All linked Filament libraries
are built locally. The tool also builds the host tools `matc`, `uberz`, and `matinfo` for the
[material archive](#material-archive); the tools in the release archive need a newer glibc
than manylinux_2_28. The staged SDK contains `include`, `lib/x86_64`, `bin`, and
`build-info.json`. `build-info.json` lists the source patches and the tools. If it does not list
every patch or tool in the tool's lists, or a library in the tool's list is missing, delete the
staged SDK and run the tool again. A second run reuses the work directory and builds only what
is missing.
Use the same Clang installation for the SDK and wrapper so their C++ standard-library headers match.

### Build the manylinux wheel with Docker

The release wheel is built in the `manylinux_2_28` image, as in CI. On Windows or Linux with
Docker, run from the project directory:

```bash
docker run --rm -v "$PWD:/project" -w /project quay.io/pypa/manylinux_2_28_x86_64 bash -c '
  bash tools/ci_linux.sh &&
  CC=clang CXX=clang++ CMAKE_ARGS=-DFILAMENT_ROOT=/project/.deps/filament-linux \
    /opt/python/cp312-cp312/bin/python -m pip wheel --no-deps -w /tmp/raw . &&
  auditwheel repair -w dist/linux /tmp/raw/*.whl'
```

`ci_linux.sh` installs the build packages and builds the SDK if `.deps/filament-linux` has no
`build-info.json`. The image's Clang uses the GCC toolset headers. C++17 features that the system
`libstdc++.so.6` lacks, such as floating-point `std::from_chars` and `std::to_chars`, are linked
statically from the toolset's `libstdc++_nonshared.a`. The module needs `GLIBCXX_3.4.22` at most.

## Run on Linux

Offscreen renderers select their OpenGL binding when they are created:

1. EGL with a GPU, from `EGL_EXT_platform_device` or Mesa's surfaceless platform.
2. GLX, if `DISPLAY` names a reachable X server.
3. EGL with a software renderer (llvmpipe), if there is no X display.

Renderers with `shared_context` always use GLX. `Renderer.gl_platform` reports `"egl"` or `"glx"`.
To override the choice for offscreen renderers, set `FILLY_OFFSCREEN_GL` to `egl` or `glx`.
EGL needs `libEGL.so.1` from glvnd, which modern distributions install with Mesa or the NVIDIA
driver. If neither EGL nor an X display is available, `Renderer()` raises `BackendError`.

On WSL, WSLg supplies an X display. Mesa's D3D12 driver gives hardware rendering on Ubuntu
22.04 (Mesa 23.2) through both GLX and EGL. On Ubuntu 24.04 with Mesa 25.2, both fall back to
llvmpipe. Check the renderer string that Filament prints when an engine starts.

For a software-rendering test with Xvfb:

```bash
LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a uv run --no-sync python -m pytest -q -m 'not psychopy'
```

Without a display, the offscreen tests run on EGL. The shared-context tests need an X display:

```bash
env -u DISPLAY -u WAYLAND_DISPLAY uv run --no-sync python -m pytest -q -m 'not psychopy and not interop'
```

Pyglet is needed for the shared-context tests. Install `pyglet==1.4.11` and Pillow to run all
non-PsychoPy tests; the tests use Pillow only to encode PNG fixtures. The moderngl and zengl
host tests also need `moderngl` and `zengl`. Software tests do not measure GPU performance.
Native Wayland, macOS, and ARM builds are not implemented.

## Build for the web

The web build compiles filly's core with Emscripten for WebGL2 into `filly-core.js` and
`filly-core.wasm`, which `web/filly.js` loads. It always uses the material archive. See
[the web build](../explanation/web.md) for the design and [use filly in a web
page](web.md) for the API.

Requirements: Linux x86_64 with glibc 2.38 or later (Ubuntu 24.04; WSL works), `curl`, `git`,
`python3`, and `ninja`. No native compiler is needed: the host tools come from Filament's Linux
release archive. The scripts download a pinned CMake (3.31.6) and Emscripten (emsdk 5.0.4, the
version that Filament 1.77.1 uses), and check the archives' SHA-256 hashes.

1. Build the Filament WebAssembly SDK once. It took less than 10 minutes on the test laptop
   (8 cores):

   ```bash
   tools/build_filament_web.sh ~/filly-web
   ```

   To use local copies of the pinned archives, set `FILAMENT_SOURCE_TGZ` and
   `FILAMENT_LINUX_TGZ`.

2. Build filly's module:

   ```bash
   tools/build_filly_web.sh build/web ~/filly-web
   ```

   `build/web` then holds `filly.js`, `filly-core.js`, `filly-core.wasm`, and `licenses/`.
   Copy all of them together.

From Windows, run both scripts in WSL, for example
`wsl -d Ubuntu-24.04 -- bash tools/build_filly_web.sh build/web`. The build tree stays in
`~/filly-web/filly-build`, on the Linux file system.

For a debugging build, configure `web/` yourself with
`-DFILLY_WEB_EXTRA_FLAGS="-fsanitize=address -g"`.

## Test

```powershell
uv run --no-sync python -m pytest -q
```

The tests create real OpenGL engines. They fail if a driver is unavailable.
The test suite generates its own GLB asset. No model download is necessary.
Shared-context tests require pyglet. The PsychoPy adapter test requires psychopy-lib.
These optional tests skip when their dependencies are absent. The PsychoPy setup includes both.
To compare images with Filament's own `gltf_viewer`, see [reference comparison](reference-comparison.md).

`tests/test_web.py` runs the web build's JavaScript API in a browser and compares its output
with the desktop module. It needs the [web build](#build-for-the-web) in `build/web`, Node.js,
and an installed browser, and skips otherwise:

```powershell
cd tests\web; npm install; cd ..\..
uv run --no-sync python -m pytest -q -m browser
```

It uses Chrome by default; set `FILLY_TEST_BROWSERS=chrome,edge,firefox` for more browsers.
Do not run it while another test run uses the GPU: on the test laptop, filly's desktop tests
crashed once while browsers rendered at the same time.

To generate the README screenshot, install the examples extra and run:

```powershell
uv pip install --python .venv\Scripts\python.exe --no-build-isolation -e ".[examples]"
uv run --no-sync python examples/screenshot.py
```

The screenshot script renders the horse in `examples/assets`. It needs no download.

## Build a wheel

```powershell
uv build --wheel --no-build-isolation
```

On Linux, use the staged source SDK and the same compiler:

```bash
CXX=clang++ CMAKE_ARGS="-DFILAMENT_ROOT=$PWD/.deps/filament-linux" uv build --wheel --no-build-isolation
```

The extension links Filament statically. The wheel does not need a separate Filament SDK at runtime.
The host still needs an OpenGL driver and its platform libraries. Windows 10 or later supplies
the Universal CRT; the Visual C++ Redistributable is not necessary. Linux needs `libGL.so.1` and
`libX11.so.6` to load the module, and EGL or an X display to render.
The wheel includes the licenses of Filament, cgltf, meshoptimizer, and libwebp.

Build with Python 3.12 to produce a `cp312-abi3` wheel. This wheel supports Python 3.12 and later
standard CPython builds. Python 3.10 and 3.11 builds produce version-specific wheels. Free-threaded
Python is not included in this matrix. See [nanobind's stable ABI configuration](https://nanobind.readthedocs.io/en/latest/packaging.html).

On Linux, the module exports only `PyInit__native`; a linker version script keeps Filament,
libwebp, and C++ template symbols local. scikit-build-core strips the installed module
(`install.strip`). The copy in `build/<wheel tag>` keeps its symbol table (`NOSTRIP`) and has
the same GNU build ID, so use it to symbolize stacks from the wheel's module. Release builds
contain no DWARF debug information.

## Automated wheels

[Build wheels](../../.github/workflows/wheels.yml) runs on pull requests, pushes to `main` or
`master`, version tags, and manual dispatch. It uses cibuildwheel for Windows AMD64 and
manylinux_2_28 x86_64, with Python 3.10 through 3.14 tests and stable-ABI wheel reuse.
Linux builds Filament inside the manylinux container, repairs dependencies with auditwheel,
and tests rendering and shared textures under Xvfb/Mesa. Cibuildwheel audits limited-ABI wheels
with abi3audit. After the build, `tools/check_native_imports.py` rejects a wheel whose module
imports a library outside its allowed list, such as `msvcp140.dll`. Windows CI runs CPU smoke tests because hosted runners lack a reliable OpenGL
device; run the full suite on a Windows machine with a GPU.

Download wheels from the workflow artifacts. The same workflow builds the sdist and publishes
releases; see [publish a release](#publish-a-release).
Source archives are pinned and hash checked; build dependencies and container images are not fully locked.

## Publish a release

A pushed tag `vX.Y.Z` publishes the wheels and the sdist of that run to PyPI, after all builds
and tests pass. The tag must match the version in `pyproject.toml`; otherwise the sdist job
fails and nothing is published. `filly.__version__` reads the installed version, so
`pyproject.toml` is the only place to change it.

The workflow publishes with PyPI trusted publishing: PyPI trusts this repository's workflow, and
no API token is stored in GitHub.

Set up once:

1. On pypi.org, open **Your account**, then **Publishing**, and add a pending publisher: project
   `filly`, owner `aforren1`, repository `filly`, workflow `wheels.yml`, environment `pypi`.
2. On test.pypi.org, do the same with environment `testpypi`.
3. On GitHub, in the repository settings, create the environments `pypi` and `testpypi`. To
   approve each release by hand, add yourself as a required reviewer of `pypi`.

Publish:

1. Set `version` in `pyproject.toml`, for example `0.1.0`, and commit.
2. Optional rehearsal: run **Build wheels** by hand (Actions, then **Run workflow**) with
   **Publish the build to TestPyPI** selected. TestPyPI does not accept a version twice.
3. Tag the commit and push the tag:

   ```powershell
   git tag v0.1.0
   git push origin v0.1.0
   ```

Publish filly before psychopy-filly: the plugin's release workflow installs filly from PyPI.
PyPI shows `README.md` as the project description. Relative links and images in it do not work
there.
