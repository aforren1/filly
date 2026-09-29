"""Tone mappers, optional postprocessing effects, sun lights, lens projection, and KTX environments."""

import math
import struct
from pathlib import Path

import numpy as np
import pytest

import filly
from test_features import lit, pack, unpack

pytestmark = pytest.mark.gpu

LIGHTROOM = Path(__file__).resolve().parents[1] / ".deps/bin/assets/ibl/lightroom_14b"
TONE_MAPPERS = ["linear", "aces_legacy", "aces", "filmic", "pbr_neutral", "gt7", "agx", "agx_punchy",
                "agx_golden", "generic", "display_range"]


def sphere_glb(*, roughness=0.3, segments=48, backdrop=False):
    """A lit white UV sphere of radius 0.8, optionally touching a square backdrop at z = -0.8."""
    rows = []
    for i in range(segments + 1):
        theta = math.pi * i / segments
        for j in range(segments + 1):
            phi = 2 * math.pi * j / segments
            rows.append((math.sin(theta) * math.cos(phi), math.cos(theta), math.sin(theta) * math.sin(phi)))
    normals = np.asarray(rows, dtype="<f4")
    indices = []
    for i in range(segments):
        for j in range(segments):
            a, b = i * (segments + 1) + j, (i + 1) * (segments + 1) + j
            indices += [a, a + 1, b, b, a + 1, b + 1]
    positions = normals * 0.8
    if backdrop:
        start = len(normals)
        normals = np.concatenate([normals, np.tile(np.array([[0, 0, 1]], dtype="<f4"), (4, 1))])
        corners = np.array([[-3, -3, -0.8], [3, -3, -0.8], [3, 3, -0.8], [-3, 3, -0.8]], dtype="<f4")
        positions = np.concatenate([positions, corners]).astype("<f4")
        indices += [start, start + 1, start + 2, start, start + 2, start + 3]
    indices = np.asarray(indices, dtype="<u4")
    binary = positions.tobytes() + normals.tobytes() + indices.tobytes()
    size = positions.nbytes
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
           "materials": [{"name": "white", "pbrMetallicRoughness": {
               "baseColorFactor": [0.8, 0.8, 0.8, 1], "metallicFactor": 0, "roughnessFactor": roughness}}],
           "buffers": [{"byteLength": len(binary)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": size},
                           {"buffer": 0, "byteOffset": size, "byteLength": size},
                           {"buffer": 0, "byteOffset": 2 * size, "byteLength": indices.nbytes}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
                          "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
                         {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5125, "count": len(indices), "type": "SCALAR"}]}
    return pack(doc, bytearray(binary))


def perspective_scene(renderer, **options):
    scene = renderer.create_scene()
    for name, value in options.items():
        setattr(scene, name, value)
    camera = scene.create_camera()
    camera.set_perspective(fov_y=45, near=0.1, far=20)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    return scene


def render(renderer, scene, size=64):
    target = renderer.create_render_target(width=size, height=size)
    renderer.render(scene, target)
    pixels = target.read()
    target.close()
    return pixels


# Default, another valid value, and an invalid value (None when only bool conversion applies).
OPTIONS = {
    "encoding": ("srgb", "linear", "sRGB"),
    "tone_mapping": ("linear", "aces_legacy", "ACES"),
    "antialiasing": ("none", "fxaa", "taa"),
    "msaa": (1, 4, 3),
    "shadows": (False, True, None),
    "refraction": (False, True, None),
    "transparent": (False, True, None),
    "dithering": (False, True, None),
    "ssao": (False, True, None),
    "bloom": (False, True, None),
}


def test_option_defaults_and_independent_assignment(renderer):
    scene = renderer.create_scene()
    names = list(OPTIONS)
    for name, (default, _, _) in OPTIONS.items():
        assert getattr(scene, name) == default, name
    # Each assignment changes only its own option.
    for position, name in enumerate(names):
        setattr(scene, name, OPTIONS[name][1])
        for other, check in enumerate(names):
            expected = OPTIONS[check][1] if other <= position else OPTIONS[check][0]
            assert getattr(scene, check) == expected, (name, check)
    assert not hasattr(scene, "postprocessing") and not hasattr(scene, "set_rendering_options")


@pytest.mark.parametrize("name", [name for name, values in OPTIONS.items() if values[2] is not None])
def test_invalid_options_raise_and_keep_value(renderer, name):
    scene = renderer.create_scene()
    before = getattr(scene, name)
    with pytest.raises(ValueError, match=name):
        setattr(scene, name, OPTIONS[name][2])
    assert getattr(scene, name) == before
    with pytest.raises(TypeError):
        scene.msaa = True


def test_default_tone_mapping_is_linear_and_neutral(renderer):
    scene = perspective_scene(renderer)
    assert scene.tone_mapping == "linear"
    scene.load(sphere_glb())
    scene.add_directional_light(direction=(0.3, -0.5, -1), intensity=80000)
    image = render(renderer, scene).astype(int)
    lit = image[:, :, :3].max(axis=2) > 0
    # A grey sphere under white light stays grey under the default tone mapper.
    assert lit.sum() > 100 and np.abs(image[:, :, 0] - image[:, :, 2])[lit].max() <= 1
    scene.tone_mapping = "aces_legacy"
    tinted = render(renderer, scene).astype(int)
    assert (tinted[:, :, 0] - tinted[:, :, 2]).max() >= 3


def test_tone_mappers_are_distinct(renderer):
    scene = perspective_scene(renderer)
    scene.load(sphere_glb())
    # A range of luminance from shadow to highlight separates the curves.
    scene.add_directional_light(direction=(0.3, -0.5, -1), intensity=150000)
    images = {}
    for name in TONE_MAPPERS:
        scene.tone_mapping = name
        images[name] = render(renderer, scene).astype(int)
    for name in TONE_MAPPERS[1:]:
        assert np.abs(images[name] - images["linear"]).max() > 4, name
    assert np.abs(images["aces"] - images["aces_legacy"]).max() > 4
    assert np.abs(images["agx"] - images["agx_punchy"]).max() > 4
    with pytest.raises(ValueError, match="aces_legacy"):
        scene.tone_mapping = "ACES"


@pytest.mark.parametrize("effect", ["dithering", "ssao", "bloom"])
def test_postprocessing_effects_are_opt_in(renderer, effect):
    def image(**options):
        scene = perspective_scene(renderer, tone_mapping="aces_legacy", **options)
        scene.background = (0.2, 0.2, 0.2, 1)
        scene.load(sphere_glb(roughness=0.2, backdrop=True))
        scene.set_environment(np.full((8, 16, 3), 0.5, dtype="f"), intensity=20000)
        # A light far above the display range gives bloom a highlight to spread.
        scene.add_directional_light(direction=(0.2, -0.3, -1), intensity=1e6 if effect == "bloom" else 30000)
        return render(renderer, scene, 96).astype(int)

    default = image()
    np.testing.assert_array_equal(image(**{effect: False}), default)
    changed = np.abs(image(**{effect: True}) - default).max(axis=2)
    assert changed.max() >= 1 and np.count_nonzero(changed) > 20, (changed.max(), np.count_nonzero(changed))


def test_sun_differs_from_directional_mainly_in_highlight(renderer):
    images = {}
    for kind in ("directional", "sun"):
        scene = perspective_scene(renderer, tone_mapping="aces_legacy")
        scene.load(sphere_glb(roughness=0.05))
        add = scene.add_sun_light if kind == "sun" else scene.add_directional_light
        light = add(direction=(-0.3, -0.4, -1), intensity=60000, color=(1, 1, 1))
        assert light.type == kind
        images[kind] = render(renderer, scene, 128).astype(int)
    difference = np.abs(images["sun"] - images["directional"]).max(axis=2)
    luminance = images["directional"][:, :, :3].sum(axis=2)
    peak = np.array(np.unravel_index(np.argmax(luminance), luminance.shape))
    largest = np.array(np.unravel_index(np.argmax(difference), difference.shape))
    assert difference.max() >= 8 and np.hypot(*(largest - peak)) < 4
    # Filament moves the light direction toward each pixel's reflection, within the disc.
    # Away from the highlight this changes diffuse shading only near the terminator.
    rows, cols = np.mgrid[:128, :128]
    assert difference[np.hypot(rows - peak[0], cols - peak[1]) > 8].max() <= 5


def test_sun_disc_appears_in_skybox(renderer):
    images = {}
    for kind in ("directional", "sun"):
        scene = perspective_scene(renderer, tone_mapping="aces_legacy")
        scene.set_environment(np.full((8, 16, 3), 0.05, dtype="f"), intensity=10000)
        scene.environment_visible = True
        # Light travels toward +z: the source lies straight ahead of the camera.
        add = scene.add_sun_light if kind == "sun" else scene.add_directional_light
        add(direction=(0, 0, 1), intensity=100000)
        images[kind] = render(renderer, scene).astype(int)
    assert images["sun"][32, 32, :3].min() > images["directional"][32, 32, :3].max() + 100
    np.testing.assert_array_equal(images["sun"][:4], images["directional"][:4])


def test_sun_light_options(scene):
    light = scene.add_sun_light(direction=(0, -1, 0), intensity=1000, angular_radius_deg=2, halo_size=4, halo_falloff=20)
    assert light.type == "sun" and light.intensity == pytest.approx(1000)
    with pytest.raises(ValueError, match="Range requires"):
        light.range = 5
    for bad in ({"angular_radius_deg": 0.1}, {"angular_radius_deg": 25}, {"halo_size": -1}, {"direction": (0, 0, 0)}):
        with pytest.raises(ValueError):
            scene.add_sun_light(**{"direction": (0, -1, 0), **bad})


def test_lens_projection_matches_filament_lens_model(scene):
    camera = scene.camera
    camera.set_lens_projection(focal_length_mm=28, aspect=1.5, near=0.1, far=100)
    lens = camera.projection
    # Filament's lens model uses a 24 mm sensor height.
    camera.set_perspective(fov_y=math.degrees(2 * math.atan(12 / 28)), aspect=1.5, near=0.1, far=100)
    np.testing.assert_allclose(lens, camera.projection, rtol=1e-5)
    for bad in ({"focal_length_mm": 0}, {"aspect": -1}, {"near": 2, "far": 1}):
        with pytest.raises(ValueError):
            camera.set_lens_projection(**{"focal_length_mm": 28, "aspect": 1, "near": 0.1, "far": 10, **bad})


def ktx_cubemap(path, size=4, levels=3, sh=(1.0, 0.5, 0.25), color=(0.5, 0.5, 0.5)):
    """Write a small RGBA16F KTX 1 cubemap in the layout cmgen uses, optionally with SH metadata."""
    metadata = b""
    if sh:
        text = "\n".join(" ".join(str(c * (1.0 if band == 0 else 0.0)) for c in sh) for band in range(9))
        entry = b"sh\0" + text.encode() + b"\0"
        metadata = struct.pack("<I", len(entry)) + entry + b"\0" * (-len(entry) % 4)
    # glType HALF_FLOAT, glFormat RGBA, glInternalFormat RGBA16F.
    header = (b"\xabKTX 11\xbb\r\n\x1a\n" + struct.pack("<13I", 0x04030201, 0x140B, 2, 0x1908, 0x881A, 0x1908,
                                                         size, size, 0, 0, 6, levels, len(metadata)))
    body = b""
    for level in range(levels):
        edge = max(size >> level, 1)
        face = np.empty((edge, edge, 4), dtype="<f2")
        face[...] = (*color, 1)
        body += struct.pack("<I", face.nbytes) + face.tobytes() * 6
    path.write_bytes(header + metadata + body)
    return path


def lit_triangle(triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    return pack(doc, binary)


def test_ktx_environment_uses_spherical_harmonics(renderer, scene, triangle_glb, tmp_path):
    scene.load(lit_triangle(triangle_glb))
    scene.load_environment_ktx(ktx_cubemap(tmp_path / "ibl.ktx"), intensity=30000)
    assert scene.environment_intensity == pytest.approx(30000)
    pixel = render(renderer, scene)[32, 32].astype(int)
    # The cubemap is gray and SH band 0 is warm, so a warm diffuse result shows SH irradiance.
    assert pixel[0] > pixel[1] > pixel[2] > 5, pixel
    scene.load_environment_ktx(ktx_cubemap(tmp_path / "blue.ktx", sh=(0.2, 0.4, 1.0)))
    pixel = render(renderer, scene)[32, 32].astype(int)
    assert pixel[2] > pixel[1] > pixel[0], pixel


def test_ktx_environment_errors(scene, tmp_path):
    with pytest.raises(filly.AssetError, match="no 'sh' metadata"):
        scene.load_environment_ktx(ktx_cubemap(tmp_path / "plain.ktx", sh=None))
    (tmp_path / "text.ktx").write_text("not a texture")
    with pytest.raises(filly.AssetError, match="not a KTX 1 file"):
        scene.load_environment_ktx(tmp_path / "text.ktx")
    with pytest.raises(filly.AssetError, match="Could not open"):
        scene.load_environment_ktx(tmp_path / "missing.ktx")
    truncated = ktx_cubemap(tmp_path / "cut.ktx").read_bytes()[:-40]
    (tmp_path / "cut.ktx").write_bytes(truncated)
    with pytest.raises(filly.AssetError):
        scene.load_environment_ktx(tmp_path / "cut.ktx")
    with pytest.raises(filly.AssetError, match="Skybox"):
        scene.load_environment_ktx(ktx_cubemap(tmp_path / "ibl.ktx"), tmp_path / "text.ktx")


@pytest.mark.skipif(not LIGHTROOM.is_dir(), reason="Filament SDK sample IBL is not in .deps")
def test_cmgen_environment_and_skybox(renderer, scene, triangle_glb):
    scene.load(lit_triangle(triangle_glb))
    scene.load_environment_ktx(LIGHTROOM / "lightroom_14b_ibl.ktx", LIGHTROOM / "lightroom_14b_skybox.ktx")
    scene.environment_visible = True
    first = render(renderer, scene)
    assert first[32, 32, :3].min() > 20 and first[2, 2, :3].max() > 20
    scene.environment_rotation = 90
    rotated = render(renderer, scene)
    assert np.abs(rotated[:8].astype(int) - first[:8]).max() > 10
    # Without a skybox file, the IBL's sharpest level is the background.
    scene.load_environment_ktx(LIGHTROOM / "lightroom_14b_ibl.ktx")
    assert render(renderer, scene)[2, 2, :3].max() > 20
    scene.clear_environment()
