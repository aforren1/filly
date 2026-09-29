# Screenshot assets

## Dielectric iridescence comparison

`iridescence-studio.png` and `iridescence-environment.png` are unedited 640 by 640 renderer
captures. Both use the same orthographic camera, panorama, exposure, and materials. Only the
100000-lux directional light is removed in the second image. These compare lighting settings,
not shader implementations.

The source is [IridescenceDielectricSpheres](https://github.com/KhronosGroup/glTF-Sample-Assets/tree/c6a6bd13ab2b3c685c7903d03561b8a9392f38b8/Models/IridescenceDielectricSpheres),
credited to Khronos, copyright 2019 Public, under CC0.

## Suzanne

`suzanne.png` is rendered by `examples/screenshot.py` with filly.
Run `uv run --no-sync python examples/screenshot.py` to generate it.
The script requires Pillow, available through the `examples` extra.

The mesh is extracted from [Iridescence Suzanne](https://github.com/KhronosGroup/glTF-Sample-Assets/tree/c6a6bd13ab2b3c685c7903d03561b8a9392f38b8/Models/IridescenceSuzanne).
The source asset is CC0, credited to UX3D (2022) and Pascal Schoen (2021).
The script checks the source hash, selects one mesh, removes the example lights and iridescent
materials, and assigns a plain PBR material. The PNG contains the renderer's RGBA8 output without
a display color transform or image retouching.

## Amber comparison

`amber-perspective.png` and `amber-orthographic.png` are unedited 512 by 512 captures from
`tools/benchmark_refraction.py`. Both use the same asset, lights, exposure, and tone mapping. The
perspective view has a 45-degree vertical field of view; the orthographic view has the same height
at the model. These show the renderer's two transmission filters, not conformance references.

The source is [MosquitoInAmber](https://github.com/KhronosGroup/glTF-Sample-Assets/tree/c6a6bd13ab2b3c685c7903d03561b8a9392f38b8/Models/MosquitoInAmber),
"Real-time Refraction Demo: Mosquito in Amber" by Sketchfab. Model: Loïc Norgeot; mosquito scan:
Geoffrey Marchal. The asset is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
[Original source](https://sketchfab.com/3d-models/real-time-refraction-demo-mosquito-in-amber-37233d6ed84844fea1ebe88069ea58d1).
