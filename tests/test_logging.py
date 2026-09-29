import subprocess
import sys

import pytest


@pytest.mark.gpu
def test_native_log_filter_and_restore():
    # A subprocess captures native driver-thread output and isolates the global setting.
    result = subprocess.run(
        [sys.executable, "-c", """
import filly
filly.set_log_level('warning')
renderer = filly.Renderer()
renderer.close()
filly.set_log_level('verbose')
renderer = filly.Renderer()
renderer.close()
"""], capture_output=True, text=True, check=True, timeout=60,
    )
    output = result.stdout + result.stderr
    assert output.count("FEngine (64 bits) created") == 1


def test_invalid_log_level():
    import filly

    with pytest.raises(ValueError, match="Log level"):
        filly.set_log_level("silent")
