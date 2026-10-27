"""Shared-texture integration for zengl attached to a window's OpenGL context."""

import zengl

from .._native import _create_host_texture, _delete_host_texture
from ._host import HostTargetBase, shared_renderer

__all__ = ["HostTarget", "create_renderer"]

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


def create_renderer():
    """Create a Filament engine sharing the current OpenGL context, which zengl attaches to.

    zengl cannot make a context current; the window library does that.
    """
    return shared_renderer("The window library")


class HostTarget(HostTargetBase):
    """A zengl RGBA8 image that Filament renders into.

    ``texture`` is a ``zengl.Image``, None after close. ``framebuffer`` is a list of zengl
    images to draw into, or ``None`` for the window. zengl fixes it when the pipeline is
    created. The size defaults to the size of the first framebuffer image. With
    ``framebuffer=None``, ``width`` and ``height`` are required: zengl does not report the
    window's size.
    """

    texture = None
    _pipelines = None

    def __init__(self, renderer, ctx, *, width=None, height=None, framebuffer=None):
        self._ctx = ctx
        self._framebuffer = framebuffer
        super().__init__(renderer, width, height)

    def _default_size(self):
        if not self._framebuffer:
            return super()._default_size()
        return self._framebuffer[0].size

    def _create_host_texture(self, width, height):
        # zengl allocates mutable storage, which cannot have the sRGB view that Filament renders
        # through, so the texture is created here. zengl deletes it on release.
        name = _create_host_texture(width, height)
        try:
            self.texture = self._ctx.image((width, height), "rgba8unorm", external=name)
        except BaseException:
            _delete_host_texture(name)
            raise
        # zengl fixes the blend state in the pipeline: one pipeline for each draw mode.
        self._pipelines = {}
        self._pipeline(True)
        # zengl 2 does not expose the texture name as an attribute.
        return zengl.inspect(self.texture)["texture"]

    def _pipeline(self, blend):
        pipeline = self._pipelines.get(blend)
        if pipeline is None:
            width, height = self.texture.size
            pipeline = self._pipelines[blend] = self._ctx.pipeline(
                vertex_shader=_VERTEX, fragment_shader=_FRAGMENT,
                layout=[{"name": "image", "binding": 0}],
                resources=[{"type": "sampler", "binding": 0, "image": self.texture,
                            "min_filter": "linear", "mag_filter": "linear",
                            "wrap_x": "clamp_to_edge", "wrap_y": "clamp_to_edge"}],
                blend=_PREMULTIPLIED if blend else None, framebuffer=self._framebuffer,
                topology="triangle_strip", vertex_count=4, viewport=(0, 0, width, height))
        return pipeline

    def _draw(self, x, y, width, height, blend):
        pipeline = self._pipeline(blend)
        pipeline.viewport = (x, y, width, height)
        pipeline.render()

    def _release_host_resources(self, host_open):
        for resource in [*(self._pipelines or {}).values(), self.texture]:
            if host_open and resource is not None:
                self._ctx.release(resource)
        self._pipelines, self.texture = None, None
