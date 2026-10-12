"""Shared-texture integration for a plain pyglet window on Windows/WGL and Linux/GLX."""

from pyglet import gl

from .. import InteropError, current_gl_context
from .._native import (_create_host_quad, _create_host_texture, _delete_host_quad, _delete_host_texture,
                       _draw_host_quad)
from ._host import HostTargetBase, shared_renderer

__all__ = ["HostTarget", "create_renderer"]


def _is_open(window):
    # pyglet clears the context when the window closes.
    return getattr(window, "context", None) is not None


def _current(window):
    if not _is_open(window):
        raise InteropError("The pyglet window is closed")
    window.switch_to()


def create_renderer(window):
    """Create a Filament engine sharing the window's OpenGL context."""
    _current(window)
    return shared_renderer("pyglet")


class HostTarget(HostTargetBase):
    """A pyglet-window RGBA8 texture that Filament renders into.

    ``texture`` is the GL texture name (int), 0 after close. The size defaults to the
    window's framebuffer size. ``draw()`` draws a quad into the bound draw framebuffer.
    """

    texture = 0
    _quad = None

    def __init__(self, renderer, window, *, width=None, height=None):
        self._window = window
        super().__init__(renderer, width, height)

    def _host_open(self):
        return _is_open(self._window)

    def _make_host_current(self):
        # switch_to() costs about 30 us per acquire(). The native check guards against code that
        # switched contexts without pyglet, which pyglet's bookkeeping misses.
        if (self._context and current_gl_context() == self._context and _is_open(self._window)
                and gl.current_context is self._window.context):
            return
        _current(self._window)

    def _default_size(self):
        return self._window.get_framebuffer_size()

    def _create_host_texture(self, width, height):
        self.texture = _create_host_texture(width, height)
        self._quad = _create_host_quad()
        return self.texture

    def _draw(self, x, y, width, height, blend):
        _draw_host_quad(self._quad, self.texture, x, y, width, height, blend)

    def _release_host_resources(self, host_open):
        if host_open:
            if self._quad is not None:
                _delete_host_quad(self._quad)
            _delete_host_texture(self.texture)
        self.texture, self._quad = 0, None
