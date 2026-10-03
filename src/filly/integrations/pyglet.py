"""Shared-texture integration for a plain pyglet window on Windows/WGL and Linux/GLX."""

import ctypes

from pyglet import gl

from .. import InteropError
from .._native import _create_host_texture, _delete_host_texture
from ._host import HostTarget, shared_renderer

__all__ = ["SharedTarget", "create_renderer"]


def _is_open(window):
    # pyglet clears the context when the window closes.
    return getattr(window, "context", None) is not None


def _current(window):
    if not _is_open(window):
        raise InteropError("The pyglet window is closed")
    window.switch_to()


def create_renderer(window, *, precompiled_shaders=False):
    """Create a Filament engine sharing the window's OpenGL context."""
    _current(window)
    return shared_renderer("pyglet", precompiled_shaders=precompiled_shaders)


class SharedTarget(HostTarget):
    """A pyglet-window RGBA8 texture that Filament renders into.

    ``draw()`` blits into the bound draw framebuffer. The blit copies premultiplied
    RGBA without blending, obeys the scissor test, and cannot write to a multisampled
    framebuffer.
    """

    texture = _framebuffer = gl.GLuint()

    def __init__(self, renderer, window, width, height):
        self._window = window
        super().__init__(renderer, width, height)

    def _host_open(self):
        return _is_open(self._window)

    def _make_host_current(self):
        _current(self._window)

    def _create_host_texture(self, width, height):
        self.texture = gl.GLuint(_create_host_texture(width, height))
        # Framebuffer objects are per context, so the host needs its own to blit from.
        self._framebuffer, read = gl.GLuint(), gl.GLint()
        gl.glGetIntegerv(gl.GL_READ_FRAMEBUFFER_BINDING, ctypes.byref(read))
        gl.glGenFramebuffers(1, ctypes.byref(self._framebuffer))
        try:
            gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, self._framebuffer)
            gl.glFramebufferTexture2D(gl.GL_READ_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                                      gl.GL_TEXTURE_2D, self.texture, 0)
        finally:
            gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, read.value)
        return self.texture.value

    def _draw(self, x, y, width, height):
        source = self.width, self.height
        read = gl.GLint()
        gl.glGetIntegerv(gl.GL_READ_FRAMEBUFFER_BINDING, ctypes.byref(read))
        try:
            gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, self._framebuffer)
            scaled = (width, height) != source
            gl.glBlitFramebuffer(0, 0, source[0], source[1], x, y, x + width, y + height,
                                 gl.GL_COLOR_BUFFER_BIT, gl.GL_LINEAR if scaled else gl.GL_NEAREST)
        finally:
            gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, read.value)

    def _release_host_resources(self, host_open):
        if host_open and self._framebuffer.value:
            gl.glDeleteFramebuffers(1, ctypes.byref(self._framebuffer))
        if host_open:
            _delete_host_texture(self.texture.value)
        self._framebuffer, self.texture = gl.GLuint(), gl.GLuint()
