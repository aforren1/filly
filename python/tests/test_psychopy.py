import sys

import numpy as np
import pytest

pytestmark = [pytest.mark.gpu, pytest.mark.psychopy,
              pytest.mark.skipif(sys.platform not in ("win32", "linux", "darwin"), reason="WGL/GLX/CGL adapter")]
# On macOS, each frame is copied into the window's context, by either of two paths.
TRANSFERS = ["iosurface", "readback"] if sys.platform == "darwin" else ["shared"]


@pytest.fixture(autouse=True, params=TRANSFERS)
def transfer(request, monkeypatch):
    if request.param != "shared":
        monkeypatch.setenv("FILLY_PSYCHOPY_TRANSFER", request.param)
    return request.param


# pyglet 1.5, which PsychoPy releases use on macOS, often fails to open a window after another
# window of the process has closed (docs/explanation/macos.md#third-party-issues). There, the
# tests share one window per configuration and close it at the end of the module. Elsewhere,
# each test has its own window.
def _reuse_windows():
    if sys.platform != "darwin":
        return False
    import pyglet
    return int(pyglet.version.split(".")[0]) < 2


_shared_windows = {}


@pytest.fixture(scope="module", autouse=True)
def _close_shared_windows():
    yield
    for win in _shared_windows.values():
        win.close()
    _shared_windows.clear()


def _window(visual, size=(128, 128), use_fbo=False, allow_gui=True, shared=True):
    options = dict(size=size, units="pix", winType="pyglet", useFBO=use_fbo, checkTiming=False,
                   waitBlanking=False, autoLog=False, allowGUI=allow_gui)
    if not (shared and _reuse_windows()):
        return visual.Window(**options)
    key = (tuple(size), use_fbo, allow_gui)
    if key not in _shared_windows:
        _shared_windows[key] = visual.Window(**options)
    return _shared_windows[key]


def _release(win):
    if any(win is shared for shared in _shared_windows.values()):
        # Clears the back buffer, as a new window would have it.
        win.flip()
    else:
        win.close()


@pytest.mark.parametrize("use_fbo", [False, True])
@pytest.mark.parametrize("effects", [False, True])
def test_psychopy_samples_texture_and_owns_flip(triangle_glb, monkeypatch, use_fbo, effects, transfer):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _window(visual, use_fbo=use_fbo, allow_gui=False)
    if use_fbo and sys.platform != "darwin":
        # PsychoPy 2026.2.4 with pyglet 1.4.11 does not present FBO windows on any tested GPU,
        # so only the FBO contents are checked. On macOS, PsychoPy's flip() waits for a callback
        # from the window's display link, which a hidden window never sends.
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
                scene.antialiasing = "fxaa"
            camera = renderer.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            model = scene.load(triangle_glb)
            reference = renderer.create_render_target(width=128, height=128) if effects else None
            with HostTarget(renderer, win, width=128, height=128) as target:
                stimulus = target.create_image_stim()
                assert target.transfer == transfer
                # The target has no CPU readback of its own.
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
                # Read before close; close() resets texture to 0.
                texture_id = target.texture
            from pyglet import gl
            assert texture_id
            assert not gl.glIsTexture(texture_id)
            with pytest.raises(Exception, match="closed"):
                stimulus.draw()
    finally:
        _release(win)


@pytest.mark.parametrize("tone_mapping", ["linear", "aces_legacy"])
def test_point_shadows_and_skybox_shared_output(triangle_glb, tone_mapping):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer
    from test_lighting import shadow_asset, panorama

    win = _window(visual)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.tone_mapping = tone_mapping
            scene.shadows = True
            scene.transparent = True
            scene.background = (0,0,0,0)
            camera = renderer.create_camera()
            camera.set_orthographic(left=-1,right=1,bottom=-1,top=1,near=0.1,far=10)
            camera.position = (0,0,3);camera.look_at((0,0,0));scene.camera = camera
            model = scene.load(shadow_asset(triangle_glb,imported=True))
            lamp = model.light("lamp")
            lamp.shadow_map_size = 512
            scene.environment = renderer.create_environment(panorama())
            scene.environment_intensity = 10000
            reference = renderer.create_render_target(width=128,height=128)
            with HostTarget(renderer, win, width=128, height=128) as target:
                stimulus = target.create_image_stim()
                underlay = visual.Rect(win,width=128,height=128,fillColor="blue",lineColor=None,autoLog=False)
                for enabled,visible,rotation in ((False,False,0),(True,True,180),(True,False,90)):
                    lamp.casts_shadows = enabled
                    scene.environment_visible = visible
                    scene.environment_rotation_deg = rotation
                    renderer.render(scene,reference)
                    rgba = reference.read().astype(float)/255
                    renderer.render(scene,target)
                    underlay.draw();stimulus.draw()
                    image = np.asarray(win._getFrame(buffer="back"))
                    expected = rgba[:,:,:3].copy();expected[:,:,2] += 1-rgba[:,:,3]
                    np.testing.assert_allclose(image,expected*255,atol=3)
                    win.flip()
    finally:
        _release(win)


@pytest.mark.parametrize("projection", ["ortho", 110])
def test_rough_glass_shared_output(projection):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer
    from test_refraction import striped_glass, configure_camera

    win = _window(visual)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.tone_mapping = "aces_legacy"
            configure_camera(renderer, scene, projection)
            scene.load(striped_glass(volume=True, thickness=0.6))
            reference = renderer.create_render_target(width=128, height=128)
            renderer.render(scene, reference)
            expected = reference.read()[:, :, :3]
            with HostTarget(renderer, win, width=128, height=128) as target:
                stimulus = target.create_image_stim()
                renderer.render(scene, target)
                stimulus.draw()
                np.testing.assert_allclose(np.asarray(win._getFrame(buffer="back")), expected, atol=3)
                win.flip()
    finally:
        _release(win)


@pytest.mark.parametrize("fxaa", [False, True])
@pytest.mark.parametrize("opacity", [0.5, 1.0])
def test_transparent_compositing(triangle_glb, fxaa, opacity):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer
    from test_features import pack, unpack

    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"][3] = 0.5
    win = _window(visual)
    try:
        underlay = visual.Rect(win, width=128, height=128, fillColor="blue", lineColor=None, autoLog=False)
        overlay = visual.Rect(win, width=12, height=12, pos=(45, 45), fillColor="green", lineColor=None, autoLog=False)
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.background = (0, 0, 0, 0)
            scene.transparent = True
            scene.antialiasing = "fxaa" if fxaa else "none"
            camera = renderer.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            scene.load(pack(doc, binary))
            reference = renderer.create_render_target(width=128, height=128)
            renderer.render(scene, reference)
            rgba = reference.read().astype(float) / 255
            assert 0.4 < rgba[64, 64, 3] < 0.6
            with HostTarget(renderer, win, width=128, height=128) as target:
                stimulus = target.create_image_stim()
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
        _release(win)


def test_target_draw_and_shared_acquire(triangle_glb):
    """target.draw() places pixels as the GL adapters do; acquire() lets several stimuli draw one frame."""
    visual = pytest.importorskip("psychopy.visual")
    from filly import InteropError
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _window(visual)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            camera = renderer.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            scene.load(triangle_glb)
            with HostTarget(renderer, win, width=64, height=64) as target:
                left = target.create_image_stim(pos=(-32, 0))
                shared = left._shared_program
                renderer.render(scene, target)
                with target.acquire():
                    target.draw(viewport=(64, 64, 64, 64))
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
            with HostTarget(renderer, win, width=64, height=64) as fresh:
                stimulus = fresh.create_image_stim()
                # Stimuli of every target in a window share one program.
                assert stimulus._shared_program == shared
                with pytest.raises(InteropError, match="Render to the target"):
                    stimulus.draw()
    finally:
        _release(win)


def _small_window(visual, shared=True):
    return _window(visual, size=(64, 48), allow_gui=False, shared=shared)


def test_host_target_accepts_integral_sizes():
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _small_window(visual)
    try:
        with create_renderer(win) as renderer:
            # win.size holds numpy integers; PsychoPy also reports sizes as floats.
            for width, height in ((win.size[0], win.size[1]), (np.int64(64), np.int32(48)), (64.0, np.float32(48))):
                with HostTarget(renderer, win, width=width, height=height) as target:
                    assert (target.width, target.height) == (64, 48)
                    stimulus = target.create_image_stim()
                    assert tuple(stimulus.size) == (64, 48)
    finally:
        _release(win)


@pytest.mark.parametrize("width, height, error, match", [
    (64.5, 48, ValueError, "64.5"),
    (64, np.float64(47.25), ValueError, "47.25"),
    (float("nan"), 48, ValueError, "nan"),
    (8193.0, 48, ValueError, "8192"),
    (np.int64(0), 48, ValueError, "8192"),
    ("64", 48, TypeError, "str"),
    (True, 48, TypeError, "bool"),
])
def test_host_target_rejects_invalid_sizes(width, height, error, match):
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _small_window(visual)
    try:
        with create_renderer(win) as renderer:
            with pytest.raises(error, match=match):
                HostTarget(renderer, win, width=width, height=height)
    finally:
        _release(win)


def test_close_after_window_close_releases_everything(transfer):
    visual = pytest.importorskip("psychopy.visual")
    from filly import InteropError
    from filly.integrations.psychopy import HostTarget, create_renderer

    if _reuse_windows() and transfer == "readback":
        # This test closes its window, and with pyglet 1.5 the next window to open after a close
        # often fails. One closed window per process keeps the windows after it safe.
        pytest.skip("pyglet 1.5 on macOS: only one test may close a window")
    win = _small_window(visual, shared=False)
    renderer = create_renderer(win)
    target = HostTarget(renderer, win, width=64, height=48)
    stimulus = target.create_image_stim()
    win.close()
    # Without the host context, only Filament's own work can be fenced.
    target.close()
    assert target.closed and target.texture == 0 and stimulus._texID.value == 0
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
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _small_window(visual)
    switch_to_other, close_other = _other_context(pyglet)
    try:
        with create_renderer(win) as renderer, HostTarget(renderer, win, width=64, height=48) as target:
            scene = renderer.create_scene()
            scene.camera = renderer.create_camera()
            scene.load(triangle_glb)
            renderer.render(scene, target)
            stimulus = target.create_image_stim()
            # Drawing binds the mask texture, which makes its name a texture object.
            stimulus.draw()
            mask = stimulus._maskID.value
            assert gl.glIsTexture(mask)
            switch_to_other()
            current = current_gl_context()
            stimulus.clearTextures()
            assert current_gl_context() == current and stimulus._maskID.value == 0
            # PsychoPy tracks its own current window, so switch through pyglet.
            win.winHandle.switch_to()
            # The mask was not deleted from the other context; it goes with its own.
            assert gl.glIsTexture(mask)
            gl.glDeleteTextures(1, ctypes.byref(gl.GLuint(mask)))
    finally:
        close_other()
        _release(win)


def _other_context(pyglet):
    """Return functions that make a second OpenGL context current and destroy it."""
    if sys.platform != "darwin":
        other = pyglet.window.Window(width=16, height=16, visible=False)
        return other.switch_to, other.close
    # pyglet 2.1.19 crashes when it creates a second window beside a PsychoPy window on macOS
    # (create_context in pyglet/gl/cocoa.py), with or without filly, so this is a plain CGL context.
    import ctypes
    cgl = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
    attributes = (ctypes.c_int * 1)(0)
    pixel_format, count, context = ctypes.c_void_p(), ctypes.c_int(), ctypes.c_void_p()
    cgl.CGLChoosePixelFormat(attributes, ctypes.byref(pixel_format), ctypes.byref(count))
    cgl.CGLCreateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    assert cgl.CGLCreateContext(pixel_format, None, ctypes.byref(context)) == 0
    cgl.CGLSetCurrentContext.argtypes = cgl.CGLDestroyContext.argtypes = [ctypes.c_void_p]
    return (lambda: cgl.CGLSetCurrentContext(context)), (lambda: cgl.CGLDestroyContext(context))


def test_renderer_close_releases_targets_and_stimuli(triangle_glb):
    """renderer.close() alone deletes the host texture and detaches the target's stimuli."""
    visual = pytest.importorskip("psychopy.visual")
    from pyglet import gl
    from filly import InteropError
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _small_window(visual)
    try:
        renderer = create_renderer(win)
        scene = renderer.create_scene()
        scene.camera = renderer.create_camera()
        scene.load(triangle_glb)
        target = HostTarget(renderer, win)
        # The default size is the window's framebuffer.
        assert (target.width, target.height) == tuple(win.frameBufferSize)
        stimulus = target.create_image_stim()
        renderer.render(scene, target)
        stimulus.draw()
        name = target.texture
        assert gl.glIsTexture(name)
        renderer.close()
        assert target.closed and target.texture == 0 and stimulus._texID.value == 0
        assert not gl.glIsTexture(name)
        with pytest.raises(InteropError, match="closed"):
            stimulus.draw()
        with pytest.raises(InteropError, match="closed"):
            target.draw()
        target.close()
    finally:
        _release(win)


def test_draw_copy_and_blend(triangle_glb):
    """draw() composites source-over by default; blend=False copies, as the GL adapters do."""
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer
    from test_features import pack, unpack

    doc, binary = unpack(triangle_glb)
    doc["materials"][0]["alphaMode"] = "BLEND"
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [1, 0, 0, 0.5]
    win = _small_window(visual)
    try:
        underlay = visual.Rect(win, width=64, height=48, fillColor="blue", lineColor=None, autoLog=False)
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.background = (0, 0, 0, 0)
            scene.transparent = True
            camera = renderer.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            scene.load(pack(doc, binary))
            with HostTarget(renderer, win, width=48, height=48) as target:
                renderer.render(scene, target)
                images = []
                for blend in (True, False):
                    underlay.draw()
                    target.draw(blend=blend)
                    # The window keeps blending for the stimuli that follow.
                    assert gl_blend_enabled()
                    images.append(np.asarray(win._getFrame(buffer="back")).astype(int))
                    win.flip()
    finally:
        _release(win)
    blended, copied = images
    # Rows count down from the top; the texture covers the lower-left 48 x 48 pixels.
    np.testing.assert_allclose(blended[24, 24], (128, 0, 127), atol=3)
    np.testing.assert_array_equal(blended[46, 2], (0, 0, 255))
    np.testing.assert_allclose(copied[24, 24], (128, 0, 0), atol=2)
    np.testing.assert_array_equal(copied[46, 2], (0, 0, 0))
    np.testing.assert_array_equal(copied[24, 60], (0, 0, 255))


def gl_blend_enabled():
    from pyglet import gl
    return bool(gl.glIsEnabled(gl.GL_BLEND))


def test_many_frames_without_a_cpu_wait_stay_ordered():
    """Each frame reaches the window in order, although nothing reads the window between frames."""
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _window(visual, allow_gui=False)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.camera = renderer.create_camera()
            values = [(index + 1) / 65 for index in range(64)]
            # Expected bytes first: these reads wait for the GPU, and the loop below must not.
            reference = renderer.create_render_target(width=16, height=16)
            expected = []
            for value in values:
                scene.background = (value, 1 - value, 0.5, 1)
                renderer.render(scene, reference)
                expected.append(reference.read()[8, 8, :3])
            with HostTarget(renderer, win, width=16, height=16) as target:
                for index, value in enumerate(values):
                    scene.background = (value, 1 - value, 0.5, 1)
                    renderer.render(scene, target)
                    target.draw(viewport=(16 * (index % 8), 16 * (index // 8), 16, 16), blend=False)
                image = np.asarray(win._getFrame(buffer="back"))
        for index, colors in enumerate(expected):
            # Rows count down from the top; viewports count up from the bottom.
            row, column = 127 - (16 * (index // 8) + 8), 16 * (index % 8) + 8
            np.testing.assert_array_equal(image[row, column], colors, err_msg=f"frame {index}")
    finally:
        _release(win)


@pytest.mark.skipif(sys.platform != "darwin", reason="Frames are copied only on macOS")
def test_macos_renderer_is_offscreen_and_falls_back_to_readback(monkeypatch, transfer):
    visual = pytest.importorskip("psychopy.visual")
    import filly
    from filly.integrations.psychopy import HostTarget, create_renderer

    monkeypatch.delenv("FILLY_PSYCHOPY_TRANSFER")
    win = _small_window(visual)
    try:
        with create_renderer(win) as renderer:
            # PsychoPy's legacy profile context cannot share objects with Filament's.
            assert renderer.shared_context == 0 and renderer.gl_platform == "cgl"
            with HostTarget(renderer, win, width=8, height=8) as target:
                assert target.transfer == "iosurface" and target.fallback_reason is None
            create = filly.Renderer._create_copy_target

            def unavailable(self, texture, **options):
                if options["transfer"] == "iosurface":
                    raise filly.InteropError("no IOSurface for the test")
                return create(self, texture, **options)

            monkeypatch.setattr(filly.Renderer, "_create_copy_target", unavailable)
            with HostTarget(renderer, win, width=8, height=8) as target:
                assert target.transfer == "readback"
                assert "no IOSurface for the test" in target.fallback_reason
            # A forced transfer does not fall back.
            monkeypatch.setenv("FILLY_PSYCHOPY_TRANSFER", "iosurface")
            with pytest.raises(filly.InteropError, match="no IOSurface"):
                HostTarget(renderer, win, width=8, height=8)
            monkeypatch.setenv("FILLY_PSYCHOPY_TRANSFER", "gpu")
            with pytest.raises(ValueError, match="FILLY_PSYCHOPY_TRANSFER"):
                HostTarget(renderer, win, width=8, height=8)
    finally:
        _release(win)


@pytest.mark.skipif(sys.platform != "darwin", reason="Covers the copy into the window's texture")
@pytest.mark.parametrize("encoding", ["srgb", "linear"])
def test_copy_keeps_the_stored_bytes_of_the_direct_path(encoding):
    """The copies must not decode or encode sRGB: the direct path writes through an sRGB attachment."""
    visual = pytest.importorskip("psychopy.visual")
    from filly.integrations.psychopy import HostTarget, create_renderer

    win = _small_window(visual)
    try:
        with create_renderer(win) as renderer:
            scene = renderer.create_scene()
            scene.camera = renderer.create_camera()
            scene.background = (0.2, 0.5, 0.8, 1)
            scene.output_path = "direct"
            scene.encoding = encoding
            reference = renderer.create_render_target(width=48, height=48)
            renderer.render(scene, reference)
            expected = reference.read()[24, 24, :3]
            with HostTarget(renderer, win, width=48, height=48) as target:
                renderer.render(scene, target)
                target.draw(blend=False)
                image = np.asarray(win._getFrame(buffer="back"))
        np.testing.assert_array_equal(image[24, 24], expected)
    finally:
        _release(win)
