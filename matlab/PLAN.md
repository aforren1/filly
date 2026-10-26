# MATLAB and Octave frontend: plan and status

This document describes how filly is made available in MATLAB and Octave, with Psychtoolbox
(PTB) as the display host. It started as the plan in `filly-mex/PLAN.md` and moved here when
work started.

Status (October 26, 2026):

| Phase | Status |
| --- | --- |
| 0: PTB spike | Done. See [Phase 0 findings](#phase-0-findings). |
| 1: glTF preparation in C++ | Done (commit b4bdd32). |
| 2: MATLAB frontend | Done on Windows. See [Phase 2 findings](#phase-2-findings). |
| 3: PTB target | Done on Windows with MATLAB. See [Phase 3 findings](#phase-3-findings). |
| 4: Linux | Done for Octave. MATLAB on Linux: CI job added, not run yet. See [Phase 4 findings](#phase-4-findings). |
| 5: float targets and PTB inputs | Not started. |
| 6: Octave on Windows | Not started (conditional). |

How to build, use, and test the frontend: [docs/how-to/matlab.md](../docs/how-to/matlab.md).

## Goal

- MATLAB and Octave users load glTF models, build scenes, and render them into PTB windows.
- Frames stay on the GPU. The renderer writes into an OpenGL texture that PTB draws with
  `Screen('DrawTexture')`.
- The Python package and the MATLAB frontend use one native renderer. Fixes, validation, and
  reference parity apply to both.

## Out of scope

- macOS. See [macOS](#macos).
- A bridge from MATLAB to the Python package (`pyenv`, `py.*`). The bridge is MATLAB-only, it
  couples MATLAB and Python versions, and on Linux MATLAB's bundled runtime libraries can clash
  with the extension module.
- Drawing into MATLAB figures. MATLAB graphics does not expose its OpenGL context. Use a
  `RenderTarget` and `read` instead.

## What the frontends share

| Part | Shared | Reason |
|---|---|---|
| Native renderer: `renderer.cpp`, `gl_interop.cpp`, `archive_materials.cpp` (the precompiled material archive), `output_pass.cpp`, `shapes.cpp`, the `.inc` files | Yes | It holds the fence protocol, the output-encoding paths, the material provider, the lifecycle rules, and the audited fixes. Two copies would drift from the gltf_viewer reference. |
| glTF preparation (`gltf_prepare.cpp`) | Yes | Without it, MATLAB gets only the features that gltfio supports natively. It moved from Python to C++ in phase 1. |
| The C++ facade in `renderer.h` | Yes | It has no Python or Filament types. The MEX gateway uses it directly. |
| Batch calls (`Node::set_transforms`, `Node::set_morph_weights`, `Material::set_parameters`) | Yes | They are in the facade, so that all frontends validate the same way. Each frontend passes its arrays as a strided `ValueArray` without a copy. |
| A C ABI (`filly_core.h`) | Only if necessary | See [When to add a C ABI](#when-to-add-a-c-abi). |
| Host adapters (`src/filly/integrations/`, `+filly/PTBTarget.m`) | No | Each host has its own texture and draw rules. The lifecycle rules carry over; the code does not. |
| Diagnostics (`_memory.py`, `benchmark.py`, `_stress.py`) | No | Not needed for the first release. |

The cost of sharing is coupling. Each API change must land in both frontends, and the MATLAB
frontend cannot release independently of the core. Most of the value is in the shared renderer,
so this is acceptable.

### Repository

One repository. The MATLAB frontend is in `matlab/`:

| Path | Content |
| --- | --- |
| `matlab/filly_mex.cpp` | The MEX gateway |
| `matlab/CMakeLists.txt` | The MEX targets, added by `-DFILLY_MATLAB=ON` and `-DFILLY_OCTAVE=ON` |
| `matlab/+filly/` | The classes and functions |
| `matlab/SetupFilly.m` | Adds the folder to the path and checks that the MEX file loads |
| `matlab/tests/` | Offscreen tests (`run_tests.m`) |
| `matlab/tests/ptb/` | PTB tests (`run_ptb_tests.m`) |
| `matlab/tools/` | The phase 0 spike (`ptb_spike.m`) and the call benchmark (`benchmark_calls.m`) |

## Architecture

```
MATLAB / Octave                          Python
+filly classdef classes                  filly package
        |                                       |
filly_mex (C++, uses the C MEX API)      filly._native (nanobind)
        \                                      /
         filly_core (static library, C++ facade in renderer.h)
                        |
          Filament SDK static libraries
```

### Core

- `filly_core` is the static library of both frontends.
- Python keeps its nanobind binding of the C++ classes. It does not go through a C API, because
  that would lose nanobind's exception mapping and array handling for no gain.

### MEX gateway

Done as planned:

- One C++ source file, `filly_mex.cpp`, that links `filly_core` statically.
- The C MEX API only (`mex.h`, `mxArray`). The same file builds for MATLAB and for Octave 8.4 on
  Linux (phase 4).
- Dispatch on a string command as the first argument: `filly_mex('Scene.load', scene, path)`.
  Properties use `filly_mex('get', handle, name)` and `filly_mex('set', handle, name, value)`.
- The gateway converts types and keeps the handle table. It has no other logic.
- Every C++ exception is caught in `run()`. The error class and message are copied into buffers
  in `mexFunction`'s frame, and `mexErrMsgIdAndTxt` is called after `run()` returns, when no C++
  object is alive.
- `mexLock` is called when the first renderer is created, before Filament starts its threads.
- `mexAtExit` closes every renderer, newest first, and then drops all handles.
  `Renderer::close()` releases each renderer's resources in the core's order, and makes no host
  OpenGL calls when the host context is gone.
- The handle table maps `uint64` handles to facade objects. Handles are never reused, so a
  handle of a deleted MATLAB object raises `filly:error`, not a crash and not another object.
- The table is never destroyed: destroying a Filament engine joins threads, which must not
  happen under the loader lock when the DLL unloads.
- Column-major conversions are done in C++: 4 x 4 matrices with an index swap, images with one
  blocked strided copy, batch arrays as strided views with no copy. `Texture.update()` writes
  straight into the core's upload storage. Two conversions use a reused scratch buffer:
  mesh vertex arrays and `createEnvironment()`, because the core takes interleaved rows there.
- On Windows, the MEX file uses the static C++ runtime and the dynamic UCRT, as the Python module
  does. It does not import MATLAB's `msvcp140.dll`.
- On Linux, the MATLAB MEX file links libstdc++ and libgcc statically, and MATLAB's export map
  (applied by `matlab_add_mex`) exports only `mexFunction` and `mexfilerequiredapiversion`. The
  Octave MEX file uses the system C++ runtime, as Octave does, and exports only `mexFunction`.

### MATLAB package

- `+filly` handle classes: `Renderer`, `Scene`, `Model`, `Node`, `Camera`, `Light`, `Material`,
  `Asset`, `Environment`, `Texture`, `ImportedTexture`, `RenderTarget`, `ImportedTarget`,
  `PTBTarget`, `PTBTexture`, `PickQuery`, `Preparation`. Functions: `filly.setLogLevel`,
  `filly.currentContext`, `filly.shapes.plane`, `box`, `uvSphere`, `cylinder`.
- Each object holds a `uint64` handle and a reference to its `filly.Renderer`. The reference
  keeps the renderer open while the object exists.
- `delete` of a renderer closes it. `delete` of another object drops the gateway's handle only,
  as garbage collection does in Python. A scene keeps its models without handles.
- Properties are `Dependent` properties with `get` and `set` methods. Name-value options use a
  small parser, not `inputParser`, because some methods run every frame.
- No `arguments` blocks, string classes, or local functions in test scripts, so that the code can
  run in Octave 7 or later. The package and all tests run in Octave 8.4 on Linux (phase 4).
- Octave 8.4 cannot concatenate classdef objects (`[a b]` fails) and cannot call a method of an
  object array with dot syntax (`nodes.setTransforms(T)` fails). It can grow an object array by
  indexed assignment (`nodes(2) = n`) and call the method with function syntax
  (`setTransforms(nodes, T)`). The package and the tests use only these forms.

### When to add a C ABI

A flat C interface (`filly_core.h`, opaque handles, error codes) solves one problem that nothing
else solves: Octave for Windows is built with MinGW-w64, and the Filament Windows SDK is built
with MSVC. A MinGW shim cannot link a C++ library built with MSVC, but it can call a C ABI DLL.

The other reasons given for a C ABI have simpler fixes:

| Problem | Fix without a C ABI |
|---|---|
| `clear mex` unloads code that Filament's driver thread runs | `mexLock` (done) |
| MATLAB's bundled `libstdc++` on Linux | Static C++ runtime and hidden symbols in the MEX file |

A C ABI adds a layer, a handle registry, error codes, and an interface to keep stable. Add it only
if PTB users on Octave for Windows are a real group. Ask the PTB community before phase 6. The C
ABI can come later without changes to the Python package.

There are two other ways to support Octave for Windows. Compare all three in phase 6:

1. **Build Filament with MinGW.** Filament 1.77.1 supports only MSVC on Windows, and it stops with
   an error for `clang-cl`. A MinGW compiler is not rejected outright: GCC and a clang that targets
   MinGW (for example llvm-mingw) pass the compiler check. Known blockers: bluegl's Windows
   trampolines (`BlueGLCoreWindowsImpl.S`) are MASM only, and several `WIN32` branches in the
   CMake files assume MSVC. This means a patch set to maintain on each SDK upgrade, like the
   Linux patches. The core, filly's code, and the MEX could then all be built with MinGW, with no
   C ABI.
2. **Build the MEX with MSVC against Octave's C MEX API.** If Octave's DLL exports the `mex*` and
   `mx*` functions with C linkage, an MSVC-built MEX can link to them through an import library
   made from the DLL's exports. Nothing C++ crosses that boundary. Octave 10.1's `mexproto.h`
   declares them in `extern "C"`, which supports this option. Check the exports of the DLL and
   the C runtime of the Octave build before relying on it.
3. **The C ABI**, as above.

Option 2 is the least work if it holds. Try it first.

## Platform constraints

### OpenGL context

PTB needs OpenGL 2.1 or later and creates a legacy (compatibility) context. On Windows and Linux,
the NVIDIA, AMD, Intel, and Mesa drivers return their highest compatibility version, usually 4.5
or 4.6. Only macOS limits a legacy context to 2.1. PTB 3.0.22 on Intel Iris Xe reported 4.6.0
(driver 32.0.101.7088).

filly does not render in the host context. `GlInterop::create_engine()` in
[gl_interop.cpp](../native/gl_interop.cpp) gives the host context to Filament as
`sharedContext`. Filament then creates its own 4.x core context on its driver thread, in the same
share group. The host context must supply only these items:

| Requirement | Extension or version | Used for |
|---|---|---|
| Object sharing with a core-profile context | WGL or GLX share lists | All interop |
| `glFenceSync`, `glWaitSync`, `glClientWaitSync` | ARB_sync (3.2) | Host and Filament ordering |
| `glTexStorage2D` | ARB_texture_storage (4.2) | Textures that need an sRGB view |
| `glTextureView` | ARB_texture_view (4.3) | sRGB views: the direct output path, and sRGB imported textures |

The default (exact) output path accepts mutable `GL_RGBA8` textures and needs neither
ARB_texture_storage nor ARB_texture_view. A Windows or Linux compatibility context supplies all
four in any case.

### macOS

The current design cannot work on macOS. CGL does not share objects between a legacy-profile and
a core-profile context, and filly has no CGL platform. A possible path, not planned:

1. Filament renders with its Metal backend into an IOSurface-backed `MTLTexture`.
2. `CGLTexImageIOSurface2D` binds the IOSurface to a `GL_TEXTURE_RECTANGLE` in PTB's legacy
   context.
3. The CPU waits for Metal command-buffer completion, because there is no cross-API fence.

Start this work only if macOS users ask for it.

## PTB integration

### Which context each call uses

OpenGL fences belong to one context, not to the share group. `glWaitSync` delays the command
stream of the context that is current when it is called. A fence from `glFenceSync` covers only
the commands of the context in which it was inserted.

`Screen('DrawTexture')` runs in PTB's own context. With `InitializeMatlabOpenGL`,
`Screen('BeginOpenGL')` makes a separate userspace context current. If filly placed its fences in
that userspace context, PTB's draw would not wait for Filament's frame, and Filament's next write
would not wait for PTB's draw. Texture names are valid in the whole share group; synchronization
is not.

Rule, confirmed in phase 0: make all filly calls that touch the host context with **PTB's own
context current, outside `BeginOpenGL`**:

- Renderer creation (`filly.Renderer.forWindow(win)`). filly releases the context, lets Filament
  create its shared context, and makes the same context current again.
- Texture creation and import. The core restores every binding that it changes.
- Acquire (`glWaitSync` on Filament's fence) before `DrawTexture`, and release (a host fence)
  after it. Fence calls change no bound state, so they do not disturb PTB's cached state.

The `+filly` PTB classes call `Screen('GetWindowInfo', win, -1)` before these calls. That call
only makes the window's context current; PTB raises an error for it inside `BeginOpenGL`. It
costs 0.05 ms. With several PTB windows, it selects the right one.
`GlInterop::require_host()` then checks that the context current at each call is the one that
the renderer was created with. Inside `BeginOpenGL`, filly raises `filly:interop`.

### Output texture (`PTBTarget`)

1. filly creates a `GL_TEXTURE_2D` with immutable `GL_RGBA8` storage (`glTexStorage2D`) in PTB's
   context and imports it as the render target.
2. `Screen('SetOpenGLTexture', win, [], glTex, GL_TEXTURE_2D, w, h, 32, [], 32)` wraps the
   texture as a PTB texture. Flag 32 keeps `Screen('Close')` without a handle from deleting it.
   PTB then owns the OpenGL texture: closing the PTB texture deletes it.
3. `PTBTarget.draw(destinationRect, ...)` acquires the texture, calls `Screen('DrawTexture')`,
   and releases the texture. `PTBTarget.acquire()` and `release()` hold it for several draws in
   one frame.
4. `draw()` passes the source rectangle `[0 -0.5 w+0.5 h]`. PTB insets the source of a
   `GL_TEXTURE_2D` texture by half a texel at the right and bottom
   (`PsychTextureSupport.c`, "We subtract 0.5 pixel-units"), so a 1:1 draw with bilinear
   filtering blends each edge pixel with its neighbor. The rectangle cancels the inset; see
   [Phase 0 findings](#phase-0-findings).
5. The renderer keeps a list of the PTB textures of its open `PTBTarget` and `PTBTexture`
   objects. `close(r)` closes them with `Screen('Close', tex)` after the core close, when
   Filament no longer uses them. The gateway passes no `on_renderer_close` callback, because the
   callback would have to call back into MATLAB during the core close.

### Input texture (`ImportedTexture`, `PTBTexture`)

`Renderer::import_gl_texture(texture, width, height, color_space, filter, wrap)` accepts a
`GL_TEXTURE_2D` with `GL_RGBA8` storage. Mutable storage is accepted for linear color data; sRGB
color data needs immutable storage for its sRGB view. PTB textures from `Screen('MakeTexture')`
and offscreen windows are usually `GL_TEXTURE_RECTANGLE`, and can have other formats, so the
import rejects them.

Phase 3 adds `filly.PTBTexture`: filly creates an immutable `GL_RGBA8` texture, imports it, and
wraps it with `SetOpenGLTexture`. PTB draws into it with Screen commands between `beginWrite()`
and `endWrite()`. This needs the imaging pipeline (`UseFastOffscreenWindows`), which draws into
textures through FBOs. PTB writes rows bottom-up, so with glTF UVs the image is upside down; the
material's texture transform (`scale [1 -1]`, `offset [0 1]`) flips it back.

Still open (phase 5): import of textures that PTB allocates itself. Options:

- Copy the PTB texture into a `GL_TEXTURE_2D` with `glCopyImageSubData` (4.3) each frame.
- Extend the import to accept rectangle textures and other formats.

### Color and bit depth

PTB users calibrate displays and often use 16-bit or floating-point framebuffers
(`PsychImaging('AddTask', 'General', 'FloatingPoint32Bit')`) or HDR.

- Linear output exists: `encoding = "linear"` on the default output path is exact. PTB's pipeline
  can then apply its own calibration.
- Linear values in an 8-bit target band visibly in dark regions. The missing part is float render
  targets: `RGBA16F` and `RGBA32F`, both offscreen and imported. This is also useful for the
  Python package.
- An 8-bit target drawn into a `FloatingPoint32Bit` window shows the same 8-bit values
  (phase 0: identical pixels).

### Stereo

Render two views into two targets, or into two viewports of one target with the per-render camera
and viewport. Draw each target after `Screen('SelectStereoDrawBuffer', win, eye)`. Base this on
`examples/stereo.py`. Phase 0 and phase 3 tested stereo mode 4 (side by side) with one target
drawn to both eyes, inside one `acquire()` and `release()`.

### Timing

`glWaitSync` makes PTB's GPU wait, not its CPU. A slow Filament frame therefore shows up as a
missed flip deadline, not as a blocked `DrawTexture` call. PTB's flip timestamps stay valid.
Measure Filament's frame time against the refresh period for each stimulus before use in an
experiment.

The CPU does wait in `acquire()`, until Filament's driver thread has placed its fence after the
frame (`stats.hostWaitMs`). See the timing table in [Phase 0 findings](#phase-0-findings).

### Shutdown

PTB users often call `sca` before cleanup. The core handles a host context that is gone:
`close()` on targets and renderers makes no host OpenGL calls, waits for Filament's own work only,
and releases all Filament resources. The gateway's exit handler follows the same path.

`PTBTarget.close()` and `PTBTexture.close()` close the PTB texture only if the window and the
texture are still open and the texture's OpenGL name is still the one that filly created. After
`sca`, PTB can give the same handles to new windows and textures.

`clear all` and `clear mex` unload PTB's `Screen`, which closes all PTB windows. The filly
gateway stays loaded. Phase 3 tests both, and an exit of MATLAB with the window open.

## MATLAB API

Names follow the Python API, in lowerCamelCase. If a name changes, change it in both frontends.
Name-value options are UpperCamelCase and match without case.

```matlab
win = PsychImaging('OpenWindow', 0, 0);

r     = filly.Renderer.forWindow(win);           % shares PTB's context
tgt   = filly.PTBTarget(r, win);                 % creates, imports, and wraps the texture
scene = r.createScene();
cam   = r.createCamera();
cam.setPerspective('FovYDeg', 45);               % aspect follows the target; near 0.1, far 100
cam.position = [0 0 3];
cam.lookAt([0 0 0]);
scene.camera = cam;
helmet = scene.load('DamagedHelmet.glb');

while ~KbCheck
    helmet.rotationEulerDeg = [0 30 * GetSecs 0];
    r.render(scene, tgt);
    tgt.draw();
    Screen('Flip', win);
end

delete(r);   % also closes tgt; only the window (sca) must close last
sca;
```

A new camera already has a perspective projection: 30 degrees, near 0.1, far 100. The optional
planes of `setPerspective` keep these defaults.

## MATLAB and Octave details

### Data layout

- **Matrices.** MATLAB arrays are column-major. The gateway converts a 4 x 4 MATLAB matrix,
  which uses column vectors as `makehgtform` does, to the facade's row-major layout with one index
  swap. The mathematics is the same as the Python API's.
- **Images.** The core's convention is that the first row is the top of the image, the same as in
  the Python API. MATLAB images are H x W x C in column-major order. The gateway reorders them in
  one blocked strided copy, without `permute`.
- **Types.** `double` and `single` for geometry and transforms. `uint8` and `single` for image
  data, as the core accepts. `read` returns `uint8` H x W x 4.
- **Indices.** Positions in a list are 1-based: triangle indices, animation and variant
  indices, material slots. glTF node indices are identifiers from the file and stay 0-based.

### Call overhead

A MEX call is cheap. `classdef` dispatch adds to it, and per-node loops add up. The batch calls
exist in all frontends. In MATLAB they are methods of object arrays; in Python and JavaScript
they are module functions, because `node.set_morph_weights()` is already the one-node form:

- `setTransforms(nodes, T)`, where `T` is 4 x 4 x N (Python: `filly.set_transforms`, `(N, 4, 4)`).
- `setParameters(materials, name, values)`, for `baseColor`, `metallic`, `roughness`, and
  `emissive` (Python: `filly.set_material_parameters`).
- `setMorphWeights(nodes, weights)`, with one row per node (Python: `filly.set_morph_weights`).

The items can come from any open models of one renderer, in any scenes. All inputs are checked
before the first update, so an error changes nothing. `set_transforms` uses Filament's local transform
transaction, so world transforms are updated once for the batch.

See [Phase 2 findings](#phase-2-findings) for the MATLAB measurements and
[Phase 4 findings](#phase-4-findings) for Octave.

### Object lifetime

MATLAB and Octave run `delete` in an unpredictable order during `clear`. The core tracks
ownership: a closed renderer invalidates its children, and a stale handle raises an error, not a
crash. The gateway's handle table rejects a handle of a deleted object. The exit handler releases
everything that remains.

### Errors

| Core class | Identifier |
|---|---|
| `FillyError` | `filly:error` |
| `AssetError` | `filly:asset` |
| `BackendError` | `filly:backend` |
| `InteropError` | `filly:interop` |
| `std::invalid_argument`, `std::out_of_range`, and argument errors of the gateway | `filly:invalidArgument` |

The last row is an addition to the plan: Python reports these errors as `ValueError` and
`TypeError`, and MATLAB code needs an identifier to tell them apart from `filly:error`.
Asset compatibility messages become warnings with the identifier `filly:assetCompatibility`.

### Threads

MEX functions run on the MATLAB main thread, and PTB keeps its context current on that thread. The
core requires one creating thread. Filament's driver thread stays inside the core. The core never
calls the MEX API from the driver thread; the gateway passes no callbacks to the core.

## Phases

### Phase 0: PTB spike

Windows, MATLAB only. `matlab/tools/ptb_spike.m` drives the gateway directly, without the
`+filly` classes. It runs one window configuration per MATLAB process:

1. Logs the current context (`wglGetCurrentContext`) after each PTB call, and inside
   `BeginOpenGL`.
2. Creates a shared-context renderer with PTB's own context current.
3. Renders unlit quads into an imported texture, wraps it with `SetOpenGLTexture`, draws it with
   `DrawTexture`, reads the window with `Screen('GetImage')`, and compares the pixels with an
   offscreen render of the same scene.
4. Renders a slow frame (24 spheres of 131,000 vertices, MSAA 4, SSAO) whose color alternates
   each frame, and checks the color of each frame in the window. As a control, it repeats
   without `acquire` and `release`.
5. Repeats for the window configurations.
6. Measures acquire, draw, release, and a `BeginOpenGL`/`EndOpenGL` pair.

#### Phase 0 findings

Measured on October 25, 2026: Windows 11, Intel Iris Xe (driver 32.0.101.7088), PTB 3.0.22,
MATLAB R2023a, 640 x 480 windowed (desktop compositor on), median of 290 frames.

| Configuration | Shares and renders | Pixels vs offscreen (max difference) | Slow frames wrong, with sync / without | acquire / DrawTexture / release (ms) |
| --- | --- | --- | --- | --- |
| `plain` (`Screen('OpenWindow')`) | Yes | 0 | 0 of 40 / 19 to 22 of 40 | 1.27 to 1.42 / 0.16 to 0.17 / 0.11 to 0.12 |
| `imaging` (`UseVirtualFramebuffer`) | Yes | 0 | 0 / 20 | 1.31 / 0.16 / 0.12 |
| `msaa4` (multisample 4) | Yes | 0 | 0 / 24 | 2.94 / 0.33 / 0.30 |
| `float32` (`FloatingPoint32Bit`) | Yes | 0 | 0 / 15 | 1.29 / 0.16 / 0.11 |
| `stereo4` (side by side, imaging) | Yes | 0 | 0 / 27 | 1.48 / 0.20 / 0.12 |
| `stereo1` (quad-buffered request) | Yes; PTB opened it in imaging mode 9 | 0 | 0 / 39 | 15.1 / 0.19 / 0.10 |
| `3d` (`InitializeMatlabOpenGL`) | Yes | 0 | 0 / 30 | 1.37 / 0.18 / 0.14 |

The pixel column is for `filterMode` 0 (nearest) in every configuration, and also for bilinear
filtering with the corrected source rectangle in `plain` and `stereo4` (the phase 3 tests check
the corrected rectangle in five configurations). With `DrawTexture`'s default bilinear filter and
no source rectangle, 2352 edge pixels differed by up to 97 in every configuration: PTB's
half-texel inset of `GL_TEXTURE_2D` sources.
The slow frame took 18 to 53 ms of GPU time (`stats.gpuFrameMs`).

Contexts:

- PTB's context was current after `OpenWindow`, after `Flip`, after `GetWindowInfo(win, -1)`,
  and after renderer creation, in every configuration. The renderer restores the context.
- `BeginOpenGL` needs `InitializeMatlabOpenGL` before `OpenWindow`; without it, PTB raises an
  error (which also closes its windows). With it, `BeginOpenGL(win)` made PTB's separate
  userspace context current (0x10001 against PTB's 0x10000), and `BeginOpenGL(win, 1)` kept
  PTB's own context. filly's `acquire` inside `BeginOpenGL(win)` raised `filly:interop`.
- A `BeginOpenGL`/`EndOpenGL` pair cost 0.30 ms. filly needs neither.

Conclusions:

- The context rule holds as written. All tested configurations share and render.
- Orientation: no flip is needed. The window shows the image upright, the same as `read()`.
- Synchronization works: with `acquire` and `release`, no frame showed a stale or partial render.
  Without them, 15 to 39 of 40 frames did. The test can therefore detect a missing fence.
- `acquire` costs 1.3 to 1.5 ms of CPU time in these runs, 2.9 ms with a multisampled window, and
  15 ms with the stereo mode 1 fallback. It waits for Filament's driver thread to place its fence
  after the frame. This is the same wait as `host_wait_ms` in the Python adapters (0.5 to 0.9 ms
  in `docs/how-to/gl-hosts.md`, another scene). The stereo mode 1 value was not investigated.
- Not tested: NVIDIA (MATLAB and PTB ran on the Intel GPU of this Optimus laptop), fullscreen
  windows, HDR, and Linux.

### Phase 1: move the glTF preparation into C++

Done (filly commit b4bdd32): the preparation is `native/gltf_prepare.cpp`, the shapes are
`native/shapes.cpp`, libwebp is linked, and the core reads external files through wide paths.

### Phase 2: MATLAB frontend

Done on Windows: the gateway, the `+filly` classes, `RenderTarget` and `read`, the batch calls in
the core, Python, and MATLAB, offscreen parity, and the call overhead.

#### Phase 2 findings

Tests (`matlab/tests/run_tests.m`): 6 test files pass, among them `test_parity.m`. The Python
suite passes with the batch calls (509 passed, 47 skipped in `.venv`; the PsychoPy and batch tests
pass in `.deps/psychopy311`).

Offscreen parity: `tools/compare_reference.py` rendered 18 assets (the Filament-material table of
`docs/how-to/reference-comparison.md`, studio environment, 512 x 512) on October 25, 2026.
`test_parity.m` renders the same assets from MATLAB with each asset's `camera.json`:

- Against `gltf_viewer`: 17 assets identical, TransmissionRoughnessTest maximum 1. The tolerance
  is the recorded one: maximum at most 2, no pixel over 2.
- Against the Python frontend's image: identical for all 18.

MetalRoughSpheresNoTextures has 0 to 3 pixels per frame, at random places, that differ by up to
120 levels. This happens in both frontends: four Python runs of the harness worker differed from
each other in 0 to 3 pixels, with a maximum of 120; nine successive frames of one MATLAB renderer
differed in 0 to 2 pixels. The test uses the per-pixel median of three frames, as the harness does
for `gltf_viewer` captures. The cause (driver or core) is not known.

Call overhead, `matlab/tools/benchmark_calls.m`, 200 nodes, three runs of medians of 7. The
laptop's clock changes between runs, so the table gives the range:

| Call | Time |
| --- | --- |
| Bare gateway call (`filly_mex('handleCount')`) | 0.6 to 2.0 µs |
| `filly_mex('get'/'set', ...)` | 0.8 to 2.6 µs |
| Class property get or set (`model.visible`, `model.position = p`) | 1.0 to 2.4 µs |
| Class method (`cam.lookAt(p)`) | 1.2 to 2.3 µs |
| 200 `model.transform = T(:, :, i)` | 2.2 to 3.2 ms |
| `setTransforms(nodes, T)`, 200 nodes | 0.07 to 0.14 ms |
| 200 `material.baseColor = c` | 1.1 to 4.4 ms |
| `setParameters(materials, ...)`, 200 materials | 0.05 to 0.16 ms |
| `render` and `read`, 320 x 240 | 3.4 to 4.4 ms |
| `read`, 1920 x 1080 | 14.7 to 20.8 ms |

For comparison, Python on the same machine: 200 `node.transform = m` 0.19 to 0.23 ms,
`set_transforms` 0.07 to 0.12 ms, `read()` at 1920 x 1080 10.5 to 12.9 ms. The planar conversion
of a 1920 x 1080 RGBA image takes 2.7 to 2.9 ms in a separate C++ benchmark (best of 30;
`memcpy` of the same bytes 0.23 ms). Blocks of 16 or 64 rows gave the same time within noise,
and a 32-bit gather variant was not faster.

### Phase 3: PTB target

Done on Windows with MATLAB: `filly.PTBTarget` and `filly.PTBTexture`.

#### Phase 3 findings

`matlab/tests/ptb/run_ptb_tests.m`, all pass:

- `test_ptb_target.m`: in the `plain`, `imaging`, `msaa4`, `float32`, and `stereo4`
  configurations, the window matches the offscreen image exactly with the default bilinear filter,
  12 slow frames each show their own color, and the target closes in three orders: `close(r)` then
  `sca`, `sca` then `close(r)`, `close(tgt)` then `close(r)` and `sca`. The PTB texture is closed
  in each order where the window is open.
- `test_ptb_texture.m`: PTB's `FillRect` into a `PTBTexture` reaches a filly material. The
  texture arrives upside down for glTF UVs, as described above; with the flip transform the
  quadrants are exact. A second write waits for the frame that sampled the first.
- Separate MATLAB processes: `clear all` with a window, renderer, target, and texture open, then
  a new window and renderer; `sca` then `clear all`, then a new window and renderer; `clear mex`
  with the renderer open (the gateway stays loaded, the renderer still renders offscreen, the
  target raises `filly:interop`, and `close(r)` succeeds); and MATLAB exit with everything open.
  Each exits with status 0.

### Phase 4: Linux

Rebuild and test the core on Linux first; it was last rebuilt and tested on 2026-09-30, before
the October API changes. Then build the MEX for MATLAB on Linux (GLX), and for Octave on Linux with
`mkoctfile --mex` and the same gateway source. Run the offscreen tests in Octave, and measure
Octave's `classdef` call overhead.

Done when: the Python suite passes on Linux, and the frontend tests pass in MATLAB and in Octave 7
or later on Linux.

Status: done for Octave 8.4. The MATLAB job on Linux is in CI and has not run yet. Octave 7 was not
tested.

#### Phase 4 findings

Measured on October 26, 2026, in an Ubuntu 24.04 container (WSL 2, the laptop of phase 0) with
packages from the Ubuntu archive: Clang 18, CMake 3.28, Octave 8.4.0, Psychtoolbox 3.0.19.5, and
Mesa 25.2.8. No GPU: llvmpipe, and Zink on lavapipe.

The local SDK was the September 29 build of `tools/build_filament_linux.py` (the same Filament
version and patches) with `matc`, `uberz`, and `matinfo` from Filament's release archive, because
the drive had no space for a new SDK build. CI builds the SDK with `tools/ci_linux.sh`.

Python suite on Linux, current tree (`-m "not psychopy"`, under `xvfb-run`):

| Driver | Result |
| --- | --- |
| llvmpipe (`LIBGL_ALWAYS_SOFTWARE=1`, as the wheel CI) | 544 passed, 60 skipped |
| Zink on lavapipe | 544 passed, 60 skipped |

Before the fence fix below, three tests in `tests/test_interop.py` failed under Zink.

Octave (`matlab/tests`, Octave 8.4):

- Offscreen tests under Xvfb with llvmpipe: all 8 files pass. `test_parity.m` skips: there are no
  harness references for Linux.
- PTB tests under Xvfb with Zink, in a D-Bus session: `test_ptb_target.m` (five configurations,
  pixels, 12 slow frames each, three close orders), `test_ptb_texture.m`, and the four scenario
  processes pass. With llvmpipe, Psychtoolbox crashes in `Screen('OpenWindow')`
  (`glXQueryDrawable` in Mesa); without a D-Bus session, its GameMode support aborts at exit.
- Error paths: 300 failed loads, each holding a 1 MB path string when the core throws, and 600
  other failed calls changed the resident memory by 0.0 to 0.1 MB (`test_error_paths.m`). A
  negative control showed that Octave 8.4 raises MEX errors with a C++ exception, not with
  `longjmp`: a `mexErrMsgIdAndTxt` call inside `run()` was caught by its `catch (...)`. The
  gateway's rule (raise only after `run()` returns) is still required, so that the error reaches
  Octave.
- The MATLAB link options (`-static-libstdc++ -static-libgcc`) were checked on the Octave module
  as a proxy, because MATLAB cannot run locally: the SDK links against Ubuntu's GCC 13
  `libstdc++.a`, the file imports no C++ runtime, exports only `mexFunction`, and passes all
  offscreen tests in Octave, which has the system libstdc++ loaded too.

Fixes in this phase:

- **Fences after immediate-mode draws (core).** Mesa's `glFenceSync` does not flush the vertices
  that immediate mode still buffers (`_mesa_fence_sync` has no `FLUSH_VERTICES`). A host draw
  with `glBegin`/`glEnd` (pyglet, PTB's `DrawTexture`) could then land after its release fence,
  and Filament's next write could overtake it. `GlInterop::host_fence()` now calls `glFlush()`
  before the fence as well as after it. Zink showed the fault; llvmpipe hides it by its
  submission order. Mesa hardware drivers can have it too.
- **The host closes its X display (core).** Filament's shared GLX context uses the host's X
  display connection, and Mesa destroys every context of a connection when it closes. PTB closes
  its connection when `Screen` unloads (`clear mex`, `clear functions`), and the next render or
  close of the renderer crashed in the driver. A shared renderer now registers an Xlib close hook
  on that connection and closes itself in the hook, before GLX cleans up. On Linux, the renderer
  is therefore closed after `clear mex`; on Windows it stays open. `scenario_clear_mex.m` checks
  both.
- **`clang-scan-deps`.** CMake 3.28 with Ninja and C++20 scans for modules and needs
  `clang-scan-deps`, which Ubuntu ships in `clang-tools`. The project sets
  `CMAKE_CXX_SCAN_FOR_MODULES OFF`.
- **Octave MEX exports.** With `-fvisibility=hidden`, Octave's `mexFunction` (declared without a
  visibility attribute) was hidden and Octave could not load the file. The MEX targets do not use
  that flag; export maps keep everything else local.

Octave incompatibilities that were fixed in the package and the tests:

| Problem in Octave 8.4 | Fix |
| --- | --- |
| `[a b]` of classdef objects fails | `filly.Object.wrap` and the tests grow arrays by indexed assignment |
| Dot calls on object arrays (`nodes.setTransforms(T)`) fail | Function syntax: `setTransforms(nodes, T)` |
| The implicit call of a protected superclass constructor fails | `filly.Object`'s constructor is public; the class is abstract |
| No `isvalid` | `filly.internal.isAlive` |
| No `getReport`, and caught errors are not `MException` objects | `tests/error_report.m` |
| No `contains` | `strfind` |
| `char` of code points over 255 fails; strings are UTF-8 bytes | `native2unicode` of UTF-8 bytes in `test_basics.m` |
| `clear mex` clears a variable named `mex` | `clear functions` in `scenario_clear_mex.m` |
| A destructor cannot set a protected property during `clear functions` | `filly.Object.delete` does not clear `Id` |
| The scenario processes start `matlab -batch` | `run_ptb_tests.m` starts `octave-cli --eval` in Octave |

Call overhead in Octave, `matlab/tools/benchmark_calls.m`, 200 nodes, three runs of medians of
7 (same laptop, in the container; renders with llvmpipe):

| Call | Octave 8.4 | MATLAB R2023a (phase 2, Windows) |
| --- | --- | --- |
| Bare gateway call | 2.1 to 2.3 µs | 0.6 to 2.0 µs |
| `filly_mex('get'/'set', ...)` | 2.4 to 3.3 µs | 0.8 to 2.6 µs |
| Class property get or set | 12.6 to 16.5 µs | 1.0 to 2.4 µs |
| Class method (`cam.lookAt(p)`) | 13.5 to 16.7 µs | 1.2 to 2.3 µs |
| 200 `model.transform = T(:, :, i)` | 3.4 to 3.9 ms | 2.2 to 3.2 ms |
| `setTransforms(nodes, T)`, 200 nodes | 0.54 to 0.62 ms | 0.07 to 0.14 ms |
| 200 `material.baseColor = c` | 3.3 to 3.7 ms | 1.1 to 4.4 ms |
| `setParameters(materials, ...)`, 200 materials | 0.53 to 0.62 ms | 0.05 to 0.16 ms |

`classdef` dispatch costs about 10 µs per call in Octave, five to ten times MATLAB's. The batch
calls are still five to six times faster than one call per node. Their Octave time was not
broken down.

Not done in this phase:

- MATLAB on Linux ran in no test yet. The CI job builds the MEX file with R2023a, checks it, and
  runs the offscreen tests with llvmpipe. It loads the system libstdc++ first (`LD_PRELOAD`),
  because Mesa's drivers can need a newer one than MATLAB's. That need was not confirmed.
- PTB in MATLAB on Linux: not tested. Ubuntu's Psychtoolbox is built for Octave.
- Octave 7: not tested.

### Phase 5: float targets and PTB inputs

`RGBA16F` and `RGBA32F` render targets, offscreen and imported, in both frontends. Import of
textures that PTB allocates (rectangle textures, other formats).

Done when: PTB's calibrated and HDR pipelines show the expected values in measured output.

### Phase 6: Octave on Windows (conditional)

Only if PTB users on Octave for Windows are a real group. Try an MSVC-built MEX against Octave's C
MEX API first. If that fails, choose between a MinGW build of Filament and the C ABI with a MinGW
shim (see [When to add a C ABI](#when-to-add-a-c-abi)).

Done when: the frontend tests pass in Octave for Windows.

## Tests

- **Core.** The Python test suite and the gltf_viewer parity harness (`tools/compare_reference.py`)
  cover the shared renderer. `tests/test_batch.py` covers the batch calls.
- **Frontend.** Script tests with `assert` in `matlab/tests`, without `matlab.unittest`, so that
  Octave can run them. `run_tests.m` runs each script in its own function workspace and checks
  that no gateway handle remains afterwards.
- **Offscreen parity.** `test_parity.m`, against the harness's references, with its tolerance.
- **PTB.** `matlab/tests/ptb`, which needs a display. Run it separately, as the PsychoPy tests are
  run.

## Build and distribution

- One CMake project in the filly repository:
  - `filly_core`: MSVC with the static C++ runtime and the dynamic UCRT on Windows, clang or gcc
    on Linux.
  - `FILLY_PYTHON` (default ON): the Python module, as the wheel build uses it.
  - `FILLY_MATLAB` (default OFF): the MEX file, with `matlab_add_mex` from FindMatlab. It is built
    into `matlab/`, next to `+filly`.
  - `FILLY_OCTAVE` (default OFF, Linux): `matlab/filly_mex.mex` for Octave. It is a CMake module
    with the include directories that `mkoctfile -p INCFLAGS` reports, or with
    `FILLY_OCTAVE_INCLUDE_DIRS`, not the planned custom command that runs `mkoctfile --mex`:
    that command cannot get `filly_core`'s link libraries from CMake. It links what
    `mkoctfile --mex` links: no Octave library before Octave 10, `liboctmex` from Octave 10, plus
    the exported `__octave_mex_soversion__` that Octave 11 requires. MATLAB and Octave files can
    be in the same folder: MATLAB loads `.mexa64` and Octave loads `.mex`.
- Releases (done 2026-10-26, not yet run): `.github/workflows/matlab.yml` builds one package for
  each platform on the oldest supported release, tests that exact file on the oldest and the
  newest release, and publishes the packages on a `matlab-v<version>` tag. `matlab/VERSION`
  holds the frontend's own version, which `filly.version` reports. Each package is a
  `cmake --install --component matlab|octave` folder: the MEX file, `+filly`, `SetupFilly.m`, a
  README, and the licenses. PTB users get a drop-in install without a compiler.
  - MATLAB: built on R2021b. MEX files load in later releases.
  - Octave: two packages, because Octave 10 changed the MEX ABI. Tested locally on 2026-10-26
    with the gnuoctave images: a build against the Octave 6.4 headers loads and renders in 6.4
    and 9.4. In Octave 10.1 it loads but crashes on the first error path, and Octave 11.3 rejects
    it ("No SOVERSION found"). A build against Octave 10.1 with `liboctmex` passes in 10.1.
  - Linux: built in manylinux_2_28 with the SDK's compiler, as the Python wheels are, so the
    files need glibc 2.28 (measured: GLIBC_2.27 at most). The MATLAB file links
    `libstdc++-static` and has no GLIBCXX dependency.
- `tools/check_native_imports.py` checks the libraries that each MEX file imports, against a list
  for each frontend and platform. For ELF files, it also checks that only the entry points are
  exported.

## Risks and open questions

| Item | Status | How to resolve |
|---|---|---|
| Fences in the wrong context if filly calls run inside `BeginOpenGL` | Confirmed in phase 0: filly raises `filly:interop` there; the classes make PTB's context current first | Done |
| WGL share lists formally need compatible pixel formats. PTB windows with multisampling, stereo, or deep color can have unusual formats. | Shared and rendered in all seven tested configurations on Intel | Test NVIDIA and AMD |
| Orientation of textures that `SetOpenGLTexture` wraps | Upright for `PTBTarget`. `PTBTexture` content is bottom-up for glTF UVs | Done; the flip is a texture transform |
| PTB's half-texel inset of `GL_TEXTURE_2D` sources | Found in phase 0; `PTBTarget.draw()` cancels it | Done |
| Cost of acquire, draw, and release per frame in PTB | Measured in phase 0 | Investigate the 15 ms acquire with the stereo mode 1 fallback if users need that mode |
| Random single-pixel outliers in MetalRoughSpheresNoTextures on Intel, both frontends | Found in phase 2 | Find the cause in the core or the driver; test on another GPU |
| `mexErrMsgIdAndTxt` skips C++ destructors in Octave | Octave 8.4 raises a C++ exception, not `longjmp`; no leak in `test_error_paths.m` | Done; the design rule stays |
| Static C++ runtime in the MEX file on Linux | Linked and tested on a proxy (the Octave module); the MATLAB file is built in CI only | Check the first CI run |
| Octave `classdef` performance for per-frame calls | Measured in phase 4: about 10 µs per class call | Use the batch calls |
| Demand for Octave on Windows | Not known | Ask PTB users before phase 6 |
| Linux has not been rebuilt or tested since the first pass of the Python package | Python suite passes with llvmpipe and Zink (phase 4) | Done |
| MATLAB's libstdc++ is older than the one that Mesa's drivers need | Not observed; the CI job preloads the system copy | Check the first CI run |
| Mesa's `glFenceSync` skips buffered immediate-mode vertices | Found under Zink in phase 4; filly flushes before each host fence | Done |
| The host closes the X display that Filament's shared context uses | Crashed after PTB's `clear mex` on Linux; the renderer now closes in an Xlib close hook | Done; the hook acts only on the creating thread |
| PTB with llvmpipe under Xvfb | Crashes in `Screen('OpenWindow')` (Mesa) | CI uses Zink on lavapipe |
| MATLAB's bundled software OpenGL on Linux | CI run 40386224972: R2021b under Xvfb loaded `sys/opengl/lib/glnxa64/libGL.so.1`; the system's `swrast_dri.so` bound `_glapi_add_dispatch` to it and crashed in renderer creation. The latest MATLAB passed. | CI starts MATLAB with `-nosoftwareopengl`. The gateway raises `filly:interop` instead of crashing when that library is loaded. Not yet verified in CI. |
| Octave 10 and later reload a MEX file with a fresh time stamp during the session | Measured 2026-10-26 in the gnuoctave images, with the files copied just before Octave started: Octave 10.1 crashed in 3 of 3 runs, Octave 11.3 lost the MEX lock in 3 of 3 runs (`mislocked` false). With the files backdated by one hour: 6 of 6 passed. Octave 6.4 and 9.4 were not affected. | CI backdates the downloaded packages; the how-to tells users not to replace the file during a session. Releases keep old time stamps. A fix inside the gateway is not known. |
| Octave 11 runs a subclass `delete` again after an explicit `delete` | `Renderer.delete` then saw a released handle and warned | `Renderer.delete` checks `filly_mex('valid', id)` first |
| Octave 11 keeps the MEX lock on a function object | A second function object for the same file can be unlocked | The gateway calls `mexLock` for every renderer, not once |
| A new PTB window whose context gets the HGLRC value of a closed one would look current to a closed target | Not observed | The PTB classes also compare the OpenGL texture name before they close a PTB texture |
| Rare crash when two processes create shared-context renderers at the same time (see the filly validation reference) | Open | Low priority here: a MATLAB and PTB session is one process |
