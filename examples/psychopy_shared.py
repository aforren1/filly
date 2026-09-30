"""Mix a shared glTF/GLB render with a regular PsychoPy text overlay."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from psychopy import core, event, visual

import filly
from filly.integrations.psychopy import SharedTarget, create_renderer
from screenshot import HORSE, VIEW, studio_environment, view_direction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", type=Path,
                        help="glTF or GLB file to display (default: the wooden horse)")
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--lighting", choices=["auto", "studio", "asset", "environment"], default="auto",
                        help="auto uses authored lights when present; environment uses only the panorama")
    parser.add_argument("--exposure", type=float, help="Camera exposure in EV100; lower is brighter")
    parser.add_argument("--environment", type=Path, help="2:1 Radiance HDR panorama")
    parser.add_argument("--environment-intensity", type=float, help="Environment intensity in lux")
    parser.add_argument("--show-environment", action="store_true", help="Draw the environment panorama behind the model")
    parser.add_argument("--environment-rotation", type=float, default=0, help="Environment rotation about Y in degrees")
    parser.add_argument("--shadows", action="store_true", help="Enable shadow maps for the selected lights")
    parser.add_argument("--shadow-map-size", type=int, default=1024, help="Shadow resolution per face, a power of two from 8 to 4096")
    parser.add_argument("--animation", type=int, default=0, help="Animation clip index")
    parser.add_argument("--no-animation", action="store_true")
    parser.add_argument("--time", type=float, help="Show one animation time in seconds instead of playing")
    parser.add_argument("--variant", help="Material variant name or index")
    parser.add_argument("--camera", help="Imported camera: its node name or glTF node index "
                        "(default: first imported camera, otherwise fit)")
    parser.add_argument("--fit-camera", action="store_true", help="Fit the complete model instead of using its imported camera")
    parser.add_argument("--view", type=float, nargs=2, metavar=("YAW", "PITCH"),
                        help="Fitted camera angle in degrees (default: 0 0 for files, 40 12 for the horse)")
    parser.add_argument("--projection", choices=["perspective", "orthographic"],
                        help="Fitted camera projection (default: perspective)")
    parser.add_argument("--background", type=float, nargs=3, metavar=("R", "G", "B"),
                        default=(0.025, 0.035, 0.055), help="Linear RGB background in [0, 1]; brighter colors help inspect glass")
    parser.add_argument("--spin", type=float, help="Whole-model spin in degrees/second (custom GLBs default to 0)")
    parser.add_argument("--transparent", action="store_true", help="Composite the model over regular PsychoPy drawing")
    parser.add_argument("--profile", type=Path, help="Write per-frame CPU timings to this CSV file")
    parser.add_argument("--warmup", type=int, default=30, help="Frames excluded from the timing summary")
    args = parser.parse_args()
    if args.model is not None and not args.model.is_file():
        parser.error(f"Model file not found: {args.model}")
    if args.frames <= 0:
        parser.error("--frames must be positive")
    if args.warmup < 0 or (args.profile and args.warmup >= args.frames):
        parser.error("--warmup must be nonnegative and less than --frames when profiling")
    if args.environment is not None and not args.environment.is_file():
        parser.error(f"Environment file not found: {args.environment}")
    if args.time is not None and (not np.isfinite(args.time) or args.time < 0):
        parser.error("--time must be finite and nonnegative")
    if args.exposure is not None and not -10 <= args.exposure <= 24:
        parser.error("--exposure must be between -10 and 24 EV100")
    if args.environment_intensity is not None and (not np.isfinite(args.environment_intensity) or args.environment_intensity < 0):
        parser.error("--environment-intensity must be finite and nonnegative")
    if not np.isfinite(args.environment_rotation):
        parser.error("--environment-rotation must be finite")
    if args.shadow_map_size<8 or args.shadow_map_size>4096 or args.shadow_map_size & (args.shadow_map_size-1):
        parser.error("--shadow-map-size must be a power of two from 8 to 4096")
    if args.show_environment and args.transparent:
        parser.error("--show-environment draws an opaque background; omit --transparent")
    if args.spin is not None and not np.isfinite(args.spin):
        parser.error("--spin must be finite")
    if not all(np.isfinite(value) and 0 <= value <= 1 for value in args.background):
        parser.error("--background components must be finite and between 0 and 1")
    if args.camera and args.projection:
        parser.error("--projection applies to a fitted camera; omit --camera to use it")
    if args.camera and args.fit_camera:
        parser.error("--camera and --fit-camera are mutually exclusive")
    if args.view is not None:
        if not all(np.isfinite(value) for value in args.view) or not -90 < args.view[1] < 90:
            parser.error("--view requires finite angles and -90 < pitch < 90 degrees")
        if args.camera:
            parser.error("--view applies to a fitted camera; omit --camera to use it")
    filly.set_log_level("warning")
    # PsychoPy 2026.2.4 with pyglet 1.4.11 does not present stimuli with useFBO=True on Intel
    # or NVIDIA, with or without Filament. False is PsychoPy's default.
    win_size = (1280, 720)
    win = visual.Window(size=win_size, units="pix", winType="pyglet", useFBO=False,
                        checkTiming=False, autoLog=False, color=(-0.95, -0.93, -0.89))
    # Present the startup background before the timed stimulus loop.
    win.flip()
    label = visual.TextBox2(win, text=args.model.stem if args.model else "Wooden Playground Horse",
                            units="pix", pos=(-456, 296),
                            letterHeight=28, color="white", anchor="top-left",
                            alignment="left", autoLog=False)
    renderer = create_renderer(win)
    scene = renderer.create_scene()
    scene.background = (0, 0, 0, 0) if args.transparent else (*args.background, 1)
    scene.refraction = True
    scene.antialiasing = "fxaa"
    scene.transparent = args.transparent
    scene.shadows = args.shadows
    scene.environment_visible = args.show_environment
    underlay = visual.GratingStim(win, tex="sin", mask=None, size=win.size, sf=0.012,
                                 contrast=0.15, autoLog=False) if args.transparent else None
    model = scene.load(args.model or HORSE)
    bounds = np.asarray(model.bounds, dtype=float)
    center = bounds.mean(axis=0)
    radius = np.linalg.norm(bounds[1] - bounds[0]) / 2
    if not np.isfinite(bounds).all() or not np.isfinite(radius) or radius <= 0:
        renderer.close()
        win.close()
        parser.error("The GLB must contain geometry with finite, nonzero bounds")

    # Frame with the camera instead of scaling geometry, which changes point-light attenuation.
    fitted = args.fit_camera or args.projection or args.view
    camera_key = args.camera
    if camera_key is None and model.cameras and not fitted:
        camera_key = model.cameras[0].node.index
    elif camera_key is not None and camera_key.isdigit():
        camera_key = int(camera_key)
    if camera_key is not None:
        try:
            camera = model.camera(camera_key)
        except filly.AssetError as error:
            available = [(c.node.index, c.node.name) for c in model.cameras]
            renderer.close()
            win.close()
            parser.error(f"{error}; camera nodes (index, name): {available}")
    else:
        camera = scene.create_camera()
        # The projection follows the render target; frame() sets the clipping planes.
        if (args.projection or "perspective") == "perspective":
            camera.set_perspective(fov_y=45, near=1, far=2)
        else:
            camera.set_orthographic(height=1, near=1, far=2)
        # The loop keeps the asset's center at the origin while it spins; frame it there. The
        # bounding sphere spans 85% of the window height at every rotation.
        model.position = -center
        camera.frame(model, fill=0.85, direction=view_direction(*(args.view or ((0, 0) if args.model else VIEW))),
                     aspect=win_size[0] / win_size[1])
    scene.camera = camera
    lights = model.lights
    use_asset_lights = args.lighting == "asset" or (args.lighting == "auto" and bool(lights))
    if not use_asset_lights:
        for light in lights:
            light.intensity = 0
        lights = [] if args.lighting == "environment" else [scene.add_directional_light(direction=(-1, -1, -2), intensity=100000)]
    if args.shadows:
        for light in lights:
            light.set_shadow_options(map_size=args.shadow_map_size)
            light.casts_shadows = True
    camera.exposure = args.exposure if args.exposure is not None else (0 if use_asset_lights else 15)
    intensity = args.environment_intensity
    if intensity is None:
        intensity = (1 if args.show_environment else 0) if use_asset_lights else 30000
    if args.environment:
        scene.load_environment(args.environment, intensity=intensity, rotation_deg=args.environment_rotation)
    elif intensity > 0:
        scene.set_environment(studio_environment(), intensity=intensity, rotation_deg=args.environment_rotation)

    if args.variant is not None:
        variants = model.variants
        try:
            variant = variants.index(args.variant) if args.variant in variants else int(args.variant)
            if not 0 <= variant < len(variants):
                raise ValueError
        except ValueError:
            renderer.close()
            win.close()
            parser.error(f"Unknown variant {args.variant!r}; available variants: {variants}")
        model.apply_variant(variant)
    clips = model.animations
    if clips and not args.no_animation and not 0 <= args.animation < len(clips):
        renderer.close()
        win.close()
        parser.error(f"--animation must be between 0 and {len(clips)-1}")
    if clips:
        print("Animations:", [(i, clip.name, clip.duration) for i, clip in enumerate(clips)])
    if model.variants:
        print("Variants:", model.variants)
    target = SharedTarget(renderer, win, win.size[0], win.size[1])
    stimulus = target.as_psychopy_texture(win)
    clock = core.Clock()
    spin = args.spin if args.spin is not None else (0 if args.model else 30)
    timings = np.empty((args.frames, 7)) if args.profile else None
    recorded = 0
    previous_flip = None

    for frame in range(args.frames):
        if event.getKeys(keyList=["escape"]):
            break
        seconds = clock.getTime()
        if camera_key is None:
            model.rotation_euler_deg = (0, seconds*spin, 0)
            # Rotate around the asset's center, which need not coincide with its origin.
            model.position = -model.transform[:3, :3] @ center
        if clips and not args.no_animation:
            model.apply_animation(args.animation, args.time if args.time is not None else seconds)
        renderer.render(scene, target)
        if args.profile:
            draw_start = perf_counter()
        if underlay is not None:
            underlay.draw()
        stimulus.draw()
        # Draw the overlay last so the shared image does not cover it.
        label.draw()
        if args.profile:
            flip_start = perf_counter()
        win.flip()
        if args.profile:
            flipped = perf_counter()
            stats = renderer.stats
            timings[recorded] = (frame, stats.cpu_submit_ms, stats.host_wait_ms, stats.host_release_ms,
                                 (flip_start-draw_start)*1000, (flipped-flip_start)*1000,
                                 (flipped-previous_flip)*1000 if previous_flip is not None else np.nan)
            previous_flip = flipped
            recorded += 1

    # Shared GPU resources must be released while the host window is still open.
    target.close()
    renderer.close()
    win.close()
    if args.profile:
        names = ("frame", "cpu_submit_ms", "host_wait_ms", "host_release_ms", "host_draw_ms", "flip_ms", "flip_interval_ms")
        np.savetxt(args.profile, timings[:recorded], delimiter=",", header=",".join(names), comments="")
        measured = timings[args.warmup:recorded]
        summary = {"frames": recorded, "warmup": args.warmup, "csv": str(args.profile)}
        if len(measured):
            summary.update({name: dict(zip(("p50", "p95", "p99"), np.nanpercentile(measured[:, i], [50, 95, 99])))
                            for i, name in enumerate(names) if i})
        print(json.dumps(summary, indent=2))
    core.quit()


if __name__ == "__main__":
    main()
