# Validation results

The renderer and shared-texture adapter were tested on September 25 through 29, 2026.

## Environment

| Component | Version or value |
| --- | --- |
| Platform | Windows x64 |
| Python | CPython 3.11.15 and 3.12.11 |
| Filament | 1.77.1, official Windows release SDK |
| Compiler | MSVC 19.44.35214, Visual Studio 2022 |
| CMake | 3.29.2 |
| nanobind | 3.1.0 |
| scikit-build-core | 1.0.3 |
| NumPy | 2.4.6 on Python 3.11; 2.5.3 on Python 3.12 |
| PsychoPy | psychopy-lib 2026.2.4, pyglet backend |
| pyglet | 1.4.11 |
| GPU | Intel Iris Xe Graphics |
| OpenGL driver | 4.5.0, build 32.0.101.7088 |

## Results

### Output path and rename revision (September 29)

This revision renames the package to `filly`, with no aliases for the old name. It also makes
color grading the only default output path and adds the explicit opt-in
`Scene.output_path = "direct"`, which replaces the automatic choice and the private
`_direct_output` flag. Mutable host textures from `glTexImage2D` import again for targets, and for
inputs with `color_space="linear"`. All 415 non-PsychoPy tests passed on Windows Python 3.12 in
108 seconds (2 skipped: `MASK` cases on the direct path, which raise by design). All 23 PsychoPy
tests passed on Python 3.11 with psychopy-lib 2026.2.4 in 9 seconds. Linux was not retested.

- Stability: an unlit 0.5 grey stored 188 for `OPAQUE`, `BLEND`, and `MASK` alike, with
  identical frames, and the path stayed `"graded"`.
- Encoding: the 319-value sweep was within one 8-bit level of the analytic transfer function on
  both paths and in both encodings, offscreen and in a shared texture, now with an unlit `OPAQUE`
  material on the direct path too.
- Alpha: unlit `OPAQUE` materials store alpha one on the direct path, because the wrapper now
  compiles them with alpha set to one. `MASK` materials cannot be fixed in the material code and
  raise `AssetError` on load, or `ValueError` when the path is set.
- Conflicts: each of the 10 options that need postprocessing raised `ValueError` in both orders
  and left the scene unchanged. Rendering to a mutable host texture with the direct path and sRGB
  encoding raised `InteropError`; the same target then rendered with color grading.
- Cost at 1920 x 1080, wall-clock `render()` plus `finish()`, median of five interleaved rounds of
  150 frames:

  | Scene | Target | `"graded"` | `"direct"` |
  | --- | --- | ---: | ---: |
  | Empty | Offscreen | 0.81 ms (0.80 to 1.76) | 0.26 ms (0.21 to 0.42) |
  | DamagedHelmet | Offscreen | 2.00 ms (1.91 to 2.08) | 1.20 ms (1.10 to 1.29) |
  | Empty | Shared texture | 0.82 ms (0.76 to 1.05) | 0.25 ms (0.23 to 0.28) |
  | DamagedHelmet | Shared texture | 1.82 ms (1.74 to 2.04) | 1.16 ms (1.09 to 1.26) |

  Back to back, 300 frames and one `finish()`: 0.43 and 0.11 ms per frame for the empty scene,
  1.32 and 0.75 ms for DamagedHelmet. `Renderer::getFrameInfoHistory()` returned no GPU time after
  the first color-grading frame, so these are wall-clock values, not GPU times.
- Loading: with `precompiled_shaders=True`, the first unlit plane now takes 129 ms, because its
  material is compiled; a lit plane took 14 ms. With the default compiled shaders it took 85 ms.
- The regenerated `docs/images/suzanne.png` is byte-identical to the committed image, which was
  therefore made with color grading. The direct path differs from it by at most one level in 85%
  of pixels.
- Reference comparison, DamagedHelmet, studio environment: MAE 0.0000 and maximum error 0 against
  `gltf_viewer`, which also uses color grading. The earlier direct-path result was MAE 0.0001 and
  maximum 1. Other assets were not rerun.
- `python -m filly.benchmark` on DamagedHelmet, 10,000 frames at 1920 x 1080: 1.04 ms per frame
  in the batch with the default path and 0.53 ms with `--output-path direct` (0.536 ms before on
  the direct path). CPU submission median: 0.33 ms and 0.19 ms.
- The nine examples ran with the renamed package: 60 frames each, two 60-frame cycles for the
  stress example, and one frame for the screenshot. The PsychoPy examples ran on Python 3.11.

### Follow-up revision (September 29)

This revision makes cloning opt-in (`scene.load(..., clonable=True)`), writes sRGB output without
postprocessing where possible, drives node-local material copies from material animation,
addresses lights and cameras by their node (`model.lights`, `model.cameras`, `light.node`,
`camera.node`), keeps true glTF node names and adds `Node.mesh_name`, and stops finalizers of host
targets from switching or using another window's context. Shared host textures now need immutable
storage. All 326 non-PsychoPy tests passed on Windows Python 3.12 in 83 seconds. All 23 PsychoPy
tests passed on Python 3.11 with psychopy-lib 2026.2.4 in 11 seconds. Linux was not retested.

- Encoding: every value of the 319-value sweep (the 1/64 grid and every 8-bit level) was within
  one 8-bit level of the analytic transfer function, offscreen and read back from a shared host
  texture, on the direct and the color-grading paths, in both encodings. The largest deviation
  from the exact value was 0.68 levels (direct) and 0.59 levels (color grading); linear output
  was exact on both paths. Opaque scenes store alpha 255 on both paths, including unlit `OPAQUE`,
  `MASK`, and `BLEND` materials over a background with alpha 0.3.
- GPU time at 1920 x 1080 (Filament timer queries, median): an empty scene took 0.03 ms on the
  direct path and 0.60 ms with color grading; DamagedHelmet took 1.18 to 1.20 ms and 1.76 to
  1.87 ms. In a shared texture: 0.05 ms and 0.89 ms, 0.95 ms and 1.53 ms. See
  [the breakdown](../explanation/design.md#output-encoding).
- Memory: private bytes grew by 113.0 MiB per loaded DamagedHelmet with the default and by
  116.7 MiB with `clonable=True` (three processes, four loads each, after a warmup load). The
  difference, 3.7 MiB, matches the 3.6 MiB file. Most of the growth is texture memory that the
  integrated GPU allocates in system memory.
- The five examples ran for 60 frames each (the screenshot, one frame, differed from the
  committed image by at most one level). The stress example ran two cycles with resources restored.
- Host finalizers: an unclosed pyglet or moderngl target collected while another window is current
  leaves that window's context current and deletes nothing; a PsychoPy stimulus cleared from
  another context keeps its mask texture. Both failed before the change.

The GL-host and interop tests ran in loops during this revision. Some runs ended in an access
violation, an illegal instruction (0xC000001D), or a privileged instruction (0xC0000096). These
crashes were first attributed to the Intel OpenGL driver. That attribution was wrong for the
crashes reproduced later.

The cause is a pyglet 1.4.11 defect on Windows. Each pyglet window has a child view window whose
class name contains `id(window)`. On close, pyglet unregisters the top-level class but not the view
class, and it drops the view class's ctypes callback, which Python then frees. When CPython gives a
new window the same address, `RegisterClassW` for the view class fails without an error, and
`CreateWindowExW` calls the freed callback. Faulthandler placed the crashes in the creation of the
view window. The defect is independent of filly:

| Loop (September 29, after a restart) | Crashed |
| --- | --- |
| pyglet 1.4.11 windows only, no filly, callbacks allocated between windows | 6 of 6 runs |
| Same loop, window classes unregistered on close | 0 of 6 runs |
| Same loop, pyglet 2.1.16 | 0 of 6 runs |
| GL-host, interop, and host-texture tests, before the test fix | 4 of 30 runs |
| Same tests, with window classes unregistered on close | 0 of 30 runs |
| 3,000 shared-context renderer creations on one window | 0 |

pyglet 2.1.16 has the same unregistration gap but did not crash in these runs. PsychoPy 2026.2.4 pins pyglet 1.4.11. `tests/conftest.py` unregisters both
classes on close for pyglet 1.x on Windows. The library does not change pyglet.

A second, rarer crash remains open. `test_shared_texture_handoff_under_contention` starts two
processes that each create a shared-context renderer at the same time. With the pyglet fix, it
failed in 2 of 40 runs. Faulthandler placed the access violation at `filly.Renderer(shared_context=...)`
in one worker; Windows Error Reporting then held the process open, so the test saw a timeout. A
single process created 3,000 shared-context renderers without a crash, so concurrent creation in
two processes appears necessary. An earlier cdb capture showed a null-pointer read on an Intel
driver worker thread during shared-context creation, which fits this case, but a native stack for
it was not captured again. Under cdb, a worker that passed also raised
`STATUS_THREADPOOL_HANDLE_EXCEPTION` (0xC000070A) at exit, which Windows raises only when a debugger
is attached. It indicates a thread-pool handle closed twice at shutdown; its owner is not known.

### API revision (September 29)

This revision adds an explicit output encoding, scene option properties, target-fitted
projections, node-local materials, node hierarchy access, name-or-index lookups, `Scene.load()`,
`Model.clone()`, separate offscreen and imported targets, candela-preserving spot cones, automatic
bone updates, `precompiled_shaders`, and `Scene.close()`. The earlier option setter, format-specific
loaders, explicit copy and bone calls, flush call, GPU-time field, and backend argument are gone.
All 308 non-PsychoPy tests passed on Windows Python 3.12 in 132 seconds. All 22 PsychoPy tests
passed on Python 3.11 with psychopy-lib 2026.2.4 in 26 seconds. Linux was not retested.

- sRGB output of unlit grey matched the analytic transfer function within one 8-bit level for all
  256 8-bit input levels and the 1/64 grid (305 of 319 values exact); linear output matched
  exactly. 0.5 grey gave 188 (sRGB) and 128 (linear) offscreen, in a host `GL_RGBA8` texture read
  by the host, and after a host draw into a non-sRGB framebuffer.
- The gltf_viewer comparison with `tone_mapping="aces_legacy"` gave the earlier metrics:
  DamagedHelmet MAE 0.0001, maximum 1, PSNR 89.45 dB; TransmissionTest MAE 0.0000, maximum 1,
  PSNR 101.07 dB.
- The five examples ran for 60 frames each, the stress example ran two cycles with resources
  restored, and `filly.benchmark` ran 10,000 DamagedHelmet frames at 1920 x 1080
  (submission p50 0.33 ms, 1.29 ms batch time per frame).

### September 29 changes

The September 29 changes return perspective transmission shading to Filament's own,
and add sun lights, lens projection, prefiltered KTX environments, all Filament tone mappers,
optional SSAO, bloom, and dithering, texture-count checks, and closing after the host window.
All 254 non-PsychoPy tests passed on Windows Python 3.12 in 123.56 seconds. All 22 PsychoPy tests passed on Python 3.11 with psychopy-lib 2026.2.4 in 10.96 seconds. Linux was not retested.

The [gltf_viewer comparison](../how-to/reference-comparison.md#results) now matches all
Filament-material assets to a maximum difference of 2, including TransmissionRoughnessTest,
TransmissionTest, and AttenuationTest, except MosquitoInAmber. That asset is an expected
divergence: studio MAE 3.07, maximum 175, in both material modes. Its amber is under a 0.1 parent
scale. filly applies the complete node transform to volume thickness, as
`KHR_materials_volume` requires; Filament 1.77.1 uses only the mesh node's own scale (an upstream
TODO). A control build with Filament's scale matched at maximum 1. Volume assets whose meshes have
no scaled parents match.
New tests cover:

- Orthographic rough glass: blur that stays visible, is unchanged by uniform scene scale and by
  a camera 30 times farther away, and equals a 45-degree perspective view of the same height.
- Volume thickness: mesh, parent, and model-root scales give the same absorption.
- Texture limits, in child processes: 8 textures render and 9 raise `AssetError` in compiled mode;
  9 and 11 textures render in fast mode; generated materials report their limit; two fast-mode
  combinations that aborted before now match compiled mode within 1.
- Tone mappers: all 11 names produce distinct images, and the default equals `"aces_legacy"`.
- SSAO, bloom, and dithering: each changes pixels only when enabled.
- Sun light: the largest difference from an equal directional light is at the specular peak, and
  the disc appears in the skybox.
- KTX environments: spherical-harmonics diffuse color, missing metadata, truncated and non-KTX
  files, and the SDK's `lightroom_14b` IBL and skybox.
- Host windows: import with a pending host GL error, and target and renderer close after the
  window closes, in both orders.

The benchmark command ran on DamagedHelmet at 1920 by 1080 pixels, 10,000 frames, 30 warmup
frames, no postprocessing:

| Measurement | Milliseconds |
| --- | ---: |
| CPU submission, median | 0.351 |
| CPU submission, 95th percentile | 0.825 |
| CPU submission, 99th percentile | 0.928 |
| Completion wait after the batch | 1,476 |
| Batch time divided by frame count | 0.536 |
| One image readback | 6.69 |

The loop is unthrottled. It logged the FrameInfo warning for 9,924 of 10,000 frames; see
the [API reference](api.md#benchmark-command). The completion wait shows that the GPU was about
1.5 s behind at the end of the batch. These numbers do not isolate GPU time.

### Earlier results

All 206 non-PsychoPy tests passed on Windows Python 3.12 in 62.21 seconds after the draft
`KHR_materials_volume_scatter` and `KHR_materials_retroreflection` support was removed. The
PsychoPy tests were not rerun after that change. Earlier runs followed the
[Khronos sample audit](sample-assets.md) corrections, large-scene command-buffer fix, lighting
updates, and volume-distance refraction correction. These tests cover modern meshopt decoding,
inherited and animated visibility, instance transforms, diffuse volume attenuation, vertical
texture orientation, and the handling of draft and metadata extensions. PsychoPy comparisons
check ordinary and shared targets with and without postprocessing, including transparent output.
Lighting tests check all six point-shadow directions, directional/spot/point shadow controls,
resolution changes, and invalid bias settings. Skybox tests cover visibility, rotation, intensity,
alpha, replacement, and cleanup. These tests do not establish reference-image parity.
The recent lighting, refraction, command-buffer, and handle-arena changes have not yet been
rerun on Linux.

NodePerformanceTest completed both audit captures in 5.78 seconds, including asset loading,
shader compilation, rendering, and PNG output. This is not a steady-state frame-time measurement.
Two isolated regression tests generate 10,000 separate meshes and materials and verify repeated
load, render, material edit, and cleanup through offscreen and shared-texture engines. Both tests
reproduced the native command-stream overflow in a control build with the original SDK defaults.
Both pass with the 24 MiB command arena and 8 MiB minimum batch size. With the SDK's default
handle arena, and with a 16 MiB arena, the driver logged that its handle arena was full and used
heap allocations. With 24 MiB and the configured 32 MiB, the warning did not appear. The tests
now fail if it appears.

The PsychoPy presentation test now presents the startup background before checking stimulus
frames. A later audit did not reproduce a black first front-buffer read with a plain PsychoPy Rect
in 10 runs on Intel or in runs on NVIDIA. The black first Filament frame came from destroying
the IBL prefilter objects after `set_environment()`. See
[tested assumptions](../explanation/assumptions.md).

The TrafficCone PsychoPy demo completed 90 frames at 960 by 640 using its imported camera and
authored light, with 10 warmup frames excluded. CPU submission was 0.220 ms median and 0.310 ms
at p95; shared-texture wait was 0.698 ms median and 1.132 ms at p95. Flip intervals were 16.657 ms
median and 17.151 ms at p95. This short smoke run does not measure GPU-only time or display latency.

The earlier 109-test suite passed on
Python 3.11 in 30.20 seconds; the six new stress-report tests passed there in 0.13 seconds.
The Python 3.11 PsychoPy dependencies installed using a prebuilt pywinhook wheel.

The earlier Python 3.12 wheel was installed in a separate virtual environment. Forty tests passed
in 12.41 seconds. Ten tests skipped because Pillow, pyglet, and PsychoPy were absent.
These durations include setup and are not renderer performance comparisons.

### Dielectric iridescence preview

All 23 targeted surface-material, camera, and dielectric-iridescence tests passed in 8.31 seconds.
The dielectric fixture checks that film thickness changes reflected color in both material modes,
and has no effect when the film factor is zero. This checks parameter response, not shader conformance.
The PsychoPy sphere demo completed 90 frames with environment lighting and an angled orthographic
camera. With 10 warmup frames excluded, flip intervals were 16.677 ms median and 17.776 ms at p95.
Both audit modes produced updated captures. This update changes preview controls and documentation;
it does not change the native shader. The 236-test result above is the preceding full-suite run.

### Glass filtering

This section describes the filter that the September 29 changes replaced. Its tests were removed
or rewritten with it.

The glass tests cover thin transmission and volume materials with orthographic cameras and
45-, 90-, and 110-degree perspective cameras. Increasing roughness must reduce image contrast;
IOR 1 with zero specular reflection must preserve the background. A control build with the
original SDK filter failed three roughness cases: orthographic, 90 degrees, and 110 degrees.
The correction preserves shadow and environment-reflection pixels at zero transmission.
PsychoPy tests compare ordinary and shared-target images for orthographic and wide-angle rough glass.

The volume-distance correction adds checks for uniform scene scaling in both material modes,
convergence of a distant perspective view toward an orthographic view, increasing blur with
travel distance, and the zero-thickness thin-sheet case. The fixed-distance control fails four
cases: orthographic scaling in both modes, projection convergence, and travel-distance response.
The corrected targeted run passed all 55 refraction, volume, and PsychoPy tests in 19.58 seconds.

Seven cached Khronos samples rendered in both compiled and fast modes: MosquitoInAmber,
ChronographWatch, TransmissionRoughnessTest, TransmissionTest, CompareDispersion, DispersionTest,
and GlassVaseFlowers. Both local galleries were refreshed after the distance correction. These are
smoke checks. Amber interior detail is restored in the orthographic comparison; layered
transmission, offscreen content, and transport through the gap to background geometry remain limits.

`tools/benchmark_refraction.py` captures the same asset with both projections and records frame
submission and completion times. It adds no per-frame image readback; PNG captures occur after
measurement. Each default run excludes 30 warmup frames and measures 120 render/finish pairs.
The shader correction adds no render passes or textures. Material compilation still occurs during
loading, including transmission materials in fast mode.

At 512 by 512 pixels on the Windows/Intel setup above, the amber run measured:

| Projection | CPU submission p50 / p95, ms | Render plus finish p50 / p95, ms |
| --- | ---: | ---: |
| Orthographic | 0.151 / 0.273 | 2.355 / 4.795 |
| Perspective, 45 degrees | 0.101 / 0.176 | 2.404 / 3.922 |

These sequential runs include driver scheduling and CPU/GPU synchronization. They do not isolate
the shader's GPU cost or establish a speed comparison between projections.

### Linux portability and limited ABI

The same installed `cp312-abi3` manylinux wheel passed the full non-PsychoPy suite in these runs:

| Environment | Python | Result | Time |
| --- | --- | --- | ---: |
| manylinux_2_28, Xvfb, Mesa 23.1.4 software rendering | 3.12.14 | 121 passed | 51.32 s |
| manylinux_2_28, Xvfb, Mesa 23.1.4 software rendering | 3.14 | 121 passed | 51.50 s |
| WSL Ubuntu 24.04, WSLg, Mesa 24.0.9 Intel D3D12 | 3.12.3 | 121 passed | 46.76 s |

The two manylinux runs executed concurrently. These times include contention and are not
performance comparisons. Linux builds used Clang 21.1.8 and Filament 1.77.1 with the source
corrections described in [design](../explanation/design.md#dependencies).

The tests exercise offscreen rendering, explicit shutdown, and shared textures. The GLX tests
use pyglet directly. Linux PsychoPy presentation, native Wayland/EGL, macOS, and ARM were not tested.
The GitHub Actions workflow has been configured locally; a hosted workflow run remains unverified.

### Rendering and ownership

The tests cover rendered pixels, image orientation, both camera projections, model movement,
visibility, restored scene state, independent targets, path and byte assets, external buffers,
input checks, ownership, thread checks, and explicit shutdown.

Additional tests cover named node hierarchy, Euler and quaternion conventions, scale, material
color and PBR factor overrides, independent asset materials, ambiguous names, and closed targets.
The screenshot exposed an extra vertical flip in NumPy readback. That flip was removed, and the
orientation assertion now checks occupied rows near both ends of the image.

Five raw WGL/GLX cases check imported texture validation, host ownership, and synchronization.
Two cases queue 256 host draws into separate image regions without intermediate readback, then
check all regions. They alternate material colors or background colors. A contention case runs
both checks in two processes at once. These checks exercise both the renderer-to-host fence and the host-to-renderer fence
before texture reuse. The normal frame path uses GPU waits, with no unconditional GPU finish.

The PsychoPy test checks sampled pixels and orientation, forbids target readback, and counts host
buffer swaps. Rendering and stimulus drawing do not swap buffers; each explicit window flip does.
It covers postprocessing both enabled and disabled. With postprocessing enabled, shared output
is compared with a separate offscreen reference, since color grading changes RGB values.

### Asset features

Generated fixtures test glass over visible geometry; clip time jumps, looping and endpoint holds;
authored morph-weight restoration; skin matrices after joint edits; material variants; imported
animated lights; editable lights and exposure; directional shadows; and environment lighting.
Additional fixtures test Radiance HDR loading, WebP conversion, Basis Universal KTX2 decoding,
strict compatibility checks, and a material combination that requires compiled mode.

Diffuse-transmission tests check direct backlighting, environment backlighting, and transformed
base-color and transmission-color textures. They do not establish full extension conformance.

ChronographWatch and DiffuseTransmissionPlant were loaded from the Khronos sample assets and
rendered at multiple animation times. The watch dial is visible through the glass. The plant's
two point-light nodes change position, and its illumination changes between time samples.
Both models were also captured from the PsychoPy front buffer with the regular text overlay.
The plant preview uses EV100 0; studio lighting uses EV100 15.

Postprocessing initially produced black or corrupt output on the Intel GPU. Disabling Filament's
framebuffer-fetch color-grading subpass corrected it. The NVIDIA driver does not expose framebuffer
fetch, so it never uses that subpass. Separate color grading, FXAA, MSAA, and
refraction now pass pixel checks. Returning to a prior scene state restores identical pixels in
the tested postprocessing configurations. No temporal effects are enabled.

### Presentation correction

The initial PsychoPy test checked pixels only in its offscreen buffer before the flip. The
example displayed a black window with `useFBO=True`, despite correct pixels in that buffer.
The same failure occurred with a plain PsychoPy rectangle without creating a Filament renderer.
A later audit reproduced it on NVIDIA as well: with `useFBO=True`, PsychoPy 2026.2.4 and pyglet
1.4.11 alternate between the clear color and black and never show stimuli.

The example and setup guide now use `useFBO=False`. A capture of the visible example window
confirmed that Suzanne was displayed. The PsychoPy test now covers both window settings and
checks front-buffer pixels after each flip for the example's `useFBO=False` setting. Both test
cases passed on Python 3.11 and 3.12. FBO sampling remains tested, but FBO presentation is not supported
on this configuration. The host FBO limitation is separate from the native postprocessing correction.

### Text overlay correction

The earlier Python 3.12 tests did not create a `TextStim`, so they missed pyglet 1.4.11's signed-byte
glyph-buffer error. The adapter no longer patches pyglet. The examples draw their labels with
`visual.TextBox2`, anchored top-left with left alignment. The display tests create and draw the
regular Suzanne label before checking its pixels, including front-buffer pixels with `useFBO=False`.
After the `TextBox2` change, `examples/psychopy_shared.py --frames 60` completed on Windows
Python 3.12. The PsychoPy display tests were not rerun for that change.

One test submits 10,000 model transform updates and checks the final image and submission count.
It does not measure GPU resource growth. Repeated renderer creation and shutdown occur across
the test suite, but this is not a long-duration leak test.

### Compositing and resource lifetime

Transparent output passes pixel checks with postprocessing enabled and disabled. PsychoPy tests
draw half-transparent geometry over a blue underlay at stimulus opacity 0.5 and 1.0. They compare
the full image with premultiplied composition of an offscreen reference, including FXAA edges.
An overlay drawn afterward checks that ordinary PsychoPy drawing still works. Background alpha
and its premultiplied RGB are also checked.

Cleanup tests check retained model, node, material, and light handles after close. They verify
that unrelated assets remain visible, imported light nodes remain editable, and a new light does
not reactivate an old handle. Thirty load/copy/render/close cycles return the tracked model and
material-copy counts to zero. These counts do not measure driver memory or compiled shader caches.
Material tests verify independent mesh colors, variant replacement, shared textures, and
environment changes on copied diffuse-transmission materials.

### glTF property animation

Thirty additional cases cover the supported `KHR_animation_pointer` subset. Tests check STEP,
LINEAR, and CUBICSPLINE interpolation, including time-scaled tangents, final keys, looping,
arbitrary time jumps, and reset. Mixed clips preserve their original indices and hold shorter
node tracks at their final pose. Rendered pixels verify material color and light color/intensity.
Lights initially set to zero intensity can brighten, including lights without an authored range.

Other cases cover material extension parameters, duplicate names, compiled and fast materials,
independent material copies, multiple nodes using one light definition, closed light handles,
normalized integer output, sparse and strided accessors, external buffers, and data URIs.
Malformed keyframes and unsupported required targets fail explicitly. Optional unsupported
targets warn. Invalid dispersion without volume is rejected before native shader compilation.

A CPU smoke measurement evaluated one cubic scalar material channel on the generated triangle
10,000 times after 1,000 warmup calls. On Python 3.12, median evaluation time was approximately
0.0002 ms; p95 and p99 were 0.0003 ms. Measurements include Python call and timer overhead at
sub-microsecond scale. No rendering or GPU work was measured. This fixture does not predict
performance for large animated assets.

Nineteen further cases cover anisotropy, iridescence, imported cameras, and UV animation.
Pixel checks verify scalar material animation, texture channels, environment reflections,
and reset. Combined offset, rotation, and negative-scale animation matches a static texture
transform in compiled and fast modes. Surface materials also compile extension combinations
in fast mode. Invalid factors, projections, and texture transforms fail during preflight.

Camera tests cover perspective and orthographic projections, grouped near/far channels,
an omitted perspective far plane, parent transforms, world-space pose setters, and stale handles.
Closing a camera's model also clears its selection in another scene. The original
IridescenceSuzanne asset completed a 30-frame PsychoPy smoke run with studio lighting.
These checks do not establish full glTF conformance or exact Khronos reference-renderer parity.

Further cases check pointer-based node translation, rotation, scale, and morph weights;
animated light range and paired spot-cone angles; material IOR; and clearcoat normal scale.
Pixel checks cover reset and compiled/fast material modes. Light tests preserve intensity,
reject invalid combined cones, and keep closed light handles inactive.

The Windows and Linux wheels include the PsychoPy adapter and statically linked Filament:

| Wheel | Compressed bytes | Native extension bytes |
| --- | ---: | ---: |
| `filly-0.1.0.dev0-cp311-cp311-win_amd64.whl` | 5,808,922 | 14,095,872 |
| `filly-0.1.0.dev0-cp312-abi3-win_amd64.whl` | 5,806,748 | 14,092,288 |
| `filly-0.1.0.dev0-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | 5,482,894 | 14,313,592 |

The compressed wheels are about 5.2 to 5.5 MiB. The material compiler and environment-filtering
code increase this from the earlier 2.44 MiB Windows build. No separate Filament library is needed.
Both `cp312-abi3` wheels passed `abi3audit --strict`. Auditwheel repaired the Linux wheel and
confirmed compatibility with manylinux_2_28; it also assigned the compatible manylinux_2_27 tag.
The operating system must supply its OpenGL driver and platform runtime libraries.

## Benchmark smoke test

The point-shadow comparison used TrafficCone's authored camera and point light at 640 by 360
pixels, with a visible constant environment at intensity 1, postprocessing, and FXAA. Each
configuration had 30 warmup frames and 120 measured render/finish pairs on the Intel Iris Xe.

| Point shadow resolution per face | CPU submission median / p95 ms | Render and finish median / p95 ms |
| --- | ---: | ---: |
| Shadows off | 0.302 / 0.705 | 2.633 / 3.667 |
| 512 | 0.537 / 0.816 | 3.144 / 4.597 |
| 1024 | 0.788 / 1.021 | 4.576 / 5.701 |

Completion time includes CPU work and a GPU wait. It is not isolated GPU time. The source
script and raw samples are local artifacts under `.deps/profile-lighting.py` and
`.deps/lighting-profile-*.csv`.

The TrafficCone PsychoPy demo also completed 90 frames at 960 by 640 with 512-pixel point
shadows, the visible studio environment, and a 45-degree environment rotation. Excluding ten
warmup frames, flip intervals were 16.739 ms median and 18.362 ms at p95; native submission was
0.771 ms median and 1.081 ms at p95. This does not establish high-refresh-rate deadlines.
The demo command is in the [PsychoPy guide](../how-to/psychopy.md#lighting-and-shadows).

The stress benchmark completed 30 Suzanne trials at 960 by 640 pixels on Python 3.12. Each trial
used 30 warmup frames, 120 measured frames, four material copies, and transparent compositing
over a regular PsychoPy grating. The run took 78.28 seconds, including setup and cleanup.
All 3,600 measured intervals were below the late threshold at the observed 59.90 Hz flip rate.
Flip interval p50/p95/p99 values were 16.678/17.220/17.615 ms; native CPU submission values were
0.112/0.162/0.267 ms. Model, light, and material-copy counts returned to baseline after every trial.

Windows process and GPU memory counters returned valid samples. Excluding the first trial,
private committed memory changed by -3,256,320 bytes from the first to last cleanup sample;
GPU shared memory changed by +32,768 bytes. GPU shared samples ranged from 172,388,352 to
173,600,768 bytes. These process-wide counters include PsychoPy and driver caches. The short
run does not establish the absence of leaks.

A Python 3.11 smoke run alternated ChronographWatch and DiffuseTransmissionPlant over four
trials, with 240 measured frames and material copying disabled. Resource counts returned to
baseline. Requesting a 120 Hz budget on the observed 60.26 Hz display marked all 240 intervals
late and left estimated missed refreshes `null`, as required for a refresh-rate mismatch.
Six report-calculation tests also passed on Python 3.11 and 3.12. See the
[stress benchmark guide](../how-to/stress-test.md) for commands and report definitions.

The transparent Suzanne demo completed 300 frames at 960 by 640 pixels on Python 3.12, with a
regular PsychoPy grating underneath and a text overlay. After 30 warmup frames, the recorded
CPU timing percentiles were:

| Measurement | Median ms | 95th percentile ms | 99th percentile ms |
| --- | ---: | ---: | ---: |
| Native submission | 0.148 | 0.293 | 0.360 |
| Host fence publication wait and GPU-wait submission | 0.648 | 1.361 | 1.589 |
| Host release | 0.040 | 0.121 | 0.193 |
| Host drawing, including wait and release | 1.155 | 2.228 | 2.897 |
| Window flip | 15.045 | 16.080 | 16.552 |
| Interval between flip returns | 16.673 | 17.387 | 17.782 |

The run used normal window flip pacing. Drawing includes the handoff costs; do not add these
columns. The CSV is written after the loop. This run validates the profiler and does not establish
120/144 Hz deadlines, isolated GPU time, or physical display latency.

In the initial offscreen increment, a generated unlit triangle was rendered at 64 by 64 pixels.
The command used 30 warmup frames and 100 measured frames. These figures predate shared-texture
support and do not measure its overhead.

| Measurement | Milliseconds |
| --- | ---: |
| CPU submission, median | 0.0289 |
| CPU submission, 95th percentile | 0.0372 |
| CPU submission, 99th percentile | 0.0541 |
| Completion wait after the batch | 14.6867 |
| Batch time divided by frame count | 0.1779 |
| One image readback | 1.1054 |

These values only check that the benchmark works. The scene and resolution are too small to
establish experimental performance. GPU-only time and display latency were not measured.
Shared texture output, synchronization across contexts, and host-controlled flips have functional
tests on the Windows and Linux configurations above. Display latency, missed refresh deadlines,
calibrated color, and other GPU vendors remain unmeasured. Version 1 acceptance still needs
representative 120/144 Hz measurements and long-duration GPU resource-growth checks.
