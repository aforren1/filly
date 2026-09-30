import json

import numpy as np
import pytest

import filly
from test_features import pack, unpack

pytestmark = pytest.mark.gpu


def test_model_close_invalidates_handles_and_preserves_other_assets(renderer, scene, triangle_glb):
    model = scene.load(triangle_glb)
    node, material = model.node("triangle"), model.material("red")
    other = scene.load(triangle_glb)
    other.position = (0.7, 0, 0)
    model.close()
    model.close()
    assert model.closed
    assert not other.closed
    for get in (lambda: model.bounds, lambda: model.visible, lambda: model.node_names,
                lambda: model.animations, lambda: model.variants, lambda: model.lights,
                lambda: node.position, lambda: node.morph_target_count, lambda: material.base_color):
        with pytest.raises(filly.FillyError, match="closed"):
            get()
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    image = target.read()
    assert image[32, 32, 0] == 0
    assert image[32, 54, 0] == 255
    assert renderer.stats.live_models == 1


def test_light_cleanup_and_imported_handles(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["extensionsUsed"].append("KHR_lights_punctual")
    doc["extensions"] = {"KHR_lights_punctual": {"lights": [{"type": "point"}]}}
    doc["nodes"][0]["extensions"] = {"KHR_lights_punctual": {"light": 0}}
    model = scene.load(pack(doc, binary))
    first, alias = model.light("triangle"), model.light("triangle")
    added = scene.add_point_light(position=(0, 0, 2))
    assert renderer.stats.live_lights == 2
    first.close()
    assert alias.closed
    with pytest.raises(filly.FillyError):
        alias.intensity = 2
    model.node("triangle").position = (0.1, 0, 0)
    model.visible = False
    model.visible = True
    assert alias.closed
    added.close()
    added.close()
    replacement = scene.add_directional_light(direction=(0, 0, -1))
    with pytest.raises(filly.FillyError, match="closed"):
        added.intensity = 2
    assert not replacement.closed
    model.close()
    assert first.closed
    second_model = scene.load(pack(doc, binary))
    second_light = second_model.light("triangle")
    second_model.close()
    assert second_light.closed
    with pytest.raises(filly.FillyError, match="closed"):
        _ = second_light.intensity
    replacement.close()
    assert renderer.stats.live_lights == 0


def test_node_local_material_pixels_and_repeated_cleanup(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["nodes"] = [{"mesh": 0, "name": "left", "translation": [-0.5, 0, 0], "scale": [0.5]*3},
                    {"mesh": 0, "name": "right", "translation": [0.5, 0, 0], "scale": [0.5]*3}]
    doc["scenes"][0]["nodes"] = [0, 1]
    asset = pack(doc, binary)
    target = renderer.create_render_target(width=64, height=64)
    for _ in range(30):
        model = scene.load(asset)
        original = model.material("red")
        private = model.node("left").material()
        private.base_color = (0, 1, 0, 1)
        np.testing.assert_allclose(original.base_color, [1, 0, 0, 1])
        np.testing.assert_allclose(model.node("left").material().base_color, [0, 1, 0, 1])
        renderer.render(scene, target)
        image = target.read()
        np.testing.assert_array_equal(image[32, 16], [0, 255, 0, 255])
        np.testing.assert_array_equal(image[32, 48], [255, 0, 0, 255])
        assert renderer.stats.material_copies == 1
        model.close()
        assert renderer.stats.live_models == renderer.stats.material_copies == 0
        with pytest.raises(filly.FillyError, match="closed"):
            private.base_color = (1, 1, 1, 1)
    renderer.finish()
    assert renderer.stats.finish_ms >= 0
    assert renderer.stats.readback_ms > 0


def test_variant_shows_on_node_local_material(renderer, scene, triangle_glb):
    doc, binary = unpack(triangle_glb)
    green = json.loads(json.dumps(doc["materials"][0]))
    green.update(name="green", pbrMetallicRoughness={"baseColorFactor": [0, 1, 0, 1]})
    doc["materials"].append(green)
    doc["extensionsUsed"].append("KHR_materials_variants")
    doc["extensions"] = {"KHR_materials_variants": {"variants": [{"name": "original"}, {"name": "green"}]}}
    doc["meshes"][0]["primitives"][0]["extensions"] = {"KHR_materials_variants": {"mappings": [
        {"material": 0, "variants": [0]}, {"material": 1, "variants": [1]}]}}
    model = scene.load(pack(doc, binary))
    target = renderer.create_render_target(width=32, height=32)

    def center():
        renderer.render(scene, target)
        return target.read()[16, 16, :3].tolist()

    node = model.node("triangle")
    local = node.material()
    local.base_color = (0, 0, 1, 1)
    assert center() == [0, 0, 255]
    # The variant maps this slot, so the node shows the variant. The node-local handle follows
    # the slot to a fresh copy of the variant material; the blue edit is discarded.
    model.apply_variant("green")
    assert center() == [0, 255, 0]
    np.testing.assert_allclose(local.base_color, [0, 1, 0, 1])
    local.base_color = (1, 1, 0, 1)
    assert center() == [255, 255, 0]
    np.testing.assert_allclose(model.material("green").base_color, [0, 1, 0, 1])
    model.apply_variant(0)
    assert center() == [255, 0, 0]
    assert renderer.stats.material_copies == 1
    with pytest.raises(filly.AssetError, match="Unknown variant"):
        model.apply_variant("missing")
    with pytest.raises(filly.AssetError, match="index 2"):
        model.apply_variant(2)
    model.close()
    assert renderer.stats.material_copies == 0


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
def test_transparent_target(renderer, scene, triangle_glb, encoding):
    scene.background = (0, 0, 0, 0)
    scene.encoding = encoding
    scene.transparent = True
    scene.load(triangle_glb)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    image = target.read()
    np.testing.assert_array_equal(image[0, 0], [0, 0, 0, 0])
    assert image[32, 32, 0] > 230
    assert image[32, 32, 3] == 255
    scene.background = (1, 0, 0, 0.5)
    renderer.render(scene, target)
    pixel = target.read()[0, 0]
    np.testing.assert_allclose(pixel, [128, 0, 0, 128], atol=5)


def test_scene_close_releases_contents(renderer, scene, triangle_glb):
    other = renderer.create_scene()
    other_camera = other.create_camera()
    other_camera.set_orthographic(height=2, near=0.1, far=10)
    other_camera.position = (0, 0, 3)
    other.camera = other_camera
    kept = other.load(triangle_glb)
    model = scene.load(triangle_glb)
    node = model.node("triangle")
    light = scene.add_point_light(position=(0, 0, 1))
    camera = scene.camera
    scene.set_environment(np.full((8, 16, 3), 0.5, dtype="f"))
    # A camera from the closing scene can be active elsewhere; it must be detached there too.
    third = renderer.create_scene()
    third.camera = camera
    target = renderer.create_render_target(width=16, height=16)
    renderer.render(scene, target)
    assert not scene.closed and renderer.stats.live_models == 2
    scene.close()
    scene.close()
    assert scene.closed and model.closed and light.closed
    assert renderer.stats.live_models == 1 and renderer.stats.live_lights == 0
    for action in (lambda: renderer.render(scene, target), lambda: scene.load(triangle_glb),
                   lambda: scene.background, lambda: setattr(scene, "encoding", "linear"),
                   lambda: node.position, lambda: camera.position, lambda: model.clone()):
        with pytest.raises(filly.FillyError, match="closed"):
            action()
    with pytest.raises(filly.FillyError, match="no active camera"):
        third.camera
    renderer.render(other, target)
    assert target.read()[8, 8, 0] == 255 and not kept.closed


def test_many_renderers_close_cleanly():
    """Renderer teardown must not leave callbacks that outlive the material provider. The removed
    material warmup had one: Filament ran its compile callback at engine shutdown, after the
    provider was freed (heap-use-after-free under ASan; crashes in about 1 of 5 runs of 42
    renderers on Windows)."""
    import subprocess
    import sys
    import textwrap
    script = textwrap.dedent("""
        import filly
        filly.set_log_level("off")
        shape = filly.shapes.uv_sphere(0.5, segments=16, rings=8)
        for _ in range(40):
            with filly.Renderer() as renderer:
                scene = renderer.create_scene()
                camera = scene.create_camera()
                camera.position = (0, 0, 3)
                camera.look_at((0, 0, 0))
                scene.camera = camera
                scene.create_mesh(**shape, base_color=(0.5, 0.5, 0.5, 1))
                target = renderer.create_render_target(width=8, height=8)
                renderer.render(scene, target)
        print("ok")
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=300)
    assert result.returncode == 0 and result.stdout.decode().strip() == "ok", result.stderr.decode(errors="replace")[-400:]
