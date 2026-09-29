"""Render a reproducible Suzanne image for the README."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import urllib.request

from PIL import Image

import filly


REVISION = "c6a6bd13ab2b3c685c7903d03561b8a9392f38b8"
SOURCE = (f"https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/{REVISION}"
          "/Models/IridescenceSuzanne/glTF-Binary/IridescenceSuzanne.glb")
SHA256 = "866762602d60a0942b7608bfabf4e8686a29033636224f4b55c5e56abe8abf8f"
ROOT = Path(__file__).resolve().parents[1]


def suzanne_glb():
    """Extract one CC0 Suzanne mesh and give it a plain PBR material."""
    source = ROOT / ".deps" / "suzanne-source.glb"
    source.parent.mkdir(exist_ok=True)
    if not source.exists():
        urllib.request.urlretrieve(SOURCE, source)
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError(f"Suzanne checksum mismatch: {source}")
    json_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + json_size])
    binary = data[28 + json_size:]
    mesh = document["meshes"][0]
    for primitive in mesh["primitives"]:
        primitive["material"] = 0
    # The source demonstrates iridescence. A plain material makes this example independent of it.
    document = {
        "asset": {"version": "2.0", "copyright": "CC0: UX3D and Pascal Schoen"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Suzanne", "mesh": 0}], "meshes": [mesh],
        "materials": [{"name": "body", "pbrMetallicRoughness": {
            "baseColorFactor": [0.04, 0.45, 0.65, 1], "metallicFactor": 0.1,
            "roughnessFactor": 0.35,
        }}],
        "buffers": document["buffers"], "bufferViews": document["bufferViews"],
        "accessors": document["accessors"],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    size = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack("<III", 0x46546C67, 2, size)
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def setup_scene(renderer, aspect=1.5, source=None):
    scene = renderer.create_scene()
    scene.background = (0.025, 0.035, 0.055, 1)
    camera = scene.create_camera()
    camera.set_orthographic(left=-1.5 * aspect, right=1.5 * aspect,
                            bottom=-1.5, top=1.5, near=0.1, far=30)
    camera.position = (0, 0.15, 6)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    scene.add_directional_light(direction=(-1, -1, -2), intensity=120000,
                                color=(1, 0.92, 0.82))
    scene.add_directional_light(direction=(1, 0, -1), intensity=40000,
                                color=(0.55, 0.75, 1))
    model = scene.load(suzanne_glb() if source is None else source)
    model.rotation_euler_deg = (8, -24, 0)
    return scene, model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/images/suzanne.png")
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0:
        parser.error("Dimensions must be positive")
    with filly.Renderer() as renderer:
        scene, _ = setup_scene(renderer, args.width / args.height)
        target = renderer.create_render_target(width=args.width, height=args.height)
        renderer.render(scene, target)
        image = target.read()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
