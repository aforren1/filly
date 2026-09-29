"""Render generated shapes offscreen: a rippling plane, a textured box, a sphere, and a cylinder.

The plane deforms every frame with Model.update_mesh(). The box shows a checkerboard from a
NumPy array. --fog adds distance fog. The last frame is saved as a PNG.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

import filly


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument("--fog", action="store_true", help="Fade distant geometry toward the background")
    parser.add_argument("--output", type=Path, default=Path("shapes.png"))
    return parser.parse_args()


def main():
    args = parse_args()
    filly.set_log_level("warning")
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        background = (0.6, 0.65, 0.7)
        scene.background = (*background, 1)
        if args.fog:
            scene.fog = True
            scene.set_fog_options(color=background, density=0.08, start=2)
        camera = scene.create_camera()
        camera.set_perspective(fov_y=40, near=0.1, far=50)
        camera.position = (0, 1.6, 4.5)
        camera.look_at((0, 0.2, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(-0.4, -1, -0.6), intensity=70000)

        # 40 x 40 cells; normals are computed from the positions, so none are passed.
        flat = filly.shapes.plane(6, 6, segments=(40, 40))
        # Lay the plane down: y becomes -z.
        rest = flat["positions"][:, [0, 2, 1]] * np.float32([1, 1, -1])
        indices = flat["indices"]
        floor = scene.create_mesh(rest, indices, uvs=flat["uvs"], base_color=(0.35, 0.4, 0.35, 1))
        floor.position = (0, -0.5, -1)
        ripple = rest.copy()
        radius = np.hypot(rest[:, 0], rest[:, 2])

        box = scene.create_mesh(**filly.shapes.box(0.8, 0.8, 0.8), roughness=0.7)
        box.position = (-1.2, 0, 0)
        checker = (np.indices((8, 8)).sum(0) % 2 * 200 + 40).astype(np.uint8)
        box.material("mesh").base_color_texture = renderer.create_texture(
            checker, color_space="srgb", filter="nearest")
        sphere = scene.create_mesh(**filly.shapes.uv_sphere(0.45), base_color=(0.85, 0.25, 0.2, 1), roughness=0.35)
        sphere.position = (0.2, 0, -0.6)
        cylinder = scene.create_mesh(**filly.shapes.cylinder(0.3, 1.0), base_color=(0.2, 0.4, 0.85, 1))
        cylinder.position = (1.4, 0, -2.5)

        target = renderer.create_render_target(width=args.width, height=args.height)
        updates = []
        for frame in range(args.frames):
            time = frame / 60
            began = perf_counter()
            # In-place ripple of the heights only: no per-frame allocation in the update.
            np.multiply(radius, 3, out=ripple[:, 1])
            ripple[:, 1] -= time * 4
            np.sin(ripple[:, 1], out=ripple[:, 1])
            ripple[:, 1] *= 0.08
            floor.update_mesh(positions=ripple)
            updates.append((perf_counter() - began) * 1000)
            box.rotation_euler_deg = (0, 40 + 30 * time, 0)
            renderer.render(scene, target)
        image = target.read()
        target.close()
    try:
        from PIL import Image
        Image.fromarray(image).save(args.output)
        saved = str(args.output)
    except ImportError:
        saved = None
    print(json.dumps({"frames": args.frames, "vertices": len(rest), "saved": saved,
                      "update_ms": dict(zip(("p50", "p95"), np.round(np.percentile(updates, [50, 95]), 3).tolist()))},
                     indent=2))


if __name__ == "__main__":
    main()
