import json
import os
import struct
import sys

import pytest

import filly


def _unregister_pyglet_window_classes():
    # pyglet 1.4.11 (pinned by PsychoPy) names its child view class after id(window), drops the
    # class's ctypes callback on close, and never unregisters the class. When CPython reuses a
    # freed window's address, RegisterClassW fails silently and CreateWindowExW calls the freed
    # callback: an access violation or illegal instruction. The suite creates and frees many
    # windows, so it hit this about once per 8 runs. Unregistering the top-level class again is a
    # harmless no-op.
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import pyglet
        from pyglet.window.win32 import Win32Window
    except ImportError:
        return
    if not pyglet.version.startswith("1."):
        # pyglet 2.1.16 has the same gap but did not crash in testing; extend this if it does.
        return
    unregister = ctypes.windll.user32.UnregisterClassW
    unregister.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    close = Win32Window.close

    def close_and_unregister(self):
        names = [cls.lpszClassName for cls in (self._window_class, self._view_window_class) if cls]
        close(self)
        for name in names:
            unregister(name, None)

    Win32Window.close = close_and_unregister


_unregister_pyglet_window_classes()

# Importing pyglet.gl opens an X display, so these modules cannot be collected without one.
# Offscreen tests still run on headless EGL.
if sys.platform == "linux" and not os.environ.get("DISPLAY"):
    collect_ignore = ["test_gl_hosts.py", "test_host_texture.py"]


@pytest.fixture
def triangle_glb():
    positions = struct.pack("<9f", -0.6, -0.4, 0, 0.6, -0.4, 0, 0, 0.8, 0)
    document = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_materials_unlit"],
        "extensionsRequired": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "triangle"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
        "materials": [{"name": "red", "extensions": {"KHR_materials_unlit": {}},
                       "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}],
        "buffers": [{"byteLength": len(positions)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [-0.6, -0.4, 0], "max": [0.6, 0.8, 0]}],
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(positions)
    return (struct.pack("<III", 0x46546C67, 2, length)
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(positions), 0x004E4942) + positions)


@pytest.fixture
def renderer():
    with filly.Renderer() as value:
        yield value


@pytest.fixture
def scene(renderer):
    value = renderer.create_scene()
    camera = value.create_camera()
    camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    value.camera = camera
    return value
