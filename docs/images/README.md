# Screenshot assets

## Dielectric iridescence comparison

`iridescence-studio.png` and `iridescence-environment.png` are unedited 640 by 640 renderer
captures. Both use the same orthographic camera, panorama, exposure, and materials. Only the
100000-lux directional light is removed in the second image. These compare lighting settings,
not shader implementations.

The source is [IridescenceDielectricSpheres](https://github.com/KhronosGroup/glTF-Sample-Assets/tree/c6a6bd13ab2b3c685c7903d03561b8a9392f38b8/Models/IridescenceDielectricSpheres),
credited to Khronos, copyright 2019 Public, under CC0.

## Wooden playground horse

`horse.gif`, shown in the README, and `horse.png` are rendered by `examples/screenshot.py` with
filly. Run `uv run --no-sync python examples/screenshot.py --gif` for the animation and
`uv run --no-sync python examples/screenshot.py` for the still image.

The animation is one cycle of the horse's spring clip, 74 frames at 15 frames per second and
600 by 400 pixels, with a 20-degree yaw sway that has the same period, so it loops without a
jump. The script renders at twice that size, crops to the area the horse covers over the
whole cycle, and scales down. All frames share one 256-color palette, without error
diffusion, which keeps the file at about 2.2 MB.
The script requires Pillow, available through the `examples` extra. Two runs on the same machine
give byte-identical files.

The model is ["Wooden Playground Horse"](https://sketchfab.com/3d-models/wooden-playground-horse-aa1c440f48a44e558dcc9097b35a6a6d)
by [Batuhan13](https://sketchfab.com/Batuhan13), licensed under
[CC BY 4.0](http://creativecommons.org/licenses/by/4.0/). The author made it by following a 3dex
tutorial. `examples/assets/wooden_playground_horse.glb` is the unmodified Sketchfab download; see
[attribution](../../examples/assets/ATTRIBUTION.md).

The script frames the rest-pose box of `model.bounds` with `camera.frame(fit="box", fill=1.0)`
and a 30-degree perspective camera, from yaw 40 and pitch 12 degrees, and shows the `Armature|HorseSpring` clip at 1.25 seconds. It adds two directional lights
(the key light casts a shadow map), a small studio panorama, and FXAA. The PNG contains the
renderer's RGBA8 output without a display color transform or image retouching.

## Amber comparison

`amber-perspective.png` and `amber-orthographic.png` are unedited 512 by 512 captures from
`tools/benchmark_refraction.py`. Both use the same asset, lights, exposure, and tone mapping. The
perspective view has a 45-degree vertical field of view; the orthographic view has the same height
at the model. These show the renderer's two transmission filters, not conformance references.

The source is [MosquitoInAmber](https://github.com/KhronosGroup/glTF-Sample-Assets/tree/c6a6bd13ab2b3c685c7903d03561b8a9392f38b8/Models/MosquitoInAmber),
"Real-time Refraction Demo: Mosquito in Amber" by Sketchfab. Model: Loïc Norgeot; mosquito scan:
Geoffrey Marchal. The asset is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
[Original source](https://sketchfab.com/3d-models/real-time-refraction-demo-mosquito-in-amber-37233d6ed84844fea1ebe88069ea58d1).
