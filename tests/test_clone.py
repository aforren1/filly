"""Model.clone(): independent instances that share geometry and compiled materials."""

import struct

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


def test_two_clones_posed_differently_in_one_frame(renderer, scene, triangle_glb):
    model = scene.load(small(triangle_glb), clonable=True)
    clone = model.clone()
    assert isinstance(clone, filly.Model) and clone is not model
    model.position = (-0.5, 0, 0)
    clone.position = (0.5, 0, 0)
    clone.rotation_euler_deg = (0, 0, 180)
    # Each instance has its own material instances; the shared glTF material is per model.
    clone.material("red").base_color = (0, 0, 1, 1)
    model.node("triangle").material().base_color = (0, 1, 0, 1)
    image = render(renderer, scene)
    np.testing.assert_array_equal(image[32, 16], [0, 255, 0, 255])
    np.testing.assert_array_equal(image[32, 48], [0, 0, 255, 255])
    # The clone is upside down: its apex points to the bottom of the image.
    assert image[36, 48, 2] == 255 and image[24, 48, 2] == 0
    np.testing.assert_allclose(model.material("red").base_color, [1, 0, 0, 1])
    assert renderer.stats.live_models == 2
    clone.visible = False
    assert render(renderer, scene)[32, 48, 2] == 0
    assert model.visible


@pytest.mark.parametrize("closed", ["original", "clone"])
def test_closing_one_instance_keeps_the_other(renderer, scene, triangle_glb, closed):
    model = scene.load(small(triangle_glb), clonable=True)
    clone = model.clone()
    model.position = (-0.5, 0, 0)
    clone.position = (0.5, 0, 0)
    first, second = (model, clone) if closed == "original" else (clone, model)
    first.close()
    assert first.closed and not second.closed
    image = render(renderer, scene)
    kept = 16 if second is model else 48
    gone = 48 if second is model else 16
    np.testing.assert_array_equal(image[32, kept], [255, 0, 0, 255])
    np.testing.assert_array_equal(image[32, gone], [0, 0, 0, 255])
    # A clone of the survivor still works after the other instance closed.
    third = second.clone()
    third.position = (0, 0.5, 0)
    assert render(renderer, scene)[16, 32, 0] == 255
    second.close()
    third.close()
    assert renderer.stats.live_models == 0
    with pytest.raises(filly.FillyError, match="closed"):
        first.clone()


def test_clone_has_independent_animation_and_lights(renderer, scene, triangle_glb):
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
    model = scene.load(pack(doc, binary), clonable=True)
    clone = model.clone()
    clone.apply_animation("lift", 1, loop=False)
    np.testing.assert_allclose(clone.node("triangle").position, [0, 0.5, 0], atol=1e-6)
    np.testing.assert_allclose(model.node("triangle").position, [0, 0, 0], atol=1e-6)
    assert renderer.stats.live_lights == 2
    clone.light("lamp").intensity = 7
    assert model.light("lamp").intensity == pytest.approx(50)
    assert clone.light(1).node == clone.node("lamp") != model.node("lamp")
    clone.close()
    assert renderer.stats.live_lights == 1


def test_clone_skin_updates_independently(renderer, scene, triangle_glb):
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
    model = scene.load(pack(doc, binary), clonable=True)
    clone = model.clone()
    clone.visible = False
    rest = render(renderer, scene)
    clone.node("joint").position = (0.7, 0, 0)
    # The hidden clone's joint moved; the original's pixels did not.
    np.testing.assert_array_equal(render(renderer, scene), rest)
    model.visible, clone.visible = False, True
    assert np.count_nonzero(render(renderer, scene) != rest) > 100


def test_clone_custom_diffuse_material_matches_original(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission"]
    doc["materials"][0].update(doubleSided=True, extensions={"KHR_materials_diffuse_transmission": {
        "diffuseTransmissionFactor": 1, "diffuseTransmissionColorFactor": [1, 0, 0]}})
    scene.set_environment(np.full((8, 16, 3), 0.5, dtype="f"), intensity=20000)
    scene.add_directional_light(direction=(0, 0, 1), intensity=100000)
    model = scene.load(pack(doc, binary), clonable=True)
    original = render(renderer, scene)
    assert original[32, 32, 0] > 20
    clone = model.clone()
    model.visible = False
    np.testing.assert_array_equal(render(renderer, scene), original)
    clone.close()
    model.visible = True
    np.testing.assert_array_equal(render(renderer, scene), original)


def test_clone_requires_clonable_load(renderer, scene, triangle_glb):
    model = scene.load(small(triangle_glb))
    with pytest.raises(filly.FillyError, match="clonable=True"):
        model.clone()
    # Releasing the source data does not affect the loaded instance.
    np.testing.assert_array_equal(render(renderer, scene)[32, 32], [255, 0, 0, 255])
    clonable = scene.load(small(triangle_glb), clonable=True)
    # A clone shares the asset, so it can be cloned again.
    clonable.clone().clone().close()


def _camera(scene):
    camera = scene.create_camera()
    camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    scene.camera = camera


@pytest.mark.parametrize("first", ["prototype scene", "clone scene"])
def test_clone_into_another_scene(renderer, scene, triangle_glb, first):
    other = renderer.create_scene()
    _camera(other)
    prototype = scene.load(small(triangle_glb), clonable=True)
    prototype.visible = False
    clone = prototype.clone(scene=other)
    assert prototype.memory.models == 2
    # The clone renders in its scene only.
    np.testing.assert_array_equal(render(renderer, other)[32, 32], [255, 0, 0, 255])
    assert render(renderer, scene)[32, 32, 0] == 0
    if first == "prototype scene":
        scene.close()
        assert prototype.closed and not clone.closed
        # The clone holds the asset, so it renders and clones on.
        np.testing.assert_array_equal(render(renderer, other)[32, 32], [255, 0, 0, 255])
        again = clone.clone()
        assert again.memory.models == 2
        other.close()
        assert clone.closed and again.closed
    else:
        other.close()
        assert clone.closed and not prototype.closed
        assert prototype.memory.models == 1
        third = renderer.create_scene()
        _camera(third)
        prototype.clone(scene=third)
        np.testing.assert_array_equal(render(renderer, third)[32, 32], [255, 0, 0, 255])
    assert renderer.stats.live_models == (0 if first == "prototype scene" else 2)


def test_clone_into_a_scene_of_another_renderer_fails(renderer, scene, triangle_glb):
    with filly.Renderer() as second:
        foreign = second.create_scene()
        prototype = scene.load(small(triangle_glb), clonable=True)
        with pytest.raises(ValueError, match="same renderer"):
            prototype.clone(scene=foreign)


def test_memory_estimate(renderer, scene, triangle_glb):
    from test_textures import textured_glb
    document = textured_glb(triangle_glb)
    plain = scene.load(document)
    memory = plain.memory
    # One 1x1 sRGB PNG, a single level: 4 bytes. Positions, UVs, normals, dummy data, indices.
    assert memory.gpu_texture_bytes == 4
    assert memory.gpu_geometry_bytes == 36 + 24 + 3 * 8 + 3 * 4 + 3 * 4
    assert memory.gpu_bytes == memory.gpu_texture_bytes + memory.gpu_geometry_bytes
    assert memory.cpu_bytes == 0 and memory.models == 1
    prototype = scene.load(document, clonable=True)
    kept = prototype.memory
    # The source data: the document that gltfio keeps, and its parse.
    assert len(document) <= kept.cpu_bytes < len(document) + 64 * 1024
    clone = prototype.clone()
    shared = clone.memory
    assert (shared.gpu_bytes, shared.cpu_bytes, shared.models) == (kept.gpu_bytes, kept.cpu_bytes, 2)
    prototype.close()
    assert clone.memory.models == 1
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
    model = scene.load(pack(doc, binary), clonable=True)
    first = model.memory
    # A 3 x 1 x 1 layer of RGBA32F positions and RGBA16I tangents, and a 256-bone buffer.
    assert first.clone_gpu_bytes == 3 * 24 + 256 * 64
    model.clone().close()
    # The closed clone's buffers stay until the asset goes.
    assert model.memory.gpu_geometry_bytes == first.gpu_geometry_bytes + first.clone_gpu_bytes


def test_custom_material_textures_are_shared_by_clones(renderer, scene, triangle_glb):
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
    model = scene.load(pack(doc, binary), clonable=True)
    textures = model.memory.gpu_texture_bytes
    assert textures > 0
    model.clone()
    model.clone()
    assert model.memory.gpu_texture_bytes == textures
