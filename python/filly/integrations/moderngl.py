"""Shared-texture integration for moderngl attached to a window's OpenGL context."""

from .._native import (_create_host_quad, _create_host_texture, _delete_host_quad, _delete_host_texture,
                       _draw_host_quad)
from ._host import HostTargetBase, shared_renderer

__all__ = ["HostTarget", "create_renderer"]


def create_renderer():
    """Create a Filament engine sharing the current OpenGL context, which moderngl attaches to.

    moderngl cannot make a context current; the window library does that.
    """
    return shared_renderer("The window library")


class HostTarget(HostTargetBase):
    """A moderngl RGBA8 texture that Filament renders into.

    ``texture`` is a ``moderngl.Texture``, None after close. The size defaults to the size of
    ``ctx.fbo``, the framebuffer that moderngl uses. ``draw()`` draws a quad into the bound
    framebuffer and restores the GL state that it changes, including the blend state.
    """

    texture = _quad = None
    _name = 0

    def __init__(self, renderer, ctx, *, width=None, height=None):
        self._ctx = ctx
        super().__init__(renderer, width, height)

    def _default_size(self):
        return self._ctx.fbo.size

    def _create_host_texture(self, width, height):
        # moderngl allocates mutable storage, which cannot have the sRGB view that Filament
        # renders through, so the texture is created here and wrapped. moderngl never deletes it.
        self._name = _create_host_texture(width, height)
        self.texture = self._ctx.external_texture(self._name, (width, height), 4, 0, "f1")
        # moderngl cannot read the blend state back, so the native quad draws and restores it.
        self._quad = _create_host_quad()
        return self._name

    def _draw(self, x, y, width, height, blend):
        _draw_host_quad(self._quad, self._name, x, y, width, height, blend)

    def _release_host_resources(self, host_open):
        if host_open:
            if self._quad is not None:
                _delete_host_quad(self._quad)
            if self.texture is not None:
                self.texture.release()
            _delete_host_texture(self._name)
        self.texture = self._quad = None
        self._name = 0
