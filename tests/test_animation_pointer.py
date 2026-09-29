import base64
import json
import struct

import numpy as np
import pytest

import filly
from filly._assets import prepare
from test_features import lit, pack, unpack


def accessor(doc, binary, values, kind="SCALAR", component=5126, normalized=False):
    formats = {5126: "f", 5121: "B"}
    components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[kind]
    binary += b"\0" * (-len(binary) % 4)
    payload = struct.pack("<" + formats[component]*len(values), *values)
    view = len(doc["bufferViews"])
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(payload)})
    binary += payload
    index = len(doc["accessors"])
    acc = {"bufferView": view, "componentType": component, "count": len(values)//components, "type": kind}
    if normalized:
        acc["normalized"] = True
    if kind == "SCALAR":
        acc.update(min=[min(values)], max=[max(values)])
    doc["accessors"].append(acc)
    return index


def add_clip(doc, binary, pointer, values, *, times=(0, 2), kind="SCALAR", interpolation="LINEAR", component=5126, normalized=False):
    for key in ("extensionsUsed", "extensionsRequired"):
        if "KHR_animation_pointer" not in doc.setdefault(key, []):
            doc[key].append("KHR_animation_pointer")
    source = accessor(doc, binary, times)
    output = accessor(doc, binary, values, kind, component, normalized)
    clip = {"name": "property", "samplers": [{"input": source, "output": output, "interpolation": interpolation}],
            "channels": [{"sampler": 0, "target": {"path": "pointer", "extensions": {"KHR_animation_pointer": {"pointer": pointer}}}}]}
    doc.setdefault("animations", []).append(clip)
    return clip


@pytest.mark.gpu
@pytest.mark.parametrize("interpolation,values,expected", [
    ("STEP", [0, 1], 0), ("LINEAR", [0, 1], 0.5),
    ("CUBICSPLINE", [0, 0, 1, 0, 1, 0], 0.75),
])
def test_interpolation_endpoints_reset_and_loop(renderer, scene, triangle_glb, interpolation, values, expected):
    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["pbrMetallicRoughness"]["metallicFactor"] = 0.2
    add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/metallicFactor", values, interpolation=interpolation)
    model = scene.load(pack(doc, binary), strict=True)
    material = model.material("red")
    assert model.animations[0].name == "property"
    assert model.animations[0].duration == 2
    model.apply_animation(0, 1)
    assert material.metallic == pytest.approx(expected)
    model.apply_animation(0, 2, loop=False)
    assert material.metallic == 1
    model.apply_animation(0, 3)
    assert material.metallic == pytest.approx(expected)
    model.apply_animation(0, 0)
    assert material.metallic == 0
    model.reset_animation()
    assert material.metallic == pytest.approx(0.2)
    model.close()


@pytest.mark.gpu
@pytest.mark.parametrize("path,values,kind", [
    ("translation", [0, 0, 0, 0.5, 0, 0], "VEC3"),
    ("scale", [1, 1, 1, 2, 1, 1], "VEC3"),
    ("rotation", [0, 0, 0, 1, 0, 0, 1, 0], "VEC4"),
])
def test_node_pointer_matches_core_animation(scene, triangle_glb, path, values, kind):
    doc, binary = unpack(triangle_glb)
    clip = add_clip(doc, binary, "/nodes/0/"+path, values, kind=kind)
    pointer_model = scene.load(pack(doc, binary), strict=True)
    clip["channels"][0]["target"] = {"node": 0, "path": path}
    core_model = scene.load(pack(doc, binary), strict=True)
    for time in (0, 1.4, 0.2, 2):
        pointer_model.apply_animation(0, time, loop=False)
        core_model.apply_animation(0, time, loop=False)
        np.testing.assert_allclose(pointer_model.node("triangle").transform, core_model.node("triangle").transform)
    pointer_model.reset_animation()
    np.testing.assert_allclose(pointer_model.node("triangle").transform, np.eye(4))


@pytest.mark.gpu
def test_material_pixels_normalized_output_and_clip_switch(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/baseColorFactor", [255, 0, 0, 255, 0, 255, 0, 255],
             kind="VEC4", component=5121, normalized=True)
    add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/roughnessFactor", [0, 1])
    model = scene.load(pack(doc, binary))
    target = renderer.create_render_target(width=32, height=32)
    model.apply_animation(0, 2, loop=False)
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read()[16, 16], [0, 255, 0, 255])
    model.apply_animation(1, 1)
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read()[16, 16], [255, 0, 0, 255])
    model.apply_animation(0, 1)
    first = model.material("red").base_color
    model.apply_animation(0, 0.2)
    model.apply_animation(0, 1)
    np.testing.assert_array_equal(model.material("red").base_color, first)


@pytest.mark.gpu
@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
def test_pointer_uses_material_index_and_reaches_node_local_copy(triangle_glb, mode):
    doc, binary = unpack(triangle_glb)
    doc["materials"].append(json.loads(json.dumps(doc["materials"][0])))
    doc["materials"][1]["name"] = "animated"
    doc["meshes"].append(json.loads(json.dumps(doc["meshes"][0])))
    doc["meshes"][1]["primitives"][0]["material"] = 1
    doc["nodes"].append({"name": "other", "mesh": 1})
    doc["scenes"][0]["nodes"].append(1)
    add_clip(doc, binary, "/materials/1/pbrMetallicRoughness/baseColorFactor", [1, 0, 0, 1, 0, 1, 0, 1], kind="VEC4")
    with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
        model = renderer.create_scene().load(pack(doc, binary))
        first = model.material("red")
        second = model.material("animated")
        # Property tracks target glTF material indices; copies of that material follow them.
        local = model.node("other").material()
        model.apply_animation(0, 2, loop=False)
        np.testing.assert_allclose(first.base_color, [1, 0, 0, 1])
        np.testing.assert_allclose(second.base_color, [0, 1, 0, 1])
        np.testing.assert_allclose(local.base_color, [0, 1, 0, 1])
        model.reset_animation()
        np.testing.assert_allclose(second.base_color, [1, 0, 0, 1])
        np.testing.assert_allclose(local.base_color, [1, 0, 0, 1])


@pytest.mark.gpu
@pytest.mark.parametrize("clone", [False, True])
def test_node_local_copy_edits_follow_the_animation_rule(renderer, scene, triangle_glb, clone):
    """Animated properties overwrite edits to a copy; edits to other properties persist."""
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["materials"][0]["pbrMetallicRoughness"].update(baseColorFactor=[1, 0, 0, 1], metallicFactor=0.2, roughnessFactor=0.9)
    add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/baseColorFactor", [1, 0, 0, 1, 0, 0, 1, 1], kind="VEC4")
    add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/metallicFactor", [0.4, 0.8])
    source = scene.load(pack(doc, binary), strict=True, clonable=clone)
    model = source.clone() if clone else source
    shared = model.material("red")
    local = model.node("triangle").material()
    local.base_color = (1, 1, 0, 1)
    local.metallic = 0.6
    local.roughness = 0.3
    model.apply_animation(0, 1)
    # Clip 0 animates the base color: the copy's edit is overwritten on both copies of the value.
    np.testing.assert_allclose(local.base_color, [0.5, 0, 0.5, 1], atol=1e-6)
    np.testing.assert_allclose(shared.base_color, [0.5, 0, 0.5, 1], atol=1e-6)
    # Metallic is animated by clip 1, so applying any clip restores its rest value first.
    assert local.metallic == pytest.approx(0.2)
    # Roughness is not animated: the edit to the copy persists, and the original keeps its own.
    assert local.roughness == pytest.approx(0.3)
    assert shared.roughness == pytest.approx(0.9)
    model.apply_animation(1, 1)
    assert local.metallic == pytest.approx(0.6) and shared.metallic == pytest.approx(0.6)
    local.base_color = (0, 1, 0, 1)
    model.reset_animation()
    # Reset restores animated properties to the original's rest values, not to the edits.
    np.testing.assert_allclose(local.base_color, [1, 0, 0, 1])
    assert local.metallic == pytest.approx(0.2)
    assert local.roughness == pytest.approx(0.3)
    if clone:
        # The source instance and its own materials are separate.
        np.testing.assert_allclose(source.material("red").base_color, [1, 0, 0, 1])
        assert source.material("red").metallic == pytest.approx(0.2)


@pytest.mark.gpu
@pytest.mark.parametrize("explicit_range", [False, True])
def test_light_pixels_multiple_instances_and_close(renderer, scene, triangle_glb, explicit_range):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["extensionsUsed"] = ["KHR_lights_punctual"]
    light = {"type": "point", "intensity": 0}
    if explicit_range:
        light["range"] = 10
    doc["extensions"] = {"KHR_lights_punctual": {"lights": [light]}}
    for name, x in (("a", -0.2), ("b", 0.2)):
        doc["nodes"].append({"name": name, "translation": [x, 0, 2], "extensions": {"KHR_lights_punctual": {"light": 0}}})
        doc["scenes"][0]["nodes"].append(len(doc["nodes"])-1)
    clip = add_clip(doc, binary, "/extensions/KHR_lights_punctual/lights/0/intensity", [0, 1000])
    output = accessor(doc, binary, [1, 0, 0, 0, 1, 0], "VEC3")
    clip["samplers"].append({"input": clip["samplers"][0]["input"], "output": output})
    clip["channels"].append({"sampler": 1, "target": {"path": "pointer", "extensions": {"KHR_animation_pointer": {
        "pointer": "/extensions/KHR_lights_punctual/lights/0/color"}}}})
    model = scene.load(pack(doc, binary))
    scene.camera.exposure = 6
    target = renderer.create_render_target(width=32, height=32)
    model.apply_animation(0, 0)
    renderer.render(scene, target)
    assert target.read()[16, 16, :3].max() == 0
    model.apply_animation(0, 2, loop=False)
    assert model.light("a").intensity == model.light("b").intensity == 1000
    renderer.render(scene, target)
    pixel = target.read()[16, 16]
    assert pixel[1] > 20 and pixel[0] == pixel[2] == 0
    model.light("a").close()
    model.reset_animation()
    assert model.light("a").closed
    assert model.light("b").intensity == 0
    np.testing.assert_allclose(model.light("b").color, [1, 1, 1])


@pytest.mark.gpu
@pytest.mark.parametrize("path,start,end", [
    ("emissiveFactor", [0, 0, 0], [0.5, 0.5, 0.5]),
    ("extensions/KHR_materials_emissive_strength/emissiveStrength", [1], [2]),
    ("extensions/KHR_materials_clearcoat/clearcoatFactor", [0], [1]),
    ("extensions/KHR_materials_clearcoat/clearcoatRoughnessFactor", [0], [1]),
    ("extensions/KHR_materials_ior/ior", [1.5], [2]),
    ("extensions/KHR_materials_sheen/sheenColorFactor", [0, 0, 0], [1, 0, 0]),
    ("extensions/KHR_materials_sheen/sheenRoughnessFactor", [0], [1]),
    ("extensions/KHR_materials_transmission/transmissionFactor", [0], [1]),
    ("extensions/KHR_materials_volume/thicknessFactor", [0], [1]),
    ("extensions/KHR_materials_dispersion/dispersion", [0], [1]),
    ("extensions/KHR_materials_specular/specularFactor", [0], [1]),
    ("extensions/KHR_materials_specular/specularColorFactor", [0, 0, 0], [1, 0, 0]),
    ("extensions/KHR_materials_diffuse_transmission/diffuseTransmissionFactor", [0], [1]),
    ("extensions/KHR_materials_diffuse_transmission/diffuseTransmissionColorFactor", [0, 0, 0], [1, 0, 0]),
])
def test_extended_material_tracks(renderer, scene, triangle_glb, path, start, end):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    parent = doc["materials"][0]
    for key in path.split("/")[:-1]:
        parent = parent.setdefault(key, {})
    parent[path.split("/")[-1]] = start if len(start) > 1 else start[0]
    extensions = doc["materials"][0].get("extensions", {})
    if any(key in path for key in ("transmission", "volume", "dispersion")) and "diffuse" not in path:
        extensions.setdefault("KHR_materials_transmission", {"transmissionFactor": 0.5})
        scene.refraction = True
        scene.tone_mapping = "aces_legacy"
    if "dispersion" in path:
        extensions["KHR_materials_volume"] = {"thicknessFactor": 1}
    doc["extensionsUsed"] = list(extensions)
    add_clip(doc, binary, "/materials/0/"+path, start+end, kind="SCALAR" if len(start) == 1 else "VEC3")
    model = scene.load(pack(doc, binary), strict=True)
    model.apply_animation(0, 1)
    renderer.render(scene, renderer.create_render_target(width=16, height=16))
    model.reset_animation()
    model.close()


@pytest.mark.gpu
def test_mixed_node_and_pointer_timeline(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    # Preserve original clip indices when a property-only clip precedes a mixed clip.
    add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/roughnessFactor", [0, 1])
    clip = add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/metallicFactor", [0, 1])
    times = accessor(doc, binary, [0, 1])
    values = accessor(doc, binary, [0, 0, 0, 0.7, 0, 0], "VEC3")
    clip["samplers"].append({"input": times, "output": values})
    clip["channels"].append({"sampler": 1, "target": {"node": 0, "path": "translation"}})
    model = scene.load(pack(doc, binary))
    assert len(model.animations) == 2
    assert model.animations[1].duration == 2
    model.apply_animation(1, 1.5)
    assert model.node("triangle").position[0] == pytest.approx(0.7)
    assert model.material("red").metallic == pytest.approx(0.75)
    model.apply_animation(0, 1)
    assert model.node("triangle").position[0] == 0


def test_accessor_sparse_stride_external_and_data_uri(triangle_glb, tmp_path):
    doc, binary = unpack(triangle_glb)
    clip = add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/roughnessFactor", [0, 1])
    acc = doc["accessors"][clip["samplers"][0]["output"]]
    view = doc["bufferViews"][acc["bufferView"]]
    view.update(byteOffset=len(binary), byteLength=16, byteStride=8)
    binary += struct.pack("<4f", 0.25, 99, 0.5, 99)
    index_view = len(doc["bufferViews"])
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": 1})
    binary += b"\1\0\0\0"
    value_view = len(doc["bufferViews"])
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": 4})
    binary += struct.pack("<f", 0.75)
    acc["sparse"] = {"count": 1, "indices": {"bufferView": index_view, "componentType": 5121}, "values": {"bufferView": value_view}}
    doc["buffers"][0]["byteLength"] = len(binary)
    for uri in ("keys.bin", "data:application/octet-stream;base64,"+base64.b64encode(binary).decode()):
        doc["buffers"][0]["uri"] = uri
        (tmp_path / "keys.bin").write_bytes(binary)
        prepared = prepare(json.dumps(doc).encode(), str(tmp_path / "model.gltf"))
        assert prepared[3][0][2][0][8] == [0.25, 0.75]


@pytest.mark.parametrize("failure", ["unsupported", "duplicate", "times", "count", "range", "bounds"])
def test_pointer_validation(triangle_glb, failure):
    doc, binary = unpack(triangle_glb)
    pointer = "/materials/0/pbrMetallicRoughness/roughnessFactor"
    if failure == "unsupported":
        pointer = "/materials/0/alphaCutoff"
    clip = add_clip(doc, binary, pointer, [0, 1], times=(0, 0) if failure == "times" else (0, 2))
    if failure == "duplicate":
        clip["channels"].append(clip["channels"][0])
    if failure == "count":
        doc["accessors"][clip["samplers"][0]["output"]]["count"] = 1
    if failure == "range":
        output = clip["samplers"][0]["output"]
        view = doc["bufferViews"][doc["accessors"][output]["bufferView"]]
        struct.pack_into("<f", binary, view["byteOffset"], 2)
    if failure == "bounds":
        doc["bufferViews"][-1]["byteLength"] = 1
    with pytest.raises(filly.AssetError):
        prepare(pack(doc, binary), "")
    if failure == "unsupported":
        doc["extensionsRequired"].remove("KHR_animation_pointer")
        with pytest.warns(filly.AssetCompatibilityWarning, match="target"):
            result = prepare(pack(doc, binary), "")
        assert result[3][0][2] == []
        with pytest.raises(filly.AssetError, match="target"):
            prepare(pack(doc, binary), "", strict=True)


def test_dispersion_requires_volume_before_native_shader_compilation(triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["materials"][0]["extensions"] = {"KHR_materials_dispersion": {"dispersion": 1}}
    with pytest.raises(filly.AssetError, match="Dispersion requires"):
        prepare(pack(doc, binary), "")
