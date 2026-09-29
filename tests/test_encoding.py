"""Output encoding and output path: sRGB through color grading by default, linear and the direct
path on request, and independent of other options."""

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


def grey_pixel(renderer, scene, material, value, alpha=1.0):
    material.base_color = (value, value, value, alpha)
    target = renderer.create_render_target(width=SIZE, height=SIZE)
    renderer.render(scene, target)
    pixel = target.read()[SIZE // 2, SIZE // 2].astype(int)
    target.close()
    return pixel


def test_default_encoding_is_srgb(renderer, scene, triangle_glb):
    material = scene.load(triangle_glb).material("red")
    assert scene.encoding == "srgb"
    np.testing.assert_allclose(grey_pixel(renderer, scene, material, 0.5), [188, 188, 188, 255], atol=1)


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
@pytest.mark.parametrize("path", ["direct", "graded"])
def test_encoding_matches_transfer_function(renderer, scene, triangle_glb, encoding, path):
    scene.encoding = encoding
    scene.output_path = path
    material = scene.load(triangle_glb).material("red")
    values = SWEEP
    expected = np.round(255 * (srgb(values) if encoding == "srgb" else values))
    actual = np.array([grey_pixel(renderer, scene, material, value)[:3] for value in values])
    error = np.abs(actual - expected[:, None])
    assert error.max() <= 1, (values[error.max(axis=1).argmax()], error.max())
    # Neutral in, neutral out.
    assert np.all(actual.max(axis=1) - actual.min(axis=1) <= 0)


# Each option alone. Bloom is excluded because it changes the image by design.
OTHER_OPTIONS = [("antialiasing", "fxaa"), ("msaa", 4), ("refraction", True), ("shadows", True),
                 ("ssao", True), ("transparent", True), ("dithering", True),
                 ("tone_mapping", "linear")]


@pytest.mark.parametrize("encoding,expected", [("srgb", 188), ("linear", 128)])
@pytest.mark.parametrize("option,value", OTHER_OPTIONS)
def test_encoding_is_independent_of_other_options(renderer, scene, triangle_glb, encoding, expected, option, value):
    scene.encoding = encoding
    material = scene.load(triangle_glb).material("red")
    setattr(scene, option, value)
    assert scene.encoding == encoding
    # Temporal dithering adds at most one level of noise.
    np.testing.assert_allclose(grey_pixel(renderer, scene, material, 0.5), [expected] * 3 + [255], atol=1)


@pytest.mark.parametrize("encoding,expected", [("srgb", 188), ("linear", 128)])
def test_transparent_output_is_premultiplied_after_encoding(renderer, scene, triangle_glb, encoding, expected):
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    scene.encoding = encoding
    scene.transparent = True
    scene.background = (0, 0, 0, 0)
    material = scene.load(pack(doc, binary)).material("red")
    # The encoded straight color times alpha: what ONE, ONE_MINUS_SRC_ALPHA blending expects.
    np.testing.assert_allclose(grey_pixel(renderer, scene, material, 0.5, alpha=0.5),
                               [expected / 2] * 3 + [128], atol=1)


def render_image(renderer, scene):
    target = renderer.create_render_target(width=SIZE, height=SIZE)
    renderer.render(scene, target)
    image = target.read()
    target.close()
    return image


def test_default_output_does_not_depend_on_alpha_mode(renderer, scene, triangle_glb):
    """Color grading stays selected whatever the materials are, so a frame never changes by the
    one-level rounding difference between the two output paths."""
    doc, binary = unpack(triangle_glb)
    images = {}
    for mode in ("OPAQUE", "BLEND", "MASK"):
        doc["materials"][0]["alphaMode"] = mode
        model = scene.load(pack(doc, binary))
        model.material("red").base_color = (0.5, 0.5, 0.5, 1)
        images[mode] = render_image(renderer, scene)
        assert scene.output_path == "graded"
        model.close()
    np.testing.assert_array_equal(images["OPAQUE"][SIZE // 2, SIZE // 2], [188, 188, 188, 255])
    np.testing.assert_array_equal(images["BLEND"], images["OPAQUE"])
    np.testing.assert_array_equal(images["MASK"], images["OPAQUE"])


CASES = [("OPAQUE", "lit"), ("BLEND", "lit"), ("BLEND", "unlit"), ("OPAQUE", "unlit"),
         ("MASK", "unlit"), ("MASK", "lit")]


@pytest.mark.parametrize("path", ["graded", "direct"])
@pytest.mark.parametrize("mode,shading", CASES)
def test_opaque_view_stores_alpha_one(renderer, scene, triangle_glb, mode, shading, path):
    """A scene that is not transparent stores alpha one on both output paths."""
    if path == "direct" and mode == "MASK":
        pytest.skip("MASK materials need color grading; see test_direct_output_rejects_mask")
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
    scene.output_path = "graded"
    model = scene.load(masked)
    with pytest.raises(ValueError, match="MASK"):
        scene.output_path = "direct"
    assert scene.output_path == "graded"
    model.close()
    scene.output_path = "direct"


# Every scene option that needs Filament's postprocessing, with a value that turns it on.
GRADED_OPTIONS = [("tone_mapping", "aces"), ("antialiasing", "fxaa"), ("msaa", 4),
                  ("refraction", True), ("transparent", True), ("dithering", True), ("ssao", True),
                  ("bloom", True), ("depth_of_field", True), ("vignette", True)]


@pytest.mark.parametrize("option,value", GRADED_OPTIONS)
def test_direct_output_rejects_options_that_need_grading(scene, option, value):
    default = getattr(scene, option)
    scene.output_path = "direct"
    with pytest.raises(ValueError, match=f"{option}.*output_path is 'direct'"):
        setattr(scene, option, value)
    assert getattr(scene, option) == default
    scene.output_path = "graded"
    setattr(scene, option, value)
    with pytest.raises(ValueError, match=f"output_path 'direct'.*{option}"):
        scene.output_path = "direct"
    assert scene.output_path == "graded"
    setattr(scene, option, default)
    scene.output_path = "direct"


def test_direct_output_options(renderer, scene, triangle_glb):
    assert scene.output_path == "graded"
    with pytest.raises(ValueError, match="output_path"):
        scene.output_path = "gpu"
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
