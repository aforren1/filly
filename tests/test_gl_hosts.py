"""Shared textures drawn by plain pyglet, moderngl, and zengl in a pyglet window's context.

Each host reads its own pixels back. The first frames prove orientation and
clear color; the atlas test queues many frames before the only readback, so
a missing fence would show a stale color instead of being hidden by a sync.
"""

import ctypes
import json
import struct
import sys

import numpy as np
import pytest

import filly

pytest.importorskip("pyglet")
from filly.integrations.pyglet import HostTarget, create_renderer  # noqa: E402

pytestmark = [pytest.mark.gpu, pytest.mark.interop,
              pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="WGL/GLX implementation")]

SIZE = 64
CELL = 32
ATLAS = 256
CLEAR = (0, 1, 1, 1)
CYAN = (0, 255, 255, 255)
BACKDROP = (0, 0, 1, 1)
COLORS = [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1)]
SOLID_VERTEX = """
#version 330 core
void main() {
    vec2 corner = vec2(gl_VertexID & 1, gl_VertexID >> 1);
    gl_Position = vec4(corner * 2.0 - 1.0, 0.0, 1.0);
}
"""
SOLID_FRAGMENT = """
#version 330 core
uniform vec4 color;
layout (location = 0) out vec4 result;
void main() {
    result = color;
}
"""


class PygletHost:
    name = "pyglet"

    def __init__(self, window):
        from pyglet import gl
        from filly.integrations import pyglet as adapter
        self.gl, self.adapter, self.window = gl, adapter, window
        self.texture, self.framebuffer = gl.GLuint(), gl.GLuint()
        gl.glGenTextures(1, ctypes.byref(self.texture))
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, ATLAS, ATLAS, 0,
                        gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
        gl.glGenFramebuffers(1, ctypes.byref(self.framebuffer))
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.framebuffer)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                                  gl.GL_TEXTURE_2D, self.texture, 0)
        assert gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER) == gl.GL_FRAMEBUFFER_COMPLETE

    def target(self, renderer):
        return self.adapter.HostTarget(renderer, self.window, width=SIZE, height=SIZE)

    def texture_name(self, target):
        return target.texture

    def screen_target(self, renderer):
        self.gl.glBindFramebuffer(self.gl.GL_FRAMEBUFFER, 0)
        return self.target(renderer)

    def read_screen(self):
        return read_back_buffer(self.gl)

    def clear(self, color):
        gl = self.gl
        gl.glClearColor(*color)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

    def draw_own(self, x, y, size, color):
        gl = self.gl
        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(x, y, size, size)
        self.clear(color)
        gl.glDisable(gl.GL_SCISSOR_TEST)

    def read(self):
        gl = self.gl
        pixels = np.empty((ATLAS, ATLAS, 4), dtype=np.uint8)
        gl.glReadBuffer(gl.GL_COLOR_ATTACHMENT0)
        gl.glReadPixels(0, 0, ATLAS, ATLAS, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE,
                        pixels.ctypes.data_as(ctypes.c_void_p))
        return pixels

    def close(self):
        gl = self.gl
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        gl.glDeleteFramebuffers(1, ctypes.byref(self.framebuffer))
        gl.glDeleteTextures(1, ctypes.byref(self.texture))


class ModernglHost:
    name = "moderngl"

    def __init__(self, window):
        moderngl = pytest.importorskip("moderngl")
        from filly.integrations import moderngl as adapter
        self.moderngl, self.adapter = moderngl, adapter
        self.ctx = moderngl.create_context()
        self.framebuffer = self.ctx.simple_framebuffer((ATLAS, ATLAS), components=4)
        self.framebuffer.use()

    def target(self, renderer):
        return self.adapter.HostTarget(renderer, self.ctx, width=SIZE, height=SIZE)

    def texture_name(self, target):
        return target.texture.glo

    def screen_target(self, renderer):
        self.ctx.screen.use()
        return self.target(renderer)

    def read_screen(self):
        data = self.ctx.screen.read(components=4)
        # moderngl selects GL_COLOR_ATTACHMENT0 as the read buffer of the default framebuffer.
        # Mesa reports GL_INVALID_OPERATION and reads the back buffer; the Windows drivers accept
        # it. Reading ctx.error clears the error, which pyglet would report when the window closes.
        self.ctx.error
        return np.frombuffer(data, dtype=np.uint8).reshape(SIZE, SIZE, 4)

    def clear(self, color):
        self.framebuffer.clear(*color)

    def draw_own(self, x, y, size, color):
        if not hasattr(self, "solid"):
            self.solid = self.ctx.program(vertex_shader=SOLID_VERTEX, fragment_shader=SOLID_FRAGMENT)
            self.solid_vao = self.ctx.vertex_array(self.solid, [])
        self.solid["color"].value = color
        self.ctx.viewport = (x, y, size, size)
        self.solid_vao.render(self.moderngl.TRIANGLE_STRIP, vertices=4)

    def read(self):
        data = self.framebuffer.read(components=4)
        return np.frombuffer(data, dtype=np.uint8).reshape(ATLAS, ATLAS, 4)

    def close(self):
        if hasattr(self, "solid"):
            self.solid_vao.release()
            self.solid.release()
        self.framebuffer.release()
        self.ctx.release()


class ZenglHost:
    name = "zengl"

    def __init__(self, window):
        zengl = pytest.importorskip("zengl")
        from filly.integrations import zengl as adapter
        self.zengl, self.adapter = zengl, adapter
        # zengl.context() is a process-wide singleton bound to whichever context was current.
        zengl.cleanup()
        self.ctx = zengl.context()
        self.image = self.ctx.image((ATLAS, ATLAS), "rgba8unorm")

    def target(self, renderer):
        return self.adapter.HostTarget(renderer, self.ctx, width=SIZE, height=SIZE, framebuffer=[self.image])

    def screen_target(self, renderer):
        return self.adapter.HostTarget(renderer, self.ctx, width=SIZE, height=SIZE)

    def texture_name(self, target):
        return self.zengl.inspect(target.texture)["texture"]

    def read_screen(self):
        # zengl cannot read the default framebuffer, and it leaves its own FBO bound for reading.
        from pyglet import gl
        return read_back_buffer(gl)

    def clear(self, color):
        self.image.clear_value = color
        self.image.clear()

    def draw_own(self, x, y, size, color):
        if not hasattr(self, "solid"):
            self.solid = self.ctx.pipeline(
                vertex_shader=SOLID_VERTEX, fragment_shader=SOLID_FRAGMENT, framebuffer=[self.image],
                uniforms={"color": [0, 0, 0, 0]}, topology="triangle_strip", vertex_count=4)
        self.solid.uniforms["color"][:] = struct.pack("4f", *color)
        self.solid.viewport = (x, y, size, size)
        self.solid.render()

    def read(self):
        return np.frombuffer(self.image.read(), dtype=np.uint8).reshape(ATLAS, ATLAS, 4)

    def close(self):
        if hasattr(self, "solid"):
            self.ctx.release(self.solid)
        self.ctx.release(self.image)
        self.zengl.cleanup()


HOSTS = [PygletHost, ModernglHost, ZenglHost]


def read_back_buffer(gl):
    pixels = np.empty((SIZE, SIZE, 4), dtype=np.uint8)
    gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, 0)
    gl.glReadBuffer(gl.GL_BACK)
    gl.glReadPixels(0, 0, SIZE, SIZE, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, pixels.ctypes.data_as(ctypes.c_void_p))
    return pixels


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


@pytest.fixture(params=HOSTS, ids=[host.name for host in HOSTS])
def host(request, window):
    value = request.param(window)
    try:
        yield value
    finally:
        window.switch_to()
        value.close()


def triangle_scene(renderer, glb, background=CLEAR, transparent=False):
    scene = renderer.create_scene()
    camera = renderer.create_camera()
    camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    scene.background = background
    if transparent:
        scene.transparent = True
    return scene, scene.load(glb)


def translucent(glb, alpha):
    size = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20:20 + size])
    binary = glb[28 + size:]
    document["materials"][0]["alphaMode"] = "BLEND"
    document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [1, 0, 0, alpha]
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def cell(pixels, x, y, size):
    """Sample a drawn copy of the 64-pixel scene; rows count up from the bottom."""
    at = lambda u, v: pixels[y + int(v * size), x + int(u * size)]
    # Triangle apex is at NDC y=0.8, so upright output is red at y=0.6 and clear at y=-0.6.
    return {"center": at(0.5, 0.5), "corner": at(0.05, 0.05), "top": at(0.5, 0.8), "bottom": at(0.5, 0.2)}


def test_library_uses_pyglet_context(host, window):
    assert filly.current_gl_context()
    if sys.platform == "win32":
        assert filly.current_gl_context() == window.context._context


def test_single_frame_orientation_and_clear(host, window, triangle_glb):
    renderer = create_renderer(window)
    try:
        scene, _ = triangle_scene(renderer, triangle_glb)
        target = host.target(renderer)
        host.clear((0.5, 0.5, 0.5, 1))
        renderer.render(scene, target)
        target.draw(viewport=(0, 0, SIZE, SIZE))
        samples = cell(host.read(), 0, 0, SIZE)
        target.close()
    finally:
        renderer.close()
    np.testing.assert_array_equal(samples["center"], (255, 0, 0, 255))
    np.testing.assert_array_equal(samples["corner"], CYAN)
    # No y-flip: Filament and every host use OpenGL's lower-left origin.
    np.testing.assert_array_equal(samples["top"], (255, 0, 0, 255))
    np.testing.assert_array_equal(samples["bottom"], CYAN)


def test_default_framebuffer(host, window, triangle_glb):
    """The examples draw into the window's back buffer rather than an FBO."""
    from pyglet import gl
    renderer = create_renderer(window)
    try:
        scene, _ = triangle_scene(renderer, triangle_glb)
        target = host.screen_target(renderer)
        gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, 0)
        gl.glClearColor(0.5, 0.5, 0.5, 1)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        renderer.render(scene, target)
        target.draw()
        samples = cell(host.read_screen(), 0, 0, SIZE)
        target.close()
    finally:
        renderer.close()
    np.testing.assert_array_equal(samples["center"], (255, 0, 0, 255))
    np.testing.assert_array_equal(samples["corner"], CYAN)
    np.testing.assert_array_equal(samples["top"], (255, 0, 0, 255))
    np.testing.assert_array_equal(samples["bottom"], CYAN)


def test_consecutive_frames_without_finish(host, window, triangle_glb):
    renderer = create_renderer(window)
    frames = (ATLAS // CELL) ** 2
    try:
        scene, model = triangle_scene(renderer, triangle_glb)
        material = model.material("red")
        target = host.target(renderer)
        host.clear((0.5, 0.5, 0.5, 1))
        for frame in range(frames):
            material.base_color = COLORS[frame % 3]
            renderer.render(scene, target)
            target.draw(viewport=((frame % 8) * CELL, (frame // 8) * CELL, CELL, CELL))
        pixels = host.read()
        target.close()
    finally:
        renderer.close()
    for frame in range(frames):
        samples = cell(pixels, (frame % 8) * CELL, (frame // 8) * CELL, CELL)
        np.testing.assert_array_equal(samples["center"], np.array(COLORS[frame % 3]) * 255,
                                      err_msg=f"{host.name} frame {frame}")
        np.testing.assert_array_equal(samples["corner"], CYAN, err_msg=f"{host.name} frame {frame}")


def test_acquire_allows_several_draws_per_frame(host, window, triangle_glb):
    renderer = create_renderer(window)
    try:
        scene, _ = triangle_scene(renderer, triangle_glb)
        target = host.target(renderer)
        host.clear((0.5, 0.5, 0.5, 1))
        renderer.render(scene, target)
        with target.acquire():
            target.draw(viewport=(0, 0, SIZE, SIZE))
            target.draw(viewport=(SIZE, 0, SIZE, SIZE))
        # The released frame stays in the texture and can be drawn again without a render.
        target.draw(viewport=(0, SIZE, SIZE, SIZE))
        pixels = host.read()
        target.close()
        fresh = host.target(renderer)
        with pytest.raises(filly.InteropError, match="Render to the target"):
            fresh.draw()
        fresh.close()
    finally:
        renderer.close()
    for x, y in ((0, 0), (SIZE, 0), (0, SIZE)):
        np.testing.assert_array_equal(cell(pixels, x, y, SIZE)["center"], (255, 0, 0, 255))
        np.testing.assert_array_equal(cell(pixels, x, y, SIZE)["corner"], CYAN)


@pytest.mark.parametrize("blend", [True, False])
def test_premultiplied_alpha(host, window, triangle_glb, blend):
    renderer = create_renderer(window)
    try:
        scene, _ = triangle_scene(renderer, translucent(triangle_glb, 0.5),
                                  background=(0, 0, 0, 0), transparent=True)
        target = host.target(renderer)
        host.clear(BACKDROP)
        renderer.render(scene, target)
        target.draw(viewport=(0, 0, SIZE, SIZE), blend=blend)
        samples = cell(host.read(), 0, 0, SIZE)
        target.close()
    finally:
        renderer.close()
    if blend:
        # Premultiplied source-over: 0.5 red plus half of the blue backdrop.
        np.testing.assert_allclose(samples["center"], (128, 0, 127, 255), atol=2)
        np.testing.assert_array_equal(samples["corner"], (0, 0, 255, 255))
    else:
        # The copy writes Filament's premultiplied RGBA and discards the backdrop.
        np.testing.assert_allclose(samples["center"], (128, 0, 0, 128), atol=1)
        np.testing.assert_array_equal(samples["corner"], (0, 0, 0, 0))


@pytest.mark.parametrize("alpha", [0.25, 0.5, 0.75])
def test_premultiplied_encoding_through_the_adapter(host, window, triangle_glb, alpha):
    """Grey levels at partial alpha, drawn by the adapter with premultiplied blending over black:
    every host shows the encoded straight color times alpha."""
    from test_encoding import srgb
    renderer = create_renderer(window)
    greys = (0.05, 0.2, 0.5, 0.8)
    samples = []
    try:
        scene, model = triangle_scene(renderer, translucent(triangle_glb, alpha),
                                      background=(0, 0, 0, 0), transparent=True)
        target = host.target(renderer)
        for grey in greys:
            model.material("red").base_color = (grey, grey, grey, alpha)
            host.clear((0, 0, 0, 1))
            renderer.render(scene, target)
            target.draw(viewport=(0, 0, SIZE, SIZE))
            samples.append(cell(host.read(), 0, 0, SIZE)["center"])
        target.close()
    finally:
        renderer.close()
    expected = 255 * srgb(greys) * alpha
    actual = np.array(samples, dtype=float)
    assert np.abs(actual[:, :3] - expected[:, None]).max() <= 1, (actual, expected)


def test_host_draws_interleave_with_shared_draws(host, window, triangle_glb):
    """Host-library draws between shared draws must see consistent cached state."""
    renderer = create_renderer(window)
    try:
        scene, model = triangle_scene(renderer, triangle_glb)
        target = host.target(renderer)
        host.clear((0.5, 0.5, 0.5, 1))
        for frame in range(3):
            model.material("red").base_color = COLORS[frame]
            renderer.render(scene, target)
            target.draw(viewport=(0, frame * SIZE, SIZE, SIZE))
            host.draw_own(SIZE, frame * SIZE, SIZE, (1, 1, 0, 1))
        pixels = host.read()
        target.close()
    finally:
        renderer.close()
    for frame in range(3):
        samples = cell(pixels, 0, frame * SIZE, SIZE)
        np.testing.assert_array_equal(samples["center"], np.array(COLORS[frame]) * 255)
        np.testing.assert_array_equal(samples["corner"], CYAN)
        np.testing.assert_array_equal(cell(pixels, SIZE, frame * SIZE, SIZE)["center"], (255, 255, 0, 255))
    from pyglet import gl
    assert gl.glGetError() == gl.GL_NO_ERROR


def test_filament_calls_leave_host_state(window, triangle_glb):
    """Host libraries cache GL state; Filament's host-side calls must not change it."""
    from pyglet import gl

    def snapshot():
        names = ["GL_ACTIVE_TEXTURE", "GL_TEXTURE_BINDING_2D", "GL_CURRENT_PROGRAM", "GL_VIEWPORT",
                 "GL_DRAW_FRAMEBUFFER_BINDING", "GL_READ_FRAMEBUFFER_BINDING", "GL_VERTEX_ARRAY_BINDING",
                 "GL_ARRAY_BUFFER_BINDING", "GL_PIXEL_UNPACK_BUFFER_BINDING", "GL_UNPACK_ALIGNMENT"]
        state = {}
        for name in names:
            value = (gl.GLint * 4)()
            gl.glGetIntegerv(getattr(gl, name), value)
            state[name] = tuple(value)
        for name in ["GL_BLEND", "GL_DEPTH_TEST", "GL_SCISSOR_TEST", "GL_CULL_FACE"]:
            state[name] = gl.glIsEnabled(getattr(gl, name))
        return state

    texture, other = gl.GLuint(), gl.GLuint()
    gl.glGenTextures(1, ctypes.byref(texture))
    gl.glGenTextures(1, ctypes.byref(other))
    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
    gl.glTexStorage2D(gl.GL_TEXTURE_2D, 1, gl.GL_RGBA8, SIZE, SIZE)
    gl.glActiveTexture(gl.GL_TEXTURE3)
    gl.glBindTexture(gl.GL_TEXTURE_2D, other)
    before = snapshot()
    try:
        with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
            scene, _ = triangle_scene(renderer, triangle_glb)
            target = renderer.import_gl_target(texture.value, width=SIZE, height=SIZE)
            assert snapshot() == before
            for _ in range(3):
                renderer.render(scene, target)
                with target.acquire():
                    assert snapshot() == before
            target.close()
            assert snapshot() == before
        assert snapshot() == before
        assert gl.glGetError() == gl.GL_NO_ERROR
    finally:
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glDeleteTextures(1, ctypes.byref(texture))
        gl.glDeleteTextures(1, ctypes.byref(other))


@pytest.mark.parametrize("close_target", [True, False])
def test_close_after_window_close_releases_everything(window, triangle_glb, close_target):
    renderer = create_renderer(window)
    assert renderer.shared_context == filly.current_gl_context()
    scene, _ = triangle_scene(renderer, triangle_glb)
    target = HostTarget(renderer, window, width=SIZE, height=SIZE)
    renderer.render(scene, target)
    target.draw()
    window.close()
    # Without the host context, only Filament's own work can be fenced.
    if close_target:
        target.close()
        assert target.closed and target.texture == 0
    renderer.close()
    assert renderer.closed and renderer.shared_context
    assert target.closed and target.texture == 0
    target.close()


def test_offscreen_renderer_has_no_shared_context():
    with filly.Renderer() as renderer:
        assert renderer.shared_context == 0


@pytest.mark.parametrize("width, height", [
    (64.5, 48), (64, np.float64(47.25)), (float("nan"), 48), (float("inf"), 48), (8193.0, 48),
    (np.int64(0), 48), (-1, 48), ("64", 48), (True, 48), (64, np.str_("48")),
])
def test_size_errors_match_native(window, width, height):
    """The adapter checks sizes before it creates the host texture; the messages must not diverge."""
    with create_renderer(window) as renderer:
        with pytest.raises((TypeError, ValueError)) as native:
            renderer.create_render_target(width=width, height=height)
        with pytest.raises(native.type) as adapter:
            HostTarget(renderer, window, width=width, height=height)
    assert str(adapter.value) == str(native.value)


def test_integral_sizes(window):
    with create_renderer(window) as renderer:
        for width, height in ((np.int64(64), np.int32(48)), (64.0, np.float32(48)), (np.uint16(64), 48.0)):
            with HostTarget(renderer, window, width=width, height=height) as target:
                assert (target.width, target.height) == (64, 48)


@pytest.mark.skipif(sys.platform != "win32", reason="Raw opengl32 call")
def test_import_ignores_stale_host_error(window):
    from pyglet import gl
    texture = gl.GLuint()
    gl.glGenTextures(1, ctypes.byref(texture))
    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
    gl.glTexStorage2D(gl.GL_TEXTURE_2D, 1, gl.GL_RGBA8, SIZE, SIZE)
    try:
        with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
            # pyglet's wrapper would consume the error; a host that never checks errors leaves it pending.
            ctypes.WinDLL("opengl32").glEnable(0xFFFF)
            renderer.import_gl_target(texture.value, width=SIZE, height=SIZE).close()
    finally:
        gl.glDeleteTextures(1, ctypes.byref(texture))


@pytest.mark.parametrize("adapter", ["pyglet", "moderngl"])
def test_garbage_collection_stays_in_the_current_context(window, triangle_glb, adapter):
    """An unclosed target collected while another window is current must not touch either context."""
    import gc
    import pyglet
    from pyglet import gl
    renderer = create_renderer(window)
    other = pyglet.window.Window(width=SIZE, height=SIZE, visible=False)
    window.switch_to()
    try:
        scene, _ = triangle_scene(renderer, triangle_glb)
        if adapter == "pyglet":
            target = HostTarget(renderer, window, width=SIZE, height=SIZE)
        else:
            moderngl = pytest.importorskip("moderngl")
            from filly.integrations import moderngl as module
            target = module.HostTarget(renderer, moderngl.create_context(), width=SIZE, height=SIZE)
        renderer.render(scene, target)
        target.draw()
        name = target.texture if adapter == "pyglet" else target._name
        other.switch_to()
        current = filly.current_gl_context()
        with pytest.warns(ResourceWarning, match="not closed"):
            del target
            gc.collect()
        # No context switch, and no delete: the host texture remains until its window closes.
        assert filly.current_gl_context() == current
        window.switch_to()
        assert gl.glIsTexture(name)
        gl.glDeleteTextures(1, ctypes.byref(gl.GLuint(name)))
    finally:
        other.close()
        window.switch_to()
        renderer.close()


def test_stereo_viewports_in_a_shared_target(host, window):
    """Two render() calls with viewports fill the halves of one imported texture."""
    renderer = create_renderer(window)
    try:
        scene = renderer.create_scene()
        scene.background = CLEAR
        scene.create_mesh(**filly.shapes.plane(0.5, 0.5), unlit=True, alpha_mode="blend", base_color=(1, 0, 0, 1))
        eyes = []
        for x in (-0.25, 0.25):
            camera = renderer.create_camera()
            camera.set_orthographic(height=2, near=0.1, far=10)
            camera.position = (x, 0, 3)
            camera.look_at((x, 0, 0))
            eyes.append(camera)
        target = host.target(renderer)
        host.clear((0.5, 0.5, 0.5, 1))
        half = SIZE // 2
        renderer.render(scene, target, camera=eyes[0], viewport=(0, 0, half, SIZE))
        renderer.render(scene, target, camera=eyes[1], viewport=(half, 0, half, SIZE), clear=False)
        target.draw(viewport=(0, 0, SIZE, SIZE))
        row = host.read()[SIZE // 2, :SIZE]
        target.close()
    finally:
        renderer.close()
    # Each eye maps 1 unit to 32 pixels; the square sits right of the left eye's center and
    # left of the right eye's center, so the halves meet in one red band.
    red = np.flatnonzero(np.all(row == (255, 0, 0, 255), axis=1))
    np.testing.assert_array_equal(red, np.arange(16, 48))
    np.testing.assert_array_equal(row[[0, 15, 48, 63]], [CYAN] * 4)


def test_renderer_close_releases_host_targets(host, window, triangle_glb):
    """renderer.close() alone closes the host targets and deletes their host objects."""
    from pyglet import gl
    renderer = create_renderer(window)
    scene, _ = triangle_scene(renderer, triangle_glb)
    target = host.target(renderer)
    other = host.target(renderer)
    renderer.render(scene, target)
    target.draw()
    names = [host.texture_name(target), host.texture_name(other)]
    assert all(gl.glIsTexture(name) for name in names)
    renderer.close()
    window.switch_to()
    assert target.closed and other.closed
    assert not any(gl.glIsTexture(name) for name in names)
    assert target.texture in (0, None) and other.texture in (0, None)
    with pytest.raises(filly.InteropError, match="closed"):
        target.draw()
    target.close()
    import gc
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        del target, other
        gc.collect()


def test_renderer_close_finishes_after_a_target_error(window, triangle_glb, monkeypatch):
    """An exception from one target's close reaches the caller after everything else closed."""
    from pyglet import gl
    renderer = create_renderer(window)
    scene, _ = triangle_scene(renderer, triangle_glb)
    failing = HostTarget(renderer, window, width=SIZE, height=SIZE)
    other = HostTarget(renderer, window, width=SIZE, height=SIZE)
    name = other.texture

    def fail():
        raise RuntimeError("target close failed")

    monkeypatch.setattr(failing, "close", fail)
    with pytest.raises(RuntimeError, match="target close failed"):
        renderer.close()
    assert renderer.closed and scene.closed
    assert other.closed and not gl.glIsTexture(name)
    # The failing target's own close never ran; the renderer released its Filament side.
    monkeypatch.undo()
    assert failing.closed
    failing.close()


def test_default_size_is_the_framebuffer(window):
    with create_renderer(window) as renderer:
        with HostTarget(renderer, window) as target:
            assert (target.width, target.height) == tuple(window.get_framebuffer_size())
        with HostTarget(renderer, window, height=16) as target:
            assert (target.width, target.height) == (window.get_framebuffer_size()[0], 16)


@pytest.mark.parametrize("viewport, error", [
    ((0, 0, 0, 8), ValueError), ((0, 0, 8), ValueError), ((0.5, 0, 8, 8), ValueError), (("0", 0, 8, 8), TypeError),
])
def test_draw_viewport_errors(window, triangle_glb, viewport, error):
    with create_renderer(window) as renderer:
        scene, _ = triangle_scene(renderer, triangle_glb)
        with HostTarget(renderer, window, width=SIZE, height=SIZE) as target:
            renderer.render(scene, target)
            with pytest.raises(error):
                target.draw(viewport=viewport)


@pytest.mark.parametrize("adapter", ["pyglet", "moderngl"])
@pytest.mark.parametrize("blend", [True, False])
def test_draw_restores_host_state(window, triangle_glb, adapter, blend):
    """Hosts cache GL state, so draw() must leave the state that it changes as it found it."""
    from pyglet import gl
    names = ["GL_ACTIVE_TEXTURE", "GL_CURRENT_PROGRAM", "GL_VIEWPORT", "GL_VERTEX_ARRAY_BINDING",
             "GL_BLEND_SRC_RGB", "GL_BLEND_DST_RGB", "GL_BLEND_SRC_ALPHA", "GL_BLEND_DST_ALPHA",
             "GL_BLEND_EQUATION_RGB", "GL_BLEND_EQUATION_ALPHA"]

    def integers(name):
        value = (gl.GLint * 4)()
        gl.glGetIntegerv(getattr(gl, name), value)
        return tuple(value)

    def snapshot():
        state = {name: integers(name) for name in names}
        # The draw samples on unit 0, whatever unit is active.
        gl.glActiveTexture(gl.GL_TEXTURE0)
        state["unit 0"] = integers("GL_TEXTURE_BINDING_2D"), integers("GL_SAMPLER_BINDING")
        gl.glActiveTexture(state["GL_ACTIVE_TEXTURE"][0])
        for name in ["GL_BLEND", "GL_DEPTH_TEST", "GL_SCISSOR_TEST", "GL_CULL_FACE", "GL_STENCIL_TEST"]:
            state[name] = gl.glIsEnabled(getattr(gl, name))
        return state

    with create_renderer(window) as renderer:
        scene, _ = triangle_scene(renderer, triangle_glb)
        if adapter == "pyglet":
            target = HostTarget(renderer, window, width=SIZE, height=SIZE)
        else:
            moderngl = pytest.importorskip("moderngl")
            from filly.integrations import moderngl as module
            ctx = moderngl.create_context()
            with module.create_renderer() as second:
                assert second.shared_context == renderer.shared_context
            target = module.HostTarget(renderer, ctx, width=SIZE, height=SIZE)
        with target:
            renderer.render(scene, target)
            sampler, other = gl.GLuint(), gl.GLuint()
            gl.glGenSamplers(1, ctypes.byref(sampler))
            gl.glGenTextures(1, ctypes.byref(other))
            try:
                # A host state unlike the draw's: blending for straight alpha, depth testing,
                # culling, another texture unit and texture, a sampler, and a small viewport.
                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFuncSeparate(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA, gl.GL_ZERO, gl.GL_ONE)
                gl.glBlendEquationSeparate(gl.GL_FUNC_SUBTRACT, gl.GL_MAX)
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glActiveTexture(gl.GL_TEXTURE0)
                gl.glBindSampler(0, sampler)
                gl.glBindTexture(gl.GL_TEXTURE_2D, other)
                gl.glActiveTexture(gl.GL_TEXTURE2)
                gl.glViewport(1, 2, 3, 4)
                before = snapshot()
                target.draw(blend=blend)
                assert snapshot() == before
                assert gl.glGetError() == gl.GL_NO_ERROR
            finally:
                gl.glActiveTexture(gl.GL_TEXTURE0)
                gl.glBindSampler(0, 0)
                gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                gl.glDeleteSamplers(1, ctypes.byref(sampler))
                gl.glDeleteTextures(1, ctypes.byref(other))
                gl.glBlendFuncSeparate(gl.GL_ONE, gl.GL_ZERO, gl.GL_ONE, gl.GL_ZERO)
                gl.glBlendEquationSeparate(gl.GL_FUNC_ADD, gl.GL_FUNC_ADD)
                for flag in (gl.GL_BLEND, gl.GL_DEPTH_TEST, gl.GL_CULL_FACE):
                    gl.glDisable(flag)
                gl.glViewport(0, 0, SIZE, SIZE)


@pytest.mark.parametrize("host_type", [PygletHost, ModernglHost], ids=["pyglet", "moderngl"])
def test_draw_ignores_culling(window, triangle_glb, host_type):
    """draw() composites onto the color buffer: the host's face culling does not apply."""
    from pyglet import gl
    host = host_type(window)
    renderer = create_renderer(window)
    try:
        scene, _ = triangle_scene(renderer, triangle_glb)
        target = host.target(renderer)
        host.clear((0.5, 0.5, 0.5, 1))
        renderer.render(scene, target)
        gl.glEnable(gl.GL_CULL_FACE)
        gl.glCullFace(gl.GL_FRONT_AND_BACK)
        try:
            target.draw(viewport=(0, 0, SIZE, SIZE))
        finally:
            gl.glDisable(gl.GL_CULL_FACE)
            gl.glCullFace(gl.GL_BACK)
        samples = cell(host.read(), 0, 0, SIZE)
        target.close()
    finally:
        renderer.close()
        window.switch_to()
        host.close()
    np.testing.assert_array_equal(samples["center"], (255, 0, 0, 255))
    np.testing.assert_array_equal(samples["corner"], CYAN)


def test_imported_target_supports_with(window):
    from pyglet import gl
    texture = filly._native._create_host_texture(SIZE, SIZE)
    try:
        with create_renderer(window) as renderer:
            with renderer.import_gl_target(texture, width=SIZE, height=SIZE) as target:
                assert isinstance(target, filly.ImportedTarget) and not target.closed
            assert target.closed
            with pytest.raises(filly.FillyError, match="closed"):
                with target:
                    pass
            with pytest.raises(TypeError, match="callable"):
                renderer.import_gl_target(texture, width=SIZE, height=SIZE, on_renderer_close=1)
            calls = []
            # A target lives while its handle does, so keep this one.
            kept = renderer.import_gl_target(texture, width=SIZE, height=SIZE,
                                              on_renderer_close=lambda: calls.append(1))
            # A target that closed first drops its callback.
            renderer.import_gl_target(texture, width=SIZE, height=SIZE,
                                       on_renderer_close=lambda: calls.append(2)).close()
        assert calls == [1] and kept.closed
        assert gl.glIsTexture(texture)
    finally:
        filly._native._delete_host_texture(texture)
