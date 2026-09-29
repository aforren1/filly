import base64
import io
import json
import math

import numpy as np
import pytest

import filly
from test_features import pack, unpack, lit
from test_animation_pointer import add_clip, accessor

pytestmark = pytest.mark.gpu


@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
def test_clearcoat_normal_scale_pointer(triangle_glb, mode):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    textured(doc, binary)
    Image = pytest.importorskip("PIL.Image")
    out = io.BytesIO()
    Image.new("RGB", (2, 2), (200, 128, 230)).save(out, format="PNG")
    doc["images"][0]["uri"] = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    coat = {"clearcoatFactor": 1, "clearcoatRoughnessFactor": 0.2,
            "clearcoatNormalTexture": {"index": 0, "scale": 0.5}}
    doc["materials"][0]["extensions"] = {"KHR_materials_clearcoat": coat}
    doc["extensionsUsed"] = ["KHR_materials_clearcoat", "KHR_texture_transform"]
    add_clip(doc, binary, "/materials/0/extensions/KHR_materials_clearcoat/clearcoatNormalTexture/scale", [0.5, 0])
    with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
        scene = renderer.create_scene()
        scene.camera = scene.create_camera()
        scene.camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        scene.camera.position = (0, 0, 3)
        scene.camera.look_at((0, 0, 0))
        scene.add_directional_light(direction=(0, 0, -1), intensity=100000)
        target = renderer.create_render_target(width=32, height=32)
        model = scene.load(pack(doc, binary), strict=True)
        renderer.render(scene, target)
        original = target.read()
        model.apply_animation(0, 2, loop=False)
        renderer.render(scene, target)
        animated = target.read()
        assert np.count_nonzero(animated != original) > 10
        model.reset_animation()
        renderer.render(scene, target)
        np.testing.assert_array_equal(target.read(), original)
        model.close()
        coat["clearcoatNormalTexture"]["scale"] = 0
        del doc["animations"]
        scene.load(pack(doc, binary), strict=True)
        renderer.render(scene, target)
        np.testing.assert_array_equal(target.read(), animated)


def camera_asset(triangle_glb, orthographic):
    doc, binary = unpack(triangle_glb)
    camera = {"type": "orthographic", "orthographic": {"xmag": 1, "ymag": 1, "znear": 0, "zfar": 10}} if orthographic else {
        "type": "perspective", "perspective": {"yfov": 0.7, "aspectRatio": 1, "znear": 0.1, "zfar": 10}}
    doc["cameras"] = [camera]
    doc["nodes"].append({"name": "view", "camera": 0, "translation": [0, 0, 3]})
    doc["scenes"][0]["nodes"].append(1)
    return doc, binary


@pytest.mark.parametrize("orthographic", [False, True])
def test_imported_camera_animation_and_close(renderer, scene, triangle_glb, orthographic):
    doc, binary = camera_asset(triangle_glb, orthographic)
    path = "orthographic/xmag" if orthographic else "perspective/yfov"
    add_clip(doc, binary, "/cameras/0/"+path, [1, 2] if orthographic else [0.7, 1.2])
    model = scene.load(pack(doc, binary), strict=True)
    camera = model.camera("view")
    assert model.cameras == [camera] and camera.node.name == "view"
    scene.camera = camera
    np.testing.assert_allclose(camera.position, [0, 0, 3])
    original = camera.projection.copy()
    if orthographic:
        assert original[0, 0] == pytest.approx(1)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    before = (target.read()[:, :, 0] > 100).sum()
    model.apply_animation(0, 2, loop=False)
    renderer.render(scene, target)
    assert (target.read()[:, :, 0] > 100).sum() < before * 0.7
    model.reset_animation()
    np.testing.assert_allclose(camera.projection, original)
    other_scene = renderer.create_scene()
    other_scene.camera = model.camera("view")
    model.close()
    with pytest.raises(filly.FillyError, match="closed"):
        _ = camera.projection
    with pytest.raises(filly.FillyError, match="camera"):
        renderer.render(other_scene, target)


@pytest.mark.parametrize("orthographic", [False, True])
def test_camera_grouped_clip_planes(renderer, scene, triangle_glb, orthographic):
    doc, binary = camera_asset(triangle_glb, orthographic)
    projection = "orthographic" if orthographic else "perspective"
    clip = add_clip(doc, binary, f"/cameras/0/{projection}/znear", [1, 3])
    far = accessor(doc, binary, [2, 4])
    clip["samplers"].append({"input": clip["samplers"][0]["input"], "output": far})
    clip["channels"].append({"sampler": 1, "target": {"path": "pointer", "extensions": {"KHR_animation_pointer": {"pointer": f"/cameras/0/{projection}/zfar"}}}})
    model = scene.load(pack(doc, binary))
    model.apply_animation(0, 2, loop=False)
    assert np.isfinite(model.camera("view").projection).all()


def textured(doc, binary):
    Image = pytest.importorskip("PIL.Image")
    pixels = np.zeros((8, 8, 3), dtype="uint8")
    pixels[:, :4, 0] = 255
    pixels[:, 4:, 1] = 255
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    doc["images"] = [{"uri": "data:image/png;base64,"+base64.b64encode(out.getvalue()).decode()}]
    doc["textures"] = [{"source": 0, "sampler": 0}]
    doc["samplers"] = [{"minFilter": 9728, "magFilter": 9728, "wrapS": 33071, "wrapT": 33071}]
    uv = accessor(doc, binary, [0.1, 0.5]*3, "VEC2")
    doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = uv
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [1, 1, 1, 1]
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0, "extensions": {"KHR_texture_transform": {}}}
    doc.setdefault("extensionsUsed", []).append("KHR_texture_transform")


@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
def test_uv_animation_matches_static_transform(triangle_glb, mode):
    doc, binary = unpack(triangle_glb)
    textured(doc, binary)
    path = "/materials/0/pbrMetallicRoughness/baseColorTexture/extensions/KHR_texture_transform/"
    clip = add_clip(doc, binary, path+"offset", [0, 0, 0.75, 0.25], kind="VEC2")
    for suffix, values, kind in (("rotation", [0, 0.5], "SCALAR"), ("scale", [1, 1, -0.5, 0.7], "VEC2")):
        output = accessor(doc, binary, values, kind)
        clip["samplers"].append({"input": clip["samplers"][0]["input"], "output": output})
        clip["channels"].append({"sampler": len(clip["samplers"])-1, "target": {"path": "pointer", "extensions": {"KHR_animation_pointer": {"pointer": path+suffix}}}})
    with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        model = scene.load(pack(doc, binary))
        target = renderer.create_render_target(width=32, height=32)
        renderer.render(scene, target)
        original = target.read()
        np.testing.assert_array_equal(original[16, 16], [255, 0, 0, 255])
        model.apply_animation(0, 2, loop=False)
        renderer.render(scene, target)
        animated = target.read()
        np.testing.assert_array_equal(animated[16, 16], [0, 255, 0, 255])
        model.visible = False
        doc.pop("animations")
        doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["extensions"]["KHR_texture_transform"] = {
            "offset": [0.75, 0.25], "rotation": 0.5, "scale": [-0.5, 0.7]}
        static = scene.load(pack(doc, binary))
        renderer.render(scene, target)
        np.testing.assert_array_equal(target.read(), animated)
        static.close()
        model.visible = True
        model.reset_animation()
        renderer.render(scene, target)
        np.testing.assert_array_equal(target.read(), original)


@pytest.mark.parametrize("extension,field,start,end", [
    ("anisotropy", "anisotropyStrength", 0, 0.9),
    ("iridescence", "iridescenceFactor", 0, 1),
])
def test_surface_pixels_and_reset(renderer, scene, triangle_glb, extension, field, start, end):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    pbr = doc["materials"][0]["pbrMetallicRoughness"]
    pbr.update(baseColorFactor=[0.8, 0.8, 0.8, 1], metallicFactor=1, roughnessFactor=0.3)
    name = "KHR_materials_"+extension
    doc["extensionsUsed"] = [name]
    doc["extensionsRequired"] = [name]
    doc["materials"][0]["extensions"] = {name: {field: start}}
    add_clip(doc, binary, "/materials/0/extensions/"+name+"/"+field, [start, end])
    model = scene.load(pack(doc, binary), strict=True)
    scene.add_directional_light(direction=(-0.4, -0.1, -1), intensity=100000)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    before = target.read()
    model.apply_animation(0, 2, loop=False)
    renderer.render(scene, target)
    after = target.read()
    assert np.abs(before.astype(int)-after.astype(int)).max() > 10
    model.reset_animation()
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read(), before)
    model.close()


@pytest.mark.parametrize("extension,texture_name,pixels,factor", [
    ("anisotropy", "anisotropyTexture", [(255, 128, 0), (255, 128, 255)], "anisotropyStrength"),
    ("iridescence", "iridescenceTexture", [(0, 255, 0), (255, 255, 0)], "iridescenceFactor"),
    ("iridescence", "iridescenceThicknessTexture", [(255, 0, 255), (255, 255, 255)], "iridescenceFactor"),
])
def test_surface_texture_channels_and_uv_animation(renderer, scene, triangle_glb, extension, texture_name, pixels, factor):
    Image = pytest.importorskip("PIL.Image")
    doc, binary = unpack(triangle_glb)
    lit(doc)
    textured(doc, binary)
    pbr = doc["materials"][0]["pbrMetallicRoughness"]
    pbr.pop("baseColorTexture")
    pbr.update(baseColorFactor=[0.8, 0.8, 0.8, 1], metallicFactor=1, roughnessFactor=0.3)
    image = np.array([[pixels[0]]*4+[pixels[1]]*4]*4, dtype="uint8")
    out = io.BytesIO(); Image.fromarray(image).save(out, format="PNG")
    doc["images"][0]["uri"] = "data:image/png;base64,"+base64.b64encode(out.getvalue()).decode()
    name = "KHR_materials_"+extension
    ext = {factor: 1, texture_name: {"index": 0, "extensions": {"KHR_texture_transform": {}}}}
    if texture_name == "iridescenceThicknessTexture":
        ext.update(iridescenceThicknessMinimum=100, iridescenceThicknessMaximum=450)
    doc["materials"][0]["extensions"] = {name: ext}
    doc["extensionsUsed"].append(name)
    add_clip(doc, binary, f"/materials/0/extensions/{name}/{texture_name}/extensions/KHR_texture_transform/offset", [0, 0, 0.5, 0], kind="VEC2")
    model = scene.load(pack(doc, binary), strict=True)
    scene.add_directional_light(direction=(-0.4, -0.1, -1), intensity=100000)
    target = renderer.create_render_target(width=32, height=32)
    renderer.render(scene, target); first = target.read()
    model.apply_animation(0, 2, loop=False)
    renderer.render(scene, target); second = target.read()
    assert np.abs(first.astype(int)-second.astype(int)).max() > 10
    model.reset_animation()
    renderer.render(scene, target)
    np.testing.assert_array_equal(first, target.read())
    model.close()
    renderer.finish()


def test_surface_environment_combination_and_rotation(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["materials"][0]["pbrMetallicRoughness"].update(baseColorFactor=[0.8, 0.8, 0.8, 1], metallicFactor=1, roughnessFactor=0.3)
    ext = {"KHR_materials_anisotropy": {"anisotropyStrength": 0.9},
           "KHR_materials_iridescence": {"iridescenceFactor": 1},
           "KHR_materials_clearcoat": {"clearcoatFactor": 0.2}}
    doc["materials"][0]["extensions"] = ext
    doc["extensionsUsed"] = list(ext)
    add_clip(doc, binary, "/materials/0/extensions/KHR_materials_anisotropy/anisotropyRotation", [0, math.pi/2])
    model = scene.load(pack(doc, binary), strict=True)
    panorama = np.full((16, 32, 3), 0.01, dtype="float32")
    panorama[4:8, 3:12] = 5
    scene.set_environment(panorama, intensity=1000000)
    # Filament's anisotropic IBL approximation bends reflections at oblique angles.
    model.rotation_euler_deg = (20, 30, 0)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target); before = target.read()
    model.apply_animation(0, 2, loop=False)
    renderer.render(scene, target); after = target.read()
    assert before[:, :, :3].max() > 10
    assert np.abs(before.astype(int)-after.astype(int)).max() > 3


def test_camera_infinite_far_and_parent_transform(renderer, scene, triangle_glb):
    doc, binary = camera_asset(triangle_glb, False)
    del doc["cameras"][0]["perspective"]["zfar"]
    model = scene.load(pack(doc, binary))
    camera = model.camera("view")
    model.position = (0.4, 0, 0)
    np.testing.assert_allclose(camera.position, [0.4, 0, 3], atol=1e-6)
    assert np.isfinite(camera.projection).all()
    camera.position = (1, 2, 4)
    np.testing.assert_allclose(camera.position, [1, 2, 4], atol=1e-6)
    camera.look_at((0, 0, 0))
    np.testing.assert_allclose(camera.position, [1, 2, 4], atol=1e-6)
    pose = camera.transform.copy()
    camera.transform = pose
    np.testing.assert_allclose(camera.transform, pose, atol=1e-6)
    np.testing.assert_allclose(camera.view_matrix @ camera.transform, np.eye(4), atol=1e-6)


def test_surface_fast_mode_compiles_extension_combinations(triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    ext = {"KHR_materials_anisotropy": {"anisotropyStrength": 0.5},
           "KHR_materials_iridescence": {"iridescenceFactor": 0.8},
           "KHR_materials_clearcoat": {"clearcoatFactor": 0.3},
           "KHR_materials_sheen": {"sheenColorFactor": [0.1, 0.2, 0.3]}}
    doc["materials"][0]["extensions"] = ext
    doc["extensionsUsed"] = list(ext)
    with filly.Renderer(precompiled_shaders=True) as renderer:
        scene = renderer.create_scene()
        model = scene.load(pack(doc, binary), strict=True)
        assert model.material_names == ["red"]
        model.close()


@pytest.mark.parametrize("invalid", ["camera_far", "camera_near", "anisotropy", "iridescence", "uv"])
def test_invalid_surface_camera_preflight(scene, triangle_glb, invalid):
    doc, binary = camera_asset(triangle_glb, False)
    lit(doc)
    if invalid == "camera_far":
        doc["cameras"][0]["perspective"]["zfar"] = 1e100
    elif invalid == "camera_near":
        doc["cameras"][0]["perspective"]["znear"] = 20
    elif invalid == "uv":
        textured(doc, binary)
        doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["extensions"]["KHR_texture_transform"]["scale"] = [1e100, 1]
        add_clip(doc, binary, "/materials/0/pbrMetallicRoughness/baseColorTexture/extensions/KHR_texture_transform/rotation", [0, 1])
    else:
        name = "KHR_materials_"+invalid
        doc["extensionsUsed"] = [name]
        doc["materials"][0]["extensions"] = {name: {"anisotropyStrength": 2} if invalid == "anisotropy" else {"iridescenceIor": 0}}
    with pytest.raises(filly.AssetError, match="Invalid"):
        scene.load(pack(doc, binary))
