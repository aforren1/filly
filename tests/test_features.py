import base64
import io
import json
import struct
from pathlib import Path
import warnings

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.gpu


def unpack(data):
    size = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20+size]), bytearray(data[28+size:])


def pack(doc, binary):
    binary += b"\0" * (-len(binary) % 4)
    doc["buffers"][0]["byteLength"] = len(binary)
    encoded = json.dumps(doc).encode()
    encoded += b" " * (-len(encoded) % 4)
    return struct.pack("<III", 0x46546C67, 2, 28+len(encoded)+len(binary)) + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + struct.pack("<II", len(binary), 0x004E4942) + binary


def lit(doc):
    doc.pop("extensionsUsed", None)
    doc.pop("extensionsRequired", None)
    doc["materials"][0].pop("extensions", None)
    doc["materials"][0]["pbrMetallicRoughness"].update(baseColorFactor=[1,1,1,1], metallicFactor=0, roughnessFactor=1)


def test_animation_time_jumps_and_reset(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    binary += struct.pack("<8f", 0, 1, 0,0,0, 0.7,0,0)
    doc["bufferViews"] += [{"buffer":0,"byteOffset":offset,"byteLength":8}, {"buffer":0,"byteOffset":offset+8,"byteLength":24}]
    doc["accessors"] += [{"bufferView":1,"componentType":5126,"count":2,"type":"SCALAR","min":[0],"max":[1]},
                         {"bufferView":2,"componentType":5126,"count":2,"type":"VEC3"}]
    doc["animations"] = [{"name":"slide", "samplers":[{"input":1,"output":2,"interpolation":"LINEAR"}],
                          "channels":[{"sampler":0,"target":{"node":0,"path":"translation"}}]}]
    model = scene.load(pack(doc,binary))
    assert model.animations[0].name == "slide"
    assert model.animations[0].duration == pytest.approx(1)
    model.position = (0.1,0,0)
    model.apply_animation(0,0.5)
    np.testing.assert_allclose(model.node("triangle").position, [0.35,0,0], atol=1e-6)
    target = renderer.create_render_target(width=32,height=32)
    renderer.render(scene,target); first=target.read()
    model.apply_animation(0,0.9)
    model.apply_animation(0,0.5)
    renderer.render(scene,target)
    np.testing.assert_array_equal(first,target.read())
    np.testing.assert_allclose(model.position,[0.1,0,0])
    model.reset_animation()
    np.testing.assert_allclose(model.node("triangle").position,[0,0,0])
    model.apply_animation(0, 1.5)
    np.testing.assert_allclose(model.node("triangle").position, [0.35,0,0], atol=1e-6)
    model.apply_animation(0, 3, loop=False)
    np.testing.assert_allclose(model.node("triangle").position, [0.7,0,0], atol=1e-6)
    model.apply_animation("slide", 0.5)
    np.testing.assert_allclose(model.node("triangle").position, [0.35,0,0], atol=1e-6)
    with pytest.raises(filly.AssetError, match="index 9"): model.apply_animation(9,0)
    with pytest.raises(filly.AssetError, match="Unknown animation"): model.apply_animation("missing",0)
    with pytest.raises(ValueError): model.apply_animation(0,float("nan"))
    with pytest.raises(ValueError): model.apply_animation(0,-1)


def test_animation_clip_switches_restore_other_channels(renderer, scene, triangle_glb):
    # gltfio keeps each node's translation, rotation, and scale apart, so a channel that only
    # the previous clip animated must still return to rest.
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    s = 2 ** -0.5
    binary += struct.pack("<2f6f8f", 0, 1, 0, 0, 0, 0.7, 0, 0, 0, 0, 0, 1, 0, 0, s, s)
    doc["bufferViews"] += [{"buffer": 0, "byteOffset": offset, "byteLength": 8},
                           {"buffer": 0, "byteOffset": offset + 8, "byteLength": 24},
                           {"buffer": 0, "byteOffset": offset + 32, "byteLength": 32}]
    doc["accessors"] += [{"bufferView": 1, "componentType": 5126, "count": 2, "type": "SCALAR", "min": [0], "max": [1]},
                         {"bufferView": 2, "componentType": 5126, "count": 2, "type": "VEC3"},
                         {"bufferView": 3, "componentType": 5126, "count": 2, "type": "VEC4"}]
    doc["nodes"].append({"name": "marker", "translation": [0, 0.25, 0]})
    doc["scenes"][0]["nodes"].append(1)
    doc["animations"] = [
        {"name": "slide", "samplers": [{"input": 1, "output": 2}], "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}]},
        {"name": "spin", "samplers": [{"input": 1, "output": 3}], "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}]},
        {"name": "both", "samplers": [{"input": 1, "output": 2}, {"input": 1, "output": 3}],
         "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}},
                      {"sampler": 1, "target": {"node": 0, "path": "rotation"}}]},
    ]
    document = pack(doc, binary)
    calls = [("slide", 1.0, False), ("spin", 0.5, True), ("both", 0.25, True), ("slide", 0.5, True),
             ("spin", 1.0, False), ("both", 0.75, True), ("spin", 0.25, True)]
    model = scene.load(document)
    triangle, marker = model.node("triangle"), model.node("marker")
    marker.position = (0, 0.5, 0)
    for clip, time, loop in calls:
        model.apply_animation(clip, time, loop=loop)
        # A fresh model with no history gives the reference pose.
        fresh = scene.load(document)
        fresh.apply_animation(clip, time, loop=loop)
        np.testing.assert_allclose(triangle.transform, fresh.node("triangle").transform, atol=1e-6, err_msg=clip)
        fresh.close()
    np.testing.assert_allclose(triangle.position, [0, 0, 0], atol=1e-6)
    np.testing.assert_allclose(triangle.quaternion, [0, 0, np.sin(np.pi / 16), np.cos(np.pi / 16)], atol=1e-6)
    # Nodes that no clip animates keep their edits.
    np.testing.assert_allclose(marker.position, [0, 0.5, 0])
    model.apply_animation("slide", 1, loop=False)
    model.reset_animation()
    np.testing.assert_allclose(triangle.transform, np.eye(4), atol=1e-6)
    np.testing.assert_allclose(marker.position, [0, 0.25, 0])
    model.apply_animation("spin", 1, loop=False)
    np.testing.assert_allclose(triangle.position, [0, 0, 0], atol=1e-6)


def test_variant_pixels(renderer, scene, triangle_glb):
    doc,binary=unpack(triangle_glb)
    doc["extensionsUsed"].append("KHR_materials_variants")
    doc["extensions"]={"KHR_materials_variants":{"variants":[{"name":"green"}]}}
    doc["materials"].append({"name":"green","extensions":{"KHR_materials_unlit":{}},"pbrMetallicRoughness":{"baseColorFactor":[0,1,0,1]}})
    doc["meshes"][0]["primitives"][0]["extensions"]={"KHR_materials_variants":{"mappings":[{"material":1,"variants":[0]}]}}
    model=scene.load(pack(doc,binary)); assert model.variants==["green"]
    target=renderer.create_render_target(width=32,height=32)
    model.apply_variant(0); renderer.render(scene,target)
    np.testing.assert_array_equal(target.read()[16,16],[0,255,0,255])


def test_light_controls_and_exposure(renderer, scene, triangle_glb):
    doc,binary=unpack(triangle_glb);lit(doc)
    scene.load(pack(doc,binary))
    light=scene.add_point_light(position=(0,0,2),intensity=1000,range=10)
    assert light.type=="point"
    assert light.intensity==pytest.approx(1000)
    target=renderer.create_render_target(width=32,height=32)
    scene.camera.exposure=6
    renderer.render(scene,target); bright=target.read()
    light.intensity=0
    renderer.render(scene,target); dark=target.read()
    assert bright[16,16,0]>dark[16,16,0]+20
    light.casts_shadows=True
    assert light.casts_shadows
    spot=scene.add_spot_light(position=(0,0,2),direction=(0,0,-1))
    spot.casts_shadows=True
    assert spot.casts_shadows
    renderer.close()
    with pytest.raises(filly.FillyError):_=light.color


def test_environment_lights_metal(renderer, scene, triangle_glb):
    doc,binary=unpack(triangle_glb);lit(doc)
    doc["materials"][0]["pbrMetallicRoughness"].update(metallicFactor=1,roughnessFactor=0.3)
    scene.load(pack(doc,binary))
    target=renderer.create_render_target(width=32,height=32)
    renderer.render(scene,target); dark=target.read()
    scene.set_environment(np.ones((8,16,3),dtype="f"),intensity=50000)
    renderer.render(scene,target); bright=target.read()
    assert bright[16,16,0]>dark[16,16,0]+20
    scene.clear_environment()
    renderer.render(scene,target)
    np.testing.assert_array_equal(dark,target.read())


def test_diffuse_transmission_backlight(renderer, scene, triangle_glb):
    doc,binary=unpack(triangle_glb);lit(doc)
    doc["extensionsUsed"]=["KHR_materials_diffuse_transmission"]
    doc["materials"][0].update(doubleSided=True,extensions={"KHR_materials_diffuse_transmission":{
        "diffuseTransmissionFactor":1,"diffuseTransmissionColorFactor":[1,0,0]}})
    scene.load(pack(doc,binary))
    scene.add_directional_light(direction=(0,0,1),intensity=100000)
    target=renderer.create_render_target(width=32,height=32)
    renderer.render(scene,target)
    pixel=target.read()[16,16]
    assert pixel[0]>20 and pixel[1]<3 and pixel[2]<3, pixel


def test_diffuse_material_occlusion_texture(renderer, scene, triangle_glb):
    Image = pytest.importorskip("PIL.Image")
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission"]
    doc["materials"][0]["extensions"] = {"KHR_materials_diffuse_transmission": {"diffuseTransmissionFactor": 0}}
    doc["materials"][0]["occlusionTexture"] = {"index": 0}
    offset = len(binary)
    binary += struct.pack("<6f", 0, 0, 1, 0, 0.5, 1)
    doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 24})
    doc["accessors"].append({"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"})
    doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = 1
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), (0, 255, 255)).save(stream, format="PNG")
    doc["images"] = [{"uri": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}]
    doc["textures"] = [{"source": 0}]
    scene.set_environment(np.ones((8, 16, 3), dtype="f"), intensity=50000)
    target = renderer.create_render_target(width=32, height=32)
    dark_model = scene.load(pack(doc, binary))
    renderer.render(scene, target)
    dark = target.read()[16, 16, :3].astype(float)
    dark_model.close()
    doc["materials"][0]["occlusionTexture"]["strength"] = 0
    scene.load(pack(doc, binary))
    renderer.render(scene, target)
    bright = target.read()[16, 16, :3].astype(float)
    assert bright.mean() > dark.mean() + 20


def test_diffuse_material_texture_orientation(renderer, scene, triangle_glb):
    Image = pytest.importorskip("PIL.Image")

    def asset(texture, top, bottom):
        doc, binary = unpack(triangle_glb); lit(doc)
        offset = len(binary)
        binary += struct.pack("<6f", 0, 0, 1, 0, 0.5, 1)
        doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 24})
        doc["accessors"].append({"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"})
        doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = 1
        image = Image.new("RGB", (1, 2)); image.putpixel((0, 0), top); image.putpixel((0, 1), bottom)
        stream = io.BytesIO(); image.save(stream, format="PNG")
        doc["images"] = [{"uri": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}]
        doc["samplers"] = [{"magFilter": 9728, "minFilter": 9728}]
        doc["textures"] = [{"source": 0, "sampler": 0}]
        material = doc["materials"][0]
        material["doubleSided"] = True
        doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission", "KHR_materials_volume"]
        extension = {"diffuseTransmissionFactor": 0 if texture == "base" else 1}
        material["extensions"] = {"KHR_materials_diffuse_transmission": extension}
        if texture == "base":
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
        else:
            material["extensions"]["KHR_materials_volume"] = {"thicknessFactor": 1, "thicknessTexture": {"index": 0},
                                                              "attenuationColor": [0.1, 0.1, 0.1], "attenuationDistance": 0.1}
        return pack(doc, binary)

    target = renderer.create_render_target(width=32, height=32)
    light = scene.add_directional_light(direction=(0, 0, -1), intensity=100000)

    def rows(data):
        model = scene.load(data)
        renderer.render(scene, target)
        model.close()
        pixels = target.read().astype(int)
        # Row 20 samples near v=0 (image top); row 8 samples near v=1.
        return pixels[20, 16, :3], pixels[8, 16, :3]

    near_top, near_bottom = rows(asset("base", (255, 0, 0), (0, 255, 0)))
    assert near_top[0] > near_top[1] + 20 and near_bottom[1] > near_bottom[0] + 20, (near_top, near_bottom)
    # Backlight through a thickness map: the thick image top absorbs more light.
    light.direction = (0, 0, 1)
    thick, thin = rows(asset("thickness", (0, 255, 0), (0, 0, 0)))
    assert thin.max() > thick.max() + 20, (thick, thin)


def test_unsupported_extensions(scene,triangle_glb):
    doc,binary=unpack(triangle_glb)
    doc["extensionsUsed"].append("VENDOR_not_supported")
    with pytest.warns(filly.AssetCompatibilityWarning,match="VENDOR_not_supported"):
        scene.load(pack(doc,binary))
    with pytest.raises(filly.AssetError,match="VENDOR_not_supported"):
        scene.load(pack(doc,binary),strict=True)
    doc["extensionsRequired"].append("VENDOR_not_supported")
    with pytest.raises(filly.AssetError,match="VENDOR_not_supported"):
        scene.load(pack(doc,binary))


def test_metadata_extensions_are_silent(scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["extensionsUsed"] += ["KHR_xmp", "KHR_xmp_json_ld"]
    doc["extensionsRequired"].append("KHR_xmp_json_ld")
    doc["extensions"] = {"KHR_xmp_json_ld": {"packets": [{"@context": {"dc": "http://purl.org/dc/elements/1.1/"}, "dc:title": "triangle"}]}}
    doc["asset"]["extensions"] = {"KHR_xmp_json_ld": {"packet": 0}}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert scene.load(pack(doc, binary), strict=True).node_names


def test_draft_extensions_are_unsupported(renderer, scene, triangle_glb):
    def backlit(volume, scatter=False, retro=False):
        doc, binary = unpack(triangle_glb); lit(doc)
        material = doc["materials"][0]
        material.update(doubleSided=True, extensions={"KHR_materials_diffuse_transmission": {
            "diffuseTransmissionFactor": 1, "diffuseTransmissionColorFactor": [1, 0, 0]}})
        doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission"]
        if volume:
            doc["extensionsUsed"].append("KHR_materials_volume")
            material["extensions"]["KHR_materials_volume"] = {
                "thicknessFactor": 0.1, "attenuationColor": [0.5, 0.5, 0.5], "attenuationDistance": 0.1}
        if scatter:
            doc["extensionsUsed"].append("KHR_materials_volume_scatter")
            material["extensions"]["KHR_materials_volume_scatter"] = {"multiscatterColor": [0.9, 0.9, 0.9]}
        if retro:
            doc["extensionsUsed"].append("KHR_materials_retroreflection")
            material["extensions"]["KHR_materials_retroreflection"] = {"retroreflectionFactor": 1}
        return pack(doc, binary)

    scene.add_directional_light(direction=(0, 0, 1), intensity=100000)
    target = renderer.create_render_target(width=32, height=32)

    def pixel(asset):
        model = scene.load(asset)
        renderer.render(scene, target)
        model.close()
        return target.read()[16, 16]

    clear, absorbed = pixel(backlit(False)), pixel(backlit(True))
    # Volume absorption still attenuates diffuse transmission.
    assert clear[0] > absorbed[0] + 10 and absorbed[0] > 5, (clear, absorbed)
    for name, options in (("KHR_materials_volume_scatter", {"scatter": True}),
                          ("KHR_materials_retroreflection", {"retro": True})):
        asset = backlit(True, **options)
        with pytest.raises(filly.AssetError, match=name):
            scene.load(asset, strict=True)
        with pytest.warns(filly.AssetCompatibilityWarning, match=name):
            np.testing.assert_array_equal(pixel(asset), absorbed)


@pytest.mark.parametrize("encoding", ["webp", "ktx2"])
def test_texture_pixels(renderer,scene,triangle_glb,encoding):
    if encoding == "webp":
        # Decoded by libwebp in the extension.
        payload = (Path(__file__).parent / "data" / "green.webp").read_bytes()
        extension, mime = "EXT_texture_webp", "image/webp"
    else:
        payload = (Path(__file__).parent / "data" / "green.ktx2").read_bytes()
        extension, mime = "KHR_texture_basisu", "image/ktx2"
    doc,binary=unpack(triangle_glb)
    doc["extensionsUsed"].append(extension)
    doc["extensionsRequired"].append(extension)
    doc["images"]=[{"mimeType":mime,"uri":f"data:{mime};base64,"+base64.b64encode(payload).decode()}]
    if encoding == "webp": doc["images"][0].pop("mimeType")
    doc["textures"]=[{"extensions":{extension:{"source":0}}}]
    doc["materials"][0]["pbrMetallicRoughness"].update(baseColorFactor=[1,1,1,1],baseColorTexture={"index":0})
    # A constant texture still needs a valid UV accessor in glTF.
    offset=len(binary);binary+=struct.pack("<6f",0,0,1,0,0.5,1)
    doc["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":24})
    doc["accessors"].append({"bufferView":1,"componentType":5126,"count":3,"type":"VEC2"})
    doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]=1
    scene.load(pack(doc,binary))
    target=renderer.create_render_target(width=32,height=32);renderer.render(scene,target)
    # KTX2 transcodes to a lossy GPU block format; WebP is encoded losslessly here.
    np.testing.assert_allclose(target.read()[16,16],[0,255,0,255],atol=8 if encoding == "ktx2" else 0)


@pytest.mark.parametrize("msaa,aa,tone", [(1,"none","linear"), (1,"fxaa","aces_legacy"), (4,"fxaa","aces")])
def test_postprocessing_pixels_and_history(renderer, scene, triangle_glb, msaa, aa, tone):
    scene.refraction = True
    scene.msaa = msaa
    scene.antialiasing = aa
    scene.tone_mapping = tone
    model = scene.load(triangle_glb)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    first = target.read()
    assert first[32,32,0] > 180 and first[32,32,1] < 50
    model.material("red").base_color = (0,1,0,1)
    renderer.render(scene, target)
    assert target.read()[32,32,1] > 180
    model.material("red").base_color = (1,0,0,1)
    renderer.render(scene, target)
    np.testing.assert_array_equal(first, target.read())


def test_glass_reveals_geometry(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["extensionsUsed"].append("KHR_materials_transmission")
    doc["materials"].append({"name":"glass", "pbrMetallicRoughness":{
        "baseColorFactor":[1,1,1,1], "metallicFactor":0, "roughnessFactor":0},
        "extensions":{"KHR_materials_transmission":{"transmissionFactor":1}}})
    doc["meshes"].append({"primitives":[{"attributes":{"POSITION":0}, "material":1}]})
    doc["nodes"].append({"mesh":1,"translation":[0,0,0.2]})
    doc["scenes"][0]["nodes"].append(1)
    scene.refraction = True
    scene.tone_mapping = "linear"
    scene.load(pack(doc,binary))
    scene.set_environment(np.ones((8,16,3), dtype="f"), intensity=30000)
    target = renderer.create_render_target(width=64,height=64)
    renderer.render(scene,target)
    pixel = target.read()[32,32]
    assert pixel[0] > 180 and pixel[0] > pixel[1]+80, pixel


@pytest.mark.parametrize("pointer", [False, True])
def test_morph_weights_and_authored_reset(renderer, scene, triangle_glb, pointer):
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    binary += struct.pack("<9f", *([0.7,0,0]*3))
    doc["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":36})
    doc["accessors"].append({"bufferView":1,"componentType":5126,"count":3,"type":"VEC3",
                             "min":[0.7,0,0],"max":[0.7,0,0]})
    doc["meshes"][0].update(weights=[0.25])
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION":1}]
    if pointer:
        from test_animation_pointer import add_clip
        add_clip(doc, binary, "/nodes/0/weights", [0.25, 1])
    model = scene.load(pack(doc,binary))
    node = model.node("triangle")
    assert node.morph_target_count == 1
    target = renderer.create_render_target(width=64,height=64)
    renderer.render(scene,target); rest = target.read()
    if pointer:
        model.apply_animation(0, 2, loop=False)
    else:
        node.set_morph_weights([1])
    renderer.render(scene,target)
    assert np.count_nonzero(rest != target.read()) > 100
    model.reset_animation()
    renderer.render(scene,target)
    np.testing.assert_array_equal(rest,target.read())
    with pytest.raises(ValueError): node.set_morph_weights([])


def test_skin_bone_update(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    offset = len(binary)
    binary += bytes(12) + struct.pack("<12f", *([1,0,0,0]*3))
    doc["bufferViews"] += [{"buffer":0,"byteOffset":offset,"byteLength":12},
                            {"buffer":0,"byteOffset":offset+12,"byteLength":48}]
    doc["accessors"] += [{"bufferView":1,"componentType":5121,"count":3,"type":"VEC4"},
                          {"bufferView":2,"componentType":5126,"count":3,"type":"VEC4"}]
    doc["meshes"][0]["primitives"][0]["attributes"].update(JOINTS_0=1,WEIGHTS_0=2)
    doc["nodes"][0]["skin"] = 0
    doc["nodes"].append({"name":"joint"})
    doc["scenes"][0]["nodes"].append(1)
    doc["skins"] = [{"joints":[1]}]
    model = scene.load(pack(doc,binary))
    target = renderer.create_render_target(width=64,height=64)
    renderer.render(scene,target); rest = target.read()
    # Bone matrices follow node edits at the next render without an explicit call.
    model.node("joint").position = (0.7,0,0)
    renderer.render(scene,target)
    assert np.count_nonzero(rest != target.read()) > 100
    model.reset_animation()
    renderer.render(scene,target)
    np.testing.assert_array_equal(rest,target.read())


def test_diffuse_transmission_environment(renderer, scene, triangle_glb):
    doc,binary = unpack(triangle_glb); lit(doc)
    doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission"]
    doc["materials"][0]["extensions"] = {"KHR_materials_diffuse_transmission":{
        "diffuseTransmissionFactor":1,"diffuseTransmissionColorFactor":[1,0,0]}}
    model = scene.load(pack(doc,binary))
    target = renderer.create_render_target(width=32,height=32)
    scene.set_environment(np.ones((8,16,3),dtype="f"),intensity=50000)
    model.node("triangle").material()
    renderer.render(scene,target); pixel = target.read()[16,16]
    assert pixel[0] > pixel[1]+20 and pixel[1] == pixel[2], pixel
    scene.clear_environment()
    renderer.render(scene,target)
    assert target.read()[16,16,:3].max() == 0


def test_hdr_environment_file(renderer, scene, triangle_glb, tmp_path):
    doc,binary = unpack(triangle_glb); lit(doc)
    scene.load(pack(doc,binary))
    path = tmp_path / "constant.hdr"
    path.write_bytes(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 2 +X 4\n" + bytes([128,128,128,129])*8)
    scene.load_environment(path,intensity=50000)
    target = renderer.create_render_target(width=32,height=32)
    renderer.render(scene,target)
    assert target.read()[16,16,0] > 20


def test_imported_animated_light(renderer, scene, triangle_glb):
    doc,binary = unpack(triangle_glb); lit(doc)
    doc["extensionsUsed"] = ["KHR_lights_punctual"]
    doc["extensions"] = {"KHR_lights_punctual":{"lights":[{"type":"point","intensity":1000,"range":10}]}}
    doc["nodes"].append({"name":"lamp","translation":[0,0,1],"extensions":{"KHR_lights_punctual":{"light":0}}})
    doc["scenes"][0]["nodes"].append(1)
    offset = len(binary); binary += struct.pack("<8f",0,1,0,0,1,3,0,1)
    doc["bufferViews"] += [{"buffer":0,"byteOffset":offset,"byteLength":8},{"buffer":0,"byteOffset":offset+8,"byteLength":24}]
    doc["accessors"] += [{"bufferView":1,"componentType":5126,"count":2,"type":"SCALAR","min":[0],"max":[1]},
                          {"bufferView":2,"componentType":5126,"count":2,"type":"VEC3"}]
    doc["animations"] = [{"samplers":[{"input":1,"output":2}],"channels":[{"sampler":0,"target":{"node":1,"path":"translation"}}]}]
    model = scene.load(pack(doc,binary))
    light = model.light("lamp")
    assert model.lights == [light] and light.node == model.node(1)
    assert light.type == "point" and light.intensity == pytest.approx(1000)
    scene.camera.exposure = 6
    target = renderer.create_render_target(width=32,height=32)
    model.apply_animation(0,0);renderer.render(scene,target);near = target.read()
    model.apply_animation(0,1,loop=False);renderer.render(scene,target);far = target.read()
    assert near[16,16,0] > far[16,16,0]+20
    model.apply_animation(0,0);renderer.render(scene,target)
    np.testing.assert_array_equal(near,target.read())
    light.intensity = 0
    renderer.render(scene,target)
    assert target.read()[16,16,0] == 0


def test_directional_shadow_pixels(renderer, scene, triangle_glb):
    doc,binary = unpack(triangle_glb); lit(doc)
    doc["nodes"][0]["scale"] = [2,2,2]
    doc["nodes"].append({"mesh":0,"scale":[0.4,0.4,0.4],"translation":[0,0,0.5]})
    doc["scenes"][0]["nodes"].append(1)
    scene.load(pack(doc,binary))
    light = scene.add_directional_light(direction=(0.4,0,-1),intensity=100000)
    scene.shadows = True
    target = renderer.create_render_target(width=128,height=128)
    renderer.render(scene,target); unshadowed = target.read()
    light.casts_shadows = True
    renderer.render(scene,target); shadowed = target.read()
    assert (unshadowed[:,:,0].astype(int)-shadowed[:,:,0].astype(int) > 20).sum() > 30


@pytest.mark.parametrize("transmitted", [False, True])
def test_diffuse_material_texture_transforms(renderer, scene, triangle_glb, transmitted):
    Image = pytest.importorskip("PIL.Image")
    pixels = np.zeros((4,8,4),dtype="uint8"); pixels[:,:,3] = 255
    pixels[:,:4,0] = 255; pixels[:,4:,1] = 255
    output = io.BytesIO(); Image.fromarray(pixels).save(output,format="PNG")
    doc,binary = unpack(triangle_glb); lit(doc)
    doc["extensionsUsed"] = ["KHR_materials_diffuse_transmission", "KHR_texture_transform"]
    doc["images"] = [{"uri":"data:image/png;base64,"+base64.b64encode(output.getvalue()).decode()}]
    doc["textures"] = [{"source":0,"sampler":0}]
    doc["samplers"] = [{"minFilter":9728,"magFilter":9728,"wrapS":33071,"wrapT":33071}]
    info = {"index":0,"extensions":{"KHR_texture_transform":{"offset":[0.5,0]}}}
    ext = {"diffuseTransmissionFactor":int(transmitted)}
    if transmitted: ext["diffuseTransmissionColorTexture"] = info
    else: doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = info
    doc["materials"][0]["extensions"] = {"KHR_materials_diffuse_transmission":ext}
    offset = len(binary); binary += struct.pack("<6f", *([0.1,0.5]*3))
    doc["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":24})
    doc["accessors"].append({"bufferView":1,"componentType":5126,"count":3,"type":"VEC2"})
    doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = 1
    model = scene.load(pack(doc,binary))
    scene.add_directional_light(direction=(0,0,1 if transmitted else -1),intensity=100000)
    target = renderer.create_render_target(width=32,height=32)
    renderer.render(scene,target); pixel = target.read()[16,16]
    assert pixel[1] > pixel[0]+20 and pixel[1] > pixel[2]+20, pixel
    model.node("triangle").material()
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read()[16, 16], pixel)
    model.close()
    renderer.finish()


def test_material_mode_compatibility(triangle_glb):
    doc,binary = unpack(triangle_glb); lit(doc)
    doc["extensionsUsed"] = ["KHR_materials_clearcoat","KHR_materials_transmission"]
    doc["materials"][0]["extensions"] = {"KHR_materials_clearcoat":{"clearcoatFactor":1},
                                           "KHR_materials_transmission":{"transmissionFactor":1}}
    # Filament's archive drops transmission for clearcoat; both modes compile this material instead.
    for mode in ("precompiled","compiled"):
        with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
            scene = renderer.create_scene()
            scene.refraction = True
            scene.tone_mapping = "aces_legacy"
            assert scene.load(pack(doc,binary),strict=True).material_names == ["red"]


@pytest.mark.parametrize("radiance", [(1, 1, 1), (0.37, 0.52, 0.9)])
@pytest.mark.parametrize("roughness", [0.05, 0.8])
def test_uniform_environment_matches_prefiltered(renderer, scene, triangle_glb, radiance, roughness):
    # A uniform panorama takes a fast path without prefiltering. One slightly different pixel
    # forces the prefiltered path, which must give almost the same light.
    doc, binary = unpack(triangle_glb); lit(doc)
    doc["materials"][0]["pbrMetallicRoughness"].update(metallicFactor=1, roughnessFactor=roughness)
    scene.load(pack(doc, binary))
    scene.environment_visible = True
    target = renderer.create_render_target(width=32, height=32)
    panorama = np.tile(np.asarray(radiance, np.float32), (8, 16, 1))
    images = []
    for nudge in (0, 1e-4):
        panorama[0, 0, 0] += nudge
        scene.set_environment(panorama, intensity=20000, rotation_deg=30)
        renderer.render(scene, target)
        images.append(target.read().astype(int))
    uniform, prefiltered = images
    assert uniform[16, 16, 0] > 20
    assert np.abs(uniform - prefiltered).max() <= 2
