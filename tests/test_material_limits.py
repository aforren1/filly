"""Texture-count limits and precompiled-archive gaps must fail or fall back, never abort."""

import json
import struct
import subprocess
import sys
import textwrap
import zlib

import numpy as np
import pytest

import filly
from filly._assets import MAX_LIT_TEXTURES, prepare

pytestmark = pytest.mark.gpu

# The nine-texture material from the preflight's documentation, then two specular textures.
SLOTS = [
    ("pbrMetallicRoughness", "baseColorTexture"), ("pbrMetallicRoughness", "metallicRoughnessTexture"),
    (None, "normalTexture"), (None, "occlusionTexture"), (None, "emissiveTexture"),
    ("KHR_materials_clearcoat", "clearcoatTexture"), ("KHR_materials_clearcoat", "clearcoatRoughnessTexture"),
    ("KHR_materials_sheen", "sheenColorTexture"), ("KHR_materials_sheen", "sheenRoughnessTexture"),
    ("KHR_materials_specular", "specularTexture"), ("KHR_materials_specular", "specularColorTexture"),
]


def textured_asset(count, extensions=None, name="many"):
    """A lit triangle whose material samples one PNG in `count` texture slots."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    image = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(b"\x00\xc0\x80\x40\xff")) + chunk(b"IEND", b""))
    geometry = struct.pack("<9f", -0.6, -0.4, 0, 0.6, -0.4, 0, 0, 0.8, 0) + struct.pack("<9f", *(0, 0, 1) * 3)
    geometry += struct.pack("<6f", 0, 0, 1, 0, 0.5, 1)
    binary = geometry + image + b"\0" * (-len(image) % 4)
    material = {"name": name, "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 0.5},
                "extensions": {key: dict(value) for key, value in (extensions or {}).items()}}
    for parent, key in SLOTS[:count]:
        node = material if parent is None else (material["pbrMetallicRoughness"] if parent == "pbrMetallicRoughness"
                                                else material["extensions"].setdefault(parent, {}))
        node[key] = {"index": 0}
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "material": 0}]}],
           "materials": [material], "textures": [{"source": 0}],
           "images": [{"bufferView": 3, "mimeType": "image/png"}], "buffers": [{"byteLength": len(binary)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 36},
                           {"buffer": 0, "byteOffset": 72, "byteLength": 24},
                           {"buffer": 0, "byteOffset": 96, "byteLength": len(image)}],
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


def render_in_subprocess(asset, mode, strict=False):
    """Load and render in a child process, so a native abort fails one test instead of pytest."""
    script = textwrap.dedent(f"""
        import sys, warnings
        import filly
        filly.set_log_level("off")
        warnings.simplefilter("ignore")
        with filly.Renderer(precompiled_shaders={mode == "precompiled"}) as renderer:
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


@pytest.mark.parametrize("count", [MAX_LIT_TEXTURES, MAX_LIT_TEXTURES + 1])
def test_compiled_texture_limit_is_measured(count):
    output = render_in_subprocess(textured_asset(count), "compiled")
    if count <= MAX_LIT_TEXTURES:
        assert output == "rendered"
    else:
        assert "'many' uses 9 textures" in output and "precompiled_shaders=True" in output


def test_preflight_names_material_and_suggests_precompiled_shaders():
    with pytest.raises(filly.AssetError, match=r"Material 'rough coat' uses 9 textures.*precompiled_shaders=True"):
        prepare(textured_asset(9, name="rough coat"), "", precompiled=False)
    # Filament's precompiled materials drop sheen for clearcoat, so precompiled shaders can render it.
    with pytest.warns(filly.AssetCompatibilityWarning, match="without some of its features"):
        prepare(textured_asset(9), "", precompiled=True)


@pytest.mark.parametrize("count", [9, 11])
def test_precompiled_shaders_renders_materials_over_the_limit(count):
    assert render_in_subprocess(textured_asset(count), "precompiled") == "rendered"


@pytest.mark.parametrize("extensions", [
    {"KHR_materials_transmission": {"transmissionFactor": 1}},
    {"KHR_materials_iridescence": {"iridescenceFactor": 1}},
])
@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
def test_generated_materials_report_the_limit(extensions, mode):
    iridescent = "KHR_materials_iridescence" in extensions
    allowed = MAX_LIT_TEXTURES - 3 if iridescent else MAX_LIT_TEXTURES
    assert render_in_subprocess(textured_asset(allowed, extensions), mode) == "rendered"
    output = render_in_subprocess(textured_asset(allowed + 1, extensions), mode)
    assert output.startswith("AssetError: Material 'many'"), output


@pytest.mark.parametrize("extensions", [
    # No precompiled material has sheen, specular, and IOR; the SDK's fallback then aborted.
    {"KHR_materials_sheen": {"sheenColorFactor": [1, 1, 1]}, "KHR_materials_specular": {}, "KHR_materials_ior": {"ior": 1.4}},
    # The precompiled clearcoat material ignores IOR.
    {"KHR_materials_clearcoat": {"clearcoatFactor": 1}, "KHR_materials_ior": {"ior": 2.0}},
])
def test_precompiled_shaders_compiles_archive_gaps(extensions):
    assert render_in_subprocess(textured_asset(0, extensions), "precompiled") == "rendered"
    images = {}
    for mode in ("compiled", "precompiled"):
        with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
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
            images[mode] = target.read()
    assert images["compiled"][16, 16, :3].max() > 20
    np.testing.assert_allclose(images["precompiled"], images["compiled"], atol=1)
