# Use pyglet, moderngl, or zengl with shared textures

Filament can render into an OpenGL texture that your application owns. Filament shares the
window's OpenGL context, so frames do not go through CPU memory. This guide shows three thin
host integrations that use a pyglet window. Each integration has a `SharedTarget` class with the
same API:

- `filly.integrations.pyglet.create_renderer(window)` makes the window's context current and
  creates the renderer. Use it for all three hosts.
- `SharedTarget(renderer, host, width, height)` creates the host texture and imports it. `host` is
  the pyglet window, a moderngl context, or a zengl context.
- `target.draw(x=0, y=0, width=None, height=None)` acquires the texture, draws it into the bound
  framebuffer, and releases it. `x` and `y` are window pixels from the lower-left corner.
- `with target.acquire():` holds the texture for more than one draw in a frame.
- `target.close()` releases the Filament target and the host texture.

The host texture is plain `GL_RGBA8` with immutable storage, so both
[output paths](../reference/api.md#output-path) work, and it holds sRGB-encoded values
(see [output encoding](../reference/api.md#output-encoding)). Sample it into a non-sRGB
framebuffer, the default for these hosts, and the window shows the encoded values unchanged. To
build an adapter for another host, see [`ImportedTarget`](../reference/api.md#importedtarget).

Close each target before its window. A target that is garbage-collected instead emits
`ResourceWarning`, and its host objects stay until the window closes.

See [Host integrations](../reference/api.md#host-integrations) for sizes and errors.

The integrations were tested on Windows 11 with an Intel Iris Xe GPU and pyglet 1.4.11,
moderngl 5.12.0, and zengl 2.7.3. Linux GLX and pyglet 2 were not tested.

## Install

Build filly as described in [Build and test](build.md). Then install the host library in
the same environment:

```powershell
uv pip install --python .venv\Scripts\python.exe moderngl zengl pyglet==1.4.11
```

The package also has the extras `pyglet`, `moderngl`, and `zengl`. Each extra includes pyglet.

## Plain pyglet

The integration blits the texture into the bound draw framebuffer.

```python
import pyglet
from filly.integrations.pyglet import SharedTarget, create_renderer

window = pyglet.window.Window(960, 640)
renderer = create_renderer(window)
scene = renderer.create_scene()
# Add a camera, lights, and models to the scene here.
target = SharedTarget(renderer, window, *window.get_framebuffer_size())

@window.event
def on_draw():
    renderer.render(scene, target)
    target.draw()

pyglet.clock.schedule(lambda dt: None)  # pyglet 1.4 redraws only when a callback runs.
pyglet.app.run()
target.close()
renderer.close()
window.close()
```

A blit has these limits:

- It does not blend. It writes Filament's premultiplied RGBA directly.
- It obeys the scissor test.
- It cannot write to a multisampled framebuffer. For a window with `sample_buffers=1`, draw a
  textured quad instead.

## moderngl

`moderngl.create_context()` attaches to the context that is current. It does not create a
context, and it cannot make the pyglet context current. Call it after `create_renderer(window)`.

```python
import moderngl
from filly.integrations.moderngl import SharedTarget
from filly.integrations.pyglet import create_renderer

renderer = create_renderer(window)
ctx = moderngl.create_context()
target = SharedTarget(renderer, ctx, *window.get_framebuffer_size())

@window.event
def on_draw():
    renderer.render(scene, target)
    ctx.clear(0.02, 0.03, 0.05)
    target.draw()

# After the event loop:
target.close()
renderer.close()
ctx.release()
window.close()
```

`ctx.texture()` allocates mutable storage, which the import rejects. The integration therefore
creates the texture with `glTexStorage2D` and wraps it with `ctx.external_texture()`. It deletes
the texture itself on `close()`, because moderngl does not delete external textures. The
integration draws a quad with a small shader. It sets the viewport and restores it. It does not
change the blend state.

## zengl

`zengl.context()` loads OpenGL functions from the context that is current. On Windows it uses
`wglGetProcAddress`. On Linux it tries EGL first and then GLX. It needs no explicit loader. The
zengl context is one object for each process. Call `zengl.cleanup()` before you close the window.

```python
import zengl
from filly.integrations.pyglet import create_renderer
from filly.integrations.zengl import SharedTarget

renderer = create_renderer(window)
ctx = zengl.context()
target = SharedTarget(renderer, ctx, *window.get_framebuffer_size())

@window.event
def on_draw():
    renderer.render(scene, target)
    ctx.new_frame()
    target.draw()
    ctx.end_frame()

# After the event loop:
target.close()
renderer.close()
zengl.cleanup()
window.close()
```

`ctx.image()` allocates mutable storage, which the import rejects. The integration therefore
creates the texture with `glTexStorage2D` and wraps it with `ctx.image(..., external=name)`.
zengl deletes the texture when the integration releases the image.

A zengl pipeline fixes its framebuffer when you create it. To draw into a zengl image instead of
the window, pass `framebuffer=[image]` to `SharedTarget`.

zengl keeps an internal framebuffer bound to `GL_READ_FRAMEBUFFER` after it creates or reads an
image. Before a raw `glReadPixels` from the window, bind framebuffer 0 for reading.

## Draw more than once in a frame

`target.draw()` acquires and releases the texture itself. A later draw without a new render
shows the same frame again. To draw one frame into several places with one fence wait, use
`acquire()`. Inside `acquire()`, a draw does not acquire or release:

```python
renderer.render(scene, target)
with target.acquire():
    target.draw(0, 0, 480, 640)
    target.draw(480, 0, 480, 640)
```

## Show a transparent view

With `scene.transparent = True`, Filament writes premultiplied RGB after the output encoding. A
red surface with alpha 0.5 is stored as (128, 0, 0, 128). Use source-over blending for premultiplied
color: `GL_ONE, GL_ONE_MINUS_SRC_ALPHA`.

- pyglet: the blit cannot blend. Use it for opaque views only.
- moderngl: enable blending before `target.draw()`:

  ```python
  ctx.enable(moderngl.BLEND)
  ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
  ```

  Do not use `moderngl.PREMULTIPLIED_ALPHA`. Its value is `(SRC_ALPHA, ONE)`, which is additive.
- zengl: the integration's pipeline always uses premultiplied source-over blending. For an opaque
  view, the result is the same as no blending.

## Close in the correct order

Close the target, then the renderer, then the window. Keep the window open until the event loop
stops. In an `on_close` handler, call `pyglet.app.exit()` and do not close the window there.

If you close the renderer before the target, the target close does nothing. This is safe.

moderngl and zengl cannot make the window's context current. If another context is current,
`acquire()`, `draw()`, and `close()` treat the window as closed. Call `window.switch_to()` first.

If you close the window first, `target.close()` and `renderer.close()` still succeed and release
all Filament resources. The window's context is gone, so:

- The adapter makes no host OpenGL calls. The host texture goes away with the window's context.
- Filament waits for its own GPU work only. The host's last GPU work is not fenced. This does
  not matter once the window is closed, because that work can no longer use the texture.

`renderer.shared_context` keeps the host context handle after the close.

On Windows, pyglet 1.4.11 can crash when a program closes a window and then opens another.
It does not unregister a closed window's child view class, and a new window can reuse that
class and its freed callback. This does not depend on filly. If your program opens windows one after another,
keep a reference to each closed window object until the program exits, so that Python cannot
reuse its address. See [validation](../reference/validation.md) for the details and the test-suite
workaround.

## Orientation

No host needs a vertical flip. Filament, the texture, and each host use the OpenGL lower-left
origin. Texture row 0 is the bottom row of the image.

## Compare the hosts

Data comes from `examples/*_shared.py` at 960 x 640, 600 frames, 60 warm-up frames, without
vsync. Values are medians (p50). "Draw" is the time for the host draw calls only. It does not
include entering or leaving `acquire()`. The GPU time comes from `GL_TIME_ELAPSED`
queries.

| Item | pyglet | moderngl | zengl |
| --- | --- | --- | --- |
| Adapter code lines | 103 | 66 | 72 |
| Draw method | `glBlitFramebuffer` | quad with a shader | zengl pipeline |
| Vertical flip | No | No | No |
| Premultiplied alpha | Cannot blend; opaque views only | Caller sets `ONE, ONE_MINUS_SRC_ALPHA` | Built into the pipeline |
| State reset after Filament calls | None | None | None; call `new_frame()` if pyglet or raw GL also draws |
| Host draw CPU time | 0.14 ms | 0.12 to 0.15 ms (with a clear) | 0.15 to 0.21 ms (with `new_frame()` and `end_frame()`) |
| Host draw GPU time | 0.11 ms | 0.09 to 0.12 ms (with a clear) | 0.11 to 0.15 ms |
| `acquire()` enter CPU time (`stats.host_wait_ms`) | 0.54 to 0.64 ms | 0.50 to 0.62 ms | 0.50 to 0.88 ms |
| `acquire()` exit CPU time (`stats.host_release_ms`) | 0.04 to 0.05 ms | 0.04 ms | 0.02 to 0.03 ms |

Ranges show two runs, measured before `acquire()` moved into the core `ImportedTarget`; the
fence operations are the same. The line counts exclude `integrations/_host.py` (141 lines),
which holds the size check, `draw()`, `close()`, and the context-manager methods for all
adapters, including PsychoPy. The core `ImportedTarget.acquire()` counts nesting, so the adapters
keep no state of their own. Entering the outermost `acquire()` blocks the CPU until Filament's
driver thread has processed the frame and published its fence. It does not wait for the GPU to
finish.

Filament's host-side calls do not change the host's OpenGL state. A test compares the texture,
framebuffer, program, vertex array, buffer, viewport, pixel-store, and enable state before and
after each call. Thus moderngl needs no `ctx.clear_errors()` or `ctx.gc()`, and zengl needs no
reset because of Filament.

## Run the examples

Each example spins Suzanne. pyglet's event loop swaps the buffers. The example prints timing
percentiles as JSON.

```powershell
.venv\Scripts\python.exe examples\pyglet_shared.py --frames 120
.venv\Scripts\python.exe examples\moderngl_shared.py --frames 120
.venv\Scripts\python.exe examples\zengl_shared.py --frames 120 --no-vsync
```

With vsync, a GPU timer that also included leaving `acquire()` measured about 15 ms. Without
vsync, it measured 0.13 ms. The cause was not isolated. The examples now stop the timer before
the release.
