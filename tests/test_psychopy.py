import sys

import numpy as np
import pytest

pytestmark = [pytest.mark.gpu, pytest.mark.psychopy,
              pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="WGL/GLX adapter")]


@pytest.mark.parametrize("use_fbo", [False, True])
@pytest.mark.parametrize("effects", [False, True])
def test_psychopy_samples_texture_and_owns_flip(triangle_glb, monkeypatch, use_fbo, effects):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import SharedTarget, create_renderer

    win = visual.Window(size=(128, 128), units="pix", winType="pyglet", useFBO=use_fbo,
                        checkTiming=False, waitBlanking=False, autoLog=False, allowGUI=False)
    if use_fbo:
        # PsychoPy 2026.2.4 with pyglet 1.4.11 does not present FBO windows on any tested GPU,
        # so only the FBO contents are checked.
        win.winHandle.set_visible(False)
    # Swap counting starts after this flip.
    win.flip()
    swaps = []
    original_swap = win.backend.swapBuffers

    def swap(*args, **kwargs):
        swaps.append(1)
        return original_swap(*args, **kwargs)

    monkeypatch.setattr(win.backend, "swapBuffers", swap)
    try:
        label = visual.TextBox2(win, text="Horse", units="pix", pos=(-60, 60), size=(110, 22),
                                letterHeight=12, color="white", anchor="top-left", alignment="left", autoLog=False)
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            if effects:
                scene.refraction = True
                scene.antialiasing = "fxaa"
            camera = scene.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            model = scene.load(triangle_glb)
            reference = renderer.create_render_target(width=128, height=128) if effects else None
            with SharedTarget(renderer, win, 128, 128) as target:
                stimulus = target.as_psychopy_texture()
                # The shared path has no CPU readback at all.
                assert not hasattr(target, "read")
                for frame, color in enumerate([(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1)]):
                    model.material("red").base_color = color
                    if reference is not None:
                        renderer.render(scene, reference)
                        expected = reference.read()[64,64,:3]
                    else:
                        expected = np.array(color[:3]) * 255
                    renderer.render(scene, target)
                    stimulus.draw()
                    label.draw()
                    assert len(swaps) == frame
                    image = np.asarray(win._getFrame(buffer="back"))
                    np.testing.assert_array_equal(image[64, 64], expected)
                    assert image[64, 64, frame] > 230
                    assert np.all(image[:22, :110] > 200, axis=2).sum() > 10
                    channel = frame
                    assert (image[24, :, channel] > 200).sum() > 0
                    assert (image[112, :, channel] > 200).sum() == 0
                    win.flip()
                    assert len(swaps) == frame + 1
                    if not use_fbo:
                        # Back-buffer pixels alone do not prove that flip presents the image.
                        presented = np.asarray(win._getFrame(buffer="front"))
                        np.testing.assert_array_equal(presented[64, 64], image[64, 64])
                        assert np.all(presented[:22, :110] > 200, axis=2).sum() > 10
                        assert (presented[24, :, channel] > 200).sum() > 0
                        assert (presented[112, :, channel] > 200).sum() == 0
                # Read before close; close() resets colorTexture to 0.
                texture_id = target.colorTexture.value
            from pyglet import gl
            assert texture_id
            assert not gl.glIsTexture(texture_id)
            with pytest.raises(Exception, match="closed"):
                stimulus.draw()
    finally:
        win.close()


@pytest.mark.parametrize("tone_mapping", ["linear", "aces_legacy"])
def test_point_shadows_and_skybox_shared_output(triangle_glb, tone_mapping):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import SharedTarget, create_renderer
    from test_lighting import shadow_asset, panorama

    win = visual.Window(size=(128,128), units="pix", winType="pyglet", useFBO=False,
                        checkTiming=False, waitBlanking=False, autoLog=False)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.tone_mapping = tone_mapping
            scene.shadows = True
            scene.transparent = True
            scene.background = (0,0,0,0)
            camera = scene.create_camera()
            camera.set_orthographic(left=-1,right=1,bottom=-1,top=1,near=0.1,far=10)
            camera.position = (0,0,3);camera.look_at((0,0,0));scene.camera = camera
            model = scene.load(shadow_asset(triangle_glb,imported=True))
            lamp = model.light("lamp")
            lamp.set_shadow_options(map_size=512)
            scene.set_environment(panorama(),intensity=10000)
            reference = renderer.create_render_target(width=128,height=128)
            with SharedTarget(renderer,win,128,128) as target:
                stimulus = target.as_psychopy_texture()
                underlay = visual.Rect(win,width=128,height=128,fillColor="blue",lineColor=None,autoLog=False)
                for enabled,visible,rotation in ((False,False,0),(True,True,180),(True,False,90)):
                    lamp.casts_shadows = enabled
                    scene.environment_visible = visible
                    scene.environment_rotation = rotation
                    renderer.render(scene,reference)
                    rgba = reference.read().astype(float)/255
                    renderer.render(scene,target)
                    underlay.draw();stimulus.draw()
                    image = np.asarray(win._getFrame(buffer="back"))
                    expected = rgba[:,:,:3].copy();expected[:,:,2] += 1-rgba[:,:,3]
                    np.testing.assert_allclose(image,expected*255,atol=3)
                    win.flip()
    finally:
        win.close()


@pytest.mark.parametrize("projection", ["ortho", 110])
def test_rough_glass_shared_output(projection):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import SharedTarget, create_renderer
    from test_refraction import striped_glass, configure_camera

    win = visual.Window(size=(128, 128), units="pix", winType="pyglet", useFBO=False,
                        checkTiming=False, waitBlanking=False, autoLog=False)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.refraction = True
            scene.tone_mapping = "aces_legacy"
            configure_camera(scene, projection)
            scene.load(striped_glass(volume=True, thickness=0.6))
            reference = renderer.create_render_target(width=128, height=128)
            renderer.render(scene, reference)
            expected = reference.read()[:, :, :3]
            with SharedTarget(renderer, win, 128, 128) as target:
                stimulus = target.as_psychopy_texture()
                renderer.render(scene, target)
                stimulus.draw()
                np.testing.assert_allclose(np.asarray(win._getFrame(buffer="back")), expected, atol=3)
                win.flip()
    finally:
        win.close()


@pytest.mark.parametrize("fxaa", [False, True])
@pytest.mark.parametrize("opacity", [0.5, 1.0])
def test_transparent_compositing(triangle_glb, fxaa, opacity):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import SharedTarget, create_renderer
    from test_features import pack, unpack

    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"][3] = 0.5
    win = visual.Window(size=(128, 128), units="pix", winType="pyglet", useFBO=False,
                        checkTiming=False, waitBlanking=False, autoLog=False)
    try:
        underlay = visual.Rect(win, width=128, height=128, fillColor="blue", lineColor=None, autoLog=False)
        overlay = visual.Rect(win, width=12, height=12, pos=(45, 45), fillColor="green", lineColor=None, autoLog=False)
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.background = (0, 0, 0, 0)
            scene.transparent = True
            scene.antialiasing = "fxaa" if fxaa else "none"
            camera = scene.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            scene.load(pack(doc, binary))
            reference = renderer.create_render_target(width=128, height=128)
            renderer.render(scene, reference)
            rgba = reference.read().astype(float) / 255
            assert 0.4 < rgba[64, 64, 3] < 0.6
            with SharedTarget(renderer, win, 128, 128) as target:
                stimulus = target.as_psychopy_texture()
                stimulus.opacity = opacity
                renderer.render(scene, target)
                underlay.draw()
                original_shader = win._progImageStim
                stimulus.draw()
                assert win._progImageStim == original_shader
                image = np.asarray(win._getFrame(buffer="back"))
                expected = rgba[:, :, :3] * opacity
                expected[:, :, 2] += 1 - rgba[:, :, 3] * opacity
                np.testing.assert_allclose(image, expected * 255, atol=3)
                assert renderer.stats.host_wait_ms >= 0
                assert renderer.stats.host_release_ms >= 0
                overlay.draw()
                image = np.asarray(win._getFrame(buffer="back"))
                np.testing.assert_array_equal(image[19, 109], [0, 128, 0])
                win.flip()
    finally:
        win.close()


def test_target_draw_and_shared_acquire(triangle_glb):
    """target.draw() places pixels as the GL adapters do; acquire() lets several stimuli draw one frame."""
    visual = pytest.importorskip("psychopy.visual")
    from filly import InteropError
    from filly.integrations.psychopy import SharedTarget, create_renderer

    win = visual.Window(size=(128, 128), units="pix", winType="pyglet", useFBO=False,
                        checkTiming=False, waitBlanking=False, autoLog=False)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            camera = scene.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            scene.load(triangle_glb)
            with SharedTarget(renderer, win, 64, 64) as target:
                left = target.as_psychopy_texture(pos=(-32, 0))
                shared = left._shared_program
                renderer.render(scene, target)
                with target.acquire():
                    target.draw(64, 64)
                    left.draw()
                # Rows count down from the top: the upper-right quadrant and the left-center square.
                image = np.asarray(win._getFrame(buffer="back"))
                np.testing.assert_array_equal(image[32, 96], (255, 0, 0))
                np.testing.assert_array_equal(image[64, 32], (255, 0, 0))
                assert image[96, 96, 0] == image[96, 96, 1] == image[96, 96, 2]  # Window background.
                win.flip()
                # A static stimulus can be drawn again on the next flip without a new render.
                left.draw()
                np.testing.assert_array_equal(np.asarray(win._getFrame(buffer="back"))[64, 32], (255, 0, 0))
                win.flip()
            with SharedTarget(renderer, win, 64, 64) as fresh:
                stimulus = fresh.as_psychopy_texture()
                # Stimuli of every target in a window share one program.
                assert stimulus._shared_program == shared
                with pytest.raises(InteropError, match="Render to the target"):
                    stimulus.draw()
    finally:
        win.close()


def _small_window(visual):
    return visual.Window(size=(64, 48), units="pix", winType="pyglet", useFBO=False,
                         checkTiming=False, waitBlanking=False, autoLog=False, allowGUI=False)


def test_shared_target_accepts_integral_sizes():
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import SharedTarget, create_renderer

    win = _small_window(visual)
    try:
        with create_renderer(win) as renderer:
            # win.size holds numpy integers; PsychoPy also reports sizes as floats.
            for width, height in ((win.size[0], win.size[1]), (np.int64(64), np.int32(48)), (64.0, np.float32(48))):
                with SharedTarget(renderer, win, width, height) as target:
                    assert (target.width, target.height) == (64, 48)
                    stimulus = target.as_psychopy_texture()
                    assert tuple(stimulus.size) == (64, 48)
    finally:
        win.close()


@pytest.mark.parametrize("width, height, error, match", [
    (64.5, 48, ValueError, "64.5"),
    (64, np.float64(47.25), ValueError, "47.25"),
    (float("nan"), 48, ValueError, "nan"),
    (8193.0, 48, ValueError, "8192"),
    (np.int64(0), 48, ValueError, "8192"),
    ("64", 48, TypeError, "str"),
    (True, 48, TypeError, "bool"),
])
def test_shared_target_rejects_invalid_sizes(width, height, error, match):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import SharedTarget, create_renderer

    win = _small_window(visual)
    try:
        with create_renderer(win) as renderer:
            with pytest.raises(error, match=match):
                SharedTarget(renderer, win, width, height)
    finally:
        win.close()


def test_close_after_window_close_releases_everything():
    visual = pytest.importorskip("psychopy.visual")
    from filly import InteropError
    from filly.integrations.psychopy import SharedTarget, create_renderer

    win = _small_window(visual)
    renderer = create_renderer(win)
    target = SharedTarget(renderer, win, 64, 48)
    stimulus = target.as_psychopy_texture()
    win.close()
    # Without the host context, only Filament's own work can be fenced.
    target.close()
    assert target._closed and target.colorTexture.value == 0 and stimulus._texID.value == 0
    target.close()
    with pytest.raises(InteropError, match="closed"):
        stimulus.draw()
    renderer.close()
    assert renderer.closed


def test_stimulus_cleanup_stays_in_its_own_context(triangle_glb):
    """ImageStim.__del__ calls clearTextures(), often while another window is current."""
    visual = pytest.importorskip("psychopy.visual")
    import ctypes
    import pyglet
    from pyglet import gl
    from filly import current_gl_context
    from filly.integrations.psychopy import SharedTarget, create_renderer

    win = _small_window(visual)
    other = pyglet.window.Window(width=16, height=16, visible=False)
    try:
        with create_renderer(win) as renderer, SharedTarget(renderer, win, 64, 48) as target:
            scene = renderer.create_scene()
            scene.camera = scene.create_camera()
            scene.load(triangle_glb)
            renderer.render(scene, target)
            stimulus = target.as_psychopy_texture()
            # Drawing binds the mask texture, which makes its name a texture object.
            stimulus.draw()
            mask = stimulus._maskID.value
            assert gl.glIsTexture(mask)
            other.switch_to()
            current = current_gl_context()
            stimulus.clearTextures()
            assert current_gl_context() == current and stimulus._maskID.value == 0
            # PsychoPy tracks its own current window, so switch through pyglet.
            win.winHandle.switch_to()
            # The mask was not deleted from the other context; it goes with its own.
            assert gl.glIsTexture(mask)
            gl.glDeleteTextures(1, ctypes.byref(gl.GLuint(mask)))
    finally:
        other.close()
        win.close()
