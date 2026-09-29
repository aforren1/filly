import numpy as np
import pytest

import filly
from test_features import lit, pack, unpack

pytestmark = pytest.mark.gpu


def shadow_asset(triangle_glb, imported=False):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    doc["nodes"] = [{"mesh":0,"name":"receiver","scale":[2,2,2]},
                    {"mesh":0,"name":"occluder","scale":[0.4,0.4,0.4],"translation":[0,0,0.5]}]
    doc["scenes"][0]["nodes"] = [0,1]
    if imported:
        doc["extensionsUsed"] = ["KHR_lights_punctual"]
        doc["extensions"] = {"KHR_lights_punctual":{"lights":[{"type":"point","intensity":100000,"range":10}]}}
        doc["nodes"].append({"name":"lamp","translation":[-0.7,0,2],"extensions":{"KHR_lights_punctual":{"light":0}}})
        doc["scenes"][0]["nodes"].append(2)
    return pack(doc,binary)


def read(renderer, scene, target):
    renderer.render(scene,target)
    return target.read().astype(int)


@pytest.mark.parametrize("axis,angle", [(0,0),(0,180),(0,90),(0,-90),(1,90),(1,-90)])
def test_point_shadow_cube_faces(renderer,scene,triangle_glb,axis,angle):
    model=scene.load(shadow_asset(triangle_glb,imported=True))
    angles=[0,0,0];angles[axis]=angle
    model.rotation_euler_deg=angles
    rotation=model.transform[:3,:3]
    scene.camera.position=rotation @ np.array([0,0,3])
    scene.camera.look_at((0,0,0),up=rotation @ np.array([0,1,0]))
    scene.shadows = True
    light=model.light("lamp")
    target=renderer.create_render_target(width=128,height=128)
    light.casts_shadows=False
    baseline=read(renderer,scene,target)
    light.set_shadow_options(map_size=512)
    light.casts_shadows=True
    shadowed=read(renderer,scene,target)
    assert ((baseline[:,:,0]-shadowed[:,:,0])>20).sum()>30
    light.casts_shadows=False
    np.testing.assert_array_equal(read(renderer,scene,target),baseline)


@pytest.mark.parametrize("kind", ["point","spot","directional"])
def test_shadow_controls_and_validation(renderer,scene,triangle_glb,kind):
    scene.load(shadow_asset(triangle_glb))
    if kind=="point": light=scene.add_point_light(position=(-0.7,0,2),intensity=100000)
    elif kind=="spot": light=scene.add_spot_light(position=(-0.7,0,2),direction=(0.7,0,-2),intensity=100000,inner=0.4,outer=0.8)
    else: light=scene.add_directional_light(direction=(0.35,0,-1),intensity=100000)
    scene.shadows = True
    target=renderer.create_render_target(width=128,height=128)
    baseline=read(renderer,scene,target)
    light.casts_shadows=True
    for size in (256,1024):
        light.set_shadow_options(map_size=size,constant_bias=0.001,normal_bias=1)
        assert ((baseline[:,:,0]-read(renderer,scene,target)[:,:,0])>20).sum()>30
    for invalid in ({"map_size":0},{"map_size":17},{"map_size":8192},
                    {"constant_bias":-1},{"normal_bias":float("nan")}):
        with pytest.raises(ValueError): light.set_shadow_options(**invalid)
    scene.shadows = False
    np.testing.assert_array_equal(read(renderer,scene,target),baseline)
    light.close()
    with pytest.raises(filly.FillyError): light.set_shadow_options()


def panorama():
    image=np.zeros((16,32,3),dtype="f")
    image[:,:16,0]=1
    image[:,16:,1]=1
    return image


@pytest.mark.parametrize("tone_mapping",["linear","aces_legacy"])
def test_environment_visibility_rotation_and_cleanup(renderer,scene,tone_mapping):
    scene.tone_mapping=tone_mapping
    scene.transparent=True
    scene.background=(0,0,1,0)
    target=renderer.create_render_target(width=64,height=64)
    background=read(renderer,scene,target)
    assert not scene.environment_visible
    scene.set_environment(panorama(),intensity=50000)
    np.testing.assert_array_equal(read(renderer,scene,target),background)
    scene.environment_visible=True
    first=read(renderer,scene,target)
    assert first[:,:,:3].max()>20 and first[:,:,3].min()==255
    scene.environment_rotation=180
    assert scene.environment_rotation==180
    opposite=read(renderer,scene,target)
    assert np.abs(first-opposite).mean()>10
    scene.environment_rotation=0
    np.testing.assert_array_equal(read(renderer,scene,target),first)
    scene.environment_intensity=0
    assert read(renderer,scene,target)[:,:,:3].max()==0
    scene.environment_intensity=50000
    np.testing.assert_array_equal(read(renderer,scene,target),first)
    scene.environment_visible=False
    np.testing.assert_array_equal(read(renderer,scene,target),background)
    scene.environment_visible=True
    scene.clear_environment()
    np.testing.assert_array_equal(read(renderer,scene,target),background)
    # Visibility is a retained preference, including when replacing the panorama.
    scene.set_environment(panorama(),intensity=50000,rotation_deg=180)
    np.testing.assert_array_equal(read(renderer,scene,target),opposite)
    for value in (float("nan"),float("inf")):
        with pytest.raises(ValueError): scene.environment_rotation=value
    scene.clear_environment()
    with pytest.raises(filly.FillyError): scene.environment_rotation=0


def spot_asset(triangle_glb):
    doc,binary=unpack(triangle_glb);lit(doc)
    doc["nodes"][0]["scale"]=[3,3,3]
    doc["extensionsUsed"]=["KHR_lights_punctual"]
    doc["extensions"]={"KHR_lights_punctual":{"lights":[{"type":"spot","intensity":20,"range":10,
                                                          "spot":{"innerConeAngle":0.2,"outerConeAngle":0.4}}]}}
    # A light points along its node's -Z axis; this one sits in front of the plane.
    doc["nodes"].append({"name":"lamp","translation":[0,0,1],"extensions":{"KHR_lights_punctual":{"light":0}}})
    doc["scenes"][0]["nodes"].append(1)
    return pack(doc,binary)


@pytest.mark.parametrize("source",["scene","imported"])
def test_spot_cone_keeps_candela(renderer,scene,triangle_glb,source):
    if source=="imported":
        doc,binary=unpack(spot_asset(triangle_glb))
        light=scene.load(pack(doc,binary)).light("lamp")
    else:
        doc,binary=unpack(triangle_glb);lit(doc);doc["nodes"][0]["scale"]=[3,3,3]
        scene.load(pack(doc,binary))
        light=scene.add_spot_light(position=(0,0,1),direction=(0,0,-1),intensity=20,inner=0.2,outer=0.4)
    scene.camera.exposure=4
    target=renderer.create_render_target(width=32,height=32)
    narrow=read(renderer,scene,target)[16,16]
    assert light.type=="spot" and narrow[0]>40
    # glTF defines spot intensity in candela, so the on-axis brightness must not change.
    light.set_spot_cone(0.3,1.2)
    assert light.intensity==pytest.approx(20)
    np.testing.assert_allclose(read(renderer,scene,target)[16,16],narrow,atol=1)
