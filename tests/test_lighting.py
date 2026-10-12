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
    light.shadow_map_size=512
    light.casts_shadows=True
    shadowed=read(renderer,scene,target)
    assert ((baseline[:,:,0]-shadowed[:,:,0])>20).sum()>30
    light.casts_shadows=False
    np.testing.assert_array_equal(read(renderer,scene,target),baseline)


@pytest.mark.parametrize("kind", ["point","spot","directional"])
def test_shadow_controls_and_validation(renderer,scene,triangle_glb,kind):
    scene.load(shadow_asset(triangle_glb))
    if kind=="point": light=scene.add_point_light(position=(-0.7,0,2),intensity=100000)
    elif kind=="spot": light=scene.add_spot_light(position=(-0.7,0,2),direction=(0.7,0,-2),intensity=100000,cone_deg=(23,46))
    else: light=scene.add_directional_light(direction=(0.35,0,-1),intensity=100000)
    scene.shadows = True
    target=renderer.create_render_target(width=128,height=128)
    baseline=read(renderer,scene,target)
    light.casts_shadows=True
    # Filament's defaults, readable before any change.
    assert (light.shadow_map_size,light.shadow_normal_bias)==(1024,1)
    assert light.shadow_constant_bias==pytest.approx(0.001)
    for size in (256,1024):
        light.shadow_map_size=size
        assert ((baseline[:,:,0]-read(renderer,scene,target)[:,:,0])>20).sum()>30
    light.shadow_constant_bias=0.002
    light.shadow_normal_bias=0.5
    # One option changes without resetting the others.
    assert light.shadow_map_size==1024 and light.shadow_constant_bias==pytest.approx(0.002)
    assert light.shadow_normal_bias==0.5
    for name,value in (("shadow_map_size",0),("shadow_map_size",17),("shadow_map_size",8192),("shadow_map_size",-1),
                       ("shadow_constant_bias",-1),("shadow_normal_bias",float("nan"))):
        with pytest.raises(ValueError): setattr(light,name,value)
    with pytest.raises(TypeError): light.shadow_map_size=512.0
    scene.shadows = False
    np.testing.assert_array_equal(read(renderer,scene,target),baseline)
    light.close()
    with pytest.raises(filly.FillyError): light.shadow_map_size=512
    with pytest.raises(filly.FillyError): light.shadow_map_size


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
    scene.environment = renderer.create_environment(panorama())
    scene.environment_intensity = 50000
    np.testing.assert_array_equal(read(renderer,scene,target),background)
    scene.environment_visible=True
    first=read(renderer,scene,target)
    assert first[:,:,:3].max()>20 and first[:,:,3].min()==255
    assert scene.environment_rotation_deg==0 and scene.environment_intensity==50000
    scene.environment_rotation_deg=180
    assert scene.environment_rotation_deg==180
    opposite=read(renderer,scene,target)
    assert np.abs(first-opposite).mean()>10
    scene.environment_rotation_deg=0
    np.testing.assert_array_equal(read(renderer,scene,target),first)
    scene.environment_intensity=0
    assert read(renderer,scene,target)[:,:,:3].max()==0
    scene.environment_intensity=50000
    np.testing.assert_array_equal(read(renderer,scene,target),first)
    scene.environment_visible=False
    np.testing.assert_array_equal(read(renderer,scene,target),background)
    scene.environment_visible=True
    environment=scene.environment
    scene.environment = None
    assert scene.environment is None and not environment.closed
    np.testing.assert_array_equal(read(renderer,scene,target),background)
    # Visibility, intensity, and rotation are scene settings that apply to the next panorama.
    scene.environment_rotation_deg = 180
    assert scene.environment_rotation_deg==180 and scene.environment_intensity==50000
    scene.environment = renderer.create_environment(panorama())
    np.testing.assert_array_equal(read(renderer,scene,target),opposite)
    scene.environment = environment
    np.testing.assert_array_equal(read(renderer,scene,target),opposite)
    for value in (float("nan"),float("inf")):
        with pytest.raises(ValueError): scene.environment_rotation_deg=value
    with pytest.raises(ValueError): scene.environment_intensity=-1
    # Closing the environment removes it from the scene.
    environment.close()
    assert scene.environment is None
    np.testing.assert_array_equal(read(renderer,scene,target),background)
    with pytest.raises(filly.FillyError, match="Environment is closed"):
        scene.environment = environment


def test_scenes_share_an_environment(renderer,scene):
    """Each scene has its own intensity and rotation of the shared, filtered textures."""
    other=renderer.create_scene()
    other.camera=scene.camera
    environment=renderer.create_environment(panorama())
    target=renderer.create_render_target(width=64,height=64)
    for each in (scene,other):
        each.environment=environment
        each.environment_visible=True
        each.environment_intensity=50000
    assert scene.environment==other.environment==environment
    first=read(renderer,scene,target)
    np.testing.assert_array_equal(read(renderer,other,target),first)
    other.environment_rotation_deg=180
    other.environment_intensity=25000
    np.testing.assert_array_equal(read(renderer,scene,target),first)
    assert np.abs(read(renderer,other,target).astype(int)-first).mean()>10
    other.close()
    assert not environment.closed and scene.environment==environment
    np.testing.assert_array_equal(read(renderer,scene,target),first)
    with filly.Renderer() as second:
        with pytest.raises(ValueError, match="same renderer"):
            second.create_scene().environment=environment


def test_default_environment_settings(renderer,scene):
    assert scene.environment is None
    assert scene.environment_intensity==30000 and scene.environment_rotation_deg==0
    assert not scene.environment_visible


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
        light=scene.add_spot_light(position=(0,0,1),direction=(0,0,-1),intensity=20,cone_deg=(11.459156,22.918312))
    scene.camera.exposure=4
    target=renderer.create_render_target(width=32,height=32)
    narrow=read(renderer,scene,target)[16,16]
    assert light.type=="spot" and narrow[0]>40
    # The cone reads back as set or authored (0.2 and 0.4 radians), not as Filament recomputes it.
    np.testing.assert_allclose(light.cone_deg,(11.459156,22.918312),rtol=1e-6)
    # glTF defines spot intensity in candela, so the on-axis brightness must not change.
    light.cone_deg=(17,69)
    assert list(light.cone_deg)==[17,69]
    assert light.intensity==pytest.approx(20)
    np.testing.assert_allclose(read(renderer,scene,target)[16,16],narrow,atol=1)


def test_each_light_type_reads_none_for_what_it_lacks(scene):
    point = scene.add_point_light(position=(1, 2, 3))
    spot = scene.add_spot_light(position=(0, 0, 1), direction=(0, 0, -2))
    directional = scene.add_directional_light(direction=(0, -1, 0))
    sun = scene.add_sun_light(direction=(0, -1, 0))
    np.testing.assert_allclose(point.position, (1, 2, 3))
    assert point.direction is None and point.cone_deg is None and point.range == 10
    np.testing.assert_allclose(spot.direction, (0, 0, -1))
    assert list(spot.cone_deg) == [15, 30]
    for light in (directional, sun):
        assert light.position is None and light.range is None and light.cone_deg is None
        np.testing.assert_allclose(light.direction, (0, -1, 0))
    # Setting what a type lacks would do nothing, so it raises instead.
    for light, name, value in ((point, "direction", (0, 0, 1)), (point, "cone_deg", (10, 20)),
                               (directional, "position", (0, 0, 0)), (sun, "position", (0, 0, 0)),
                               (directional, "range", 5), (sun, "cone_deg", (10, 20))):
        with pytest.raises(ValueError, match="applies only"):
            setattr(light, name, value)
    for cone in ((-1, 10), (20, 10), (10, 90), (0, 0), (float("nan"), 10)):
        with pytest.raises(ValueError):
            spot.cone_deg = cone
    assert list(spot.cone_deg) == [15, 30]


@pytest.mark.parametrize("kind", ["point", "spot"])
def test_default_intensities_are_visible_at_the_default_exposure(renderer, kind):
    """A default point or spot light 1 m from a white surface lights it as the default
    directional light does."""
    camera = renderer.create_camera()
    camera.position = (0, 0, 2)
    camera.look_at((0, 0, 0))
    target = renderer.create_render_target(width=16, height=16)
    pixels = {}
    for light in ("directional", kind):
        with renderer.create_scene() as scene:
            scene.camera = camera
            scene.create_mesh(**filly.shapes.plane(4, 4))
            if light == "directional":
                scene.add_directional_light(direction=(0, 0, -1))
            elif light == "point":
                scene.add_point_light(position=(0, 0, 1))
            else:
                scene.add_spot_light(position=(0, 0, 1), direction=(0, 0, -1))
            renderer.render(scene, target)
            pixels[light] = target.read()[8, 8, :3].astype(int)
    assert pixels["directional"].min() > 150
    np.testing.assert_allclose(pixels[kind], pixels["directional"], atol=1)
