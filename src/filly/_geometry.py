"""Load-time decoding and instance expansion for glTF geometry extensions."""

import base64
import copy
import math

from ._native import AssetError, _decode_meshopt
from ._accessors import accessor_reader


def decode_meshopt(doc, binary, uri_bytes):
    names = {"KHR_meshopt_compression", "EXT_meshopt_compression"}
    for buffer in doc.get("buffers", []):
        if len(names & buffer.get("extensions", {}).keys()) > 1:
            raise ValueError("A buffer cannot use both meshopt extensions")
    sources = {}
    decoded = False
    for view in doc.get("bufferViews", []):
        extensions = view.get("extensions", {})
        present = names & extensions.keys()
        if not present:
            continue
        if len(present) != 1:
            raise ValueError("A buffer view cannot use both meshopt extensions")
        name = present.pop()
        ext = extensions.pop(name)
        count, stride, index = ext["count"], ext["byteStride"], ext["buffer"]
        start, size = ext.get("byteOffset", 0), ext["byteLength"]
        if any(type(x) is not int or x < 0 for x in (count, stride, index, start, size)):
            raise ValueError("Invalid meshopt buffer range")
        if index >= len(doc.get("buffers", [])) or count * stride != view["byteLength"]:
            raise ValueError("Meshopt decoded length does not match its buffer view")
        if "byteStride" in view and view["byteStride"] != stride:
            raise ValueError("Meshopt stride does not match its buffer view")
        if index not in sources:
            source = doc["buffers"][index]
            sources[index] = uri_bytes(source["uri"]) if "uri" in source else binary
        source = sources[index]
        if start + size > min(len(source), doc["buffers"][index]["byteLength"]):
            raise ValueError("Meshopt range exceeds its source buffer")
        payload = source[start:start+size]
        if name == "EXT_meshopt_compression" and (ext.get("filter") == "COLOR" or
                (ext["mode"] == "ATTRIBUTES" and payload[:1] == b"\xa1")):
            raise ValueError("Vertex version 1 and COLOR filtering require KHR_meshopt_compression")
        result = _decode_meshopt(payload, count, stride, ext["mode"], ext.get("filter", "NONE"))
        view["buffer"] = len(doc["buffers"])
        view["byteOffset"] = 0
        doc["buffers"].append({"byteLength": len(result),
            "uri": "data:application/octet-stream;base64," + base64.b64encode(result).decode("ascii")})
        decoded = True
    if decoded:
        # Placeholder fallback buffers need no allocation after every compressed view is decoded.
        used = sorted({view["buffer"] for view in doc["bufferViews"]})
        remap = {old: new for new, old in enumerate(used)}
        doc["buffers"] = [doc["buffers"][old] for old in used]
        for view in doc["bufferViews"]:
            view["buffer"] = remap[view["buffer"]]
        for buffer in doc["buffers"]:
            for name in names:
                buffer.get("extensions", {}).pop(name, None)
        for key in ("extensionsUsed", "extensionsRequired"):
            if key in doc:
                doc[key] = [name for name in doc[key] if name not in names]


def expand_instances(doc, binary, uri_bytes):
    accessor = accessor_reader(doc, binary, uri_bytes)
    nodes = doc.get("nodes", [])
    expanded = {}
    for index, node in enumerate(list(nodes)):
        ext = node.get("extensions", {}).pop("EXT_mesh_gpu_instancing", None)
        if ext is None:
            continue
        attributes = ext["attributes"]
        if "mesh" not in node or not attributes:
            raise ValueError("Instancing requires a mesh and at least one attribute")
        values, count = {}, None
        for name, acc_index in attributes.items():
            if type(acc_index) is not int or not 0 <= acc_index < len(doc.get("accessors", [])):
                raise ValueError("Invalid instance accessor index")
            acc = doc["accessors"][acc_index]
            if count is not None and count != acc["count"]:
                raise ValueError("Instance attribute counts must match")
            count = acc["count"]
            if name.startswith("_"):
                continue
            if name not in ("TRANSLATION", "ROTATION", "SCALE"):
                raise ValueError("Unknown instance attribute")
            if name == "ROTATION":
                if not (acc["componentType"] == 5126 or
                        (acc["componentType"] in (5120, 5122) and acc.get("normalized", False))):
                    raise ValueError("Instance rotation requires floats or normalized signed integers")
            elif acc["componentType"] != 5126 or acc.get("normalized", False):
                raise ValueError("Instance translation and scale require floats")
            values[name] = accessor(acc_index, 4 if name == "ROTATION" else 3)
        if type(count) is not int or not 0 < count <= 100_000:
            raise ValueError("Invalid instance count")
        mesh = node.pop("mesh")
        children = node.setdefault("children", [])
        expanded[index] = []
        for instance in range(count):
            # Unnamed, as in the source document; reach it through the parent's children.
            child = {"mesh": mesh}
            for key in ("skin", "weights"):
                if key in node:
                    child[key] = copy.deepcopy(node[key])
            for name, rows in values.items():
                value = rows[instance]
                if name == "ROTATION":
                    norm = math.sqrt(sum(x*x for x in value))
                    if abs(norm - 1) > 0.01:
                        raise ValueError("Instance rotation must be a unit quaternion")
                    value = [x/norm for x in value]
                child[name.lower()] = value
            expanded[index].append(len(nodes))
            children.append(len(nodes))
            nodes.append(child)
        node.pop("skin", None)
        node.pop("weights", None)
    # Morph animation belongs to each expanded mesh; TRS animation remains on the parent.
    for animation in doc.get("animations", []):
        channels = []
        for channel in animation["channels"]:
            target = channel["target"]
            source = target.get("node") if target.get("path") == "weights" else None
            pointer = target.get("extensions", {}).get("KHR_animation_pointer", {}).get("pointer", "")
            if pointer.startswith("/nodes/") and pointer.endswith("/weights"):
                source = int(pointer.split("/")[2])
            if source in expanded:
                for child in expanded[source]:
                    replacement = copy.deepcopy(channel)
                    replacement["target"] = {"node": child, "path": "weights"}
                    channels.append(replacement)
            else:
                channels.append(channel)
        animation["channels"] = channels
    for key in ("extensionsUsed", "extensionsRequired"):
        if key in doc:
            doc[key] = [name for name in doc[key] if name != "EXT_mesh_gpu_instancing"]
