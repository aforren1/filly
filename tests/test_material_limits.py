"""Materials with many textures and extension combinations must load and render, never abort."""

import json
import struct
import subprocess
import sys
import textwrap
import zlib

import pytest

import filly

pytestmark = pytest.mark.gpu

# Nine texture slots, which once exceeded the sampler limit of a compiled lit material, then two
# specular textures. Extension textures share the generic samplers of an archive entry; see
# docs/explanation/material-precompilation.md.
SLOTS = [
    ("pbrMetallicRoughness", "baseColorTexture"), ("pbrMetallicRoughness", "metallicRoughnessTexture"),
    (None, "normalTexture"), (None, "occlusionTexture"), (None, "emissiveTexture"),
    ("KHR_materials_clearcoat", "clearcoatTexture"), ("KHR_materials_clearcoat", "clearcoatRoughnessTexture"),
    ("KHR_materials_sheen", "sheenColorTexture"), ("KHR_materials_sheen", "sheenRoughnessTexture"),
    ("KHR_materials_specular", "specularTexture"), ("KHR_materials_specular", "specularColorTexture"),
]


def textured_asset(count, extensions=None, name="many", slots=None, distinct=False):
    """A lit triangle whose material samples one PNG in `count` texture slots, or a separate
    PNG in each slot with `distinct`."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    def png(value):
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes([0, 0xc0, 0x80, value, 0xff]))) + chunk(b"IEND", b""))
    slots = SLOTS[:count] if slots is None else slots
    images = [png(0x40 + i) for i in range(len(slots) if distinct else 1)]
    geometry = struct.pack("<9f", -0.6, -0.4, 0, 0.6, -0.4, 0, 0, 0.8, 0) + struct.pack("<9f", *(0, 0, 1) * 3)
    geometry += struct.pack("<6f", 0, 0, 1, 0, 0.5, 1)
    binary = geometry
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 36},
             {"buffer": 0, "byteOffset": 72, "byteLength": 24}]
    for image in images:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(image)})
        binary += image + b"\0" * (-len(image) % 4)
    material = {"name": name, "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 0.5},
                "extensions": {key: dict(value) for key, value in (extensions or {}).items()}}
    for i, (parent, key) in enumerate(slots):
        node = material if parent is None else (material["pbrMetallicRoughness"] if parent == "pbrMetallicRoughness"
                                                else material["extensions"].setdefault(parent, {}))
        node[key] = {"index": i if distinct else 0}
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "material": 0}]}],
           "materials": [material], "textures": [{"source": i} for i in range(len(images))],
           "images": [{"bufferView": 3 + i, "mimeType": "image/png"} for i in range(len(images))],
           "buffers": [{"byteLength": len(binary)}],
           "bufferViews": views,
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                          "min": [-0.6, -0.4, 0], "max": [0.6, 0.8, 0]},
                         {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC2"}],
           "extensionsUsed": sorted(material["extensions"])}
    encoded = json.dumps(doc).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def render_in_subprocess(asset, strict=False):
    """Load and render in a child process, so a native abort fails one test instead of pytest."""
    script = textwrap.dedent(f"""
        import sys, warnings
        import filly
        filly.set_log_level("off")
        warnings.simplefilter("ignore")
        with filly.Renderer() as renderer:
            scene = renderer.create_scene()
            scene.refraction = True
            scene.tone_mapping = "aces_legacy"
            camera = scene.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            scene.add_directional_light(direction=(0, 0, -1))
            try:
                scene.load(sys.stdin.buffer.read(), strict={strict})
            except filly.AssetError as error:
                print("AssetError:", error)
                sys.exit(0)
            target = renderer.create_render_target(width=16, height=16)
            renderer.render(scene, target)
            target.read()
        print("rendered")
    """)
    result = subprocess.run([sys.executable, "-c", script], input=asset, capture_output=True, timeout=120)
    assert result.returncode == 0, f"exit {result.returncode:#x}: {result.stderr.decode(errors='replace')[-400:]}"
    return result.stdout.decode().strip()


@pytest.mark.parametrize("count", [8, 9, 11])
def test_materials_with_many_textures_render(count):
    assert render_in_subprocess(textured_asset(count)) == "rendered"


@pytest.mark.parametrize("extensions", [
    {"KHR_materials_sheen": {"sheenColorFactor": [1, 1, 1]}, "KHR_materials_specular": {}, "KHR_materials_ior": {"ior": 1.4}},
    {"KHR_materials_clearcoat": {"clearcoatFactor": 1}, "KHR_materials_ior": {"ior": 2.0}},
])
def test_extension_combinations_render(extensions):
    """Combinations that Filament's own material archive lacked, which once aborted the process."""
    assert render_in_subprocess(textured_asset(0, extensions)) == "rendered"
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(0.3, -0.2, -1), intensity=100000)
        scene.load(textured_asset(0, extensions), strict=True)
        target = renderer.create_render_target(width=32, height=32)
        renderer.render(scene, target)
        assert target.read()[16, 16, :3].max() > 20


CLEARCOAT_SHEEN = [(None, "normalTexture"),
                   ("KHR_materials_clearcoat", "clearcoatTexture"), ("KHR_materials_clearcoat", "clearcoatRoughnessTexture"),
                   ("KHR_materials_clearcoat", "clearcoatNormalTexture"),
                   ("KHR_materials_sheen", "sheenColorTexture"), ("KHR_materials_sheen", "sheenRoughnessTexture")]


@pytest.mark.parametrize("extensions", [
    {"KHR_materials_iridescence": {"iridescenceFactor": 1}},
    {"KHR_materials_transmission": {"transmissionFactor": 1}},
])
def test_archive_roles_that_share_a_texture_share_a_sampler(extensions):
    """11 texture slots of one image need 2 extension samplers (linear and sRGB)."""
    assert render_in_subprocess(textured_asset(11, extensions), strict=True) == "rendered"


@pytest.mark.parametrize("extensions, capacity", [
    ({"KHR_materials_clearcoat": {"clearcoatFactor": 1}, "KHR_materials_sheen": {"sheenColorFactor": [1, 1, 1]}}, 4),
    ({"KHR_materials_clearcoat": {"clearcoatFactor": 1}, "KHR_materials_sheen": {"sheenColorFactor": [1, 1, 1]},
      "KHR_materials_transmission": {"transmissionFactor": 1}}, 3),
])
def test_archive_drops_the_least_important_extension_textures(extensions, capacity):
    """Five distinct extension textures: sheen roughness and clearcoat normal go first."""
    asset = textured_asset(0, extensions, name="coat", slots=CLEARCOAT_SHEEN, distinct=True)
    dropped = "clearcoatNormalTexture" if capacity == 4 else "sheenRoughnessTexture, clearcoatNormalTexture"
    message = f"Material 'coat' has 5 extension textures, but its shader has {capacity} extension texture samplers; " \
              f"it renders without its {dropped}"
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        scene.refraction = True
        with pytest.warns(filly.AssetCompatibilityWarning, match=message):
            scene.load(asset)
        with pytest.raises(filly.AssetError, match="renders without its"):
            scene.load(asset, strict=True)
    assert render_in_subprocess(asset) == "rendered"
