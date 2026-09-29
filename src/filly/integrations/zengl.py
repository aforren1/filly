"""Shared-texture integration for zengl attached to a pyglet window's context.

Create the renderer with ``filly.integrations.pyglet.create_renderer(window)``.
"""

import zengl

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
layout (location = 0) out vec4 color;
void main() {
    color = texture(image, uv);
}
"""

# Filament writes premultiplied RGB; this is source-over for premultiplied color.
_PREMULTIPLIED = {"enable": True, "src_color": "one", "dst_color": "one_minus_src_alpha",
                  "src_alpha": "one", "dst_alpha": "one_minus_src_alpha"}


class SharedTarget(HostTarget):
    """A zengl RGBA8 image that Filament renders into.

    ``draw()`` draws the image with premultiplied source-over blending.
    ``framebuffer`` is a list of zengl images to draw into, or ``None`` for
    the window. zengl fixes it when the pipeline is created.
    """

    texture = _pipeline = None

    def __init__(self, renderer, ctx, width, height, framebuffer=None):
        self._ctx = ctx
        self._framebuffer = framebuffer
        super().__init__(renderer, width, height)

    def _create_host_texture(self, width, height):
        # zengl allocates mutable storage, which cannot have the sRGB view that Filament renders
        # through, so the texture is created here. zengl deletes it on release.
        name = _create_host_texture(width, height)
        try:
            self.texture = self._ctx.image((width, height), "rgba8unorm", external=name)
        except BaseException:
            _delete_host_texture(name)
            raise
        self._pipeline = self._ctx.pipeline(
            vertex_shader=_VERTEX, fragment_shader=_FRAGMENT,
            layout=[{"name": "image", "binding": 0}],
            resources=[{"type": "sampler", "binding": 0, "image": self.texture,
                        "min_filter": "linear", "mag_filter": "linear",
                        "wrap_x": "clamp_to_edge", "wrap_y": "clamp_to_edge"}],
            blend=_PREMULTIPLIED, framebuffer=self._framebuffer,
            topology="triangle_strip", vertex_count=4, viewport=(0, 0, width, height))
        # zengl 2 does not expose the texture name as an attribute.
        return zengl.inspect(self.texture)["texture"]

    def _draw(self, x, y, width, height):
        self._pipeline.viewport = (x, y, width, height)
        self._pipeline.render()

    def _release_host_resources(self, host_open):
        for resource in (self._pipeline, self.texture):
            if host_open and resource is not None:
                self._ctx.release(resource)
        self._pipeline = self.texture = None
