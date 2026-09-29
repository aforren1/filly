"""The one-triangle glTF that gives a generated mesh its node and material.

The loader turns it into a model with the same material that a glTF file with these factors
gets. The native side then replaces its geometry with the caller's arrays.
"""

import json
import math
import struct

_ALPHA_MODES = {"opaque": "OPAQUE", "mask": "MASK", "blend": "BLEND"}


def _unit(name, values):
    values = [float(v) for v in values]
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
        raise ValueError(f"{name} values must be in [0, 1]")
    return values


def placeholder(low, high, *, colors, base_color, metallic, roughness, emissive, unlit,
                double_sided, alpha_mode):
    if alpha_mode not in _ALPHA_MODES:
        raise ValueError(f"alpha_mode must be 'opaque', 'mask', or 'blend', got {alpha_mode!r}")
    emissive = [float(v) for v in emissive]
    if not all(math.isfinite(v) and v >= 0 for v in emissive):
        raise ValueError("emissive values must be finite and nonnegative")
    # Accessor bounds become the model's load-time bounds, so the triangle spans the mesh.
    corners = [low, high, low]
    binary = b"".join(struct.pack("<3f", *corner) for corner in corners)
    binary += struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1)
    binary += struct.pack("<6f", 0, 0, 0, 0, 0, 0)
    attributes = {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}
    views = [(0, 36), (36, 36), (72, 24)]
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
         "min": [float(v) for v in low], "max": [float(v) for v in high]},
        {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
        {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC2"},
    ]
    if colors:
        attributes["COLOR_0"] = 3
        views.append((96, 48))
        binary += struct.pack("<12f", *([1.0] * 12))
        accessors.append({"bufferView": 3, "componentType": 5126, "count": 3, "type": "VEC4"})
    material = {
        "name": "mesh",
        "pbrMetallicRoughness": {"baseColorFactor": _unit("base_color", base_color),
                                 "metallicFactor": _unit("metallic", [metallic])[0],
                                 "roughnessFactor": _unit("roughness", [roughness])[0]},
        "doubleSided": bool(double_sided),
        "alphaMode": _ALPHA_MODES[alpha_mode],
    }
    used = []
    strength = max(emissive + [1.0])
    if any(emissive):
        material["emissiveFactor"] = [v / strength for v in emissive]
    if strength > 1:
        material["extensions"] = {"KHR_materials_emissive_strength": {"emissiveStrength": strength}}
        used.append("KHR_materials_emissive_strength")
    if unlit:
        material.setdefault("extensions", {})["KHR_materials_unlit"] = {}
        used.append("KHR_materials_unlit")
    document = {
        "asset": {"version": "2.0", "generator": "filly create_mesh"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "mesh"}],
        "meshes": [{"name": "mesh", "primitives": [{"attributes": attributes, "material": 0}]}],
        "materials": [material],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": offset, "byteLength": size} for offset, size in views],
        "accessors": accessors,
    }
    if used:
        document["extensionsUsed"] = used
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)
