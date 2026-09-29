"""Shared glTF accessor decoding for animation and instance attributes."""

import math
import struct
from ._native import AssetError

def _at(values, index):
    if type(index) is not int or not 0 <= index < len(values):
        raise ValueError("Accessor index is out of range")
    return values[index]

def accessor_reader(doc, binary, uri_bytes):
    buffers = {}

    def view_bytes(index):
        view = _at(doc.get("bufferViews", []), index)
        if "EXT_meshopt_compression" in view.get("extensions", {}):
            raise AssetError("Pointer animation accessors in meshopt-compressed buffer views are not supported")
        index = view["buffer"]
        source = _at(doc.get("buffers", []), index)
        if index not in buffers:
            buffers[index] = uri_bytes(source["uri"]) if "uri" in source else binary
        start, size = view.get("byteOffset", 0), view["byteLength"]
        if start < 0 or size < 0 or start + size > min(len(buffers[index]), source["byteLength"]):
            raise ValueError("Animation buffer view extends past its buffer")
        return memoryview(buffers[index])[start:start+size], view

    def read_elements(view_index, offset, count, components, component_type, packed=False):
        formats = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
        if component_type not in formats:
            raise ValueError("Invalid animation component type")
        unpack = struct.Struct("<" + formats[component_type]*components)
        data, view = view_bytes(view_index)
        stride = unpack.size if packed else view.get("byteStride", unpack.size)
        component_size = unpack.size // components
        if offset < 0 or stride < unpack.size or stride % component_size or offset % component_size:
            raise ValueError("Invalid animation accessor offset or stride")
        if offset + (count-1)*stride + unpack.size > len(data):
            raise ValueError("Animation accessor extends past its buffer view")
        return [list(unpack.unpack_from(data, offset+i*stride)) for i in range(count)]

    def accessor(index, components, times=False):
        acc = _at(doc.get("accessors", []), index)
        if acc["type"] != {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[components]:
            raise ValueError("Animation accessor type does not match its property")
        count, kind = acc["count"], acc["componentType"]
        if type(count) is not int or count <= 0 or count > 10_000_000:
            raise ValueError("Invalid animation accessor count")
        if kind not in (5120, 5121, 5122, 5123, 5125, 5126):
            raise ValueError("Invalid animation component type")
        if times and (kind != 5126 or acc.get("normalized", False)):
            raise ValueError("Animation input must use floating-point seconds")
        if acc.get("normalized", False) and kind not in (5120, 5121, 5122, 5123):
            raise ValueError("Invalid normalized animation component type")
        values = read_elements(acc["bufferView"], acc.get("byteOffset", 0), count, components, kind) if "bufferView" in acc else [[0]*components for _ in range(count)]
        if "sparse" in acc:
            sparse = acc["sparse"]
            n = sparse["count"]
            indices, overrides = sparse["indices"], sparse["values"]
            if not 0 < n <= count or indices["componentType"] not in (5121, 5123, 5125):
                raise ValueError("Invalid sparse animation accessor")
            positions = read_elements(indices["bufferView"], indices.get("byteOffset", 0), n, 1, indices["componentType"], True)
            replacements = read_elements(overrides["bufferView"], overrides.get("byteOffset", 0), n, components, kind, True)
            previous = -1
            for (position,), value in zip(positions, replacements):
                if not previous < position < count:
                    raise ValueError("Sparse animation indices must increase within the accessor")
                values[position] = value
                previous = position
        if acc.get("normalized", False):
            maximum = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535}[kind]
            values = [[max(-1, x/maximum) for x in row] for row in values]
        if not all(math.isfinite(x) and abs(x) <= 3.402823466e38 for row in values for x in row):
            raise ValueError("Animation values must be finite float32 values")
        return values

    return accessor
