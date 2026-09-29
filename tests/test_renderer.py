from concurrent.futures import ThreadPoolExecutor
import gc
import json
import struct

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.gpu


def test_offscreen_pixels_and_orientation(renderer, scene, triangle_glb):
    scene.load(triangle_glb)
    target = renderer.create_render_target(width=64, height=48)
    renderer.render(scene, target)
    image = target.read()
    assert image.shape == (48, 64, 4)
    assert image.dtype == np.uint8
    assert image.flags.c_contiguous
    np.testing.assert_array_equal(image[24, 32], [255, 0, 0, 255])
    np.testing.assert_array_equal(image[0, 0], [0, 0, 0, 255])
    assert 0 < (image[10, :, 0] > 200).sum() < (image[28, :, 0] > 200).sum()
    assert (image[40, :, 0] > 200).sum() == 0
    renderer.close()
    assert image[24, 32, 0] == 255


def test_transform_visibility_and_history(renderer, scene, triangle_glb):
    model = scene.load(triangle_glb)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    first = target.read()
    model.position = (4, 0, 0)
    renderer.render(scene, target)
    assert target.read()[..., 0].max() == 0
    model.position = (0, 0, 0)
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read(), first)
    model.visible = False
    renderer.render(scene, target)
    assert target.read()[..., 0].max() == 0
    model.visible = True
    renderer.render(scene, target)
    np.testing.assert_array_equal(target.read(), first)
    assert renderer.stats.frames_rendered == 5
    assert renderer.stats.cpu_submit_ms >= 0
    # GPU time is not measured, so there is no field that could suggest it.
    assert not hasattr(renderer.stats, "gpu_ms")


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_matrices_are_row_major_copies(scene, triangle_glb, dtype):
    model = scene.load(triangle_glb)
    matrix = np.eye(4, dtype=dtype)
    matrix[:3, 3] = [1, 2, 3]
    model.transform = matrix
    np.testing.assert_array_equal(model.transform, matrix)
    np.testing.assert_array_equal(model.position, [1, 2, 3])
    copy = model.transform
    copy[0, 3] = 9
    assert model.position[0] == 1
    scene.camera.transform = matrix
    np.testing.assert_allclose(scene.camera.view_matrix @ matrix, np.eye(4), atol=1e-6)
    with pytest.raises(ValueError):
        matrix[0, 0] = np.nan
        model.transform = matrix


def test_perspective_changes_coverage(renderer, scene, triangle_glb):
    scene.load(triangle_glb)
    target = renderer.create_render_target(width=64, height=64)
    renderer.render(scene, target)
    ortho = target.read()[..., 0] > 200
    scene.camera.set_perspective(fov_y=60, aspect=1, near=0.1, far=10)
    renderer.render(scene, target)
    perspective = target.read()[..., 0] > 200
    assert 0 < perspective.sum() < ortho.sum()
    assert scene.camera.projection[3, 2] == -1


def test_background_and_independent_targets(renderer, scene):
    a = renderer.create_render_target(width=19, height=13)
    b = renderer.create_render_target(width=8, height=8, depth=False)
    scene.background = (0.25, 0.5, 0.75, 1)
    renderer.render(scene, a)
    scene.background = (1, 0, 0, 1)
    renderer.render(scene, b)
    # Background colors are linear, like material factors, and are sRGB-encoded on output.
    np.testing.assert_allclose(a.read()[0, 0], [137, 188, 225, 255], atol=1)
    np.testing.assert_array_equal(b.read()[0, 0], [255, 0, 0, 255])


@pytest.mark.parametrize("filename", ["vertices.bin", "vertices with spaces.bin", "vertices_\u866b%+.bin"])
def test_asset_paths_and_external_buffers(scene, triangle_glb, tmp_path, filename):
    from urllib.parse import quote

    tmp_path = tmp_path / "asset_\u866b"
    tmp_path.mkdir()
    path = tmp_path / "triangle.glb"
    path.write_bytes(triangle_glb)
    assert scene.load(path).visible
    json_size = struct.unpack_from("<I", triangle_glb, 12)[0]
    document = json.loads(triangle_glb[20:20 + json_size])
    document["buffers"][0]["uri"] = quote(filename)
    gltf = tmp_path / "triangle.gltf"
    gltf.write_text(json.dumps(document))
    with pytest.raises(filly.AssetError, match="Missing glTF resource"):
        scene.load(gltf)
    (tmp_path / filename).write_bytes(triangle_glb[28 + json_size:])
    assert scene.load(gltf).visible
    with pytest.raises(filly.AssetError):
        scene.load(b"invalid")
    with pytest.raises(filly.AssetError):
        scene.load(tmp_path / "absent.glb")


def test_target_size_accepts_integral_numbers(renderer):
    for value in (np.int32(4), np.uint16(4), np.int64(4), 4.0, np.float32(4), np.float64(4)):
        target = renderer.create_render_target(width=value, height=value)
        assert (target.width, target.height) == (4, 4)
        target.close()
    for value in (4.5, np.float32(4.25), float("nan"), float("inf")):
        with pytest.raises(ValueError, match="width must be a whole number"):
            renderer.create_render_target(width=value, height=4)
    for value in ("4", None, True, [4]):
        with pytest.raises(TypeError):
            renderer.create_render_target(width=4, height=value)
    with pytest.raises(ValueError, match="from 1 through 8192, got 8193x4"):
        renderer.create_render_target(width=8193.0, height=4)
    with pytest.raises(ValueError, match="from 1 through 8192"):
        renderer.create_render_target(width=2**70, height=4)
    with pytest.raises(ValueError, match="from 1 through 8192"):
        renderer.create_render_target(width=-(2**70), height=4)


def test_validation(renderer, scene):
    for dimensions in [(0, 4), (4, -1), (8193, 4)]:
        with pytest.raises(ValueError):
            renderer.create_render_target(width=dimensions[0], height=dimensions[1])
    with pytest.raises(ValueError, match="format must be 'rgba8', got 'rgb8'"):
        renderer.create_render_target(width=4, height=4, format="rgb8")
    with pytest.raises(ValueError):
        scene.camera.set_perspective(fov_y=180, aspect=1, near=0.1, far=10)
    with pytest.raises(ValueError):
        scene.camera.look_at(scene.camera.position)
    with pytest.raises(ValueError):
        scene.add_directional_light(direction=(0, 0, 0))
    target = renderer.create_render_target(width=4, height=4)
    with pytest.raises(filly.FillyError, match="before reading"):
        target.read()
    with pytest.raises(filly.FillyError, match="scene.camera"):
        renderer.render(renderer.create_scene(), target)


def test_cross_renderer_and_thread_rejection(renderer, scene):
    with filly.Renderer() as other:
        target = other.create_render_target(width=8, height=8)
        with pytest.raises(ValueError, match="belong"):
            renderer.render(scene, target)
        with pytest.raises(ValueError, match="belong"):
            scene.camera = other.create_scene().create_camera()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(filly.FillyError, match="creating thread"):
            executor.submit(renderer.create_scene).result()


def test_close_invalidates_children(renderer, scene, triangle_glb):
    camera = scene.camera
    model = scene.load(triangle_glb)
    target = renderer.create_render_target(width=8, height=8)
    renderer.close()
    renderer.close()
    assert renderer.closed
    for operation in [lambda: camera.transform, lambda: model.position,
                      lambda: target.width, scene.create_camera, renderer.finish]:
        with pytest.raises(filly.FillyError, match="closed"):
            operation()


def test_children_keep_engine_alive():
    renderer = filly.Renderer()
    scene = renderer.create_scene()
    del renderer
    gc.collect()
    camera = scene.create_camera()
    camera.position = (1, 2, 3)
    np.testing.assert_array_equal(camera.position, [1, 2, 3])


def test_removed_surface_is_gone():
    # OpenGL is the only backend; render() already flushes; bones update automatically.
    with pytest.raises(TypeError):
        filly.Renderer(backend="opengl")
    for owner, name in ((filly.Renderer, "flush"), (filly.Model, "update_bones"), (filly.Scene, "load_glb"),
                        (filly.Scene, "load_gltf"), (filly.Scene, "set_rendering_options"),
                        (filly.Node, "make_material_unique"), (filly.Stats, "gpu_ms")):
        assert not hasattr(owner, name), name
    assert not hasattr(filly, "RenderTarget")


def test_context_manager_preserves_exception():
    with pytest.raises(RuntimeError, match="trial failed"):
        with filly.Renderer() as renderer:
            raise RuntimeError("trial failed")
    assert renderer.closed


def test_ten_thousand_transform_updates(renderer, scene, triangle_glb):
    model = scene.load(triangle_glb)
    target = renderer.create_render_target(width=32, height=32)
    renderer.render(scene, target)
    initial = target.read()
    for i in range(10000):
        model.position = (0.5 if i % 2 == 0 else 0, 0, 0)
        renderer.render(scene, target)
    np.testing.assert_array_equal(target.read(), initial)
    assert renderer.stats.frames_rendered == 10001


@pytest.mark.parametrize("postprocessing", [False, True])
def test_environment_before_first_frame(triangle_glb, postprocessing):
    # Destroying the IBL prefilter objects after set_environment() made models loaded later
    # render black (no postprocessing) or wrong in the first frame (postprocessing).
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        if postprocessing:
            scene.antialiasing = "fxaa"
            scene.tone_mapping = "aces_legacy"
        scene.set_environment(np.ones((16, 32, 3), dtype=np.float32), intensity=20000)
        camera = scene.create_camera()
        camera.set_perspective(fov_y=40, aspect=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.load(triangle_glb)
        target = renderer.create_render_target(width=64, height=64)
        renderer.render(scene, target)
        first = target.read()
        renderer.render(scene, target)
        np.testing.assert_array_equal(first, target.read())
        assert first[32, 32, 0] > 200


def test_entity_indices_are_recycled(renderer, scene):
    # Filament recycles destroyed entity indices in endFrame(). Without that, creation slows
    # sharply once 2^17 indices have been used.
    import time
    target = renderer.create_render_target(width=8, height=8)

    def batch():
        start = time.perf_counter()
        for _ in range(20):
            for _ in range(1000):
                scene.add_directional_light(direction=(0, -1, 0)).close()
            renderer.render(scene, target)
        return time.perf_counter() - start

    first = batch()
    for _ in range(8):
        batch()
    assert batch() < 10 * first
