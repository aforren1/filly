# Performance

This page gives the measured costs of filly on the development laptop, for the current build.
Each section links to the measurement in the [performance log](../archive/performance-log.md),
which also gives the method, the ranges, and the earlier results. To measure on your own
computer, see [profile a frame](../how-to/profile.md).

## Test computer

| Item | Value |
| --- | --- |
| GPUs | Intel Iris Xe, which drives the 60 Hz panel, and NVIDIA RTX A500 Laptop GPU (Optimus) |
| Operating system | Windows 11, on AC power, Balanced power scheme |
| Target | 1920 x 1080 offscreen, unless the section gives a different size |
| Loop | Paced at 60 Hz by a sleep, unless the section says `finish()` |
| Values | Median of the per-round medians of 3 to 5 rounds, each in a fresh process |

"GPU frame" is `Stats.gpu_frame_ms`: Filament's timer from the start to the end of the frame.
"Native submit" is the CPU time of `render()` in filly's C++ code. The Filament release is
1.79.0 where the table says so, and 1.77.1 for all other values.

## Frame cost

One model that fills the view, one directional light, and a small studio environment:

| Scene | GPU frame, Intel | Native submit, Intel | Filament |
| --- | ---: | ---: | --- |
| DamagedHelmet | 2.57 ms | 0.21 ms | 1.79.0 |
| DamagedHelmet, shadows | 3.42 ms | 0.32 ms | 1.79.0 |
| SheenChair | 2.90 ms | 0.22 ms | 1.79.0 |
| Empty scene | 0.80 ms | 0.19 ms | 1.77.1 |

On the NVIDIA GPU, the DamagedHelmet frame takes 0.28 ms of GPU time (1.77.1). See
[Filament 1.79.0](../archive/performance-log.md#filament-1790-november-1-2026) and
[output path](../archive/performance-log.md#output-path-september-30-2026).

## Cost of options

GPU time at 1080p on the Intel GPU, for DamagedHelmet:

| Option | GPU time |
| --- | ---: |
| `output_path = "direct"` instead of the default exact path | 0.71 ms less |
| MSAA 4x | about 1.2 ms more |
| FXAA | about 0.94 ms more |
| Shadows (1.79.0) | 0.85 ms more |
| `"aces_legacy"` tone mapper instead of linear | about 0.87 ms more |
| Render on the NVIDIA GPU instead of the Intel GPU | about 2 ms less |

The effects cost less at smaller sizes: at 512 x 512, MSAA 4x costs about 0.35 ms. The MSAA and
FXAA values were measured with the earlier default output path, color grading. See
[ranked recoverable frame time](../archive/performance-log.md#ranked-recoverable-frame-time).

## Shared-texture hosts

A host that draws filly's texture, such as PsychoPy or pyglet, adds about 1.8 ms of main-thread
CPU time per frame at 1080p. Most of this time is the wait in `acquire()` for Filament's driver
thread. The host's own draw costs 0.39 ms of GPU time for a pyglet blit, and 0.78 ms for a
PsychoPy `ImageStim`. These values are from September 30, 2026. See
[host paths](../archive/performance-log.md#host-paths).

## Many objects

- **Automatic instancing.** Filament draws up to 64 copies of one primitive with one material
  instance in one call (8 in WebGL). For 1,000 instanced spheres on the Intel GPU, `render()`
  plus `finish()` took 5.5 ms instead of 8.6 ms. Scenes without repeated primitives showed no
  difference. See [automatic instancing](../archive/performance-log.md#automatic-instancing).
- **Shared meshes.** 3,000 copies of one mesh from `renderer.create_mesh_asset()` used 11 MiB of
  private memory and rendered with readback in 4.7 ms. 3,000 separate meshes from
  `scene.create_mesh()` used 895 MiB and took 21 ms. See
  [generated meshes](../archive/performance-log.md#generated-meshes).
- **Transforms of many nodes.** With Filament 1.79.0, a change of the transform of every node in
  every frame costs about 0.1 microseconds per node more than with 1.77.1. For 5,000 nodes, the
  native submit is 1.9 ms. A static scene and a moving camera are not slower. See
  [Filament 1.79.0](../archive/performance-log.md#filament-1790-november-1-2026).

## Picking

| Item | Intel | NVIDIA |
| --- | ---: | ---: |
| GPU time of the first query in a frame (ChronographWatch, 1080p) | 1.8 ms | 0.9 ms |
| GPU time of each further query in the same frame | 0.1 to 0.2 ms | 0.1 to 0.2 ms |
| Renders until `ready()` is `True`, with `ready()` before each `render()` at 60 Hz | 1 | 0 |
| First pick frame after `prepare()` | 7 to 10 ms | 4 ms |

Frames without a query have no picking cost. With `ssao` on, the first query costs only 0.1 ms
(Intel) to 0.6 ms (NVIDIA), because the SSAO depth pass also writes the picking data. In
browsers with Filament 1.79.0, the answer arrives 1 frame after the render. See
[picking](../archive/performance-log.md#picking) and
[first pick after prepare()](../archive/performance-log.md#first-pick-after-prepare).

## Readback

DamagedHelmet, `render()` followed by the readback, MSAA off:

| Readback | 1920 x 1080, NVIDIA | 1920 x 1080, Intel | 512 x 512, NVIDIA | 512 x 512, Intel |
| --- | ---: | ---: | ---: | ---: |
| `render()` and `finish()` only | 0.55 ms | 0.95 ms | 0.33 ms | 0.68 ms |
| `read()` | 2.98 ms | 3.37 ms | 0.70 ms | 1.20 ms |
| `begin_read()`, then `read()` of the previous frame | 1.70 ms | 1.72 ms | 0.57 ms | 0.50 ms |

A loop that keeps every array pays about 3.4 ms per 1920 x 1080 frame for new memory. To prevent
this, give each `read()` an array to fill with `read(out=...)`. In MATLAB on the NVIDIA GPU,
`render()` and `read()` take 16.5 ms at 1920 x 1080. The conversion to MATLAB's column-major
layout and MATLAB's allocation are part of this time. See [readback](../archive/performance-log.md#readback-october-29-2026).

## Accuracy of the GPU timer

`Stats.gpu_frame_ms` is within 5% (Intel) and 8% (NVIDIA) of the sum of the passes in a
RenderDoc replay. In a 60 Hz loop, the value is for the frame 3 renders earlier;
`gpu_frame_number` names that frame. An unthrottled loop gives no value. In browsers, Chrome and
Edge give a value after 2 frames; Firefox does not offer the timer extension. See
[GPU frame time validation](../archive/performance-log.md#gpu-frame-time-validation).

## Not measured

- The PsychoPy host since the headless swap-chain fix of September 30, 2026.
- Displays faster than 60 Hz. Pacing at 144 Hz did not change the GPU frame time.
- The latency of the copy from an NVIDIA-rendered window to the panel that the Intel GPU drives.
- Most results with Filament 1.79.0. Only the frame cost of single models and of many nodes was
  measured again after the upgrade.
