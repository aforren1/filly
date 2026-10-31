# Use filly from MATLAB, Octave, and Psychtoolbox

The MATLAB frontend is in `matlab/`. It has a MEX gateway (`filly_mex.cpp`) and the `+filly`
package of classes. It uses the same native renderer as the Python package. The design, the
status, and the measurements are in [`matlab/PLAN.md`](../../matlab/PLAN.md).

Supported now:

| Platform | MATLAB | Octave |
| --- | --- | --- |
| Windows x64 | R2021b and later | No (see phase 6 of the plan) |
| Linux x86_64, glibc 2.28 or later | R2021b and later | 6.4 and later |
| macOS arm64, built from source | R2026b tested | 10 and later; 11.3 tested |

On macOS, there are no release packages, and Psychtoolbox windows cannot share textures with
filly: Psychtoolbox makes a legacy OpenGL 2.1 context, and macOS shares objects only between
contexts of the same profile. Offscreen rendering works.

## Install a release

Each `v<version>` release on GitHub has one zip file for each package:

| Package | Runs on |
| --- | --- |
| `filly-matlab-windows` | MATLAB R2021b and later on Windows |
| `filly-matlab-linux` | MATLAB R2021b and later on Linux |
| `filly-octave-linux-6.4` | Octave 6.4 through 9 on Linux |
| `filly-octave-linux-10` | Octave 10 and later on Linux |

Octave 10 changed the MEX ABI, so each side of that change has its own package. A package for
the wrong Octave does not load: Octave 11 rejects a file without the version symbol that Octave
10 introduced, and Octave 10 crashes on the first error from a file built for an earlier
release.

1. Unzip the package into an empty folder.
2. In MATLAB or Octave, go to that folder and run `SetupFilly`. See
   [Set up the path](#set-up-the-path).
3. Run `filly.version` to see the version of the frontend. It has its own version, apart from
   the Python package.

To build the MEX files yourself, use the next sections.

## Build the MEX file on Windows

Requirements: the requirements of the [Python build](build.md) (Visual Studio 2022, CMake 3.28
or later, the Filament SDK in `.deps`), and MATLAB. Python and nanobind are not necessary.

From the project directory, run:

```powershell
cmake -S . -B build/matlab -G "Visual Studio 17 2022" -A x64 -DFILLY_PYTHON=OFF -DFILLY_MATLAB=ON `
  "-DMatlab_ROOT_DIR=C:/Program Files/MATLAB/R2023a"
cmake --build build/matlab --config Release --target filly_mex
```

The build writes `matlab/filly_mex.mexw64`. The first configure downloads libwebp, as for the
Python build. To build offline, add `-DFETCHCONTENT_SOURCE_DIR_LIBWEBP=<folder>`.

The MEX file uses the same C runtime as the Python module: the static C++ runtime and the
dynamic Universal CRT. It does not load MATLAB's `msvcp140.dll`. Its only other imports are
`libmx.dll`, `libmex.dll`, and Windows system DLLs.

The Python wheel build sets no options, so it does not need MATLAB or Octave.

## Build the MEX files on Linux

Requirements: the requirements of the [Linux build](build.md#build-on-linux) (Clang, CMake 3.28
or later, Ninja, and the Linux SDK in `.deps/filament-linux`), and MATLAB or Octave 6.4 or later
with its development files. On Ubuntu 24.04, the Octave packages are `octave` and `octave-dev`.

For Octave, run:

```bash
CC=clang CXX=clang++ cmake -S . -B build/octave -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DFILLY_PYTHON=OFF -DFILLY_OCTAVE=ON -DFILAMENT_ROOT="$PWD/.deps/filament-linux"
cmake --build build/octave --target filly_octave_mex
```

The build writes `matlab/filly_mex.mex`. CMake gets the Octave include folders from
`mkoctfile -p INCFLAGS`. To select an Octave installation, add
`-DFILLY_MKOCTFILE=<path to mkoctfile>`. The file uses the system C++ runtime, as Octave does.
It links the same Octave libraries as `mkoctfile --mex`:

- Before Octave 10, none. The MEX API resolves against the running Octave.
- From Octave 10, `liboctmex`, from `mkoctfile -p OCTLIBDIR`. The file also exports
  `__octave_mex_soversion__`, which Octave 11 requires.

Build with the Octave that you run, or with an Octave from the same side of the Octave 10
change (see [Install a release](#install-a-release)). Without `mkoctfile`, set
`-DFILLY_OCTAVE_INCLUDE_DIRS` (the `octave-<version>/octave` and `octave-<version>` folders) and,
for Octave 10 and later, `-DFILLY_OCTAVE_LIBRARY_DIR` (the folder of `liboctmex`). The release
build does this in the manylinux container: see `matlab/tools/ci_matlab_linux.sh`.

For MATLAB, run:

```bash
CC=clang CXX=clang++ cmake -S . -B build/matlab -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DFILLY_PYTHON=OFF -DFILLY_MATLAB=ON -DFILAMENT_ROOT="$PWD/.deps/filament-linux" \
  -DMatlab_ROOT_DIR=/usr/local/MATLAB/R2023a
cmake --build build/matlab --target filly_mex
```

The build writes `matlab/filly_mex.mexa64`. MATLAB puts its own libstdc++ and libgcc_s first in
the library path, and they are older than the system copies. The MEX file therefore links both
statically. Both files can be in the same folder: MATLAB loads `.mexa64` and Octave loads `.mex`.

To check the libraries that a file imports and the symbols that it exports, run
`python3 tools/check_native_imports.py matlab/filly_mex.mex` (or `.mexa64`).

## Build the MEX files on macOS

Requirements: the requirements of the [macOS build](build.md#macos-requirements) and the SDK in
`.deps/filament-mac`, and MATLAB or Octave 10 or later. Homebrew's `octave` package was tested.
Python and nanobind are not necessary, but the commands below use CMake and Ninja from the
project's `.venv`.

From the project directory, run:

```bash
export PATH="$PWD/.venv/bin:/opt/homebrew/bin:$PATH"
MACOSX_DEPLOYMENT_TARGET=11.0 cmake -S . -B build/mex-mac -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DFILLY_PYTHON=OFF -DFILLY_MATLAB=ON -DFILLY_OCTAVE=ON \
  -DMatlab_ROOT_DIR=/Applications/MATLAB_R2026b.app
cmake --build build/mex-mac --target filly_mex filly_octave_mex
```

To build only one of the files, leave out the other option and target. The build writes
`matlab/filly_mex.mexmaca64` for MATLAB and `matlab/filly_mex.mex` for Octave. Both use the
system C++ runtime.

- The MATLAB file is a C API MEX file. It links only `libmx` and `libmex`.
- The Octave file links no Octave library. It gets the MEX API from the running Octave, whose
  `octave-cli` and `octave-gui` link `liboctmex`, so the file loads in other installations of
  Octave 10 and later too. The MEX API headers of Octave 10.1 and 11.3 are the same.

The tests passed with MATLAB R2026b and Octave 11.3. If `matlab -batch` stops at startup, look
for a macOS prompt that asks for access to the folder: MATLAB waits until you accept it.

### MATLAB and Mesa on Linux

MATLAB brings two libraries that the system's Mesa drivers (Intel, AMD, and software rendering)
do not work with:

- MATLAB's own libstdc++ can be too old for the drivers, which link the system libstdc++. A
  renderer then cannot create its OpenGL context.
- Some releases (R2021b in CI) load an old Mesa `libGL` from MATLAB's `sys/opengl/lib` folder,
  also with `-nosoftwareopengl`. It exports Mesa's internal `_glapi` functions, and a system Mesa
  driver that binds to them crashes. filly checks which library provides them before it creates a
  renderer, and raises a `filly:interop` error that names MATLAB's library instead of crashing.

Start MATLAB with the system's libraries preloaded, so that the symbol lookup finds them first.
On Debian and Ubuntu:

```bash
LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6:/usr/lib/x86_64-linux-gnu/libGL.so.1:/usr/lib/x86_64-linux-gnu/libglapi.so.0 matlab
```

Leave out `libglapi.so.0` if the system does not have it: newer Mesa releases do not. On other
distributions, the libraries are in another folder, for example `/usr/lib64`. CI uses this
configuration. It was not tested on a local MATLAB installation.

## Set up the path

In MATLAB or Octave, run `SetupFilly` in the `matlab` folder. It adds the folder to the path and
checks that the MEX file loads. `SetupFilly(true)` also saves the path.

## Render offscreen

`examples/matlab/offscreen.m` is a complete version of this example, with lights and an
environment.

```matlab
r = filly.Renderer();
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
horse = scene.load(filly.samples.horse());
cam.frame(horse);
scene.addDirectionalLight([0 -1 -1]);
target = r.createRenderTarget(640, 480);
r.render(scene, target);
pixels = target.read();          % 480 x 640 x 4 uint8, first row at the top
imshow(pixels(:, :, 1:3));
close(r);
```

## Render into a Psychtoolbox window

`examples/matlab/ptb_spin.m` runs a version of this example with the sample horse,
`filly.samples.horse()`, so it needs no download.

```matlab
win = Screen('OpenWindow', 0, 0);
r = filly.Renderer.forWindow(win);   % shares PTB's OpenGL context
tgt = filly.PTBTarget(r, win);       % a PTB texture that covers the window
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
helmet = scene.load('DamagedHelmet.glb');
cam.frame(helmet);
scene.environment = r.loadEnvironment('studio.hdr');
scene.environmentVisible = true;     % draw the environment as the background
start = GetSecs;
while ~KbCheck
    helmet.rotationEulerDeg = [0 30 * (GetSecs - start) 0];   % 30 degrees per second
    r.render(scene, tgt);
    tgt.draw();                      % Screen('DrawTexture') between acquire and release
    Screen('Flip', win);
end
close(r);                            % also closes tgt; only the window must close last
sca;
```

Rules:

- Make all filly calls outside `Screen('BeginOpenGL')`. Inside it, PTB can make a userspace
  context current, and filly raises a `filly:interop` error.
- `tgt.draw(destinationRect, ...)` takes the arguments of `Screen('DrawTexture')` that follow
  `sourceRect`. For several draws of one frame, call `tgt.acquire()` before them and
  `tgt.release()` after them.
- Filament writes premultiplied color. To blend the image over other content, use
  `Screen('BlendFunction', win, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)`.
- For stereo, render each eye into its own target, and draw each target after
  `Screen('SelectStereoDrawBuffer', win, eye)`.
- PTB owns the target's texture. `close(tgt)` and `close(r)` release it after Filament no
  longer uses it. After `sca`, `close(r)` still releases everything and makes no OpenGL calls.

To draw with PTB into a texture that a material samples, use `filly.PTBTexture`. It needs the
imaging pipeline, for example `PsychImaging('AddTask', 'General', 'UseFastOffscreenWindows')`:

```matlab
tex = filly.PTBTexture(r, win, 256, 256);
tex.beginWrite();
Screen('FillRect', tex.texture, [255 0 0], [0 0 128 128]);
tex.endWrite();
material.baseColorTexture = tex;
material.baseColorTextureScale = [1 -1];   % PTB writes rows bottom-up
material.baseColorTextureOffset = [0 1];
```

## Differences from the Python API

Names are the Python names in lowerCamelCase: `rotation_euler_deg` becomes `rotationEulerDeg`.
Keyword arguments become name-value pairs in UpperCamelCase, for example
`cam.setPerspective('FovYDeg', 45)`. Names match without case.

| Item | MATLAB |
| --- | --- |
| Matrices | 4 x 4 `double` with column vectors, as `makehgtform` makes them. The mathematics is the same as the Python row-major arrays. |
| Images | H x W x C `uint8` or `single`, first row at the top. `read()` returns H x W x 4 `uint8`. |
| Readback | `target.beginRead()` returns a `filly.Readback`, as in Python. `read()` of a target or a readback has no `out` argument: MATLAB arrays are values, so each read returns a new array. |
| Vectors | Row vectors, for example `[0 1 0]`. `double` and `single` are accepted. |
| Positions in a list | 1-based: mesh triangle indices, animation and variant indices, material slots. |
| glTF node indices | 0-based, as in the file: `model.node(0)`, `node.index`. |
| Empty values | `[]` where Python uses `None`. |
| Text | `char` vectors. String scalars are converted. |
| Arrays of objects | `[node1 node2]` in MATLAB; in Octave, indexed assignment (see below). For the batch calls. |
| `with` blocks | `acquire()` and `release()`, `beginWrite()` and `endWrite()`. |

Errors have these identifiers:

| Identifier | Cause |
| --- | --- |
| `filly:error` | `FillyError`: for example, a closed object, or a handle of a deleted object |
| `filly:asset` | `AssetError` |
| `filly:backend` | `BackendError` |
| `filly:interop` | `InteropError`: for example, the wrong OpenGL context is current |
| `filly:invalidArgument` | A value or a type that the Python API rejects with `ValueError` or `TypeError` |

Asset compatibility messages become warnings with the identifier `filly:assetCompatibility`.

### Octave

The package and the tests are the same for Octave. Octave 8.4 has these limits, so the package
and the tests do not use these forms:

- Octave cannot concatenate classdef objects: `[node1 node2]` fails. Grow an array by indexed
  assignment instead:

  ```matlab
  nodes = node1;
  nodes(2) = node2;
  ```

  Properties such as `model.nodes` already return arrays.
- Octave cannot call a method of an object array with dot syntax: `nodes.setTransforms(T)`
  fails. Use function syntax, which works in both: `setTransforms(nodes, T)`.
- Octave reads `clear mex` as the name of a variable. Use `clear functions` to unload MEX files.
- Octave adds `filly_mex: ` to the start of error messages. The identifiers are the same.
- In the Octave GUI, close each renderer (`close(r)`) before you quit. The GUI runs the
  interpreter on a worker thread, and at exit it deletes the remaining objects on another thread,
  where filly cannot close a renderer: Octave then shows "Renderer close failed: Use the renderer
  on its creating thread", and the renderer stays open until the process ends. A closed renderer
  gives no warning.
- Do not replace `filly_mex.mex` while Octave runs, and after a build, wait a few seconds before
  you start Octave. Octave 10 and later reload a MEX file whose time stamp is close to the start
  of the session or later. With a renderer open, Octave 10 then crashes and Octave 11 loses the
  MEX lock. Files from an unzipped release keep their old time stamps, so this does not apply to
  them.

### Object lifetime

- Every object keeps a reference to its renderer, so the renderer stays open while an object
  exists.
- `delete(r)` and `close(r)` close the renderer and everything that it owns.
- Deleting another object drops the MATLAB handle only, as garbage collection does in Python. A
  scene keeps its models and lights.
- A handle of a deleted object raises `filly:error`. It cannot refer to another object.
- After the first renderer, the MEX file locks itself. `clear mex` and `clear all` do not
  unload it. Both unload PTB's `Screen`, which closes PTB's windows.
- On Linux, unloading `Screen` also closes PTB's X display connection, which the renderer's
  OpenGL context uses. A renderer from `filly.Renderer.forWindow` then closes itself, and its
  objects raise `filly:error`. On Windows, the renderer stays open and renders offscreen.

### Call overhead

Each property access or method call is one MEX call. For many nodes or materials in each frame,
use the batch calls on object arrays: `setTransforms(nodes, T)` with a 4 x 4 x N array,
`setParameters(materials, name, values)`, and `setMorphWeights(nodes, weights)` with one row per
node. The items can come from any scenes of one renderer. With 200 nodes, `setTransforms` took 0.07 to 0.14 ms
for each frame, and 200 `transform` assignments took 2.2 to 3.2 ms. See `matlab/PLAN.md` for
the measurements.

## Run the tests

The offscreen tests are scripts with `assert`. To run them, enter:

```powershell
cd matlab/tests
matlab -batch "exit(run_tests() > 0)"
```

In Octave, enter:

```bash
cd matlab/tests
octave-cli --no-gui --no-init-file --eval "exit(run_tests() > 0)"
```

On a Linux machine without a GPU, use Xvfb and Mesa's software renderer, as CI does:

```bash
LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a -s "-screen 0 1024x768x24" \
  octave-cli --no-gui --no-init-file --eval "exit(run_tests() > 0)"
```

`test_parity.m` compares renders with the images of the
[reference comparison](reference-comparison.md). Run that tool first. Set
`FILLY_REFERENCE_DIR` if its output is not in `.deps/reference-compare`. The test skips if that
output is missing or names files that no longer exist.

CI (`.github/workflows/matlab.yml`) builds the packages on the oldest supported releases,
checks the imports and exports of each MEX file, and tests the same files on the oldest and the
newest release. The Linux packages build in the manylinux_2_28 container, as the Python wheels
do, so they need glibc 2.28 or later.

| Package | Built with | Tested on |
| --- | --- | --- |
| `filly-matlab-windows` | MATLAB R2021b | R2021b and the latest MATLAB: `test_gateway.m` only, which needs no renderer. Hosted Windows runners have no usable OpenGL device. |
| `filly-matlab-linux` | MATLAB R2021b headers | R2021b (Ubuntu 22.04) and the latest MATLAB (Ubuntu 24.04): the offscreen tests, under Xvfb with Mesa's llvmpipe |
| `filly-octave-linux-6.4` | Octave 6.4 headers | Octave 6.4 and 9.4: the offscreen tests with llvmpipe. Ubuntu's Octave 8.4: the Psychtoolbox tests with Ubuntu's Psychtoolbox, under Zink |
| `filly-octave-linux-10` | Octave 10.1 headers and `liboctmex` | Octave 10.1 and 11.3: the offscreen tests with llvmpipe |
| `filly-matlab-macos` | MATLAB R2023b, the first release for Apple silicon | R2023b and the latest MATLAB: `test_gateway.m` only. The OpenGL of hosted macOS runners is Apple's software renderer, which is too slow for the offscreen tests. Not published. |
| `filly-octave-macos` | Homebrew's Octave | The same Octave: `test_gateway.m` only. Not published. |

Run the parity test and the Psychtoolbox tests in MATLAB on a machine with a GPU before a
release.

The Psychtoolbox tests need a display. They open windows on screen 0:

```powershell
cd matlab/tests/ptb
matlab -batch "exit(run_ptb_tests() > 0)"
```

In Octave, use `octave-cli --eval` in the same way. The scenario tests start one `octave-cli` or
MATLAB process each.

Psychtoolbox 3.0.19 from Ubuntu 24.04 crashes in `Screen('OpenWindow')` with Mesa's llvmpipe
under Xvfb. Mesa's Zink driver on the lavapipe Vulkan driver works. Psychtoolbox also needs a
D-Bus session: without one, its GameMode support stops the process at exit. CI runs:

```bash
MESA_LOADER_DRIVER_OVERRIDE=zink LIBGL_ALWAYS_SOFTWARE=1 LIBGL_KOPPER_DRI2=1 \
  dbus-run-session -- xvfb-run -a -s "-screen 0 1024x768x24" \
  octave-cli --no-gui --no-init-file --eval "exit(run_ptb_tests() > 0)"
```

The packages are `octave-psychtoolbox-3`, `xvfb`, `xauth`, `mesa-vulkan-drivers`,
`libgl1-mesa-dri`, `dbus`, and `dbus-x11`.

`matlab/tools/ptb_spike.m` is the phase 0 measurement, and `matlab/tools/benchmark_calls.m`
measures the call overhead.

## Publish a release

The frontend releases together with the Python package, with one `v<version>` tag. The version
comes from the tag; see [publish a release](build.md#publish-a-release) for the steps and for how
the version is computed.

Before you push the tag, run the parity test and the Psychtoolbox tests in MATLAB on a machine
with a GPU. CI cannot run them.

After the tag push, CI builds and tests every package again. The gateway tests check that
`filly.version()` matches the tag. When all jobs pass, the release job zips each package and
creates the GitHub release for the tag.
