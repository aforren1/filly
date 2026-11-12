# Materials

This page explains how filly gets its shaders. filly has no material compiler at run time. Every
glTF material uses one entry of a material archive that the build compiles. For the history of
this design, the options that were compared, and the measurements, see
[material precompilation (archive)](../archive/material-precompilation.md).

## Why filly precompiles its materials

Filament's runtime material compiler, `filamat`, was half of the module: 7.0 of 15.1 MB. The
WebAssembly build of Filament does not include it, so online output could match local output
only with precompiled materials. A new material configuration also cost 0.1 to 0.35 s per asset
to compile.

gltfio's own precompiled archive does not cover filly. It has no slots for anisotropy,
iridescence, or diffuse-transmission textures, and its matching selected wrong entries for some
materials (see [upstream issue B2](../reference/upstream-issues.md#b2)). So filly builds its own
archive.

## Slot-indirected ubershaders

Each archive entry is an ubershader: one shader for a family of materials. A uniform turns each
feature on or off. The entry has five samplers for the core glTF textures (base color,
metallic-roughness, normal, occlusion, and emissive), with gltfio's names. Extension textures
share up to four generic samplers.

Each extension texture role, for example the clearcoat or the transmission texture, has three
uniforms:

- `<role>Slot`: the generic sampler of the texture, or -1 for no texture.
- `<role>Index`: the UV set.
- `<role>UvMatrix`: the texture transform.

A small shader function selects the sampler with a uniform branch. This is valid GLSL ES 3.00,
so the same archive serves the desktop and the web builds.

## Entries

The archive has 13 kinds of entries. Each kind has three packages, for the opaque, masked, and
fade blending modes: 39 packages in all.

| Kind | Lobes and inputs |
| --- | --- |
| `LitCore` | Core lobes and IOR. No extension textures. |
| `LitExtended` | As `LitCore`, plus clearcoat, sheen, and iridescence. |
| `LitSpecular` | As `LitExtended`, plus specular. |
| `LitAnisotropy` | As `LitSpecular`, plus anisotropy. |
| `RefractionThin` | As `LitExtended`, plus transmission and thin refraction. |
| `RefractionThinSpecular` | As `RefractionThin`, plus specular. |
| `RefractionThinAnisotropy` | As `RefractionThinSpecular`, plus anisotropy. |
| `RefractionSolid` | As `RefractionThin`, but solid, plus volume and dispersion. |
| `RefractionSolidSpecular` | As `RefractionSolid`, plus specular. |
| `RefractionSolidAnisotropy` | As `RefractionSolidSpecular`, plus anisotropy. |
| `SpecularGlossiness` | `KHR_materials_pbrSpecularGlossiness`. |
| `Unlit` | `KHR_materials_unlit`. |
| `DiffuseTransmission` | `KHR_materials_diffuse_transmission`. |

The entries are templates in `core/materials/`. `core/materials/materials.cmake` writes one
`.mat` file for each kind and blending mode, and compiles each with the SDK's `matc`. Then it
packs the packages with `uberz` and embeds the archive in the module.

## How a glTF material gets an entry

When filly loads an asset, `plan_archive_materials()` in `core/gltf_prepare.cpp` gives each glTF
material a plan: the entry, the generic sampler of each extension texture, and the image data
of those textures. The material provider in `core/archive_materials.cpp` then makes the material
instance, sets the defaults and factors, and binds the textures.

A material can have more extension textures than its entry has generic samplers. Then filly
drops the least important textures and issues an `AssetCompatibilityWarning` that names the
material and the dropped textures. The order of importance, from most to least important:
transmission, thickness, anisotropy, iridescence, clearcoat, sheen color, specular color,
specular, iridescence thickness, clearcoat roughness, sheen roughness, clearcoat normal. A map
that defines where an effect exists comes before a detail map: without a detail map, the effect
stays at its factor. No Khronos sample asset needs a drop.

Runtime texture assignments use the same slots, so they need no compile.

## Costs

- **GPU time.** An ubershader evaluates lobes whose strength is zero. At 1080p on Intel Iris Xe,
  a frame with archive materials took 11% (plain materials) to 36% (clearcoat, anisotropy) more
  GPU time than with the specialized runtime materials. This was measured before the removal of
  the runtime path. See
  [performance](../reference/performance.md).
- **Size.** The archive is about 150 KB compressed. The module shrank from 15.2 MB to 7.4 MB
  when the runtime compiler was removed.

## Maintenance on Filament upgrades

- Each material package carries Filament's material version. The engine rejects packages of
  another version, so the build compiles the archive with the `matc` of the same SDK. CMake
  checks that the SDK's material version matches `FILAMENT_VERSION`.
- The `.mat` templates use Filament's `MaterialInputs` fields, property names, and blending
  defines. Review them with the Filament release notes.
- The refraction hook in `core/refraction.h` depends on the name `sampler0_ssr` and on the order
  of the generated shader. The build fails if the optimized shader no longer calls the hook
  (`core/materials/check_refraction.cmake`).
- The provider depends on gltfio's texture parameter names for the core textures, and on the
  order of `createMaterialInstance()` and texture binding in gltfio's `AssetLoader`.
