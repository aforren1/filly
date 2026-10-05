"""glTF preparation runs natively: WebP through libwebp, Unicode resource paths, and no Python
preparation modules."""

import base64
import json
from pathlib import Path
import struct
import subprocess
import sys
import textwrap

import numpy as np
import pytest

import filly
from test_features import pack

# 2 x 2 lossless WebP: red, green (top row); blue, white (bottom row).
QUADRANTS = (Path(__file__).parent / "data" / "quadrants.webp").read_bytes()
EXPECTED = {"top left": (255, 0, 0), "top right": (0, 255, 0), "bottom left": (0, 0, 255), "bottom right": (255, 255, 255)}


def quad_document(image):
    """An unlit quad over the fixture camera's view, sampling `image` with nearest filtering."""
    positions = struct.pack("<12f", -1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
    # glTF UV (0, 0) is the top-left corner of the image.
    uvs = struct.pack("<8f", 0, 1, 1, 1, 1, 0, 0, 0)
    indices = struct.pack("<6H", 0, 1, 2, 0, 2, 3) + b"\0\0"
    binary = bytearray(positions + uvs + indices)
    doc = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "extensionsUsed": ["KHR_materials_unlit", "EXT_texture_webp"],
        "extensionsRequired": ["EXT_texture_webp"],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"extensions": {"KHR_materials_unlit": {}},
                       "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "samplers": [{"magFilter": 9728, "minFilter": 9728, "wrapS": 33071, "wrapT": 33071}],
        "textures": [{"sampler": 0, "extensions": {"EXT_texture_webp": {"source": 0}}}],
        "images": [image],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 48}, {"buffer": 0, "byteOffset": 48, "byteLength": 32},
                        {"buffer": 0, "byteOffset": 80, "byteLength": 12}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]},
                      {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
                      {"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"}],
    }
    return doc, binary


def quadrants(renderer, scene):
    target = renderer.create_render_target(width=32, height=32)
    renderer.render(scene, target)
    pixels = target.read()
    return {"top left": tuple(pixels[8, 8, :3]), "top right": tuple(pixels[8, 24, :3]),
            "bottom left": tuple(pixels[24, 8, :3]), "bottom right": tuple(pixels[24, 24, :3])}


@pytest.mark.gpu
@pytest.mark.parametrize("embedding", ["buffer view", "data uri"])
def test_webp_texture_decodes_known_pixels(renderer, scene, embedding):
    if embedding == "buffer view":
        doc, binary = quad_document({"bufferView": 3, "mimeType": "image/webp"})
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(QUADRANTS)})
        binary += QUADRANTS
    else:
        # Without a mimeType; the data URI names the type.
        doc, binary = quad_document({"uri": "data:image/webp;base64," + base64.b64encode(QUADRANTS).decode()})
    scene.load(pack(doc, binary), strict=True)
    assert quadrants(renderer, scene) == EXPECTED


@pytest.mark.gpu
def test_unicode_path_with_external_buffer_and_image(renderer, scene, tmp_path):
    folder = tmp_path / "ünïcødé ассет"
    folder.mkdir()
    doc, binary = quad_document({"uri": "t%C3%A9xture.webp"})
    (folder / "tëxture.webp").write_bytes(QUADRANTS)
    (folder / "gëometry.bin").write_bytes(bytes(binary))
    doc["buffers"][0]["uri"] = "g%C3%ABometry.bin"
    doc["images"][0]["uri"] = "t%C3%ABxture.webp"
    path = folder / "quad.gltf"
    path.write_text(json.dumps(doc), encoding="utf-8")
    model = scene.load(path, strict=True)
    assert quadrants(renderer, scene) == EXPECTED
    model.close()
    # Byte sources must embed every resource.
    with pytest.raises(filly.AssetError, match="Byte assets must contain all resources"):
        scene.load(path.read_bytes())


@pytest.mark.gpu
def test_python_has_no_gltf_preparation(triangle_glb):
    script = textwrap.dedent("""
        import sys
        import filly
        filly.set_log_level("off")
        with filly.Renderer() as renderer:
            renderer.create_scene().load(sys.stdin.buffer.read())
            renderer.create_scene().create_mesh(**filly.shapes.box())
        print(sorted(name for name in sys.modules if name.startswith("filly")))
    """)
    result = subprocess.run([sys.executable, "-c", script], input=triangle_glb, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-400:]
    assert result.stdout.decode().strip() == "['filly', 'filly._native', 'filly._native.shapes', 'filly.samples', 'filly.shapes']"
    package = Path(filly.__file__).parent
    for name in ("_assets", "_animation", "_geometry", "_accessors", "_mesh", "shapes"):
        assert not (package / f"{name}.py").exists()


def test_shapes_are_native_arrays():
    shape = filly.shapes.uv_sphere(0.5, segments=8, rings=4)
    assert filly.shapes.__name__ == "filly._native.shapes"
    assert shape["positions"].dtype == np.float32 and shape["positions"].shape == (45, 3)
    assert shape["indices"].dtype == np.uint32 and shape["indices"].shape == (48, 3)
    np.testing.assert_allclose(np.linalg.norm(shape["positions"], axis=1), 0.5, rtol=1e-6)
    with pytest.raises(ValueError, match="uv_sphere needs"):
        filly.shapes.uv_sphere(0)
