"""Show that filly and the host window draw the same frame.

filly renders the black area inside the blue outline, and pyglet draws the gray area around it. A
red bar sweeps across the boundary: pyglet draws its ends and filly draws its middle, in front of
the horse. The bar is straight only when both sides show the same frame.

Press L to delay the host by one frame and see what a lag looks like: the middle of the bar no
longer lines up with its ends. Press space to pause, which holds a lag on the screen.
"""

import argparse
from time import perf_counter

import numpy as np
import pyglet
from pyglet import gl

import filly
from screenshot import HORSE, animate, studio_environment

# A primary color encodes to the same levels on both sides, so the bar has one color across the boundary.
BAR_COLOR = (1, 0, 0)
OUTLINE_COLOR = (0, 0, 1)
HOST_BACKGROUND = (0.5, 0.5, 0.5)
BAR_WIDTH = 20
OUTLINE_WIDTH = 4
# The inset leaves this fraction of the window on each side, so the bar has host pixels at both ends.
MARGIN = 0.2


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--bar-speed", type=float, default=600, help="Pixels per second")
    parser.add_argument("--spin", type=float, default=30, help="Degrees per second")
    args = parser.parse_args()
    if args.bar_speed <= 0:
        parser.error("--bar-speed must be positive")
    return args


def setup_scene(renderer, width, height):
    """The horse and the middle of the bar. Return the scene, the horse, and a function that moves the bar."""
    scene = renderer.create_scene()
    scene.background = (0, 0, 0, 1)
    horse = scene.load(HORSE)
    low, high = np.asarray(horse.bounds, dtype=float)
    center = (low + high) / 2
    radius = np.linalg.norm(high - low) / 2
    # The sphere around the rest-pose box is loose: the horse stays in view at 0.8 of its radius.
    half_height = 0.8 * radius
    half_width = half_height * width / height
    camera = renderer.create_camera()
    camera.position = center + (0, 0, 2 * radius)
    camera.look_at(center)
    camera.set_orthographic(left=-half_width, right=half_width, bottom=-half_height, top=half_height,
                            near=0.1 * radius, far=4 * radius)
    scene.camera = camera
    scene.add_directional_light(direction=(-1, -1.5, -1), intensity=110000, color=(1, 0.92, 0.82))
    scene.environment = renderer.create_environment(studio_environment())
    scene.environment_intensity = 20000

    # The orthographic camera has a fixed size in world units per pixel, so the bar's edges land on
    # the same pixel columns as the host's.
    pixel = 2 * half_height / height
    bar = scene.create_mesh(**filly.shapes.box(), base_color=(*BAR_COLOR, 1), unlit=True)
    bar.scale = (BAR_WIDTH * pixel, 2 * half_height, 0.01 * radius)

    def place_bar(left):
        # Outside the horse's bounding sphere, so that the horse never covers the bar.
        bar.position = (center[0] - half_width + (left + BAR_WIDTH / 2) * pixel, center[1], center[2] + 1.5 * radius)

    return scene, horse, place_bar


def fill(x, y, width, height, color):
    gl.glScissor(x, y, width, height)
    gl.glClearColor(*color, 1)
    gl.glClear(gl.GL_COLOR_BUFFER_BIT)


def main():
    from filly.integrations.pyglet import HostTarget, create_renderer

    args = parse_args()
    filly.set_log_level("warning")
    window = pyglet.window.Window(args.width, args.height, caption="filly sync")
    window.switch_to()
    renderer = create_renderer(window)
    columns, rows = window.get_framebuffer_size()
    x, y = int(MARGIN * columns), int(MARGIN * rows)
    width, height = columns - 2 * x, rows - 2 * y
    target = HostTarget(renderer, window, width=width, height=height)
    scene, horse, place_bar = setup_scene(renderer, width, height)

    def bar_left(seconds):
        # Relative to the inset. The bar stays inside it, so that both areas always show the bar.
        return int(seconds * args.bar_speed) % (width - BAR_WIDTH)

    # Each side derives all of its drawing from one time, so a lag is one earlier time for the host.
    state = {"seconds": 0.0, "previous": 0.0, "last": perf_counter(), "paused": False, "lag": False}

    def show_controls():
        status = "host one frame late" if state["lag"] else "in sync"
        window.set_caption(f"filly sync: {status} (L: lag, space: pause)")

    def on_draw():
        now = perf_counter()
        if not state["paused"]:
            state["previous"] = state["seconds"]
            state["seconds"] += now - state["last"]
        state["last"] = now

        seconds = state["seconds"]
        animate(horse, seconds, args.spin)
        place_bar(bar_left(seconds))
        renderer.render(scene, target)

        host_seconds = state["previous"] if state["lag"] else seconds
        gl.glEnable(gl.GL_SCISSOR_TEST)
        fill(0, 0, columns, rows, HOST_BACKGROUND)
        fill(x - OUTLINE_WIDTH, y - OUTLINE_WIDTH, width + 2 * OUTLINE_WIDTH, height + 2 * OUTLINE_WIDTH, OUTLINE_COLOR)
        fill(x + bar_left(host_seconds), 0, BAR_WIDTH, rows, BAR_COLOR)
        gl.glDisable(gl.GL_SCISSOR_TEST)
        target.draw(viewport=(x, y, width, height), blend=False)

    def on_key_press(symbol, modifiers):
        if symbol == pyglet.window.key.L:
            state["lag"] = not state["lag"]
            show_controls()
        elif symbol == pyglet.window.key.SPACE:
            state["paused"] = not state["paused"]
        elif symbol == pyglet.window.key.ESCAPE:
            pyglet.app.exit()
            return pyglet.event.EVENT_HANDLED

    def on_close():
        # Keep the window open; the renderer needs its context to close.
        pyglet.app.exit()
        return pyglet.event.EVENT_HANDLED

    show_controls()
    window.push_handlers(on_draw=on_draw, on_key_press=on_key_press, on_close=on_close)
    # A scheduled callback makes pyglet 1.4 redraw every loop iteration; vsync paces it.
    pyglet.clock.schedule(lambda dt: None)
    try:
        pyglet.app.run()
    finally:
        window.switch_to()
        # Closes the target too.
        renderer.close()
    window.close()


if __name__ == "__main__":
    main()
