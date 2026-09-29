"""Render side-by-side stereo into one shared texture in a pyglet window.

Two cameras sit an interocular distance apart with parallel axes. The left eye renders into
the left half of the target and the right eye into the right half. The second render passes
clear=False, so it keeps the first half; its own half is still filled with the background.
Parallel axes put zero disparity at infinity; objects in front of the cameras appear near.
"""

import argparse
import json
from time import perf_counter

import numpy as np
import pyglet

import filly
from filly.integrations.pyglet import SharedTarget, create_renderer


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--iod", type=float, default=0.064, help="Interocular distance in meters")
    parser.add_argument("--distance", type=float, default=1.2, help="Viewing distance to the objects in meters")
    parser.add_argument("--spin", type=float, default=30, help="Degrees per second")
    parser.add_argument("--no-vsync", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    filly.set_log_level("warning")
    window = pyglet.window.Window(args.width, args.height, caption="filly stereo", vsync=not args.no_vsync)
    window.switch_to()
    renderer = create_renderer(window)
    width, height = window.get_framebuffer_size()
    target = SharedTarget(renderer, window, width, height)

    scene = renderer.create_scene()
    scene.background = (0.05, 0.05, 0.05, 1)
    scene.add_directional_light(direction=(-0.5, -1, -0.8), intensity=60000)
    scene.add_directional_light(direction=(0.8, 0.2, -0.5), intensity=15000)
    box = scene.create_mesh(**filly.shapes.box(0.12, 0.12, 0.12), base_color=(0.8, 0.3, 0.2, 1), roughness=0.5)
    box.position = (-0.12, 0, 0)
    sphere = scene.create_mesh(**filly.shapes.uv_sphere(0.07), base_color=(0.2, 0.5, 0.9, 1), roughness=0.4)
    sphere.position = (0.12, 0, -0.1)
    cylinder = scene.create_mesh(**filly.shapes.cylinder(0.04, 0.15), base_color=(0.3, 0.8, 0.3, 1))
    cylinder.position = (0, 0, 0.12)

    half = width // 2
    eyes = []
    for side in (-1, 1):
        camera = scene.create_camera()
        # The aspect follows each half's viewport.
        camera.set_perspective(fov_y=30, near=0.05, far=10)
        camera.position = (side * args.iod / 2, 0, args.distance)
        camera.look_at((side * args.iod / 2, 0, 0))
        eyes.append(camera)
    viewports = [(0, 0, half, height), (half, 0, width - half, height)]

    timings = []
    start = perf_counter()

    def on_draw():
        angle = (perf_counter() - start) * args.spin
        for model in (box, sphere, cylinder):
            model.rotation_euler_deg = (15, angle, 0)
        renderer.render(scene, target, camera=eyes[0], viewport=viewports[0])
        left = renderer.stats.cpu_submit_ms
        renderer.render(scene, target, camera=eyes[1], viewport=viewports[1], clear=False)
        target.draw()
        timings.append((left, renderer.stats.cpu_submit_ms, perf_counter()))
        if len(timings) >= args.frames:
            pyglet.app.exit()

    def on_close():
        pyglet.app.exit()
        return pyglet.event.EVENT_HANDLED

    window.push_handlers(on_draw=on_draw, on_close=on_close)
    pyglet.clock.schedule(lambda dt: None)
    try:
        pyglet.app.run()
    finally:
        window.switch_to()
        target.close()
        renderer.close()
        window.close()
    measured = np.array(timings[min(30, len(timings) // 2):])
    summary = {"frames": len(timings), "viewports": viewports}
    if len(measured) > 1:
        for i, name in enumerate(("left_submit_ms", "right_submit_ms")):
            summary[name] = dict(zip(("p50", "p95"), np.round(np.percentile(measured[:, i], [50, 95]), 3).tolist()))
        summary["frame_interval_ms_p50"] = round(float(np.median(np.diff(measured[:, 2]))) * 1000, 3)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
