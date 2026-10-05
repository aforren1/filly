import importlib.util
from pathlib import Path

import numpy as np
import pytest

import filly


def test_sample_location_without_import():
    # psychopy-filly's Builder component finds the file this way, without the native module.
    # In an editable install, the package spans the source folder and the native build folder;
    # the origin, __init__.py, is in the folder that holds samples/.
    folder = Path(importlib.util.find_spec("filly").origin).parent
    assert folder / "samples" / "suzanne.glb" == filly.samples.SUZANNE
    assert filly.samples.SUZANNE.is_file()
    assert (folder / "samples" / "ATTRIBUTION.md").is_file()


@pytest.mark.gpu
def test_suzanne_renders_blue_metal(renderer, scene):
    model = scene.load(filly.samples.SUZANNE)
    assert model.material_names == ["Suzanne"]
    material = model.material("Suzanne")
    assert material.metallic == 1 and 0 < material.roughness < 1
    scene.set_environment(np.ones((8, 16, 3), np.float32), intensity=20000)
    scene.add_directional_light(direction=(-1, -1, -2), intensity=100000)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    red, green, blue, alpha = target.read()[32, 32].astype(int)
    assert blue > green > red and alpha == 255


@pytest.mark.gpu
def test_unknown_names_list_the_model_names(scene):
    model = scene.load(filly.samples.SUZANNE)
    with pytest.raises(filly.AssetError, match="^Unknown material 'Wood'; the model has: 'Suzanne'$"):
        model.material("Wood")
    with pytest.raises(filly.AssetError, match="^Unknown animation 'spin'; the model has no named animations$"):
        model.apply_animation("spin", 0.0)
    with pytest.raises(filly.AssetError, match="^Unknown variant 'red'; the model has no named variants$"):
        model.apply_variant("red")
    with pytest.raises(filly.AssetError, match="^Unknown node 'Head'; the model has: 'Suzanne'$"):
        model.node("Head")
