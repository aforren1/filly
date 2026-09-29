# Build and test

## Windows requirements

- Windows x64 with an OpenGL driver.
- Visual Studio 2022 with the C++ desktop workload and a Windows SDK.
- CMake 3.24 or later. CMake 3.29 was used for the initial build.
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

The SDK must match version 1.77.1 and contain `include` and `lib/x86_64/md`.
The build links the release libraries with the dynamic MSVC runtime.

## Build on Linux

Use Linux x86_64, Python 3.12 or later for the SDK tool, Clang, CMake, Ninja, and the X11/OpenGL
development packages. Filament 1.77.1 is built from source with libstdc++ and position-independent
code. Reserve several GiB for source and build files. The first build can take several minutes.

On Ubuntu, install the system dependencies:

```bash
sudo apt-get update
sudo apt-get install clang cmake ninja-build libx11-dev libgl-dev libglu1-mesa xvfb xauth
```

Then run from the project directory:

```bash
uv venv --python 3.12
uv pip install nanobind scikit-build-core numpy pytest ninja
uv run --no-project tools/build_filament_linux.py --jobs 2
CXX=clang++ uv pip install --no-build-isolation -e . -Ccmake.define.FILAMENT_ROOT="$PWD/.deps/filament-linux"
```

The tool verifies the source and public-header archive hashes. All linked Filament libraries
are built locally. The staged SDK contains `include`, `lib/x86_64`, and `build-info.json`.
Use the same Clang installation for the SDK and wrapper so their C++ standard-library headers match.
Rendering requires a working GLX display, even for offscreen targets. WSLg supplies one on WSL;
use Xvfb with Mesa for a headless software-rendering test:

```bash
LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a uv run --no-sync python -m pytest -q -m 'not psychopy'
```

WSLg hardware rendering passed the non-PsychoPy suite on Ubuntu 24.04 with the Intel D3D12
driver and Mesa 24.0.9. The pinned SDK build includes GLX sharing and shutdown corrections.
WSLg already supplies a display, so Xvfb is optional there. Set `LIBGL_ALWAYS_SOFTWARE=1`
to select Mesa software rendering. Software tests do not measure GPU performance.

Pyglet is needed for the shared-context tests. Install `pyglet==1.4.11` and Pillow to run all
non-PsychoPy tests. Native Wayland, EGL-only headless rendering, macOS, and ARM builds are not implemented.

## Test

```powershell
uv run --no-sync python -m pytest -q
```

The tests create real OpenGL engines. They fail if a driver is unavailable.
The test suite generates its own GLB asset. No model download is necessary.
Shared-context tests require pyglet. The PsychoPy adapter test requires psychopy-lib.
These optional tests skip when their dependencies are absent. The PsychoPy setup includes both.
To compare images with Filament's own `gltf_viewer`, see [reference comparison](reference-comparison.md).

To generate the README screenshot, install the examples extra and run:

```powershell
uv pip install --python .venv\Scripts\python.exe --no-build-isolation -e ".[examples]"
uv run --no-sync python examples/screenshot.py
```

The screenshot script downloads the Suzanne model on first use.

## Build a wheel

```powershell
uv build --wheel --no-build-isolation
```

On Linux, use the staged source SDK and the same compiler:

```bash
CXX=clang++ CMAKE_ARGS="-DFILAMENT_ROOT=$PWD/.deps/filament-linux" uv build --wheel --no-build-isolation
```

The extension links Filament statically. The wheel does not need a separate Filament SDK at runtime.
The host still needs an OpenGL driver and its platform runtime libraries. Windows also needs
a compatible Microsoft C++ runtime. Linux needs X11/GLX.
The wheel includes the Filament license.

Build with Python 3.12 to produce a `cp312-abi3` wheel. This wheel supports Python 3.12 and later
standard CPython builds. Python 3.10 and 3.11 builds produce version-specific wheels. Free-threaded
Python is not included in this matrix. See [nanobind's stable ABI configuration](https://nanobind.readthedocs.io/en/latest/packaging.html).

## Automated wheels

[Build wheels](../../.github/workflows/wheels.yml) runs on pull requests, pushes to `main` or
`master`, version tags, and manual dispatch. It uses cibuildwheel for Windows AMD64 and
manylinux_2_28 x86_64, with Python 3.10 through 3.14 tests and stable-ABI wheel reuse.
Linux builds Filament inside the manylinux container, repairs dependencies with auditwheel,
and tests rendering and shared textures under Xvfb/Mesa. Cibuildwheel audits limited-ABI wheels
with abi3audit. Windows CI runs CPU smoke tests because hosted runners lack a reliable OpenGL
device; run the full suite on a Windows machine with a GPU.

Download wheels from the workflow artifacts. The workflow does not publish packages.
Source archives are pinned and hash checked; build dependencies and container images are not fully locked.
