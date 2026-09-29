"""Drift a sinusoidal grating across a tilted plane in a pyglet window.

Each frame computes the grating in a preallocated NumPy array and uploads it with
Texture.update(). With --transform, the texture is uploaded once and the texture transform
drifts it instead, which uploads nothing per frame. The grating is linear light with its mean
equal to the grey background, so it has the same mean luminance at any contrast.
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
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--size", type=int, default=256, help="Texture size in texels")
    parser.add_argument("--cycles", type=float, default=6, help="Grating cycles across the plane")
    parser.add_argument("--speed", type=float, default=2, help="Drift in cycles per second")
    parser.add_argument("--orientation", type=float, default=0, help="Grating orientation in degrees")
    parser.add_argument("--contrast", type=float, default=0.8, help="Michelson contrast in linear light")
    parser.add_argument("--transform", action="store_true", help="Drift with the texture transform")
    parser.add_argument("--no-vsync", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    filly.set_log_level("warning")
    window = pyglet.window.Window(args.width, args.height, caption="filly drifting grating",
                                  vsync=not args.no_vsync)
    window.switch_to()
    renderer = create_renderer(window)
    width, height = window.get_framebuffer_size()
    target = SharedTarget(renderer, window, width, height)

    scene = renderer.create_scene()
    scene.background = (0.5, 0.5, 0.5, 1)
    camera = scene.create_camera()
    camera.set_perspective(fov_y=40, near=0.1, far=10)
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    # Unlit, so texel values are output values.
    plane = scene.create_mesh(**filly.shapes.plane(1.6, 1.6), unlit=True)
    plane.rotation_euler_deg = (0, 25, 0)

    # Grating phase in cycles at each texel, for the chosen orientation.
    u = (np.arange(args.size, dtype=np.float32) + 0.5) / args.size
    angle = np.deg2rad(args.orientation)
    cycles = np.float32(args.cycles) * (u[None, :] * np.cos(angle) + u[:, None] * np.sin(angle))
    cycles = cycles.astype(np.float32)
    pixels = np.empty((args.size, args.size), dtype=np.float32)
    amplitude = np.float32(0.5 * args.contrast)

    def grating(phase):
        # In place: no allocation per frame.
        np.subtract(cycles, np.float32(phase), out=pixels)
        np.multiply(pixels, np.float32(2 * np.pi), out=pixels)
        np.sin(pixels, out=pixels)
        np.multiply(pixels, amplitude, out=pixels)
        np.add(pixels, np.float32(0.5), out=pixels)
        return pixels

    texture = renderer.create_texture(grating(0), color_space="linear")
    material = plane.material("mesh")
    material.base_color_texture = texture

    timings = []
    start = perf_counter()

    def on_draw():
        phase = (perf_counter() - start) * args.speed
        began = perf_counter()
        if args.transform:
            # A shift of 1 / cycles in U moves the texture by one grating period.
            material.set_texture_transform("base_color", offset=(phase / args.cycles, 0))
        else:
            texture.update(grating(phase))
        updated = perf_counter()
        renderer.render(scene, target)
        target.draw()
        timings.append(((updated - began) * 1000, renderer.stats.cpu_submit_ms, perf_counter()))
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
        texture.close()
        target.close()
        renderer.close()
        window.close()
    measured = np.array(timings[min(30, len(timings) // 2):])
    summary = {"frames": len(timings), "mode": "transform" if args.transform else "update",
               "texture": [args.size, args.size]}
    if len(measured) > 1:
        summary["update_ms"] = dict(zip(("p50", "p95"), np.round(np.percentile(measured[:, 0], [50, 95]), 3).tolist()))
        summary["cpu_submit_ms"] = dict(zip(("p50", "p95"), np.round(np.percentile(measured[:, 1], [50, 95]), 3).tolist()))
        summary["frame_interval_ms_p50"] = round(float(np.median(np.diff(measured[:, 2]))) * 1000, 3)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
