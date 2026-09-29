import numpy as np
import pytest
import json
import struct

import filly

pytestmark = pytest.mark.gpu


def edit_glb(data, edit):
    size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + size])
    edit(document)
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary_chunk = data[20 + size:]
    length = 20 + len(encoded) + len(binary_chunk)
    return struct.pack("<III", 0x46546C67, 2, length) + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + binary_chunk


def test_named_node_and_material_pixels(renderer, scene, triangle_glb):
    model = scene.load(triangle_glb)
    node = model.node("triangle")
    assert "triangle" in model.node_names
    assert "red" in model.material_names
    material = model.material("red")
    np.testing.assert_allclose(material.base_color, [1, 0, 0, 1])
    material.base_color = (0, 1, 0, 1)
    np.testing.assert_allclose(node.material().base_color, [0, 1, 0, 1])
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    image = target.read()
    np.testing.assert_array_equal(image[32, 32], [0, 255, 0, 255])
    node.position = (0.5, 0, 0)
    renderer.render(scene, target)
    shifted = target.read()
    assert np.where(shifted[..., 1] > 200)[1].mean() > np.where(image[..., 1] > 200)[1].mean() + 10
    np.testing.assert_array_equal(model.position, [0, 0, 0])
    with pytest.raises(filly.AssetError):
        model.node("absent")
    with pytest.raises(filly.AssetError):
        model.material("absent")
    with pytest.raises(filly.AssetError):
        node.material(99)
    with pytest.raises(ValueError):
        material.base_color = (2, 0, 0, 1)
    renderer.close()
    with pytest.raises(filly.FillyError):
        node.position = (0, 0, 0)
    with pytest.raises(filly.FillyError):
        _ = material.base_color


def test_trs_conventions_and_validation(scene, triangle_glb):
    model = scene.load(triangle_glb)
    model.position = (1, 2, 3)
    model.scale = (2, 3, 4)
    model.rotation_euler_deg = (0, 0, 90)
    expected = np.array([[0, -3, 0, 1], [2, 0, 0, 2], [0, 0, 4, 3], [0, 0, 0, 1]])
    np.testing.assert_allclose(model.transform, expected, atol=1e-6)
    np.testing.assert_allclose(model.scale, [2, 3, 4], atol=1e-6)
    np.testing.assert_allclose(model.rotation_euler_rad, [0, 0, np.pi / 2], atol=1e-6)
    model.quaternion = (0, 0, 0, 2)
    np.testing.assert_allclose(model.quaternion, [0, 0, 0, 1], atol=1e-6)
    model.rotation_euler_deg = (20, 30, 40)
    np.testing.assert_allclose(model.rotation_euler_deg, [20, 30, 40], atol=1e-4)
    with pytest.raises(ValueError):
        model.quaternion = (0, 0, 0, 0)
    with pytest.raises(ValueError):
        model.scale = (0, 1, 1)
    sheared = np.eye(4)
    sheared[0, 1] = 0.5
    model.transform = sheared
    with pytest.raises(ValueError, match="shear"):
        model.rotation_euler_deg = (0, 0, 0)


def test_closed_target_rejects_use(renderer, scene):
    target = renderer.create_render_target(width=8, height=8)
    assert isinstance(target, filly.OffscreenTarget) and not target.closed
    assert not hasattr(target, "acquire")
    target.close()
    target.close()
    assert target.closed
    with pytest.raises(filly.FillyError, match="closed"):
        renderer.render(scene, target)
    with pytest.raises(filly.FillyError, match="closed"):
        target.read()


def test_lit_material_factors_and_instance_isolation(scene, triangle_glb):
    def make_lit(document):
        document.pop("extensionsRequired")
        document.pop("extensionsUsed")
        document["materials"][0].pop("extensions")
    data = edit_glb(triangle_glb, make_lit)
    first = scene.load(data).material("red")
    second = scene.load(data).material("red")
    first.base_color = (0.2, 0.4, 0.6, 1)
    first.metallic = 0.3
    first.roughness = 0.7
    assert first.metallic == pytest.approx(0.3)
    assert first.roughness == pytest.approx(0.7)
    np.testing.assert_allclose(second.base_color, [1, 0, 0, 1])
    with pytest.raises(ValueError):
        first.metallic = -1
    with pytest.raises(ValueError):
        first.roughness = float("nan")


def test_node_hierarchy_and_ambiguous_names(renderer, scene, triangle_glb):
    def hierarchy(document):
        document["nodes"] = [
            {"name": "parent", "children": [1], "translation": [0.5, 0, 0]},
            {"name": "child", "mesh": 0, "translation": [-0.5, 0, 0]},
            {"name": "duplicate"}, {"name": "duplicate"},
        ]
        document["scenes"][0]["nodes"] = [0, 2, 3]
    model = scene.load(edit_glb(triangle_glb, hierarchy))
    parent = model.node("parent")
    child = model.node("child")
    target = renderer.create_render_target(width=32, height=32)
    renderer.render(scene, target)
    assert target.read()[16, 16, 0] == 255
    child.position = (2, 0, 0)
    renderer.render(scene, target)
    assert target.read()[..., 0].max() == 0
    np.testing.assert_array_equal(parent.position, [0.5, 0, 0])
    with pytest.raises(filly.AssetError, match="Ambiguous"):
        model.node("duplicate")


def test_asset_bounds_include_authored_transforms(scene, triangle_glb):
    def transform(document):
        document["nodes"][0].update(translation=[10, 20, 30], scale=[2, 3, 4])
    model = scene.load(edit_glb(triangle_glb, transform))
    expected = [[8.8, 18.8, 30], [11.2, 22.4, 30]]
    np.testing.assert_allclose(model.bounds, expected, atol=1e-5)
    model.position = (100, 0, 0)
    model.node("triangle").position = (0, 0, 0)
    np.testing.assert_allclose(model.bounds, expected, atol=1e-5)
