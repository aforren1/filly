"""A host OpenGL texture as a material input: the host writes, then Filament samples."""

import ctypes
import sys

import numpy as np
import pytest

import filly

pytest.importorskip("pyglet")
from filly.integrations.pyglet import create_renderer  # noqa: E402

pytestmark = [pytest.mark.gpu, pytest.mark.interop,
              pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="WGL/GLX implementation")]

SIZE = 16


@pytest.fixture
def window():
    pyglet = pytest.importorskip("pyglet")
    value = pyglet.window.Window(width=SIZE, height=SIZE, visible=False)
    value.switch_to()
    try:
        yield value
    finally:
        if value.context is not None:
            value.switch_to()
            value.close()


class HostPainter:
    """A host texture with immutable RGBA8 storage and a framebuffer that clears it."""

    def __init__(self, size):
        from pyglet import gl
        self.gl = gl
        self.texture = filly._native._create_host_texture(size, size)
        self.framebuffer = gl.GLuint()
        gl.glGenFramebuffers(1, ctypes.byref(self.framebuffer))
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.framebuffer)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, self.texture, 0)
        assert gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER) == gl.GL_FRAMEBUFFER_COMPLETE
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)

    def fill(self, rgba):
        """Store these bytes in every texel. A clear of a plain RGBA8 texture stores c * 255."""
        gl = self.gl
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.framebuffer)
        gl.glClearColor(*(value / 255 for value in rgba))
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)

    def close(self):
        gl = self.gl
        gl.glDeleteFramebuffers(1, ctypes.byref(self.framebuffer))
        filly._native._delete_host_texture(self.texture)


def textured_scene(renderer, texture):
    scene = renderer.create_scene()
    camera = scene.create_camera()
    camera.set_orthographic(height=2, near=0.1, far=10)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    plane = scene.create_mesh(**filly.shapes.plane(2, 2), unlit=True, alpha_mode="blend")
    plane.material("mesh").base_color_texture = texture
    return scene, plane


@pytest.mark.parametrize("path", ["direct", "grading"])
def test_host_writes_reach_the_next_frame(window, path):
    renderer = create_renderer(window)
    painter = HostPainter(4)
    try:
        texture = renderer.import_gl_input(painter.texture, width=4, height=4, color_space="srgb", filter="nearest")
        scene, plane = textured_scene(renderer, texture)
        scene.msaa = 4 if path == "grading" else 1
        target = renderer.create_render_target(width=SIZE, height=SIZE)
        # sRGB bytes are decoded when sampled and encoded again on output, so they come back.
        for color in ((188, 0, 0, 255), (0, 100, 255, 255), (30, 60, 90, 255)):
            with texture.write():
                assert texture.writing
                painter.fill(color)
            renderer.render(scene, target)
            np.testing.assert_allclose(target.read()[8, 8], color, atol=1)
        with texture.write():
            painter.fill((0, 0, 0, 255))
            with pytest.raises(filly.InteropError, match="write"):
                renderer.render(scene, target)
        with pytest.raises(filly.InteropError, match="already"):
            with texture.write():
                with texture.write():
                    pass
        assert plane.material("mesh").base_color_texture == texture
        texture.close()
        assert texture.closed and plane.material("mesh").base_color_texture is None
        target.close()
    finally:
        painter.close()
        renderer.close()


def test_linear_host_texture_is_sampled_raw(window):
    renderer = create_renderer(window)
    painter = HostPainter(4)
    try:
        texture = renderer.import_gl_input(painter.texture, width=4, height=4, color_space="linear")
        scene, _ = textured_scene(renderer, texture)
        target = renderer.create_render_target(width=SIZE, height=SIZE)
        with texture.write():
            painter.fill((128, 128, 128, 255))
        renderer.render(scene, target)
        # Linear 128/255 encodes to sRGB 188.
        np.testing.assert_allclose(target.read()[8, 8], (188, 188, 188, 255), atol=1)
        # Many frames and writes without a CPU wait stay ordered.
        for value in range(0, 250, 10):
            with texture.write():
                painter.fill((value, value, value, 255))
            renderer.render(scene, target)
        expected = np.round(255 * (1.055 * (240 / 255) ** (1 / 2.4) - 0.055))
        np.testing.assert_allclose(target.read()[8, 8, :3], expected, atol=1)
        texture.close()
        target.close()
    finally:
        painter.close()
        renderer.close()


def test_import_requires_a_shared_renderer_and_storage(window):
    with filly.Renderer() as offscreen:
        with pytest.raises(filly.InteropError, match="shared_context"):
            offscreen.import_gl_input(1, width=4, height=4, color_space="srgb")
    with create_renderer(window) as renderer:
        from pyglet import gl
        mutable = gl.GLuint()
        gl.glGenTextures(1, ctypes.byref(mutable))
        gl.glBindTexture(gl.GL_TEXTURE_2D, mutable)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, 4, 4, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
        # Sampling sRGB data needs an sRGB view, and a view needs immutable storage.
        with pytest.raises(filly.InteropError, match="immutable"):
            renderer.import_gl_input(mutable.value, width=4, height=4, color_space="srgb")
        renderer.import_gl_input(mutable.value, width=4, height=4, color_space="linear").close()
        with pytest.raises(ValueError, match="color_space"):
            renderer.import_gl_input(mutable.value, width=4, height=4, color_space="rgb")
        gl.glDeleteTextures(1, ctypes.byref(mutable))
