"""Per-frame CPU and GPU cost of a scene of many nodes that share one mesh.

Compare builds by running it with each build's Python, in interleaved rounds:

    python tools/benchmark_nodes.py --nodes 5000 --mode {static,orbit,transforms} --out result.json

static: nothing changes. orbit: the camera moves. transforms: set_transforms() moves every node.
Each frame waits for the GPU with finish(). Prints one JSON line of medians.
"""

import argparse
import json
import math
import statistics
import struct
import tempfile
import time
from pathlib import Path

import numpy as np

import filly


def sphere_grid_glb(count):
    """A glTF binary with `count` nodes on a grid that share one UV-sphere mesh (256 triangles)."""
    shape = filly.shapes.uv_sphere(0.4, segments=16, rings=9)
    positions = shape["positions"].astype(np.float32)
    normals = shape["normals"].astype(np.float32)
    indices = shape["indices"].astype(np.uint32).ravel()
    blob = positions.tobytes() + normals.tobytes() + indices.tobytes()
    side = math.ceil(math.sqrt(count))
    nodes = [{"mesh": 0, "translation": [float(i % side) - side / 2, 0.0, float(i // side) - side / 2]}
             for i in range(count)]
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": list(range(count))}],
        "nodes": nodes,
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.5, 0.3, 1], "metallicFactor": 0,
                                                "roughnessFactor": 0.5}}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": positions.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": positions.nbytes, "byteLength": normals.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": positions.nbytes + normals.nbytes, "byteLength": indices.nbytes,
             "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": positions.min(0).tolist(), "max": positions.max(0).tolist()},
            {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ],
    }
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    blob += b"\0" * (-len(blob) % 4)
    length = 12 + 8 + len(text) + 8 + len(blob)
    return (struct.pack("<III", 0x46546C67, 2, length) + struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(blob), 0x004E4942) + blob)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=5000)
    parser.add_argument("--mode", choices=["static", "orbit", "transforms"], default="static")
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    width, height = map(int, args.size.split("x"))
    filly.set_log_level("warning")
    path = Path(tempfile.gettempdir()) / f"bench_nodes_{args.nodes}.glb"
    path.write_bytes(sphere_grid_glb(args.nodes))

    with filly.Renderer() as renderer:
        target = renderer.create_render_target(width=width, height=height)
        scene = renderer.create_scene()
        scene.add_directional_light(direction=(-1, -2, -1), intensity=80000)
        scene.environment = renderer.create_environment(np.ones((8, 16, 3), np.float32))
        scene.environment_intensity = 15000
        model = scene.add(renderer.load_asset(str(path)))
        nodes = model.nodes
        base = np.stack([node.transform for node in nodes]).astype(np.float64)
        camera = renderer.create_camera()
        scene.camera = camera
        camera.frame(model, fill=0.9, direction=(0, -1, -1))
        center = np.zeros(3)
        start = np.array(camera.position, dtype=np.float64)
        radius = float(np.linalg.norm(start[[0, 2]]))
        transforms = base.copy()

        submit, update, wall, gpu = [], [], {}, {}
        for frame in range(args.warmup + args.frames):
            t0 = time.perf_counter()
            if args.mode == "orbit":
                angle = 0.005 * frame
                camera.position = (radius * math.sin(angle), start[1], radius * math.cos(angle))
                camera.look_at(tuple(center))
            elif args.mode == "transforms":
                transforms[:, 1, 3] = base[:, 1, 3] + 0.1 * math.sin(0.05 * frame)
                filly.set_transforms(nodes, transforms)
            t1 = time.perf_counter()
            renderer.render(scene, target)
            renderer.finish()
            t2 = time.perf_counter()
            stats = renderer.stats
            if stats.gpu_frame_ms is not None:
                gpu[stats.gpu_frame_number] = stats.gpu_frame_ms
            if frame >= args.warmup:
                update.append((t1 - t0) * 1000)
                submit.append(stats.cpu_submit_ms)
                wall[frame] = (t2 - t1) * 1000
        gpu_measured = [value for number, value in gpu.items() if number >= args.warmup]
        result = {
            "filament": getattr(filly, "filament_version", "unknown"),  # absent before filly 0.4.1
            "nodes": args.nodes, "mode": args.mode,
            "update_ms": statistics.median(update),
            "cpu_submit_ms": statistics.median(submit),
            "render_finish_ms": statistics.median(wall.values()),
            "gpu_frame_ms": statistics.median(gpu_measured) if gpu_measured else None,
        }
    print(json.dumps(result))
    if args.out:
        args.out.write_text(json.dumps(result))


if __name__ == "__main__":
    main()
