"""Batch calls: one call for many node transforms, material parameters, or morph weights."""

import struct

import numpy as np
import pytest

import filly
from test_asset import render
from test_features import pack, unpack


def boxes(scene, count):
    shape = filly.shapes.box(width=0.2, height=0.2, depth=0.2)
    return [scene.create_mesh(**shape) for _ in range(count)]


def translations(count):
    matrices = np.tile(np.eye(4), (count, 1, 1))
    matrices[:, 0, 3] = np.linspace(-0.6, 0.6, count)
    matrices[:, 1, 3] = np.linspace(0.5, -0.5, count)
    return matrices


def test_set_transforms_matches_single_updates(renderer, scene):
    scene.add_directional_light(direction=(0.3, -0.5, -1))
    models = boxes(scene, 4)
    matrices = translations(4)
    filly.set_transforms([m.nodes[0] for m in models], matrices)
    for model, matrix in zip(models, matrices):
        np.testing.assert_allclose(model.nodes[0].transform, matrix, atol=1e-6)
    batched = render(renderer, scene)
    for model in models:
        model.nodes[0].transform = np.eye(4)
    for model, matrix in zip(models, matrices):
        model.nodes[0].transform = matrix
    np.testing.assert_array_equal(render(renderer, scene), batched)


def test_set_transforms_takes_any_float_layout(scene):
    models = boxes(scene, 3)
    nodes = [m.nodes[0] for m in models]
    matrices = translations(3)
    # Column-major (Fortran-order) float32, the layout of a MATLAB array.
    filly.set_transforms(nodes, np.asfortranarray(matrices.astype(np.float32)))
    np.testing.assert_allclose(nodes[2].transform, matrices[2], atol=1e-6)
    # Nested lists convert to float64.
    filly.set_transforms(nodes[:1], [np.eye(4).tolist()])
    np.testing.assert_allclose(nodes[0].transform, np.eye(4))
    filly.set_transforms([], np.zeros((0, 4, 4)))


def test_set_transforms_is_atomic(renderer, scene):
    models = boxes(scene, 3)
    nodes = [m.nodes[0] for m in models]
    matrices = translations(3)
    filly.set_transforms(nodes, matrices)
    bad = translations(3) * 0
    bad[:, 3, 3] = 1
    bad[2, 0, 0] = np.nan
    with pytest.raises(ValueError):
        filly.set_transforms(nodes, bad)
    for node, matrix in zip(nodes, matrices):
        np.testing.assert_allclose(node.transform, matrix, atol=1e-6)
    with pytest.raises(ValueError, match="shape"):
        filly.set_transforms(nodes, matrices[:2])
    with pytest.raises(ValueError, match="dimensions"):
        filly.set_transforms(nodes, matrices.reshape(3, 16))
    # Scenes do not matter, only the renderer: its engine applies the whole batch.
    neighbor = boxes(renderer.create_scene(), 1)[0].nodes[0]
    filly.set_transforms([nodes[0], neighbor], matrices[:2])
    np.testing.assert_allclose(neighbor.transform, matrices[1], atol=1e-6)
    with filly.Renderer() as other:
        stranger = boxes(other.create_scene(), 1)[0].nodes[0]
        with pytest.raises(ValueError, match="another renderer"):
            filly.set_transforms([nodes[0], stranger], matrices[:2])
    models[0].close()
    with pytest.raises(Exception, match="closed"):
        filly.set_transforms(nodes, matrices)


def test_set_material_parameters(scene):
    materials = [m.nodes[0].material() for m in boxes(scene, 3)]
    colors = np.array([[1, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 0.5]])
    filly.set_material_parameters(materials, "base_color", colors)
    for material, color in zip(materials, colors):
        np.testing.assert_allclose(material.base_color, color, atol=1e-6)
    filly.set_material_parameters(materials, "roughness", [0.1, 0.2, 0.3])
    assert materials[2].roughness == pytest.approx(0.3)
    filly.set_material_parameters(materials, "metallic", np.array([[1], [0], [1]]))
    assert materials[0].metallic == pytest.approx(1)
    filly.set_material_parameters(materials, "emissive", np.full((3, 3), 2.0))
    np.testing.assert_allclose(materials[1].emissive, [2, 2, 2])
    bad = colors.copy()
    bad[2, 0] = 1.5
    with pytest.raises(ValueError):
        filly.set_material_parameters(materials, "base_color", bad)
    np.testing.assert_allclose(materials[2].base_color, colors[2], atol=1e-6)
    with pytest.raises(ValueError, match="shape"):
        filly.set_material_parameters(materials, "base_color", colors[:, :3])
    with pytest.raises(ValueError, match="Material parameter"):
        filly.set_material_parameters(materials, "shininess", colors)


def morph_glb(triangle_glb):
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    binary += struct.pack("<9f", 0, 0.5, 0, 0, 0.5, 0, 0, 0.5, 0)
    doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 36})
    doc["accessors"].append({"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3",
                             "min": [0, 0.5, 0], "max": [0, 0.5, 0]})
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 1}]
    return pack(doc, binary)


def test_set_morph_weights_matches_single_updates(renderer, scene, triangle_glb):
    asset = renderer.load_asset(morph_glb(triangle_glb))
    models = [scene.add(asset), scene.add(asset)]
    models[0].position = (-0.5, 0, 0)
    models[1].position = (0.5, 0, 0)
    nodes = [m.node("triangle") for m in models]
    rest = render(renderer, scene)
    filly.set_morph_weights(nodes, [[1], [0.5]])
    batched = render(renderer, scene)
    assert not np.array_equal(batched, rest)
    nodes[0].set_morph_weights([1])
    nodes[1].set_morph_weights([0.5])
    np.testing.assert_array_equal(render(renderer, scene), batched)
    with pytest.raises(ValueError, match="columns"):
        filly.set_morph_weights(nodes, [[1, 1], [1, 1]])
    with pytest.raises(ValueError, match="no morph targets"):
        filly.set_morph_weights([nodes[0], boxes(scene, 1)[0].nodes[0]], [[1], [1]])
