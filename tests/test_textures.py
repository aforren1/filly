"""Runtime textures: known texels in, known pixels out, on both output paths."""

import struct
import zlib

import numpy as np
import pytest

import filly
from test_features import lit, pack, unpack

pytestmark = pytest.mark.gpu

SIZE = 16


def srgb(value):
    value = np.asarray(value, dtype=float)
    return np.round(255 * np.where(value <= 0.0031308, 12.92 * value, 1.055 * value ** (1 / 2.4) - 0.055))


def linear(byte):
    value = np.asarray(byte, dtype=float) / 255
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def use_path(scene, path):
    """Color grading with MSAA, or the direct opt-in. A full-view plane has no geometry edge for
    MSAA to change."""
    if path == "direct":
        scene.output_path = "direct"
    else:
        scene.msaa = 4


def textured_plane(scene, **options):
    return scene.create_mesh(**filly.shapes.plane(2, 2), unlit=True, alpha_mode="blend", **options)


def render(renderer, scene, size=SIZE):
    target = renderer.create_render_target(width=size, height=size)
    renderer.render(scene, target)
    image = target.read().astype(int)
    target.close()
    return image


def quadrants(image):
    """Texel centers of a 2 x 2 texture drawn over the whole target: top-left, top-right, ..."""
    q = image.shape[0] // 4
    return [image[q, q], image[q, 3 * q], image[3 * q, q], image[3 * q, 3 * q]]


@pytest.mark.parametrize("path", ["direct", "grading"])
def test_rgba8_srgb_texels_reach_the_output(renderer, scene, path):
    use_path(scene, path)
    texels = np.array([[[255, 0, 0, 255], [0, 255, 0, 255]],
                       [[0, 0, 255, 255], [188, 100, 20, 255]]], dtype=np.uint8)
    plane = textured_plane(scene)
    plane.material("mesh").base_color_texture = renderer.create_texture(texels, color_space="srgb", filter="nearest")
    image = render(renderer, scene)
    # The first array row is the top of the image, as in target.read().
    for actual, expected in zip(quadrants(image), texels.reshape(4, 4)):
        np.testing.assert_allclose(actual, expected, atol=1)


@pytest.mark.parametrize("path", ["direct", "grading"])
@pytest.mark.parametrize("channels,dtype,space", [
    (1, np.uint8, "srgb"), (1, np.uint8, "linear"), (3, np.uint8, "srgb"), (3, np.uint8, "linear"),
    (4, np.uint8, "linear"), (1, np.float32, "linear"), (3, np.float32, "linear"), (4, np.float32, "linear"),
])
def test_formats_and_color_spaces(renderer, scene, path, channels, dtype, space):
    use_path(scene, path)
    grey = np.array([0.1, 0.5, 0.8, 1.0])
    if dtype == np.float32:
        values = grey.astype(np.float32)
    elif space == "srgb":
        values = srgb(grey).astype(np.uint8)
    else:
        values = np.round(grey * 255).astype(np.uint8)
    stored = values / 255 if dtype == np.uint8 else values
    light = linear(values) if space == "srgb" else stored
    texels = np.repeat(values.reshape(2, 2, 1), channels, axis=2)
    if channels == 4:
        texels[..., 3] = 255 if dtype == np.uint8 else 1
    if channels == 1:
        texels = texels[..., 0]
    texture = renderer.create_texture(np.ascontiguousarray(texels), color_space=space, filter="nearest")
    assert (texture.channels, texture.dtype, texture.color_space) == (channels, np.dtype(dtype).name, space)
    textured_plane(scene).material("mesh").base_color_texture = texture
    image = render(renderer, scene)
    expected = srgb(light)
    for actual, value in zip(quadrants(image), expected):
        # One channel reads as grey with alpha one.
        np.testing.assert_allclose(actual, [value, value, value, 255], atol=1)


def test_float32_rejects_srgb(renderer):
    with pytest.raises(ValueError, match="linear"):
        renderer.create_texture(np.zeros((2, 2, 4), np.float32), color_space="srgb")
    with pytest.raises(ValueError, match="1, 3, or 4"):
        renderer.create_texture(np.zeros((2, 2, 2), np.uint8), color_space="linear")
    with pytest.raises(TypeError):
        renderer.create_texture(np.zeros((2, 2, 4), np.float64), color_space="linear")
    with pytest.raises(TypeError):
        renderer.create_texture(np.zeros((2, 2, 4), np.uint8))


def test_update_in_place(renderer, scene):
    texture = renderer.create_texture(np.zeros((2, 2, 4), np.uint8), color_space="linear", filter="nearest")
    textured_plane(scene).material("mesh").base_color_texture = texture
    target = renderer.create_render_target(width=SIZE, height=SIZE)
    frame = np.zeros((2, 2, 4), np.uint8)
    frame[..., 3] = 255
    # More updates than staging buffers, without a render in between: the last one wins.
    for value in range(10):
        frame[..., :3] = value * 20
        texture.update(frame)
    renderer.render(scene, target)
    np.testing.assert_allclose(target.read()[4, 4], [*srgb([180 / 255] * 3), 255], atol=1)
    # The array can change right after update(): it was copied.
    frame[..., :3] = 255
    texture.update(frame)
    frame[..., :3] = 0
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read()[4, 4], [255, 255, 255, 255])
    with pytest.raises(ValueError, match="shape and dtype"):
        texture.update(np.zeros((2, 3, 4), np.uint8))
    with pytest.raises(ValueError, match="shape and dtype"):
        texture.update(np.zeros((2, 2, 4), np.float32))
    target.close()


def test_mipmaps_average_a_minified_checkerboard(renderer, scene):
    checker = (np.indices((256, 256)).sum(0) % 2 * 255).astype(np.uint8)
    plane = textured_plane(scene)
    plane.material("mesh").base_color_texture = renderer.create_texture(checker, color_space="linear", mipmaps=True)
    # 256 texels over 8 pixels: the mipmap average is linear 0.5.
    image = render(renderer, scene, size=8)
    np.testing.assert_allclose(image[..., :3], srgb(0.5), atol=2)
    plane.material("mesh").base_color_texture = renderer.create_texture(checker, color_space="linear", filter="nearest")
    image = render(renderer, scene, size=8)
    # Without mipmaps, each pixel takes one texel.
    assert set(np.unique(image[..., :3])) <= {0, 255}


def test_texture_transform_drifts(renderer, scene):
    # Left texel black, right texel white; repeat wraps a shifted texture.
    texture = renderer.create_texture(np.array([[0, 255]], np.uint8), color_space="linear", filter="nearest")
    material = textured_plane(scene).material("mesh")
    material.base_color_texture = texture
    before = render(renderer, scene)
    assert before[8, 4, 0] == 0 and before[8, 12, 0] == 255
    material.set_texture_transform("base_color", offset=(0.5, 0))
    after = render(renderer, scene)
    assert after[8, 4, 0] == 255 and after[8, 12, 0] == 0
    material.set_texture_transform("base_color", scale=(2, 1))
    # Two periods: black, white, black, white.
    row = render(renderer, scene)[8, :, 0]
    np.testing.assert_array_equal(row[[2, 6, 10, 14]], [0, 255, 0, 255])
    material.set_texture_transform("base_color", rotation_deg=180)
    np.testing.assert_array_equal(render(renderer, scene), after)
    with pytest.raises(filly.FillyError, match="Assign a texture"):
        material.set_texture_transform("emissive")
    with pytest.raises(ValueError, match="slot"):
        material.set_texture_transform("normal")


@pytest.mark.parametrize("path", ["direct", "grading"])
def test_glTF_material_without_texture_slot(renderer, scene, triangle_glb, path):
    """The flat-colored stimulus case: the provider compiles the variant with the slot."""
    use_path(scene, path)
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    model = scene.load(pack(doc, binary))
    material = model.material("red")
    center = lambda: render(renderer, scene)[SIZE // 2, SIZE // 2]
    np.testing.assert_array_equal(center(), [255, 0, 0, 255])
    # The triangle has no TEXCOORD_0, so every fragment samples texel (0, 0): linear 0.5 grey.
    material.base_color_texture = renderer.create_texture(np.full((1, 1, 3), 128, np.uint8), color_space="linear")
    np.testing.assert_allclose(center(), [srgb(128 / 255), 0, 0, 255], atol=1)
    # Factors carry over in both directions.
    material.base_color = (0, 1, 0, 1)
    np.testing.assert_allclose(center(), [0, srgb(128 / 255), 0, 255], atol=1)
    material.base_color_texture = None
    assert material.base_color_texture is None
    np.testing.assert_array_equal(center(), [0, 255, 0, 255])
    assert material.base_color == pytest.approx((0, 1, 0, 1))


def test_node_local_and_shared_materials(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    doc["nodes"] = [{"mesh": 0, "name": "left", "translation": [-0.5, 0, 0], "scale": [0.5, 0.5, 1]},
                    {"mesh": 0, "name": "right", "translation": [0.5, 0, 0], "scale": [0.5, 0.5, 1]}]
    doc["scenes"][0]["nodes"] = [0, 1]
    model = scene.load(pack(doc, binary))
    white = renderer.create_texture(np.full((1, 1, 4), 255, np.uint8), color_space="srgb")
    blue = renderer.create_texture(np.array([[[0, 0, 255, 255]]], np.uint8), color_space="srgb")
    left, right = (lambda image: image[8, 4], lambda image: image[8, 12])
    model.node("left").material().base_color_texture = white
    image = render(renderer, scene)
    # Only the left node's copy is textured: red times white stays red, and the right is unchanged.
    np.testing.assert_array_equal(left(image), [255, 0, 0, 255])
    model.node("left").material().base_color = (1, 1, 1, 1)
    model.node("left").material().base_color_texture = blue
    image = render(renderer, scene)
    np.testing.assert_array_equal(left(image), [0, 0, 255, 255])
    np.testing.assert_array_equal(right(image), [255, 0, 0, 255])
    # The shared material reaches the right node only; the left keeps its own copy.
    shared = model.material("red")
    shared.base_color = (1, 1, 1, 1)
    shared.base_color_texture = white
    image = render(renderer, scene)
    np.testing.assert_array_equal(left(image), [0, 0, 255, 255])
    np.testing.assert_array_equal(right(image), [255, 255, 255, 255])
    assert shared.base_color_texture == white
    # A node-local material made from the textured shared one starts with its texture and
    # keeps it when the shared material loses it.
    shared.base_color_texture = blue
    local = model.node("right").material()
    assert local.base_color_texture == blue
    shared.base_color_texture = None
    image = render(renderer, scene)
    np.testing.assert_array_equal(right(image), [0, 0, 255, 255])
    local.base_color_texture = None
    np.testing.assert_array_equal(right(render(renderer, scene)), [255, 255, 255, 255])


def test_animation_and_variants_reach_textured_materials(renderer, scene, triangle_glb):
    from test_animation_pointer import add_clip
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    doc["extensionsUsed"].append("KHR_materials_variants")
    doc["extensions"] = {"KHR_materials_variants": {"variants": [{"name": "green"}]}}
    doc["materials"].append({"name": "green", "alphaMode": "BLEND", "extensions": {"KHR_materials_unlit": {}},
                             "pbrMetallicRoughness": {"baseColorFactor": [0, 1, 0, 1]}})
    doc["meshes"][0]["primitives"][0]["extensions"] = {
        "KHR_materials_variants": {"mappings": [{"material": 1, "variants": [0]}]}}
    add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/baseColorFactor", [1, 1, 1, 1, 0, 0, 1, 1], kind="VEC4")
    model = scene.load(pack(doc, binary))
    grey = renderer.create_texture(np.full((1, 1, 3), 128, np.uint8), color_space="linear")
    half = srgb(128 / 255)
    center = lambda: render(renderer, scene)[8, 8]
    model.material("red").base_color_texture = grey
    model.material("green").base_color_texture = grey
    # The clip animates the glTF material; the textured material shows the animated factor.
    model.apply_animation(0, 2, loop=False)
    np.testing.assert_allclose(center(), [0, 0, half, 255], atol=1)
    model.apply_variant("green")
    np.testing.assert_allclose(center(), [0, half, 0, 255], atol=1)


def _png(rgb):
    raw = b"\x00" + bytes(rgb)
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def textured_glb(triangle_glb, with_normal_map=False):
    doc, binary = unpack(triangle_glb)
    image = _png((0, 255, 0))
    offset = len(binary)
    binary += image
    binary += b"\0" * (-len(binary) % 4)
    uv = len(binary)
    binary += struct.pack("<6f", 0, 0, 0, 0, 0, 0)
    doc["bufferViews"] += [{"buffer": 0, "byteOffset": offset, "byteLength": len(image)},
                           {"buffer": 0, "byteOffset": uv, "byteLength": 24}]
    doc["accessors"].append({"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC2"})
    doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = 1
    doc["images"] = [{"bufferView": 1, "mimeType": "image/png"}]
    doc["textures"] = [{"source": 0}]
    material = doc["materials"][0]
    material["alphaMode"] = "BLEND"
    material["pbrMetallicRoughness"] = {"baseColorFactor": [1, 1, 1, 1], "baseColorTexture": {"index": 0}}
    if with_normal_map:
        lit(doc)
        material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
        material["normalTexture"] = {"index": 0}
    return pack(doc, binary)


def test_glTF_texture_is_restored_after_removal(renderer, scene, triangle_glb):
    material = scene.load(textured_glb(triangle_glb)).material("red")
    center = lambda: render(renderer, scene)[SIZE // 2, SIZE // 2]
    np.testing.assert_array_equal(center(), [0, 255, 0, 255])
    assert material.base_color_texture is None
    material.base_color_texture = renderer.create_texture(np.array([[[255, 0, 255, 255]]], np.uint8), color_space="srgb")
    np.testing.assert_array_equal(center(), [255, 0, 255, 255])
    material.set_texture_transform("base_color", offset=(0.25, 0.25))
    material.base_color_texture = None
    np.testing.assert_array_equal(center(), [0, 255, 0, 255])


def test_other_glTF_textures_block_a_new_slot(renderer, scene, triangle_glb):
    model = scene.load(textured_glb(triangle_glb, with_normal_map=True))
    texture = renderer.create_texture(np.full((1, 1, 4), 255, np.uint8), color_space="srgb")
    with pytest.raises(filly.AssetError, match="other slots"):
        model.material("red").emissive_texture = texture
    # The slot that exists keeps its compiled material; only transforms are unavailable.
    model.material("red").base_color_texture = texture
    with pytest.raises(filly.AssetError, match="without texture transforms"):
        model.material("red").set_texture_transform("base_color", offset=(0.5, 0))


@pytest.mark.parametrize("path", ["direct", "grading"])
def test_emissive_texture_on_lit_material(renderer, scene, path):
    use_path(scene, path)
    # Black and nonmetallic, without lights: only emission reaches the output, unscaled by exposure.
    plane = scene.create_mesh(**filly.shapes.plane(2, 2), base_color=(0, 0, 0, 1), roughness=1)
    material = plane.material("mesh")
    material.emissive = (1, 1, 1)
    material.emissive_texture = renderer.create_texture(np.full((1, 1, 3), 0.25, np.float32), color_space="linear")
    image = render(renderer, scene)
    np.testing.assert_allclose(image[8, 8], [*srgb([0.25] * 3), 255], atol=1)
    assert material.emissive == pytest.approx((1, 1, 1))
    unlit = textured_plane(scene).material("mesh")
    with pytest.raises(filly.AssetError, match="no emissive"):
        unlit.emissive_texture = material.emissive_texture


def test_closing_a_texture_removes_it(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    material = scene.load(pack(doc, binary)).material("red")
    texture = renderer.create_texture(np.array([[[0, 0, 255, 255]]], np.uint8), color_space="srgb")
    material.base_color_texture = texture
    texture.close()
    assert texture.closed and material.base_color_texture is None
    np.testing.assert_array_equal(render(renderer, scene)[8, 8], [255, 0, 0, 255])
    with pytest.raises(filly.FillyError, match="closed"):
        texture.update(np.zeros((1, 1, 4), np.uint8))
    with pytest.raises(filly.FillyError, match="closed"):
        material.base_color_texture = texture
    other = renderer.create_texture(np.zeros((1, 1, 4), np.uint8), color_space="srgb")
    renderer.close()
    assert other.closed


def test_texture_from_another_renderer_is_rejected(scene, triangle_glb):
    material = scene.load(triangle_glb).material("red")
    with filly.Renderer() as other:
        texture = other.create_texture(np.zeros((1, 1, 4), np.uint8), color_space="srgb")
        with pytest.raises(ValueError, match="this renderer"):
            material.base_color_texture = texture


def test_repeated_frames_are_identical(renderer, scene):
    rng = np.random.default_rng(1)
    texture = renderer.create_texture(rng.integers(0, 256, (8, 8), dtype=np.uint8), color_space="linear",
                                      filter="nearest")
    textured_plane(scene).material("mesh").base_color_texture = texture
    first = render(renderer, scene)
    texture.update(rng.integers(0, 256, (8, 8), dtype=np.uint8))
    render(renderer, scene)
    texture.update(rng.integers(0, 256, (8, 8), dtype=np.uint8))
    rng = np.random.default_rng(1)
    texture.update(rng.integers(0, 256, (8, 8), dtype=np.uint8))
    np.testing.assert_array_equal(render(renderer, scene), first)


def test_precompiled_shaders_support_texture_slots(triangle_glb):
    """Fast mode takes the textured variant from Filament's archive or compiles it."""
    with filly.Renderer(precompiled_shaders=True) as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(height=2, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        doc, binary = unpack(triangle_glb)
        lit(doc)
        material = scene.load(pack(doc, binary)).material("red")
        material.base_color = (0, 0, 0, 1)
        material.emissive = (1, 1, 1)
        material.emissive_texture = renderer.create_texture(np.array([[[0, 128, 255]]], np.uint8), color_space="linear")
        np.testing.assert_allclose(render(renderer, scene)[8, 8], [0, srgb(128 / 255), 255, 255], atol=1)
        material.base_color_texture = renderer.create_texture(np.full((1, 1, 3), 255, np.uint8), color_space="srgb")
        material.emissive_texture = None
        # Without its texture, the emissive factor alone emits white.
        np.testing.assert_array_equal(render(renderer, scene)[8, 8], [255, 255, 255, 255])
