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


def skinned_rig(morph=False):
    """A Sketchfab-style rig: the armature scales by 100 and the inverse bind matrices by 0.01.

    Bind-pose vertices are world positions. The mesh node sits under the armature, but skinning
    ignores its transform. Joint 1 rests 90 degrees about +Z from its bind pose, around (5, 1, 0).
    Rest-pose vertices: (4, 0, 0), (6, 0, 0), (4, 1, 0) from (5, 2, 0), and (4.5, 1.5, 1) from
    (5, 2, 1) half on each joint. With the morph, vertex 3 at weight one is (5, 2, 3) in bind
    space and (4.5, 1.5, 3) at rest.
    """
    armature = np.array([[100, 0, 0, 5], [0, 100, 0, 0], [0, 0, 100, 0], [0, 0, 0, 1]], dtype=float)
    joint1_bind = armature @ np.array([[1, 0, 0, 0], [0, 1, 0, 0.01], [0, 0, 1, 0], [0, 0, 0, 1]])
    inverse_binds = [np.linalg.inv(armature), np.linalg.inv(joint1_bind)]
    positions = np.float32([[4, 0, 0], [6, 0, 0], [5, 2, 0], [5, 2, 1]])
    joints = np.uint8([[0, 0, 0, 0], [0, 0, 0, 0], [1, 0, 0, 0], [0, 1, 0, 0]])
    weights = np.float32([[1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [0.5, 0.5, 0, 0]])
    indices = np.uint16([0, 1, 2, 0, 1, 3])
    delta = np.float32([[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 2]])
    matrices = np.float32([m.T for m in inverse_binds])  # column-major
    chunks = [positions, joints, weights, matrices, indices] + ([delta] if morph else [])
    binary, views = b"", []
    for chunk in chunks:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": chunk.nbytes})
        binary += chunk.tobytes() + bytes(-chunk.nbytes % 4)
    views[4]["target"] = 34963
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3",
         "min": positions.min(0).tolist(), "max": positions.max(0).tolist()},
        {"bufferView": 1, "componentType": 5121, "count": 4, "type": "VEC4"},
        {"bufferView": 2, "componentType": 5126, "count": 4, "type": "VEC4"},
        {"bufferView": 3, "componentType": 5126, "count": 2, "type": "MAT4"},
        {"bufferView": 4, "componentType": 5123, "count": 6, "type": "SCALAR"},
    ]
    primitive = {"attributes": {"POSITION": 0, "JOINTS_0": 1, "WEIGHTS_0": 2}, "indices": 4, "material": 0}
    mesh = {"primitives": [primitive]}
    if morph:
        accessors.append({"bufferView": 5, "componentType": 5126, "count": 4, "type": "VEC3",
                          "min": [0, 0, 0], "max": [0, 0, 2]})
        primitive["targets"] = [{"POSITION": 5}]
        mesh["weights"] = [0]
    document = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"name": "armature", "children": [1, 2], "translation": [5, 0, 0], "scale": [100] * 3},
            {"name": "mesh", "mesh": 0, "skin": 0},
            {"name": "joint0", "children": [3]},
            {"name": "joint1", "translation": [0, 0.01, 0], "rotation": [0, 0, 2 ** -0.5, 2 ** -0.5]},
        ],
        "skins": [{"joints": [2, 3], "inverseBindMatrices": 3}],
        "meshes": [mesh],
        "materials": [{"doubleSided": True, "extensions": {"KHR_materials_unlit": {}},
                       "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def test_skinned_bounds_are_the_rest_pose(scene):
    # gltfio's box moved the bind-space accessor box by the mesh node's transform:
    # [[405, 0, 0], [605, 200, 100]] here.
    model = scene.load(skinned_rig())
    np.testing.assert_allclose(model.bounds, [[4, 0, 0], [6, 1.5, 1]], atol=1e-5)
    # Each morph target counts at weight one, as in the unskinned accessor bounds.
    morphed = scene.load(skinned_rig(morph=True))
    np.testing.assert_allclose(morphed.bounds, [[4, 0, 0], [6, 1.5, 3]], atol=1e-5)


def test_skinned_bounds_frame_the_rendered_mesh(renderer, scene):
    model = scene.load(skinned_rig())
    low, high = np.asarray(model.bounds)
    center = (low + high) / 2
    # One pixel per 1/32 unit: the bounds' x and y span 64 by 48 pixels in the middle.
    scene.camera.set_orthographic(left=-2, right=2, bottom=-2, top=2, near=0.1, far=20)
    scene.camera.position = (center[0], center[1], 10)
    scene.camera.look_at(tuple(center))
    target = renderer.create_render_target(width=128, height=128)
    renderer.render(scene, target)
    rows, columns = np.nonzero(target.read()[..., 0] > 128)
    # Image rows run down; the rest-pose triangles fill x in [4, 6] and y in [0, 1.5]. The
    # tolerance covers pixel centers on the thin top vertex and the edges.
    np.testing.assert_allclose([columns.min(), columns.max()], [32, 95], atol=1)
    np.testing.assert_allclose([rows.min(), rows.max()], [40, 87], atol=1)
