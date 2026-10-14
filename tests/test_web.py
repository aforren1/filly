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


def instances_glb():
    """27 rotated and scaled boxes of one EXT_mesh_gpu_instancing node, with one material.

    More instances than one automatic-instancing draw holds in WebGL (8), so that the web build
    draws several batches.
    """
    box = filly.shapes.box(0.5, 0.5, 0.5)
    grid = np.stack(np.meshgrid(*[np.arange(3)] * 3, indexing="ij"), -1).reshape(-1, 3)
    translations = ((grid - 1) * 0.8).astype(np.float32)
    angles = np.linspace(0, np.pi, 27, dtype=np.float32)
    rotations = np.stack([np.sin(angles / 2) * 0.6, np.sin(angles / 2) * 0.8, 0 * angles, np.cos(angles / 2)], -1)
    scales = np.repeat(np.linspace(0.6, 1.2, 27, dtype=np.float32)[:, None], 3, 1)
    arrays = [box["positions"], box["normals"], box["indices"].reshape(-1), translations,
              rotations.astype(np.float32), scales]
    binary, views, accessors = b"", [], []
    for array in arrays:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": array.nbytes})
        accessor = {"bufferView": len(views) - 1, "componentType": 5125 if array.dtype == np.uint32 else 5126,
                    "count": len(array), "type": {1: "SCALAR", 3: "VEC3", 4: "VEC4"}[array.shape[1] if array.ndim > 1 else 1]}
        if not accessors:
            accessor.update(min=array.min(0).tolist(), max=array.max(0).tolist())
        accessors.append(accessor)
        binary += array.tobytes()
    document = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["EXT_mesh_gpu_instancing"],
        "extensionsRequired": ["EXT_mesh_gpu_instancing"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "extensions": {"EXT_mesh_gpu_instancing": {"attributes": {
            "TRANSLATION": 3, "ROTATION": 4, "SCALE": 5}}}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.5, 0.2, 1], "metallicFactor": 0,
                                                "roughnessFactor": 0.6}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack("<III", 0x46546C67, 2, length)
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def _web_build():
    build = Path(os.environ.get("FILLY_WEB_BUILD", ROOT / "build" / "web"))
    if not (build / "filly-core.wasm").is_file():
        pytest.skip(f"no web build in {build}; run tools/build_filly_web.sh")
    return build


def _run_page(browser, page, folder, wasm_type=None, report=False):
    """Serve the web build, the pages, and tree.glb from folder; return the page's testResult,
    or with `report`, also the console messages. `wasm_type`: the Content-Type of .wasm files."""
    executable = os.environ.get(f"FILLY_TEST_{browser.upper()}", EXECUTABLES.get(browser, ""))
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    if not (WEB / "node_modules" / "puppeteer-core").is_dir():
        pytest.skip("run `npm install` in tests/web first")
    if not Path(executable).is_file():
        pytest.skip(f"{browser} not found at {executable!r}")
    build = _web_build()
    if not (folder / "filly").is_dir():
        shutil.copytree(build, folder / "filly")
        # The page tests the current glue; the build folder may hold an older copy.
        shutil.copy(ROOT / "web" / "filly.js", folder / "filly" / "filly.js")
        for name in ("api.html", "handover.html"):
            shutil.copy(WEB / name, folder)
        (folder / "tree.glb").write_bytes(tree_glb())
        (folder / "instances.glb").write_bytes(instances_glb())
        shutil.copytree(ROOT / "tests" / "data" / "draco-box", folder / "draco-box")
    command = [node, str(WEB / "run_page.mjs"), str(folder), page, browser, executable]
    result = subprocess.run(command + ([wasm_type] if wasm_type else []),
                            capture_output=True, text=True, timeout=300, cwd=WEB)
    assert result.returncode == 0, result.stderr[-3000:]
    output = json.loads(result.stdout.strip().splitlines()[-1])
    assert output["pageErrors"] == [], output["pageErrors"]
    return output if report else output["result"]


@pytest.fixture(scope="module", params=BROWSERS)
def browser_folder(request, tmp_path_factory):
    return request.param, tmp_path_factory.mktemp(f"web-{request.param}")


@pytest.fixture(scope="module")
def page_results(browser_folder):
    return _run_page(browser_folder[0], "api.html", browser_folder[1])


@pytest.fixture(scope="module")
def handover_results(browser_folder):
    return _run_page(browser_folder[0], "handover.html?frames=30", browser_folder[1])


def _value(results, name):
    entry = results[name]
    assert entry["ok"], entry.get("error")
    return entry["value"]


def _plane_scene(renderer, texture=None, base_color=(1, 1, 1, 1)):
    scene = renderer.create_scene()
    model = scene.create_mesh(**filly.shapes.plane(2, 2), unlit=True, base_color=base_color)
    if texture is not None:
        model.material("mesh").base_color_texture = texture
    camera = renderer.create_camera()
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
    assert _value(page_results, "render_options") == [188, 188, 188, 255]
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


def test_imported_textures(page_results):
    # 128 stored linear is sRGB level 188; 128 stored as sRGB is level 128.
    assert _value(page_results, "host_linear") == {"center": [188, 188, 188, 255], "isHost": True,
                                                   "colorSpace": "linear"}
    assert _value(page_results, "host_srgb") == {"center": [128, 128, 128, 255], "wrote": True, "writing": False}


def test_material_texture_slots(page_results):
    assert _value(page_results, "material_texture_slots") == {
        "base": True, "emissive": None, "offset": [0.5, 0], "scale": [0.5, 1], "rotationDeg": 0,
        "emissiveOffset": None, "cleared": None, "clearedOffset": None}


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
        camera = renderer.create_camera()
        camera.set_perspective(fov_y_deg=35, near=0.5, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(-1, -2, -1), intensity=80000)
        scene.environment = renderer.create_environment(np.ones((8, 16, 3), np.float32))
        scene.environment_intensity = 15000
        desktop = _render(renderer, scene, 160, 120)
    _assert_matches_desktop(web, desktop)


def test_instances_match_desktop(page_results):
    # The web build batches at most 8 instances per draw, desktop 64.
    web = np.array(_value(page_results, "instances")).reshape(120, 160, 4)
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        scene.background = (0.1, 0.2, 0.3, 1)
        scene.load(instances_glb())
        camera = renderer.create_camera()
        camera.set_perspective(fov_y_deg=35, near=0.5, far=20)
        camera.position = (1, 2, 5)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(-1, -2, -1), intensity=80000)
        scene.environment = renderer.create_environment(np.ones((8, 16, 3), np.float32))
        scene.environment_intensity = 15000
        desktop = _render(renderer, scene, 160, 120)
    # 27 boxes have more edges than one.
    _assert_matches_desktop(web, desktop, flat_share=0.75)


def test_draco_matches_desktop(page_results):
    # Draco decoding is Filament's; the web build links its own copy of the decoder.
    from test_draco import DRACO_BOX, render_box
    web = np.array(_value(page_results, "draco")).reshape(120, 160, 4)
    with filly.Renderer() as renderer:
        desktop = render_box(renderer, DRACO_BOX)
    _assert_matches_desktop(web, desktop)


def _assert_matches_desktop(web, desktop, flat_share=0.9):
    # Flat 3 x 3 neighborhoods match within one level; edges rasterize differently.
    padded = np.pad(desktop, ((1, 1), (1, 1), (0, 0)), mode="edge")
    stack = np.stack([padded[1 + dy:121 + dy, 1 + dx:161 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
    flat = (stack.max(axis=0) - stack.min(axis=0)).max(axis=2) <= 4
    difference = np.abs(desktop - web).max(axis=2)
    assert flat.mean() > flat_share
    assert difference[flat].max() <= 1, np.bincount(difference[flat])
    assert (difference > 1).mean() < 0.05


def test_model_tree_and_asset(page_results):
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
    assert value["rotationEulerDeg"] == [0, 0, 0]
    assert value["addedLiveModels"] == value["liveModels"] + 1
    assert value["addedPosition"] == [1, 0, 0] and value["originalPosition"] == [0, 0, 0]
    assert value["afterCloseLiveModels"] == value["liveModels"]


def test_load_files(page_results):
    assert _value(page_results, "load_files") == {"names": ["root", "triangle", "front"], "unchanged": True}


def test_scene_effects(page_results):
    value = _value(page_results, "scene_effects")
    for name in ("ssao", "bloom", "fog", "depthOfField", "vignette"):
        assert value[name] is True, name
    assert value["focusDistance"] == 2.5 and value["aperture"] == 4
    assert value["fogColor"] == [1, 0, 0] and value["fogDensity"] == 0.5 and value["fogStart"] == 0
    assert value["vignetteMidpoint"] == 0.25 and value["vignetteColor"] == [0, 0, 0]
    assert (value["vignetteRoundness"], value["vignetteFeather"]) == (0.5, 0.5)
    assert value["environmentIntensity"] == 30000 and value["environmentRotationDeg"] == 0
    assert value["lights"] == {"pointDirection": None, "pointRange": 10, "sunPosition": None, "sunRange": None,
                               "sunCone": None, "spotCone": [15, 30], "spotShadow": [1024, 0.5],
                               "spotConeAfter": [10, 20]}
    # Dense red fog covers the box at the center.
    red, green, blue, alpha = value["center"]
    assert red > 200 and green < 60 and blue < 60 and alpha == 255


def test_errors(page_results):
    value = _value(page_results, "errors")
    assert value["caught"] == {"badBytes": "AssetError", "badFill": "RangeError", "badMesh": "RangeError",
                               "noTexture": "FillyError", "lightLacks": "RangeError", "badUpdate": "RangeError",
                               "missingUrl": "AssetError"}
    assert value["assetIsFilly"] is True
    assert value["unknownMaterial"] == "Unknown material 'wood'; the model has: 'mesh'"


def test_camera_defaults(page_results):
    value = _value(page_results, "camera_defaults")
    # fill 0.5 of the 64-pixel height, although frame() ran before the first render.
    assert abs(value["across"] - 32) <= 1 and abs(value["down"] - 32) <= 1, value
    assert value["keptNear"] == pytest.approx(0.5, rel=1e-4)
    assert value["errors"] == {
        "none": "Scene has no camera. Set scene.camera, or pass camera= to render()",
        "closed": "Scene has no camera: its camera was closed. Set scene.camera, or pass camera= to render()",
    }


def test_gpu_frame_time(page_results):
    value = _value(page_results, "gpu_frame_time")
    assert value["initial"] == [None, None]
    if not value["timer"]:
        # Firefox and Safari do not offer EXT_disjoint_timer_query_webgl2.
        assert value["times"] == []
        return
    assert len(value["times"]) > 10, value
    for number, ms, rendered in value["times"]:
        assert ms > 0 and number < rendered
    # Even frame numbers rendered 16384 times the pixels of odd ones.
    large = np.median([ms for number, ms, _ in value["times"] if number % 2 == 0])
    small = np.median([ms for number, ms, _ in value["times"] if number % 2 == 1])
    assert large > 2 * small, value


def test_wasm_without_wasm_type(browser_folder):
    """A host that sends the .wasm file as application/octet-stream, as Pavlovia does: the module
    loads without the console error and second download of Emscripten's fallback."""
    browser, folder = browser_folder
    output = _run_page(browser, "handover.html", folder, wasm_type="application/octet-stream",
                       report=True)
    assert output["consoleErrors"] == [], output["consoleErrors"]
    warnings = [text for text in output["consoleWarnings"] if "wasm" in text.lower()]
    assert warnings == [], warnings
    assert output["result"]["readValues"]["width"] == 512


def test_handover(handover_results):
    calls = handover_results["calls"]
    # Property reads after the hand-back leave the context with the page.
    assert calls["reads"]["total"] == 0, calls["reads"]
    assert handover_results["readValues"]["width"] == 512
    # Enter queries no GL state, and hand-back undoes only what the core changed: about 6
    # units of a lit scene instead of every unit (32 on ANGLE).
    assert "getParameter" not in calls["enter"]["byName"], calls["enter"]
    assert calls["enter"]["total"] <= 20, calls["enter"]
    assert calls["handBack"]["total"] <= 60, calls["handBack"]
    state = handover_results["state"]
    assert state["leftovers"] == [] and state["error"] == 0, state
    # The core works with the GL defaults, and the page gets its own pixel-store values back,
    # both those set before filly attached and those set after.
    entered = handover_results["entered"]
    assert entered == {"flipY": False, "premultiply": False, "unpackAlignment": 4, "packAlignment": 4}, entered
    assert (state["flipY"], state["premultiply"], state["unpackAlignment"], state["packAlignment"]) \
        == (True, True, 1, 8), state


def test_model_cache(page_results):
    value = _value(page_results, "model_cache")
    size = len(tree_glb())
    assert value["fetchedBytes"] == size
    kept, memory = value["kept"], value["memory"]
    assert kept["models"] == 0 and memory["models"] == 2 and memory["gpuTextureBytes"] == 0
    assert memory["gpuBytes"] == memory["gpuTextureBytes"] + memory["gpuGeometryBytes"] > 0
    # The open asset keeps its document.
    assert memory["cpuBytes"] >= size
    with filly.Renderer() as renderer:
        asset = renderer.load_asset(tree_glb())
        scene = renderer.create_scene()
        scene.add(asset)
        scene.add(asset)
        desktop = asset.memory
    # The parse's size differs: WebAssembly has 4-byte pointers.
    assert memory["gpuBytes"] == desktop.gpu_bytes
    # Closing one scene keeps the asset and the model in the other scene.
    assert value["firstClosed"] is True
    assert value["modelsAfterClose"] == 1 and value["againModels"] == 2
    # Closing the asset releases its source data; its models stay.
    assert value["assetClosed"] is True
    assert value["afterAssetClose"]["models"] == 2 and value["afterAssetClose"]["cpuBytes"] < size
    assert value["liveAfter"] == value["liveModels"] - 2


def _desktop_picks(renderer, scene, target, points):
    queries = [renderer.pick(scene, x, y) for x, y in points]
    renderer.render(scene, target)
    renderer.finish()
    return [query.result for query in queries]


def _assert_picks_match(web, desktop, models):
    assert len(web) == len(desktop)
    for hit, expected in zip(web, desktop):
        if expected is None:
            assert hit is None
            continue
        assert hit["model"] == models.index(expected.model)
        assert hit["node"] == expected.node.index
        # The same float depth from the same projection; WebGL may round differently.
        np.testing.assert_allclose(hit["position"], expected.position, atol=1e-4)
        assert hit["depth"] == pytest.approx(expected.depth, abs=1e-4)


def test_pick_matches_desktop(page_results):
    value = _value(page_results, "pick")
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        back = scene.create_mesh(**filly.shapes.plane(1.6, 1.6), unlit=True, base_color=(0, 1, 0, 1))
        back.position = (0, 0, -0.5)
        front = scene.create_mesh(**filly.shapes.box(0.8, 0.8, 0.8), unlit=True)
        front.rotation_euler_deg = (20, 30, 0)
        glass = scene.create_mesh(**filly.shapes.plane(0.4, 0.4), unlit=True, base_color=(1, 0, 0, 0.3),
                                  alpha_mode="blend")
        glass.position = (0.55, -0.55, 0.1)
        camera = renderer.create_camera()
        camera.set_perspective(fov_y_deg=40, near=0.1, far=20)
        camera.position = (0.3, 0.4, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        target = renderer.create_render_target(width=64, height=64)
        layers = _desktop_picks(renderer, scene, target, [(32, 32), (48, 15), (49, 49), (5, 5)])
        scene.pick_blended = True
        blended = _desktop_picks(renderer, scene, target, [(48, 15)])
        instanced = renderer.create_scene()
        model = instanced.load(instances_glb())
        view = renderer.create_camera()
        view.set_perspective(fov_y_deg=35, near=0.5, far=20)
        view.position = (1, 2, 5)
        view.look_at((0, 0, 0))
        instanced.camera = view
        wide = renderer.create_render_target(width=160, height=120)
        instances = _desktop_picks(renderer, instanced, wide, [(80, 60), (40, 30), (120, 90), (100, 40), (60, 85), (5, 5)])
        models = [front, back, glass, model]
        assert [hit and hit.model for hit in layers] == [front, back, back, None] and blended[0].model == glass
        assert len({hit.node.index for hit in instances if hit}) == 5
        for name, desktop in (("layers", layers), ("blended", blended), ("instances", instances)):
            web = value[name]
            # The query waits for the render, which takes it: its frame is that render's.
            assert web["queued"] is False and set(web["frames"]) == {0}
            _assert_picks_match(web["hits"], desktop, models)
    # Frames from the render that took the pick to the first ready() that returned true, with
    # one render per requestAnimationFrame: 2 or 3 in Chrome, Edge, and Firefox on the test machine.
    print("web pick latency (frames):", value["latency"])
    assert max(value["latency"]) <= 4, value["latency"]


def test_environment(page_results):
    value = _value(page_results, "environment")
    assert value["defaults"] == {"environment": None, "intensity": 30000, "rotationDeg": 0}
    assert value["shared"] is True and value["intensities"] == [30000, 15000]
    assert value["afterClose"] == [None, None, True]
