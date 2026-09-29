"""Decode supported glTF property animation once, before native resource upload."""

import math
import re
import struct

from ._native import AssetError


# Property path: native parameter, component count, default, minimum, maximum.
MATERIAL_PROPERTIES = {
    "pbrMetallicRoughness/baseColorFactor": ("baseColorFactor", 4, [1]*4, 0, 1),
    "pbrMetallicRoughness/metallicFactor": ("metallicFactor", 1, [1], 0, 1),
    "pbrMetallicRoughness/roughnessFactor": ("roughnessFactor", 1, [1], 0, 1),
    "emissiveFactor": ("emissiveFactor", 3, [0]*3, 0, 1),
    "normalTexture/scale": ("normalScale", 1, [1], -math.inf, math.inf),
    "occlusionTexture/strength": ("aoStrength", 1, [1], 0, 1),
    "extensions/KHR_materials_emissive_strength/emissiveStrength": ("emissiveStrength", 1, [1], 0, math.inf),
    "extensions/KHR_materials_transmission/transmissionFactor": ("transmissionFactor", 1, [0], 0, 1),
    "extensions/KHR_materials_clearcoat/clearcoatFactor": ("clearCoatFactor", 1, [0], 0, 1),
    "extensions/KHR_materials_clearcoat/clearcoatRoughnessFactor": ("clearCoatRoughnessFactor", 1, [0], 0, 1),
    "extensions/KHR_materials_clearcoat/clearcoatNormalTexture/scale": ("clearCoatNormalScale", 1, [1], -math.inf, math.inf),
    "extensions/KHR_materials_ior/ior": ("ior", 1, [1.5], 1, math.inf),
    "extensions/KHR_materials_sheen/sheenColorFactor": ("sheenColorFactor", 3, [0]*3, 0, 1),
    "extensions/KHR_materials_sheen/sheenRoughnessFactor": ("sheenRoughnessFactor", 1, [0], 0, 1),
    "extensions/KHR_materials_specular/specularFactor": ("specularStrength", 1, [1], 0, 1),
    "extensions/KHR_materials_specular/specularColorFactor": ("specularColorFactor", 3, [1]*3, 0, 1),
    "extensions/KHR_materials_volume/thicknessFactor": ("volumeThicknessFactor", 1, [0], 0, math.inf),
    "extensions/KHR_materials_dispersion/dispersion": ("dispersion", 1, [0], 0, math.inf),
    "extensions/KHR_materials_diffuse_transmission/diffuseTransmissionFactor": ("diffuseFactor", 1, [0], 0, 1),
    "extensions/KHR_materials_diffuse_transmission/diffuseTransmissionColorFactor": ("diffuseColor", 3, [1]*3, 0, 1),
    "extensions/KHR_materials_anisotropy/anisotropyStrength": ("anisotropyStrength", 1, [0], 0, 1),
    "extensions/KHR_materials_anisotropy/anisotropyRotation": ("anisotropyRotation", 1, [0], -math.inf, math.inf),
    "extensions/KHR_materials_iridescence/iridescenceFactor": ("iridescenceFactor", 1, [0], 0, 1),
    "extensions/KHR_materials_iridescence/iridescenceIor": ("iridescenceIor", 1, [1.3], 1, math.inf),
    "extensions/KHR_materials_iridescence/iridescenceThicknessMinimum": ("iridescenceThicknessMinimum", 1, [100], 0, math.inf),
    "extensions/KHR_materials_iridescence/iridescenceThicknessMaximum": ("iridescenceThicknessMaximum", 1, [400], 0, math.inf),
}

UV_PROPERTIES = {
    "pbrMetallicRoughness/baseColorTexture": "baseColorUvMatrix",
    "pbrMetallicRoughness/metallicRoughnessTexture": "metallicRoughnessUvMatrix",
    "normalTexture": "normalUvMatrix", "occlusionTexture": "occlusionUvMatrix", "emissiveTexture": "emissiveUvMatrix",
    "extensions/KHR_materials_clearcoat/clearcoatTexture": "clearCoatUvMatrix",
    "extensions/KHR_materials_clearcoat/clearcoatRoughnessTexture": "clearCoatRoughnessUvMatrix",
    "extensions/KHR_materials_clearcoat/clearcoatNormalTexture": "clearCoatNormalUvMatrix",
    "extensions/KHR_materials_sheen/sheenColorTexture": "sheenColorUvMatrix",
    "extensions/KHR_materials_sheen/sheenRoughnessTexture": "sheenRoughnessUvMatrix",
    "extensions/KHR_materials_transmission/transmissionTexture": "transmissionUvMatrix",
    "extensions/KHR_materials_volume/thicknessTexture": "volumeThicknessUvMatrix",
    "extensions/KHR_materials_specular/specularTexture": "specularUvMatrix",
    "extensions/KHR_materials_specular/specularColorTexture": "specularColorUvMatrix",
    "extensions/KHR_materials_anisotropy/anisotropyTexture": "anisotropyUvMatrix",
    "extensions/KHR_materials_iridescence/iridescenceTexture": "iridescenceUvMatrix",
    "extensions/KHR_materials_iridescence/iridescenceThicknessTexture": "iridescenceThicknessUvMatrix",
    "extensions/KHR_materials_diffuse_transmission/diffuseTransmissionTexture": "diffuseUvMatrix",
    "extensions/KHR_materials_diffuse_transmission/diffuseTransmissionColorTexture": "diffuseColorUvMatrix",
}


def _at(values, index):
    if type(index) is not int or not 0 <= index < len(values):
        raise ValueError("Animation index is out of range")
    return values[index]


def prepare_animations(doc, binary, uri_bytes, issue, required):
    from ._accessors import accessor_reader
    accessor = accessor_reader(doc, binary, uri_bytes)

    def mark(obj, key, value):
        extras = obj.get("extras", {})
        if not isinstance(extras, dict):
            extras = {"original": extras}
        # Put our key before arbitrary nested user extras for native ID lookup.
        obj["extras"] = {key: value, **{k: v for k, v in extras.items() if k != key}}

    cameras = []
    for index, camera in enumerate(doc.get("cameras", [])):
        ortho = camera["type"] == "orthographic"
        if camera["type"] not in ("orthographic", "perspective"):
            raise ValueError("Invalid camera type")
        p = camera[camera["type"]]
        values = [p["xmag"], p["ymag"], p["znear"], p["zfar"]] if ortho else [p["yfov"], p.get("aspectRatio", 0), p["znear"], p.get("zfar", math.inf)]
        if not all(math.isfinite(x) and abs(x) <= 3.402823466e38 for x in values[:3]) or math.isnan(values[3]) or values[0] <= 0 or values[1] < 0 or (values[1] == 0 and "aspectRatio" in p) or values[2] < 0 or values[3] <= values[2]:
            raise ValueError("Invalid camera projection")
        if ("zfar" in p and (not math.isfinite(values[3]) or values[3] > 3.402823466e38)) or (not ortho and (values[0] >= math.pi or values[2] <= 0)):
            raise ValueError("Invalid camera projection")
        cameras.append((ortho, values))
        for node in doc.get("nodes", []):
            if node.get("camera") == index:
                mark(node, "fillyCamera", index)

    clips, native_animations = [], []
    for animation in doc.get("animations", []):
        channels, tracks, seen = [], [], set()
        for channel in animation["channels"]:
            target = channel["target"]
            extension = target.get("extensions", {}).get("KHR_animation_pointer")
            if extension is None:
                if target.get("path") == "pointer":
                    raise ValueError("Pointer animation target lacks KHR_animation_pointer")
                channels.append(channel)
                continue
            pointer = extension["pointer"]
            if target.get("path") != "pointer" or "node" in target:
                raise ValueError("Pointer animation requires path='pointer' and no target node")
            node = re.fullmatch(r"/nodes/(0|[1-9][0-9]*)/(translation|rotation|scale|weights)", pointer)
            visibility = re.fullmatch(r"/nodes/(0|[1-9][0-9]*)/extensions/KHR_node_visibility/visible", pointer)
            if node:
                index, path = int(node[1]), node[2]
                obj = _at(doc.get("nodes", []), index)
                if pointer in seen or ("matrix" in obj and path != "weights"):
                    raise ValueError("Duplicate node pointer or animation of a matrix-authored node")
                seen.add(pointer)
                # These properties have identical semantics to ordinary glTF node channels.
                channels.append(dict(channel, target={"node": index, "path": path}))
                continue
            material = re.fullmatch(r"/materials/(0|[1-9][0-9]*)/(.+)", pointer)
            light = re.fullmatch(r"/extensions/KHR_lights_punctual/lights/(0|[1-9][0-9]*)/(color|intensity|range|spot/innerConeAngle|spot/outerConeAngle)", pointer)
            camera = re.fullmatch(r"/cameras/(0|[1-9][0-9]*)/(perspective|orthographic)/(yfov|aspectRatio|znear|zfar|xmag|ymag)", pointer)
            uv = re.fullmatch(r"(.+)/extensions/KHR_texture_transform/(offset|rotation|scale)", material[2]) if material else None
            setup = []
            if visibility:
                index, parameter = int(visibility[1]), "visible"
                _at(doc.get("nodes", []), index)["extensions"]["KHR_node_visibility"]
                components, lower, upper, target_kind = 1, 0, 1, 5
            elif material and material[2] in MATERIAL_PROPERTIES:
                index, path = int(material[1]), material[2]
                obj = _at(doc.get("materials", []), index)
                parameter, components, default, lower, upper = MATERIAL_PROPERTIES[path]
                parent = obj
                for part in path.split("/")[:-1]:
                    parent = parent[part]
                rest = parent.get(path.split("/")[-1], default)
                rest = rest if isinstance(rest, list) else [rest]
                if len(rest) != components or not all(math.isfinite(x) and abs(x) <= 3.402823466e38 and lower <= x <= upper for x in rest):
                    raise ValueError("Invalid authored animation property")
                mark(obj, "fillyMaterial", index)
                target_kind = 0
            elif material and uv and uv[1] in UV_PROPERTIES:
                index = int(material[1])
                obj = _at(doc.get("materials", []), index)
                info = obj
                for part in uv[1].split("/"):
                    info = info[part]
                transform = info["extensions"]["KHR_texture_transform"]
                offset, scale, angle = transform.get("offset", [0, 0]), transform.get("scale", [1, 1]), transform.get("rotation", 0)
                if len(offset) != 2 or len(scale) != 2 or not all(math.isfinite(x) and abs(x) <= 3.402823466e38 for x in [*offset, *scale, angle]):
                    raise ValueError("Invalid animated texture transform")
                matrix = UV_PROPERTIES[uv[1]]
                transpose = matrix not in ("anisotropyUvMatrix", "iridescenceUvMatrix", "iridescenceThicknessUvMatrix", "diffuseUvMatrix", "diffuseColorUvMatrix")
                setup = [*offset, *scale, angle, int(transpose)]
                parameter = matrix + "/" + uv[2]
                components = 1 if uv[2] == "rotation" else 2
                lower, upper, target_kind = -math.inf, math.inf, 3
                mark(obj, "fillyMaterial", index)
            elif camera:
                index, projection, parameter = int(camera[1]), camera[2], camera[3]
                obj = _at(doc.get("cameras", []), index)
                fields = ("xmag", "ymag", "znear", "zfar") if projection == "orthographic" else ("yfov", "aspectRatio", "znear", "zfar")
                if obj["type"] != projection or parameter not in fields or (parameter == "zfar" and parameter not in obj[projection]):
                    raise ValueError("Camera pointer does not name a defined property")
                lower = 0 if projection == "orthographic" and parameter == "znear" else 1e-7
                upper = math.pi-1e-6 if parameter == "yfov" else math.inf
                components, target_kind = 1, 2
                parameter = str(fields.index(parameter))
            elif light:
                index, parameter = int(light[1]), light[2]
                obj = _at(doc["extensions"]["KHR_lights_punctual"]["lights"], index)
                components, lower, upper = (3, 0, 1) if parameter == "color" else (1, 0, math.inf)
                target_kind = 1
                if parameter == "range":
                    if obj["type"] == "directional" or "range" not in obj or not 0 < obj["range"] <= 3.402823466e38:
                        raise ValueError("Range animation requires an authored point/spot light range")
                    lower = 1e-30
                elif parameter.startswith("spot/"):
                    if obj["type"] != "spot":
                        raise ValueError("Cone animation requires a spot light")
                    spot = obj["spot"]
                    setup = [spot.get("innerConeAngle", 0), spot.get("outerConeAngle", math.pi/4)]
                    if not all(math.isfinite(x) for x in setup) or not 0 <= setup[0] < setup[1] <= math.pi/2:
                        raise ValueError("Invalid authored spot light cones")
                    lower = 0 if parameter.endswith("innerConeAngle") else 1e-30
                    # float32(pi/2) rounds upward; allow that representation for accessor values.
                    upper, target_kind = 1.5707963705062866, 4
                for node in doc.get("nodes", []):
                    if node.get("extensions", {}).get("KHR_lights_punctual", {}).get("light") == index:
                        mark(node, "fillyLight", index)
                        mark(node, "fillyAutoRange", int("range" not in obj and obj["type"] != "directional"))
            else:
                issue(f"Unsupported KHR_animation_pointer target: {pointer}", required)
                continue
            if pointer in seen:
                raise ValueError("Duplicate animation pointer target")
            seen.add(pointer)
            sampler = _at(animation["samplers"], channel["sampler"])
            interpolation = sampler.get("interpolation", "LINEAR")
            if visibility:
                output = _at(doc.get("accessors", []), sampler["output"])
                if interpolation != "STEP" or output["componentType"] != 5121 or output.get("normalized", False):
                    raise ValueError("Visibility animation requires STEP and unnormalized unsigned bytes")
            if interpolation not in ("STEP", "LINEAR", "CUBICSPLINE"):
                raise ValueError("Invalid pointer animation interpolation")
            times = [row[0] for row in accessor(sampler["input"], 1, True)]
            if times[0] < 0 or any(a >= b for a, b in zip(times, times[1:])):
                raise ValueError("Animation times must be nonnegative and strictly increasing")
            values = accessor(sampler["output"], components)
            if visibility:
                values = [[int(bool(row[0]))] for row in values]
            cubic = interpolation == "CUBICSPLINE"
            if cubic and len(times) < 2:
                raise ValueError("Cubic animation requires at least two keyframes")
            if len(values) != len(times)*(3 if cubic else 1):
                raise ValueError("Animation output count does not match its input")
            keys = values[1::3] if cubic else values
            if not all(lower <= x <= upper for row in keys for x in row):
                raise ValueError("Animation keyframe value is outside its property's range")
            tracks.append((target_kind, index, parameter, components, ("STEP", "LINEAR", "CUBICSPLINE").index(interpolation),
                           lower, upper, times, [x for row in values for x in row], setup))
        native_index = len(native_animations) if channels else -1
        if channels:
            # Property samplers can use integer encodings that the node animator does not accept.
            indices = {old: new for new, old in enumerate(dict.fromkeys(ch["sampler"] for ch in channels))}
            samplers = [_at(animation["samplers"], old) for old in indices]
            native_channels = [dict(ch, sampler=indices[ch["sampler"]]) for ch in channels]
            native_animations.append(dict(animation, channels=native_channels, samplers=samplers))
        clips.append((animation.get("name", ""), native_index, tracks))
    if "animations" in doc:
        if native_animations:
            doc["animations"] = native_animations
        else:
            del doc["animations"]
    for key in ("extensionsUsed", "extensionsRequired"):
        if key in doc:
            doc[key] = [name for name in doc[key] if name != "KHR_animation_pointer"]
    return clips, cameras
