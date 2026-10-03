"""The web build's JavaScript API, run in a browser and compared with the desktop module.

Needs the web build in build/web (or FILLY_WEB_BUILD; see docs/how-to/build.md), Node.js,
`npm install` in tests/web, and a browser: FILLY_TEST_BROWSERS lists chrome, edge, or firefox
(default chrome), and FILLY_TEST_<BROWSER> overrides the executable path.

Run with: pytest -m browser
"""

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).parent / "web"
EXECUTABLES = {
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
}
BROWSERS = os.environ.get("FILLY_TEST_BROWSERS", "chrome").split(",")


def tree_glb():
    """A root node with a triangle mesh and a camera node named "front" as children."""
    positions = struct.pack("<9f", -0.6, -0.4, 0, 0.6, -0.4, 0, 0, 0.8, 0)
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "root", "children": [1, 2]}, {"name": "triangle", "mesh": 0},
                  {"name": "front", "camera": 0, "translation": [0, 0, 3]}],
        "cameras": [{"type": "perspective", "perspective": {"yfov": 0.5, "znear": 0.1, "zfar": 100}}],
        "meshes": [{"name": "triangle mesh", "primitives": [{"attributes": {"POSITION": 0}}]}],
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


def _web_build():
    build = Path(os.environ.get("FILLY_WEB_BUILD", ROOT / "build" / "web"))
    if not (build / "filly-core.wasm").is_file():
        pytest.skip(f"no web build in {build}; run tools/build_filly_web.sh")
    return build


@pytest.fixture(scope="module", params=BROWSERS)
def page_results(request, tmp_path_factory):
    browser = request.param
    executable = os.environ.get(f"FILLY_TEST_{browser.upper()}", EXECUTABLES.get(browser, ""))
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    if not (WEB / "node_modules" / "puppeteer-core").is_dir():
        pytest.skip("run `npm install` in tests/web first")
    if not Path(executable).is_file():
        pytest.skip(f"{browser} not found at {executable!r}")
    folder = tmp_path_factory.mktemp(f"web-{browser}")
    build = _web_build()
    shutil.copytree(build, folder / "filly")
    # The page tests the current glue; the build folder may hold an older copy.
    shutil.copy(ROOT / "web" / "filly.mjs", folder / "filly" / "filly.mjs")
    shutil.copy(WEB / "api.html", folder)
    (folder / "tree.glb").write_bytes(tree_glb())
    result = subprocess.run([node, str(WEB / "run_page.mjs"), str(folder), "api.html", browser, executable],
                            capture_output=True, text=True, timeout=300, cwd=WEB)
    assert result.returncode == 0, result.stderr[-3000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["pageErrors"] == [], report["pageErrors"]
    return report["result"]


def _value(results, name):
    entry = results[name]
    assert entry["ok"], entry.get("error")
    return entry["value"]


def _plane_scene(renderer, texture=None, base_color=(1, 1, 1, 1)):
    scene = renderer.create_scene()
    model = scene.create_mesh(**filly.shapes.plane(2, 2), unlit=True, base_color=base_color)
    if texture is not None:
        model.material("mesh").base_color_texture = texture
    camera = scene.create_camera()
    camera.set_orthographic(height=2, near=0.1, far=10)
    camera.position = (0, 0, 1)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    return scene


def _render(renderer, scene, width=64, height=64):
    target = renderer.create_render_target(width=width, height=height)
    renderer.render(scene, target)
    pixels = target.read().astype(int)
    target.close()
    return pixels


def test_platform(page_results):
    assert page_results["platform"] == "webgl"
    assert page_results["framesRendered"] > 5


def test_prepare(page_results):
    value = _value(page_results, "prepare")
    assert value["framesRendered"] == 1
    assert value["center"][3] == 255 and value["center"][0] > 50
    if value["parallel"]:
        assert value["duringPrepare"] > 0
        assert value["duringRender"] == 0
    else:
        # Without KHR_parallel_shader_compile (Firefox), Filament compiles at the first draw.
        assert value["duringRender"] > 0


def test_solid_and_float_texture(page_results):
    # Unlit linear 0.5 is sRGB level 188, as on the desktop.
    assert _value(page_results, "solid") == [188, 188, 188, 255]
    value = _value(page_results, "texture_float")
    assert value == {"center": [188, 188, 188, 255], "dtype": "float32"}


def test_texture_matches_desktop(page_results):
    value = _value(page_results, "texture_u8")
    web = np.array(value["pixels"]).reshape(64, 64, 4)
    quadrants = np.array([[[255, 0, 0, 255], [0, 255, 0, 255]], [[0, 0, 255, 255], [255, 255, 255, 255]]], np.uint8)
    with filly.Renderer() as renderer:
        texture = renderer.create_texture(quadrants, color_space="srgb", filter="nearest")
        desktop = _render(renderer, _plane_scene(renderer, texture))
    assert np.array_equal(web, desktop)
    assert web[16, 16].tolist() == [255, 0, 0, 255] and web[48, 48].tolist() == [255, 255, 255, 255]
    assert value["info"] == {"width": 2, "height": 2, "channels": 4, "dtype": "uint8", "colorSpace": "srgb",
                             "mipmaps": False, "isTexture": True}
    assert value["updatedCenter"] == [0, 0, 255, 255]
    assert value["closed"] is True


def test_single_channel_texture(page_results):
    # WebGL2 has no texture swizzle, which single-channel textures need.
    entry = page_results["texture_single_channel"]
    assert not entry["ok"] and "swizzle" in entry["error"].lower(), entry


def test_host_textures(page_results):
    # 128 stored linear is sRGB level 188; 128 stored as sRGB is level 128.
    assert _value(page_results, "host_linear") == {"center": [188, 188, 188, 255], "isHost": True,
                                                   "colorSpace": "linear"}
    assert _value(page_results, "host_srgb") == {"center": [128, 128, 128, 255], "wrote": True, "writing": False}


def test_material_texture_slots(page_results):
    assert _value(page_results, "material_texture_slots") == {"base": True, "emissive": None, "cleared": None}


def test_mesh_update(page_results):
    value = _value(page_results, "mesh_update")
    assert value["isMesh"] is True and value["vertexCount"] == 3
    (before_left, before_right), (after_left, after_right) = value["before"], value["after"]
    assert before_left > 400 and before_right == 0
    assert after_left == 0 and after_right == before_left


def test_shapes_match_desktop(page_results):
    value = _value(page_results, "shapes")
    desktop = {"plane": filly.shapes.plane(2, 1, segments=(3, 2)), "box": filly.shapes.box(),
               "uvSphere": filly.shapes.uv_sphere(segments=8, rings=4), "cylinder": filly.shapes.cylinder(caps=False)}
    for name, arrays in desktop.items():
        for key, array in arrays.items():
            web = value[name][key]
            assert web["type"] == ("Uint32Array" if key == "indices" else "Float32Array"), (name, key)
            assert web["length"] == array.size, (name, key)
            assert abs(web["sum"] - float(array.sum(dtype=np.float64))) < 1e-3, (name, key)


def test_lit_box_matches_desktop(page_results):
    web = np.array(_value(page_results, "lit_box")).reshape(120, 160, 4)
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        scene.background = (0.1, 0.2, 0.3, 1)
        model = scene.create_mesh(**filly.shapes.box(), base_color=(0.8, 0.5, 0.2, 1), roughness=0.6)
        model.rotation_euler_deg = (20, 35, 0)
        camera = scene.create_camera()
        camera.set_perspective(fov_y=35, near=0.5, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(-1, -2, -1), intensity=80000)
        scene.set_environment(np.ones((8, 16, 3), np.float32), intensity=15000)
        desktop = _render(renderer, scene, 160, 120)
    # Flat 3 x 3 neighborhoods match within one level; edges rasterize differently.
    padded = np.pad(desktop, ((1, 1), (1, 1), (0, 0)), mode="edge")
    stack = np.stack([padded[1 + dy:121 + dy, 1 + dx:161 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
    flat = (stack.max(axis=0) - stack.min(axis=0)).max(axis=2) <= 4
    difference = np.abs(desktop - web).max(axis=2)
    assert flat.mean() > 0.9
    assert difference[flat].max() <= 1, np.bincount(difference[flat])
    assert (difference > 1).mean() < 0.05


def test_model_tree_and_clone(page_results):
    value = _value(page_results, "model_tree")
    assert value["names"] == ["root", "triangle", "front"]
    assert value["indices"] == [0, 1, 2]
    assert value["rootParent"] is None
    assert value["rootChildren"] == ["triangle", "front"]
    assert value["triangleParent"] == "root"
    assert value["meshName"] == "triangle mesh"
    assert value["sameNode"] is True and value["differentNode"] is False
    assert value["cameraNode"] == "front" and value["sceneCameraNode"] is None
    assert value["morphTargets"] == 0
    assert value["rotationEulerRad"] == [0, 0, 0]
    assert value["cloneLiveModels"] == value["liveModels"] + 1
    assert value["clonePosition"] == [1, 0, 0] and value["originalPosition"] == [0, 0, 0]
    assert value["afterCloseLiveModels"] == value["liveModels"]


def test_scene_effects(page_results):
    value = _value(page_results, "scene_effects")
    for name in ("ssao", "bloom", "fog", "depthOfField", "vignette"):
        assert value[name] is True, name
    assert value["focusDistance"] == 2.5 and value["aperture"] == 4
    # Dense red fog covers the box at the center.
    red, green, blue, alpha = value["center"]
    assert red > 200 and green < 60 and blue < 60 and alpha == 255


def test_errors(page_results):
    value = _value(page_results, "errors")
    assert value["caught"] == {"badBytes": "AssetError", "badFill": "RangeError", "badMesh": "RangeError",
                               "badSlot": "RangeError", "badUpdate": "RangeError", "missingUrl": "AssetError"}
    assert value["assetIsFilly"] is True
