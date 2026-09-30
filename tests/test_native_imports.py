import importlib.util
from pathlib import Path
import sys

import pytest

import filly._native

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "check_native_imports.py"
_spec = importlib.util.spec_from_file_location("check_native_imports", _TOOL)
check_native_imports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_native_imports)


@pytest.fixture(scope="module")
def module_image():
    return Path(filly._native.__file__).read_bytes()


def test_module_imports_only_allowed_libraries(module_image):
    assert check_native_imports.imports(module_image)
    assert check_native_imports.unexpected(module_image) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows C runtime layout")
def test_windows_module_uses_the_hybrid_crt(module_image):
    names = [name.lower() for name in check_native_imports.imports(module_image)]
    assert not [name for name in names if name.startswith(("msvcp", "vcruntime", "ucrtbase", "concrt"))]
    # The UCRT stays dynamic so the process keeps one heap across modules.
    assert any(name.startswith("api-ms-win-crt-heap-") for name in names)
