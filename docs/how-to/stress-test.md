# Measure PsychoPy timing and resource stability

Install the [PsychoPy dependencies](psychopy.md) and build the renderer first.
Run a short check from the project directory:

```powershell
uv run --no-sync python examples/psychopy_stress.py --cycles 4 --frames 120 --warmup 30 --transparent
```

The default asset is the wooden horse in `examples/assets`, which the screenshot example also
uses. Each cycle loads an asset, creates node-local materials, draws warmup and measured frames,
then closes the model. A regular PsychoPy grating and text label are drawn each frame.
The transparent option shows the grating through the Filament image.

For a longer run, use a new output directory:

```powershell
uv run --no-sync python examples/psychopy_stress.py --cycles 100 --frames 600 --warmup 60 --copies 4 --transparent --output .deps/stress-long
```

Alternate your own assets by supplying multiple paths. The first animation clip plays at
deterministic time samples. Authored lights use EV100 0; otherwise, studio lighting uses EV100 15.
`--copies N` gives the first N mesh nodes a node-local material. Node-local materials follow the
authored material animation, so the option does not change what animates. The default is one
node-local material, on the first mesh node.

```powershell
uv run --no-sync python examples/psychopy_stress.py C:/models/watch.glb C:/models/plant.glb --copies 0 --cycles 30 --frames 600
```

To test a 120 Hz display, select that mode in Windows, then run:

```powershell
uv run --no-sync python examples/psychopy_stress.py --fullscreen --screen 0 --width 1920 --height 1080 --refresh-hz 120 --cycles 30 --frames 1200
```

`--refresh-hz` sets the analysis budget and animation sampling rate. It does not change the display
mode. The script first draws 80 simple frames and estimates the flip rate from the last 60
intervals. Without `--refresh-hz`, this estimate supplies the budget. Inspect the recorded calibration
intervals before relying on that estimate. Fullscreen window size and render-target dimensions
are recorded separately. Press Escape to stop and save the completed measured frames.

## Read the reports

The script prints the report directory. The default is a new timestamped directory under `.deps`.
An existing output directory is rejected to preserve earlier results.

| File | Contents |
| --- | --- |
| `frames.csv` | One row per measured frame, identified by cycle and frame index. |
| `memory.jsonl` | Process memory, available GPU memory counters, resource counts, and counter errors at each trial boundary. |
| `summary.json` | Settings, software and GL versions, calibration, timing percentiles, resource checks, and memory trends. |

Frames record CPU update, native submission, host fence wait/release, host drawing, and flip
durations in milliseconds. Host drawing includes fence wait/release, the grating, the shared
stimulus, and the label. Do not add the fence columns to the drawing column. Flip intervals use
PsychoPy's returned timestamps. They include Python and event-processing overhead between frames.
No disk writes, memory queries, readbacks, or explicit completion waits occur in the measured
frame loop. CPU timers and the native statistics query add some measurement overhead.

An interval of at least 1.5 frame periods counts as late. Estimated missed refreshes sum
`max(0, floor(interval / period + 0.5) - 1)`. This estimate is `null` when calibration differs
from the requested rate by more than 5%. For example, a 60 Hz display does not produce valid
120 Hz missed-refresh estimates. Late intervals still describe overruns of the requested budget.
These timestamps do not measure physical display latency. Isolated GPU time remains `null`.

Loading, warmup, cleanup, memory sampling, and inter-trial gaps are excluded from frame timing.
The load duration includes node-local materials and camera setup. Cleanup includes model close,
GPU completion, and Python garbage collection. The memory queries are outside these durations.
Resource counts must return to their pre-load baseline after each cycle, or the script raises
an error and saves the partial report. Summary and raw files are updated between cycles.

## Interpret memory growth

Windows process samples report working-set bytes and private committed bytes using
[GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo).
GPU samples use the process's dedicated/shared memory instances in Windows performance counters,
read with [PDH](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw).
Values are summed across available adapters. Unavailable or invalid counters are `null`, with
the reason in `memory_errors`. They are not replaced with zero.

Samples occur before the first load, after each load, after rendered work completes, after model
cleanup, and after renderer/window shutdown. GPU counters can lag allocation and release. They
cover the process, including PsychoPy, and do not measure Filament allocations in isolation.

Memory trends use post-cleanup samples. The first cycle is excluded by default; increase
`--skip-memory-cycles` to cover shader compilation and warmup for all asset configurations.
At least two remaining valid samples are required. Reports contain the final-minus-initial byte
change and a linear slope in bytes per cycle. Driver caches, retained shader definitions, Python
allocators, and the per-cycle report metadata can all contribute to growth. A positive slope alone
does not establish a leak. Compare longer runs with the same assets, settings, and hardware.
