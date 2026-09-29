"""Asset checks and image conversion before the native glTF loader runs."""

import base64
import io
import json
from pathlib import Path
import struct
import warnings

from ._native import AssetError


class AssetCompatibilityWarning(UserWarning):
    """An optional asset feature cannot be reproduced by this renderer."""


SUPPORTED = frozenset({
    "KHR_lights_punctual", "KHR_materials_unlit", "KHR_materials_clearcoat",
    "KHR_materials_sheen", "KHR_materials_transmission", "KHR_materials_volume",
    "KHR_materials_ior", "KHR_materials_specular", "KHR_materials_emissive_strength",
    "KHR_materials_pbrSpecularGlossiness", "KHR_materials_variants",
    "KHR_materials_dispersion", "KHR_materials_diffuse_transmission",
    "KHR_texture_transform", "KHR_texture_basisu", "EXT_texture_webp",
    "KHR_mesh_quantization", "KHR_draco_mesh_compression", "EXT_meshopt_compression",
    "KHR_animation_pointer",
    "KHR_materials_anisotropy", "KHR_materials_iridescence",
    "KHR_meshopt_compression", "EXT_mesh_gpu_instancing", "KHR_node_visibility",
})

# Metadata only: these cannot change the rendered image.
IGNORED = frozenset({"KHR_xmp", "KHR_xmp_json_ld"})


def prepare(data, path, *, strict=False, refraction=False, precompiled=False):
    """Return normalized bytes and data for native material and animation extensions."""
    try:
        return _prepare(data, path, strict=strict, refraction=refraction, precompiled=precompiled)
    except (ValueError, KeyError, IndexError, TypeError, struct.error, OSError) as exc:
        raise AssetError(f"Invalid glTF asset: {exc}") from exc


def _prepare(data, path, *, strict, refraction, precompiled):
    binary = b""
    is_glb = data[:4] == b"glTF"
    if is_glb:
        if len(data) < 20:
            raise ValueError("Incomplete GLB header")
        magic, version, length, json_size, kind = struct.unpack_from("<5I", data)
        if version != 2 or length != len(data) or kind != 0x4E4F534A or 20 + json_size > length:
            raise ValueError("Invalid GLB header or JSON chunk")
        doc = json.loads(data[20:20 + json_size])
        offset = 20 + json_size
        if offset < length:
            size, kind = struct.unpack_from("<II", data, offset)
            if kind != 0x004E4942 or offset + 8 + size != length:
                raise ValueError("Invalid GLB binary chunk")
            binary = data[offset + 8:]
    else:
        doc = json.loads(data)

    def issue(message, required=False):
        if strict or required:
            raise AssetError(message)
        warnings.warn(message, AssetCompatibilityWarning, stacklevel=4)

    # In a view that is not transparent, Filament writes the sharpened edge alpha of MASK
    # materials to the target, and only color grading then stores alpha one. Variant materials
    # are included because a variant can switch a mesh to one of them.
    masked = any(material.get("alphaMode", "OPAQUE") == "MASK" for material in doc.get("materials", []))

    used = set(doc.get("extensionsUsed", [])) | set(doc.get("extensionsRequired", []))
    required = set(doc.get("extensionsRequired", []))
    for name in sorted(used - SUPPORTED - IGNORED):
        issue(f"Unsupported glTF extension: {name}", name in required)
    if "KHR_materials_transmission" in used and not refraction:
        issue("Asset uses glass transmission. Set scene.refraction = True before loading it.")
    for material in doc.get("materials", []):
        extensions = material.get("extensions", {})
        if "KHR_materials_dispersion" in extensions:
            if "KHR_materials_volume" not in extensions or any(name in extensions for name in ("KHR_materials_unlit", "KHR_materials_pbrSpecularGlossiness")):
                raise AssetError("Dispersion requires a volume material without unlit or specular-glossiness")
    for index, material in enumerate(doc.get("materials", [])):
        _check_texture_count(material, index, precompiled, issue)

    buffers = {}

    def uri_bytes(uri):
        if uri.startswith("data:"):
            header, payload = uri.split(",", 1)
            if ";base64" in header:
                return base64.b64decode(payload, validate=True)
            from urllib.parse import unquote_to_bytes
            return unquote_to_bytes(payload)
        if not path:
            raise AssetError("Byte assets must contain all resources")
        from urllib.parse import unquote
        return (Path(path).parent / unquote(uri)).read_bytes()

    def image_bytes(index):
        image = doc["images"][index]
        if "uri" in image:
            return uri_bytes(image["uri"])
        view = doc["bufferViews"][image["bufferView"]]
        index = view["buffer"]
        if index not in buffers:
            source = doc["buffers"][index]
            buffers[index] = uri_bytes(source["uri"]) if "uri" in source else binary
        offset, length = view.get("byteOffset", 0), view["byteLength"]
        payload = buffers[index][offset:offset+length]
        if len(payload) != length:
            raise ValueError("Image buffer view extends past its buffer")
        return payload

    from ._geometry import decode_meshopt, expand_instances
    decode_meshopt(doc, binary, uri_bytes)
    expand_instances(doc, binary, uri_bytes)
    for index, node in enumerate(doc.get("nodes", [])):
        visible = node.get("extensions", {}).get("KHR_node_visibility", {}).get("visible", True)
        if type(visible) is not bool:
            raise ValueError("Node visibility must be a boolean")
        extras = node.get("extras", {})
        if not isinstance(extras, dict):
            extras = {"original": extras}
        reserved = ("fillyVisibility", "fillyVisible")
        node["extras"] = {"fillyVisibility": index, "fillyVisible": int(visible),
                          **{k: v for k, v in extras.items() if k not in reserved}}
    # gltfio names an unnamed node after its mesh, light, or camera; Node.name must not.
    meshes = doc.get("meshes", [])

    def text(value):
        return value if isinstance(value, str) else None
    nodes = [(text(node.get("name")), text(meshes[node["mesh"]].get("name")) if "mesh" in node else None)
             for node in doc.get("nodes", [])]

    from ._animation import prepare_animations
    animations, cameras = prepare_animations(doc, binary, uri_bytes, issue, "KHR_animation_pointer" in required)

    # Pillow supplies WebP on SDK builds that lack libwebp. Conversion happens only at load time.
    webp_images = set()
    for texture in doc.get("textures", []):
        extension = texture.get("extensions", {}).pop("EXT_texture_webp", None)
        if extension is not None:
            texture["source"] = extension["source"]
            webp_images.add(extension["source"])
    for i, image in enumerate(doc.get("images", [])):
        uri = image.get("uri", "").lower()
        if i in webp_images or image.get("mimeType") == "image/webp" or uri.endswith(".webp") or uri.startswith("data:image/webp"):
            try:
                from PIL import Image
            except ImportError as exc:
                raise AssetError("WebP assets require Pillow; install filly[images]") from exc
            output = io.BytesIO()
            with Image.open(io.BytesIO(image_bytes(i))) as decoded:
                decoded.convert("RGBA").save(output, format="PNG")
            image.pop("bufferView", None)
            image.update(mimeType="image/png", uri="data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"))
    for key in ("extensionsUsed", "extensionsRequired"):
        if key in doc:
            doc[key] = [name for name in doc[key] if name != "EXT_texture_webp" and name not in IGNORED]

    def texture_info(info):
        if info is None:
            return (b"", "image/png", 0, [1,0,0,0,1,0,0,0,1], 10497, 10497, 9987, 9729)
        texture = doc["textures"][info["index"]]
        index = texture.get("extensions", {}).get("KHR_texture_basisu", {}).get("source", texture.get("source"))
        image = doc["images"][index]
        transform = info.get("extensions", {}).get("KHR_texture_transform", {})
        uv = transform.get("texCoord", info.get("texCoord", 0))
        if uv not in (0, 1):
            raise AssetError("Extended materials support TEXCOORD_0 and TEXCOORD_1")
        import math
        angle = transform.get("rotation", 0)
        sx, sy = transform.get("scale", [1,1]); ox, oy = transform.get("offset", [0,0])
        if not all(math.isfinite(x) and abs(x) <= 3.402823466e38 for x in (angle, sx, sy, ox, oy)):
            raise ValueError("Invalid texture transform")
        c, s = math.cos(angle), math.sin(angle)
        matrix = [c*sx,s*sx,0,-s*sy,c*sy,0,ox,oy,1]
        sampler = doc.get("samplers", [])[texture["sampler"]] if "sampler" in texture else {}
        payload = image_bytes(index)
        mime = image.get("mimeType") or ("image/ktx2" if payload.startswith(b"\xabKTX 20") else "image/png")
        wrap_s, wrap_t = sampler.get("wrapS",10497), sampler.get("wrapT",10497)
        min_filter, mag_filter = sampler.get("minFilter",9987), sampler.get("magFilter",9729)
        if wrap_s not in (10497,33071,33648) or wrap_t not in (10497,33071,33648):
            raise ValueError("Invalid texture wrap mode")
        if min_filter not in (9728,9729,9984,9985,9986,9987) or mag_filter not in (9728,9729):
            raise ValueError("Invalid texture filter")
        return (payload, mime, uv, matrix, wrap_s, wrap_t, min_filter, mag_filter)

    surfaces = []
    for index, material in enumerate(doc.get("materials", [])):
        extensions = material.get("extensions", {})
        if not extensions.keys() & {"KHR_materials_anisotropy", "KHR_materials_iridescence"}:
            continue
        if extensions.keys() & {"KHR_materials_unlit", "KHR_materials_pbrSpecularGlossiness", "KHR_materials_diffuse_transmission"}:
            raise AssetError("Anisotropy/iridescence cannot use unlit, specular-glossiness, or custom diffuse transmission")
        a, i = extensions.get("KHR_materials_anisotropy", {}), extensions.get("KHR_materials_iridescence", {})
        values = [a.get("anisotropyStrength", 0), a.get("anisotropyRotation", 0), i.get("iridescenceFactor", 0),
                  i.get("iridescenceIor", 1.3), i.get("iridescenceThicknessMinimum", 100), i.get("iridescenceThicknessMaximum", 400)]
        import math
        if not all(math.isfinite(x) and abs(x) <= 3.402823466e38 for x in values) or not (0 <= values[0] <= 1 and 0 <= values[2] <= 1 and values[3] >= 1 and min(values[4:]) >= 0):
            raise ValueError("Invalid anisotropy or iridescence factors")
        surfaces.append((values, texture_info(a.get("anisotropyTexture")), texture_info(i.get("iridescenceTexture")), texture_info(i.get("iridescenceThicknessTexture"))))
        material["name"] = f"__fp_surface_{len(surfaces)-1}__" + material.get("name", f"material_{index}")
        extensions.pop("KHR_materials_anisotropy", None)
        extensions.pop("KHR_materials_iridescence", None)

    diffuse = []
    for index, material in enumerate(doc.get("materials", [])):
        ext = material.get("extensions", {}).get("KHR_materials_diffuse_transmission")
        if ext is None:
            continue
        # Unknown extensions already produced a warning or error above.
        conflicts = (set(material["extensions"]) & SUPPORTED) - {"KHR_materials_diffuse_transmission", "KHR_materials_ior", "KHR_materials_emissive_strength", "KHR_materials_volume", "KHR_materials_dispersion"}
        if conflicts:
            raise AssetError(f"Diffuse transmission material has unsupported combinations: {sorted(conflicts)}")
        factor = float(ext.get("diffuseTransmissionFactor", 0))
        color = ext.get("diffuseTransmissionColorFactor", [1,1,1])
        if not 0 <= factor <= 1 or len(color) != 3 or not all(0 <= x <= 1 for x in color):
            raise ValueError("Invalid diffuse transmission factor or color")
        volume = material["extensions"].get("KHR_materials_volume", {})
        thickness = volume.get("thicknessFactor", 0)
        attenuation = volume.get("attenuationColor", [1, 1, 1])
        distance = volume.get("attenuationDistance", float("inf"))
        import math
        if not math.isfinite(thickness) or not 0 <= thickness <= 3.402823466e38 or not distance > 0 or ("attenuationDistance" in volume and not math.isfinite(distance)):
            raise ValueError("Invalid volume thickness or attenuation distance")
        if len(attenuation) != 3 or not all(0 <= x <= 1 for x in attenuation):
            raise ValueError("Invalid volume attenuation color")
        absorption = [-math.log(max(x, 1e-30)) / distance for x in attenuation]
        if not all(math.isfinite(x) and x <= 3.402823466e38 for x in absorption):
            raise ValueError("Volume absorption exceeds shader range")
        diffuse.append((factor, color, texture_info(ext.get("diffuseTransmissionTexture")),
                        texture_info(ext.get("diffuseTransmissionColorTexture")), thickness, absorption,
                        texture_info(volume.get("thicknessTexture"))))
        material["name"] = f"__fp_diffuse_{len(diffuse)-1}__" + material.get("name", f"material_{index}")
        del material["extensions"]["KHR_materials_diffuse_transmission"]
        # Diffuse transport uses its own finite-thickness shader. Dispersion affects
        # specular refraction, which this combination does not contain.
        for name in ("KHR_materials_volume", "KHR_materials_dispersion"):
            material["extensions"].pop(name, None)

    morphs = []
    for index, node in enumerate(doc.get("nodes", [])):
        mesh = doc.get("meshes", [])[node["mesh"]] if "mesh" in node else {}
        count = max((len(p.get("targets", [])) for p in mesh.get("primitives", [])), default=0)
        weights = node.get("weights", mesh.get("weights", [0.0]*count))
        morphs.append(weights)
        if count:
            if len(weights) != count:
                raise ValueError("Morph weight count does not match targets")
            extras = node.get("extras", {})
            if not isinstance(extras, dict):
                extras = {"original": extras}
            extras["fillyNode"] = index
            node["extras"] = extras

    binary = _merge_buffers(doc, binary, uri_bytes)
    is_glb = True
    encoded = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    if is_glb:
        encoded += b" " * (-len(encoded) % 4)
        tail = struct.pack("<II", len(binary), 0x004E4942) + binary if binary else b""
        encoded = struct.pack("<III", 0x46546C67, 2, 20+len(encoded)+len(tail)) + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + tail
    return encoded, diffuse, morphs, animations, surfaces, cameras, nodes, masked


# Filament 1.77.1 compiles glTF materials at feature level 1: 16 fragment samplers, of which a lit
# material with screen-space reflection or refraction leaves 8 for textures
# (filamat MaterialBuilder::checkMaterialLevelFeatures). Measured: a 9th texture aborts the
# process in the SDK's compiled provider, and fails compilation in the wrapper's generator.
MAX_LIT_TEXTURES = 8
# Anisotropy and iridescence materials always declare these three samplers.
SURFACE_SAMPLERS = 3
_TEXTURE_SLOTS = (
    (("pbrMetallicRoughness",), ("baseColorTexture", "metallicRoughnessTexture")),
    ((), ("normalTexture", "occlusionTexture", "emissiveTexture")),
    (("extensions", "KHR_materials_clearcoat"), ("clearcoatTexture", "clearcoatRoughnessTexture", "clearcoatNormalTexture")),
    (("extensions", "KHR_materials_sheen"), ("sheenColorTexture", "sheenRoughnessTexture")),
    (("extensions", "KHR_materials_transmission"), ("transmissionTexture",)),
    (("extensions", "KHR_materials_volume"), ("thicknessTexture",)),
    (("extensions", "KHR_materials_specular"), ("specularTexture", "specularColorTexture")),
)


def _check_texture_count(material, index, precompiled, issue):
    """Reject lit materials whose texture samplers exceed the shader limit, before Filament aborts."""
    extensions = material.get("extensions", {})
    # Unlit uses one texture; diffuse transmission uses its own fixed material.
    if extensions.keys() & {"KHR_materials_unlit", "KHR_materials_diffuse_transmission"}:
        return
    count = 0
    for path, keys in _TEXTURE_SLOTS:
        node = material
        for part in path:
            node = node.get(part, {})
        if path == ("pbrMetallicRoughness",) and "KHR_materials_pbrSpecularGlossiness" in extensions:
            # gltfio then samples these two textures in place of the metallic-roughness pair.
            node, keys = extensions["KHR_materials_pbrSpecularGlossiness"], ("diffuseTexture", "specularGlossinessTexture")
        count += sum(key in node for key in keys)
    name = material.get("name", f"material {index}")
    surface = bool(extensions.keys() & {"KHR_materials_anisotropy", "KHR_materials_iridescence"})
    if surface and count + SURFACE_SAMPLERS > MAX_LIT_TEXTURES:
        raise AssetError(f"Material {name!r} uses {count} textures; with anisotropy or iridescence the limit "
                         f"is {MAX_LIT_TEXTURES - SURFACE_SAMPLERS}")
    if count <= MAX_LIT_TEXTURES:
        return
    if not extensions.keys() & {"KHR_materials_transmission", "KHR_materials_volume"}:
        if not precompiled:
            raise AssetError(f"Material {name!r} uses {count} textures; compiled lit materials support at most "
                             f"{MAX_LIT_TEXTURES}. Use Renderer(precompiled_shaders=True) for this asset.")
        # Filament's precompiled materials then drop clearcoat, sheen, IOR, or specular inputs.
        issue(f"Material {name!r} uses {count} textures; precompiled shaders render it without some of its features.")
        return
    raise AssetError(f"Material {name!r} uses {count} textures; lit materials with this combination support at most "
                     f"{MAX_LIT_TEXTURES}")


def _merge_buffers(doc, binary, uri_bytes):
    """Return one GLB binary chunk holding every buffer, and remap buffer views to it.

    cgltf reads external buffer files with narrow fopen() on desktop builds and ignores
    ResourceLoader's URI cache for buffers. A GLB binary chunk avoids file I/O in the SDK
    without base64 text: bytes are copied once here and parsed in place by cgltf.
    """
    buffers = doc.get("buffers", [])
    if not buffers:
        return binary
    merged = bytearray()
    offsets = []
    for index, buffer in enumerate(buffers):
        if "uri" in buffer:
            try:
                payload = uri_bytes(buffer["uri"])
            except FileNotFoundError as exc:
                raise AssetError(f"Missing glTF resource: {buffer['uri']}") from exc
        elif index == 0 and binary:
            payload = binary
        else:
            raise ValueError("Only the first buffer of a GLB may omit its URI")
        if len(payload) < buffer["byteLength"]:
            raise ValueError("Buffer is shorter than its byteLength")
        # 16-byte alignment keeps every accessor's component alignment valid.
        merged += bytes(-len(merged) % 16)
        offsets.append(len(merged))
        merged += payload[:buffer["byteLength"]]
    for view in doc.get("bufferViews", []):
        view["byteOffset"] = view.get("byteOffset", 0) + offsets[view["buffer"]]
        view["buffer"] = 0
    merged += bytes(-len(merged) % 4)
    doc["buffers"] = [{"byteLength": len(merged)}]
    return bytes(merged)
