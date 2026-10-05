import copy
import struct

import numpy as np
import pytest

import filly
from filly._native import _decode_meshopt
from test_animation_pointer import accessor, add_clip
from test_features import lit, pack, unpack


# Encoded with meshoptimizer v1.0. Source vertices match triangle_glb; indices
# are [0,1,2,2,1,3]; COLOR source is opaque red, green, blue, and white.
VERTEX0 = bytes.fromhex("a0010c000000cc010c000000ce010c00000031013c000000ff7d0000010c000000ff010c000000fd0000000000000000000000000000000000000000000000009a9919bfcdccccbe00000000")
VERTEX1 = bytes.fromhex("a1fffaaa0000cc0000ce00003100ff7d0000ff0000fd0000000000000000009a9919bfcdccccbe00000000000000")
TRIANGLES = bytes.fromhex("e1f010007687566778a9866589689801690000")
INDICES = bytes.fromhex("d100040400020800000000")
COLOR = bytes.fromhex("a1bf007e7d8100fdfdfe0083828000000000000000000000000000000000000000407fc1ff00")


@pytest.mark.parametrize("payload", [VERTEX0, VERTEX1])
def test_meshopt_vertex_versions(payload, triangle_glb):
    _, binary = unpack(triangle_glb)
    assert _decode_meshopt(payload, 3, 12, "ATTRIBUTES", "NONE") == binary


@pytest.mark.parametrize("mode,payload", [("TRIANGLES", TRIANGLES), ("INDICES", INDICES)])
@pytest.mark.parametrize("stride", [2, 4])
def test_meshopt_index_modes(mode, payload, stride):
    assert _decode_meshopt(payload, 6, stride, mode, "NONE") == struct.pack("<6" + ("H" if stride == 2 else "I"), 0, 1, 2, 2, 1, 3)


def test_meshopt_color_filter():
    result = np.frombuffer(_decode_meshopt(COLOR, 4, 4, "ATTRIBUTES", "COLOR"), dtype=np.uint8).reshape(4, 4)
    np.testing.assert_allclose(result, [[255,0,0,255], [0,255,0,255], [0,0,255,255], [255]*4], atol=1)


@pytest.mark.parametrize("payload,count,stride,mode,filter", [
    (VERTEX1[:-1], 3, 12, "ATTRIBUTES", "NONE"), (VERTEX1, 3, 12, "ATTRIBUTES", "COLOR"),
    (VERTEX1, 3, 12, "ATTRIBUTES", "QUATERNION"), (VERTEX1, 0, 12, "ATTRIBUTES", "NONE"),
    (INDICES, 6, 3, "INDICES", "NONE"), (INDICES, 6, 4, "INDICES", "EXPONENTIAL"),
])
def test_meshopt_invalid_streams(payload, count, stride, mode, filter):
    with pytest.raises(filly.AssetError):
        _decode_meshopt(payload, count, stride, mode, filter)


@pytest.mark.gpu
@pytest.mark.parametrize("extension,payload", [("EXT_meshopt_compression", VERTEX0), ("KHR_meshopt_compression", VERTEX1)])
def test_meshopt_required_without_fallback(renderer, scene, triangle_glb, extension, payload):
    doc, binary = unpack(triangle_glb)
    model = scene.load(triangle_glb)
    target = renderer.create_render_target(width=32, height=32)
    renderer.render(scene, target)
    expected = target.read()
    model.close()
    doc["extensionsUsed"].append(extension)
    doc["extensionsRequired"].append(extension)
    doc["buffers"].append({"byteLength": 36, "extensions": {extension: {"fallback": True}}})
    doc["bufferViews"][0].update(buffer=1, extensions={extension: {
        "buffer": 0, "byteLength": len(payload), "byteStride": 12, "count": 3, "mode": "ATTRIBUTES"}})
    scene.load(pack(doc, bytearray(payload)), strict=True)
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read(), expected)


@pytest.mark.gpu
def test_visibility_parent_animation_model_toggle_and_reset(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["nodes"] += [{"name": "parent", "children": [0], "extensions": {"KHR_node_visibility": {"visible": False}}}]
    doc["scenes"][0]["nodes"] = [1]
    doc["extensionsUsed"].append("KHR_node_visibility")
    add_clip(doc, binary, "/nodes/1/extensions/KHR_node_visibility/visible", [0, 255], component=5121, interpolation="STEP")
    model = scene.load(pack(doc, binary), strict=True)
    target = renderer.create_render_target(width=32, height=32)
    def pixel():
        renderer.render(scene, target)
        return int(target.read()[16, 16, 0])
    hidden = pixel()
    model.visible = False
    model.visible = True
    assert pixel() == hidden
    model.apply_animation(0, 2, loop=False)
    assert pixel() > hidden + 100
    model.visible = False
    assert pixel() == hidden
    model.visible = True
    assert pixel() > hidden + 100
    model.apply_animation(0, 0)
    assert pixel() == hidden
    model.apply_animation(0, 2, loop=False)
    model.reset_animation()
    assert pixel() == hidden


@pytest.mark.gpu
@pytest.mark.parametrize("component,interpolation", [(5126,"STEP"), (5121,"LINEAR")])
def test_visibility_rejects_non_boolean_sampler(scene, triangle_glb, component, interpolation):
    doc, binary = unpack(triangle_glb)
    doc["nodes"][0]["extensions"] = {"KHR_node_visibility": {}}
    add_clip(doc, binary, "/nodes/0/extensions/KHR_node_visibility/visible", [0,1], component=component, interpolation=interpolation)
    with pytest.raises(filly.AssetError):
        scene.load(pack(doc, binary))


@pytest.mark.gpu
def test_instances_match_explicit_hierarchy(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["nodes"][0].update(scale=[0.4]*3, translation=[0.1,0.1,0])
    translations = accessor(doc, binary, [-1,0,0, 1,0,0], "VEC3")
    scales = accessor(doc, binary, [0.5,1,1, 1,0.5,1], "VEC3")
    doc["nodes"][0]["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": {"TRANSLATION": translations, "SCALE": scales}}}
    doc["extensionsUsed"].append("EXT_mesh_gpu_instancing")
    doc["extensionsRequired"].append("EXT_mesh_gpu_instancing")
    doc["meshes"][0]["name"] = "shape"
    model = scene.load(pack(doc,binary), strict=True)
    # Expanded instances are unnamed children that keep the mesh name.
    instances = model.node(0).children
    assert [(n.index, n.name, n.mesh_name) for n in instances] == [(1, None, "shape"), (2, None, "shape")]
    assert model.node(0).mesh_name is None
    target = renderer.create_render_target(width=64,height=64)
    renderer.render(scene,target)
    actual = target.read()
    assert np.count_nonzero(actual[:,:,0] > 100) > 50
    model.close()
    doc["nodes"][0].pop("extensions")
    doc["nodes"][0].pop("mesh")
    doc["nodes"][0]["children"] = [1,2]
    doc["nodes"] += [{"mesh":0,"translation":[-1,0,0],"scale":[0.5,1,1]}, {"mesh":0,"translation":[1,0,0],"scale":[1,0.5,1]}]
    scene.load(pack(doc,binary), strict=True)
    renderer.render(scene,target)
    np.testing.assert_array_equal(actual,target.read())


@pytest.mark.gpu
def test_instance_counts_must_match(scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    t = accessor(doc, binary, [0]*6, "VEC3")
    s = accessor(doc, binary, [1]*3, "VEC3")
    doc["nodes"][0]["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": {"TRANSLATION":t,"SCALE":s}}}
    with pytest.raises(filly.AssetError, match="counts must match"):
        scene.load(pack(doc,binary))


@pytest.mark.gpu
def test_visibility_hides_lights_but_keeps_cameras(renderer, scene, triangle_glb):
    doc,binary=unpack(triangle_glb)
    lit(doc)
    doc["extensionsUsed"]=["KHR_lights_punctual","KHR_node_visibility"]
    doc["extensions"]={"KHR_lights_punctual":{"lights":[{"type":"directional","intensity":100000}]}}
    doc["cameras"]=[{"type":"orthographic","orthographic":{"xmag":1,"ymag":1,"znear":0.1,"zfar":10}}]
    doc["nodes"] += [{"extensions":{"KHR_lights_punctual":{"light":0}}},
                     {"name":"imported-camera","camera":0,"translation":[0,0,3]},
                     {"children":[1,2],"extensions":{"KHR_node_visibility":{"visible":False}}}]
    doc["scenes"][0]["nodes"]=[0,3]
    add_clip(doc,binary,"/nodes/3/extensions/KHR_node_visibility/visible",[0,1],component=5121,interpolation="STEP")
    model=scene.load(pack(doc,binary),strict=True)
    scene.camera=model.camera("imported-camera")
    scene.camera.exposure=15
    target=renderer.create_render_target(width=32,height=32)
    renderer.render(scene,target)
    dark=int(target.read()[16,16,0])
    model.apply_animation(0,2,loop=False)
    renderer.render(scene,target)
    assert int(target.read()[16,16,0]) > dark+30
    model.reset_animation()
    renderer.render(scene,target)
    assert int(target.read()[16,16,0]) == dark


@pytest.mark.gpu
def test_clip_switch_restores_morph_weights(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    offsets = accessor(doc, binary, [0, 0.3, 0] * 3, "VEC3")
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": offsets}]
    doc["nodes"][0]["weights"] = [0.5]
    times = accessor(doc, binary, [0, 2])
    weights = accessor(doc, binary, [0, 1])
    shift = accessor(doc, binary, [0, 0, 0, 0, -0.2, 0], "VEC3")
    doc["animations"] = [
        {"name": "lift", "samplers": [{"input": times, "output": weights}], "channels": [{"sampler": 0, "target": {"node": 0, "path": "weights"}}]},
        {"name": "drop", "samplers": [{"input": times, "output": shift}], "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}]},
    ]
    document = pack(doc, binary)
    target = renderer.create_render_target(width=64, height=64)
    fresh = scene.load(document)
    fresh.apply_animation("drop", 1)
    renderer.render(scene, target)
    expected = target.read()
    fresh.close()
    model = scene.load(document)
    model.apply_animation("lift", 2, loop=False)
    renderer.render(scene, target)
    assert not np.array_equal(expected, target.read())
    # The authored weight returns, not the last weight of "lift".
    model.apply_animation("drop", 1)
    renderer.render(scene, target)
    np.testing.assert_array_equal(expected, target.read())


@pytest.mark.gpu
def test_instance_normalized_sparse_rotation_and_morph_channels(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    # A morph target that lifts the triangle, driven by a weights channel on the instanced node.
    offsets = accessor(doc, binary, [0, 0.3, 0] * 3, "VEC3")
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": offsets}]
    doc["meshes"][0]["weights"] = [0]
    # Sparse signed-byte quaternions, with default identity for the first instance.
    base=len(binary)
    binary += struct.pack("<8b",0,0,0,127,0,0,0,127)
    base_view=len(doc["bufferViews"])
    doc["bufferViews"].append({"buffer":0,"byteOffset":base,"byteLength":8})
    index_view=len(doc["bufferViews"])
    doc["bufferViews"].append({"buffer":0,"byteOffset":len(binary),"byteLength":1})
    binary += b"\x01\0\0\0"
    value_view=len(doc["bufferViews"])
    doc["bufferViews"].append({"buffer":0,"byteOffset":len(binary),"byteLength":4})
    binary += struct.pack("<4b",0,0,127,0)
    rotation=len(doc["accessors"])
    doc["accessors"].append({"bufferView":base_view,"componentType":5120,"normalized":True,"type":"VEC4","count":2,
        "sparse":{"count":1,"indices":{"bufferView":index_view,"componentType":5121},"values":{"bufferView":value_view}}})
    translation = accessor(doc, binary, [-0.4, 0, 0, 0.4, 0, 0], "VEC3")
    doc["nodes"][0]["extensions"]={"EXT_mesh_gpu_instancing":{"attributes":{"ROTATION":rotation, "TRANSLATION": translation}}}
    times = accessor(doc, binary, [0, 2])
    weights = accessor(doc, binary, [0, 1])
    doc["animations"]=[{"samplers":[{"input": times, "output": weights}],"channels":[{"sampler":0,"target":{"node":0,"path":"weights"}}]}]
    model = scene.load(pack(doc, binary))
    np.testing.assert_allclose(model.node(1).quaternion, [0, 0, 0, 1])
    np.testing.assert_allclose(model.node(2).quaternion, [0, 0, 1, 0])
    # Preparation retargets the weights channel to every expanded mesh node.
    model.apply_animation(0, 2, loop=False)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    animated = target.read()
    model.close()
    doc["nodes"][0].pop("extensions")
    doc["nodes"][0].pop("mesh")
    doc["nodes"][0]["children"] = [1, 2]
    doc["nodes"] += [{"mesh": 0, "translation": [-0.4, 0, 0], "weights": [1]},
                     {"mesh": 0, "translation": [0.4, 0, 0], "rotation": [0, 0, 1, 0], "weights": [1]}]
    doc.pop("animations")
    scene.load(pack(doc, binary))
    renderer.render(scene, target)
    np.testing.assert_array_equal(animated, target.read())
