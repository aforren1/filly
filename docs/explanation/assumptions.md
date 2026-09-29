# Tested assumptions

This page records control experiments for ten statements in the code and documentation.
Each section gives the claim, the experiment, the result, the verdict, and the change.

## Test setup

- Date: 2026-09-28. Filament 1.77.1 SDK, Windows 11, OpenGL backend.
- Intel: Iris Xe, driver 4.6.0 Build 32.0.101.7088. This is the default WGL device.
- NVIDIA: RTX A500 Laptop GPU, driver 596.58. A per-executable GPU preference selected it.
- PsychoPy: psychopy-lib 2026.2.4 with pyglet 1.4.11 on Python 3.11.
- Probe builds added environment switches to a scratch copy of the code. The probe scripts
  and images are not part of the repository.
- Other processes used the GPU during the tests. Frame rates varied by up to 2x between
  identical runs. The timing numbers show direction only.

"Max diff" is the largest per-channel difference in 8-bit values.

## 1. Framebuffer-fetch color-grading subpass

**Claim.** The Intel Iris Xe driver corrupts Filament's framebuffer-fetch color-grading subpass.
The wrapper therefore sets `d.renderer.disable_subpasses` for every engine.

**Experiment.** `p1_subpass.py` renders DamagedHelmet with and without the setting. It covers
tone mapping `aces` and `linear`, FXAA on and off, MSAA 1 and 4, and sizes 64 x 64, 256 x 256,
1280 x 720, and 127 x 93. It renders offscreen and into a pyglet texture, in both material modes.

**Result.**

- Intel exposes `GL_EXT_shader_framebuffer_fetch`. With the subpass, every MSAA 1 case is wrong.
  The output is black with one gradient tile. At 1280 x 720, 98% of the pixels are black.
  Offscreen, shared, `compiled`, and `fast` fail the same way.
- With MSAA 4, Filament does not use the subpass. Both settings give identical pixels.
- NVIDIA does not expose the extension. Both settings give identical pixels in all 128 cases.
- With the subpass disabled, Intel and NVIDIA agree. 0.3% of pixels, at edges, differ by more
  than 8. Max diff is 41.

**Verdict.** Confirmed on Intel. The failure is not specific to shared textures, material mode,
or size. On drivers without framebuffer fetch, the setting has no effect.

**Change.** Keep the global setting. Its effect is already limited to drivers that expose framebuffer
fetch. Filament's workaround registry (`OpenGLContext::initBugs`) is internal to the prebuilt SDK.
It has no Intel Windows entry, so a driver-specific fix needs an upstream report. The Mesa Intel
driver on Linux was not tested. Correct the code comment and design text.

## 2. PsychoPy `useFBO=True`

**Claim.** PsychoPy's FBO blit produces a black window, so every example forces `useFBO=False`.

**Experiment.** `p2_psychopy.py` opens a visible 256 x 256 window in a new process for each case.
It draws a rectangle whose color changes each frame, with and without a Filament stimulus.
After each flip it reads the front buffer, calls `win.getMovieFrame()`, and takes an OS screenshot.

**Result.**

- PsychoPy's default is `useFBO=False` (`psychopy/visual/window.py`, line 190).
- With `useFBO=True`, on Intel and NVIDIA, the window alternates between the clear color and black.
  The rectangle never appears. The front buffer, the movie frame, and the screenshot agree.
  A warm-up flip does not help. Filament is not needed to cause it.
- With `useFBO=False`, all three reads show the correct frame from frame 0 on both GPUs.

**Verdict.** Partly true. `useFBO=True` does not work, but the cause is PsychoPy 2026.2.4 with
pyglet 1.4.11. It is not Intel-specific and not related to Filament.

**Change.** Keep `useFBO=False`; it is the default. Correct the reason in the example, the
how-to page, and the PsychoPy test.

## 3. Command-buffer size

**Claim.** `minCommandBufferSizeMB = 8` and `commandBufferSizeMB = 24` are necessary because
gltfio queues a whole asset before it flushes. The SDK defaults abort on 10,000 meshes.
The documentation states a cost of 21 MiB.

**Experiment.** `p3_cmdbuf.py` loads NodePerformanceTest in a new process for each setting.
`p3_memory.py` measures process commit after engine creation.

**Result.**

| Minimum / total (MiB) | Result |
| --- | --- |
| 1 / 3 (SDK default) | Abort: 6,425,912 bytes in one batch |
| 1 / 3, flush between `createAsset()` and `loadResources()` | Abort: 6,424,904 bytes |
| 1 / 3, flush every 256 or 1,024 material instances | Abort: same size |
| 1 / 6, 2 / 6 | Abort |
| 1 / 8, 2 / 8, 4 / 12, 8 / 24 | Loads and renders |

- The whole batch comes from `AssetLoader::createAsset()`, about 640 bytes per mesh.
  No public hook can flush inside that call.
- Windows commits the ring twice for its mirror mapping. Private bytes after engine creation:
  644 MB at 1 / 3, 649 MB at 1 / 8, 681 MB at 8 / 24. The current setting costs about 37 MiB
  more than the default, not 21 MiB.
- The SDK handle arena also fills and logs a warning. Load time and frame time with the heap
  fallback were not slower than with the 32 MiB arena.

**Verdict.** Partly true. A larger ring is necessary. A flush in the load path cannot replace it.
The minimum batch size of 8 MiB is not necessary. The stated memory cost is wrong.

**Change.** Use the default 1 MiB minimum and a 12 MiB ring, about 2x the measured need.
The commit cost falls to 24 MiB per engine. Assets with about 18,000 meshes or more can still
abort the process. A preflight size estimate in Python could reject them instead.

## 4. `renderStandaloneView()` and frame rejection

**Claim.** The wrapper uses `renderStandaloneView()` because `beginFrame()` rejects frames for pacing.

**Experiment.** Filament source: `FRenderer::beginFrame()` returns false only after a backend
exception, when `skipNextFrames()` is active, or when the frame fence from the configured latency
is not signaled. Probe builds switch between `renderStandaloneView()` and `beginFrame()`, `render()`,
and `endFrame()` on a 1 x 1 headless swap chain (`p4_frame.py`, `p4_first_frame.py`,
`p4_trigger.py`, `p4_env_order.py`, `p4_shared_first.py`).

**Result.**

- `beginFrame()` returned false for 598 of 721 frames in an unthrottled offscreen loop. The wrapper
  can render anyway. The Renderer API permits this, and the output was correct.
- `renderStandaloneView()` does not call `engine.gc()`, the texture-cache `gc()`, or `driver.tick()`.
  Filament recycles destroyed entity indices only in `engine.gc()`. There are 2^17 indices.
- Entity create and destroy, time per 40,000 lights:

  | Path | First 40,000 | 120,000 to 160,000 | 360,000 to 400,000 |
  | --- | --- | --- | --- |
  | `renderStandaloneView()` | 76 ms | 812 ms | 65,015 ms |
  | `beginFrame()` | 61 to 109 ms | 62 to 113 ms | 66 to 153 ms |

- Unthrottled throughput with `beginFrame()` was about 20% lower on a quiet GPU: 1,469 to 1,695
  frames per second offscreen, against 1,986 to 2,019. CPU submit time per frame was equal, with a
  median near 0.05 ms, and 0.11 to 0.12 ms in a 60 Hz loop. A no-op `commit()` for the headless swap
  chain recovered part of the difference in a noisy run.
- An unthrottled loop with `beginFrame()` logs "FrameInfo's circular queue is full" at warning level,
  about once per frame. A 60 Hz loop did not log it.
- A second defect appeared during this test. When `set_environment()` runs before a model whose
  materials are new to the engine, that model renders black without postprocessing, for all
  60 frames checked. With postprocessing, the first frame is wrong. A model loaded before the call
  is not affected. This occurs on Intel and NVIDIA, offscreen and shared. It caused the dark first
  Filament frame in PsychoPy (item 10).
- The frame path changes which cases fail, but it does not remove the defect. Keeping Filament's
  IBL prefilter objects alive for the life of the renderer removes it on both paths. The probable
  cause is reuse of backend handles that the prefilter objects free. This is not proven.

**Verdict.** Refuted as a reason. The rejection is advisory. `renderStandaloneView()` is a documented
API, but its header describes it as a low-overhead path and "a poor man's compute API".
It omits the upkeep that long sessions need.

**Change.** Render with `beginFrame()`, `render()`, and `endFrame()` on a headless swap chain.
Ignore the return value. Create the IBL prefilter objects once per renderer. Add regression tests
for both defects. The Linux GLX path was not tested.

**Follow-up, September 29.** The warning comes from `FrameInfoManager::beginFrame()`. It keeps
16 frames and releases one only after the driver thread has processed it. With the engine feature
`engine.frame_info.disable_gpu_complete_metric` set, 2,000 unthrottled DamagedHelmet frames still
logged 1,968 warnings, the same count as without it. A 1 ms or 3 ms sleep per frame logged none
in either case. The CPU runs ahead of the driver thread, not only the GPU. Filament has no switch
for the warning, so the API reference documents it.

## 5. Releasing the host WGL context

**Claim.** The host context must be released while Filament creates its shared context.

**Experiment.** `p5_share.py` creates a shared engine with the host context current and released.

**Result.**

- Released: engine creation works. The shared texture has the expected pixel. The host context is
  current again after creation.
- Not released: `wglCreateContextAttribsARB` fails on the driver thread. Intel reports
  `ERROR_BUSY` (170). NVIDIA reports 0xC00720DD. Engine creation fails with `BackendError`.

**Verdict.** Confirmed on both GPUs.

**Change.** None. Add the error codes to the comment and design text.

## 6. Feature level

**Claim (implied).** The engine runs at feature level 1 with 16 samplers, although the backend
supports level 3. The dummy textures in `materials.cpp` exist because of level 1.

**Experiment.** `p6_feature_level.py` sets levels 1, 2, and 3. It renders DamagedHelmet and
SheenChair in both material modes. It loads a synthetic material with 10 samplers: five standard
textures, three clear-coat textures, and two sheen textures.

**Result.**

- Intel supports level 3; NVIDIA was not checked. Levels 2 and 3 give identical pixels to level 1.
  Frame time did not change beyond noise.
- The engine level does not change material limits. Materials are compiled at level 1 by gltfio's
  `JitShaderProvider` and by the wrapper. Level 1 and level 2 allow 16 samplers per stage. A lit
  material then has 8 or 9 user samplers.
- The 10-sampler material aborts the process in `compiled` mode at engine level 1 and 3.
  The log reports "feature level 1 and is using more than 8 samplers". `fast` mode loads it.
- The dummy textures fill declared sampler parameters that an asset does not use. Filament needs
  every declared sampler bound. This is independent of the feature level.

**Verdict.** The dummy-texture claim is refuted. Raising the engine level alone changes nothing.

**Change.** Keep level 1. Add a preflight check that rejects lit materials with more than 8 textures
in `compiled` mode, or compile materials at level 3. Either change needs its own tests.

**Follow-up, September 29.** Generated assets in child processes bisected the limit. A standard
lit material loads with 8 textures and aborts with 9 in `compiled` mode. The wrapper's own
transmission and volume materials fail with `AssetError` above 8; anisotropy and iridescence
materials fail above 5, because they always declare 3 more samplers. The same run found fast-mode
aborts without any texture limit: sheen, specular, and IOR together, and clearcoat, sheen, and
specular textures together. The archive has no match, and the SDK's default material lacks the
parameters that it then sets. The preflight and a fast-mode compile path now cover these cases.

## 7. External buffers and Unicode paths

**Claim.** External `.bin` buffers must be inlined because the desktop SDK bypasses the URI cache
for buffers and cannot open Unicode paths.

**Experiment.** Source reading of `libs/gltfio/src/ResourceLoader.cpp`, `Utility.cpp`, and cgltf.
`p7_unicode.py` loads FlightHelmet and NodePerformanceTest as `.gltf` files with external buffers.
Each asset is in an ASCII directory and in a directory named `Ünïcødé ❤ 测试`.

**Result.**

- Desktop builds set `GLTFIO_USE_FILESYSTEM`. `loadCgltfBuffers()` then calls `cgltf_load_buffers()`
  with cgltf's default reader, which uses narrow `fopen()`. It does not read the URI cache.
- For images, `ResourceLoader` reads the URI cache first. The wrapper supplies image bytes there.
- `addResourceData()` for a buffer URI only stores the bytes. The desktop loader never reads them.
- Without inlining, both assets load from the ASCII directory and fail from the Unicode directory.
- With inlining or with a GLB binary chunk, all cases load. Pixels are identical in all successful cases.

| NodePerformanceTest (37 MB buffer) | Payload | Preflight | Load |
| --- | --- | --- | --- |
| base64 data URI (current) | 45.0 MB | 0.66 to 0.70 s | 4.0 to 4.5 s |
| GLB binary chunk | 36.8 MB | 0.47 s | 3.3 s |

**Verdict.** Confirmed. The SDK cannot open these buffers itself. Base64 text is not the cheapest fix.

**Change.** Copy all buffers into one GLB binary chunk during preflight. cgltf reads it in place.

## 8. `flushAndWait()` calls

**Claim (implied).** The completion waits are needed for correctness.

**Experiment.** Source reading. `p8_loadwait.py` replaces the wait after `loadResources()` with
`flush()` and times the load and the first frame.

**Result.**

- After `loadResources()`: upload callbacks hold shared references to the source asset and the URI
  cache. Synchronous `loadResources()` waits for texture decoding. `releaseSourceData()` is safe
  without the wait. Pixels were identical without it.
- The wait moves cost out of the first frame:

  | Asset | Load with wait | First frame with wait | Load without | First frame without |
  | --- | --- | --- | --- | --- |
  | DamagedHelmet | 0.39 s | 2 to 7 ms | 0.40 s | 43 to 57 ms |
  | Sponza | 0.88 s | 51 to 55 ms | 0.49 to 0.63 s | 243 to 269 ms |
  | NodePerformanceTest | 3.6 to 3.8 s | 1.4 to 1.8 s | 2.6 to 2.7 s | 2.3 to 2.6 s |

- `Renderer.finish()` waits by request. `OffscreenTarget.read()` must wait for the readback.
- `ImportedTarget.close()` must wait for imported textures, because the host can then delete or
  reuse the texture name. For owned targets the wait is conservative.
- `set_environment()` waits after the prefilter work. Filament orders the destroy commands itself,
  so the wait is conservative. It keeps setup cost out of later frames.

**Verdict.** The load wait is not needed for memory safety. It is useful for experiments.

**Change.** Keep the waits. State the reason in the load-path comment.

## 9. Default material mode

**Claim.** Compiled shaders (now `precompiled_shaders=False`) are the default. The precompiled
archive (then `fast`, now `precompiled_shaders=True`) has known combination restrictions.

**Experiment.** `p9_materials.py` loads five assets in a new process for each mode. It runs the
`fast` preflight on 150 sample assets. `p9_restricted.py` compares both modes for flagged assets.

**Result.**

| Asset | Cold load, compiled | Cold load, fast | First frame, compiled | First frame, fast |
| --- | --- | --- | --- | --- |
| DamagedHelmet | 0.55 s | 0.44 s | 2 ms | 7 ms |
| SheenChair | 0.68 s | 0.34 s | 66 ms | 281 ms |
| ClearCoatCarPaint | 0.43 s | 0.29 s | 2 ms | 1 ms |
| IridescenceLamp | 0.67 s | 0.66 s | 52 ms | 75 ms |
| ToyCar | 0.62 s | 0.46 s | 59 ms | 58 ms |

- A second load of the same asset took the same time in both modes.
- The `fast` preflight flagged 3 of 150 assets: CommercialRefrigerator, CompareClearcoat, and
  StainedGlassLamp. CommercialRefrigerator rendered identically in both modes; CompareClearcoat had
  max diff 1. StainedGlassLamp was not rendered. CommercialRefrigerator's flagged material uses
  transmission, which the wrapper's own generator compiles in both modes.
- Separately, the preflight rejects AnimationPointerUVs with "Duplicate animation pointer target".

**Verdict.** The restrictions exist in the preflight rules, but they did not change pixels in the
tested cases. The latency cost of `compiled` mode is 0.1 to 0.35 s per new asset.

**Change.** Keep `compiled` as the default. Render one warm-up frame after each load, because
both modes compile GL programs at the first draw.

## 10. Black first front buffer in PsychoPy

**Claim.** Intel WGL can return a black front buffer on the first visible presentation, also with
a plain PsychoPy rectangle and no Filament engine.

**Experiment.** `p2_psychopy.py` with the window arguments of `tests/test_psychopy.py`, except a
256 x 256 size. There is no warm-up flip. The front buffer is read after the first flip. This ran
10 times on Intel. On NVIDIA, the standard probe window ran with and without a warm-up flip.

**Result.**

- All front-buffer reads, movie frames, and screenshots showed the correct first frame.
- With Filament, the first frame was dark on Intel and NVIDIA. The probe called `set_environment()`
  before it loaded the model. This is the second defect in item 4, not a front-buffer problem.
- A probe error gave a false black result. The window origin was measured before the first flip,
  and PsychoPy moves the window at that flip. The screenshot then showed the desktop.

**Verdict.** Not reproduced. The observed black first frame was a renderer defect.

**Change.** Remove the claim from the test comment and the validation record. The warm-up flip
can stay because the test counts swaps after it.

## Other findings

- `tests/test_refraction.py::test_volume_filter_is_invariant_to_scene_units[45-fast]` fails on
  NVIDIA with and without the recommended changes. 2.1% of pixels differ by 3, and the tolerance is 2.
  The test was removed on September 29 with the volume-distance filter that it checked.
- PsychoPy tests did not run with the recommended changes. That needs a Python 3.11 build.
- The non-PsychoPy suite passed with the recommended changes on Intel: 215 passed, 31 skipped.
  The run used the tree as of 2026-09-28. Later changes to the PsychoPy and host integrations
  were not tested with these changes.
