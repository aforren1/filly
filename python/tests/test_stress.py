import numpy as np
import pytest

from filly._stress import FRAME_COLUMNS, memory_trend, summarize_frames


def test_refresh_estimates_and_missing_intervals():
    rows = np.zeros((5, len(FRAME_COLUMNS)))
    rows[:, -1] = [np.nan, 1000/60, 2000/60, 3000/60, 1000/60]
    result = summarize_frames(rows, 60, 60.01)
    assert result["intervals"] == 4
    assert result["late_intervals"] == 2
    assert result["estimated_missed_refreshes"] == 3
    assert result["timing_ms"]["flip_interval_ms"]["max"] == 50
    mismatch = summarize_frames(rows, 120, 60)
    assert not mismatch["refresh_matches"]
    assert mismatch["estimated_missed_refreshes"] is None
    assert summarize_frames(rows[:0], 60, 60)["timing_ms"]["flip_interval_ms"] is None


def test_memory_trend_excludes_setup_and_missing_samples():
    samples = [dict(cycle=0, phase="baseline", private_bytes=900),
               dict(cycle=0, phase="closed", private_bytes=100),
               dict(cycle=1, phase="closed", private_bytes=200),
               dict(cycle=2, phase="loaded", private_bytes=9000),
               dict(cycle=2, phase="closed", private_bytes=None),
               dict(cycle=3, phase="closed", private_bytes=240)]
    result = memory_trend(samples, "private_bytes")
    assert result == {"samples": 2, "delta_bytes": 40, "bytes_per_cycle": 20}
    assert memory_trend(samples[:2], "private_bytes")["bytes_per_cycle"] is None


@pytest.mark.parametrize("rate", [0, -1, np.nan, np.inf])
def test_invalid_refresh_rate(rate):
    with pytest.raises(ValueError):
        summarize_frames(np.empty((0, len(FRAME_COLUMNS))), rate, 60)
