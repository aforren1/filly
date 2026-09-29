"""Measure runtime texture and mesh update costs.

CPU: the time that Texture.update() or Model.update_mesh() takes on the calling thread (copy
into staging storage and command submission). Completion: the extra wall time of
update + render + finish over render + finish for a tiny target, which covers Filament's
driver thread and the GPU upload. Filament 1.77.1's release SDK has no GPU timer queries.
"""

import argparse
import json
from time import perf_counter

import numpy as np

import filly


def percentiles(values):
    return dict(zip(("p50", "p95"), np.round(np.percentile(values, [50, 95]), 3).tolist()))


def measure(renderer, scene, target, update, frames):
    for _ in range(10):
        update()
        renderer.render(scene, target)
    renderer.finish()
    cpu, with_update, without = [], [], []
    for _ in range(frames):
        began = perf_counter()
        update()
        cpu.append((perf_counter() - began) * 1000)
        renderer.render(scene, target)
        renderer.finish()
        with_update.append((perf_counter() - began) * 1000)
        began = perf_counter()
        renderer.render(scene, target)
        renderer.finish()
        without.append((perf_counter() - began) * 1000)
    extra = np.array(with_update) - np.array(without)
    return {"cpu_ms": percentiles(cpu), "completion_ms": percentiles(extra)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frames", type=int, default=200)
    args = parser.parse_args()
    filly.set_log_level("error")
    results = {}
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(height=2, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        plane = scene.create_mesh(**filly.shapes.plane(2, 2), unlit=True, alpha_mode="blend")
        target = renderer.create_render_target(width=64, height=64)
        rng = np.random.default_rng(0)
        for width, height in ((256, 256), (1024, 1024), (1920, 1080)):
            for dtype in (np.uint8, np.float32):
                frames = [rng.random((height, width, 4)).astype(dtype) if dtype == np.float32
                          else rng.integers(0, 256, (height, width, 4), dtype=np.uint8) for _ in range(2)]
                space = "srgb" if dtype == np.uint8 else "linear"
                texture = renderer.create_texture(frames[0], color_space=space)
                plane.material("mesh").base_color_texture = texture
                counter = [0]

                def update():
                    counter[0] += 1
                    texture.update(frames[counter[0] % 2])

                results[f"texture {width}x{height} rgba {np.dtype(dtype).name}"] = measure(
                    renderer, scene, target, update, args.frames)
                plane.material("mesh").base_color_texture = None
                texture.close()
        for segments in (32, 256):
            shape = filly.shapes.plane(2, 2, segments=(segments, segments))
            mesh = scene.create_mesh(**shape)
            moved = [shape["positions"], shape["positions"] + np.float32([0, 0, 0.1])]
            counter = [0]

            def deform():
                counter[0] += 1
                mesh.update_mesh(positions=moved[counter[0] % 2])

            results[f"mesh positions, {len(shape['positions'])} vertices"] = measure(
                renderer, scene, target, deform, args.frames)
            mesh.close()
        target.close()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
