"""Spin and animate the wooden horse in a plain pyglet window through a shared OpenGL texture.

pyglet's event loop calls on_draw and swaps buffers. The moderngl and zengl
examples reuse run() and change only the host draw.
"""

import argparse
import ctypes
import json
from time import perf_counter

import numpy as np
import pyglet
from pyglet import gl

import filly
from screenshot import animate, setup_scene


class GpuTimer:
    """GL_TIME_ELAPSED queries, read one frame late so they do not stall the pipeline."""

    def __init__(self):
        self.queries = (gl.GLuint * 2)()
        gl.glGenQueries(2, self.queries)
        self.frame = 0

    def begin(self):
        gl.glBeginQuery(gl.GL_TIME_ELAPSED, self.queries[self.frame % 2])

    def end(self):
        gl.glEndQuery(gl.GL_TIME_ELAPSED)
        self.frame += 1
        if self.frame < 2:
            return np.nan
        elapsed = gl.GLuint64()
        gl.glGetQueryObjectui64v(self.queries[self.frame % 2], gl.GL_QUERY_RESULT, ctypes.byref(elapsed))
        return elapsed.value / 1e6

    def close(self):
        gl.glDeleteQueries(2, self.queries)


def parse_args(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--spin", type=float, default=30, help="Degrees per second")
    parser.add_argument("--no-vsync", action="store_true", help="Do not wait for vertical blank on swap")
    parser.add_argument("--warmup", type=int, default=30, help="Frames excluded from the timing summary")
    args = parser.parse_args()
    if args.frames <= args.warmup or args.warmup < 0:
        parser.error("--frames must be greater than --warmup, and --warmup must be nonnegative")
    return args


def open_window(args, caption):
    window = pyglet.window.Window(args.width, args.height, caption=caption, vsync=not args.no_vsync)
    window.switch_to()
    return window


def run(window, renderer, target, draw, args):
    """Render and draw args.frames frames, then close the target and the renderer.

    ``draw()`` is the host's per-frame drawing. It must call ``target.draw()``,
    which runs inside ``target.acquire()`` here.
    The caller closes the window after this returns.
    """
    width, height = target.width, target.height
    scene, model = setup_scene(renderer, width / height, spin=True)
    timer = GpuTimer()
    timings = []
    start = perf_counter()

    def on_draw():
        animate(model, perf_counter() - start, args.spin)
        renderer.render(scene, target)
        # Acquire outside the timed region so the timers exclude the wait for Filament's frame.
        with target.acquire():
            began = perf_counter()
            timer.begin()
            draw()
            gpu = timer.end()
            ended = perf_counter()
        stats = renderer.stats
        timings.append(((ended - began) * 1000, stats.host_wait_ms, stats.host_release_ms,
                        gpu, stats.cpu_submit_ms))
        if len(timings) >= args.frames:
            pyglet.app.exit()

    def on_close():
        # Keep the window open; the target and the renderer need its context to close.
        pyglet.app.exit()
        return pyglet.event.EVENT_HANDLED

    window.push_handlers(on_draw=on_draw, on_close=on_close)
    # A scheduled callback makes pyglet 1.4 redraw every loop iteration; vsync paces it.
    pyglet.clock.schedule(lambda dt: None)
    try:
        pyglet.app.run()
    finally:
        window.switch_to()
        timer.close()
        target.close()
        renderer.close()
    measured = np.array(timings[args.warmup:])
    names = ("host_draw_cpu_ms", "host_wait_ms", "host_release_ms", "host_draw_gpu_ms", "cpu_submit_ms")
    summary = {"frames": len(timings), "warmup": args.warmup, "size": [width, height]}
    if len(measured):
        summary.update({name: dict(zip(("p50", "p95"), np.round(np.nanpercentile(measured[:, i], [50, 95]), 3)))
                        for i, name in enumerate(names)})
    print(json.dumps(summary, indent=2))


def main():
    from filly.integrations.pyglet import SharedTarget, create_renderer

    args = parse_args(__doc__.splitlines()[0])
    filly.set_log_level("warning")
    window = open_window(args, "filly + pyglet")
    renderer = create_renderer(window)
    width, height = window.get_framebuffer_size()
    target = SharedTarget(renderer, window, width, height)
    # The blit replaces every pixel, so the window needs no clear.
    run(window, renderer, target, target.draw, args)
    window.close()


if __name__ == "__main__":
    main()
