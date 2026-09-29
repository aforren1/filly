"""Compare filly renders with Filament's gltf_viewer from the same SDK release.

Both sides get the same asset, camera, exposure, equirectangular environment, tone mapper,
and postprocessing settings. The tool writes side-by-side images, amplified difference images,
and a JSON/CSV report. See docs/how-to/reference-comparison.md.
"""

import argparse
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / ".deps"
CATALOG = ROOT / "docs" / "reference" / "sample-assets.csv"
MODELS = DEPS / "sample-audit" / "assets" / "Models"
VIEWER = DEPS / "bin" / "gltf_viewer.exe"
# gltf_viewer always reserves this many pixels on the left for its ImGui sidebar, even headless.
# The window must be wider by this amount to get a main viewport of the requested size.
VIEWER_SIDEBAR = 410
# Filament's lens model uses a 24 mm sensor height (Camera::setLensProjection).
SENSOR_HEIGHT_MM = 24.0
# gltf_viewer's default lens (libs/viewer/include/viewer/Settings.h, CameraSettings::focalLength).
FOCAL_LENGTH_MM = 28.0
IBL_INTENSITY = 30000.0
VIEW_DIRECTION = (0.35, 0.25, 1.0)
# gltf_viewer's default sunlight direction (libs/viewer/include/viewer/Settings.h), white color.
SUN_DIRECTION = (0.6, -1.0, -0.8)
SUN_INTENSITY = 100000.0


def write_hdr(path, pixels):
    """Write a flat (not run-length encoded) Radiance RGBE file; stb_image reads both forms."""
    import numpy as np
    rgb = np.asarray(pixels, dtype=np.float64)
    peak = rgb.max(axis=2)
    mantissa, exponent = np.frexp(peak)
    scale = np.where(peak > 1e-32, mantissa * 256.0 / np.maximum(peak, 1e-32), 0.0)
    rgbe = np.zeros(rgb.shape[:2] + (4,), dtype=np.uint8)
    rgbe[..., :3] = np.clip(rgb * scale[..., None], 0, 255).astype(np.uint8)
    rgbe[..., 3] = np.where(peak > 1e-32, exponent + 128, 0).astype(np.uint8)
    header = f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {rgb.shape[0]} +X {rgb.shape[1]}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header.encode("ascii") + rgbe.tobytes())


def make_environment(kind, directory):
    """Create a deterministic panorama so that both renderers filter identical source data."""
    import numpy as np
    path = directory / f"env-{kind}.hdr"
    height = 256
    if kind == "uniform":
        # Constant radiance makes filtered specular and irradiance independent of the filter.
        pixels = np.full((height, 2 * height, 3), 1.0)
    else:
        v = (np.arange(height) + 0.5) / height
        u = (np.arange(2 * height) + 0.5) / (2 * height)
        elevation = (0.5 - v)[:, None] * math.pi
        azimuth = (u - 0.5)[None, :] * 2 * math.pi
        sky = np.clip(np.sin(elevation), 0, 1)
        pixels = np.empty((height, 2 * height, 3))
        pixels[...] = np.stack([0.35 + 0.25 * sky, 0.40 + 0.35 * sky, 0.45 + 0.65 * sky], axis=-1)
        pixels[(elevation < 0).repeat(2 * height, axis=1)] = (0.25, 0.20, 0.15)
        # Distinct warm, cool, and green sources at known azimuths expose rotation or mirror errors.
        for az, el, radius, color in ((-50, 35, 12, (40, 30, 20)), (70, 20, 10, (6, 10, 20)),
                                      (160, 5, 14, (3, 12, 3))):
            d = np.degrees(np.arccos(np.clip(
                np.sin(elevation) * math.sin(math.radians(el)) +
                np.cos(elevation) * math.cos(math.radians(el)) * np.cos(azimuth - math.radians(az)), -1, 1)))
            pixels[d < radius] = color
    write_hdr(path, pixels)
    return path


def find_asset(name):
    path = Path(name)
    if path.suffix.lower() in (".glb", ".gltf") and path.exists():
        return path.resolve()
    with CATALOG.open(encoding="utf-8") as stream:
        variant = next((row["variant"] for row in csv.DictReader(stream) if row["asset"] == name), None)
    if variant is None:
        raise SystemExit(f"Unknown asset: {name}")
    directory = MODELS / name / variant
    files = sorted(p for p in directory.glob("*") if p.suffix.lower() in (".glb", ".gltf"))
    if not files:
        raise SystemExit(f"No glTF file in {directory}; run tools/check_sample_assets.py --download-only")
    return files[0].resolve()


def fit_camera(bounds, width, height, focal_length):
    """Frame the bounding sphere from a fixed oblique direction."""
    import numpy as np
    bounds = np.asarray(bounds, dtype=float)
    center = bounds.mean(axis=0)
    radius = float(np.linalg.norm(bounds[1] - bounds[0]) / 2)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("Asset has no finite nonzero bounds")
    half = math.atan(SENSOR_HEIGHT_MM / 2 / focal_length)
    half_x = math.atan(math.tan(half) * width / height)
    distance = radius / math.sin(min(half, half_x)) * 1.05
    direction = np.asarray(VIEW_DIRECTION) / np.linalg.norm(VIEW_DIRECTION)
    eye = center + direction * distance
    return {"eye": eye.tolist(), "target": center.tolist(), "up": [0.0, 1.0, 0.0],
            "fov_y": math.degrees(2 * half), "focal_length_mm": focal_length,
            "near": max(distance - radius * 1.5, distance * 0.01), "far": distance + radius * 3}


def render_filly(args):
    """Worker process: render one asset with filly and record the camera it used."""
    import numpy as np
    from PIL import Image
    import filly

    filly.set_log_level("warning")
    out = Path(args.worker_output)
    with filly.Renderer(precompiled_shaders=args.shaders == "precompiled") as renderer:
        scene = renderer.create_scene()
        scene.background = (0, 0, 0, 1)
        scene.refraction = True
        scene.shadows = False
        scene.antialiasing = args.antialiasing
        scene.msaa = args.msaa
        # gltf_viewer's default tone mapper; filly defaults to the neutral linear one.
        scene.tone_mapping = "aces_legacy"
        scene.encoding = "srgb"
        model = scene.load(args.worker)
        camera_setup = fit_camera(model.bounds, args.width, args.height, args.focal_length)
        scene.load_environment(str(args.environment), intensity=IBL_INTENSITY, rotation_deg=0)
        scene.environment_visible = args.skybox
        if args.sun:
            # Same SUN light as the viewer; the default disc and halo match its settings.
            scene.add_sun_light(direction=SUN_DIRECTION, intensity=SUN_INTENSITY, color=(1, 1, 1))
        camera = scene.create_camera()
        # The same call gltf_viewer makes, so both sides share one projection matrix.
        camera.set_lens_projection(focal_length_mm=camera_setup["focal_length_mm"], aspect=args.width / args.height,
                                   near=camera_setup["near"], far=camera_setup["far"])
        camera.position = camera_setup["eye"]
        camera.look_at(camera_setup["target"], up=camera_setup["up"])
        scene.camera = camera
        camera_setup["exposure_ev100"] = camera.exposure
        target = renderer.create_render_target(width=args.width, height=args.height)
        for _ in range(args.frames):
            renderer.render(scene, target)
        pixels = target.read()
        Image.fromarray(np.ascontiguousarray(pixels[:, :, :3])).save(out / "filly.png")
    (out / "camera.json").write_text(json.dumps(camera_setup, indent=2), encoding="utf-8")


def viewer_settings(camera, args):
    """Settings JSON for gltf_viewer --settings. Keys follow libs/viewer/src/Settings.cpp."""
    return {
        "view": {
            "antiAliasing": "FXAA" if args.antialiasing == "fxaa" else "NONE",
            "dithering": "NONE",
            "msaa": {"enabled": args.msaa > 1, "sampleCount": max(args.msaa, 1)},
            "taa": {"enabled": False},
            "ssao": {"enabled": False},
            "screenSpaceReflections": {"enabled": False},
            # MSAA off with bloom off makes the headless OpenGL gltf_viewer capture a quarter-size
            # image. Bloom at zero strength is identity in the tone-mapping pass and avoids it.
            "bloom": {"enabled": True, "strength": 0.0},
            "dof": {"enabled": False},
            "vignette": {"enabled": False},
            "fog": {"enabled": False},
            "dsr": {"enabled": False},
            "postProcessingEnabled": True,
            "colorGrading": {"enabled": True, "toneMapping": "ACES_LEGACY", "quality": "MEDIUM"},
        },
        "lighting": {"enableShadows": False, "enableSunlight": args.sun,
                     "sunlight": {"direction": list(SUN_DIRECTION), "color": [1.0, 1.0, 1.0],
                                  "intensity": SUN_INTENSITY, "castShadows": False},
                     "iblIntensity": IBL_INTENSITY, "iblRotation": 0.0},
        "viewer": {"autoScaleEnabled": False, "skyboxEnabled": args.skybox,
                   "groundPlaneEnabled": False, "backgroundColor": [0.0, 0.0, 0.0]},
        # FilamentApp2 reapplies setLensProjection(focalLength) after preRender every frame, so
        # the field of view must be expressed as a focal length; horizontalFov is ignored.
        "camera": {"enabled": True, "center": camera["eye"], "lookAt": camera["target"],
                   "up": camera["up"], "near": camera["near"], "far": camera["far"],
                   "focalLength": camera["focal_length_mm"], "aperture": 16.0,
                   "shutterSpeed": 125.0, "sensitivity": 100.0},
        "animation": {"enabled": False},
    }


def render_viewer(asset, camera, directory, args):
    from PIL import Image
    import numpy as np
    settings = directory / "viewer-settings.json"
    settings.write_text(json.dumps(viewer_settings(camera, args), indent=2), encoding="utf-8")
    tiff = directory / "viewer.tif"
    command = [str(VIEWER), "--api", "opengl", "--headless",
               "--window-size", f"{args.width + VIEWER_SIDEBAR}x{args.height}",
               "--frames", str(args.viewer_frames), "--ibl", str(args.environment),
               "--settings", str(settings), "--screenshot", str(tiff), str(asset)]
    if args.shaders == "precompiled":
        command.insert(1, "--ubershader")
    captures = []
    with (directory / "viewer.log").open("w", encoding="utf-8") as log:
        log.write(" ".join(command) + "\n")
        # Some headless captures contain a few corrupted rows. Two identical captures are
        # accepted; otherwise a per-pixel median of three removes a single bad capture.
        while len(captures) < 2 or (len(captures) == 2 and not np.array_equal(*captures)):
            tiff.unlink(missing_ok=True)
            log.flush()
            subprocess.run(command, cwd=VIEWER.parent, stdout=log, stderr=subprocess.STDOUT,
                           timeout=args.timeout, check=False)
            if not tiff.exists():
                raise RuntimeError("gltf_viewer wrote no screenshot; see viewer.log")
            captures.append(np.asarray(Image.open(tiff).convert("RGB")))
    pixels = captures[0] if len(captures) == 2 else np.median(captures, axis=0).astype(np.uint8)
    Image.fromarray(np.ascontiguousarray(pixels)).save(directory / "viewer.png")
    return len(captures)


def compare(directory, amplify):
    import numpy as np
    from PIL import Image
    reference = np.asarray(Image.open(directory / "viewer.png").convert("RGB"), dtype=np.int16)
    candidate = np.asarray(Image.open(directory / "filly.png").convert("RGB"), dtype=np.int16)
    if reference.shape != candidate.shape:
        raise RuntimeError(f"Size mismatch: viewer {reference.shape}, filly {candidate.shape}")
    error = np.abs(reference - candidate)
    per_pixel = error.max(axis=2)
    mse = float(np.mean(error.astype(np.float64) ** 2))
    diff = np.clip(per_pixel.astype(np.int32) * amplify, 0, 255).astype(np.uint8)
    Image.fromarray(diff).save(directory / "diff.png")
    strip = np.concatenate([reference, candidate, np.repeat(diff[..., None], 3, axis=2)], axis=1)
    Image.fromarray(strip.astype(np.uint8)).save(directory / "side-by-side.png")
    signed = (candidate - reference).reshape(-1, 3).mean(axis=0)
    return {"mae": round(float(error.mean()), 4), "max_error": int(error.max()),
            "frac_over_2": round(float((per_pixel > 2).mean()), 5),
            # None means identical images (infinite PSNR); strict JSON has no infinity.
            "psnr_db": round(10 * math.log10(255.0 ** 2 / mse), 2) if mse > 0 else None,
            "mean_signed_rgb": [round(float(v), 3) for v in signed]}


def run_asset(name, output, args):
    asset = find_asset(name)
    label = asset.stem if Path(name).suffix else name
    directory = output / label
    directory.mkdir(parents=True, exist_ok=True)
    result = {"asset": label, "path": str(asset)}
    started = time.perf_counter()
    try:
        if not args.rescore:
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(asset),
                       "--worker-output", str(directory), "--size", args.size, "--focal-length", str(args.focal_length),
                       "--environment", str(args.environment), "--antialiasing", args.antialiasing,
                       "--shaders", args.shaders, "--frames", str(args.frames), "--msaa", str(args.msaa),
                       "--skybox" if args.skybox else "--no-skybox"] + (["--sun"] if args.sun else [])
            with (directory / "filly.log").open("w", encoding="utf-8") as log:
                process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
            if process.returncode:
                raise RuntimeError(f"filly worker exit {process.returncode}; see filly.log")
        result["camera"] = json.loads((directory / "camera.json").read_text(encoding="utf-8"))
        if not args.rescore:
            result["viewer_captures"] = render_viewer(asset, result["camera"], directory, args)
            result["seconds"] = round(time.perf_counter() - started, 1)
        result.update(compare(directory, args.amplify), status="ok")
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", nargs="*", help="glTF/GLB paths or catalog names from docs/reference/sample-assets.csv")
    parser.add_argument("--catalog", action="store_true", help="Use every rendered asset in the catalog CSV")
    parser.add_argument("--size", default="512x512", help="Image size WIDTHxHEIGHT (default: 512x512)")
    parser.add_argument("--focal-length", type=float, default=FOCAL_LENGTH_MM,
                        help="Lens focal length in mm on a 24 mm sensor (default: gltf_viewer's 28)")
    parser.add_argument("--environment", default="studio",
                        help="'studio', 'uniform', or a path to an equirectangular .hdr")
    parser.add_argument("--skybox", action=argparse.BooleanOptionalAction, default=True,
                        help="Draw the environment behind the model on both sides")
    parser.add_argument("--sun", action="store_true",
                        help="Add gltf_viewer's default SUN light: its direction, 100000 lux, no shadows")
    parser.add_argument("--antialiasing", choices=("none", "fxaa"), default="none")
    parser.add_argument("--msaa", type=int, choices=(1, 4), default=1, help="MSAA sample count on both sides")
    parser.add_argument("--shaders", choices=("compiled", "precompiled"), default="compiled",
                        help="'precompiled' uses Renderer(precompiled_shaders=True) and passes --ubershader to gltf_viewer")
    parser.add_argument("--frames", type=int, default=3, help="filly frames before readback")
    parser.add_argument("--viewer-frames", type=int, default=60,
                        help="gltf_viewer frames before capture; asynchronous texture loads need several")
    parser.add_argument("--amplify", type=int, default=8, help="Scale factor for diff images")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path, default=DEPS / "reference-compare")
    parser.add_argument("--rescore", action="store_true",
                        help="Recompute metrics from images already in the output directory; do not render")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.width, args.height = (int(v) for v in args.size.lower().split("x"))

    if args.worker:
        args.environment = Path(args.environment)
        render_filly(args)
        return

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.environment in ("studio", "uniform"):
        args.environment = make_environment(args.environment, output)
    else:
        args.environment = Path(args.environment).resolve()
    names = list(args.assets)
    if args.catalog:
        with CATALOG.open(encoding="utf-8") as stream:
            names += [row["asset"] for row in csv.DictReader(stream) if row["status"] == "rendered"]
    if not names:
        parser.error("Give asset paths or names, or --catalog")

    tag = "-".join([args.environment.stem] + (["sun"] if args.sun else []) + ([] if args.skybox else ["noskybox"])
                   + ([args.antialiasing] if args.antialiasing != "none" else []) + ([f"msaa{args.msaa}"] if args.msaa > 1 else [])
                   + ([args.shaders] if args.shaders != "compiled" else []))
    config = {key: (str(value) if isinstance(value, Path) else value) for key, value in vars(args).items()
              if key not in ("assets", "worker", "worker_output", "output", "catalog")}
    stem = output / f"report-{tag}"
    # Later runs with the same settings replace rows for their assets and keep the others.
    previous = {}
    if stem.with_suffix(".json").exists():
        previous = {r["asset"]: r for r in json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))["results"]}
    results = []
    for name in names:
        result = run_asset(name, output / tag, args)
        if args.rescore:
            result = {**previous.get(result["asset"], {}), **result}
        results.append(result)
        summary = ", ".join(f"{k}={result[k]}" for k in ("mae", "max_error", "frac_over_2", "psnr_db") if k in result)
        print(f"{result['asset']}: {result['status']} {summary or result.get('error', '')}", flush=True)

    previous.update((r["asset"], r) for r in results)
    results = list(previous.values())
    stem.with_suffix(".json").write_text(json.dumps({"config": config, "results": results}, indent=2),
                                         encoding="utf-8")
    columns = ("asset", "status", "mae", "max_error", "frac_over_2", "psnr_db", "mean_signed_rgb", "error")
    with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"Report: {stem.with_suffix('.json')}")


if __name__ == "__main__":
    main()
