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


def test_horse_is_installed_with_its_attribution():
    assert filly.samples.HORSE.is_file()
    assert filly.samples.HORSE.stat().st_size == 181808
    # The wheel's attribution repeats the horse's entry from the shared samples/ folder, which the
    # MATLAB and Octave packages ship. Only the repository has that folder.
    shared = Path(__file__).resolve().parents[2] / "samples" / "ATTRIBUTION.md"
    if not shared.is_file():
        pytest.skip("no repository samples/ folder")
    entry = shared.read_text(encoding="utf-8").split("## horse.glb")[1].split("\n\n")[0]
    wheel = (Path(importlib.util.find_spec("filly").origin).parent / "samples" / "ATTRIBUTION.md").read_text(
        encoding="utf-8")
    assert "## horse.glb" + entry in wheel


@pytest.mark.gpu
def test_horse_gallops_with_morph_targets(renderer, scene):
    model = scene.load(filly.samples.HORSE)
    assert [clip.duration for clip in model.animations] == [1.5]
    camera = renderer.create_camera()
    camera.frame(model)
    scene.camera = camera
    scene.add_directional_light(direction=(-1, -1, -2), intensity=100000)
    target = renderer.create_render_target(width=64, height=64)
    frames = []
    for seconds in (0.0, 0.5):
        model.apply_animation(0, seconds)
        renderer.render(scene, target)
        frames.append(target.read())
    # The clip moves the legs through morph targets, so the two poses cover different pixels.
    assert (frames[0][..., 3] > 0).sum() > 0
    assert not np.array_equal(frames[0], frames[1])


@pytest.mark.gpu
def test_suzanne_renders_blue_metal(renderer, scene):
    model = scene.load(filly.samples.SUZANNE)
    assert model.material_names == ["Suzanne"]
    material = model.material("Suzanne")
    assert material.metallic == 1 and 0 < material.roughness < 1
    scene.environment = renderer.create_environment(np.ones((8, 16, 3), np.float32))
    scene.environment_intensity = 20000
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
