"""Capture glass with both projections and record submission and completion times."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

import filly
from filly.benchmark import positive_int


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", type=Path)
    parser.add_argument("--size", type=positive_int, default=512)
    parser.add_argument("--frames", type=positive_int, default=120)
    parser.add_argument("--warmup", type=positive_int, default=30)
    parser.add_argument("--output", type=Path, default=Path(".deps/refraction-benchmark"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    filly.set_log_level("warning")
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        scene.background = (0.15, 0.15, 0.15, 1)
        scene.refraction = True
        scene.antialiasing = "fxaa"
        scene.tone_mapping = "aces_legacy"
        model = scene.load(args.asset)
        camera = scene.create_camera()
        camera.exposure = 15
        scene.camera = camera
        scene.add_directional_light(direction=(-1, -1, -2), intensity=100000)
        panorama = np.full((32, 64, 3), 0.4, dtype=np.float32)
        panorama[4:15, 6:14] = (3, 2.8, 2.5)
        panorama[6:20, 42:50] = (1.5, 1.8, 2.2)
        scene.set_environment(panorama, intensity=30000)
        target = renderer.create_render_target(width=args.size, height=args.size)
        report = {"asset": str(args.asset), "size": args.size, "frames": args.frames,
                  "warmup": args.warmup, "runs": {}}
        samples = np.empty((args.frames, 2))
        for projection in ("orthographic", "perspective"):
            # The perspective view sees the bounding sphere from 3 radii; the orthographic view
            # is as high as the perspective view at the sphere's center.
            tan_half = np.tan(np.deg2rad(45 / 2))
            if projection == "orthographic":
                camera.set_orthographic(height=1, near=1, far=2)
                fill = 1 / (3 * tan_half)
            else:
                camera.set_perspective(fov_y=45, aspect=1, near=1, far=2)
                fill = np.tan(np.arcsin(1 / 3)) / tan_half
            try:
                camera.frame(model, fill=fill, direction=(0, 0, -1), aspect=1)
            except ValueError:
                parser.error("Asset must have finite nonzero bounds")
            for _ in range(args.warmup):
                renderer.render(scene, target)
            renderer.finish()
            for i in range(args.frames):
                start = perf_counter()
                renderer.render(scene, target)
                submit = renderer.stats.cpu_submit_ms
                renderer.finish()
                samples[i] = (submit, (perf_counter() - start) * 1000)
            report["runs"][projection] = {
                metric: dict(zip(("p50", "p95", "p99"), np.percentile(samples[:, j], [50, 95, 99])))
                for j, metric in enumerate(("cpu_submit_ms", "render_and_finish_ms"))}
            np.savetxt(args.output / f"{projection}.csv", samples, delimiter=",",
                       header="cpu_submit_ms,render_and_finish_ms", comments="")
            Image.fromarray(target.read()).save(args.output / f"{projection}.png")
        encoded = json.dumps(report, indent=2)
        (args.output / "report.json").write_text(encoded + "\n", encoding="utf-8")
        print(encoded)


if __name__ == "__main__":
    main()
