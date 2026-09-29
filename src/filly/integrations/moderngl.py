"""Shared-texture integration for moderngl attached to a pyglet window's context.

Create the renderer with ``filly.integrations.pyglet.create_renderer(window)``.
"""

import moderngl

from .._native import _create_host_texture, _delete_host_texture
from ._host import HostTarget

__all__ = ["SharedTarget"]

_VERTEX = """
#version 330 core
out vec2 uv;
void main() {
    uv = vec2(gl_VertexID & 1, gl_VertexID >> 1);
    gl_Position = vec4(uv * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAGMENT = """
#version 330 core
uniform sampler2D image;
in vec2 uv;
out vec4 color;
void main() {
    color = texture(image, uv);
}
"""


class SharedTarget(HostTarget):
    """A moderngl RGBA8 texture that Filament renders into.

    ``draw()`` draws a quad into the bound framebuffer. The shader writes
    premultiplied RGBA. The caller owns the blend state.
    """

    texture = _program = _vao = None
    _name = 0

    def __init__(self, renderer, ctx, width, height):
        self._ctx = ctx
        super().__init__(renderer, width, height)

    def _create_host_texture(self, width, height):
        # moderngl allocates mutable storage, which cannot have the sRGB view that Filament
        # renders through, so the texture is created here and wrapped. moderngl never deletes it.
        self._name = _create_host_texture(width, height)
        self.texture = self._ctx.external_texture(self._name, (width, height), 4, 0, "f1")
        self._program = self._ctx.program(vertex_shader=_VERTEX, fragment_shader=_FRAGMENT)
        self._vao = self._ctx.vertex_array(self._program, [])
        return self._name

    def _draw(self, x, y, width, height):
        viewport = self._ctx.viewport
        try:
            self._ctx.viewport = (x, y, width, height)
            self.texture.use(0)
            self._vao.render(moderngl.TRIANGLE_STRIP, vertices=4)
        finally:
            self._ctx.viewport = viewport

    def _release_host_resources(self, host_open):
        for resource in (self._vao, self._program, self.texture):
            if host_open and resource is not None:
                resource.release()
        if host_open:
            _delete_host_texture(self._name)
        self._vao = self._program = self.texture = None
        self._name = 0
