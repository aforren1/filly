"""Measure submission, batch completion, and one readback separately."""

import argparse
import json
from time import perf_counter

import numpy as np

from . import Renderer


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be positive")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset")
    parser.add_argument("--width", type=positive_int, default=1920)
    parser.add_argument("--height", type=positive_int, default=1080)
    parser.add_argument("--frames", type=positive_int, default=10000)
    parser.add_argument("--warmup", type=positive_int, default=30)
    parser.add_argument("--output-path", choices=("graded", "direct"), default="graded")
    args = parser.parse_args()
    with Renderer() as renderer:
        scene = renderer.create_scene()
        scene.output_path = args.output_path
        scene.load(args.asset)
        camera = scene.create_camera()
        camera.set_perspective(fov_y=45, near=0.01, far=100)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(-1, -1, -1))
        target = renderer.create_render_target(width=args.width, height=args.height)
        for _ in range(args.warmup):
            renderer.render(scene, target)
        renderer.finish()
        submissions = np.empty(args.frames)
        started = perf_counter()
        for i in range(args.frames):
            renderer.render(scene, target)
            submissions[i] = renderer.stats.cpu_submit_ms
        finish_started = perf_counter()
        renderer.finish()
        finished = perf_counter()
        target.read()
        readback_ms = (perf_counter() - finished) * 1000
        print(json.dumps({
            "frames": args.frames,
            "width": args.width, "height": args.height,
            "output_path": args.output_path,
            "cpu_submit_ms": dict(zip(("p50", "p95", "p99"), np.percentile(submissions, [50, 95, 99]))),
            "batch_finish_wait_ms": (finished - finish_started) * 1000,
            "batch_ms_per_frame": (finished - started) * 1000 / args.frames,
            "readback_ms": readback_ms,
        }, indent=2))


if __name__ == "__main__":
    main()
