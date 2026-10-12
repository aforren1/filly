"""Renderer.load_asset() and Scene.add(): independent models that share geometry and materials."""

import struct
import warnings

import numpy as np
import pytest

import filly
from test_features import lit, pack, unpack

pytestmark = pytest.mark.gpu


def small(triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["nodes"][0]["scale"] = [0.4] * 3
    return pack(doc, binary)


def render(renderer, scene, size=64):
    target = renderer.create_render_target(width=size, height=size)
    renderer.render(scene, target)
    image = target.read()
    target.close()
    return image


def test_two_models_posed_differently_in_one_frame(renderer, scene, triangle_glb):
    asset = renderer.load_asset(small(triangle_glb))
    model, other = scene.add(asset), scene.add(asset)
    assert isinstance(asset, filly.Asset) and isinstance(other, filly.Model)
    model.position = (-0.5, 0, 0)
    other.position = (0.5, 0, 0)
    other.rotation_euler_deg = (0, 0, 180)
    # Each model has its own material instances; the shared glTF material is per model.
    other.material("red").base_color = (0, 0, 1, 1)
    model.node("triangle").material().base_color = (0, 1, 0, 1)
    image = render(renderer, scene)
    np.testing.assert_array_equal(image[32, 16], [0, 255, 0, 255])
    np.testing.assert_array_equal(image[32, 48], [0, 0, 255, 255])
    # The other model is upside down: its apex points to the bottom of the image.
    assert image[36, 48, 2] == 255 and image[24, 48, 2] == 0
    np.testing.assert_allclose(model.material("red").base_color, [1, 0, 0, 1])
    assert renderer.stats.live_models == 2
    other.visible = False
    assert render(renderer, scene)[32, 48, 2] == 0
    assert model.visible
    # A new model starts from the authored state, not from another model's edits.
    np.testing.assert_allclose(scene.add(asset).material("red").base_color, [1, 0, 0, 1])


@pytest.mark.parametrize("closed", ["first", "second"])
def test_closing_one_model_keeps_the_other(renderer, scene, triangle_glb, closed):
    asset = renderer.load_asset(small(triangle_glb))
    first, second = scene.add(asset), scene.add(asset)
    first.position = (-0.5, 0, 0)
    second.position = (0.5, 0, 0)
    gone, kept = (first, second) if closed == "first" else (second, first)
    gone.close()
    assert gone.closed and not kept.closed
    image = render(renderer, scene)
    np.testing.assert_array_equal(image[32, 48 if kept is second else 16], [255, 0, 0, 255])
    np.testing.assert_array_equal(image[32, 16 if kept is second else 48], [0, 0, 0, 255])
    third = scene.add(asset)
    third.position = (0, 0.5, 0)
    assert render(renderer, scene)[16, 32, 0] == 255
    kept.close()
    third.close()
    assert renderer.stats.live_models == 0
    assert asset.memory.models == 0


def test_models_have_independent_animation_and_lights(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["nodes"][0]["scale"] = [0.4] * 3
    doc["extensionsUsed"].append("KHR_lights_punctual")
    doc["extensions"] = {"KHR_lights_punctual": {"lights": [{"type": "spot", "spot": {}, "intensity": 50, "range": 5}]}}
    doc["nodes"].append({"name": "lamp", "extensions": {"KHR_lights_punctual": {"light": 0}}})
    doc["scenes"][0]["nodes"].append(1)
    offset = len(binary)
    binary += struct.pack("<8f", 0, 1, 0, 0, 0, 0, 0.5, 0)
    doc["bufferViews"] += [{"buffer": 0, "byteOffset": offset, "byteLength": 8},
                           {"buffer": 0, "byteOffset": offset + 8, "byteLength": 24}]
    doc["accessors"] += [{"bufferView": 1, "componentType": 5126, "count": 2, "type": "SCALAR", "min": [0], "max": [1]},
                         {"bufferView": 2, "componentType": 5126, "count": 2, "type": "VEC3"}]
    doc["animations"] = [{"name": "lift", "samplers": [{"input": 1, "output": 2}],
                          "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}]}]
    asset = renderer.load_asset(pack(doc, binary))
    model, other = scene.add(asset), scene.add(asset)
    other.apply_animation("lift", 1, loop=False)
    np.testing.assert_allclose(other.node("triangle").position, [0, 0.5, 0], atol=1e-6)
    np.testing.assert_allclose(model.node("triangle").position, [0, 0, 0], atol=1e-6)
    assert renderer.stats.live_lights == 2
    other.light("lamp").intensity = 7
    assert model.light("lamp").intensity == pytest.approx(50)
    assert other.light(1).node == other.node("lamp") != model.node("lamp")
    other.close()
    assert renderer.stats.live_lights == 1


def test_skins_update_independently(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    binary += bytes(12) + struct.pack("<12f", *([1, 0, 0, 0] * 3))
    doc["bufferViews"] += [{"buffer": 0, "byteOffset": offset, "byteLength": 12},
                           {"buffer": 0, "byteOffset": offset + 12, "byteLength": 48}]
    doc["accessors"] += [{"bufferView": 1, "componentType": 5121, "count": 3, "type": "VEC4"},
                         {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC4"}]
    doc["meshes"][0]["primitives"][0]["attributes"].update(JOINTS_0=1, WEIGHTS_0=2)
    doc["nodes"][0]["skin"] = 0
    doc["nodes"].append({"name": "joint"})
    doc["scenes"][0]["nodes"].append(1)
    doc["skins"] = [{"joints": [1]}]
    asset = renderer.load_asset(pack(doc, binary))
    model, other = scene.add(asset), scene.add(asset)
    other.visible = False
    rest = render(renderer, scene)
    other.node("joint").position = (0.7, 0, 0)
    # The hidden model's joint moved; the visible model's pixels did not.
    np.testing.assert_array_equal(render(renderer, scene), rest)
    model.visible, other.visible = False, True
    assert np.count_nonzero(render(renderer, scene) != rest) > 100


def test_custom_diffuse_material_matches_across_models(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission"]
    doc["materials"][0].update(doubleSided=True, extensions={"KHR_materials_diffuse_transmission": {
        "diffuseTransmissionFactor": 1, "diffuseTransmissionColorFactor": [1, 0, 0]}})
    scene.environment = renderer.create_environment(np.full((8, 16, 3), 0.5, dtype="f"))
    scene.environment_intensity = 20000
    scene.add_directional_light(direction=(0, 0, 1), intensity=100000)
    asset = renderer.load_asset(pack(doc, binary))
    model = scene.add(asset)
    original = render(renderer, scene)
    assert original[32, 32, 0] > 20
    other = scene.add(asset)
    model.visible = False
    np.testing.assert_array_equal(render(renderer, scene), original)
    other.close()
    model.visible = True
    np.testing.assert_array_equal(render(renderer, scene), original)


def test_closed_asset_keeps_its_models(renderer, scene, triangle_glb):
    with renderer.load_asset(small(triangle_glb)) as asset:
        model = scene.add(asset)
        kept = asset.memory.cpu_bytes
    assert asset.closed and not model.closed
    # The source data is gone; the GPU data stays with the model.
    assert model.memory.cpu_bytes < kept and model.memory.models == 1
    np.testing.assert_array_equal(render(renderer, scene)[32, 32], [255, 0, 0, 255])
    with pytest.raises(filly.FillyError, match="Asset is closed"):
        scene.add(asset)
    with pytest.raises(filly.FillyError, match="Asset is closed"):
        asset.memory
    with pytest.raises(filly.FillyError, match="closed"):
        with asset:
            pass
    asset.close()


def _camera(renderer, scene):
    camera = renderer.create_camera()
    camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    scene.camera = camera


def test_asset_outlives_scenes(renderer, scene, triangle_glb):
    other = renderer.create_scene()
    _camera(renderer, other)
    asset = renderer.load_asset(small(triangle_glb))
    model = other.add(asset)
    assert asset.memory.models == 1
    # The model renders in its scene only.
    np.testing.assert_array_equal(render(renderer, other)[32, 32], [255, 0, 0, 255])
    assert render(renderer, scene)[32, 32, 0] == 0
    other.close()
    assert model.closed and asset.memory.models == 0
    third = renderer.create_scene()
    _camera(renderer, third)
    third.add(asset)
    np.testing.assert_array_equal(render(renderer, third)[32, 32], [255, 0, 0, 255])
    renderer.close()
    assert asset.closed


def test_asset_of_another_renderer_fails(renderer, scene, triangle_glb):
    with filly.Renderer() as second:
        foreign = second.load_asset(small(triangle_glb))
        with pytest.raises(ValueError, match="same renderer"):
            scene.add(foreign)


def test_load_asset_warnings(renderer, triangle_glb, tmp_path):
    doc, binary = unpack(triangle_glb)
    doc["extensionsUsed"].append("VENDOR_not_supported")
    document = pack(doc, binary)
    with pytest.warns(filly.AssetCompatibilityWarning, match="VENDOR_not_supported"):
        renderer.load_asset(document).close()
    with pytest.raises(filly.AssetError, match="VENDOR_not_supported"):
        renderer.load_asset(document, strict=True)
    path = tmp_path / "triangle.glb"
    path.write_bytes(small(triangle_glb))
    assert renderer.load_asset(path).memory.models == 0
    # A warning that the filter turns into an error closes the asset.
    with warnings.catch_warnings():
        warnings.simplefilter("error", filly.AssetCompatibilityWarning)
        with pytest.raises(filly.AssetCompatibilityWarning):
            renderer.load_asset(document)


def test_memory_estimate(renderer, scene, triangle_glb):
    from test_textures import textured_glb
    document = textured_glb(triangle_glb)
    plain = scene.load(document)
    memory = plain.memory
    # One 1x1 sRGB PNG, a single level: 4 bytes. Positions, UVs, normals, dummy data, indices.
    assert memory.gpu_texture_bytes == 4
    assert memory.gpu_geometry_bytes == 36 + 24 + 3 * 8 + 3 * 4 + 3 * 4
    assert memory.gpu_bytes == memory.gpu_texture_bytes + memory.gpu_geometry_bytes
    # scene.load() keeps no source data: no handle can add the asset again.
    assert memory.cpu_bytes == 0 and memory.models == 1
    asset = renderer.load_asset(document)
    kept = asset.memory
    assert kept.models == 0 and kept.gpu_bytes == memory.gpu_bytes
    # The source data: the document that gltfio keeps, and its parse.
    assert len(document) <= kept.cpu_bytes < len(document) + 64 * 1024
    model = scene.add(asset)
    shared = model.memory
    assert (shared.gpu_bytes, shared.cpu_bytes, shared.models) == (kept.gpu_bytes, kept.cpu_bytes, 1)
    asset.close()
    assert model.memory.cpu_bytes == 0 and model.memory.models == 1
    mesh = scene.create_mesh(**filly.shapes.box())
    box = mesh.memory
    assert box.gpu_geometry_bytes >= 24 * (12 + 8 + 8 + 4) + 12 * 12
    assert box.cpu_bytes >= 24 * 12


def test_memory_counts_morph_and_bone_buffers_per_instance(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    binary += struct.pack("<9f", 0, 0.3, 0, 0, 0.3, 0, 0, 0.3, 0)
    doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 36})
    doc["accessors"].append({"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3",
                             "min": [0, 0.3, 0], "max": [0, 0.3, 0]})
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 1}]
    asset = renderer.load_asset(pack(doc, binary))
    first = scene.add(asset).memory
    # A 3 x 1 x 1 layer of RGBA32F positions and RGBA16I tangents, and a 256-bone buffer.
    assert first.instance_gpu_bytes == 3 * 24 + 256 * 64
    scene.add(asset).close()
    # The closed model's buffers stay until the asset goes.
    assert asset.memory.gpu_geometry_bytes == first.gpu_geometry_bytes + first.instance_gpu_bytes


def test_custom_material_textures_are_shared_by_models(renderer, scene, triangle_glb):
    from test_textures import _png
    doc, binary = unpack(triangle_glb)
    lit(doc)
    image = _png((0, 255, 0))
    offset = len(binary)
    binary += image + b"\0" * (-len(image) % 4)
    uv = len(binary)
    binary += struct.pack("<6f", 0, 0, 0, 0, 0, 0)
    doc["bufferViews"] += [{"buffer": 0, "byteOffset": offset, "byteLength": len(image)},
                           {"buffer": 0, "byteOffset": uv, "byteLength": 24}]
    doc["accessors"].append({"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC2"})
    doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = 1
    doc["images"] = [{"bufferView": 1, "mimeType": "image/png"}]
    doc["textures"] = [{"source": 0}]
    doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission"]
    doc["materials"][0]["extensions"] = {"KHR_materials_diffuse_transmission": {
        "diffuseTransmissionFactor": 1, "diffuseTransmissionTexture": {"index": 0}}}
    asset = renderer.load_asset(pack(doc, binary))
    textures = scene.add(asset).memory.gpu_texture_bytes
    assert textures > 0
    scene.add(asset)
    scene.add(asset)
    assert asset.memory.gpu_texture_bytes == textures


@pytest.mark.parametrize("load", ["scene", "asset"])
def test_glTF_textures_reach_every_model(renderer, scene, triangle_glb, load):
    """The asset loads its textures before any model exists; each model binds them."""
    from test_textures import textured_glb
    document = textured_glb(triangle_glb)
    if load == "scene":
        scene.load(document)
    else:
        asset = renderer.load_asset(document)
        scene.add(asset).visible = False
        scene.add(asset)
    # The green texture, not the white base color factor.
    np.testing.assert_array_equal(render(renderer, scene)[32, 32], [0, 255, 0, 255])


@pytest.mark.parametrize("model", [0, 1])
def test_morph_targets_of_every_model(renderer, scene, triangle_glb, model):
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    binary += struct.pack("<9f", 0, 0.5, 0, 0, 0.5, 0, 0, 0.5, 0)
    doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 36})
    doc["accessors"].append({"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3",
                             "min": [0, 0.5, 0], "max": [0, 0.5, 0]})
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 1}]
    asset = renderer.load_asset(pack(doc, binary))
    models = [scene.add(asset), scene.add(asset)]
    models[1 - model].visible = False
    rest = render(renderer, scene)
    models[model].node("triangle").set_morph_weights([1])
    moved = render(renderer, scene)
    # The bottom edge moves up by 0.5 units, 16 pixels, past row 44.
    assert rest[44, 32, 0] == 255 and moved[44, 32, 0] == 0 and moved[26, 32, 0] == 255


def test_lit_morph_targets_match_across_models(renderer, scene, triangle_glb):
    """Later models get the morphed tangent frames that gltfio computes for the first."""
    doc, binary = unpack(triangle_glb)
    lit(doc)
    offset = len(binary)
    # The apex moves toward the camera, which tilts the surface and changes its shading.
    binary += struct.pack("<9f", 0, 0, 0, 0, 0, 0, 0, 0, 0.8)
    doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 36})
    doc["accessors"].append({"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3",
                             "min": [0, 0, 0], "max": [0, 0, 0.8]})
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 1}]
    doc["meshes"][0]["weights"] = [0]
    scene.add_directional_light(direction=(0, -0.5, -1), intensity=100000)
    asset = renderer.load_asset(pack(doc, binary))
    models = [scene.add(asset), scene.add(asset)]
    images = []
    for shown in models:
        for model in models:
            model.visible = model is shown
        rest = render(renderer, scene)
        shown.node("triangle").set_morph_weights([1])
        images.append(render(renderer, scene))
        assert np.abs(images[-1].astype(int) - rest).max() > 10
    np.testing.assert_array_equal(images[0], images[1])
