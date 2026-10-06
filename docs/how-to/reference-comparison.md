# Compare with gltf_viewer

Use `tools/compare_reference.py` to compare filly output with Filament's `gltf_viewer`.
Both come from Filament 1.77.1. This is the only parity target that uses the same engine,
the same gltfio loader, and the same shaders. Khronos reference images use other renderers
and other lighting, so they cannot show pixel parity.

## Requirements

- A built filly package in `.venv`.
- `.deps/bin/gltf_viewer.exe` from the Filament 1.77.1 Windows release.
- Khronos sample assets in `.deps/sample-audit/assets`. To get them, run
  `uv run --no-sync python tools/check_sample_assets.py --download-only`.
- An OpenGL 4.5 driver. The tool renders both sides with OpenGL.

## Run a comparison

Give catalog names from `docs/reference/sample-assets.csv`, or paths to `.glb` or `.gltf` files:

```powershell
uv run --no-sync python tools/compare_reference.py BoxTextured DamagedHelmet IridescenceSuzanne
```

Use `--catalog` to compare all assets that the catalog lists as rendered.
For the glass assets, run:

```powershell
uv run --no-sync python tools/compare_reference.py TransmissionRoughnessTest MosquitoInAmber AttenuationTest TransmissionTest
```

Each asset takes 30 to 90 seconds, because `gltf_viewer` starts at least twice.

The tool writes to `.deps/reference-compare/<configuration>/<asset>/`:

| File | Content |
| --- | --- |
| `viewer.png` | `gltf_viewer` capture |
| `filly.png` | filly capture |
| `diff.png` | Maximum channel difference, multiplied by `--amplify` (default 8) |
| `side-by-side.png` | Viewer, filly, and difference, from left to right |
| `camera.json`, `viewer-settings.json` | The camera and the viewer settings for the run |
| `viewer.log`, `filly.log` | Process output |

It also writes `report-<configuration>.json` and `.csv` in the output directory. A later run
with the same configuration replaces the rows for its assets and keeps the other rows.
Use `--rescore` to recompute metrics from the saved images without a new render.

### Metrics

All metrics use 8-bit sRGB RGB values. Alpha is not compared.

| Metric | Definition |
| --- | --- |
| `mae` | Mean absolute difference over all pixels and channels |
| `max_error` | Largest channel difference |
| `frac_over_2` | Fraction of pixels whose largest channel difference is more than 2 |
| `psnr_db` | 10 log10(255² / MSE). An empty value means identical images. |
| `mean_signed_rgb` | Mean of filly minus viewer, per channel. Positive means filly is brighter. |

### Options

| Option | Effect |
| --- | --- |
| `--environment studio\|uniform\|PATH` | Environment panorama. `studio` and `uniform` are generated. |
| `--sun` | Add `gltf_viewer`'s default SUN light: its direction, 100,000 lux, no shadows. |
| `--antialiasing fxaa`, `--msaa 4` | Enable the same antialiasing on both sides. |
| `--shaders precompiled` | Run `gltf_viewer --ubershader`. filly always uses its material archive. |
| `--no-skybox` | Hide the environment on both sides. |
| `--size WxH`, `--focal-length MM` | Image size and lens focal length. The default is `gltf_viewer`'s 28 mm. |

## How the tool matches the two renderers

The settings come from the Filament source: `samples/gltf_viewer.cpp`,
`libs/filamentapp/src/FilamentApp2.cpp`, `libs/filamentapp/src/IBL.cpp`,
`libs/viewer/src/ViewerGui.cpp`, and `libs/viewer/src/Settings.cpp`.
The tool gives `gltf_viewer` a `--settings` JSON file. It does not use `--batch`.

| Item | gltf_viewer default | Comparison setting (both sides) |
| --- | --- | --- |
| Model transform | `fitIntoUnitCube(aabb, 4)` | Disabled (`viewer.autoScaleEnabled: false`). The model keeps its glTF coordinates. |
| Camera | Orbit manipulator, eye (0, 0, 0), target (0, 0, -4), 28 mm lens | `camera.frame()` on `model.bounds`, viewing along -(0.35, 0.25, 1). The bounding sphere is 1.05 times as far as where it would touch the narrower view axis. |
| Projection | `setLensProjection(28 mm)`, near 0.1, far 100 | `setLensProjection(28 mm)` on both sides; filly uses `Camera.set_lens_projection()`. Near and far come from the bounds. |
| Exposure | Aperture f/16, 1/125 s, ISO 100 | Same. This is also the filly default (EV100 14.97). |
| IBL | Prefiltered KTX `lightroom_14b` with 3-band SH | The same 2:1 Radiance HDR on both sides, 30,000 lux, rotation 0. Both use `IBLPrefilterContext`. |
| Skybox | Visible | Visible |
| Sun | SUN light, 100,000 lux, shadows | Off. With `--sun`: a SUN light on both sides (`Scene.add_sun_light()`), white, no shadows. |
| Tone mapping | ACES legacy, color grading quality MEDIUM | Same tone mapper. The tool sets filly `tone_mapping="aces_legacy"`; the filly default is the neutral `"linear"`. The viewer's color grading encodes sRGB; filly's color grading writes linear color, and filly's encode pass encodes sRGB. |
| Antialiasing | FXAA and MSAA 4x | None (default), or FXAA and MSAA 4x |
| SSAO, bloom, TAA, SSR, dithering | SSAO on, bloom on, dithering temporal | All off. Viewer bloom is on with strength 0. See the known issues. filly has `ssao`, `bloom`, and `dithering` options, but temporal dithering noise differs between processes. |
| Animation | Plays in interactive mode | Off. Both show the authored rest pose. |
| Image size | Window minus a 410 px sidebar | The viewer window is 410 px wider than the requested size. |

The viewer screenshot is an 8-bit TIFF. The tool converts it to PNG.

Two environment differences remain. They cause no measurable difference for the core materials below:

- filly generates mipmaps for the panorama before filtering. `gltf_viewer` uploads level 0 only.
- filly also gives `IndirectLight` an irradiance cubemap. The standard Filament shader
  does not sample it (`diffuseIrradiance()` in `shaders/src/surface_light_indirect.fs`). Without spherical harmonics, both sides
  get diffuse light from the roughest specular mip level. The filly custom diffuse-transmission
  material uses the cubemap for backlighting.

## Results

Run on September 29, 2026: Windows 11, Intel Iris Xe, OpenGL 4.5 driver 32.0.101.7088,
512 × 512 pixels, 28 mm lens, `precompiled_shaders=False` (filly's runtime material path, removed
on October 6, 2026), no antialiasing, skybox visible.
On the same day, after the change to explicit output encoding and scene option properties,
DamagedHelmet (MAE 0.0001, max error 1, PSNR 89.45 dB) and TransmissionTest (MAE 0.0000,
max error 1, PSNR 101.07 dB) in the studio environment gave the same metrics as below.

The `uniform` environment has constant radiance 1.0. This makes the result independent of the
environment filter. It isolates camera, exposure, tone mapping, and material differences.
The `studio` environment has a sky gradient, a ground color, and three bright sources at
different azimuths. A rotation or mirror error in the environment gives a large skybox and
reflection difference.

### Filament materials

| Asset | Uniform MAE | Uniform max | Studio MAE | Studio max | Studio frac > 2 | Studio PSNR (dB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BoxTextured | 0.0000 | 1 | 0.0000 | 1 | 0 | 92.6 |
| MetalRoughSpheres | 0 | 0 | 0.0000 | 1 | 0 | 102.3 |
| MetalRoughSpheresNoTextures | 0 | 0 | 0.0000 | 1 | 0 | 107.1 |
| DamagedHelmet | 0 | 0 | 0.0001 | 1 | 0 | 89.5 |
| Duck | 0 | 0 | 0 | 0 | 0 | identical |
| WaterBottle | 0 | 0 | 0.0000 | 1 | 0 | 107.1 |
| NormalTangentMirrorTest | 0 | 0 | 0 | 0 | 0 | identical |
| TextureCoordinateTest | 0 | 0 | 0 | 0 | 0 | identical |
| OrientationTest | 0.0001 | 1 | 0.0000 | 1 | 0 | 104.1 |
| AlphaBlendModeTest | 0.0000 | 1 | 0.0002 | 1 | 0 | 86.3 |
| ClearCoatTest | 0 | 0 | 0 | 0 | 0 | identical |
| SheenChair | 0 | 0 | 0.0000 | 1 | 0 | 107.1 |
| SheenCloth | | | 0.0000 | 1 | 0 | 107.1 |
| SpecularTest | | | 0 | 0 | 0 | identical |
| EmissiveStrengthTest | | | 0.0998 | 32 | 0.0119 | 45.8 (intended, see below) |
| TextureTransformTest | | | 0 | 0 | 0 | identical |
| TransmissionTest | 0.0000 | 1 | 0.0000 | 1 | 0 | 101.1 |
| TransmissionRoughnessTest | | | 0.0000 | 1 | 0 | 107.1 |
| AttenuationTest | | | 0.0004 | 2 | 0 | 82.4 |

**Emissive strength (September 30, 2026).** filly applies `KHR_materials_emissive_strength` once, as
the extension specifies. gltfio 1.77.1, and therefore `gltf_viewer`, applies it twice. Materials
with a strength other than 1 are therefore darker in filly than in `gltf_viewer`. Measured with
`--environment studio` at 512 x 512: EmissiveStrengthTest MAE 0.0998, max 32, 1.2% of pixels over
2/255; CompareEmissiveStrength MAE 0.549, max 87, 3.1% over 2/255. DamagedHelmet in the same run is
identical to `gltf_viewer`.


These assets match. Camera, projection, exposure, environment orientation, skybox, UV
orientation, tangent frames, alpha modes, tone mapping, transmission, and volume agree with
`gltf_viewer`. Transmission and volume materials come from the filly shader generator in
both modes, but for perspective cameras their shading is Filament's own. The volume assets above
have no scaled parent nodes, so the different thickness scale below does not affect them.
Differences of 1 remain in some pixels. The source of these is not isolated. The viewer
screenshot converts sRGB to linear and back to 8 bits, and the two processes use different
render targets.

The glass assets diverged before the September 29 changes. The earlier filly transmission
filter used a projection-derived footprint and a volume travel distance. It now uses Filament's
filter for perspective cameras:

| Asset | Studio MAE before | Max before | MAE after | Max after |
| --- | ---: | ---: | ---: | ---: |
| TransmissionRoughnessTest | 1.18 | 101 | 0.0000 | 1 |
| AttenuationTest | 0.0016 | 3 | 0.0004 | 2 |
| TransmissionTest | 0.0090 | 15 | 0.0000 | 1 |
| MosquitoInAmber | 3.13 | 176 | 3.07 | 175 |

#### Expected divergence: volume thickness scale

MosquitoInAmber differs by design: studio MAE 3.07, maximum 175, 13.4% of pixels over 2,
PSNR 24.4 dB, in both material modes. Its amber mesh sits under a node with scale 0.1.
`KHR_materials_volume` scales thickness by the complete node transform, and filly does.
Filament 1.77.1 uses only the mesh node's own scale, which its source marks as a TODO, so
`gltf_viewer` renders the amber ten times too thick. A control build with Filament's scale
matched `gltf_viewer` at MAE 0.0000, maximum 1. Volume assets whose meshes have no scaled
parents, such as AttenuationTest and TransmissionTest, match.

### Other configurations (studio environment)

| Configuration | Assets | MAE range | Max error | Largest frac > 2 |
| --- | --- | --- | ---: | ---: |
| `--sun` | BoxTextured, MetalRoughSpheres, DamagedHelmet, WaterBottle, ClearCoatTest, SheenChair | 0 to 0.0001 | 1 | 0 |
| `--shaders precompiled` | DamagedHelmet, MetalRoughSpheres, ClearCoatTest, TransmissionTest, TransmissionRoughnessTest, AttenuationTest | Same as compiled | 2 | 0 |
| `--antialiasing fxaa --msaa 4` | BoxTextured, DamagedHelmet, MetalRoughSpheres | 0 to 0.0001 | 8 | 0 |

With precompiled shaders (filly's former `precompiled_shaders=True` and `gltf_viewer --ubershader`),
both sides produced the same metrics as with compiled shaders, including the MosquitoInAmber
divergence. For filly's material archive against `gltf_viewer`, see
[material precompilation](../explanation/material-precompilation.md#parity-with-gltf_viewer).
With `--sun`, both sides use a SUN light. Before `add_sun_light()` existed, filly used a
directional light, and the specular highlights differed by up to 33.

### Extensions that filly implements outside gltfio

gltfio 1.77.1 ignores `KHR_materials_iridescence`, `KHR_materials_anisotropy`, and
`KHR_materials_diffuse_transmission`. filly renders them with its own materials.
For these assets `gltf_viewer` is not a correct reference. The numbers show how much
filly changes the stock result.

| Asset | Studio MAE | Max | Frac > 2 | PSNR (dB) | Cause |
| --- | ---: | ---: | ---: | ---: | --- |
| IridescenceSuzanne | 0.81 | 110 | 0.051 | 33.0 | Iridescence and glass materials. The plain Suzanne matches. |
| IridescenceDielectricSpheres | 0.35 | 49 | 0.077 | 45.8 | filly iridescence |
| AnisotropyStrengthTest | 1.20 | 115 | 0.091 | 33.1 | gltfio renders the spheres as isotropic |
| DiffuseTransmissionTest | 6.61 | 204 | 0.127 | 20.3 | gltfio ignores diffuse transmission |

## Known issues in gltf_viewer

These issues affect the reference side. The tool contains a workaround for each.

- With MSAA and bloom both off, the headless OpenGL capture contains a quarter-size image in
  the lower-left corner. The rest is black. The tool enables bloom with strength 0 instead.
  This gives the same pixels as filly without bloom.
- Some headless captures contain a band of about 10 corrupted rows. This occurred in 2 of 4
  BoxTextured captures in one test, and in none of the later comparison runs. The tool captures twice. If the two captures differ, it captures a third
  time and uses the per-pixel median.
- The 410 px ImGui sidebar is reserved in headless mode too.
- `FilamentApp2` calls `setLensProjection(focalLength)` after `preRender` in every frame. Thus
  `camera.horizontalFov` in the settings has no effect, and `focalLength` must be positive.
  The `horizontalFov` field is passed to Filament as a vertical field of view.
- `gltf_viewer` cannot select an imported glTF camera from the settings file.

## Limits of this comparison

- Imported cameras, the default unit-cube fit, and filly's own camera fitting are not tested.
  Both sides get the same explicit camera.
- The default `gltf_viewer` scene is not tested. It uses the prefiltered KTX environment
  `lightroom_14b`, a SUN light with shadows, SSAO, bloom, and temporal dithering. filly can
  load that environment with `load_environment_ktx()`, but it uses the file's spherical harmonics
  for diffuse light and `gltf_viewer` 1.77.1 does not, so diffuse lighting differs. Dithering noise
  also differs between the two processes.
- Shadows, animation, skinning, morph targets, and material variants are not compared.
  Only IridescenceSuzanne has punctual lights, and its plain Suzanne matches.
- The generated panoramas are 256 × 512 pixels. Higher-frequency environments can show filter
  differences that these panoramas do not show.
- Only one GPU and driver were used.
