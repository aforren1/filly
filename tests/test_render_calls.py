"""Per-call camera, viewport, and clear: two eyes side by side in one target."""

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.gpu

WIDTH, HEIGHT = 64, 32
BACKGROUND = (0.2, 0.2, 0.2, 1)


def srgb(value):
    value = np.asarray(value, dtype=float)
    return np.round(255 * np.where(value <= 0.0031308, 12.92 * value, 1.055 * value ** (1 / 2.4) - 0.055))


GREY = srgb(0.2)


def stereo_scene(renderer, path):
    scene = renderer.create_scene()
    scene.background = BACKGROUND
    if path == "direct":
        scene.output_path = "direct"
    else:
        scene.msaa = 4
    # A unit square at the origin.
    scene.create_mesh(**filly.shapes.plane(0.5, 0.5), unlit=True, base_color=(1, 0, 0, 1))
    eyes = []
    for x in (-0.25, 0.25):
        camera = scene.create_camera()
        # Height 2 and the half-target viewport's aspect of 1: each eye sees [-1, 1] around x.
        camera.set_orthographic(height=2, center=(0, 0), near=0.1, far=10)
        camera.position = (x, 0, 3)
        camera.look_at((x, 0, 0))
        eyes.append(camera)
    return scene, eyes


def square_columns(image, row=HEIGHT // 2):
    return np.flatnonzero(image[row, :, 0] == 255)


@pytest.mark.parametrize("path", ["direct", "exact"])
def test_two_eyes_land_in_their_halves(renderer, path):
    scene, (left, right) = stereo_scene(renderer, path)
    target = renderer.create_render_target(width=WIDTH, height=HEIGHT)
    renderer.render(scene, target, camera=left, viewport=(0, 0, 32, 32))
    renderer.render(scene, target, camera=right, viewport=(32, 0, 32, 32), clear=False)
    image = target.read().astype(int)
    # Each eye maps 2 units to 32 pixels. The square spans [-0.25, 0.25] in the world, so it
    # is 8 pixels wide, centered 0.25 units right of the left eye and left of the right eye.
    np.testing.assert_array_equal(square_columns(image), np.r_[16 + 4 - 4:16 + 4 + 4, 48 - 4 - 4:48 - 4 + 4])
    np.testing.assert_array_equal(image[16, 16 + 4], [255, 0, 0, 255])
    for column in (2, 30, 34, 62):
        np.testing.assert_allclose(image[16, column], [GREY, GREY, GREY, 255], atol=1)
    target.close()


@pytest.mark.parametrize("path", ["direct", "exact"])
def test_clear_false_keeps_the_rest_and_fills_the_viewport(renderer, path):
    scene, (left, right) = stereo_scene(renderer, path)
    target = renderer.create_render_target(width=WIDTH, height=HEIGHT)
    near, far = scene.create_camera(), scene.create_camera()
    # The near camera sees 0.2 units vertically, inside the square; the far one misses it.
    for camera, x in ((near, 0), (far, 10)):
        camera.set_orthographic(height=0.2, near=0.1, far=10)
        camera.position = (x, 0, 3)
        camera.look_at((x, 0, 0))
    renderer.render(scene, target, camera=near)
    assert np.all(target.read()[..., 0] == 255)
    renderer.render(scene, target, camera=far, viewport=(32, 0, 32, 32), clear=False)
    image = target.read().astype(int)
    # The left half keeps the previous frame; the right half is background, not that frame.
    np.testing.assert_array_equal(image[:, :32], np.broadcast_to([255, 0, 0, 255], (HEIGHT, 32, 4)))
    np.testing.assert_allclose(image[:, 32:, :3], GREY, atol=1)
    assert np.all(image[..., 3] == 255)
    # Clearing first and drawing the other half without a clear gives the same frame.
    renderer.render(scene, target, camera=far)
    renderer.render(scene, target, camera=near, viewport=(0, 0, 32, 32), clear=False)
    np.testing.assert_array_equal(target.read().astype(int), image)
    target.close()


def test_camera_override_is_for_one_call(renderer, scene):
    shape = filly.shapes.plane(0.5, 0.5)
    scene.create_mesh(**shape, unlit=True, alpha_mode="blend")
    other = scene.create_camera()
    other.set_orthographic(height=2, near=0.1, far=10)
    other.position = (5, 0, 3)
    other.look_at((5, 0, 0))
    target = renderer.create_render_target(width=32, height=32)
    renderer.render(scene, target, camera=other)
    assert target.read()[16, 16, 0] == 0
    assert scene.camera != other
    renderer.render(scene, target)
    assert target.read()[16, 16, 0] == 255
    # A scene without a camera renders with an explicit one.
    empty = renderer.create_scene()
    with pytest.raises(filly.FillyError, match="camera"):
        renderer.render(empty, target)
    renderer.render(empty, target, camera=other)
    with filly.Renderer() as foreign:
        foreign_camera = foreign.create_scene().create_camera()
        with pytest.raises(ValueError, match="this renderer"):
            renderer.render(scene, target, camera=foreign_camera)
    target.close()


def test_projection_follows_the_viewport_aspect(renderer, scene):
    camera = scene.create_camera()
    camera.set_perspective(fov_y=60, near=0.1, far=10)
    target = renderer.create_render_target(width=WIDTH, height=HEIGHT)
    renderer.render(scene, target, camera=camera, viewport=(0, 0, 16, 32))
    projection = camera.projection
    assert projection[1, 1] / projection[0, 0] == pytest.approx(16 / 32)
    renderer.render(scene, target, camera=camera)
    assert camera.projection[1, 1] / camera.projection[0, 0] == pytest.approx(WIDTH / HEIGHT)
    target.close()


@pytest.mark.parametrize("viewport", [(0, 0, 65, 32), (-1, 0, 8, 8), (0, 0, 0, 8), (60, 30, 8, 8)])
def test_viewport_must_fit(renderer, scene, viewport):
    target = renderer.create_render_target(width=WIDTH, height=HEIGHT)
    with pytest.raises(ValueError, match="viewport"):
        renderer.render(scene, target, viewport=viewport)
    with pytest.raises(ValueError, match="viewport"):
        renderer.render(scene, target, viewport=(0, 0, 8))
    with pytest.raises(ValueError):
        renderer.render(scene, target, viewport=(0.5, 0, 8, 8))
    target.close()
