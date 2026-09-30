"""Output encoding and output path: filly's exact encode pass by default, the direct path on
request, and independent of other options."""

import numpy as np
import pytest

import filly
from test_features import lit, pack, unpack

pytestmark = pytest.mark.gpu

SIZE = 16
# The 1/64 grid from the requirement plus every 8-bit input level: 319 values.
SWEEP = np.unique(np.r_[np.arange(65) / 64, np.arange(256) / 255])


def srgb(value):
    value = np.asarray(value, dtype=float)
    return np.where(value <= 0.0031308, 12.92 * value, 1.055 * value ** (1 / 2.4) - 0.055)


def half_neighbors(values):
    """The two fp16 values around each float32 value: what an RGBA16F buffer can hold for it,
    whatever the driver's rounding mode."""
    v32 = np.asarray(values, dtype=np.float32)
    near = v32.astype(np.float16)
    below = np.where(near.astype(np.float32) > v32, np.nextafter(near, np.float16(-1)), near)
    above = np.where(near.astype(np.float32) < v32, np.nextafter(near, np.float16(2)), near)
    return below.astype(float), above.astype(float)


def grey_pixel(renderer, scene, material, value, alpha=1.0):
    material.base_color = (value, value, value, alpha)
    target = renderer.create_render_target(width=SIZE, height=SIZE)
    renderer.render(scene, target)
    pixel = target.read()[SIZE // 2, SIZE // 2].astype(int)
    target.close()
    return pixel


def sweep(renderer, scene, material, values=SWEEP, alpha=1.0):
    """The stored center pixel for each value, with one target for the whole sweep."""
    target = renderer.create_render_target(width=SIZE, height=SIZE)
    pixels = []
    for value in values:
        material.base_color = (value, value, value, alpha)
        renderer.render(scene, target)
        pixels.append(target.read()[SIZE // 2, SIZE // 2].astype(int))
    target.close()
    return np.array(pixels)


def transfer(encoding):
    return srgb if encoding == "srgb" else (lambda v: np.asarray(v, dtype=float))


def test_default_encoding_is_srgb(renderer, scene, triangle_glb):
    material = scene.load(triangle_glb).material("red")
    assert (scene.encoding, scene.output_path) == ("srgb", "exact")
    np.testing.assert_array_equal(grey_pixel(renderer, scene, material, 0.5), [188, 188, 188, 255])


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
def test_exact_path_rounds_exactly(renderer, scene, triangle_glb, encoding):
    """The encode pass rounds the analytic transfer function of the value in the scene-linear
    buffer exactly. That buffer is RGBA16F, so the stored level is exact for one of the two fp16
    neighbors of the input, and within one level of the analytic value of the input."""
    scene.encoding = encoding
    material = scene.load(triangle_glb).material("red")
    actual = sweep(renderer, scene, material)[:, :3]
    f = transfer(encoding)
    below, above = half_neighbors(SWEEP)
    candidates = np.stack([np.round(255 * f(below)), np.round(255 * f(above))], axis=1)
    exact = (actual[:, :, None] == candidates[:, None, :]).any(axis=2)
    assert exact.all(), SWEEP[~exact.all(axis=1)]
    error = np.abs(actual - 255 * f(SWEEP)[:, None])
    # Half a level of rounding plus the fp16 step of the input (0.052 levels at most).
    assert error.max() <= 0.553, (SWEEP[error.max(axis=1).argmax()], error.max())
    assert np.all(actual.max(axis=1) == actual.min(axis=1)), "neutral in, neutral out"


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
def test_exact_path_rounds_every_half_float_exactly(renderer, scene, encoding):
    """Every fp16 value in [0, 1], one per pixel, from a float texture: the texture, the unlit
    shader, and the RGBA16F linear buffer keep the value unchanged, so each stored level must
    equal the rounded analytic value."""
    values = np.arange(0, 0x3C01, dtype=np.uint16).view(np.float16).astype(np.float32)
    width = 128
    height = -(-values.size // width)
    grid = np.ones(width * height, np.float32)
    grid[:values.size] = values
    texels = np.repeat(grid.reshape(height, width, 1), 4, axis=2)
    texels[..., 3] = 1
    scene.encoding = encoding
    plane = scene.create_mesh(**filly.shapes.plane(2, 2), unlit=True)
    plane.material("mesh").base_color_texture = renderer.create_texture(
        np.ascontiguousarray(texels), color_space="linear", filter="nearest")
    target = renderer.create_render_target(width=width, height=height)
    renderer.render(scene, target)
    image = target.read().astype(int)
    target.close()
    actual = image.reshape(-1, 4)[:values.size]
    expected = np.round(255 * transfer(encoding)(values.astype(float)))
    wrong = np.flatnonzero((actual[:, :3] != expected[:, None]).any(axis=1))
    assert wrong.size == 0, (values[wrong[:5]], actual[wrong[:5]], expected[wrong[:5]])
    assert (actual[:, 3] == 255).all()


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
def test_direct_path_matches_transfer_function(renderer, scene, triangle_glb, encoding):
    scene.encoding = encoding
    scene.output_path = "direct"
    material = scene.load(triangle_glb).material("red")
    actual = sweep(renderer, scene, material)[:, :3]
    expected = np.round(255 * transfer(encoding)(SWEEP))
    error = np.abs(actual - expected[:, None])
    assert error.max() <= 1, (SWEEP[error.max(axis=1).argmax()], error.max())
    assert np.all(actual.max(axis=1) == actual.min(axis=1)), "neutral in, neutral out"


# Options that do not change a flat unlit image. Each one alone must leave every stored level
# of the sweep unchanged: the same encode pass writes every configuration.
UNRELATED = [("antialiasing", "fxaa"), ("msaa", 4), ("refraction", True), ("shadows", True),
             ("ssao", True), ("transparent", True), ("tone_mapping", "linear")]


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
@pytest.mark.parametrize("option,value", UNRELATED)
def test_sweep_is_identical_with_other_options(renderer, triangle_glb, encoding, option, value):
    images = []
    for enabled in (False, True):
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        scene.camera = camera
        scene.encoding = encoding
        if enabled:
            setattr(scene, option, value)
        images.append(sweep(renderer, scene, scene.load(triangle_glb).material("red")))
        scene.close()
    np.testing.assert_array_equal(images[1], images[0])


# Options that run Filament's postprocessing: color grading writes scene-linear color before the
# encode pass, through a 512-entry fp16 LUT, so levels can move by one. Dithering adds up to one
# level of noise by design.
POSTPROCESSED = [("bloom", True), ("depth_of_field", True), ("dithering", True)]


@pytest.mark.parametrize("option,value", POSTPROCESSED)
def test_sweep_stays_within_one_level_with_postprocessing(renderer, scene, triangle_glb, option, value):
    setattr(scene, option, value)
    material = scene.load(triangle_glb).material("red")
    actual = sweep(renderer, scene, material)[:, :3]
    error = np.abs(actual - np.round(255 * srgb(SWEEP))[:, None])
    assert error.max() <= 1, (SWEEP[error.max(axis=1).argmax()], error.max())
    assert np.all(actual.max(axis=1) == actual.min(axis=1)), "neutral in, neutral out"


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
@pytest.mark.parametrize("alpha", [0.25, 0.5, 0.75])
def test_transparent_output_is_premultiplied_after_encoding(renderer, scene, triangle_glb, encoding, alpha):
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    scene.encoding = encoding
    scene.transparent = True
    scene.background = (0, 0, 0, 0)
    material = scene.load(pack(doc, binary)).material("red")
    values = SWEEP[::8]
    actual = sweep(renderer, scene, material, values, alpha)
    # The encoded straight color times alpha: what ONE, ONE_MINUS_SRC_ALPHA blending expects.
    expected = 255 * transfer(encoding)(values) * alpha
    assert np.abs(actual[:, :3] - expected[:, None]).max() <= 1
    np.testing.assert_allclose(actual[:, 3], 255 * alpha, atol=0.5)


def render_image(renderer, scene, size=SIZE):
    target = renderer.create_render_target(width=size, height=size)
    renderer.render(scene, target)
    image = target.read()
    target.close()
    return image


def flat_scene(renderer, color):
    """A plane that fills the view: a flat field with no edge."""
    scene = renderer.create_scene()
    camera = scene.create_camera()
    camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
    camera.position = (0, 0, 3)
    scene.camera = camera
    scene.create_mesh(**filly.shapes.plane(4, 4), unlit=True, base_color=color)
    return scene


@pytest.mark.parametrize("option,value", UNRELATED)
def test_flat_field_is_identical_with_other_options(renderer, option, value):
    """FXAA finds no edge in a flat field and keeps the encoded level; the other options have
    nothing to change there."""
    images = []
    for enabled in (False, True):
        scene = flat_scene(renderer, (0.37, 0.21, 0.6, 1))
        if enabled:
            setattr(scene, option, value)
        images.append(render_image(renderer, scene, 64))
        scene.close()
    np.testing.assert_array_equal(images[1], images[0])
    assert (images[0] == images[0][0, 0]).all()


def test_shadows_do_not_change_an_unshadowed_lit_scene(renderer, triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    images = []
    for shadows in (False, True):
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        scene.camera = camera
        scene.background = (0.1, 0.2, 0.3, 1)
        # The light shines along the view axis onto the triangle, and nothing lies between them.
        light = scene.add_directional_light(direction=(0, 0, -1), intensity=50000)
        light.casts_shadows = True
        scene.shadows = shadows
        scene.load(pack(doc, binary))
        images.append(render_image(renderer, scene, 64))
        scene.close()
    np.testing.assert_array_equal(images[1], images[0])


def test_default_output_does_not_depend_on_alpha_mode(renderer, scene, triangle_glb):
    """The exact path stays selected whatever the materials are, so a frame never changes by the
    one-level rounding difference between the two output paths."""
    doc, binary = unpack(triangle_glb)
    images = {}
    for mode in ("OPAQUE", "BLEND", "MASK"):
        doc["materials"][0]["alphaMode"] = mode
        model = scene.load(pack(doc, binary))
        model.material("red").base_color = (0.5, 0.5, 0.5, 1)
        images[mode] = render_image(renderer, scene)
        assert scene.output_path == "exact"
        model.close()
    np.testing.assert_array_equal(images["OPAQUE"][SIZE // 2, SIZE // 2], [188, 188, 188, 255])
    np.testing.assert_array_equal(images["BLEND"], images["OPAQUE"])
    np.testing.assert_array_equal(images["MASK"], images["OPAQUE"])


CASES = [("OPAQUE", "lit"), ("BLEND", "lit"), ("BLEND", "unlit"), ("OPAQUE", "unlit"),
         ("MASK", "unlit"), ("MASK", "lit")]


@pytest.mark.parametrize("path", ["exact", "direct"])
@pytest.mark.parametrize("mode,shading", CASES)
def test_opaque_view_stores_alpha_one(renderer, scene, triangle_glb, mode, shading, path):
    """A scene that is not transparent stores alpha one on both output paths."""
    if path == "direct" and mode == "MASK":
        pytest.skip("MASK materials need the exact path; see test_direct_output_rejects_mask")
    doc, binary = unpack(triangle_glb)
    if shading == "lit":
        lit(doc)
    doc["materials"][0]["alphaMode"] = mode
    scene.output_path = path
    scene.background = (0.2, 0.2, 0.2, 0.3)
    model = scene.load(pack(doc, binary))
    # Filament's unlit shader outputs this alpha for OPAQUE; the generated shader replaces it.
    model.material("red").base_color = (1, 0, 0, 0.6)
    image = render_image(renderer, scene)
    assert image[SIZE // 2, SIZE // 2, 3] == 255 and image[0, 0, 3] == 255


def test_viewport_without_clear_encodes_only_the_viewport(renderer, triangle_glb):
    scene = flat_scene(renderer, (0.5, 0.5, 0.5, 1))
    target = renderer.create_render_target(width=32, height=32)
    other = flat_scene(renderer, (0.2, 0.2, 0.2, 1))
    renderer.render(other, target)
    renderer.render(scene, target, viewport=(8, 8, 16, 16), clear=False)
    image = target.read().astype(int)
    target.close()
    np.testing.assert_array_equal(image[16, 16], [188, 188, 188, 255])
    np.testing.assert_array_equal(image[2, 2], [124, 124, 124, 255])
    np.testing.assert_array_equal(image[30, 30], [124, 124, 124, 255])


def test_direct_output_rejects_mask(renderer, scene, triangle_glb):
    """Filament writes the sharpened edge alpha of MASK materials to the target."""
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "MASK"
    masked = pack(doc, binary)
    scene.output_path = "direct"
    with pytest.raises(filly.AssetError, match="MASK.*output_path is 'direct'"):
        scene.load(masked)
    with pytest.raises(filly.AssetError, match="MASK"):
        scene.create_mesh(**filly.shapes.plane(1, 1), alpha_mode="mask")
    scene.output_path = "exact"
    model = scene.load(masked)
    with pytest.raises(ValueError, match="MASK"):
        scene.output_path = "direct"
    assert scene.output_path == "exact"
    model.close()
    scene.output_path = "direct"


# Every scene option that the direct path cannot render, with a value that turns it on.
EXACT_OPTIONS = [("tone_mapping", "aces"), ("antialiasing", "fxaa"), ("msaa", 4),
                 ("refraction", True), ("transparent", True), ("dithering", True), ("ssao", True),
                 ("bloom", True), ("depth_of_field", True), ("vignette", True)]


@pytest.mark.parametrize("option,value", EXACT_OPTIONS)
def test_direct_output_rejects_options_that_need_the_exact_path(scene, option, value):
    default = getattr(scene, option)
    scene.output_path = "direct"
    with pytest.raises(ValueError, match=f"{option}.*output_path is 'direct'"):
        setattr(scene, option, value)
    assert getattr(scene, option) == default
    scene.output_path = "exact"
    setattr(scene, option, value)
    with pytest.raises(ValueError, match=f"output_path 'direct'.*{option}"):
        scene.output_path = "direct"
    assert scene.output_path == "exact"
    setattr(scene, option, default)
    scene.output_path = "direct"


def test_direct_output_options(renderer, scene, triangle_glb):
    assert scene.output_path == "exact"
    with pytest.raises(ValueError, match="output_path"):
        scene.output_path = "graded"
    scene.output_path = "direct"
    # These work without postprocessing: fog is shader-side (see test_effects.py) and shadows
    # are a separate pass.
    scene.fog = True
    scene.fog = False
    scene.shadows = True
    scene.encoding = "linear"
    scene.tone_mapping = "linear"
    material = scene.load(triangle_glb).material("red")
    np.testing.assert_allclose(grey_pixel(renderer, scene, material, 0.5), [128, 128, 128, 255], atol=1)


def test_non_neutral_tone_mappers_remain_available(renderer, scene, triangle_glb):
    material = scene.load(triangle_glb).material("red")
    scene.tone_mapping = "aces_legacy"
    pixel = grey_pixel(renderer, scene, material, 0.5)
    # gltf_viewer's default tints neutral grey; it stays an explicit choice.
    assert pixel[0] - pixel[2] >= 3, pixel
    # Back to linear: Filament's postprocessing is off again, and the output is exact.
    scene.tone_mapping = "linear"
    np.testing.assert_array_equal(grey_pixel(renderer, scene, material, 0.5), [188, 188, 188, 255])


# A chart of unlit patches: 12 greys, then 36 colors from a fixed seed. VIEWER holds what
# gltf_viewer 1.77.1 stored for them with ACES legacy tone mapping (tools/compare_reference.py,
# Intel Iris Xe, September 30, 2026). The earlier route, Filament's linear 3D LUT output
# encoded by filly, differed from it in 44 of these 144 channels, by up to 6 levels.
CHART_COLUMNS, CHART_ROWS = 8, 6
VIEWER = [[2, 2, 2], [4, 3, 3], [7, 7, 7], [13, 13, 13], [22, 23, 23], [37, 37, 38], [59, 59, 60],
          [90, 91, 92], [130, 130, 131], [173, 172, 171], [208, 205, 201], [230, 226, 219],
          [193, 219, 202], [52, 71, 215], [108, 212, 204], [122, 71, 63], [50, 123, 143],
          [192, 226, 205], [200, 225, 109], [24, 175, 5], [0, 147, 132], [225, 182, 154],
          [131, 50, 2], [70, 193, 57], [91, 0, 211], [23, 59, 216], [171, 214, 183], [208, 0, 153],
          [173, 216, 124], [166, 0, 100], [76, 18, 210], [169, 225, 179], [176, 181, 186],
          [0, 120, 51], [113, 1, 224], [71, 188, 86], [223, 188, 54], [218, 222, 214],
          [147, 10, 35], [231, 165, 56], [220, 184, 167], [97, 111, 52], [117, 218, 148],
          [158, 80, 199], [0, 95, 0], [146, 225, 188], [129, 151, 212], [101, 169, 187]]


def chart_colors():
    greys = np.geomspace(0.002, 1.0, 12)
    colors = np.random.default_rng(7).random((CHART_COLUMNS * CHART_ROWS - len(greys), 3)) ** 2.2
    return np.round(np.vstack([np.repeat(greys[:, None], 3, 1), colors]), 4)


def chart_glb(colors):
    """One unlit unit square per color, in rows of CHART_COLUMNS centered on the origin."""
    binary, views, accessors, meshes, materials = b"", [], [], [], []
    for i, color in enumerate(colors):
        x, y = i % CHART_COLUMNS - CHART_COLUMNS / 2, CHART_ROWS / 2 - 1 - i // CHART_COLUMNS
        corners = np.float32([(x, y, 0), (x + 1, y, 0), (x + 1, y + 1, 0), (x, y + 1, 0)])
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": corners.nbytes})
        binary += corners.tobytes()
        accessors.append({"bufferView": i, "componentType": 5126, "count": 4, "type": "VEC3",
                          "min": corners.min(0).tolist(), "max": corners.max(0).tolist()})
        materials.append({"extensions": {"KHR_materials_unlit": {}},
                          "pbrMetallicRoughness": {"baseColorFactor": [*map(float, color), 1]}})
        meshes.append({"primitives": [{"attributes": {"POSITION": i}, "indices": len(colors), "material": i}]})
    views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": 12})
    binary += np.uint16([0, 1, 2, 0, 2, 3]).tobytes()
    accessors.append({"bufferView": len(colors), "componentType": 5123, "count": 6, "type": "SCALAR"})
    document = {"asset": {"version": "2.0"}, "extensionsUsed": ["KHR_materials_unlit"], "scene": 0,
                "scenes": [{"nodes": list(range(len(colors)))}],
                "nodes": [{"mesh": i} for i in range(len(colors))], "meshes": meshes,
                "materials": materials, "accessors": accessors, "bufferViews": views,
                "buffers": [{"byteLength": len(binary)}]}
    return pack(document, binary)


def render_chart(renderer, tone_mapping="aces_legacy", alpha=1.0, **options):
    """RGBA of each chart patch's center pixel, 32 pixels per patch."""
    scene = renderer.create_scene()
    scene.tone_mapping = tone_mapping
    for name, value in options.items():
        setattr(scene, name, value)
    camera = scene.create_camera()
    camera.set_orthographic(left=-CHART_COLUMNS / 2, right=CHART_COLUMNS / 2,
                            bottom=-CHART_ROWS / 2, top=CHART_ROWS / 2, near=0.1, far=10)
    camera.position = (0, 0, 3)
    scene.camera = camera
    doc, binary = unpack(chart_glb(chart_colors()))
    if alpha < 1:
        for material in doc["materials"]:
            material["pbrMetallicRoughness"]["baseColorFactor"][3] = alpha
            material["alphaMode"] = "BLEND"
    scene.load(pack(doc, binary))
    target = renderer.create_render_target(width=32 * CHART_COLUMNS, height=32 * CHART_ROWS)
    renderer.render(scene, target)
    image = target.read()
    target.close()
    scene.close()
    return image[16::32, 16::32].reshape(-1, image.shape[-1]).astype(int)


def test_channel_mixing_tone_mapper_matches_gltf_viewer(renderer):
    """ACES legacy mixes channels, so Filament's color grading encodes and rounds, as in
    gltf_viewer; filly's encode pass must not encode again."""
    actual = render_chart(renderer)[:, :3]
    mismatched = np.argwhere(actual != np.array(VIEWER))
    # Another rasterizer's LUT filtering can move a level: llvmpipe moved 3 channels, Intel none.
    # The earlier route moved 44 channels, by up to 6 levels.
    assert len(mismatched) <= 8, [(i, actual[i].tolist(), VIEWER[i]) for i, _ in mismatched]
    assert np.abs(actual - np.array(VIEWER)).max() <= 1


@pytest.mark.parametrize("option,value", [("antialiasing", "fxaa"), ("msaa", 4)])
def test_channel_mixing_route_is_identical_with_other_options(renderer, option, value):
    np.testing.assert_array_equal(render_chart(renderer, **{option: value}), render_chart(renderer))


def test_channel_mixing_transparent_output_is_premultiplied(renderer):
    """Filament's translucent color grading also stores srgb(c) * a."""
    opaque = render_chart(renderer)[:, :3]
    for alpha in (0.25, 0.75):
        pixels = render_chart(renderer, alpha=alpha, transparent=True, background=(0, 0, 0, 0))
        np.testing.assert_allclose(pixels[:, 3], 255 * alpha, atol=0.5)
        assert np.abs(pixels[:, :3] - opaque * alpha).max() <= 1


def test_channel_mixing_linear_encoding_stays_linear(renderer):
    """With linear encoding, Filament's color grading writes linear color and filly stores it."""
    srgb_levels = render_chart(renderer)[:, :3]
    linear_levels = render_chart(renderer, encoding="linear")[:, :3]
    # Dark greys are far below their sRGB levels, and where 8-bit linear levels are fine enough,
    # encoding them lands within the 3 levels by which Filament's linear LUT output differs.
    assert linear_levels[5, 0] < srgb_levels[5, 0] - 20
    bright = linear_levels >= 32
    assert np.abs(255 * srgb(linear_levels / 255) - srgb_levels)[bright].max() <= 3


def test_channel_mixing_dithering_is_filaments(renderer):
    """Filament dithers before its 8-bit rounding, so a flat field varies by at most one level."""
    plain = render_chart(renderer)
    dithered = render_chart(renderer, dithering=True)
    assert np.abs(dithered[:, :3] - plain[:, :3]).max() <= 1


@pytest.mark.parametrize("clear", [True, False])
def test_channel_mixing_viewport(renderer, clear):
    """Inside the viewport the output is Filament's graded image. Outside it, a clear stores the
    encoded background, not tone mapped, as the linear route does; without a clear the earlier
    image stays."""
    scene = flat_scene(renderer, (0.5, 0.5, 0.5, 1))
    scene.tone_mapping = "aces_legacy"
    scene.background = (0.2, 0.2, 0.2, 1)
    full = render_image(renderer, scene, size=32).astype(int)
    target = renderer.create_render_target(width=32, height=32)
    renderer.render(flat_scene(renderer, (0.05, 0.05, 0.05, 1)), target)
    renderer.render(scene, target, viewport=(8, 8, 16, 16), clear=clear)
    image = target.read().astype(int)
    target.close()
    np.testing.assert_array_equal(image[16, 16], full[16, 16])
    outside = [124, 124, 124, 255] if clear else [63, 63, 63, 255]
    np.testing.assert_array_equal(image[2, 2], outside)
    np.testing.assert_array_equal(image[30, 30], outside)
