"""Click a model in a pyglet window: filly picks the surface under the pointer.

The window shows the wooden horse and three shapes. A click queues a pick; the answer arrives
one or more frames later. The demo then tints the hit node, marks the hit point with a small
sphere, and prints the model, node, world position, depth, and latency. Press S to start or stop
the spin: a pick describes the frame that took it, not the frame that shows its answer.

--auto clicks at scripted points and exits, for a run without a person.
"""

import argparse
import time

import numpy as np
import pyglet

import filly
from screenshot import HORSE, VIEW, studio_environment, view_direction

TINT = (1.0, 0.35, 0.3, 1.0)


def world_box(model):
    """The world-space box around a model: Model.bounds is in the model's own frame."""
    low, high = np.asarray(model.bounds, dtype=float)
    corners = np.array([[(low, high)[i >> k & 1][k] for k in range(3)] for i in range(8)])
    world = corners @ model.transform[:3, :3].T + model.transform[:3, 3]
    return world.min(axis=0), world.max(axis=0)


def setup_scene(renderer):
    scene = renderer.create_scene()
    scene.background = (0.025, 0.035, 0.055, 1)
    scene.antialiasing = "fxaa"
    horse = scene.load(HORSE)
    low, high = np.asarray(horse.bounds, dtype=float)
    height = high[1] - low[1]
    # Unit shapes, scaled to the horse, in a row on the floor beside it.
    side = 0.22 * height
    shapes = {}
    for i, (name, shape, color) in enumerate([("box", filly.shapes.box(), (0.2, 0.5, 0.9, 1)),
                                              ("sphere", filly.shapes.uv_sphere(), (0.3, 0.8, 0.4, 1)),
                                              ("cylinder", filly.shapes.cylinder(), (0.9, 0.7, 0.2, 1))]):
        model = scene.create_mesh(**shape, base_color=color, roughness=0.5)
        model.scale = (side, side, side)
        model.position = (high[0] + 0.4 * height, low[1] + side / 2, low[2] + (i + 0.5) * (high[2] - low[2]) / 3)
        shapes[name] = model
    marker = scene.create_mesh(**filly.shapes.uv_sphere(0.015 * height), base_color=(1, 1, 1, 1), unlit=True)
    marker.visible = False
    boxes = np.array([world_box(model) for model in [horse, *shapes.values()]])
    camera = renderer.create_camera()
    camera.frame((boxes[:, 0].min(axis=0), boxes[:, 1].max(axis=0)), fill=0.9, direction=view_direction(*VIEW),
                 fit="sphere")
    scene.camera = camera
    scene.add_directional_light(direction=(-1, -1.5, -1), intensity=110000, color=(1, 0.92, 0.82))
    scene.environment = renderer.create_environment(studio_environment())
    scene.environment_intensity = 20000
    return scene, horse, shapes, marker


class Highlight:
    """Tint slot 0 of one node; restore the previous color when another node is tinted."""

    def __init__(self):
        self.material = self.color = None

    def set(self, node):
        self.clear()
        # The first material() call copies the shared material, so other nodes keep their color.
        self.material = node.material(0)
        self.color = self.material.base_color
        self.material.base_color = TINT

    def clear(self):
        if self.material is not None:
            self.material.base_color = self.color
            self.material = None


def main():
    from filly.integrations.pyglet import HostTarget, create_renderer

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--spin", type=float, default=20, help="Degrees per second while spinning")
    parser.add_argument("--auto", action="store_true", help="Click at scripted points, then exit")
    args = parser.parse_args()

    filly.set_log_level("warning")
    window = pyglet.window.Window(args.width, args.height, caption="filly picking: click a model, S spins")
    window.switch_to()
    renderer = create_renderer(window)
    target = HostTarget(renderer, window)
    scene, horse, shapes, marker = setup_scene(renderer)
    # prepare() also does the driver's one-time picking work, which would otherwise delay the
    # first click by tens of milliseconds on some GPUs.
    preparation = renderer.prepare(scene)
    while not preparation.ready():
        time.sleep(0.001)

    highlight = Highlight()
    pending = []  # (query, frames_rendered when clicked, click time)
    state = {"spin": False, "angle": 0.0, "last": time.perf_counter(), "frames": 0}
    center = np.asarray(horse.bounds, dtype=float).mean(axis=0)
    names = {horse: "horse", **{model: name for name, model in shapes.items()}}

    def click(x, y):
        # pyglet gives window coordinates from the lower left, as pick() expects; the target
        # has the framebuffer's size, which differs on high-DPI displays.
        columns, rows = window.get_framebuffer_size()
        px, py = x * columns / window.width, y * rows / window.height
        # The marker is a model too: hide it, so that the pick does not hit the last marker.
        marker.visible = False
        pending.append((renderer.pick(scene, px, py), renderer.stats.frames_rendered, time.perf_counter()))

    def answer(query, clicked_frame, clicked_at):
        hit = query.result
        waited = renderer.stats.frames_rendered - clicked_frame
        latency = f"{waited} frames, {(time.perf_counter() - clicked_at) * 1000:.1f} ms after the click"
        if hit is None:
            highlight.clear()
            marker.visible = False
            print(f"background (frame {query.frame_number}; {latency})")
            return
        highlight.set(hit.node)
        marker.position = hit.position
        marker.visible = True
        which = names.get(hit.model, "marker")
        x, y, z = hit.position
        print(f"{which}, node {hit.node.name!r} (index {hit.node.index}) at ({x:.3f}, {y:.3f}, {z:.3f}), "
              f"depth {hit.depth:.3f} (frame {query.frame_number}; {latency})")

    def on_draw():
        now = time.perf_counter()
        if state["spin"]:
            state["angle"] += args.spin * (now - state["last"])
            horse.rotation_euler_deg = (0, state["angle"], 0)
            horse.position = center - horse.transform[:3, :3] @ center
        state["last"] = now
        # Answers that arrived since the last frame, before this frame's render takes new queries.
        for item in [item for item in pending if item[0].ready()]:
            pending.remove(item)
            answer(*item)
        renderer.render(scene, target)
        target.draw()
        state["frames"] += 1
        if args.auto:
            script(state["frames"])

    def on_screen(point):
        """Window coordinates of a world point, from the camera of the last render."""
        camera = scene.camera
        clip = camera.projection @ camera.view_matrix @ np.append(point, 1.0)
        x, y = clip[:2] / clip[3]
        return (x + 1) / 2 * window.width, (y + 1) / 2 * window.height

    def model_center(model):
        return np.mean(world_box(model), axis=0)

    def script(frame):
        # The horse's center is between its legs, so aim at its body above the center.
        low, high = world_box(horse)
        body = (low + high) / 2 + (0, 0.15 * (high[1] - low[1]), 0)
        steps = {20: lambda: on_screen(body), 50: lambda: (5, 5),
                 80: lambda: on_screen(model_center(shapes["sphere"])),
                 110: lambda: on_screen(model_center(shapes["box"]))}
        if frame in steps:
            click(*steps[frame]())
        elif frame == 140:
            state["spin"] = True
            click(*on_screen(body))
        elif frame >= 180 and not pending:
            pyglet.app.exit()

    def on_mouse_press(x, y, button, modifiers):
        if button == pyglet.window.mouse.LEFT:
            click(x, y)

    def on_key_press(symbol, modifiers):
        if symbol == pyglet.window.key.S:
            state["spin"] = not state["spin"]
        elif symbol == pyglet.window.key.ESCAPE:
            pyglet.app.exit()
            return pyglet.event.EVENT_HANDLED

    def on_close():
        # Keep the window open; the renderer needs its context to close.
        pyglet.app.exit()
        return pyglet.event.EVENT_HANDLED

    window.push_handlers(on_draw=on_draw, on_mouse_press=on_mouse_press, on_key_press=on_key_press,
                         on_close=on_close)
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
