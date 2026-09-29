"""Projections that follow the render target's aspect ratio."""

import numpy as np
import pytest

import filly
from test_features import pack, unpack

pytestmark = pytest.mark.gpu


def render(renderer, scene, width, height):
    target = renderer.create_render_target(width=width, height=height)
    renderer.render(scene, target)
    image = target.read()
    target.close()
    return image


@pytest.mark.parametrize("kind", ["perspective", "lens"])
def test_perspective_follows_target_aspect(renderer, scene, kind):
    camera = scene.camera
    if kind == "perspective":
        camera.set_perspective(fov_y=45, near=0.1, far=10)
    else:
        camera.set_lens_projection(focal_length_mm=28, near=0.1, far=10)
    # Before any render the fitted projection uses aspect 1.
    first = camera.projection
    assert first[0, 0] == pytest.approx(first[1, 1])
    render(renderer, scene, 64, 32)
    wide = camera.projection
    assert wide[0, 0] == pytest.approx(wide[1, 1] / 2)
    render(renderer, scene, 16, 48)
    tall = camera.projection
    assert tall[0, 0] == pytest.approx(tall[1, 1] * 3)
    # An explicit aspect wins over the target.
    if kind == "perspective":
        camera.set_perspective(fov_y=45, aspect=1, near=0.1, far=10)
    else:
        camera.set_lens_projection(focal_length_mm=28, aspect=1, near=0.1, far=10)
    render(renderer, scene, 64, 32)
    fixed = camera.projection
    assert fixed[0, 0] == pytest.approx(fixed[1, 1])


def test_orthographic_height_follows_target_aspect(renderer, scene, triangle_glb):
    camera = scene.camera
    camera.set_orthographic(height=2, center=(0.5, -0.25), near=0.1, far=10)
    projection = camera.projection
    np.testing.assert_allclose([projection[0, 0], projection[1, 1]], [1, 1])
    render(renderer, scene, 64, 32)
    projection = camera.projection
    # Width 4 around x = 0.5; height 2 around y = -0.25.
    np.testing.assert_allclose([projection[0, 0], projection[0, 3], projection[1, 1], projection[1, 3]],
                               [0.5, -0.25, 1, 0.25], atol=1e-6)


def test_fitted_projection_does_not_distort(renderer, scene, triangle_glb):
    scene.load(triangle_glb)
    scene.camera.set_orthographic(height=2, near=0.1, far=10)
    square = render(renderer, scene, 64, 64)[..., 0] > 128
    wide = render(renderer, scene, 128, 64)[..., 0] > 128
    # Same pixels per scene unit, so the triangle keeps its size and shape.
    np.testing.assert_array_equal(wide[:, 32:96], square)
    assert not wide[:, :32].any() and not wide[:, 96:].any()


@pytest.mark.parametrize("arguments,message", [
    ({"left": -1, "right": 1, "bottom": -1, "top": 1, "height": 2}, "not both"),
    ({"left": -1, "right": 1, "bottom": -1}, "all of left"),
    ({"center": (0, 0)}, "center requires height"),
    ({"height": 0}, "height > 0"),
    ({}, "all of left"),
])
def test_orthographic_arguments(scene, arguments, message):
    with pytest.raises(ValueError, match=message):
        scene.camera.set_orthographic(near=0.1, far=10, **arguments)


@pytest.mark.parametrize("aspect", [0, -1, float("nan")])
def test_explicit_aspect_must_be_positive(scene, aspect):
    with pytest.raises(ValueError, match="aspect"):
        scene.camera.set_perspective(fov_y=45, aspect=aspect, near=0.1, far=10)


@pytest.mark.parametrize("authored", [None, 1.0])
def test_imported_camera_without_aspect_follows_target(renderer, triangle_glb, authored):
    doc, binary = unpack(triangle_glb)
    perspective = {"yfov": 0.8, "znear": 0.1, "zfar": 10}
    if authored:
        perspective["aspectRatio"] = authored
    doc["cameras"] = [{"type": "perspective", "perspective": perspective}]
    doc["nodes"].append({"name": "eye", "camera": 0, "translation": [0, 0, 3]})
    doc["scenes"][0]["nodes"].append(1)
    scene = renderer.create_scene()
    model = scene.load(pack(doc, binary))
    camera = model.camera("eye")
    scene.camera = camera
    render(renderer, scene, 64, 32)
    projection = camera.projection
    expected = 1.0 if authored else 0.5
    assert projection[0, 0] == pytest.approx(projection[1, 1] * expected)
    with pytest.raises(filly.FillyError, match="glTF projection"):
        camera.set_perspective(fov_y=45, near=0.1, far=10)
