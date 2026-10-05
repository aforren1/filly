"""Node hierarchy access and name-or-index lookup."""

import numpy as np
import pytest

import filly
from test_features import pack, unpack

pytestmark = pytest.mark.gpu


def tree_asset(triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["meshes"][0]["name"] = "shape"
    doc["nodes"] = [
        {"name": "root", "children": [1, 2]},
        {"mesh": 0, "translation": [-0.5, 0, 0]},
        {"name": "dup", "mesh": 0, "translation": [0.5, 0, 0]},
        {"name": "dup", "mesh": 0, "translation": [0, 0.5, 0], "scale": [0.2] * 3},
        {"name": "eye", "camera": 0, "translation": [0, 0, 3]},
        {"name": "lamp", "extensions": {"KHR_lights_punctual": {"light": 0}}},
    ]
    doc["scenes"][0]["nodes"] = [0, 3, 4, 5]
    doc["cameras"] = [{"type": "orthographic", "orthographic": {"xmag": 1, "ymag": 1, "znear": 0.1, "zfar": 10}}]
    doc["extensionsUsed"].append("KHR_lights_punctual")
    doc["extensions"] = {"KHR_lights_punctual": {"lights": [{"type": "point", "intensity": 10}]}}
    return pack(doc, binary)


def test_nodes_names_indices_and_hierarchy(scene, triangle_glb):
    model = scene.load(tree_asset(triangle_glb))
    nodes = model.nodes
    assert [node.index for node in nodes] == [0, 1, 2, 3, 4, 5]
    # Node 1 is unnamed; gltfio's fallback to the mesh name does not apply.
    assert [node.name for node in nodes] == ["root", None, "dup", "dup", "eye", "lamp"]
    assert [node.mesh_name for node in nodes] == [None, "shape", "shape", "shape", None, None]
    assert model.node_names == ["root", "dup", "dup", "eye", "lamp"]
    root = model.node("root")
    assert root == model.node(0) == nodes[0] and hash(root) == hash(model.node(0))
    assert root.parent is None and model.node(3).parent is None
    assert model.node(1).parent == root
    assert root.children == [model.node(1), model.node(2)]
    assert model.node(1).children == []
    np.testing.assert_allclose(model.node(2).position, [0.5, 0, 0])
    assert repr(model.node(2)) == "<Node 2 'dup'>"


def test_lookup_errors(scene, triangle_glb):
    model = scene.load(tree_asset(triangle_glb))
    with pytest.raises(filly.AssetError, match="Ambiguous node name 'dup'.*2, 3"):
        model.node("dup")
    with pytest.raises(filly.AssetError, match="Unknown node 'missing'; the model has: '.*'dup', 'dup'"):
        model.node("missing")
    with pytest.raises(filly.AssetError, match="glTF index 6"):
        model.node(6)
    with pytest.raises(filly.AssetError, match="glTF index -1"):
        model.node(-1)
    for key in (True, 1.0, None):
        with pytest.raises(TypeError):
            model.node(key)


def test_lights_and_cameras_by_node(scene, triangle_glb):
    model = scene.load(tree_asset(triangle_glb))
    # Keys are node names or glTF node indices, as for model.node().
    camera = model.camera("eye")
    # One handle per camera node: lookups return the same camera.
    assert camera == model.camera(4) and hash(camera) == hash(model.camera(4))
    assert model.cameras == [camera] and camera.node == model.node("eye")
    scene.camera = model.camera(4)
    assert scene.camera == camera
    model.light(5).intensity = 20
    lamp = model.light("lamp")
    assert lamp.intensity == pytest.approx(20)
    assert model.lights == [lamp] and hash(lamp) == hash(model.light(5))
    assert lamp.node == model.node(5) and lamp.node.name == "lamp"
    with pytest.raises(filly.AssetError, match="glTF node 0 has no camera"):
        model.camera(0)
    # By name, only the nodes that carry a light or camera count; the error lists them.
    with pytest.raises(filly.AssetError, match="Unknown light node 'eye'; the model has: 'lamp'$"):
        model.light("eye")
    with pytest.raises(filly.AssetError, match="glTF index 6"):
        model.light(6)
    with pytest.raises(filly.AssetError, match="Unknown camera node 'missing'; the model has: 'eye'$"):
        model.camera("missing")
    with pytest.raises(filly.AssetError, match="Unknown light node 'dup'"):
        model.light("dup")
    for key in (True, 4.0, None):
        with pytest.raises(TypeError):
            model.camera(key)
    # Scene-created lights and cameras have no node.
    assert scene.create_camera().node is None
    assert scene.add_point_light(position=(0, 0, 1)).node is None
    # Position-based name lists were replaced by the handle lists.
    assert not hasattr(filly.Model, "light_names") and not hasattr(filly.Model, "camera_names")
