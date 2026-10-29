"""Meshes from arrays and the shape helpers."""

import json
import struct

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.gpu

SIZE = 32


def srgb(value):
    value = np.asarray(value, dtype=float)
    return np.round(255 * np.where(value <= 0.0031308, 12.92 * value, 1.055 * value ** (1 / 2.4) - 0.055))


def render(renderer, scene, size=SIZE):
    target = renderer.create_render_target(width=size, height=size)
    renderer.render(scene, target)
    image = target.read().astype(int)
    target.close()
    return image


def perspective_scene(renderer):
    scene = renderer.create_scene()
    camera = renderer.create_camera()
    camera.set_perspective(fov_y_deg=45, near=0.1, far=20)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    return scene


def glb(shape, material):
    """A glTF file with the same arrays, for comparison with create_mesh()."""
    arrays = [shape["positions"], shape["normals"], shape["uvs"], shape["indices"]]
    binary, views = b"", []
    for array in arrays:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": array.nbytes})
        binary += array.tobytes()
    positions = shape["positions"]
    doc = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                    "indices": 3, "material": 0}]}],
        "materials": [material],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": positions.min(0).tolist(), "max": positions.max(0).tolist()},
            {"bufferView": 1, "componentType": 5126, "count": len(positions), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": len(positions), "type": "VEC2"},
            {"bufferView": 3, "componentType": 5125, "count": shape["indices"].size, "type": "SCALAR"},
        ],
    }
    encoded = json.dumps(doc).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


@pytest.mark.parametrize("name", ["sphere", "box"])
def test_generated_mesh_matches_glTF(renderer, name):
    shape = filly.shapes.uv_sphere(0.8, segments=24, rings=12) if name == "sphere" else filly.shapes.box(1, 1, 1)
    factors = {"base_color": (0.8, 0.3, 0.2, 1), "metallic": 0.2, "roughness": 0.6}
    material = {"pbrMetallicRoughness": {"baseColorFactor": list(factors["base_color"]),
                                         "metallicFactor": factors["metallic"],
                                         "roughnessFactor": factors["roughness"]}}
    images = []
    for generated in (False, True):
        scene = perspective_scene(renderer)
        scene.add_directional_light(direction=(-1, -1, -1), intensity=80000)
        scene.add_point_light(position=(1, 1, 2), intensity=40)
        model = scene.create_mesh(**shape, **factors) if generated else scene.load(glb(shape, material))
        model.rotation_euler_deg = (20, 30, 0)
        images.append(render(renderer, scene, 64))
        scene.close()
    difference = np.abs(images[0] - images[1])
    # Same material from the same provider key; tangent frames come from different code.
    assert difference.max() <= 1, difference.max()
    assert (difference > 0).mean() < 0.02


def test_lambert_shading_follows_the_normals(renderer):
    """The tangent frames carry the normals: a lit sphere follows Lambert's cosine law."""
    scene = perspective_scene(renderer)
    scene.camera.set_orthographic(height=2, near=0.1, far=20)
    scene.add_directional_light(direction=(0, 0, -1), intensity=50000)
    scene.create_mesh(**filly.shapes.uv_sphere(1.0, segments=128, rings=64), base_color=(0.5, 0.5, 0.5, 1), roughness=1)
    image = render(renderer, scene, 64)
    exposure = 1 / (1.2 * 2 ** np.log2(16 ** 2 * 125))
    center = 0.5 / np.pi * 50000 * exposure
    # At pixel x on the unit sphere, n . l = sqrt(1 - x^2). Diffuse only: roughness 1 makes the
    # dielectric specular lobe small, so allow a few levels.
    for column in (32, 44, 52):
        x = (column + 0.5) / 32 - 1
        expected = srgb(center * np.sqrt(1 - x * x))
        assert abs(image[32, column, 0] - expected) <= 3, (column, image[32, column], expected)


def test_normals_are_computed_when_absent(renderer):
    """Area-weighted smooth normals: a shared vertex averages its faces by area."""
    # A tent: two unit squares meeting at the ridge x = 0, tilted 45 degrees toward +X and -X.
    h = np.float32(np.sqrt(0.5))
    positions = np.array([[-h, 0.5, 0], [0, 0.5, h], [h, 0.5, 0], [-h, -0.5, 0], [0, -0.5, h], [h, -0.5, 0]], np.float32)
    # Each ridge vertex touches one triangle per face, or two per face, so both faces weigh equally.
    indices = np.array([[3, 4, 1], [3, 1, 0], [4, 5, 1], [5, 2, 1]], np.uint32)
    side = np.float32(np.sqrt(0.5))
    # Outer vertices take their face normal; ridge vertices the mean of the two, +Z.
    normals = np.array([[-side, 0, side], [0, 0, 1], [side, 0, side]] * 2, np.float32)
    images = []
    for given in (normals, None):
        scene = perspective_scene(renderer)
        scene.add_directional_light(direction=(-1, -0.5, -1))
        scene.create_mesh(positions, indices, normals=given)
        images.append(render(renderer, scene))
        scene.close()
    assert images[0][16, 16, 0] > 0
    assert np.abs(images[0] - images[1]).max() <= 1


def test_vertex_colors_and_updates(renderer, scene):
    shape = filly.shapes.plane(2, 2)
    colors = np.tile(np.float32([1, 0, 0, 1]), (4, 1))
    plane = scene.create_mesh(**shape, colors=colors, unlit=True, alpha_mode="blend")
    np.testing.assert_array_equal(render(renderer, scene)[16, 16], [255, 0, 0, 255])
    colors[:, :3] = [0, 0, 0.5]
    plane.update_mesh(colors=colors)
    np.testing.assert_allclose(render(renderer, scene)[16, 16], [0, 0, srgb(0.5), 255], atol=1)
    with pytest.raises(ValueError, match="rows"):
        plane.update_mesh(colors=colors[:3])
    bare = scene.create_mesh(**shape)
    with pytest.raises(ValueError, match="without vertex colors"):
        bare.update_mesh(colors=colors)
    with pytest.raises(ValueError, match="computes its normals"):
        scene.create_mesh(shape["positions"], shape["indices"]).update_mesh(normals=shape["normals"])


def test_mesh_without_colors_is_white():
    """Archive materials multiply by COLOR and read UV1, so the mesh supplies both."""
    with filly.Renderer() as renderer:
        scene = perspective_scene(renderer)
        scene.camera.set_orthographic(height=2, near=0.1, far=20)
        scene.background = (0.2, 0.2, 0.2, 1)
        scene.create_mesh(**filly.shapes.plane(1, 1), unlit=True, alpha_mode="blend", base_color=(0.5, 0.5, 0.5, 1))
        image = render(renderer, scene)
    np.testing.assert_array_equal(image[16, 16], [srgb(0.5)] * 3 + [255])
    np.testing.assert_array_equal(image[2, 2, :3], [srgb(0.2)] * 3)


def test_position_updates_move_pixels_without_history(renderer, scene):
    shape = filly.shapes.plane(1, 1)
    plane = scene.create_mesh(**shape, unlit=True, alpha_mode="blend")
    first = render(renderer, scene)
    # The camera sees [-1, 1]: the unit plane covers the middle half of the target.
    assert first[16, 16, 0] == 255 and first[16, 4, 0] == 0
    moved = shape["positions"] + np.float32([0.75, 0, 0])
    plane.update_mesh(positions=moved)
    second = render(renderer, scene)
    # Now it covers [0.25, 1]: columns 20 to 31.
    assert second[16, 16, 0] == 0 and second[16, 21, 0] == 255 and second[16, 30, 0] == 255
    np.testing.assert_allclose(plane.bounds, [[-0.5, -0.5, 0], [0.5, 0.5, 0]])
    # The renderable bounds follow the update, so culling keeps the moved mesh.
    plane.update_mesh(positions=shape["positions"] + np.float32([3, 0, 0]))
    plane.update_mesh(positions=shape["positions"] + np.float32([0.75, 0, 0]))
    np.testing.assert_array_equal(render(renderer, scene), second)
    plane.update_mesh(positions=shape["positions"])
    np.testing.assert_array_equal(render(renderer, scene), first)


def test_lit_update_with_same_arrays_is_identical(renderer):
    """Updates use a copy of SurfaceOrientation; the same arrays give the same frames."""
    scene = perspective_scene(renderer)
    scene.add_directional_light(direction=(-1, -1, -1))
    shape = filly.shapes.uv_sphere(0.8)
    for normals in (shape["normals"], None):
        sphere = scene.create_mesh(shape["positions"], shape["indices"], normals=normals, uvs=shape["uvs"])
        first = render(renderer, scene)
        sphere.update_mesh(positions=shape["positions"] * 0.5)
        sphere.update_mesh(positions=shape["positions"])
        np.testing.assert_array_equal(render(renderer, scene), first)
        sphere.close()


def test_invalid_meshes(scene):
    shape = filly.shapes.plane()
    with pytest.raises(ValueError, match="out of range"):
        scene.create_mesh(shape["positions"], shape["indices"] + 10)
    with pytest.raises(ValueError, match="rows"):
        scene.create_mesh(shape["positions"], shape["indices"], uvs=shape["uvs"][:2])
    with pytest.raises(ValueError, match="finite"):
        scene.create_mesh(shape["positions"] * np.nan, shape["indices"])
    with pytest.raises(ValueError, match="alpha_mode"):
        scene.create_mesh(**shape, alpha_mode="glass")
    with pytest.raises(filly.FillyError, match="generated meshes"):
        scene.load(glb(shape, {})).update_mesh(positions=shape["positions"])


def test_invalid_mesh_assets(renderer):
    shape = filly.shapes.plane()
    with pytest.raises(ValueError, match="out of range"):
        renderer.create_mesh_asset(shape["positions"], shape["indices"] + 10)
    with pytest.raises(ValueError, match="alpha_mode"):
        renderer.create_mesh_asset(**shape, alpha_mode="glass")
    with pytest.raises(ValueError, match="nonzero"):
        renderer.create_mesh_asset(shape["positions"], shape["indices"], normals=shape["normals"] * 0)


def grid(models, columns=4):
    for i, model in enumerate(models):
        model.position = (i % columns - 1.5, i // columns - 0.5, 0)


def grid_scene(renderer):
    scene = renderer.create_scene()
    camera = renderer.create_camera()
    camera.set_orthographic(height=4, near=0.1, far=20)
    camera.position = (0, 0, 5)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    scene.add_directional_light(direction=(-1, -1, -2), intensity=80000)
    return scene


def test_mesh_asset_models_share_buffers(renderer):
    shape = filly.shapes.box(0.6, 0.6, 0.6)
    asset = renderer.create_mesh_asset(**shape)
    empty = asset.memory
    assert empty.models == 0 and empty.gpu_geometry_bytes >= 24 * (12 + 8 + 8 + 4) + 12 * 12
    scene = grid_scene(renderer)
    models = [scene.add(asset) for _ in range(8)]
    shared = asset.memory
    # Models add no geometry: the asset has one vertex and one index buffer for all of them.
    assert shared.models == 8 and shared.instance_gpu_bytes == 0
    assert (shared.gpu_geometry_bytes, shared.gpu_texture_bytes) == (empty.gpu_geometry_bytes, empty.gpu_texture_bytes)
    assert models[3].memory.gpu_geometry_bytes == empty.gpu_geometry_bytes
    # A separate mesh has the same buffers, once per model.
    single = scene.create_mesh(**shape).memory
    assert single.gpu_geometry_bytes == empty.gpu_geometry_bytes and single.models == 1


def test_mesh_asset_copies_render_like_separate_meshes(renderer):
    shape = filly.shapes.uv_sphere(0.4, segments=16, rings=8)
    factors = {"base_color": (0.8, 0.4, 0.2, 1), "metallic": 0.3, "roughness": 0.5}
    images = []
    for shared in (False, True):
        scene = grid_scene(renderer)
        scene.msaa = 4
        if shared:
            asset = renderer.create_mesh_asset(**shape, **factors)
            models = [scene.add(asset) for _ in range(8)]
            asset.close()
        else:
            models = [scene.create_mesh(**shape, **factors) for _ in range(8)]
        grid(models)
        models[5].rotation_euler_deg = (0, 0, 40)
        images.append(render(renderer, scene, 96))
        scene.close()
    assert images[0][:, :, :3].max() > 100
    np.testing.assert_array_equal(images[0], images[1])


def test_mesh_asset_materials_belong_to_each_model(renderer):
    """Models share one material instance until a handle is requested; edits stay per model."""
    shape = filly.shapes.plane(0.8, 0.8)
    asset = renderer.create_mesh_asset(**shape, unlit=True, base_color=(0.5, 0.5, 0.5, 1))
    scene = grid_scene(renderer)
    models = [scene.add(asset) for _ in range(8)]
    grid(models)
    before = render(renderer, scene, 64)
    models[0].material("mesh").base_color = (1, 0, 0, 1)
    models[1].node("mesh").material(0).base_color = (0, 1, 0, 1)
    # Reading a material that no edit changed gives the asset's factors.
    assert models[2].material("mesh").base_color == pytest.approx((0.5, 0.5, 0.5, 1))
    after = render(renderer, scene, 64)
    # The pixel at the center of model i: the target spans 4 x 4 units at 64 x 64 pixels.
    def pixel(i):
        x, y = i % 4 - 1.5, i // 4 - 0.5
        return int(32 - y * 16), int(32 + x * 16)
    gray = srgb(0.5)
    np.testing.assert_array_equal(before[pixel(0)][:3], [gray] * 3)
    np.testing.assert_array_equal(after[pixel(0)][:3], [255, 0, 0])
    np.testing.assert_array_equal(after[pixel(1)][:3], [0, 255, 0])
    for i in range(2, 8):
        np.testing.assert_array_equal(after[pixel(i)], before[pixel(i)])
    # A later model starts from the asset's factors, not from the edits of others.
    late = scene.add(asset)
    assert late.material("mesh").base_color == pytest.approx((0.5, 0.5, 0.5, 1))


def test_mesh_asset_update_changes_every_model(renderer):
    shape = filly.shapes.plane(1, 1)
    asset = renderer.create_mesh_asset(**shape, unlit=True)
    scenes = [grid_scene(renderer), grid_scene(renderer)]
    first, second = scenes[0].add(asset), scenes[1].add(asset)
    asset.close()
    # Models keep the mesh after the asset handle closes; the handle can add no more.
    with pytest.raises(filly.FillyError):
        scenes[0].add(asset)
    with pytest.raises(filly.FillyError):
        asset.memory
    images = [render(renderer, scene) for scene in scenes]
    np.testing.assert_array_equal(images[0], images[1])
    first.update_mesh(positions=shape["positions"] + np.float32([1, 0, 0]))
    moved = [render(renderer, scene) for scene in scenes]
    np.testing.assert_array_equal(moved[0], moved[1])
    assert (moved[0] != images[0]).any()
    # The culling bounds of both models follow the update.
    first.update_mesh(positions=shape["positions"] + np.float32([1.4, 0, 0]))
    assert render(renderer, scenes[1])[16, 30, 0] == 255
    second.close()
    first.update_mesh(positions=shape["positions"])
    np.testing.assert_array_equal(render(renderer, scenes[0]), images[0])


def test_mesh_asset_alpha_mask_needs_exact_output(renderer):
    asset = renderer.create_mesh_asset(**filly.shapes.plane(), alpha_mode="mask")
    scene = renderer.create_scene()
    scene.output_path = "direct"
    with pytest.raises(filly.AssetError, match="MASK"):
        scene.add(asset)
    scene.output_path = "exact"
    scene.add(asset)


@pytest.mark.parametrize("name,shape", [
    ("plane", filly.shapes.plane(2, 1, segments=(4, 3))), ("box", filly.shapes.box(1, 2, 3)),
    ("sphere", filly.shapes.uv_sphere(0.5, segments=12, rings=6)), ("cylinder", filly.shapes.cylinder(0.5, 2, segments=12)),
])
def test_shape_arrays(name, shape):
    positions, normals, uvs, indices = (shape[key] for key in ("positions", "normals", "uvs", "indices"))
    assert positions.dtype == normals.dtype == uvs.dtype == np.float32 and indices.dtype == np.uint32
    assert positions.shape == normals.shape and uvs.shape == (len(positions), 2) and indices.shape[1] == 3
    assert indices.max() < len(positions)
    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-6)
    assert uvs.min() >= 0 and uvs.max() <= 1
    # Counterclockwise front faces: the face normal agrees with the vertex normals.
    a, b, c = (positions[indices[:, i]] for i in range(3))
    faces = np.cross(b - a, c - a)
    area = np.linalg.norm(faces, axis=1)
    assert area.min() > 0
    assert np.all(np.einsum("ij,ij->i", faces, normals[indices].sum(1)) > 0)
