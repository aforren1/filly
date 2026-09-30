import ctypes
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

import filly

pytestmark = [pytest.mark.gpu, pytest.mark.interop,
              pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="WGL/GLX implementation")]


@pytest.fixture
def host():
    pyglet = pytest.importorskip("pyglet")
    from pyglet import gl
    window = pyglet.window.Window(width=64, height=64, visible=False)
    window.switch_to()
    texture = gl.GLuint()
    gl.glGenTextures(1, ctypes.byref(texture))
    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
    gl.glTexStorage2D(gl.GL_TEXTURE_2D, 1, gl.GL_RGBA8, 64, 64)
    try:
        yield window, texture, gl
    finally:
        window.switch_to()
        gl.glDeleteTextures(1, ctypes.byref(texture))
        window.close()


def read_host_texture(texture, gl):
    image = np.empty((64, 64, 4), dtype=np.uint8)
    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
    gl.glGetTexImage(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE,
                     image.ctypes.data_as(ctypes.c_void_p))
    return image


def test_shared_texture_handoff_under_contention(tmp_path):
    pytest.importorskip("pyglet")
    # A second renderer exposes cross-display races that paced single-process draws hide.
    case = str(Path(__file__).resolve()) + "::test_queued_host_sampling_before_texture_reuse"
    children, logs = [], []
    with ExitStack() as stack:
        try:
            for index in range(2):
                path = tmp_path / f"worker-{index}.log"
                logs.append(path)
                output = stack.enter_context(path.open("w"))
                children.append(subprocess.Popen(
                    [sys.executable, "-m", "pytest", case, "-q", "-p", "no:cacheprovider"],
                    stdout=output, stderr=subprocess.STDOUT,
                ))
            results = [child.wait(timeout=60) for child in children]
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait()
    for result, path in zip(results, logs):
        assert result == 0, path.read_text()


def test_shared_texture_roundtrip_and_ownership(host, triangle_glb):
    window, texture, gl = host
    with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        model = scene.load(triangle_glb)
        target = renderer.import_gl_texture(texture.value, width=64, height=64)
        assert isinstance(target, filly.ImportedTarget) and not hasattr(target, "read")
        for color in [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1)] * 4:
            model.material("red").base_color = color
            # A frame that the host never sampled can be replaced.
            renderer.render(scene, target)
            renderer.render(scene, target)
            assert not target.acquired
            with target.acquire() as held:
                assert held is target and target.acquired
                with target.acquire():
                    np.testing.assert_array_equal(read_host_texture(texture, gl)[32, 32], np.array(color) * 255)
                assert target.acquired
                with pytest.raises(filly.InteropError, match="Release the target"):
                    renderer.render(scene, target)
            assert not target.acquired
            # The last frame can be sampled again without a new render.
            with target.acquire():
                np.testing.assert_array_equal(read_host_texture(texture, gl)[32, 32], np.array(color) * 255)
        target.close()
        assert target.closed
        assert gl.glIsTexture(texture)
    assert gl.glIsTexture(texture)


def test_shared_texture_holds_srgb_bytes(host, triangle_glb):
    """A host that samples the texture as plain RGBA8 sees encoded values, not linear ones."""
    window, texture, gl = host
    with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(height=2, near=0.1, far=10)
        camera.position = (0, 0, 3)
        scene.camera = camera
        model = scene.load(triangle_glb)
        target = renderer.import_gl_texture(texture.value, width=64, height=64)
        for encoding, expected in (("srgb", 188), ("linear", 128)):
            scene.encoding = encoding
            model.material("red").base_color = (0.5, 0.5, 0.5, 1)
            renderer.render(scene, target)
            with target.acquire():
                pixel = read_host_texture(texture, gl)[32, 32].astype(int)
            np.testing.assert_allclose(pixel, [expected, expected, expected, 255], atol=1)
        target.close()


@pytest.mark.parametrize("encoding", ["srgb", "linear"])
@pytest.mark.parametrize("path", ["direct", "exact"])
def test_shared_texture_matches_transfer_function(host, triangle_glb, encoding, path):
    """The same sweep as the offscreen test, sampled by the host as plain RGBA8."""
    from test_encoding import SWEEP, srgb
    window, texture, gl = host
    with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(height=2, near=0.1, far=10)
        camera.position = (0, 0, 3)
        scene.camera = camera
        scene.encoding = encoding
        scene.output_path = path
        material = scene.load(triangle_glb).material("red")
        target = renderer.import_gl_texture(texture.value, width=64, height=64)
        actual = []
        for value in SWEEP:
            material.base_color = (value, value, value, 1)
            renderer.render(scene, target)
            with target.acquire():
                actual.append(read_host_texture(texture, gl)[32, 32, :3].astype(int))
        target.close()
    f = srgb if encoding == "srgb" else (lambda v: np.asarray(v, dtype=float))
    actual = np.array(actual)
    error = np.abs(actual - np.round(255 * f(SWEEP))[:, None])
    assert error.max() <= 1, (SWEEP[error.max(axis=1).argmax()], error.max())
    if path == "exact":
        # Exact for the value in the RGBA16F scene-linear buffer, as offscreen.
        from test_encoding import half_neighbors
        below, above = half_neighbors(SWEEP)
        candidates = np.stack([np.round(255 * f(below)), np.round(255 * f(above))], axis=1)
        assert (actual[:, :, None] == candidates[:, None, :]).any(axis=2).all()


@pytest.fixture
def mutable(host):
    """A third-party host texture: GL_RGBA8 from glTexImage2D, without immutable storage."""
    window, texture, gl = host
    name = gl.GLuint()
    gl.glGenTextures(1, ctypes.byref(name))
    gl.glBindTexture(gl.GL_TEXTURE_2D, name)
    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, 64, 64, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
    try:
        yield name, gl
    finally:
        window.switch_to()
        gl.glDeleteTextures(1, ctypes.byref(name))


def grey_scene(renderer, triangle_glb):
    scene = renderer.create_scene()
    camera = scene.create_camera()
    camera.set_orthographic(height=2, near=0.1, far=10)
    camera.position = (0, 0, 3)
    scene.camera = camera
    scene.load(triangle_glb).material("red").base_color = (0.5, 0.5, 0.5, 1)
    return scene


def test_mutable_texture_imports_on_the_default_path(mutable, triangle_glb):
    texture, gl = mutable
    with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
        scene = grey_scene(renderer, triangle_glb)
        target = renderer.import_gl_texture(texture.value, width=64, height=64)
        for encoding, path, expected in (("srgb", "exact", 188), ("linear", "exact", 128),
                                         ("linear", "direct", 128)):
            scene.encoding = encoding
            scene.output_path = path
            renderer.render(scene, target)
            with target.acquire():
                pixel = read_host_texture(texture, gl)[32, 32].astype(int)
            np.testing.assert_allclose(pixel, [expected, expected, expected, 255], atol=1)
        target.close()


def test_direct_srgb_output_rejects_a_mutable_texture(mutable, triangle_glb):
    """The GPU can encode only into an sRGB view, and a view needs immutable storage."""
    texture, gl = mutable
    with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
        scene = grey_scene(renderer, triangle_glb)
        scene.output_path = "direct"
        target = renderer.import_gl_texture(texture.value, width=64, height=64)
        with pytest.raises(filly.InteropError, match="output_path 'direct'.*glTexStorage2D"):
            renderer.render(scene, target)
        # The failed call changed nothing: the target still renders on the default path.
        scene.output_path = "exact"
        renderer.render(scene, target)
        with target.acquire():
            np.testing.assert_allclose(read_host_texture(texture, gl)[32, 32, :3], 188, atol=1)
        target.close()


def test_import_validation(host):
    window, texture, gl = host
    with pytest.raises(filly.InteropError):
        filly.Renderer(shared_context=123)
    with pytest.raises(filly.InteropError):
        filly.Renderer(shared_context=0)
    with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
        with pytest.raises(filly.InteropError):
            renderer.import_gl_texture(0, width=64, height=64)
        with pytest.raises(filly.InteropError):
            renderer.import_gl_texture(texture.value, width=63, height=64)
        with pytest.raises(ValueError, match="whole number"):
            renderer.import_gl_texture(texture.value, width=64.5, height=64)
        with pytest.raises(ValueError, match="from 1 through 8192, got 0x64"):
            renderer.import_gl_texture(texture.value, width=0, height=64)
        with pytest.raises(ValueError, match="format must be 'rgba8'"):
            renderer.import_gl_texture(texture.value, width=64, height=64, format="rgb8")
        numeric = renderer.import_gl_texture(texture.value, width=np.int64(64), height=64.0)
        assert (numeric.width, numeric.height) == (64, 64)
        numeric.close()
        target = renderer.import_gl_texture(texture.value, width=64, height=64)
        with pytest.raises(filly.InteropError, match="Render to the target"):
            with target.acquire():
                pass
        assert not target.acquired
        target.close()
        with pytest.raises(filly.FillyError, match="closed"):
            target.acquire().__enter__()


@pytest.mark.parametrize("source", ["material", "background"])
def test_queued_host_sampling_before_texture_reuse(host, triangle_glb, source):
    """Keep GPU work asynchronous; inspect the host atlas only after the last frame."""
    window, texture, gl = host
    atlas, framebuffer = gl.GLuint(), gl.GLuint()
    gl.glGenTextures(1, ctypes.byref(atlas))
    gl.glBindTexture(gl.GL_TEXTURE_2D, atlas)
    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, 512, 512, 0,
                    gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
    gl.glGenFramebuffers(1, ctypes.byref(framebuffer))
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, framebuffer)
    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, atlas, 0)
    assert gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER) == gl.GL_FRAMEBUFFER_COMPLETE
    try:
        with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
            scene = renderer.create_scene()
            camera = scene.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            material = scene.load(triangle_glb).material("red") if source == "material" else None
            target = renderer.import_gl_texture(texture.value, width=64, height=64)
            gl.glUseProgram(0)
            gl.glDisable(gl.GL_DEPTH_TEST)
            gl.glDisable(gl.GL_BLEND)
            gl.glMatrixMode(gl.GL_PROJECTION)
            gl.glLoadIdentity()
            gl.glMatrixMode(gl.GL_MODELVIEW)
            gl.glLoadIdentity()
            gl.glEnable(gl.GL_TEXTURE_2D)
            gl.glTexEnvi(gl.GL_TEXTURE_ENV, gl.GL_TEXTURE_ENV_MODE, gl.GL_REPLACE)
            # 0.5 grey checks the encoding as the host samples it: sRGB 188, not linear 128.
            colors = [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1), (0.5, 0.5, 0.5, 1)]
            expected = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (188, 188, 188, 255)]
            for frame in range(256):
                if material is not None:
                    material.base_color = colors[frame % 4]
                else:
                    scene.background = colors[frame % 4]
                renderer.render(scene, target)
                with target.acquire():
                    gl.glViewport((frame % 16) * 32, (frame // 16) * 32, 32, 32)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
                    gl.glBegin(gl.GL_QUADS)
                    for u, v in [(0, 0), (1, 0), (1, 1), (0, 1)]:
                        gl.glTexCoord2f(u, v)
                        gl.glVertex2f(u * 2 - 1, v * 2 - 1)
                    gl.glEnd()
            pixels = np.empty((512, 512, 4), dtype=np.uint8)
            gl.glReadBuffer(gl.GL_COLOR_ATTACHMENT0)
            gl.glReadPixels(0, 0, 512, 512, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE,
                             pixels.ctypes.data_as(ctypes.c_void_p))
            for frame in range(256):
                np.testing.assert_allclose(pixels[(frame // 16) * 32 + 16, (frame % 16) * 32 + 16],
                                           expected[frame % 4], atol=1, err_msg=f"{source} frame {frame}")
            target.close()
    finally:
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        gl.glDeleteFramebuffers(1, ctypes.byref(framebuffer))
        gl.glDeleteTextures(1, ctypes.byref(atlas))
