import copy

import numpy as np
import pytest

import filly
from test_features import pack, unpack

pytestmark = pytest.mark.gpu


@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
@pytest.mark.parametrize("extended", [False, True])
def test_volume_thickness_uses_parent_and_model_scale(triangle_glb, mode, extended):
    """KHR_materials_volume scales thickness by the complete node transform.

    Filament 1.77.1 uses only the mesh node's own scale; gltf_viewer differs here by design.
    """
    doc, binary = unpack(triangle_glb)
    extensions = {
        "KHR_materials_transmission": {"transmissionFactor": 1},
        "KHR_materials_volume": {"thicknessFactor": 0.6,
                                 "attenuationColor": [0.1, 0.7, 0.3], "attenuationDistance": 0.2},
    }
    if extended:
        extensions["KHR_materials_iridescence"] = {"iridescenceFactor": 0.2}
    doc["extensionsUsed"] += list(extensions)
    doc["materials"].append({"name": "glass", "extensions": extensions,
                             "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 0}})
    doc["meshes"].append({"primitives": [{"attributes": {"POSITION": 0}, "material": 1}]})
    doc["nodes"] = [{"mesh": 1, "name": "glass-node"}, {"children": [0], "name": "parent"}]
    doc["scenes"][0]["nodes"] = [1]
    backing = copy.deepcopy(doc)
    backing["nodes"] = [{"mesh": 0, "translation": [0, 0, -0.5], "scale": [2, 2, 2]}]
    backing["scenes"][0]["nodes"] = [0]
    # Keep the backing independent so model-root edits affect only the volume.
    backing["materials"] = backing["materials"][:1]
    backing["meshes"] = backing["meshes"][:1]
    backing["extensionsUsed"] = ["KHR_materials_unlit"]
    with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
        scene = renderer.create_scene()
        scene.refraction = True
        scene.tone_mapping = "aces_legacy"
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.load(pack(backing, binary))
        model = scene.load(pack(doc, binary))
        target = renderer.create_render_target(width=64, height=64)

        def render():
            renderer.render(scene, target)
            return target.read()

        for scale in (0.25, 0.7, 1.5):
            model.node("glass-node").scale = (scale,) * 3
            reference = render()
            model.node("glass-node").scale = (1, 1, 1)
            model.node("parent").scale = (scale,) * 3
            np.testing.assert_allclose(render(), reference, atol=1)
            model.node("parent").scale = (1, 1, 1)
            model.scale = (scale,) * 3
            np.testing.assert_allclose(render(), reference, atol=1)
            model.scale = (1, 1, 1)
        # This comparison must include visible absorption, not just matching empty images.
        opaque = render()[32, 32, 0]
        model.close()
        assert int(render()[32, 32, 0]) > int(opaque) + 30


@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
@pytest.mark.parametrize("reverse", [False, True])
def test_volume_cache_separates_dispersion(triangle_glb, mode, reverse):
    doc, binary = unpack(triangle_glb)
    plain = {"name": "plain", "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 0},
             "extensions": {"KHR_materials_transmission": {"transmissionFactor": 1},
                            "KHR_materials_volume": {"thicknessFactor": 0.1}}}
    dispersive = copy.deepcopy(plain)
    dispersive["name"] = "dispersive"
    dispersive["extensions"]["KHR_materials_dispersion"] = {"dispersion": 1}
    doc["materials"] = [dispersive, plain] if reverse else [plain, dispersive]
    doc["extensionsUsed"] = list(dispersive["extensions"])
    doc.pop("extensionsRequired")
    doc["meshes"].append(copy.deepcopy(doc["meshes"][0]))
    doc["meshes"][1]["primitives"][0]["material"] = 1
    doc["nodes"].append({"mesh": 1, "translation": [0, 0, -0.5]})
    doc["scenes"][0]["nodes"].append(1)
    with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
        scene = renderer.create_scene()
        scene.refraction = True
        scene.tone_mapping = "aces_legacy"
        # Both variants must retain their own shader layout, regardless of cache insertion order.
        model = scene.load(pack(doc, binary))
        assert set(model.material_names) == {"plain", "dispersive"}
