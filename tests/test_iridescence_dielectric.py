import numpy as np
import pytest

import filly
from test_features import lit, pack, unpack

pytestmark = pytest.mark.gpu


def test_dielectric_thin_film_palette(triangle_glb):
    doc, binary = unpack(triangle_glb)
    lit(doc)
    pbr = doc["materials"][0]["pbrMetallicRoughness"]
    # A dark dielectric separates thin-film reflection from the diffuse response.
    pbr.update(baseColorFactor=[0.04, 0.04, 0.04, 1], metallicFactor=0, roughnessFactor=0.1)
    film = {"iridescenceFactor": 1, "iridescenceIor": 1.7, "iridescenceThicknessMaximum": 100}
    doc["materials"][0]["extensions"] = {
        "KHR_materials_ior": {"ior": 1.33}, "KHR_materials_iridescence": film}
    doc["extensionsUsed"] = ["KHR_materials_ior", "KHR_materials_iridescence"]
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        scene.tone_mapping = "aces_legacy"
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.set_environment(np.ones((16, 32, 3), dtype=np.float32), intensity=30000)
        target = renderer.create_render_target(width=32, height=32)

        def sample(thickness, factor):
            film.update(iridescenceThicknessMaximum=thickness, iridescenceFactor=factor)
            model = scene.load(pack(doc, binary))
            renderer.render(scene, target)
            color = target.read()[16, 16, :3].astype(int)
            model.close()
            return color

        plain = sample(100, 0)
        np.testing.assert_array_equal(sample(700, 0), plain)
        palette = np.array([sample(thickness, 1) for thickness in (100, 250, 400, 550, 700)])
        assert np.ptp(palette - plain, axis=1).max() > 8
        assert np.abs(palette - plain).max() > 8
        assert np.ptp(palette, axis=0).max() > 8
