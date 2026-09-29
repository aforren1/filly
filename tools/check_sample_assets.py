"""Download a pinned Khronos catalog and record isolated load/render smoke checks.

Images are review artifacts, not conformance assertions. Assets retain upstream licenses.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import warnings

REVISION = "c6a6bd13ab2b3c685c7903d03561b8a9392f38b8"
REPOSITORY = "https://github.com/KhronosGroup/glTF-Sample-Assets"
RAW = f"https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/{REVISION}/"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch(url, path, blob_sha=None):
    def valid(data):
        return blob_sha is None or hashlib.sha1(
            f"blob {len(data)}\0".encode() + data).hexdigest() == blob_sha
    if path.exists() and valid(path.read_bytes()):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=90) as response:
        data = response.read()
    if not valid(data):
        raise ValueError(f"Upstream blob checksum mismatch: {url}")
    temporary = path.with_suffix(path.suffix + ".download")
    temporary.write_bytes(data)
    temporary.replace(path)


def inspect_document(path):
    data = path.read_bytes()
    if data[:4] == b"glTF":
        size = struct.unpack_from("<I", data, 12)[0]
        return json.loads(data[20:20 + size])
    return json.loads(data)


def write_gallery(output, report):
    rows = []
    for result in report["results"]:
        name = result["name"]
        images = "".join(f'<a href="{html.escape(urllib.parse.quote(report["mode"] + "/" + name + "/" + frame["image"]))}"><img loading="lazy" width="160" src="{html.escape(urllib.parse.quote(report["mode"] + "/" + name + "/" + frame["image"]))}"></a>'
                         for frame in result.get("frames", []))
        preview = result.get("preview")
        settings = f"Preview settings: {json.dumps(preview)}" if preview else ""
        notes = "\n".join([settings, result.get("error", ""), *result.get("warnings", []), *result.get("limitations", [])]).strip()
        references = sorted((output / "assets" / "Models" / name / "screenshot").glob("*"))
        reference = next((p for p in references if "large" in p.name.lower()), references[0] if references else None)
        if reference:
            src = html.escape(urllib.parse.quote(reference.relative_to(output).as_posix()))
            images += f'<br>Upstream reference (different renderer and lighting):<br><a href="{src}"><img loading="lazy" width="320" src="{src}"></a>'
        rows.append(f'<tr id="{html.escape(name)}"><td><a href="{html.escape(result["source"])}">{html.escape(name)}</a>'
                    f'<br>{html.escape(result["variant"])}</td><td>{html.escape(result["status"])}</td>'
                    f'<td>{images}</td><td><pre>{html.escape(notes)}</pre></td></tr>')
    page = ('<!doctype html><meta charset="utf-8"><title>glTF sample audit</title>'
            '<style>body{font:14px sans-serif;margin:24px}td{padding:8px;vertical-align:top;border-bottom:1px solid #ccc}'
            'pre{white-space:pre-wrap;max-width:32em}img{margin-right:4px}</style>'
            '<h1>glTF sample audit</h1><p>Rendered means the load and two image captures completed. '
            'It does not establish visual correctness or extension conformance.</p>'
            f'<p>Revision: {REVISION}. Images are derived from the linked assets; upstream licenses apply.</p>'
            '<table><tr><th>Asset</th><th>Result</th><th>Views</th><th>Notes</th></tr>' + ''.join(rows) + '</table>')
    (output / f'gallery-{report["mode"]}.html').write_text(page, encoding="utf-8")


def worker(path, output, mode, *, lighting="auto", view=None, projection=None, height=320):
    import numpy as np
    from PIL import Image
    import filly

    started = time.perf_counter()
    result = {"path": str(path), "mode": mode, "status": "error"}
    if lighting != "auto" or view is not None or projection is not None or height != 320:
        result["preview"] = {"lighting": lighting, "view_degrees": view, "projection": projection, "height": height}
    output.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            document = inspect_document(path)
            result["extensions_used"] = document.get("extensionsUsed", [])
            result["extensions_required"] = document.get("extensionsRequired", [])
            limits = {
                "EXT_mesh_gpu_instancing": "Instance transforms expand into mesh-sharing child nodes; GPU draw batching is not implemented.",
                "KHR_materials_iridescence": "Filament fits thin-film Fresnel to a view-dependent F0; the direct diffuse lobe omits the extension's maximum-channel energy weighting. Reference-image parity is not established.",
            }
            result["limitations"] = [message for ext, message in limits.items() if ext in result["extensions_used"]]
            filly.set_log_level("warning")
            with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
                scene = renderer.create_scene()
                scene.background = (0.15, 0.15, 0.15, 1)
                scene.refraction = True
                scene.antialiasing = "fxaa"
                scene.tone_mapping = "aces_legacy"
                model = scene.load(path)
                bounds = np.asarray(model.bounds)
                center = bounds.mean(axis=0)
                radius = np.linalg.norm(bounds[1] - bounds[0]) / 2
                if not np.isfinite(bounds).all() or not np.isfinite(radius) or radius <= 0:
                    raise ValueError("No finite nonzero geometry bounds")
                cameras = model.cameras
                # Prefer a uniquely named camera node, then the first camera node.
                imported = bool(cameras) and view is None and projection is None
                if imported:
                    names = [c.node.name for c in cameras]
                    camera = next((c for c in cameras if c.node.name and names.count(c.node.name) == 1), cameras[0])
                    camera_name = camera.node.name or f"camera node {camera.node.index}"
                else:
                    camera, camera_name = scene.create_camera(), None
                aspect = 1
                if imported:
                    node = document["nodes"][camera.node.index]
                    camera_projection = document["cameras"][node["camera"]]
                    if camera_projection["type"] == "perspective":
                        aspect = camera_projection["perspective"].get("aspectRatio", 1)
                    else:
                        aspect = camera_projection["orthographic"]["xmag"] / camera_projection["orthographic"]["ymag"]
                elif projection == "orthographic":
                    half = radius * 1.15
                    camera.set_orthographic(left=-half, right=half, bottom=-half, top=half,
                                            near=max(radius * 0.001, 1e-6), far=radius * 10)
                else:
                    camera.set_perspective(fov_y=45, near=max(radius * 0.001, 1e-6), far=radius * 10)
                result["camera"] = camera_name if imported else "fitted"
                scene.camera = camera
                asset_lights = bool(model.lights) and lighting == "auto"
                if lighting == "environment":
                    for light in model.lights:
                        light.intensity = 0
                if imported and asset_lights:
                    scene.background = (0, 0, 0, 1)
                camera.exposure = 0 if asset_lights else 15
                if not asset_lights and lighting == "auto":
                    scene.add_directional_light(direction=(-1, -1, -2), intensity=100000)
                panorama = np.full((32, 64, 3), 0.4, dtype=np.float32)
                panorama[4:15, 6:14] = (3, 2.8, 2.5)
                panorama[6:20, 42:50] = (1.5, 1.8, 2.2)
                # Authored lighting tests can depend on darkness away from their lights.
                if not asset_lights:
                    scene.set_environment(panorama, intensity=30000)
                target = renderer.create_render_target(width=max(1, round(height*aspect)), height=height)
                result["animations"] = [{"name": a.name, "duration": a.duration} for a in model.animations]
                result["variants"] = model.variants
                result["frames"] = []
                directions = ((0, 0, 1), (0.65, 0.25, 1))
                if view is not None:
                    pitch = math.radians(view[1])
                    directions = [(math.sin(math.radians(yaw))*math.cos(pitch), math.sin(pitch),
                                   math.cos(math.radians(yaw))*math.cos(pitch)) for yaw in (view[0], view[0]+20)]
                for index, direction in enumerate(directions):
                    if index and model.animations:
                        model.apply_animation(0, model.animations[0].duration * 0.5, loop=False)
                    direction = np.asarray(direction, dtype=float)
                    if not imported:
                        camera.position = center + direction / np.linalg.norm(direction) * radius * 3
                        camera.look_at(center)
                    renderer.render(scene, target)
                    pixels = target.read()
                    image = output / f"view-{index}.png"
                    Image.fromarray(pixels).save(image)
                    result["frames"].append({"image": image.name, "rgb_min": int(pixels[:, :, :3].min()),
                                             "rgb_max": int(pixels[:, :, :3].max())})
                result["status"] = "rendered"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["traceback"] = traceback.format_exc()
        result["warnings"] = list(dict.fromkeys(str(w.message) for w in caught))
    result["seconds"] = round(time.perf_counter() - started, 3)
    write_json(output / "result.json", result)
    return result


def main():
    # Catalog names include Unicode, including when Windows stdout is redirected.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".deps/sample-audit"))
    parser.add_argument("--models", nargs="+", help="Catalog names; default: all assets")
    parser.add_argument("--mode", choices=("compiled", "precompiled"), default="compiled",
                        help="precompiled uses Renderer(precompiled_shaders=True)")
    parser.add_argument("--lighting", choices=("auto", "environment"), default="auto")
    parser.add_argument("--view", type=float, nargs=2, metavar=("YAW", "PITCH"), help="Fitted camera angles in degrees; second view adds 20 degrees of yaw")
    parser.add_argument("--projection", choices=("perspective", "orthographic"), help="Override imported cameras with a fitted projection")
    parser.add_argument("--height", type=int, default=320, help="Capture height in pixels")
    parser.add_argument("--timeout", type=float, default=120, help="Maximum seconds per render process")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not 1 <= args.height <= 8192:
        parser.error("--height must be between 1 and 8192")
    if args.view is not None and (not all(math.isfinite(v) for v in args.view) or not -90 < args.view[1] < 90):
        parser.error("--view requires finite angles and -90 < pitch < 90 degrees")
    output = args.output.resolve()
    if args.worker:
        worker(args.worker.resolve(), output, args.mode, lighting=args.lighting, view=args.view,
               projection=args.projection, height=args.height)
        return
    cache = output / "assets"
    index_path, tree_path = output / "model-index.json", output / "tree.json"
    fetch(RAW + "Models/model-index.json", index_path)
    fetch(f"https://api.github.com/repos/KhronosGroup/glTF-Sample-Assets/git/trees/{REVISION}?recursive=1", tree_path)
    index = json.loads(index_path.read_bytes())
    tree = json.loads(tree_path.read_bytes())
    if tree.get("truncated"):
        raise RuntimeError("Sample file inventory was truncated")
    if args.models:
        unknown = set(args.models) - {item["name"] for item in index}
        if unknown:
            parser.error(f"Unknown assets: {sorted(unknown)}")
        index = [item for item in index if item["name"] in args.models]
    files = {item["path"]: item for item in tree["tree"] if item["type"] == "blob"}
    selections = []
    downloads = {}
    for item in index:
        variants = item["variants"]
        variant = next((key for key in ("glTF-Binary", "glTF") if key in variants), next(iter(variants)))
        prefix = f"Models/{item['name']}/{variant}/"
        selected = prefix + variants[variant]
        if selected not in files:
            raise RuntimeError(f"Catalog entry missing from pinned tree: {selected}")
        selections.append((item, variant, cache / selected))
        # Keep each selected variant's resources together, including external buffers and images.
        for path, record in files.items():
            if (path.startswith(prefix) or path == f"Models/{item['name']}/README.md"
                    or path == f"Models/{item['name']}/{item.get('screenshot', '')}"):
                downloads[path] = record
    size = sum(item["size"] for item in downloads.values()) / 1024**2
    print(f"{len(selections)} assets, {size:.1f} MiB source files, revision {REVISION}", flush=True)
    def download(pair):
        path, record = pair
        fetch(RAW + urllib.parse.quote(path), cache / path, record["sha"])
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(download, downloads.items()))
    if args.download_only:
        return
    report = {"repository": REPOSITORY, "revision": REVISION, "python": sys.version,
              "platform": sys.platform, "mode": args.mode,
              "scope": "One variant per catalog asset, first imported camera or two fitted views, clip 0 at start/midpoint; no conformance claim",
              "results": []}
    for item, variant, path in selections:
        name = item["name"]
        destination = output / args.mode / name
        destination.mkdir(parents=True, exist_ok=True)
        result_path = destination / "result.json"
        if result_path.exists():
            result_path.unlink()
        with (destination / "process.log").open("w", encoding="utf-8") as log:
            try:
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(path),
                           "--output", str(destination), "--mode", args.mode,
                           "--lighting", args.lighting, "--height", str(args.height)]
                if args.view is not None:
                    command += ["--view", *map(str, args.view)]
                if args.projection is not None:
                    command += ["--projection", args.projection]
                process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                    timeout=args.timeout)
                if process.returncode:
                    result = {"status": "crash", "error": f"Process exit {process.returncode}"}
                elif result_path.exists():
                    result = json.loads(result_path.read_bytes())
                else:
                    result = {"status": "error", "error": "Worker did not produce a report"}
            except subprocess.TimeoutExpired:
                result = {"status": "timeout", "error": f"Exceeded {args.timeout} seconds"}
        result.update(name=name, variant=variant,
                      source=f"{REPOSITORY}/tree/{REVISION}/Models/{name}")
        report["results"].append(result)
        write_json(output / f"report-{args.mode}.json", report)
        print(f"{name}: {result['status']}" + (f" ({result['error']})" if "error" in result else ""), flush=True)
    print(f"Report: {output / f'report-{args.mode}.json'}", flush=True)
    write_gallery(output, report)


if __name__ == "__main__":
    main()
