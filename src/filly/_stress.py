"""Report helpers for the PsychoPy stress benchmark; no display dependencies."""

import math
import numpy as np


FRAME_COLUMNS = ("cycle", "frame", "update_ms", "cpu_submit_ms", "host_wait_ms",
                 "host_release_ms", "host_draw_ms", "flip_ms", "flip_interval_ms")


def percentiles(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not values.size:
        return None
    return dict(zip(("p50", "p95", "p99", "max"),
                    map(float, np.percentile(values, [50, 95, 99, 100]))))


def summarize_frames(rows, refresh_hz, observed_hz):
    if not math.isfinite(refresh_hz) or refresh_hz <= 0:
        raise ValueError("refresh_hz must be finite and positive")
    period = 1000 / refresh_hz
    intervals = rows[:, FRAME_COLUMNS.index("flip_interval_ms")]
    intervals = intervals[np.isfinite(intervals) & (intervals > 0)]
    matched = observed_hz is not None and abs(observed_hz / refresh_hz - 1) <= 0.05
    return {
        "frames": len(rows), "intervals": len(intervals), "refresh_hz": refresh_hz,
        "observed_hz": observed_hz, "refresh_matches": bool(matched),
        "late_intervals": int(np.count_nonzero(intervals >= 1.5 * period)),
        "estimated_missed_refreshes": int(np.maximum(0, np.floor(intervals / period + 0.5) - 1).sum()) if matched else None,
        "timing_ms": {name: percentiles(rows[:, i]) for i, name in enumerate(FRAME_COLUMNS) if i >= 2},
    }


def memory_trend(samples, key, skip=1):
    # Cold shader caches and driver allocations make the first cleanup a poor baseline.
    closed = [row for row in samples if row["phase"] == "closed"][skip:]
    valid = [row for row in closed if row.get(key) is not None]
    if len(valid) < 2:
        return {"samples": len(valid), "delta_bytes": None, "bytes_per_cycle": None}
    x = np.asarray([row["cycle"] for row in valid], dtype=float)
    y = np.asarray([row[key] for row in valid], dtype=float)
    x -= x.mean()
    return {"samples": len(valid), "delta_bytes": int(y[-1] - y[0]),
            "bytes_per_cycle": float(x @ (y-y.mean()) / (x @ x))}
