import json
import os
import subprocess
import sys
import textwrap

import pytest

import filly

# Each case runs in a new process because the platform is chosen from the environment when the
# renderer is created, and an unset DISPLAY must not affect the other tests.
_RENDER = textwrap.dedent("""
    import json, struct
    import filly
    positions = struct.pack("<9f", -0.6, -0.4, 0, 0.6, -0.4, 0, 0, 0.8, 0)
    document = {
        "asset": {"version": "2.0"}, "extensionsUsed": ["KHR_materials_unlit"], "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
        "materials": [{"extensions": {"KHR_materials_unlit": {}},
                       "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}],
        "buffers": [{"byteLength": 36}], "bufferViews": [{"buffer": 0, "byteLength": 36}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [-0.6, -0.4, 0], "max": [0.6, 0.8, 0]}],
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    glb = (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + 36)
           + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
           + struct.pack("<II", 36, 0x004E4942) + positions)
    try:
        renderer = filly.Renderer()
    except filly.BackendError as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(0)
    with renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.load(glb)
        target = renderer.create_render_target(width=32, height=32)
        renderer.render(scene, target)
        pixels = target.read()
        print(json.dumps({"platform": renderer.gl_platform, "center": pixels[16, 16].tolist(),
                          "corner": pixels[1, 1].tolist()}))
""")


def _run(**changes):
    env = dict(os.environ)
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run([sys.executable, "-c", _RENDER], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    # Filament writes its own log lines to stdout as well.
    return json.loads([line for line in result.stdout.splitlines() if line.startswith("{")][-1])


def test_windows_renderer_uses_wgl():
    if sys.platform != "win32":
        pytest.skip("WGL is the Windows binding")
    with filly.Renderer() as renderer:
        assert renderer.gl_platform == "wgl"


linux = pytest.mark.skipif(sys.platform != "linux", reason="EGL and GLX selection is Linux only")


@linux
@pytest.mark.gpu
def test_offscreen_without_display_uses_egl():
    result = _run(DISPLAY=None, WAYLAND_DISPLAY=None, FILLY_OFFSCREEN_GL=None)
    if "error" in result and "libEGL" in result["error"]:
        pytest.skip(result["error"])
    assert result.get("platform") == "egl", result
    assert result["center"] == [255, 0, 0, 255]
    assert result["corner"] == [0, 0, 0, 255]


@linux
@pytest.mark.gpu
def test_forced_egl_renders_with_display_set():
    result = _run(FILLY_OFFSCREEN_GL="egl")
    if "error" in result and "libEGL" in result["error"]:
        pytest.skip(result["error"])
    assert result.get("platform") == "egl", result
    assert result["center"] == [255, 0, 0, 255]


@linux
def test_forced_glx_without_display_raises():
    result = _run(DISPLAY=None, WAYLAND_DISPLAY=None, FILLY_OFFSCREEN_GL="glx")
    assert "needs an X display" in result["error"]


@linux
def test_invalid_platform_choice_raises():
    result = _run(FILLY_OFFSCREEN_GL="vulkan")
    assert "must be 'egl' or 'glx'" in result["error"]
