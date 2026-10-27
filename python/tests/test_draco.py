"""KHR_draco_mesh_compression comes from Filament's decoder; these tests catch an SDK without it."""

from pathlib import Path

import numpy as np

import filly

DATA = Path(__file__).parent / "data"
DRACO_BOX = DATA / "draco-box" / "Box.gltf"
PLAIN_BOX = DATA / "box" / "Box.gltf"


def render_box(renderer, path, width=160, height=120):
    """The lit scene that web/tests/api.html renders as "draco"."""
    scene = renderer.create_scene()
    scene.background = (0.1, 0.2, 0.3, 1)
    scene.load(path)
    camera = renderer.create_camera()
    camera.set_perspective(fov_y_deg=35, near=0.5, far=20)
    camera.position = (1.5, 1.2, 2.5)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    scene.add_directional_light(direction=(-1, -2, -1), intensity=80000)
    scene.environment = renderer.create_environment(np.ones((8, 16, 3), np.float32))
    scene.environment_intensity = 15000
    target = renderer.create_render_target(width=width, height=height)
    renderer.render(scene, target)
    pixels = target.read().astype(int)
    target.close()
    scene.close()
    return pixels


def test_draco_box_matches_uncompressed(renderer):
    scene = renderer.create_scene()
    draco, plain = scene.load(DRACO_BOX), scene.load(PLAIN_BOX)
    # Draco quantizes positions: Box's corners move by 0.0005.
    np.testing.assert_allclose(draco.bounds, plain.bounds, atol=1e-3)
    scene.close()
    compressed, uncompressed = render_box(renderer, DRACO_BOX), render_box(renderer, PLAIN_BOX)
    difference = np.abs(compressed - uncompressed).max(axis=2)
    background = (np.abs(uncompressed[..., :3] - uncompressed[0, 0, :3]).max(axis=2) > 8).mean()
    assert background > 0.1, "the box does not show"
    # Quantized corners move silhouette pixels only.
    assert (difference > 2).mean() < 0.01
