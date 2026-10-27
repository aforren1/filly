"""Fog, depth of field, and vignette: off by default, analytic where possible, no frame history."""

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.gpu

SIZE = 64


def srgb(value):
    value = np.asarray(value, dtype=float)
    return np.round(255 * np.where(value <= 0.0031308, 12.92 * value, 1.055 * value ** (1 / 2.4) - 0.055))


def render(renderer, scene, **options):
    target = renderer.create_render_target(width=SIZE, height=SIZE)
    renderer.render(scene, target, **options)
    image = target.read().astype(int)
    target.close()
    return image


def unlit_plane(scene, width, height, position, color=(1, 1, 1, 1), alpha_mode="opaque"):
    model = scene.create_mesh(**filly.shapes.plane(width, height), unlit=True, alpha_mode=alpha_mode, base_color=color)
    model.position = position
    return model


def test_effects_are_off_by_default(renderer, scene):
    assert (scene.fog, scene.depth_of_field, scene.vignette) == (False, False, False)
    assert scene.output_path == "exact"
    camera = renderer.create_camera()
    assert camera.aperture == 16


@pytest.mark.parametrize("path", ["direct", "exact"])
def test_fog_follows_beer_lambert(renderer, scene, path):
    if path == "direct":
        scene.output_path = "direct"
    else:
        scene.msaa = 4
    # The orthographic camera at z = 3 sees [-1, 1]; the left half is 3 units away, the right 7.
    unlit_plane(scene, 1, 2, (-0.5, 0, 0))
    unlit_plane(scene, 1, 2, (0.5, 0, -4))
    density, fog = 0.3, 0.25
    scene.fog = True
    scene.fog_color = (fog, fog, fog)
    scene.fog_density = density
    image = render(renderer, scene)
    for column, depth in ((16, 3), (48, 7)):
        for row in (8, 32, 56):
            # Fog uses the distance from the camera, not the depth.
            x, y = (column + 0.5) / 32 - 1, 1 - (row + 0.5) / 32
            transmittance = np.exp(-density * np.sqrt(x * x + y * y + depth * depth))
            expected = srgb(transmittance + fog * (1 - transmittance))
            np.testing.assert_allclose(image[row, column, :3], expected, atol=1)
    # Far geometry moves further toward the fog color than near geometry.
    assert image[32, 48, 0] < image[32, 16, 0]
    scene.fog = False
    np.testing.assert_array_equal(render(renderer, scene)[32, [16, 48], :3], 255)


def test_fog_color_is_the_output_color(renderer, scene):
    """Filament scales fog by environment intensity and exposure; the wrapper divides that out."""
    unlit_plane(scene, 2, 2, (0, 0, 0))
    scene.fog = True
    scene.fog_color = (0.5, 0.2, 0.0)
    scene.fog_density = 50
    expected = [*srgb([0.5, 0.2, 0.0]), 255]
    np.testing.assert_allclose(render(renderer, scene)[32, 32], expected, atol=1)
    scene.camera.exposure = 9
    scene.environment = renderer.create_environment(np.ones((8, 16, 3), np.float32))
    scene.environment_intensity = 5000
    np.testing.assert_allclose(render(renderer, scene)[32, 32], expected, atol=1)
    other = renderer.create_camera()
    other.set_orthographic(height=2, near=0.1, far=10)
    other.position = (0, 0, 3)
    other.look_at((0, 0, 0))
    other.exposure = 3
    np.testing.assert_allclose(render(renderer, scene, camera=other)[32, 32], expected, atol=1)
    with pytest.raises(ValueError):
        scene.fog_density = -1
    # A rejected value leaves the others as they were.
    assert scene.fog_density == 50
    np.testing.assert_allclose(scene.fog_color, (0.5, 0.2, 0.0))
    # Fog starts beyond the plane, 3 units away. The color and density stay.
    scene.fog_start = 5
    np.testing.assert_array_equal(render(renderer, scene)[32, 32], [255, 255, 255, 255])
    scene.fog_start = 0
    np.testing.assert_allclose(render(renderer, scene)[32, 32], expected, atol=1)


def test_effect_parameters_read_back_and_survive_toggling(scene):
    # Filament's defaults, readable before any change.
    assert (scene.fog, scene.vignette) == (False, False)
    np.testing.assert_allclose(scene.fog_color, (1, 1, 1))
    assert scene.fog_density == pytest.approx(0.1) and scene.fog_start == 0
    assert (scene.vignette_midpoint, scene.vignette_roundness, scene.vignette_feather) == (0.5, 0.5, 0.5)
    np.testing.assert_allclose(scene.vignette_color, (0, 0, 0))
    scene.fog_density = 0.2
    np.testing.assert_allclose(scene.fog_color, (1, 1, 1))
    scene.vignette_color = (1, 0, 0)
    scene.vignette_roundness = 1
    scene.vignette = True
    scene.vignette = False
    scene.fog = True
    scene.fog = False
    assert scene.fog_density == pytest.approx(0.2)
    np.testing.assert_allclose(scene.vignette_color, (1, 0, 0))
    assert (scene.vignette_midpoint, scene.vignette_roundness, scene.vignette_feather) == (0.5, 1, 0.5)
    for name, value in (("fog_color", (2, 0, 0)), ("fog_start", -1), ("vignette_midpoint", 1.5),
                        ("vignette_roundness", -0.1), ("vignette_feather", 0.01), ("vignette_color", (0, 0, -1))):
        with pytest.raises(ValueError):
            setattr(scene, name, value)


def edge_widths(image):
    """Blurred pixels across the vertical edge, in the upper and lower halves."""
    widths = []
    for row in (SIZE // 4, 3 * SIZE // 4):
        values = image[row, :, 0]
        widths.append(int(np.count_nonzero((values > 10) & (values < 245))))
    return widths


def depth_scene(renderer):
    scene = renderer.create_scene()
    camera = renderer.create_camera()
    # A long lens gives the circle of confusion a measurable size: f = 0.137 m at 10 degrees.
    camera.set_perspective(fov_y_deg=10, near=0.5, far=50)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    camera.aperture = 1
    scene.camera = camera
    # Upper half of the view: an edge 3 m away. Lower half: an edge 9 m away. Depth of field
    # reads depth, which only opaque materials write.
    unlit_plane(scene, 5, 5, (-2.5, 2.5, 0), alpha_mode="opaque")
    unlit_plane(scene, 5, 5, (-2.5, -2.5, -6), alpha_mode="opaque")
    return scene, camera


def test_depth_of_field_blurs_out_of_focus_edges(renderer):
    scene, camera = depth_scene(renderer)
    sharp = render(renderer, scene)
    assert edge_widths(sharp) == [0, 0]
    scene.depth_of_field = True
    camera.focus_distance = 3
    near_focus = render(renderer, scene)
    camera.focus_distance = 9
    far_focus = render(renderer, scene)
    # Circle of confusion A f / (S - f) * |1 - S / d| on a 24 mm sensor: at S = 3 m the far edge
    # has about 12 pixels, at S = 9 m the near edge about 35 before Filament's clamp.
    near, far = edge_widths(near_focus)
    assert near <= 2 and far >= 4, (near, far)
    near, far = edge_widths(far_focus)
    assert far <= 2 and near >= 4, (near, far)
    # A smaller aperture (larger f-number) blurs less without changing exposure.
    camera.aperture = 2
    near, _ = edge_widths(render(renderer, scene))
    assert 0 < near < edge_widths(far_focus)[0]
    # Filament's depth-of-field passes store color as R11G11B10F, so flat areas can move 2 levels.
    np.testing.assert_allclose(render(renderer, scene)[:, :SIZE // 4], sharp[:, :SIZE // 4], atol=2)
    with pytest.raises(ValueError):
        camera.aperture = 0.1
    with pytest.raises(ValueError):
        camera.focus_distance = -1


def test_focus_distance_default_reads_back(renderer):
    camera = renderer.create_camera()
    # Filament focuses no closer than the near plane, so 0 focuses there.
    assert camera.focus_distance == 0
    camera.focus_distance = 2
    camera.focus_distance = 0
    assert camera.focus_distance == 0


def test_vignette_darkens_the_corners(renderer, scene):
    unlit_plane(scene, 2, 2, (0, 0, 0), color=(0.5, 0.5, 0.5, 1))
    plain = render(renderer, scene)
    scene.vignette = True
    scene.vignette_roundness = 1.0
    image = render(renderer, scene)
    # The center keeps the encoded 0.5 grey; brightness falls along the diagonal to the corner.
    np.testing.assert_allclose(image[32, 32], plain[32, 32], atol=1)
    np.testing.assert_allclose(plain[32, 32, :3], srgb(0.5), atol=1)
    diagonal = image[np.arange(32, 64, 4), np.arange(32, 64, 4), 0]
    assert np.all(np.diff(diagonal) <= 0) and diagonal[-1] < diagonal[0] - 50
    scene.vignette_color = (1, 0, 0)
    corner = render(renderer, scene)[63, 63]
    assert corner[0] > corner[1] + 50
    with pytest.raises(ValueError):
        scene.vignette_feather = 0
    scene.vignette = False
    np.testing.assert_array_equal(render(renderer, scene), plain)


def test_effects_have_no_frame_history(renderer):
    scene, camera = depth_scene(renderer)
    camera.focus_distance = 3
    scene.depth_of_field = True
    scene.vignette = True
    scene.fog = True
    scene.fog_color = (0.3, 0.3, 0.3)
    scene.fog_density = 0.05
    target = renderer.create_render_target(width=SIZE, height=SIZE)
    renderer.render(scene, target)
    first = target.read()
    # Different frames in between must not leak into a repeat of the first.
    camera.position = (0.3, 0.1, 3)
    for _ in range(3):
        renderer.render(scene, target)
    camera.position = (0, 0, 3)
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read(), first)
    target.close()
