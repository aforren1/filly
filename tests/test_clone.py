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
