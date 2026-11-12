# Validation

This page tells how filly is tested, and which defects are known and not fixed. For the dated
results of each test campaign, see the [validation log](../archive/validation-log.md). For the
tests of Filament behavior that filly depends on, see
[tested assumptions](../archive/assumptions.md).

## Test suites

| Suite | Location | What it covers |
| --- | --- | --- |
| Python | `python/tests/` | Rendering, encoding, effects, materials, textures, meshes, animation, framing, picking, readback, object lifetime, the OpenGL host adapters, and PsychoPy. The tests create real OpenGL engines and compare pixels with analytic values. |
| MATLAB and Octave | `matlab/tests/` | The MEX gateway, error paths, object lifetime, matrices, meshes, textures, readback, Psychtoolbox, and pixel parity with the Python package. |
| Web | `web/tests/test_web.py` | The JavaScript API in Chrome, Edge, and Firefox. It compares pixels and picks with the desktop module. |
| Reference comparison | `tools/compare_reference.py` | Pixel comparison with Filament's own `gltf_viewer` for the features that gltfio supplies. See [compare with gltf_viewer](../how-to/reference-comparison.md). |
| Sample assets | `tools/check_sample_assets.py` | Loading and rendering of the Khronos glTF sample assets. See [Khronos sample compatibility](sample-assets.md). |

To run the suites, see [build and test](../how-to/build.md#test) and
[use MATLAB, Octave, and Psychtoolbox](../how-to/matlab.md#run-the-tests).

## Continuous integration

GitHub Actions runners have no GPU, so CI tests less than a local run on a computer with a GPU:

| Workflow | Platforms | Tests |
| --- | --- | --- |
| `wheels.yml` | Linux, Windows, macOS | Linux: the Python suite without PsychoPy, with Mesa's llvmpipe under Xvfb. Windows: the tests that need no renderer. macOS: these tests and the headless tests. |
| `matlab.yml` | Linux, Windows, macOS | Linux: the MATLAB and Octave suites with llvmpipe, and the Psychtoolbox tests with Mesa's Zink on lavapipe. Windows and macOS: the tests that need no renderer. |
| `web.yml` | Linux | The web suite in headless Chrome with SwiftShader, compared with the desktop module on llvmpipe. |

Parity tests in MATLAB and the Psychtoolbox tests in MATLAB run only on a computer with a GPU.

## Known defects

### Crash when pyglet 1.4.11 opens windows one after another

On Windows, pyglet 1.4.11 can crash when a program closes a window and then opens a new one.
Each pyglet window has a child view window whose class name contains `id(window)`. On close,
pyglet does not unregister the view class, and Python frees its callback. When Python gives a
new window the same address, Windows calls the freed callback. The defect does not depend on
filly: a loop of pyglet windows without filly crashed in 6 of 6 runs.

PsychoPy 2026.2.4 requires pyglet 1.4.11. `python/tests/conftest.py` unregisters both window
classes on close, and then the tests did not crash in 30 runs. filly does not change pyglet. In
your own program, keep a reference to each closed window object until the program exits.

### Rare crash when two processes create shared-context renderers at the same time

`test_shared_texture_handoff_under_contention` starts two processes that each create a
shared-context renderer at the same time. It failed in 2 of 40 runs on the Intel GPU, with an
access violation in `filly.Renderer(shared_context=...)`. One process created 3,000
shared-context renderers without a crash. A native stack for this crash was not captured.
