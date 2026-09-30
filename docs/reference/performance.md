# Performance

Measured frame costs of filly on the development laptop, and the frame time that changes to
filly or to a scene would recover. The tools and the method are in
[Profile a frame](../how-to/profile.md).

## Conditions

| Item | Value |
| --- | --- |
| Machine | Optimus laptop: Intel Iris Xe (driver 32.0.101.7088) drives the 1920 x 1200, 60 Hz panel; NVIDIA RTX A500 Laptop GPU (driver 596.58, 32.0.15.9658). |
| Power | AC for every result in this page unless a section says "battery". Windows power scheme Balanced, no power-mode overlay. |
| Load | CPU load before each run: median 15 to 16%, maximum 35%. Runs waited while the load was above 35%. |
| Build | Scratch venvs built with `tools/profile_tracy.py build --no-tracy`, which adds only `Renderer._frame_info_history()`. [Output path (September 30, 2026)](#output-path-september-30-2026) names its builds. The other sections used the tree of the morning of September 30: runtime material path (`w-rt`), archive path with 39 entries (`w-ar`), and runtime with the headless-swap candidate fix (`w-ns`), all with color grading as the default output path. PsychoPy runs use `.deps\psychopy311` (runtime path). |
| Loop | `profile_frame.py cpu`: 120 warmup frames, 600 measured frames. Offscreen frames are paced by a sleep at 60 Hz (or 144 Hz where stated). Hosts swap with vsync at 60 Hz. The model turns 0.5 degrees per frame. |
| Rounds | 5 rounds; each round runs every configuration once in a fresh process, in an order rotated by one per round. |
| Cells | Median of the 5 per-round medians, with the range of the per-round medians in brackets. Milliseconds. |
| GPU time | "GPU frame" is Filament's own timer, `Renderer::getFrameInfoHistory()` `gpuFrameDuration`: a `GL_TIME_ELAPSED` query from `beginFrame()` to `endFrame()`. It includes GPU idle gaps inside the frame and the headless `SwapBuffers` (a `glFlush()` since the swap fix), and excludes the host draw. |

Scene: DamagedHelmet (or the named asset) filling the view, one directional light, a small
studio environment for image-based lighting, background color. "Empty" is the same scene
without a model.

Assets: plain = DamagedHelmet, clearcoat = ClearCoatCarPaint, sheen = SheenChair,
anisotropy = CompareAnisotropy, specular = CompareSpecular. In the two Compare assets, half of
the objects use the plain material.

## Headline

Intel Iris Xe, 1920 x 1080 offscreen, 60 Hz pacing, AC, GPU frame time, measured on
September 30 after the output-path change:

| Change | GPU saved at 1080p | GPU saved at 512 x 512 |
| --- | ---: | ---: |
| Exact output path instead of color grading (the new default) | 0.84 ms (DamagedHelmet), 0.82 ms (empty) | 0.15 ms, 0.11 ms |
| Direct output instead of the exact path (opt-in) | 0.71 ms (DamagedHelmet), 0.70 ms (empty) | 0.12 ms, 0.11 ms |

The default frame for DamagedHelmet went from 3.16 to 2.32 ms (1.61 ms direct); an empty frame
from 1.62 to 0.80 ms (0.11 ms direct). On NVIDIA, DamagedHelmet went from 0.367 to 0.275 ms
(0.181 ms direct). The exact path costs about 0.12 ms more CPU per frame (native submit), because
its encode pass is a second Filament view. Color grading cost 1.5 ms per 1080p frame on Intel
mostly because of its 1D LUT: three fetches of a 3D texture per pixel. See
[Output path (September 30, 2026)](#output-path-september-30-2026).

Earlier headline, before the output-path change (color grading by default, headless
`SwapBuffers` not yet fixed):

| Change | GPU saved at 1080p | GPU saved at 512 x 512 |
| --- | ---: | ---: |
| Direct output instead of color grading | 1.89 ms (DamagedHelmet), 1.96 ms (empty) | 0.66 ms, 0.77 ms |
| No MSAA 4x | 1.20 ms | 0.35 ms |
| No FXAA | 0.99 ms | 0.24 ms |
| Runtime instead of archive materials | 0.38 ms (plain) to 1.18 ms (anisotropy) | 0.17 to 0.48 ms |
| No shadows | 0.68 ms | 0.36 ms |
| No `SwapBuffers` on the headless swap chain | 0.46 ms | not measured |

On NVIDIA, the headless `SwapBuffers` blocked Filament's frame for a full display refresh:
Filament's timer reported 16.2 ms for a frame whose work takes 0.37 ms. Replacing the swap with
`glFlush()` removed this. See [Headless swap chain](#headless-swap-chain).

A shared-texture host adds about 1.8 ms of main-thread CPU time per frame at 1080p (2.1 ms
against 0.26 ms offscreen), most of it the wait in `acquire()`. The host draw costs 0.39 ms of
GPU time for the pyglet blit and 0.78 ms for PsychoPy's `ImageStim` at 1080p.

## Channel-mixing tone mappers (September 30, 2026)

A tone mapper that mixes channels (`aces_legacy` here) with sRGB encoding now lets Filament's
color grading encode into an RGBA8 buffer, and a graded variant of filly's encode pass copies it.
See [output encoding](../explanation/design.md#output-encoding). `before` is the tree with the
linear 3D LUT output in the RGBA16F buffer; `after` is the changed tree. Both are
`tools/profile_tracy.py build --no-tracy` builds, `profile_frame.py cpu helmet-aces` (new
scenario) and `helmet` (linear tone mapping, as a control). Intel Iris Xe unless stated; NVIDIA
RTX A500 through `SHIM_MCCOMPAT=0x800000001`. AC, Balanced scheme, 60 Hz pacing, 5 rounds,
14:48 to 15:00. CPU load before runs: median 12%, maximum 35%.

| Scenario | GPU frame, before | GPU frame, after | Native submit (CPU), before | after |
| --- | ---: | ---: | ---: | ---: |
| ACES legacy, 1080p | 3.387 [3.299-3.392] | 3.167 [3.042-3.189] | 0.320 [0.303-0.399] | 0.320 [0.269-0.426] |
| ACES legacy, 512 | 0.979 [0.970-0.982] | 0.890 [0.881-0.897] | 0.281 [0.263-0.290] | 0.286 [0.197-0.395] |
| ACES legacy, 1080p, FXAA | 4.313 [4.214-4.358] | 4.135 [4.039-4.158] | 0.362 [0.286-0.380] | 0.397 [0.354-0.463] |
| ACES legacy, 1080p, NVIDIA | 0.394 [0.392-0.399] | 0.349 [0.349-0.355] | 0.276 [0.263-0.340] | 0.224 [0.205-0.296] |
| Linear, 1080p (control) | 2.308 [2.093-2.317] | 2.300 [2.220-2.315] | 0.266 [0.254-0.370] | 0.298 [0.250-0.309] |

- The GPU frame is 0.18 to 0.22 ms shorter at 1080p on Intel and 0.045 ms on NVIDIA: color
  grading writes 4 bytes per pixel instead of 8, and the encode pass reads them and applies no
  transfer function. The CPU ranges overlap.
- Two rejected variants, measured the same way: one encode material with the viewport test for
  both routes cost the linear control 0.045 ms (2.364 against 2.319 ms); a graded material that
  kept the transfer function behind the viewport test cost 1.0 ms more than the variant above
  (4.213 ms in an earlier session of the same matrix).

## Output path (September 30, 2026)

The default output path changed from Filament's color grading (`"graded"`) to filly's encode pass
(`"exact"`). See [Output encoding](../explanation/design.md#output-encoding) for the design.

Builds: `old` is the tree before the change (color grading by default, with the `glFlush()`
headless-swap fix), `new` is the changed tree, both built with
`tools/profile_tracy.py build --no-tracy --source DIR`. `direct` is `new` with
`--output-path direct`. Earlier sections of this page used builds without the swap fix, which
added about 0.46 ms to every Intel GPU frame.

### Why color grading cost 1.5 ms on Intel

RenderDoc replay, Intel, AC, empty scene at 1920 x 1080, median of 10 replays. A scratch build of
the old tree switched one setting at a time. The same captures were replayed on NVIDIA.

| Variant | colorGrading pass, Intel | NVIDIA |
| --- | ---: | ---: |
| As shipped: RGB16F buffer, 1D LUT (`engine.color_grading.use_1d_lut`) | 1.463 | 0.158 |
| R11G11B10F buffer (`hdrColorBuffer = MEDIUM`) | 1.460 | not replayed |
| 3D LUT, 32^3 (1D LUT off) | 0.901 | 0.092 |
| 3D LUT, 16^3 | 0.910 | not replayed |
| Dithering on | 1.639 | 0.159 |
| Framebuffer-fetch subpass (wrong output on Intel) | 1.334 in the color pass, plus 0.421 for the copy to the target | no framebuffer fetch |

- The 1D LUT is the largest single cost. Filament stores it as a 512 x 1 x 1 R16F 3D texture and
  samples it three times per pixel (`colorGrade1D()`); the 3D LUT samples once. The two extra 3D
  texture fetches cost 0.56 ms at 1080p on Intel and 0.07 ms on NVIDIA.
- A full-screen pass has a floor: the plain copy of the subpass variant took 0.42 ms at 1080p. The
  rest of the shader with one 3D LUT fetch took 0.48 ms more.
- The intermediate format does not matter (1.463 against 1.460 ms), so the pass is not limited by
  reading the HDR buffer. The LUT dimensions do not matter either.
- The default frame had no other postprocessing pass: the capture holds the color pass and color
  grading only (no resolve, blit, or structure pass).
- The earlier 2.0 ms per frame included 0.46 ms of headless `SwapBuffers`, since fixed. With the
  fix, the live difference to the direct path was 1.51 ms (1.617 against 0.105 ms, below).
- Breakdown of 1.46 ms (replay): 0.42 ms full-screen floor, 0.48 ms shader with one LUT fetch,
  0.56 ms for the two extra fetches of the 1D LUT.

### GPU frame, Intel

Intel Iris Xe, AC, Balanced scheme, 60 Hz pacing (offscreen) or vsync (pyglet), 5 rounds,
10:47 to 11:14. CPU load before runs: median 15%, maximum 32%.

| Scenario | old | new | direct | old minus new | new minus direct |
| --- | ---: | ---: | ---: | ---: | ---: |
| Empty, 1080p | 1.617 [1.615-1.625] | 0.800 [0.798-0.801] | 0.105 [0.103-0.106] | 0.82 | 0.70 |
| DamagedHelmet, 1080p | 3.159 [3.107-3.181] | 2.321 [2.314-2.323] | 1.607 [1.597-1.620] | 0.84 | 0.71 |
| Empty, 512 | 0.277 [0.275-0.278] | 0.164 [0.163-0.165] | 0.051 [0.050-0.052] | 0.11 | 0.11 |
| DamagedHelmet, 512 | 0.900 [0.894-0.904] | 0.747 [0.744-0.750] | 0.632 [0.629-0.635] | 0.15 | 0.12 |
| DamagedHelmet, 1080p, FXAA | 4.063 [4.060-4.075] | 3.262 [3.254-3.277] | - | 0.80 | - |
| DamagedHelmet, 1080p, pyglet | 3.189 [3.177-3.191] | 2.334 [2.317-2.345] | 1.631 [1.629-1.635] | 0.86 | 0.70 |
| DamagedHelmet, 512, pyglet | 0.905 [0.900-0.910] | 0.745 [0.743-0.756] | 0.639 [0.631-0.643] | 0.16 | 0.11 |

pyglet host, GPU start to acquired (host timestamps): 4.259, 3.283, and 2.515 ms at 1080p;
2.637, 2.187, and 2.153 ms at 512. `acquire()` enter (CPU): 1.357, 0.975, and 1.176 ms at 1080p.

The linear buffer format, measured with a scratch build that uses RGBA32F: 0.845 [0.844-0.850] ms
empty and 2.405 [2.352-2.425] ms DamagedHelmet at 1080p, 0.05 and 0.08 ms more than RGBA16F. See
[Output encoding](../explanation/design.md#output-encoding) for why RGBA16F was kept.

### GPU frame, NVIDIA

NVIDIA RTX A500, offscreen, 1920 x 1080, 60 Hz pacing, AC, 5 rounds, 11:26 to 11:36. The scratch
builds ran from a copy of the base interpreter with `GpuPreference=2` under
`HKCU\Software\Microsoft\DirectX\UserGpuPreferences`, removed afterwards; Filament's log
confirmed the NVIDIA renderer.

| Scenario | old | new | direct |
| --- | ---: | ---: | ---: |
| Empty | 0.232 [0.227-0.240] | 0.209 [0.200-0.217] | 0.092 [0.084-0.106] |
| DamagedHelmet | 0.367 [0.367-0.368] | 0.275 [0.275-0.276] | 0.181 [0.180-0.181] |
| DamagedHelmet, FXAA | 0.494 [0.494-0.494] | 0.393 [0.393-0.395] | - |

### CPU time

Native submit (`Stats.cpu_submit_ms`), median of round medians, ms:

| Scenario | old | new | direct |
| --- | ---: | ---: | ---: |
| Intel, empty, 1080p | 0.208 | 0.332 | 0.160 |
| Intel, DamagedHelmet, 1080p | 0.201 | 0.324 | 0.197 |
| Intel, DamagedHelmet, 1080p, FXAA | 0.243 | 0.377 | - |
| Intel, DamagedHelmet, 1080p, pyglet | 0.204 | 0.258 | 0.178 |
| NVIDIA, DamagedHelmet, 1080p | 0.239 | 0.323 | 0.227 |
| NVIDIA, DamagedHelmet, 1080p, FXAA | 0.243 | 0.438 | - |

The encode pass is a second Filament view, and FXAA a third. In a Tracy build (one run, Intel,
DamagedHelmet, 1080p), `Filament::render` of the scene view took 0.155 ms and the output passes
0.115 ms (p50). The round medians of native submit vary by up to 0.15 ms between rounds, so treat
differences below that as noise. Turning off frustum culling on the pass views was not measurably
faster (0.301 [0.272-0.513] against 0.412 [0.279-0.453] ms); the change is kept, because the
triangle is never culled.

Renderer creation compiles the encode material: 0.88 to 0.96 s against 0.70 to 0.80 s before (four
runs each). Part of this is the material compiler's first use, which a runtime-path load paid
before. Setting `antialiasing = "fxaa"` compiles the FXAA material: 35 to 39 ms.

### Per-pass GPU time of the new path

RenderDoc replay, 10 replays, captured on Intel on AC at 10:41 to 10:43, and the same captures
replayed on NVIDIA. Replay GPU clocks differ between captures: the color pass of the same
DamagedHelmet frame took 0.97 to 1.46 ms on Intel. Compare passes within one capture.

| Scenario | Pass | Intel ms | NVIDIA ms |
| --- | --- | ---: | ---: |
| Empty, 1080p | Color Pass (clears the RGBA16F buffer) | 0.000 | 0.035 |
| Empty, 1080p | Encode (RGBA16F to SRGB8_A8 target) | 0.686 | 0.076 |
| DamagedHelmet, 1080p | Color Pass | 1.458 | 0.216 |
| DamagedHelmet, 1080p | Encode | 0.705 | 0.077 |
| DamagedHelmet, 1080p, FXAA | Color Pass | 0.973 | 0.217 |
| DamagedHelmet, 1080p, FXAA | Encode (to the RGBA8 FXAA input) | 0.435 | 0.086 |
| DamagedHelmet, 1080p, FXAA | FXAA | 0.593 | 0.101 |
| DamagedHelmet, 512 | Color Pass | 0.547 | 0.094 |
| DamagedHelmet, 512 | Encode | 0.114 | 0.015 |
| DamagedHelmet, 1080p, old | Color Pass | 1.451 | 0.220 |
| DamagedHelmet, 1080p, old | colorGrading | 1.448 | 0.164 |
| DamagedHelmet, 1080p, FXAA, old | Color Pass | 1.124 | 0.220 |
| DamagedHelmet, 1080p, FXAA, old | colorGrading | 1.071 | 0.156 |
| DamagedHelmet, 1080p, FXAA, old | fxaa | 0.672 | 0.131 |

- The encode pass costs about half of color grading in the same capture, on both GPUs. Against
  the 0.42 ms copy floor on Intel, its transfer function costs about 0.27 ms at the empty scene's
  clock.
- A second probe with an RGBA8 instead of SRGB8_A8 offscreen target showed no difference
  (0.688 ms for both, empty scene).
- The exact path has no other pass: the scene renders straight into the RGBA16F buffer.

### Encoding accuracy

- Every fp16 value in `[0, 1]` (15,361 values) encodes to the rounded analytic value, sRGB and
  linear, on Intel and NVIDIA (`test_exact_path_rounds_every_half_float_exactly`).
- The 319-value sweep: 9 values are one level from the rounded analytic value of the float
  input, because both drivers truncate the fp32 shader output to fp16 in the RGBA16F buffer; each
  stored level is exact for the truncated value. Largest deviation 0.538 levels. The same 9
  values move with MSAA, FXAA, refraction, shadows, SSAO, and transparent views, so the sweep is
  identical in all of them. With RGBA32F, 0 values moved without MSAA, but 9 still moved with
  MSAA or a transparent view, which pass through Filament's own fp16 buffers.
- Bloom and depth of field (Filament's color grading before the encode pass): 19 of 319 values
  one level off, largest deviation 0.611 levels. Direct path: 23 of 319, 0.676 levels.

## Output path, effects, and size (before the change)

Intel, offscreen, runtime materials, 60 Hz pacing. Measured with color grading as the default
("graded") and the headless `SwapBuffers`; see
[Output path (September 30, 2026)](#output-path-september-30-2026) for the current path.

| Scenario | GPU frame 1080p | GPU frame 512 | native submit 1080p | CPU busy 1080p |
| --- | ---: | ---: | ---: | ---: |
| Empty, graded | 2.069 [1.835-2.095] | 0.816 [0.637-0.820] | 0.189 [0.167-0.206] | 0.209 [0.185-0.234] |
| Empty, direct | 0.106 [0.105-0.108] | 0.051 [0.051-0.052] | 0.192 [0.164-0.216] | 0.216 [0.185-0.245] |
| DamagedHelmet, graded | 3.503 [3.389-3.553] | 1.297 [1.256-1.468] | 0.212 [0.188-0.236] | 0.258 [0.226-0.289] |
| DamagedHelmet, direct | 1.610 [1.599-1.621] | 0.636 [0.634-0.640] | 0.189 [0.174-0.198] | 0.236 [0.208-0.237] |
| DamagedHelmet, shadows | 4.179 [4.097-4.246] | 1.657 [1.489-1.765] | 0.246 [0.236-0.271] | 0.285 [0.275-0.313] |
| DamagedHelmet, MSAA 4x | 4.703 [4.577-4.762] | 1.643 [1.504-1.670] | 0.234 [0.203-0.245] | 0.284 [0.247-0.303] |
| DamagedHelmet, FXAA | 4.496 [4.402-4.624] | 1.540 [1.496-1.667] | 0.218 [0.211-0.229] | 0.260 [0.256-0.275] |

At 144 Hz pacing (Intel, 1080p, 4 rounds on AC; see [Headless swap chain](#headless-swap-chain)):
empty graded 2.029, empty direct 0.105, DamagedHelmet graded 3.585, DamagedHelmet direct
1.624. The pacing rate does not change the GPU frame time.

CPU time does not depend on the output path or the effects within the resolution of these runs:
native submit is 0.19 to 0.25 ms, and all 60 Hz offscreen runs had frame intervals of
17.03 to 17.08 ms at the 95th percentile.

## Materials: runtime and archive

Intel, offscreen, color grading on, 60 Hz pacing. "Delta" is archive minus runtime.

| Asset | Runtime 1080p | Archive 1080p | Delta 1080p | Runtime 512 | Archive 512 | Delta 512 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Plain | 3.503 [3.389-3.553] | 3.887 [3.794-3.937] | +0.38 (+11%) | 1.297 [1.256-1.468] | 1.469 [1.340-1.632] | +0.17 |
| Clearcoat | 3.039 [2.725-3.110] | 4.141 [4.044-4.253] | +1.10 (+36%) | 1.089 [0.974-1.203] | 1.468 [1.295-1.624] | +0.38 |
| Sheen | 3.362 [3.255-3.388] | 4.155 [3.984-4.185] | +0.79 (+24%) | 1.367 [1.244-1.377] | 1.609 [1.552-1.687] | +0.24 |
| Anisotropy | 3.419 [3.372-3.450] | 4.599 [4.477-4.667] | +1.18 (+35%) | 1.515 [1.329-1.572] | 1.991 [1.849-2.019] | +0.48 |
| Specular | 2.885 [2.767-2.916] | 3.899 [3.790-3.972] | +1.01 (+35%) | 1.177 [0.964-1.239] | 1.423 [1.314-1.566] | +0.25 |

The frame includes about 2.0 ms of color grading at 1080p. Without it, the shading part of the
archive path costs about 1.3x (plain) to 2.2x (clearcoat, specular) the runtime path. This
subtraction is an estimate. The RenderDoc Color Pass times (battery, relative only) give 1.15x
(plain) to 2.09x (clearcoat) on Intel and 1.07x to 1.40x on NVIDIA; see
[Per-pass GPU time (battery)](#per-pass-gpu-time-battery).
CPU submit time is the same for both paths (0.18 to 0.21 ms).

## Host paths

Intel, DamagedHelmet, color grading, runtime materials. Offscreen is paced by a sleep; the hosts
by vsync at 60 Hz. "GPU start to acquired" is measured with GL timestamps in the host context:
it is Filament's frame on the GPU timeline, including driver-thread latency. PsychoPy runs
have no GPU frame column because the PsychoPy venv has no instrumented build.

| Path | GPU frame | GPU start to acquired | GPU host draw | `acquire()` enter (CPU) | host draw (CPU) | CPU busy | CPU busy p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1080p offscreen | 3.503 [3.389-3.553] | - | - | - | - | 0.258 [0.226-0.289] | 0.375 [0.344-0.418] |
| 1080p pyglet | 3.602 [3.559-3.659] | 4.719 [4.593-4.811] | 0.384 [0.381-0.387] | 1.452 [1.424-1.544] | 0.255 [0.247-0.272] | 2.089 [2.057-2.190] | 3.032 [2.959-3.244] |
| 1080p PsychoPy | - | 5.660 [5.480-5.801] | 0.776 [0.775-0.782] | 1.367 [1.132-1.456] | 0.237 [0.214-0.264] | 2.032 [1.666-2.127] | 3.042 [2.679-3.559] |
| 512 offscreen | 1.297 [1.256-1.468] | - | - | - | - | 0.240 [0.201-0.282] | 0.340 [0.325-0.399] |
| 512 pyglet | 1.331 [1.259-1.431] | 2.964 [2.768-3.136] | 0.074 [0.072-0.077] | 1.166 [1.108-1.205] | 0.208 [0.189-0.245] | 1.685 [1.581-1.714] | 2.850 [2.497-3.649] |
| 512 PsychoPy | - | 3.044 [2.851-3.323] | 0.130 [0.128-0.132] | 1.030 [0.868-1.120] | 0.192 [0.176-0.200] | 1.561 [1.324-1.661] | 2.884 [2.509-3.063] |

- `acquire()` blocks the main thread until Filament's driver thread has processed the frame and
  published its fence. This is most of the host CPU cost.
- "CPU busy" is the main thread from frame start to the end of `acquire()` exit; it excludes
  `flip()`.
- The shared-host rows of `render()` wall include about 0.09 ms of GL timestamp and `glFlush()`
  overhead from the measurement; `profile_frame.py` now keeps it outside the timed phases.
- Frame intervals: 95th percentile 17.4 to 17.8 ms for the hosts at 60 Hz; 1 frame above
  1.5 times the median in 3,000 PsychoPy and 3,000 pyglet frames.

## Headless swap chain

**Status (September 30, 2026): fixed in the main build.** `SyncPlatform::commit()` in
`native/gl_interop.cpp` now calls `glFlush()` instead of `SwapBuffers()` for every platform. The
measurements below come from the `w-ns` scratch build, which made the same change. The full test
suites pass with it on Windows; Linux was not retested.

filly renders every frame to a headless swap chain, and Filament's `PlatformWGL::commit()`
calls `SwapBuffers()` on its hidden 1 x 1 window. The candidate fix (`w-ns`) overrides
`commit()` in filly's platform to call `glFlush()` instead.

DamagedHelmet, 1080p, color grading, 4 or 5 rounds on AC (runs after the switch to battery at
08:53 are left out):

| GPU, path | Build | GPU frame | GPU start to acquired | `acquire()` enter (CPU) | CPU busy |
| --- | --- | ---: | ---: | ---: | ---: |
| Intel, offscreen 60 Hz | current | 3.650 [3.544-3.675] | - | - | 0.250 [0.173-0.275] |
| Intel, offscreen 60 Hz | fix | 3.191 [3.179-3.205] | - | - | 0.254 [0.169-0.282] |
| Intel, offscreen 144 Hz | current | 3.585 [3.077-3.669] | - | - | 0.232 [0.143-0.323] |
| Intel, offscreen 144 Hz | fix | 3.195 [3.111-3.204] | - | - | 0.239 [0.222-0.270] |
| Intel, pyglet | current | 3.672 [3.547-3.722] | 4.860 [4.582-4.945] | 1.475 [1.378-1.492] | 2.114 [1.989-2.144] |
| Intel, pyglet | fix | 3.188 [3.150-3.205] | 4.140 [3.612-4.325] | 1.267 [0.882-1.406] | 1.931 [1.427-2.079] |
| NVIDIA, offscreen 60 Hz | current | 16.229 [16.216-16.302] | - | - | 0.123 [0.108-0.182] |
| NVIDIA, offscreen 60 Hz | fix | 0.367 [0.367-0.368] | - | - | 0.238 [0.176-0.241] |
| NVIDIA, offscreen 144 Hz | current | 16.182 [16.160-16.315] | - | - | 0.111 [0.101-0.118] |
| NVIDIA, offscreen 144 Hz | fix | 0.367 [0.366-0.368] | - | - | 0.202 [0.195-0.223] |
| NVIDIA, pyglet | current | 0.745 [0.722-0.795] | 1.464 [1.368-1.617] | 1.206 [1.099-1.278] | 1.556 [1.454-1.754] |
| NVIDIA, pyglet | fix | 0.381 [0.379-0.383] | 1.080 [1.025-1.158] | 0.623 [0.574-0.763] | 1.042 [0.937-1.246] |

- **Confirmed on NVIDIA.** The swap on the hidden window waits for the display refresh, at 60 Hz
  and at 144 Hz pacing. Filament's timer spans the wait, so its GPU frame reads one refresh
  (16.2 ms) for 0.37 ms of work. The CPU does not stall offscreen (frame intervals stay at the
  pacing rate), but the frame's GPU work and its completion are delayed. With a pyglet host the
  swap costs 0.36 ms of GPU frame time and 0.58 ms of CPU in `acquire()`.
- **Intel.** The swap costs 0.39 to 0.48 ms of GPU frame time and 0.21 ms of CPU in `acquire()`.
- Frame ids, `FrameInfo`, fences, and readback worked with the fix in these runs. It was not
  tested on Linux, with GLX or EGL, or with the test suite.

## Per-pass GPU time (battery)

**Battery.** Captured on the Intel GPU at 09:09 to 09:15 on battery (Balanced scheme), after
the switch from AC at 08:53. Use these tables as relative breakdowns only; do not compare them
with the AC tables above.

`profile_frame.py renderdoc`, one frame after 120 warmup frames, 1920 x 1080 unless stated,
median of 10 replays per action. The same capture is replayed on each GPU. Pass names are
Filament's frame-graph names. Replay times exclude inter-pass gaps, driver-thread latency, and
the headless swap, so they are lower than the live GPU frame.

| Scenario | Pass | Intel ms | NVIDIA ms |
| --- | --- | ---: | ---: |
| Empty, graded | colorGrading | 1.475 | 0.158 |
| Empty, graded | Color Pass (clears) | 0.000 | 0.038 |
| Empty, direct | Color Pass (clears) | 0.000 | 0.025 |
| DamagedHelmet, graded | Color Pass | 1.697 | 0.239 |
| DamagedHelmet, graded | colorGrading | 1.488 | 0.165 |
| DamagedHelmet, direct | Color Pass | 1.636 | 0.203 |
| DamagedHelmet, shadows | Shadow Pass (1024 x 1024 D16) | 0.158 | 0.046 |
| DamagedHelmet, shadows | Color Pass | 2.123 | 0.250 |
| DamagedHelmet, shadows | colorGrading | 1.504 | 0.165 |
| DamagedHelmet, MSAA 4x | Color Pass (with resolve) | 2.794 | 0.521 |
| DamagedHelmet, MSAA 4x | colorGrading | 1.501 | 0.166 |
| DamagedHelmet, FXAA | Color Pass | 1.679 | 0.242 |
| DamagedHelmet, FXAA | colorGrading (to an RGBA8 intermediate) | 1.482 | 0.156 |
| DamagedHelmet, FXAA | fxaa | 0.959 | 0.132 |
| DamagedHelmet 512, graded | Color Pass | 0.620 | 0.098 |
| DamagedHelmet 512, graded | colorGrading | 0.215 | 0.030 |
| DamagedHelmet 512, direct | Color Pass | 0.594 | 0.080 |

Color Pass of each material, runtime and archive path (colorGrading is 1.44 to 1.51 ms on
Intel and 0.16 ms on NVIDIA in every case):

| Asset | Intel runtime | Intel archive | Ratio | NVIDIA runtime | NVIDIA archive | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Plain | 1.697 | 1.947 | 1.15 | 0.239 | 0.256 | 1.07 |
| Clearcoat | 1.075 | 2.246 | 2.09 | 0.141 | 0.197 | 1.40 |
| Sheen | 1.282 | 2.230 | 1.74 | 0.201 | 0.222 | 1.10 |
| Anisotropy | 1.485 | 2.209 | 1.49 | 0.217 | 0.238 | 1.10 |
| Specular | 0.940 | 1.508 | 1.60 | 0.145 | 0.171 | 1.18 |

- The color-grading pass costs the same with or without a model: it is a fixed full-screen
  cost of about 1.5 ms at 1080p on Intel (replay) and 0.22 ms at 512 x 512.
- On NVIDIA every pass is 6 to 10 times faster than on Intel.
- The archive shaders have no preprocessor source in the capture, so the shader table does not
  list their features. The feature list of the runtime shaders is in the capture reports.
- The render-target column of the tool shows "1x1" for the direct path; that column is not
  reliable for imported or direct targets.

## CPU split (battery)

**Battery.** Tracy-instrumented build (`tools/profile_tracy.py`), DamagedHelmet, 1080p, color
grading, Intel GPU, 600 frames after 120 warmup, one run each, 09:19 to 09:20. CPU clocks on
battery are lower than on AC: the `acquire()` wait was 2.1 ms here against 1.45 ms on AC.

| Zone (ms) | Thread | Offscreen p50 | Offscreen p95 | pyglet p50 | pyglet p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| `submit` (native part of `render()`) | main | 0.187 | 0.369 | 0.245 | 0.381 |
| `Filament::beginFrame` | main | 0.030 | 0.060 | 0.032 | 0.058 |
| `Filament::render` | main | 0.141 | 0.280 | 0.187 | 0.301 |
| `Filament::endFrame` | main | 0.012 | 0.025 | 0.015 | 0.027 |
| `Engine::flush` | main | 0.000 | 0.001 | 0.000 | 0.000 |
| filly bookkeeping (`submit` minus the above) | main | about 0.004 | | about 0.011 | |
| `ImportedTarget::acquire` / `wait_on_host` | main | - | - | 2.112 | 4.182 |
| `driver frame` (backend beginFrame to endFrame) | driver | 0.590 | 1.296 | 1.099 | 2.439 |
| `driver commit` (headless SwapBuffers) | driver | 0.158 | 0.355 | 0.369 | 1.454 |
| `driver createSync` | driver | - | - | 0.125 | 0.565 |

Python around the native call: `render()` wall minus native submit was 0.013 ms (offscreen)
and 0.017 ms (pyglet); the model edit took 0.03 ms. The pyglet host draw took 0.52 ms of CPU.

- Filament's `Renderer::render` (culling, frame-graph setup, command recording) is 75% of the
  native submit. filly's own bookkeeping is below 0.02 ms.
- The main thread's largest cost in a shared host is `wait_on_host`: waiting for the driver
  thread to reach the frame's fence. The driver thread spends 0.6 to 1.1 ms on the frame's GL
  commands, of which 0.16 to 0.37 ms is the headless `SwapBuffers`.

## Headless swap chain in nsys (battery)

**Battery.** NVIDIA, DamagedHelmet, 1080p, 300 frames after 120 warmup, one run each,
09:03 to 09:06. Driver-thread GL time per frame and the latency from the end of `render()` on
the Python thread to the end of that frame on the driver thread (`SwapBuffers`, or `glFlush`
with the fix):

| Path | Build | Driver thread GL calls per frame | of which `glFenceSync` | render() to frame end p50 | p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| Offscreen 60 Hz | current | 16.38 ms | 15.35 ms | 0.68 ms | 16.91 ms |
| Offscreen 144 Hz pacing | current | 16.43 ms | 15.55 ms | 8.41 ms | 15.92 ms |
| pyglet 60 Hz | current | 1.41 ms | 0.86 ms | 1.11 ms | 1.77 ms |
| Offscreen 60 Hz | fix | 1.28 ms | 0.17 ms | 1.17 ms | 1.78 ms |
| Offscreen 144 Hz pacing | fix | 1.12 ms | 0.16 ms | 0.98 ms | 1.67 ms |
| pyglet 60 Hz | fix | 0.64 ms | 0.10 ms | 1.15 ms | 1.71 ms |

The first GL call after the headless `SwapBuffers` (`glFenceSync`) blocks the driver thread
until the next vertical blank of the 60 Hz display. Offscreen, a frame's GPU work can then wait
up to one refresh after `render()` returns (p95 16.9 ms at 60 Hz, median 8.4 ms at 144 Hz
pacing). With the fix, the driver thread finishes each frame about 1 ms after `render()`.

## Ranked recoverable frame time

Measured at 1080p on the Intel GPU unless stated. "Native" means a change to filly's C++ code.
Ranks 2 to 5 and 7 were measured with color grading as the default; their size relative to the
exact path is not remeasured.

| Rank | Saving | Change | Cost or risk | Native |
| --- | --- | --- | --- | --- |
| - | done: 0.84 ms GPU (0.15 ms at 512); NVIDIA 0.09 ms | Default output through filly's encode pass instead of Filament's color grading. | 0.12 ms more CPU per frame; renderer creation compiles the encode material (about 0.17 s more). | Yes (done) |
| - | done: NVIDIA one refresh of latency and 0.58 ms CPU per frame in `acquire()`; Intel 0.46 ms GPU and 0.21 ms CPU | `SyncPlatform::commit()` calls `glFlush()` instead of `SwapBuffers()`. | Not tested on Linux in these runs. | Yes (done) |
| 1 | 0.71 ms GPU (0.12 ms at 512) | Use `output_path = "direct"`. | No tone mapping other than linear, no FXAA, MSAA, bloom, SSAO, refraction, transparency, dithering, vignette, or depth of field; `MASK` materials rejected. Output is within one 8-bit level of the analytic encoding but rounds differently from the exact path. | No (exists as an opt-in) |
| 2 | 1.2 ms GPU (0.35 ms at 512) | Do not use MSAA 4x. | Aliased edges. | No |
| 3 | 0.94 ms GPU (1.0 ms before the change) | Do not use FXAA. | Aliased edges. | No |
| 4 | 0.4 to 1.2 ms GPU (0.17 to 0.48 ms at 512) for plain to anisotropic materials | Keep the runtime material path, or split the archive's extended entries into specialized entries. | The runtime path compiles materials at load (0.1 to 0.35 s per new configuration). More archive entries increase its size. | Yes (material build) |
| 5 | 0.68 ms GPU (0.36 ms at 512) | Turn shadows off where a stimulus does not need them. | Visual. | No |
| 6 | about 0.1 ms CPU per frame | Run the encode pass without a second Filament view: raw GL on Filament's driver thread through the platform hook. | filly would have to restore every GL state that Filament caches, and create the textures itself to know their names. Not tried. | Yes |
| 7 | about 0.39 ms GPU at 1080p (0.06 ms at 512) | Draw the shared texture in PsychoPy with the blit that the pyglet adapter uses, when no transform, mask, or blending is needed. | Loses `ImageStim` placement, opacity, and masking for that path. Estimated from the pyglet blit (0.384 ms) against `ImageStim` (0.776 ms); not measured in PsychoPy. | No (adapter) |
| 8 | about 2 ms GPU for DamagedHelmet at 1080p (2.32 ms Intel, 0.28 ms NVIDIA) | Render on the NVIDIA GPU (`SHIM_MCCOMPAT=0x800000001`, a driver profile, or a per-executable GPU preference). | On this Optimus laptop the panel is driven by the Intel GPU, so the host window's frames are copied between adapters. The latency of that copy is not measured. | No |
| - | not measured | Render and acquire later in the frame so that `acquire()` finds the frame already published. | Changes the experiment loop. | No |

Items 1 to 5 add up only where a scene uses all of the features. For a plain stimulus at 1080p,
the exact path takes the Filament frame from 3.16 ms to 2.32 ms on the Intel GPU, and the direct
path to 1.61 ms.

## Open questions

- **Color grading cost (answered September 30).** The 2.0 ms per frame included 0.46 ms of
  headless `SwapBuffers`. The remaining 1.5 ms is mostly the 1D LUT, three fetches of a 3D
  texture per pixel, plus a 0.42 ms floor for any full-screen pass on this GPU at this load. The
  earlier revision's 0.6 ms is still not explained; its loop, GPU clock state, and engine
  feature flags are not recorded.
- **Intel GPU clocks.** The Intel GPU lowers its clock at light load, and nothing on this
  laptop fixes it. The numbers here are for the load of one paced stimulus; a heavier scene
  can raise the clock and lower per-pass times.
- **Not measured on AC:** the per-pass tables, the Tracy split, and the nsys traces (all taken on
  battery and labeled), and the headless-swap comparison at 512 x 512.
- **Not measured at all:** the cost of copying an NVIDIA-rendered host window to the Intel-driven
  panel; the PsychoPy host with the headless-swap fix (the PsychoPy venv has no scratch build);
  `--sync finish` loops; displays above 60 Hz (144 Hz was emulated by pacing only).
- **nsys ranges with the fix.** Without a swap, the last GPU range of a Filament frame in nsys
  extends to the first GL call of the next frame, so nsys "GPU busy" per frame is not valid for
  the fixed build. Use Filament's GPU frame time instead.
