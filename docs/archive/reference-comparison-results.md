# gltf_viewer comparison results

These are the results of the comparisons of filly with Filament's `gltf_viewer` from September
2026, with Filament 1.77.1. They are a dated record and are not kept current. To run a
comparison, see [compare with gltf_viewer](../how-to/reference-comparison.md).

## Conditions

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

## Filament materials

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

### Expected divergence: volume thickness scale

MosquitoInAmber differs by design: studio MAE 3.07, maximum 175, 13.4% of pixels over 2,
PSNR 24.4 dB, in both material modes. Its amber mesh sits under a node with scale 0.1.
`KHR_materials_volume` scales thickness by the complete node transform, and filly does.
Filament 1.77.1 uses only the mesh node's own scale, which its source marks as a TODO, so
`gltf_viewer` renders the amber ten times too thick. A control build with Filament's scale
matched `gltf_viewer` at MAE 0.0000, maximum 1. Volume assets whose meshes have no scaled
parents, such as AttenuationTest and TransmissionTest, match.

## Other configurations (studio environment)

| Configuration | Assets | MAE range | Max error | Largest frac > 2 |
| --- | --- | --- | ---: | ---: |
| `--sun` | BoxTextured, MetalRoughSpheres, DamagedHelmet, WaterBottle, ClearCoatTest, SheenChair | 0 to 0.0001 | 1 | 0 |
| `--shaders precompiled` | DamagedHelmet, MetalRoughSpheres, ClearCoatTest, TransmissionTest, TransmissionRoughnessTest, AttenuationTest | Same as compiled | 2 | 0 |
| `--antialiasing fxaa --msaa 4` | BoxTextured, DamagedHelmet, MetalRoughSpheres | 0 to 0.0001 | 8 | 0 |

With precompiled shaders (filly's former `precompiled_shaders=True` and `gltf_viewer --ubershader`),
both sides produced the same metrics as with compiled shaders, including the MosquitoInAmber
divergence. For filly's material archive against `gltf_viewer`, see
[material precompilation](material-precompilation.md#parity-with-gltf_viewer).
With `--sun`, both sides use a SUN light. Before `add_sun_light()` existed, filly used a
directional light, and the specular highlights differed by up to 33.

## Extensions that filly implements outside gltfio

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

