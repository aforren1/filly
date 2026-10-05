"""Make filly's sample model, src/filly/samples/suzanne.glb, from Khronos's Suzanne.

Usage: python tools/make_suzanne.py

The source is Suzanne from the Khronos glTF Sample Assets at a fixed commit (CC0-1.0; UX3D,
Norbert Nopper, 2017). The output keeps the geometry, without texture coordinates and tangents,
with shared vertices merged. The source material is a near-uniform gray metal from two 1024 x
1024 textures; the output has one untextured blue metal material instead.
"""

import json
import struct
import urllib.request
from pathlib import Path

import numpy as np

COMMIT = "c6a6bd13ab2b3c685c7903d03561b8a9392f38b8"
SOURCE = f"https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/{COMMIT}/Models/Suzanne/glTF/"
OUTPUT = Path(__file__).resolve().parents[1] / "src" / "filly" / "samples" / "suzanne.glb"
FLOAT, UINT16, UINT32 = 5126, 5123, 5125


def fetch(name):
    with urllib.request.urlopen(SOURCE + name) as response:
        return response.read()


def read_accessor(document, binary, index):
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    dtype = {FLOAT: np.float32, UINT16: np.uint16, UINT32: np.uint32}[accessor["componentType"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", 0)
    if stride and stride != width * np.dtype(dtype).itemsize:
        raise ValueError("interleaved source buffers are not supported")
    values = np.frombuffer(binary, dtype, accessor["count"] * width, start)
    return values.reshape(accessor["count"], width) if width > 1 else values


def main():
    document = json.loads(fetch("Suzanne.gltf"))
    binary = fetch(document["buffers"][0]["uri"])
    (primitive,) = document["meshes"][0]["primitives"]
    positions = read_accessor(document, binary, primitive["attributes"]["POSITION"])
    normals = read_accessor(document, binary, primitive["attributes"]["NORMAL"])
    indices = read_accessor(document, binary, primitive["indices"]).astype(np.int64)

    # The source repeats each vertex once per triangle corner; merge exact duplicates.
    vertices = np.ascontiguousarray(np.hstack([positions, normals]), dtype=np.float32)
    unique, inverse = np.unique(vertices.view(np.dtype((np.void, 24))).ravel(), return_inverse=True)
    vertices = unique.view(np.float32).reshape(-1, 6)
    indices = inverse.ravel()[indices]
    index_type = (np.uint16, UINT16) if len(vertices) < 65536 else (np.uint32, UINT32)
    positions = np.ascontiguousarray(vertices[:, :3])
    normals = np.ascontiguousarray(vertices[:, 3:])

    chunks = [indices.astype(index_type[0]).tobytes(), positions.tobytes(), normals.tobytes()]
    views, offset = [], 0
    for chunk, target in zip(chunks, (34963, 34962, 34962)):
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(chunk), "target": target})
        offset += len(chunk) + (-len(chunk) % 4)
    body = b"".join(chunk + b"\0" * (-len(chunk) % 4) for chunk in chunks)
    output = {
        "asset": {"version": "2.0", "generator": "filly tools/make_suzanne.py",
                  "copyright": "2017 UX3D, Norbert Nopper (CC0-1.0); modified for filly"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Suzanne", "mesh": 0}],
        "meshes": [{"name": "Suzanne", "primitives": [
            {"attributes": {"POSITION": 1, "NORMAL": 2}, "indices": 0, "material": 0}]}],
        "materials": [{"name": "Suzanne", "pbrMetallicRoughness": {
            "baseColorFactor": [0.05, 0.25, 0.85, 1.0], "metallicFactor": 1.0, "roughnessFactor": 0.35}}],
        "buffers": [{"byteLength": len(body)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": index_type[1], "count": len(indices), "type": "SCALAR"},
            {"bufferView": 1, "componentType": FLOAT, "count": len(positions), "type": "VEC3",
             "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
            {"bufferView": 2, "componentType": FLOAT, "count": len(normals), "type": "VEC3"},
        ],
    }
    encoded = json.dumps(output, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(body)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_bytes(struct.pack("<III", 0x46546C67, 2, length)
                       + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
                       + struct.pack("<II", len(body), 0x004E4942) + body)
    print(f"{OUTPUT}: {len(vertices)} vertices, {len(indices) // 3} triangles, {length} bytes")


if __name__ == "__main__":
    main()
