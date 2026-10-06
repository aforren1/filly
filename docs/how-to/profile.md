# Profile a frame

Use `tools/profile_frame.py` to find where frame time goes. The tool builds a named scenario,
renders it, and prints plain-text tables:

| Subcommand | Answers | Host | GPU |
| --- | --- | --- | --- |
| `cpu` | CPU time per frame phase. With a shared host, also GPU time of Filament's frame and of the host draw, from GL timestamps in the host context. | all | all |
| `renderdoc` | GPU time per Filament pass and per shader, from a RenderDoc replay. | offscreen | Intel or NVIDIA |
| `nsys` | GPU time per Filament frame, GL call time per thread, render-to-GPU latency. | all | NVIDIA only |
| `replay` | The `renderdoc` tables for an existing capture. | | |

`tools/profile_tracy.py` splits the native CPU time of `render()`, `acquire()`, and Filament's
driver thread with Tracy zones in a scratch build. See
[Split native CPU time with Tracy](#split-native-cpu-time-with-tracy).

Timings from these tools are for comparison on one machine. Read [Limits](#limits) before you
use a number.

## Prerequisites

- A build of filly in `.venv` (Python 3.12) or `.deps\psychopy311` (Python 3.11, for PsychoPy).
- The sample assets in `.deps\sample-audit\assets\Models`. The scenarios use `DamagedHelmet`,
  `ClearCoatCarPaint`, and `SheenChair`. `--asset PATH` accepts any glTF file.
- For `renderdoc`: RenderDoc 1.39 in `C:\Program Files\RenderDoc`. Set `RENDERDOC_DIR` for
  another location.
- For `nsys`: Nsight Systems 2025.3.2. Set `NSYS` to the path of `nsys.exe` for another version.
  Install the `nvtx` package into a separate directory, not into the venv, and pass it with
  `--nvtx-site` or `FILLY_PROFILE_SITE`:

  ```powershell
  uv pip install --python .venv\Scripts\python.exe --target C:\tmp\prof\site312 nvtx==0.2.16
  uv pip install --python .deps\psychopy311\Scripts\python.exe --target C:\tmp\prof\site311 nvtx==0.2.16
  ```

Run every command from the project directory. `python tools/profile_frame.py list` prints the
scenarios. Options such as `--size 512x512`, `--output-path direct`, `--msaa 4`,
`--antialiasing fxaa`, `--shadows`, and `--host pyglet` change a scenario. Without
`--output-path`, a scenario keeps the build's default output path, so a build of an earlier
revision (default `graded`) and the current build (default `exact`) run the same scenarios.

## Select the GPU

On an Optimus laptop, OpenGL uses the Intel GPU by default. `--gpu nvidia` sets
`SHIM_MCCOMPAT=0x800000001` for the rendering process, which moves it to the NVIDIA GPU.
`--gpu intel` sets `0x800000000`. `cpu` does not set the variable; set it yourself. The
`cpu` table prints the host GL renderer for shared hosts.

## Measure CPU time per phase

```powershell
.venv\Scripts\python.exe tools/profile_frame.py cpu helmet --frames 600 --warmup 120
.deps\psychopy311\Scripts\python.exe tools/profile_frame.py cpu helmet --host psychopy --frames 600
```

Offscreen frames are paced at `--rate` (default 60 Hz) by a sleep, because no display paces
them. Shared hosts are paced by the swap (`--no-vsync` turns vsync off). `--sync finish` calls
`Renderer.finish()` after every frame and times it. `--csv PATH` writes one row per frame.

Example output (PsychoPy, 512 x 512, Intel, 60 frames; not a measurement):

```text
phase (ms)                                    p50      p95      p99      max
update (Python scene edits)                 0.012    0.022    0.036    0.041
render() wall                               0.102    0.139    0.275    0.326
  native submit (Stats.cpu_submit_ms)       0.096    0.130    0.254    0.301
  outside native submit                     0.006    0.012    0.021    0.024
acquire() enter wall                        0.742    0.986    1.039    1.041
  host wait (Stats.host_wait_ms)            0.711    0.955    1.008    1.010
host draw                                   0.158    0.373    0.477    0.515
acquire() exit wall                         0.034    0.074    0.081    0.084
  host release (Stats.host_release_ms)      0.030    0.055    0.066    0.068
present (flip or pacing sleep)             15.533   15.938   16.147   16.227
CPU busy (frame minus present/finish)       1.058    1.399    1.557    1.616
frame interval                             16.628   17.098   17.301   17.379
GPU: start -> acquired (Filament frame)     1.710    2.726    2.852    2.923
GPU: host draw                              0.065    0.124    0.129    0.134
```

| Row | Meaning |
| --- | --- |
| update | Python scene edits: one model rotation per frame. |
| render() wall | The Python call. `native submit` is the time inside filly's native `render()`: bookkeeping, Filament's `beginFrame()`, `render()`, `endFrame()`, and the command flush. `outside native submit` is the binding, argument checks, and GIL release. |
| acquire() enter | Waits until Filament's driver thread has processed the frame and published its fence, then queues a GPU wait in the host context. It is CPU time on the main thread. `host wait` is the native part. |
| host draw | The adapter's blit, or PsychoPy's `ImageStim.draw()`. |
| acquire() exit | The host fence and flush. |
| present | `flip()` for hosts, the pacing sleep offscreen. With vsync, the wait for the display is here. |
| CPU busy | Frame start to the end of `acquire()` exit. This is the CPU cost that competes with other per-frame work. |
| GPU: start -> acquired | Shared hosts only. GL timestamps in the host context before `render()` and after `acquire()`. The host queue waits for Filament's fence, so this is Filament's frame on the GPU timeline, including driver-thread latency and queueing. It is an upper bound on Filament's GPU time. |
| GPU: host draw | GPU time of the host draw. |

With the instrumented build of [Tracy](#split-native-cpu-time-with-tracy), `--frame-info` adds
Filament's own GPU frame time (`Renderer::getFrameInfoHistory()`, a `GL_TIME_ELAPSED` query from
`beginFrame()` to `endFrame()`). It includes GPU idle gaps inside the frame and excludes the host
draw. It is available only in a paced loop; see [Limits](#limits).

The table does not split Filament's native CPU time into its parts (filly bookkeeping,
`Renderer::render`, `endFrame`, the driver thread). The `nsys` subcommand shows the GL calls of
the driver thread. A finer split needs an instrumented build or CPU sampling; see
[Limits](#limits).

## Measure GPU time per pass with RenderDoc

```powershell
.venv\Scripts\python.exe tools/profile_frame.py renderdoc helmet --repeat 10
```

The tool does these steps:

1. It starts the scenario under `renderdoccmd capture --opt-hook-children`. The venv
   `python.exe` is a launcher, so RenderDoc must hook the child interpreter.
2. After the warmup, the scenario calls `Renderer.finish()`, then RenderDoc's in-application
   `StartFrameCapture(NULL, NULL)`, renders one frame, calls `finish()` again, and calls
   `EndFrameCapture`. Filament renders on its own driver thread and context (OpenGL 4.5 core,
   created by `PlatformWGL`), so the `finish()` calls keep other frames out of the capture.
   RenderDoc accepts Filament's context.
3. It replays the capture `--repeat` times and keeps the median GPU duration
   (`EventGPUDuration`) of each action. The replay runs in qrenderdoc's embedded Python
   (`tools/profile_rdc_replay.py`) against a local replay server.
4. It groups the actions by Filament's frame-graph pass and prints the tables.

The replay server is a copy of `renderdoccmd.exe` named `filly-rdc-replay.exe` in
`%TEMP%\filly-profile\renderdoc`. qrenderdoc exports `NvOptimusEnablement`, and the NVIDIA driver
profile for `renderdoccmd.exe` also selects the NVIDIA GPU. The copy replays on the GPU that
`--replay-gpu` selects: by default the capture GPU, and Intel for `--gpu default`.

Example output (DamagedHelmet, 512 x 512, Intel, 10 replays; not a measurement):

```text
pass (F<n> = Filament frame)         GPU ms  share draws other  targets
F0 Color Pass                         0.547  82.7%     1     2  512x512 R16G16B16A16_FLOAT
F1 Color Pass                         0.114  17.3%     1     0  512x512 R8G8B8A8_SRGB
total (sum of timed actions)          0.661
excluded replay-only actions: 5 (End of Capture, glFinish, glFlush, glInvalidateFramebuffer)

pixel shader   GPU ms draws  features [samplers]
         222    0.546     1  blend:opaque double_sided_capability lighting reflections ... model:lit [...]
         226    0.114     1   [source]
```

In this example, `F0 Color Pass` is the scene, rendered into the scene-linear RGBA16F buffer,
and `F1 Color Pass` is filly's encode pass (sampler `source`) into the target.

- The pass names are Filament's frame-graph pass names, for example `Shadow Pass`,
  `Color Pass`, `colorGrading`, `fxaa`, `Resolve`, and blits. They come from
  `glPushGroupMarkerEXT`. Filament emits these markers only if the driver lists
  `GL_EXT_debug_marker`. The tested Intel and NVIDIA drivers do not; RenderDoc adds the
  extension. The markers therefore appear in RenderDoc and nowhere else.
- `F<n>` numbers Filament frame graphs in the capture: one per view that a `render()` call
  renders. The scene is one; filly's encode pass (sampler `source`) and FXAA pass (sampler
  `ldr`) are one each, and all of them are named `Color Pass`. Stereo rendering adds frames, and
  on the direct path so does `render(..., clear=False)`.
- `other` counts clears, blits, and copies.
- The shader table lists Filament's feature defines (`clear_coat`, `sheen_color`, `v:shadowing`,
  and so on) and the sampler names. Use it to compare material variants and archive entries.
- `--detail` lists every action with its render target.
- RenderDoc replays `glInvalidateFramebuffer` by filling the attachment with a pattern, which costs
  GPU time that the live frame does not have. The tool excludes it, `glFinish`, `glFlush`,
  `SwapBuffers`, and `End of Capture` from the totals.

`replay CAPTURE.rdc` prints the tables for an existing capture. Captures are large
(90 MB for DamagedHelmet), because they include the initial contents of every resource.

## Trace GPU frame time with Nsight Systems

```powershell
.venv\Scripts\python.exe tools/profile_frame.py nsys helmet --gpu nvidia --nvtx-site C:\tmp\prof\site312
.deps\psychopy311\Scripts\python.exe tools/profile_frame.py nsys helmet --host psychopy --gpu nvidia --nvtx-site C:\tmp\prof\site311
```

The tool runs `nsys profile --trace=opengl-annotations,nvtx --opengl-gpu-workload=true`, then
`nsys stats`, and reads the SQLite export. It prints:

- GPU busy time per frame for each GL context. Filament's context belongs to the thread
  `FEngine::loop`; the tool splits its frames at the `wglMakeCurrent` that starts each frame
  on that thread. The host context belongs to the Python main thread; its frames end at the
  window's `SwapBuffers`. In a build without the headless swap, the last GPU range of a
  Filament frame extends to the next frame, so "GPU busy" is not valid there.
- GPU ranges by position in the frame. Filament emits no debug groups on these drivers, so nsys
  names each range by the GL calls around it. For example, `glDrawElementsInstanced ->
  glBindFramebuffer` is the last draw of a pass.
- GL call time per thread and per call, per frame. For the driver thread this is Filament's
  backend CPU work. Long `glFenceSync` or `wglMakeCurrent` times are blocking in the driver.
- The latency from the end of `render()` on the Python thread to the `SwapBuffers` of that
  frame on the driver thread.
- The NVTX ranges of the Python thread (`filly:render`, `filly:acquire`, `filly:host draw`,
  `filly:present`) and the GPU time of the KHR_debug groups that the tool pushes in the host
  context (`host draw`).
- The `cpu` table of the same run. It includes NVTX and nsys overhead.

Example output (DamagedHelmet, 512 x 512, offscreen, NVIDIA, 60 frames; not a measurement):

```text
GPU (NVIDIA workload trace), context of FEngine::loop, last 60 frames:
  metric (ms)                                 p50      p95      max
  GPU busy per frame (sum of ranges)        0.084    0.115    0.247
  GPU span per frame (first to last)        0.096    0.129    0.259
  GPU ranges by position (60 of 60 frames have 6 ranges):
    0    0.002 ms  glBindFramebuffer -> glFramebufferTexture2D
    1    0.001 ms  glDrawBuffers -> glFramebufferRenderbuffer
    2    0.056 ms  glDrawElementsInstanced -> glBindFramebuffer
    3    0.002 ms  glBindFramebuffer -> glBindFramebuffer
    4    0.001 ms  glBindFramebuffer -> glBindFramebuffer
    5    0.021 ms  glDrawElementsInstanced -> SwapBuffers

GL calls per thread in the measured window, per frame (mean of 60 frames):
  thread FEngine::loop: 16.290 ms per frame in GL calls
    glFenceSync                    15.812 ms     3.0 calls  max  16.508 ms
    wglMakeCurrent                  0.157 ms     1.0 calls  max   0.293 ms
    SwapBuffers                     0.109 ms     1.0 calls  max   0.201 ms
    ...
render() return -> driver-thread SwapBuffers end (ms): p50 3.244  p95 3.564  max 3.693
```

In this example (an earlier build), range 2 is the color pass (the helmet draw) and range 5 is
color grading. The
driver thread blocks in `glFenceSync` for most of each frame; see [Limits](#limits).

## Split native CPU time with Tracy

filly has no Tracy zones in its sources. `tools/profile_tracy.py build` copies the sources to
`C:\tmp\prof\tracy-work`, adds zones there, and builds the copy into its own venv at below-normal
priority. It downloads Tracy 0.14.1 and checks the SHA-256 digests. The project tree and its
venvs do not change.

```powershell
.venv\Scripts\python.exe tools/profile_tracy.py build
.venv\Scripts\python.exe tools/profile_tracy.py run helmet --frames 600 --warmup 120
```

`run` starts `tracy-capture`, runs `profile_frame.py cpu` with the instrumented build, exports the
zones with `tracy-csvexport -u`, and prints percentiles per zone for the measured frames:

| Zone | Thread | Meaning |
| --- | --- | --- |
| `submit` | main | The native part of `render()`. |
| `Filament::beginFrame`, `Filament::render`, `Filament::endFrame`, `Engine::flush` | main | Filament's calls inside `submit`. `Filament::render` is the scene view. `submit` minus these and `filly output passes` is filly's bookkeeping. |
| `filly output passes` | main | Filament's `render()` of filly's encode view, and of the FXAA view if the scene has FXAA. |
| `ImportedTarget::acquire`, `wait_on_host` | main | The wait for the driver thread to publish Filament's fence. |
| `driver frame` | driver | The driver thread's work on one frame, from the backend `beginFrame` to `endFrame`. |
| `driver commit` | driver | `commit()` of the headless swap chain: a `glFlush()`. |
| `driver createSync` | driver | filly's fence and sRGB requests. |

The instrumentation text-patches the copy. If the native sources change, `build` stops with a
message that names the anchor that no longer matches.

Other `build` options:

- `--work DIR`: a separate scratch directory per variant, for example `C:\tmp\prof\w-ar`.
- `--no-tracy`: only the `Renderer._frame_info_history()` binding, for `cpu --frame-info`
  without Tracy overhead.
- `--define NAME=VALUE`: an extra CMake definition.
- `--source DIR`: build another source tree instead of the project, for example a snapshot of
  an earlier revision or a copy with probe switches. The tree needs `CMakeLists.txt`,
  `pyproject.toml`, `README.md`, `native`, and `src`.
- `--jobs N`: parallel compile jobs (default 2, at below-normal priority).

## Compare configurations in rounds

`tools/profile_matrix.py` runs `profile_frame.py cpu` for a list of configurations in
interleaved rounds and prints one table: the median of the per-round medians and their range.

```powershell
.venv\Scripts\python.exe tools/profile_matrix.py run matrix.json --rounds 5 --out C:\tmp\prof\m-main
.venv\Scripts\python.exe tools/profile_matrix.py table C:\tmp\prof\m-main
```

`matrix.json` is a list of objects with `name`, `python` (the interpreter of a scratch build or
venv), `args` (for `profile_frame.py cpu`), and optional `env`. Before each run the tool
samples the CPU load and waits while it is above `--max-load` percent. It records the power
source of each run and stops if the power source changes, so that AC and battery results are
never mixed. `table --drop NAME:ROUND` leaves out single runs.

## Record GPU activity with Windows Performance Recorder

WPR records the GPU queues of every process on every adapter, including Intel. It needs an
elevated shell. The tool does not summarize WPR traces yet.

```powershell
wpr -start GPU -filemode
.venv\Scripts\python.exe tools/profile_frame.py cpu helmet --host pyglet --frames 600
wpr -stop C:\tmp\prof\gpu.etl
```

Open the trace in GPUView or Windows Performance Analyzer (`Windows Kits\10\Windows Performance
Toolkit`).

## Limits

- **Replay is not the live frame.** RenderDoc replays each action with its own timer queries
  and GPU state. Durations exclude the gaps between passes, driver-thread latency, and
  presentation. Use them to compare passes and configurations, not as frame time.
- **GPU clocks change.** Laptop GPUs change clocks with load, temperature, and power source. A
  short replay can run at a low clock; a long run can throttle. Use the plan in
  [Find recoverable frame time](#find-recoverable-frame-time).
- **RenderDoc and host contexts.** RenderDoc supports only core-profile contexts. PsychoPy and
  pyglet 1.4 create 4.6 compatibility contexts on the tested drivers. In a PsychoPy process,
  `StartFrameCapture` does not start, so the tool refuses `--host psychopy`. A plain pyglet
  capture worked in validation, but it is not guaranteed. Filament's passes are the same
  offscreen and in a shared texture.
- **nsys traces only NVIDIA.** It has no GPU workload data for the Intel GPU. The process can
  exit with an access violation under nsys after a pyglet or PsychoPy run; the trace is complete.
- **No native CPU sampling without elevation.** nsys CPU sampling and context-switch tracing, the
  nsys `wddm` trace, and WPR need an elevated shell. The Tracy zones do not; they cover only the
  zones listed in [Split native CPU time with Tracy](#split-native-cpu-time-with-tracy), not
  Filament's internals.
- **Filament's GPU frame timer is not exposed.** `Renderer::getFrameInfoHistory()` is not bound.
  In a scratch build that binds it, it reported GPU time on Intel for paced loops (60 Hz sleep,
  vsync, or `finish()` per frame), offscreen and shared, on both output paths. In an
  unthrottled loop it reported nothing: its 16-frame queue fills, and while the queue is full,
  Filament skips both the new timer query and the read of finished ones.
- **Intel GPU clocks drop at light load.** In a 60 Hz loop at 512 x 512, Filament's GPU time for
  the same frame rose from about 0.38 ms to 0.7 ms within one second. GPU time depends on the
  load around it; measure in the loop that you will use.
- **The driver thread blocks after the headless swap on NVIDIA.** Filament presents each frame
  with `SwapBuffers` on a hidden window. On NVIDIA, the next GL call waits for the display's
  vertical blank, and Filament's GPU frame timer reads about one refresh. See
  [Performance](../reference/performance.md#headless-swap-chain).
- **Markers cost time.** NVTX ranges, GL timestamps, and the extra `glFlush()` after the first
  timestamp add CPU time. Compare runs with the same options.

## Find recoverable frame time

Use this procedure to compare configurations. Change one variable at a time.

1. Prepare the machine. Connect the charger and keep it connected; results on AC and on
   battery are not comparable. Record the Windows power mode.
   Close other applications, sync clients, and browsers. Wait until the CPU and GPU are idle
   (Task Manager). Do not use the machine during a run.
2. Run each configuration in interleaved rounds (A, B, C, A, B, C, ...), five rounds or more,
   so that thermal drift affects all configurations equally.
3. In each round, render at least 120 warmup frames and 600 measured frames with `cpu`. Take
   the median of the per-round medians, and report the range of the round medians.
4. For per-pass GPU time, take one RenderDoc capture per configuration with `--repeat 10`, on
   Intel and on NVIDIA. Treat a difference smaller than the spread between repeats as none.
5. Report the GPU (`GL renderer`), the driver version, the power source and mode, and the
   display rate. Results for this laptop are in [Performance](../reference/performance.md).
