import numpy as np
import pytest

import filly
from test_features import lit, pack, unpack
from test_animation_pointer import accessor, add_clip

pytestmark = pytest.mark.gpu


def light_asset(triangle_glb, spot=False):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    light = {"type": "spot" if spot else "point", "intensity": 1000, "range": 10}
    if spot:
        light["spot"] = {"innerConeAngle": 0.1, "outerConeAngle": 0.2}
    doc["extensionsUsed"] = ["KHR_lights_punctual"]
    doc["extensions"] = {"KHR_lights_punctual": {"lights": [light]}}
    doc["nodes"].append({"name": "lamp", "translation": [0, 0, 2], "extensions": {"KHR_lights_punctual": {"light": 0}}})
    doc["scenes"][0]["nodes"].append(1)
    return doc, binary


def test_animated_range_pixels_and_reset(renderer, scene, triangle_glb):
    doc, binary = light_asset(triangle_glb)
    add_clip(doc, binary, "/extensions/KHR_lights_punctual/lights/0/range", [1, 10])
    model = scene.load(pack(doc, binary), strict=True)
    scene.camera.exposure = 6
    target = renderer.create_render_target(width=32, height=32)
    model.apply_animation(0, 0)
    renderer.render(scene, target)
    assert target.read()[16, 16, :3].max() == 0
    model.apply_animation(0, 2, loop=False)
    renderer.render(scene, target)
    assert target.read()[16, 16, :3].max() > 20
    model.light("lamp").range = 3
    model.reset_animation()
    assert model.light("lamp").range == pytest.approx(10)
    model.light("lamp").close()
    model.apply_animation(0, 1)
    assert model.light("lamp").closed


@pytest.mark.parametrize("reverse", [False, True])
def test_spot_cones_combined_before_commit(renderer, scene, triangle_glb, reverse):
    doc, binary = light_asset(triangle_glb, True)
    prefix = "/extensions/KHR_lights_punctual/lights/0/spot/"
    clip = add_clip(doc, binary, prefix+"innerConeAngle", [0.1, 0.4])
    output = accessor(doc, binary, [0.2, 0.7])
    clip["samplers"].append({"input": clip["samplers"][0]["input"], "output": output})
    clip["channels"].append({"sampler": 1, "target": {"path": "pointer", "extensions": {"KHR_animation_pointer": {"pointer": prefix+"outerConeAngle"}}}})
    if reverse:
        clip["channels"].reverse()
    model = scene.load(pack(doc, binary), strict=True)
    scene.camera.exposure = 6
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    before = target.read()
    model.apply_animation(0, 2, loop=False)
    renderer.render(scene, target)
    after = target.read()
    assert np.abs(after.astype(int)-before.astype(int)).max() > 20
    assert model.light("lamp").intensity == pytest.approx(1000)
    model.reset_animation()
    renderer.render(scene, target)
    np.testing.assert_array_equal(before, target.read())
    model.light("lamp").close()
    model.apply_animation(0, 1)
    assert model.light("lamp").closed


def test_invalid_combined_cones_reject_and_recover(renderer, scene, triangle_glb):
    doc, binary = light_asset(triangle_glb, True)
    add_clip(doc, binary, "/extensions/KHR_lights_punctual/lights/0/spot/innerConeAngle", [0.1, 0.5])
    model = scene.load(pack(doc, binary))
    with pytest.raises(filly.AssetError, match="cones"):
        model.apply_animation(0, 2, loop=False)
    model.reset_animation()
    model.apply_animation(0, 0)


def test_directional_range_rejected(scene, triangle_glb):
    doc, binary = light_asset(triangle_glb)
    doc["extensions"]["KHR_lights_punctual"]["lights"][0]["type"] = "directional"
    add_clip(doc, binary, "/extensions/KHR_lights_punctual/lights/0/range", [1, 10])
    with pytest.raises(filly.AssetError, match="range"):
        scene.load(pack(doc, binary))
