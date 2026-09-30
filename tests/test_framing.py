"""Node.bounds and Camera.frame(): framing a model, a node, or a box."""

import json
import struct

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.gpu

OFFSET = np.array([3.0, -2.0, 5.0])


def unit_sphere(scene):
    """A white unlit sphere of radius 1 at OFFSET, finely tessellated so its silhouette is round."""
    shape = filly.shapes.uv_sphere(radius=1, segments=256, rings=128)
    shape["positions"] = shape["positions"] + OFFSET.astype(np.float32)
    return scene.create_mesh(**shape, unlit=True, base_color=(1, 1, 1, 1))


def extents(image):
    """Lit pixels along the center row and the center column."""
    lit = image[..., 0] > 128
    height, width = lit.shape
    return int(lit[height // 2].sum()), int(lit[:, width // 2].sum())


def render(renderer, scene, width, height):
    target = renderer.create_render_target(width=width, height=height)
    renderer.render(scene, target)
    image = target.read()
    target.close()
    return image


@pytest.mark.parametrize("projection", ["perspective", "orthographic"])
@pytest.mark.parametrize("size", [(160, 96), (96, 160)])
def test_sphere_fill_covers_the_expected_pixels(renderer, projection, size):
    """A box whose circumscribed sphere is the unit sphere: with fill 0.5 the rendered sphere
    spans half of the narrower image axis, in both directions."""
    width, height = size
    scene = renderer.create_scene()
    camera = scene.create_camera()
    if projection == "perspective":
        camera.set_perspective(fov_y=40, near=0.1, far=100)
    else:
        camera.set_orthographic(height=2, near=0.1, far=100)
    scene.camera = camera
    unit_sphere(scene)
    half = 1 / np.sqrt(3)
    box = [OFFSET - half, OFFSET + half]
    direction = np.array([0.3, -0.2, -1.0])
    distance = camera.frame(box, fill=0.5, direction=direction, aspect=width / height)
    np.testing.assert_allclose(camera.position, OFFSET - distance * direction / np.linalg.norm(direction), atol=1e-4)
    across, down = extents(render(renderer, scene, width, height))
    expected = 0.5 * min(width, height)
    assert abs(across - expected) <= 1 and abs(down - expected) <= 1, (across, down, expected)
    if projection == "perspective":
        # The documented distance: tan(asin(r / d)) = fill * tan(half the narrower field of view).
        tan_half = np.tan(np.radians(20)) * min(1, width / height)
        np.testing.assert_allclose(distance, np.sqrt(1 + 1 / (0.5 * tan_half) ** 2), rtol=1e-6)


def test_sphere_framing_ignores_rotation(renderer):
    scene = renderer.create_scene()
    camera = scene.create_camera()
    camera.set_perspective(fov_y=35, near=0.1, far=100)
    scene.camera = camera
    shape = filly.shapes.box(2, 1, 0.5)
    shape["positions"] = shape["positions"] + np.float32([1, 2, 3])
    model = scene.create_mesh(**shape)
    poses = []
    for rotation in [(0, 0, 0), (30, 45, 10), (0, 90, 0)]:
        model.rotation_euler_deg = rotation
        center = model.transform[:3, :3] @ [1, 2, 3]
        distance = camera.frame(model, direction=(0, 0, -1))
        np.testing.assert_allclose(camera.position, center + [0, 0, distance], atol=1e-4)
        poses.append((distance, np.asarray(camera.projection)))
    for distance, projection in poses[1:]:
        assert distance == pytest.approx(poses[0][0], rel=1e-6)
        np.testing.assert_allclose(projection, poses[0][1], rtol=1e-5)
    # A tight box fit follows the rotated shape instead.
    model.rotation_euler_deg = (0, 0, 0)
    flat = camera.frame(model, direction=(0, 0, -1), fit="box")
    model.rotation_euler_deg = (0, 90, 0)
    assert camera.frame(model, direction=(0, 0, -1), fit="box") != pytest.approx(flat)


def test_box_fit_touches_fill(renderer):
    """fit='box': the farthest projected corner reaches fill of the half-extent."""
    scene = renderer.create_scene()
    camera = scene.create_camera()
    camera.set_perspective(fov_y=30, near=0.1, far=100)
    scene.camera = camera
    shape = filly.shapes.plane(4, 1)
    model = scene.create_mesh(**shape, unlit=True, base_color=(1, 1, 1, 1))
    camera.frame(model, fill=0.8, direction=(0, 0, -1), fit="box", aspect=2)
    image = render(renderer, scene, 200, 100)
    across, down = extents(image)
    # The plane is 4 x 1 and the view is 2:1, so its width limits: 80% of 200 pixels.
    assert abs(across - 160) <= 1, across
    assert abs(down - 40) <= 1, down


def two_node_glb():
    """Two unit quads: node "left" around (-5, 0, 0) and node "right", a child of "hub", around
    (5, 0, 0). "hub" and "marker" have no mesh; "marker" has no children either."""
    positions = np.float32([[-0.5, -0.5, 0], [0.5, -0.5, 0], [0.5, 0.5, 0], [-0.5, 0.5, 0]])
    indices = np.uint16([0, 1, 2, 0, 2, 3])
    binary = positions.tobytes() + indices.tobytes()
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 1, 3]}],
        "extensionsUsed": ["KHR_materials_unlit"],
        "nodes": [{"name": "left", "mesh": 0, "translation": [-5, 0, 0]},
                  {"name": "hub", "children": [2], "translation": [4, 0, 0]},
                  {"name": "right", "mesh": 0, "translation": [1, 0, 0]},
                  {"name": "marker", "translation": [0, 7, 0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"extensions": {"KHR_materials_unlit": {}}, "doubleSided": True}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": positions.nbytes},
                        {"buffer": 0, "byteOffset": positions.nbytes, "byteLength": indices.nbytes}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3",
                       "min": [-0.5, -0.5, 0], "max": [0.5, 0.5, 0]},
                      {"bufferView": 1, "componentType": 5123, "count": 6, "type": "SCALAR"}],
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def test_node_bounds_cover_the_subtree(scene):
    model = scene.load(two_node_glb())
    np.testing.assert_allclose(model.node("left").bounds, [[-5.5, -0.5, 0], [-4.5, 0.5, 0]])
    np.testing.assert_allclose(model.node("right").bounds, [[4.5, -0.5, 0], [5.5, 0.5, 0]])
    # A node without a mesh covers its descendants; the root covers the model.
    np.testing.assert_allclose(model.node("hub").bounds, model.node("right").bounds)
    np.testing.assert_allclose(model.bounds, [[-5.5, -0.5, 0], [5.5, 0.5, 0]])
    # Rest-pose bounds, like Model.bounds: node edits do not change them.
    model.node("hub").position = (0, 3, 0)
    np.testing.assert_allclose(model.node("right").bounds, [[4.5, -0.5, 0], [5.5, 0.5, 0]])


def test_frame_node_not_model(renderer, scene):
    model = scene.load(two_node_glb())
    model.position = (0, 1, 0)
    camera = scene.camera
    camera.set_perspective(fov_y=30, near=0.1, far=100)
    camera.frame(model.node("right"), fill=0.9, direction=(0, 0, -1))
    # The model transform applies: the node's center is (5, 1, 0).
    np.testing.assert_allclose(camera.position[:2], [5, 1], atol=1e-5)
    image = render(renderer, scene, 64, 64)
    lit = image[..., 0] > 128
    assert lit[32, 32] and lit.sum() > 0.3 * lit.size
    # The left quad is far outside this view.
    camera.frame(model, fill=0.9, direction=(0, 0, -1))
    np.testing.assert_allclose(camera.position[:2], [0, 1], atol=1e-5)


def test_frame_sets_enclosing_clip_planes(scene):
    unit_sphere(scene)
    camera = scene.camera
    camera.set_perspective(fov_y=45, near=0.1, far=1000)
    box = [OFFSET - 1, OFFSET + 1]
    distance = camera.frame(box, direction=(0, -1, -1))
    # Filament renders with the far plane at infinity, so the projection shows only near.
    projection = np.asarray(camera.projection)
    near = projection[2, 3] / (projection[2, 2] - 1)
    radius = np.sqrt(3)
    assert 0 < near < distance - radius, (near, distance)
    camera.frame(box, direction=(0, -1, -1), near=0.5, far=50)
    projection = np.asarray(camera.projection)
    assert projection[2, 3] / (projection[2, 2] - 1) == pytest.approx(0.5, rel=1e-4)


def test_frame_keeps_the_field_of_view(scene):
    camera = scene.camera
    camera.set_perspective(fov_y=50, near=0.1, far=100)
    before = np.asarray(camera.projection)[1, 1]
    camera.frame([[0, 0, 0], [1, 1, 1]], direction=(1, 0, 0))
    assert np.asarray(camera.projection)[1, 1] == pytest.approx(before)


def test_frame_uses_the_current_direction(scene):
    camera = scene.camera
    camera.set_perspective(fov_y=50, near=0.1, far=100)
    camera.position = (0, 0, 0)
    camera.look_at((1, 0, 0))
    distance = camera.frame([[9, -1, -1], [11, 1, 1]])
    np.testing.assert_allclose(camera.position, [10 - distance, 0, 0], atol=1e-5)


@pytest.mark.parametrize("kwargs,error", [
    (dict(fill=0), "fill"), (dict(fill=1.5), "fill"), (dict(fill=float("nan")), "fill"),
    (dict(direction=(0, 0, 0)), "direction"), (dict(direction=(0, 1, 0)), "parallel"),
    (dict(up=(0, 0, 0)), "up"), (dict(fit="tight"), "fit"), (dict(near=-1), "near"),
    (dict(near=5, far=1), "near"), (dict(aspect=0), "aspect"),
])
def test_frame_rejects_invalid_input(scene, kwargs, error):
    kwargs.setdefault("direction", (0, 0, -1))
    with pytest.raises(ValueError, match=error):
        scene.camera.frame([[0, 0, 0], [1, 1, 1]], **kwargs)


def test_frame_rejects_empty_targets(scene):
    camera = scene.camera
    for box in ([[1, 0, 0], [0, 1, 1]], [[0, 0, 0], [0, 0, 0]], [[0, 0, float("nan")], [1, 1, 1]]):
        with pytest.raises(ValueError):
            camera.frame(box)
    model = scene.load(two_node_glb())
    # A node without a mesh in its subtree has empty bounds: each minimum above its maximum.
    marker = model.node("marker")
    assert np.all(np.asarray(marker.bounds)[0] > np.asarray(marker.bounds)[1])
    with pytest.raises(ValueError, match="empty"):
        camera.frame(marker)
    with pytest.raises(TypeError, match="target"):
        camera.frame("model")


def test_frame_rejects_imported_cameras(renderer, scene, triangle_glb):
    size = struct.unpack_from("<I", triangle_glb, 12)[0]
    document = json.loads(triangle_glb[20:20 + size])
    document["cameras"] = [{"type": "perspective", "perspective": {"yfov": 0.8, "znear": 0.1}}]
    document["nodes"].append({"camera": 0, "translation": [0, 0, 3]})
    document["scenes"][0]["nodes"].append(1)
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary = triangle_glb[20 + size:]
    glb = struct.pack("<III", 0x46546C67, 2, 20 + len(encoded) + len(binary)) \
        + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + binary
    model = scene.load(glb)
    with pytest.raises(filly.FillyError, match="frame"):
        model.cameras[0].frame(model)
