"""Render a reproducible image of the wooden playground horse for the README.

The horse is "Wooden Playground Horse" by Batuhan13, CC BY 4.0; see assets/ATTRIBUTION.md.
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

import filly


ROOT = Path(__file__).resolve().parents[1]
HORSE = Path(__file__).resolve().parent / "assets" / "wooden_playground_horse.glb"
# The horse's only clip, a spring rock. At this time it leans back with its head up.
POSE_SECONDS = 1.25
# Camera yaw and pitch in degrees. Yaw 0 looks at the horse's face; 40 is a three-quarter view.
VIEW = (40, 12)


def studio_environment():
    """A small linear HDR panorama with softbox reflections and a dim room."""
    panorama = np.full((64, 128, 3), 0.12, dtype=np.float32)
    panorama[8:30, 12:28] = (3.0, 2.8, 2.5)
    panorama[12:40, 84:100] = (1.5, 1.8, 2.2)
    return panorama


def view_direction(yaw_deg, pitch_deg):
    """The viewing direction of a camera at this yaw and pitch around the target."""
    yaw, pitch = np.deg2rad([yaw_deg, pitch_deg])
    return -np.array([np.sin(yaw) * np.cos(pitch), np.sin(pitch), np.cos(yaw) * np.cos(pitch)])


def animate(model, seconds, spin=0.0, yaw=None):
    """Play the first clip and turn the model about the vertical axis through its center.

    The turn is `seconds * spin` degrees, or `yaw` degrees when it is given.
    """
    low, high = np.asarray(model.bounds, dtype=float)
    center = (low + high) / 2
    if model.animations:
        model.apply_animation(0, seconds)
    model.rotation_euler_deg = (0, seconds * spin if yaw is None else yaw, 0)
    # Rotate about the center, which is not the asset's origin, so the framing stays valid.
    model.position = center - model.transform[:3, :3] @ center


def setup_scene(renderer, aspect=1.5, source=None, spin=False):
    """Load the horse (or `source`) and frame it from the VIEW direction.

    A still image fits the box tightly. With `spin`, the bounding sphere keeps the whole model in
    view at every rotation.
    """
    scene = renderer.create_scene()
    scene.background = (0.025, 0.035, 0.055, 1)
    scene.antialiasing = "fxaa"
    scene.shadows = True
    model = scene.load(HORSE if source is None else source)
    camera = scene.create_camera()
    # frame() keeps the field of view and sets the clipping planes. It moves the camera, not the
    # model: the horse is about 8.6 units tall, and scaling it would change its lighting.
    camera.set_perspective(fov_y=30, near=1, far=2)
    if spin:
        camera.frame(model, fill=1.0, direction=view_direction(*VIEW), aspect=aspect)
    else:
        camera.frame(model, fill=1.0, direction=view_direction(*VIEW), fit="box", aspect=aspect)
    scene.camera = camera
    key = scene.add_directional_light(direction=(-1, -1.5, -1), intensity=110000,
                                      color=(1, 0.92, 0.82))
    key.casts_shadows = True
    scene.add_directional_light(direction=(1, -0.3, -1), intensity=30000, color=(0.55, 0.75, 1))
    scene.set_environment(studio_environment(), intensity=20000)
    return scene, model


def save_gif(output, width, height, fps, sway):
    """Render one cycle of the spring clip as a looping GIF.

    The yaw sways by `sway` degrees with the same period as the clip, so the last frame leads
    back into the first. One palette for all frames keeps the colors from flickering. Error
    diffusion is off: it made no visible difference at this size and cost 30% more bytes.
    """
    # The sphere fit leaves room for the sway and the rock, so render larger and crop to the area
    # the horse covers over the whole cycle. One crop for all frames keeps the loop steady.
    scale = 2
    with filly.Renderer() as renderer:
        scene, model = setup_scene(renderer, width / height, spin=True)
        period = model.animations[0].duration
        count = max(2, round(period * fps))
        target = renderer.create_render_target(width=width * scale, height=height * scale)
        rendered = []
        for index in range(count):
            seconds = period * index / count
            animate(model, seconds, yaw=sway * np.sin(2 * np.pi * index / count))
            renderer.render(scene, target)
            rendered.append(target.read()[..., :3])
    background = rendered[0][0, 0].astype(int)
    covered = np.zeros(rendered[0].shape[:2], dtype=bool)
    for image in rendered:
        covered |= np.abs(image.astype(int) - background).max(axis=2) > 2
    rows, cols = np.nonzero(covered)
    box_w, box_h = cols.max() - cols.min() + 1, rows.max() - rows.min() + 1
    # 8% margin, then widen or heighten the box to the output aspect, inside the render.
    crop_w = max(box_w, box_h * width / height) * 1.08
    crop_w = min(crop_w, width * scale, height * scale * width / height)
    crop_h = crop_w * height / width
    center_x, center_y = (cols.min() + cols.max()) / 2, (rows.min() + rows.max()) / 2
    left = int(round(np.clip(center_x - crop_w / 2, 0, width * scale - crop_w)))
    top = int(round(np.clip(center_y - crop_h / 2, 0, height * scale - crop_h)))
    crop = (left, top, left + int(round(crop_w)), top + int(round(crop_h)))
    frames = [Image.fromarray(image).crop(crop).resize((width, height), Image.Resampling.LANCZOS)
              for image in rendered]
    # Build the shared palette from a strip of frames spread over the cycle.
    sample = frames[:: max(1, count // 8)]
    strip = Image.new("RGB", (width, height * len(sample)))
    for row, frame in enumerate(sample):
        strip.paste(frame, (0, row * height))
    palette = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    output.parent.mkdir(parents=True, exist_ok=True)
    # GIF delays are in 10 ms steps. Round the clip's frame interval to the nearest step, so the
    # loop plays within 5% of the authored speed.
    delay = max(10, int(round(period / count * 100)) * 10)
    indexed[0].save(output, save_all=True, append_images=indexed[1:], duration=delay,
                    loop=0, optimize=True, disposal=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=None,
                        help="Default: docs/images/horse.png, or horse.gif with --gif")
    parser.add_argument("--width", type=int, default=None, help="Default: 1200, or 600 with --gif")
    parser.add_argument("--height", type=int, default=None, help="Default: 800, or 400 with --gif")
    parser.add_argument("--time", type=float, default=POSE_SECONDS, help="Pose time in seconds")
    parser.add_argument("--gif", action="store_true", help="Render one looping cycle of the animation")
    parser.add_argument("--fps", type=int, default=15, help="GIF frame rate")
    parser.add_argument("--sway", type=float, default=20.0, help="GIF yaw sway in degrees")
    args = parser.parse_args()
    args.width = args.width or (600 if args.gif else 1200)
    args.height = args.height or (400 if args.gif else 800)
    args.output = args.output or ROOT / "docs/images" / ("horse.gif" if args.gif else "horse.png")
    if args.width <= 0 or args.height <= 0:
        parser.error("Dimensions must be positive")
    if not np.isfinite(args.time) or args.time < 0:
        parser.error("--time must be finite and nonnegative")
    if not 1 <= args.fps <= 50 or not np.isfinite(args.sway):
        parser.error("--fps must be from 1 through 50 and --sway finite")
    if args.gif:
        save_gif(args.output, args.width, args.height, args.fps, args.sway)
        print(args.output)
        return
    with filly.Renderer() as renderer:
        scene, model = setup_scene(renderer, args.width / args.height)
        animate(model, args.time)
        target = renderer.create_render_target(width=args.width, height=args.height)
        renderer.render(scene, target)
        image = target.read()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
