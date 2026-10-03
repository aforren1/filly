# Material precompilation

This document answers one question: can filly remove Filament's runtime material compiler
(`filamat` and its shader toolchain) and ship precompiled materials instead?

Short answer: yes, but not with gltfio's ubershader archive as it is. filly needs its own
archive of about 30 ubershaders, built at build time with the SDK's `matc` and `uberz`, and its
own material provider. Extension textures must share generic sampler slots. With that design,
every material in the 150 Khronos sample assets fits without a feature reduction, the module
shrinks by about half, and the same archive can serve the web build.

Every number in this document has a label: *measured* (with the method in
[Experiments](#experiments)) or *estimate*. Measurements are from Filament 1.77.1 on the Intel
Iris Xe test machine, unless a section states otherwise.

Status (September 29, 2026): phases 1 to 3 are implemented. The archive path is the CMake option
`FILLY_MATERIALS=archive`; the default build stays on the runtime path until the owner has
reviewed the results. See [Phase 1 to 3 results](#phase-1-to-3-results-september-29-2026).

## Why this matters

| Reason | Fact |
| --- | --- |
| Module size | The Windows module is 15,103,488 bytes. A linker map attributes 7.04 MB to `filamat`, 3.07 MB to `filament`, 1.49 MB to `basis_transcoder`, 0.71 MB to `uberarchive`, and 0.62 MB to `backend`. *Measured.* |
| Online parity | The planned PsychoPy online component would use Filament's WebAssembly build. That build does not link `filamat`, so it cannot compile materials. Only precompiled materials can make online output match local output. |
| Load latency | A new material configuration costs 0.1 to 0.35 s per asset, most of it in `filamat`. |
| Other frontends | The planned MATLAB frontend links the same core. `filamat` (glslang, SPIRV-Tools, SPIRV-Cross) is the largest part of any port, for example a MinGW build for Octave. |

## What filly compiles at run time today

The core calls `filamat::MaterialBuilder` in two places and uses gltfio's `JitShaderProvider` in a
third. The list below is complete for the current source.

| Source | What it compiles | When |
| --- | --- | --- |
| `native/surface_material.cpp` | An adapted copy of gltfio's `JitShaderProvider` generator: one material per `MaterialKey` and UV layout. It adds anisotropy and iridescence (with three extra samplers, the "extended" material), a model-to-world volume thickness scale (`CUSTOM0` vertex variable), alpha 1 for unlit `OPAQUE`, and the orthographic refraction LOD hook from `native/refraction.h`. It compiles with `Optimization::NONE`. | Transmission and volume materials, unlit `OPAQUE` materials, anisotropy and iridescence materials, in both shader modes. With `precompiled_shaders=True`, also every key that the SDK archive does not match exactly. |
| `native/materials.cpp`, `diffuse_material()` | The diffuse-transmission material (`KHR_materials_diffuse_transmission`): custom surface shading, a backlight cubemap, 8 2D samplers and 1 cube sampler. One material per alpha mode. | Assets with that extension. |
| gltfio `JitShaderProvider` | All other glTF keys. | Default mode (`precompiled_shaders=False`). |
| `native/slots.inc`, `assign_texture()` | A new variant of the glTF key with a base color or emissive slot and texture transforms added. | The first runtime texture assignment to a material that lacks the slot. |
| `Scene.create_mesh()` | A one-triangle glTF placeholder with the requested factors, so the same provider path as a loaded asset. | Each new combination of unlit, alpha mode, double-sided, and vertex colors. |

Features that do not compile materials: fog (a Filament variant, present in every package), the
output paths and color grading (view and post-process state; filly's encode and FXAA materials
are precompiled at build time since October 3, 2026, in both material modes), texture transforms
after load (uniforms), and property animation (uniforms).

The texture-count limit follows from the sampler budget. See [Sampler budget](#sampler-budget).
In compiled mode a lit material with more than 8 textures raises `AssetError`. In precompiled mode
the same material loads with the SDK archive's feature reductions and a warning.

## How gltfio's ubershader system works in 1.77.1

### Archive format

An uberz archive is one zstd frame. The decompressed data is a `ReadableArchive`: a header, an
array of `ArchiveSpec`, and the packages. Each spec has a shading model, a blending mode (either can
be a wildcard), a list of named feature flags, and one material package. Each flag has the value
`UNSUPPORTED`, `OPTIONAL`, or `REQUIRED` (`ArchiveFeature`). Offsets become pointers at load
(`convertOffsetsToPointers`).

The build (`libs/gltfio/CMakeLists.txt`) runs `matc` on templated `.mat.in` files, then `uberz`
on each `.filamat` and `.spec` pair, then `resgen` makes a C array. The SDK archive has 19 entries:

| Source | Entries | Samplers |
| --- | --- | --- |
| `base` | lit, specular-glossiness, unlit, each opaque, fade, masked | 5 core + 3 clearcoat |
| `transmission` | lit, 3 blend modes | 5 core + transmission + 2 specular |
| `volume` | any shading, 3 blend modes | 5 core + transmission + thickness + specular |
| `sheen` | lit opaque only | 5 core + 2 sheen + specular |
| `specular` | lit, 3 blend modes | 5 core + clearcoat + 2 specular |

Every entry declares exactly 8 samplers, the lit limit at feature level 1 with screen-space
reflections. Every texture role has a named sampler, an `xxxIndex` uniform (UV set, or -1 for no
texture), and a `mat3` UV transform.

### Matching

`UbershaderProvider::getMaterial()`:

1. `prepareConfig()` removes features that no entry can hold together: sheen with volume or
   transmission; volume, transmission, sheen, and IOR with clearcoat; specular-glossiness with
   specular; unlit with specular; clearcoat normal and roughness textures with specular; the
   specular color texture with sheen or volume. It logs a warning for each.
2. `constrainMaterial()` maps texture coordinates to at most two UV sets and drops textures that
   need a third.
3. `ArchiveCache::getMaterial()` returns the **first** spec whose shading and blending match, that
   has a non-`UNSUPPORTED` flag for every feature the key uses, and whose `REQUIRED` flags the key
   has.
4. Without a match it returns spec 0 (lit opaque). `createMaterialInstance()` then sets parameters
   that spec 0 may lack, and Filament aborts the process. filly avoids this path: `ArchiveSpecs`
   in `materials.cpp` repeats the match and compiles the key instead.

Consequences, *measured* by reading the shipped archive (`uberarchive.lib`) and by a JSON-level
model of the matching over the sample assets:

- The archive order decides ties. A material with only `KHR_materials_specular` matches the
  transmission entry before the specular entry, so Filament renders it as a refractive object,
  with the extra refraction pass. 7 of the 191 sample keys take this path.
- `strIsEqual()` compares only the length of the requested name (a prefix match). "Sheen" matches
  a "SheenColorTexture" flag if that flag comes first. For the 1.77.1 archive the result is the
  same as an exact match for all 19 specs and 23 features, but the flag order is hash order, so a
  new archive can change the result.
- `createMaterialInstance()` sets `volumeThicknessIndex` from `transmissionUV`, not from
  `volumeThicknessUV`. filly does not use this path, because it compiles volume materials.
- The shipped packages were compiled with `-a opengl -a vulkan -p desktop -g`: unoptimized, with
  Vulkan SPIR-V that filly never uses. A lit entry is 477 KB: 201 KB GLSL and 274 KB SPIR-V.
- The archive is 707,607 bytes compressed and 8,078,232 bytes decompressed. filly decompresses it
  twice when `precompiled_shaders=True` (once for `ArchiveSpecs`, once in the provider) and keeps
  both copies. Decompression takes about 9 ms.

### Why the SDK archive is not enough for filly

- It has no anisotropy, iridescence, diffuse transmission, unlit alpha forcing, full-scale volume
  thickness, or refraction LOD hook.
- Its reductions drop features. filly promises the complete material in both modes.
- With `precompiled_shaders=True`, filly still compiles 75 of 191 distinct sample keys, in 52 of
  150 assets. *Measured* (JSON model of filly's routing).

## The variant space filly needs

### Compile-time and run-time inputs

| Input | Today | In an ubershader |
| --- | --- | --- |
| Shading model (lit, specular-glossiness, unlit) | Compile time | Compile time: one entry each |
| Blending (opaque, masked, fade) | Compile time | Compile time: one entry each |
| Refraction type (none, thin, solid) and refraction mode | Compile time | Compile time: one entry each |
| Custom surface shading (diffuse transmission) | Compile time | Compile time: own entries |
| Material properties that switch lobes on (clearcoat, sheen, anisotropy, iridescence, specular, IOR, dispersion) | Compile time | Compile time per entry; the lobe strength is a uniform |
| Texture present, per role | Compile time (a sampler per used role) | Uniform (`xxxIndex` or `xxxSlot`) with a bound dummy texture |
| UV set per role | Compile time (`${color}` placeholders) | Uniform |
| Texture transforms | Compile time (`hasTextureTransforms`) | Always on; identity when unused |
| Double-sided | Compile time (`doubleSided`) | Instance state; the material needs `doubleSided : false` to get the capability |
| Vertex colors | Compile time (`require(COLOR)`) | Always required; the loader supplies white |
| Unlit `OPAQUE` alpha 1 | Compile time | `#if defined(BLEND_MODE_OPAQUE)` in the unlit entries |
| Volume scale variable, refraction hook | Compile time | In the refraction and diffuse entries |
| Fog, skinning, morphing, shadows | Filament variants | Filament variants, unchanged |

Only the lobe set is a real compile-time axis. Lobes are expensive and one of them changes pixels
at zero strength. See [Parity](#parity) and [GPU cost](#gpu-cost).

### What the sample assets use

A scratch script read the JSON of the 150 Khronos sample assets in `.deps/sample-audit` and
modeled `getMaterialKey()`, `constrainMaterial()`, and filly's routing. gltfio makes one instance
per (material, vertex colors) pair, so the script counts these pairs, including
`KHR_materials_variants` mappings. *Measured*:

- 11,834 pairs over all assets, 191 distinct compile keys (key plus UV layout). The median asset
  has 2 keys; TextureTransformMultiTest has 29.
- Pairs per feature: iridescence 707, IOR 405, transmission 136, texture transforms 118, volume 101,
  anisotropy 71, specular 68, sheen 42, clearcoat 40, diffuse transmission 34, dispersion 28,
  vertex colors 15.
- Textures per material: at most 7. No material has more than 8.
- Extension textures per material (clearcoat, sheen, transmission, thickness, specular,
  anisotropy, iridescence, diffuse transmission): at most 3. Four materials have 3.
- Combinations that no named-sampler family with 8 samplers can hold occur: transmission with
  volume and iridescence (IridescenceLamp, IridescentDishWithOlives) needs 5 core + 4 named
  extension samplers.

With the entry set proposed below, the 191 keys fall into 15 entries. "References" counts
primitive references to a material:

| Entry | References | Keys | Assets |
| --- | ---: | ---: | ---: |
| lit core, opaque | 11,082 | 68 | 135 |
| lit extended (clearcoat, sheen, iridescence), opaque | 827 | 40 | 23 |
| refraction solid, opaque | 91 | 23 | 22 |
| lit anisotropy, opaque | 71 | 6 | 7 |
| unlit, opaque | 71 | 6 | 5 |
| lit core, masked | 40 | 7 | 11 |
| diffuse transmission, opaque | 33 | 6 | 5 |
| refraction thin, opaque | 30 | 11 | 12 |
| lit core, fade | 23 | 7 | 11 |
| 6 more entries | 26 | 17 | 10 or fewer |

## Constraints

### Sampler budget

Filament 1.77.1 compiles materials at feature level 1, which has 16 fragment samplers.
`MaterialBuilder::checkMaterialLevelFeatures()` reserves 4 (shadow map, structure, SSAO, fog), 3
more for lit materials (froxels, DFG, specular IBL), and 1 more for screen-space reflections or
refraction. `matc` enforces the same rule. *Measured* with `matc`:

| Material | User samplers allowed | Result |
| --- | ---: | --- |
| Lit, `reflections : screenspace` | 8 | 9 fails |
| Lit, `reflections : default` | 9 | 9 compiles, 10 fails |
| Lit with screen-space refraction | 8 | 9 fails |
| Diffuse transmission (lit, no screen-space) | 9 | Compiles with 8 2D and 1 cube sampler |

filly never enables screen-space reflections, so `reflections : screenspace` only costs a sampler
in non-refractive materials. The reflection term is then `Fr * (1 - 0) + E * 0`, the same as
without it.

Feature level 3 allows 16 user samplers, but WebGL2 is feature level 1. Raising the level would
break web parity.

### Parity

A scratch harness renders a UV sphere (1024 x 1024, direct output, no antialiasing, a sun and an
IBL with a gradient cubemap) with filly's generator for a lit opaque key without textures, and
with precompiled packages that have the same factors and zero-strength lobes. *Measured* against
filly's generator:

| Material | Max difference (of 255) | Differing color values |
| --- | ---: | ---: |
| filly generator, `matc`-optimized instead of unoptimized | 0 | 0 |
| SDK `base_lit_opaque` (clearcoat lobe at 0) | 0 | 0 |
| Ubershader with clearcoat, sheen, specular, IOR, iridescence at 0 | 0 | 0 |
| Ubershader with the anisotropy lobe at 0 (alone or with others) | 8 | 260,650 of 3,145,728 |
| filly's extended material (anisotropy and iridescence) | 8 | 260,650 |

Filament's anisotropic GGX with anisotropy 0 is equal in exact arithmetic, but not in float
arithmetic. Anisotropy must therefore have its own entries. The table also shows that filly's
current extended material renders iridescence-only materials through the anisotropic lobe.

Not measured: textured materials, punctual lights, shadows, and the generic sampler slots
proposed below. The slot code samples the same texture at the same UV, so the expected difference
is 0 (*estimate*).

### GPU cost

The same harness, with the sphere filling the frame, measured pipelined frame time. The GPU clock
varies a lot; the table shows the range of two stable repetitions out of three. *Measured*, noisy:

| Material | Frame time at 1 megapixel |
| --- | --- |
| filly generator, no textures | 1.1 to 1.5 ms |
| Ubershader, core lobes only | 1.1 to 1.4 ms |
| Ubershader with clearcoat (SDK `base`, optimized or not) | 1.8 to 2.1 ms |
| Ubershader, all lobes except anisotropy | 2.3 to 2.8 ms |
| Ubershader, all lobes | 2.4 to 2.7 ms |

An always-on lobe costs GPU time even at zero strength. The SDK archive already pays this: every
`base` entry has the clearcoat lobe, so today `precompiled_shaders=True` is slower on the GPU than
the default (*measured*: about 1.4 to 1.7 times for a full-screen sphere). A lit entry without extension
lobes is necessary for the common case.

### Latency

Load latency has two parts: material creation (`filamat`, or parsing a package) and the GL program
compile at the first draw. The GPU driver caches programs on disk by source text, so the second
part depends on whether the machine has seen the shader before. The harness ran one material per
process, with a unique constant in each shader source per repetition to force a cold driver
compile, then a second process with the same source. *Measured*, median of 4:

| Material | Creation | First frame, cold driver cache | First frame, warm driver cache |
| --- | ---: | ---: | ---: |
| filly generator (unoptimized, as filly builds it) | 110 to 180 ms | 320 ms | 19 ms |
| filly extended material | 155 ms | 600 ms | 29 ms |
| Ubershader, core lobes | 2 ms | 390 ms | 15 ms |
| Ubershader with clearcoat | 2 ms | 570 ms | 20 ms |
| Ubershader, all lobes | 1 to 2 ms | 900 to 1000 ms | 20 ms |

Optimized runtime compilation (`Optimization::PERFORMANCE`) took 434 ms (one run), which is why
filly compiles unoptimized.

Consequences:

- Precompilation removes the `filamat` part, 0.1 to 0.2 s per new configuration, in every process.
- It does not remove the driver compile. On a machine that has not seen the shader, a large
  ubershader compiles slower than a specialized material. With a fixed archive, filly can compile
  all programs of the common entries once at renderer creation (`Material::compile()`), and the
  driver cache then serves every later run. With runtime compilation the program set depends on
  the asset and cannot be warmed in advance.
- Filament's `Platform::setBlobFunc()` can persist program binaries. filly does not use it.

## Options

### a. filly features in gltfio-style named-sampler families

Write filly `.mat` files in the SDK style: one named sampler per texture role, families per
feature group, filly's features added as uniforms. Build a filly archive at build time.

- Coverage: each family must declare every role it supports, within 8 samplers. Transmission with
  volume and iridescence needs 9, and sample assets use it. Some combinations still need
  reductions, which filly does not want.
- Many families: the SDK needs 19 entries for fewer features. filly would need more, with a
  reduction table to maintain.
- Runtime texture slots: base color and emissive are named samplers in every entry, so an
  assignment is a uniform and sampler change (as in option d).
- It keeps gltfio's texture binding unchanged, which is the only advantage over option d.

### b. A fixed set of specialized variants

Precompile the generator's output for a fixed list of keys (for example the 191 sample keys) and
reject the rest.

- Size: 191 entries of about 60 to 100 KB each, 11 to 19 MB decompressed (*estimate*).
- Coverage: any new asset can fail. Runtime texture slots multiply the key count.
- Latency and GPU cost: the same as today's compiled mode, minus `filamat`.
- Useful only as a cache, not as the product.

### c. Runtime compilation locally, precompiled materials only on the web

- Size: no saving locally.
- Parity: two material paths (specialized locally, ubershader online). The parity table shows
  that most lobes are equal at zero strength, but anisotropy is not, and every difference
  between the two paths must be measured and maintained.
- Effort: the web still needs the full archive and a provider that understands it, so this is
  option d plus the current system.

### d. Slot-indirected ubershaders (recommended)

Each entry declares the five core samplers with gltfio's names (`baseColorMap`,
`metallicRoughnessMap`, `normalMap`, `occlusionMap`, `emissiveMap`) and three generic extension
samplers (`ext0` to `ext2`). Each extension role (clearcoat, clearcoat roughness, clearcoat
normal, sheen color, sheen roughness, transmission, thickness, specular, specular color,
anisotropy, iridescence, iridescence thickness) has an `xxxSlot` uniform (-1 or 0 to 2), an
`xxxIndex` uniform (UV set), and its own `xxxUvMatrix`. A small function selects the sampler with
a uniform branch, which is valid GLSL ES 3.00.

Entries (30), each for opaque, masked, and fade:

| Entry | Lobes | Samplers |
| --- | --- | ---: |
| Lit core | Core, specular, IOR | 5 + 3 (or 4) |
| Lit extended | Core, specular, IOR, clearcoat, sheen, iridescence | 5 + 3 (or 4) |
| Lit anisotropy | All lit lobes | 5 + 3 (or 4) |
| Refraction thin | Extended lobes, thin refraction, orthographic LOD hook | 5 + 3 |
| Refraction solid | As thin, plus volume, dispersion, volume scale variable | 5 + 3 |
| Refraction thin and solid with anisotropy | As above, plus anisotropy | 5 + 3 |
| Specular-glossiness | SDK `base` | 5 + 3 |
| Unlit | SDK `base`, alpha 1 for opaque | 1 used |
| Diffuse transmission | The current material, unchanged | 9 |

Non-refractive entries can have a fourth extension slot, because they do not need the
screen-space sampler.

Measured parts of this design:

- `filly_lit.mat` (all lobes, 5 + 3 samplers, uniform branches) compiles at feature level 1 for
  desktop GLSL and GLSL ES 3.00. `filly_refr.mat` with the volume scale variable and the
  refraction hook from `native/refraction.h` compiles for both, and the optimized GLSL keeps the
  hook. The diffuse-transmission material compiles unchanged as a `.mat` file.
- A 21-entry prototype archive: 2,115,674 bytes of packages, 127,589 bytes as uberz (desktop GL,
  optimized). With the variant filter `stereo,ssr,vsm` (filly uses none of them): 1,425,768 and
  98,046 bytes. For the web (`-p mobile`, GLSL ES 3.00): 136,231 and 101,885 bytes. `matc` took
  22 to 43 s for 21 entries. Decompression takes about 2 ms.
- Package sizes per lit entry: 477 KB in the SDK archive, 203 KB rebuilt for GL only, 105 KB
  optimized, 69 KB with the variant filter.

What changes in filly:

- **Provider.** A filly provider replaces both gltfio providers. It maps a key and filly's
  extension data to an entry, sets defaults (IOR 1.5, reflectance 0.5, specular 1, all indices and
  slots -1, identity matrices, dummy textures), assigns extension slots, and clears the extension
  texture bits in the key that it returns, so that gltfio binds only the core textures.
- **Extension textures.** filly decodes and binds them, as it already does for anisotropy,
  iridescence, and diffuse transmission (`Provider::decode()`). The glTF preparation already reads
  their texture info.
- **Runtime texture slots.** Every entry has `baseColorMap` and `emissiveMap`. An assignment
  duplicates the instance and sets the sampler, the index, and the matrix. No compile, no new key,
  and no "other glTF textures" restriction. The custom anisotropy, iridescence, and
  diffuse-transmission materials also get runtime slots.
- **Texture limit.** 5 core textures plus 3 extension textures (4 for non-refractive lit). All
  sample materials fit. Compared with compiled mode today, a material with 4 or more extension
  textures and at most 8 in total no longer loads (none in the sample set). This replaces the
  current 8-texture rule, the 5-texture rule for anisotropy and iridescence, and the archive
  reductions. It is the same on the web.
- **Diffuse transmission.** Three precompiled entries. No other change.
- **Generated meshes.** Every entry requires `COLOR` and `UV1`. The mesh vertex buffer must always
  supply them. See [Findings](#findings).
- **API.** `precompiled_shaders` has no meaning and goes away.

Size: the module without `filamat` and `shaders` is 7,896,064 bytes (*measured*). Replacing the
SDK archive (0.71 MB) with a filly archive of about 0.15 to 0.2 MB gives about 7.3 to 7.4 MB
(*estimate*). Deflate-compressed, the module goes from 6.23 MB to 3.50 MB (*measured*, without the
archive change).

### e. Additions that apply to any option

- **Specialization constants.** `Material::Builder::constant()` specializes one package at
  material creation. Constants for texture presence would give each key a specialized program,
  like the current generator, without `filamat`. They cannot switch lobes off, because Filament
  decides the lobes from the material properties at `matc` time. Use them only if the uniform
  branches cost measurable GPU time.
- **Program warmup and cache.** `Material::compile()` at renderer creation for the opaque lit
  entries, and `Platform::setBlobFunc()` for program binaries.
- **Package cache (local only).** Cache `filamat` output on disk by key and SDK version. It
  removes repeated `filamat` time but not the size or the web problem.

### Comparison

| | a | b | c | d |
| --- | --- | --- | --- | --- |
| Module size | about 7.4 MB | 7.9 MB + large archive | 15.1 MB | about 7.4 MB |
| Web parity | Yes, with reductions | Partial | No | Yes |
| Coverage of sample materials | Not all | Only listed keys | All (local) | All |
| Latency, new configuration | Driver only | Driver only | Unchanged locally | Driver only |
| GPU cost | Lobe cost per family | As today | As today | Lobe cost per entry |
| Runtime texture slots | Uniforms | New keys fail | Unchanged | Uniforms, all materials |
| Effort (*estimate*) | 3 to 4 weeks | 1 week | d plus current system | 3 to 4 weeks, plus the web |

## Consequences for the web build

- Filament's web build sets `IS_MOBILE_TARGET`, so `matc` runs with `-p mobile -a opengl` and
  emits GLSL ES 3.00. The filly `.mat` files compile for this target (*measured*). The archive
  needs no SPIR-V, MSL, or WGSL.
- WebGL2 is feature level 1. WebGL2 guarantees 16 fragment texture units, 16 KB uniform blocks,
  and 12 uniform blocks per stage, the same budget that `matc` checks. The prototype entry uses
  about 1 KB of material uniforms (*estimate* from its parameter list).
- The archive alone is not enough. The web build must run filly's provider and preparation, not
  only Filament's JavaScript API. Compile the provider part of the core with Emscripten into the
  custom `filament.js` build that the plan already requires. Keep the provider free of WGL and GLX
  code.
- Output still differs from local output: browser color handling, ANGLE translation, and
  precision qualifiers. Parity then means the same material path, not identical pixels. Measure
  it with the reference assets.

## Consequences for the MATLAB frontend

- The MEX links the same core, so it gets the same size reduction and the same behavior.
- A MinGW build of Filament (one of the options for Octave on Windows) no longer needs `filamat`
  and its toolchain.
- The archive is a platform-independent build artifact. Build it once with the Windows SDK tools
  and use it for Windows, Linux, and the MEX. The Linux SDK build (`tools/build_filament_linux.py`)
  then needs no tools.

## Recommendation

Use option d. Keep `filamat` only as a build option during the transition, then remove it.

### Decision (September 29, 2026)

The owner accepted option d and its trade-offs: the extension texture limit, the GPU cost of the
extended entries for materials that use clearcoat, sheen, or iridescence, and a driver compile on
first use of a large entry. Two points stay open for phase 3:

- A material with more extension textures than its entry has slots. The proposal is to load it
  and drop the least important extension texture with a compatibility warning, not to reject the
  asset. The order of importance is not decided.
- The extension slot limit is the same on desktop and on the web. Do not compile desktop entries
  at a higher feature level for more slots, because the web output would then differ.

### Phases

1. **Fix what any ubershader path needs.** Supply `COLOR` and `UV1` for generated meshes. Decide
   whether iridescence-only materials keep the anisotropic lobe. *Done.*
2. **Archive at build time.** Add the `.mat` sources to the repository. A CMake step runs `matc`
   and `uberz` from the SDK `bin` directory and embeds the archive. Build desktop GL with
   `-V stereo,ssr,vsm`. *Done.*
3. **Provider.** Implement the entry mapping, defaults, extension slots, and extension texture
   binding. Keep the current path behind a build option. Compare both paths over the sample assets
   with `tools/compare_reference.py` and fail on any difference above the measured noise. *Done;
   waiting for the owner's review.*
4. **Remove `filamat`.** Remove the `precompiled_shaders` option, the archive reduction code
   (`ArchiveSpecs`, `reduce_for_archive()`), the key-based variant logic in `slots.inc`, and the
   8- and 5-texture rules. The plan to warm the common entries with `Material::compile()` was
   dropped on September 30: warmup made first frames slower (see
   [gap closure](#gap-closure-september-30-2026)).
5. **Web.** Build the same `.mat` sources with `-p mobile`, and link the provider into the
   Emscripten build. *Done October 3, 2026: `web/`, [the web build](web.md). The output-pass
   materials moved from `filamat` to precompiled packages for it, in every build.*

### Risks

- **GPU cost.** Always-on lobes cost about 2 times a specialized material (*measured*, one GPU,
  noisy). The lit core entry keeps the common case at the specialized cost. Measure again
  on the NVIDIA GPU and on a slow integrated GPU before phase 4.
- **First-use compile.** A large ubershader compiles slower in the driver than a specialized
  material on a machine that has not seen it. Warm the entries at renderer creation, or document a
  warmup frame as today. *Measured on September 30 with cold driver caches: the archive path has
  half the first-frame cost of the runtime path, and warmup did not help; the warmup frame stays
  the documented remedy.*
- **Parity beyond the test scene.** Textured materials, punctual lights, shadows, and the slot
  selection are not measured. Phase 3 must cover them.
- **Extension texture decoding.** filly decodes extension textures synchronously, one at a time.
  Assets with many of them load slower than with gltfio's parallel decoder.
- **Assets over the slot limit.** A material with 4 or more extension textures fails to load. None
  exists in the sample set. *Phase 3 loads it without the least important textures and warns.*

### SDK upgrade maintenance

- Material packages carry the material version (77 for 1.77.1). The engine rejects packages of
  another version, so every upgrade must rebuild the archive with the matching `matc`. The build
  step makes this automatic.
- The `.mat` sources use Filament's `MaterialInputs` fields, property names, and blending
  defines. Review them with the release notes.
- The refraction hook depends on the name `sampler0_ssr` and on the order of the generated shader.
  This is the same review as today (see `native/refraction.h`). Add a build check that the
  optimized GLSL still contains the hook. *Done: `native/materials/check_refraction.cmake`.*
- The provider depends on gltfio's texture parameter names for the core roles and on the order of
  `createMaterialInstance()` and texture binding in `AssetLoader`.
- It no longer depends on `UbershaderProvider`, `ArchiveCache`, or `prepareConfig()`, which filly
  copies today.

## Phase 1 to 3 results (September 29, 2026)

Phases 1 to 3 are implemented. The default build keeps the runtime path; build the archive path
with `-Ccmake.define.FILLY_MATERIALS=archive` (see [build](../how-to/build.md#material-path)).
Phases 4 and 5 are not started. Measurements are on the Intel Iris Xe test machine unless a
row names the NVIDIA RTX A500 Laptop GPU of the same machine.

### Phase 1: fixes that any ubershader path needs

| Fix | Change | Regression test | Before the fix (*measured*) |
| --- | --- | --- | --- |
| Generated meshes | `Model::attach_mesh()` always supplies `COLOR` (white `UBYTE4` when the mesh has no colors) and `UV1` (reads the `UV0` buffer). | `test_mesh.py::test_mesh_without_colors_is_white` | A 0.5 grey unlit blended plane over a 0.2 background showed 124 (background) with `precompiled_shaders=True`, 188 with compiled shaders. |
| Iridescence without anisotropy | The runtime generator emits only the lobes of the extensions that the material has (`SurfaceSource::has_anisotropy`, `has_iridescence`). | `test_material_paths.py::test_iridescence_without_strength_matches_the_plain_material` | In the test scene, 89 of 9,216 pixels differed by 1 from the plain material. |
| Specular-only materials | `ArchiveSpecs::supports()` rejects a first archive match with transmission or volume for a key without them; such keys compile. | `test_material_paths.py::test_specular_only_material_is_not_refractive` | The material was the SDK's transmission entry (screen-space refraction). The test scene showed no pixel difference at transmission 0. |

Pixel baseline of the default path, phase-1 build against the previous final run (156 inputs,
rest pose and two animation times): 148 unchanged. The 8 changed inputs are all
iridescence-only materials, which lost the anisotropic lobe:

| Asset | Max difference | Pixels different |
| --- | ---: | ---: |
| IridescenceAbalone | 110 | 5,634 (98 over 50) |
| IridescentDishWithOlives | 20 (t0.5) | 160 |
| IridescenceSuzanne | 18 | 250 |
| IridescenceMetallicSpheres | 9 | 2,694 |
| CompareIridescence | 4 | 45 |
| IridescenceLamp | 2 | 23 |
| IridescenceDielectricSpheres | 1 | 392 |
| SunglassesKhronos | 1 | 5 |

Cause: at strength 0, the anisotropic GGX lobe equals the isotropic lobe only for an
orthonormal tangent frame. With a normal map, the shading normal is not orthogonal to the vertex
tangents, so the difference is large on IridescenceAbalone, the only normal-mapped asset of the
eight. The isotropic lobe is correct for these materials. The design's value of 8 came from its
own harness scene; the regression test scene gives 1.

### Phase 2: the archive at build time

`native/materials` has four templates (`surface.mat.in` for the lit and refractive entries,
`unlit.mat.in`, `specular_glossiness.mat.in`, `diffuse_transmission.mat.in`) and
`materials.cmake`. CMake writes one `.mat` file per entry and blending mode, runs `matc -a opengl
-p desktop -V stereo,ssr,vsm` on each, packs them with `uberz`, and embeds the archive as a C
array. Each uberz spec has a flag with the entry name, which the provider looks up. The refraction
code comes from the three strings in `native/refraction.h`, so both paths share one copy. When
`matinfo` is available, the build fails if the optimized refraction shader no longer calls the
hook (`check_refraction.cmake`; it looks for `textureSize(sampler0_ssr` or, in unoptimized
shaders, `fpTextureLod`).

Entries as of September 30 (the three `Specular` entries were split off; see
[gap closure](#gap-closure-september-30-2026)):

| Entry | Lobes and inputs | Generic samplers | Packages, opaque / masked / fade (bytes) |
| --- | --- | ---: | --- |
| `LitCore` | Core, IOR | 0 | 63,090 / 63,388 / 59,161 |
| `LitExtended` | Core, IOR, clearcoat, sheen, iridescence | 4 | 80,632 / 82,139 / 74,625 |
| `LitSpecular` | As `LitExtended`, plus specular | 4 | 82,986 / 84,776 / 77,018 |
| `LitAnisotropy` | As `LitSpecular`, plus anisotropy | 4 | 85,855 / 87,965 / 79,855 |
| `RefractionThin` | As `LitExtended`, plus transmission, thin refraction, orthographic LOD hook | 3 | 84,287 / 85,928 / 78,085 |
| `RefractionThinSpecular` | `RefractionThin` plus specular | 3 | 86,546 / 88,492 / 80,645 |
| `RefractionSolid` | As `RefractionThin`, solid, plus volume, dispersion, volume scale variable | 3 | 89,315 / 92,707 / 83,383 |
| `RefractionSolidSpecular` | `RefractionSolid` plus specular | 3 | 91,874 / 95,522 / 85,926 |
| `RefractionThinAnisotropy` | `RefractionThinSpecular` plus anisotropy | 3 | 89,517 / 91,777 / 83,621 |
| `RefractionSolidAnisotropy` | `RefractionSolidSpecular` plus anisotropy | 3 | 94,917 / 98,897 / 88,929 |
| `SpecularGlossiness` | Core | 0 | 62,755 / 63,023 / 58,862 |
| `Unlit` | Base color, alpha 1 for opaque | 0 | 12,219 / 11,438 / 12,345 |
| `DiffuseTransmission` | The runtime path's material | 3 own | 69,166 / 72,110 / 64,900 |

*Measured*: 39 packages, 2,938,676 bytes; the uberz archive is 152,574 bytes (Windows tools) and
152,603 bytes (Linux tools). On September 29, with 30 packages, the archive was 128,458 bytes and
`matc` took 15.5 s for all packages in series (0.2 to 0.7 s each); the Visual Studio build ran
them in parallel in about 11 s. Decompression at renderer creation is about 2 ms (*estimate*
from the design's prototype).

Changes to the proposed entry table:

- Only materials with `KHR_materials_specular` get an entry with specular inputs. With
  `specularFactor` written, Filament's isotropic lobe uses glTF's F90 (1 for specular 1) instead
  of its F90 from F0, `saturate(50 * 0.33 * f0)`, which is lower for dark metals and low IOR. A
  core entry with specular inputs differed from the runtime path by up to 54 on the point-light
  frame of CompareIridescence. On September 29 the other lobe entries still had specular inputs;
  on September 30 they were split (see [gap closure](#gap-closure-september-30-2026)). The
  anisotropic entries keep specular inputs, because Filament's anisotropic lobe always uses the
  F90 from F0.
- Non-refractive lit entries use `reflections : default`, which frees the ninth sampler. filly
  never enables screen-space reflections, so the result is the same.
- Only entries with volume have vertex code. A material with vertex code gets its own depth
  programs. (This made no measured pixel difference, but it avoids extra programs.)
- Each package defines `FILLY_ENTRY_<name>`. Filament's program cache key is a hash of the user
  shader code only (`MaterialBuilder`, `MaterialCacheId`), without the blending mode or other
  properties. Without the define, the opaque, masked, and fade packages of an entry share keys,
  and a program binary cache loaded the opaque program for masked materials. filly's program
  binary cache was removed on September 30; the define stays, because any `Platform` blob cache
  (for example an embedder's) keys programs the same way. In-process program sharing uses the
  package CRC32, not this key.

The Linux SDK tool now builds `matc`, `uberz`, and `matinfo` with the libraries
(`tools/build_filament_linux.py`, `TOOLS`), because the release archive's tools need a newer glibc
than manylinux_2_28. In the existing work volume this took 18 s. Linux and Windows packages have
the same shader text (compared for `LitCore` opaque); they differ only in the material id, which
Filament computes with `std::hash` (different in MSVC and libstdc++), and the package checksum.

### Phase 3: the filly material provider

`native/archive_materials.cpp` replaces both gltfio providers on the archive path. Preparation
(`plan_archive_materials()` in `native/gltf_prepare.cpp`) gives each glTF material a plan: the
entry, the generic sampler of each extension texture role, and the image bytes of those textures.
The provider:

- returns the entry instance for the material that the extras marker names, with a fixed UV
  layout (`TEXCOORD_0` in UV0, `TEXCOORD_1` in UV1) for every material;
- sets defaults (IOR 1.5, specular 1, transmission 1 for volume without
  `KHR_materials_transmission`, iridescence IOR 1.3 and thickness 100 to 400, every slot -1,
  identity matrices, dummy textures) and the anisotropy and iridescence factors;
- clears the extension texture bits, and the features that the entry lacks, in the key that it
  returns, so that gltfio binds only core textures and sets only parameters that exist, and sets
  the clearcoat normal scale that gltfio sets only with the texture;
- decodes and binds extension textures like `Provider::decode()`. Roles that use the same glTF
  texture in the same color space share a sampler and one decoded texture.

Runtime textures (`slots.inc`, archive branch) duplicate the glTF instance and set the sampler,
the UV set, and the matrix. Every material has both slots, including anisotropy, iridescence,
and diffuse transmission, with no compile and no "other glTF textures" restriction.

Order of importance of extension textures, most important first: transmission, thickness,
anisotropy, iridescence, clearcoat, sheen color, specular color, specular, iridescence
thickness, clearcoat roughness, sheen roughness, clearcoat normal. A material with more distinct
extension textures than its entry has samplers loads without the last ones, and
`AssetCompatibilityWarning` names the material and the dropped textures. Rationale: without a
detail map (normal, roughness), the effect remains with its factor; without the maps that define
where an effect exists (transmission, thickness, anisotropy direction, iridescence mask), the
material changes character. No sample material needs a drop (*measured* by the planner over all
sample assets: no warning).

Warmup (removed on September 30): a new renderer called `Material::compile()` for the opaque,
blended, and masked core lit entries and the opaque and blended unlit entries, with the
directional and dynamic lighting variants, and did not wait. `FILLY_MATERIAL_WARMUP=0` skipped
it. Program binary cache (removed on September 30): filly implemented `Platform::setBlobFunc()`
with an on-disk cache (`native/program_cache.cpp`), off by default. `EglPlatform` now creates
shared contexts for Filament's compiler thread (`isExtraContextSupported()`, `createContext()`,
`releaseContext()`); this stays.

#### Tests

| Build | Windows suite | PsychoPy suite |
| --- | --- | --- |
| Runtime (default) | 431 passed, 14 skipped | 23 passed |
| Archive | 432 passed, 13 skipped | 23 passed |

New tests on both paths: generated mesh colors, iridescence lobes, specular-only routing, UV
rotation direction of animated transforms. Tests of the 8- and 5-texture rules and of the "other
slots" restriction run on the runtime path only; the archive path has tests for sampler sharing,
dropped textures (warning and strict error), runtime textures with other glTF textures, and
runtime textures on anisotropy, iridescence, and diffuse-transmission materials.

#### Pixel parity with the runtime path

`pixel_baseline.py` rendered each input in its own process at 256 x 256 with a fitted camera,
a directional light, and a fixed panorama: the rest pose, the same with shadows
(`scene.shadows`, the sun casts shadows), a point light instead of the sun, and two animation
times for animated assets. *Measured*, final builds of both paths, 156 inputs:

- 93 inputs are identical in every frame; AnimationPointerUVs fails to load on both paths
  (duplicate pointer target, unchanged).
- 54 inputs differ by at most 2 in some frame, most of them only in the shadow frame.
- 8 inputs differ by more than 2:

| Asset | Max difference | Pixels over 2/255 | Frame with the maximum | Cause |
| --- | ---: | ---: | --- | --- |
| PotOfCoalsAnimationPointer | 148 | 0.53% | shadow | IOR default |
| MeshPrimitiveModes | 143 | 0.01% | rest, shadow | `TRIANGLE_FAN` drawn as points |
| IORTestGrid | 81 | 0.27% | rest | IOR default |
| RecursiveSkeletons | 54 | 0.17% | shadow | Shadow edges |
| ABeautifulGame | 27 | 0.10% | rest, shadow | IOR default |
| GlassHurricaneCandleHolder | 15 | 0.01% | rest | IOR default |
| PrimitiveModeNormalsTest | 9 | less than 0.01% | shadow | Shadow edges |
| SheenTestGrid | 8 | less than 0.01% | shadow | Shadow edges |

The 54 inputs with differences of 1 or 2 are: AnimatedCube, AnisotropyRotationTest,
AnisotropyStrengthTest, AntiqueCamera, BoomBoxWithAxes, BoxTextured, BoxTexturedNonPowerOfTwo,
BrainStem, CesiumMan, ClearCoatCarPaint, ClearCoatTest, CompareAlphaCoverage,
CompareAmbientOcclusion, CompareAnisotropy, CompareBaseColor, CompareClearcoat,
CompareEmissiveStrength, CompareIridescence, CompareMetallic, CompareNormal, CompareRoughness,
CompareSheen, CompareSpecular, CompareTransmission, DirectionalLight, DragonAttenuation,
DragonDispersion, Duck, EnvironmentTest, Fox, GlassVaseFlowers, IridescenceDielectricSpheres,
IridescenceMetallicSpheres, IridescenceSuzanne, Lantern, LightsPunctualLamp, MetalRoughSpheres,
MetalRoughSpheresNoTextures, MorphStressTest, MosquitoInAmber, NegativeScaleTest,
NodePerformanceTest, RiggedSimple, SpecularTest, StainedGlassLamp, Suzanne, TextureEncodingTest,
TextureTransformMultiTest, TransmissionOrderTest, TransmissionRoughnessTest, TransmissionTest,
TransmissionThinwallTestGrid, unicode-Suzanne, and XmpMetadataRoundedCube.
A difference of 1 or 2 is also what the controls below show between two builds that should agree.

Causes, each checked with a control:

- **IOR default.** Without `KHR_materials_ior`, the runtime path and `gltf_viewer` derive the
  refraction IOR from F0, which specular (PotOfCoals: specular 0, so IOR 1) and metallic textures
  (ABeautifulGame, GlassHurricaneCandleHolder) change. The archive path uses glTF's default
  1.5. Control: with an explicit `KHR_materials_ior` of 1.5 on the refractive materials, the four
  assets differ by at most 1 between the paths.
- **TRIANGLE_FAN.** gltfio draws the fan of MeshPrimitiveModes as points, with degenerate
  generated normals, so the lit result is undefined: the runtime shader gives white points, the
  archive shader none. Control: with an unlit material both paths draw the 7 points.
- **Shadow edges.** RecursiveSkeletons, PrimitiveModeNormalsTest, and SheenTestGrid differ only
  along shadow edges. Control: the runtime build with `precompiled_shaders=True` (Filament's own
  archive) differs from the same build's compiled materials by exactly the same amounts (54/131
  pixels, 9/6, 8/3). The difference is between packages that `filamat` builds in the process
  and `matc` packages, not a property of filly's entries. An unoptimized archive (`matc -g`) gave
  the same values, so shader optimization is not the cause.

#### Parity with gltf_viewer

`tools/compare_reference.py`, default configuration (studio environment, 512 x 512), *measured*:

| Asset | Runtime path | Archive path |
| --- | --- | --- |
| DamagedHelmet | identical | identical |
| TransmissionTest | identical | identical |
| ClearCoatTest | identical | identical |
| SheenChair | identical | identical |
| IridescenceMetallicSpheres | MAE 5.79, max 255, 19.5% over 2 | MAE 5.79, max 255, 19.5% over 2 |

The iridescence rows differ from `gltf_viewer` because gltfio has no iridescence; the two filly
captures are identical.

#### Fixes found during the comparison

- **UV rotation direction.** `texture_info()` and the texture-transform animation built the
  rotation in the opposite direction to gltfio's `matrixFromUvTransform()`. Animated rotation of
  core textures, and every rotation of anisotropy, iridescence, and diffuse-transmission textures,
  was mirrored on both paths. TextureTransformMultiTest renders X marks instead of check marks
  with the old convention (seen on the archive path's clearcoat textures). Fixed in both paths;
  test `test_gltf_surface_camera.py::test_uv_rotation_direction_matches_static_transform`. The
  runtime path's PotOfCoalsAnimationPointer frame at t0.25 changed by up to 71 (0.03% of pixels).
- **Program cache keys.** See the `FILLY_ENTRY` define above. For the same reason, the program
  cache was not installed on the runtime path, whose generated materials can share code between
  alpha modes and sidedness. (The program cache was removed on September 30.)

Found on September 29 and closed on September 30 (see
[gap closure](#gap-closure-september-30-2026)): the diffuse-transmission material left
`emissive.w` at its default of 1, so the glTF emission was scaled by the camera exposure. The
statement of September 29 that gltfio never binds the specular-glossiness texture was wrong:
`MaterialKey` declares `hasSpecularGlossinessTexture` and `hasMetallicRoughnessTexture` in a
union, so the binding sees the flag.

#### GPU cost

Full-frame sphere at 1024 x 1024, sun and IBL, pipelined time per frame over 400 frames, three
repetitions, *measured* (ms):

| Material | Intel, runtime | Intel, archive | NVIDIA, runtime | NVIDIA, archive |
| --- | --- | --- | --- | --- |
| Plain (`LitCore`) | 0.93 to 1.02 | 1.10 to 1.19 | 0.76 to 0.77 | 0.80 to 0.82 |
| Sheen 0 (`LitExtended`) | 1.09 to 1.11 | 2.12 to 2.46 | 0.76 to 0.96 | 1.05 to 1.11 |
| Clearcoat 0 (`LitExtended`) | 1.01 to 1.05 | 2.13 to 2.26 (one 3.03) | 0.72 to 0.87 | 0.90 to 1.04 |
| Anisotropy 0 (`LitAnisotropy`) | 1.15 to 1.19 | 2.24 to 2.33 | 0.88 to 1.03 | 0.99 to 1.02 |

The runtime column compiles only the extension's own lobe; the archive entry has all lobes of
its entry. On Intel the extended entries cost about 2 times, and `LitCore` about 1.15 times (the
uniform texture branches and the extra `COLOR` and `UV1` inputs). The NVIDIA numbers come from a
scratch build without vsync on the headless swap chain (the normal build is capped at 60 Hz on
this GPU) and from the first of two rounds; in the second round every value doubled, which
matches a clock drop, so the NVIDIA numbers are noisy.

#### Load time and first use

One sphere asset per process, driver shader caches warm, median of 3, *measured* (ms). Archive
rows without a note have the program cache off and no warmup.

| Case | Intel load | Intel first frame | NVIDIA load | NVIDIA first frame |
| --- | ---: | ---: | ---: | ---: |
| Runtime, plain | 189 | 23 | 146 | 215 |
| Runtime, sheen | 170 | 23 | 321 | 457 |
| Runtime, anisotropy | 144 | 22 | 368 | 471 |
| Archive, plain | 64 | 18 | 43 | 201 |
| Archive, sheen (`LitExtended`) | 48 | 18 | 94 | 413 |
| Archive, anisotropy | 43 | 18 | 110 | 472 |
| Archive, plain, program cache deleted | 51 | 55 | 48 | 228 |
| Archive, plain, program cache warm | 72 | 58 | 47 | 208 |
| Archive, plain, cache warm, warmup | 48 | 46 | 48 | 211 |

The archive path removes 100 to 260 ms of material compilation from each load. With warm driver
caches, the first frame is equal or faster. The NVIDIA first frame is about 200 ms in every
case, so it is not program compilation. The program binary cache made the Intel first frame
about 35 ms slower (`glProgramBinary` on this driver is slower than a compile that the driver's
own cache serves), and it made no measurable difference on NVIDIA; it is therefore off by
default (`FILLY_SHADER_CACHE` turns it on). Cold driver caches were not measured; the design's
values (390 to 1,000 ms per program on first use) apply. *September 30: cold caches measured,
warmup and program cache removed; see [gap closure](#gap-closure-september-30-2026).*

Cold loads of sample assets, one per process, median of 3, Intel, *measured* (ms):

| Asset | Runtime | Archive |
| --- | ---: | ---: |
| DamagedHelmet | 303 | 177 |
| Sponza | 626 | 385 |
| SheenChair | 347 | 87 |
| ClearCoatCarPaint | 164 | 55 |
| ChronographWatch | 546 | 158 |
| IridescenceAbalone | 486 | 284 |
| ABeautifulGame | 1,054 | 736 |

gltfio still decodes the extension textures, because `AssetLoader::preresolveTextures()` reads
the unmodified key, and the provider decodes them a second time for its samplers. That memory
and time are wasted (from the source; not measured). The loads above are faster anyway.

Warmup, *measured*: renderer creation took 0.6 to 1.0 s on Intel with or without warmup (the
compile calls return at once). The warmup programs finished on the compiler thread 0.36 to
0.37 s after creation without the program cache and 0.70 to 0.72 s with it (Intel), 0.20 to
0.28 s and 0.31 to 0.43 s (NVIDIA). With warm driver caches the first frame did not change. The
compile-completion callback of `Material::compile()` is not a reliable end signal: Filament
submits it when each program token is released, which can wait for the program's first use, and
2 of 3 Intel runs with a warm program cache reported pending programs after 10 s. *September 30:
the same callback caused a heap-use-after-free at renderer close; see
[gap closure](#gap-closure-september-30-2026).*

Shader compile mode, *measured* from the threads of the process (Filament names its pool thread
`CompilerThreadPool`):

| Platform | Mode |
| --- | --- |
| WGL, offscreen | thread pool (1 thread) |
| WGL, shared context | thread pool |
| GLX, offscreen and shared (WSLg, and Xvfb with llvmpipe) | thread pool |
| EGL (WSLg D3D12, with and without a display) | thread pool (new in this phase) |

The previous Linux wheel had no compiler thread on EGL (*measured*); Filament then compiles one
program per frame on the driver thread (or uses `KHR_parallel_shader_compile`), and
`Material::compile()` does nothing in the synchronous mode. Warmup on WSLg took 0.47 to 0.54 s,
and 0.05 s with llvmpipe. A pool larger than one thread was not measured.

#### Module size

| Build | Module (bytes) | Deflate |
| --- | ---: | ---: |
| Runtime | 15,113,728 | 6,250,196 |
| Archive (`filamat`, `shaders`, and `uberarchive` on the link line) | 7,323,648 | 2,928,873 |
| Archive, scratch link without them | 7,323,648 | |

The linker drops the unused material compiler, so the archive module already has the size that
the design estimated (7.3 to 7.4 MB). The Linux archive wheel is 2,488,650 bytes.

#### Linux

- `tools/build_filament_linux.py` built `matc`, `uberz`, and `matinfo` in the existing work
  volume (`filly-work`) in 18 s.
- The archive-path wheel was built in the `filly-ml` manylinux_2_28 container as
  `tools/ci_linux.sh` and [build](../how-to/build.md#build-the-manylinux-wheel-with-docker)
  describe, with `-DFILLY_MATERIALS=archive`: 2,488,650 bytes, `manylinux_2_27`/`2_28` tags.
- WSL Ubuntu 22.04, WSLg (Mesa 23.2.1, D3D12 on the Iris Xe, OpenGL 4.1): 432 passed, 13
  skipped in 4 of 5 full runs. One run with warmup ended with a segmentation fault in
  `test_gl_hosts.py::test_close_after_window_close_releases_everything`, in `scene.load()` just
  after a shared-context GLX renderer was created. It did not happen again in 4 full runs (2 with
  and 2 without warmup) or in 6 runs of `test_gl_hosts.py`. *Cause found on September 30*: the
  warmup's compile callback wrote into the freed material provider (see
  [gap closure](#gap-closure-september-30-2026)).
- WSL, Xvfb with `LIBGL_ALWAYS_SOFTWARE=1` (llvmpipe, LLVM 15): 432 passed, 13 skipped.
- WSL, EGL without a display (D3D12), `-m 'not psychopy and not interop'`: 374 passed, 12 skipped.
- Not run on Linux: the PsychoPy suite, and the runtime-path wheel of this phase.

#### Not verified

- Cold driver shader caches (first run after a driver update) on either GPU. *Measured on
  September 30 with per-process cold caches; a real driver update was not.*
- GPU cost on a slow integrated GPU other than the Iris Xe.
- The NVIDIA frame times beyond the noisy first round.
- Materials over the slot limit in real assets; only generated test assets exercise the drop.
- `TEXCOORD_2` and higher in real assets; none in the sample set.
- Removal of `filamat` (phase 4). The web build (phase 5) runs; see [the web build](web.md).

## Gap closure (September 30, 2026)

Five gaps from phases 1 to 3 were closed before phase 4. Measurements are on the same machine
(Intel Iris Xe 32.0.101.7088, NVIDIA RTX A500 Laptop GPU 596.58).

### Diffuse-transmission emission

**Cause.** Both material paths set only `material.emissive.rgb`. Filament's default for
`emissive.w` is 1, and it scales emission by `mix(1, exposure, emissive.w)`, so the glTF emission
was multiplied by the camera exposure and came out nearly black.

**Fix** (`native/materials/diffuse_transmission.mat.in`, `native/materials.cpp`): `emissive.w` is
0, as in the other lit materials. The environment backlight term, which is IBL light, is
multiplied by `getExposure()` in the shader, so it keeps its previous value.

**Result**, *measured*: a black triangle with emissive factor (0, 0.5, 0) under a head-on sun
rendered (92, 92, 92) with diffuse transmission and (92, 204, 92) without it; after the fix both
render the same. Test:
`test_material_paths.py::test_diffuse_transmission_emission_matches_other_lit_materials`
(emissive (0.1, 0.25, 0.05) with `KHR_materials_emissive_strength` 2). The pixel baseline of
DiffuseTransmissionPlant, DiffuseTransmissionTeacup, and DiffuseTransmissionTest did not change on
either path (they have no emission, and the backlight is unchanged).

Found on the way: Filament 1.77.1's gltfio multiplies `emissiveFactor` by `emissiveStrength` and
also sets the `emissiveStrength` parameter, which the shaders multiply again. Factor 0.05 with
strength 4 renders like factor 0.8, on both paths and in every lit material. `gltf_viewer` does
the same (EmissiveStrengthTest and CompareEmissiveStrength are identical to it). Superseded
(September 30, 2026): the owner chose the extension's specified behavior. filly now restores the
unscaled factor after gltfio creates each instance, and differs from `gltf_viewer` for these
assets. The defect comes from two independent gltfio changes: google/filament#4975 baked the
strength into the factor, and google/filament#5373 later added the shader parameter without
removing the baking.

### Specular-glossiness textures

**Cause of the report.** The statement that gltfio never binds the specular-glossiness texture
was wrong. `gltfio::MaterialKey` declares `hasMetallicRoughnessTexture` and
`hasSpecularGlossinessTexture` (and their UV fields) in a union, so the flag that
`getMaterialKey()` sets for the specular-glossiness texture is the flag that
`createMaterialInstance()` checks. gltfio binds the texture as `metallicRoughnessMap` in sRGB, and
filly's `SpecularGlossiness` entry and the runtime path sample it there.

**Fix.** None needed. Test: `test_material_paths.py::test_specular_glossiness_texture_is_sampled`
renders 1 x 1 textures (0, 0, 0, 0), (128, 128, 128, 0), and (64, 128, 200, 128) and compares
them with the same values as factors (sRGB specular color, linear glossiness): *measured* equal on
both paths, and different from the untextured material by 99 to 111.

`tools/compare_reference.py SpecGlossVsMetalRough`: identical to `gltf_viewer` on both paths, so
`gltf_viewer` binds the texture too.

### Fresnel F90 in the extended entries

**Cause.** With `specularFactor` or `specularColorFactor` among the material inputs, Filament's
isotropic lobe uses `pixel.f90 = metallic + specularFactor * (1 - metallic)` instead of
`saturate(50 * 0.33 * (f0.r + f0.g + f0.b))` from F0. The two differ when F0 is small: dark
metals and dielectrics with low IOR. Indirect light and F0 are the same at the defaults. The
anisotropic lobe always uses the F90 from F0.

**Fix** (`native/materials/materials.cmake`, `surface.mat.in`, `native/gltf_prepare.cpp`,
`native/gltf_prepare.h`, `native/archive_materials.cpp`). A uniform cannot select the F90: the
value is computed in Filament's `getCommonPixelParams()` from the material inputs, and a metallic
material gets F90 = 1 for every `specularFactor`. The lobe entries were split instead:
`LitExtended`, `RefractionThin`, and `RefractionSolid` have no specular inputs, and the new
`LitSpecular`, `RefractionThinSpecular`, and `RefractionSolidSpecular` have them. Only materials
with `KHR_materials_specular` get a `Specular` entry, which is what the runtime path and
`gltf_viewer` do. The anisotropic entries keep specular inputs. Cost: 39 packages instead of 30,
archive 152,574 bytes instead of 128,458. The final archive module, also without the warmup and
the program cache, is 7,328,768 bytes (7,323,648 on September 29); the runtime module is
15,109,632 bytes.

**Result**, *measured*, 128 x 128 sphere, orthographic, maximum difference / pixels over 2 of 255
between the runtime path and the archive path:

| Material (no `KHR_materials_specular`) | Point light behind (rim), before | Rim, after | Point light in front, before | IBL only, before |
| --- | --- | --- | --- | --- |
| Dark metal (base 0.01, metallic 1), clearcoat 0 | 53 / 808 | 0 / 0 | 1 / 0 | 0 / 0 |
| Dark metal, iridescence 0 | 53 / 808 | 0 / 0 | 1 / 0 | 0 / 0 |
| Dark metal, sheen | 52 / 659 | 0 / 0 | 1 / 0 | 0 / 0 |
| Dark metal, transmission 0 | 53 / 808 | 0 / 0 | 1 / 0 | 0 / 0 |
| Dark metal, transmission 0.5 and volume | 53 / 808 | 0 / 0 | 1 / 0 | 0 / 0 |
| Dielectric at IOR 1.1, clearcoat, sheen, iridescence, or transmission | 115 / 552 to 557 | 0 / 0 | 1 / 0 | 0 / 0 |
| Dielectric at IOR 1.1, transmission 0.5 and volume | 129 / 643 | 0 / 0 | 1 / 0 | 0 / 0 |
| Dielectric at IOR 1.5, every lobe | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Any surface, anisotropy 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

After the fix, the front-light and IBL frames are also 0 / 0 for every material. The same 21
materials with `KHR_materials_specular` (factor 0.7, color (1, 0.8, 0.6)) were identical on both
paths before and after. Tests: `test_lobes_without_specular_extension_keep_derived_f90`
(clearcoat and iridescence at 0 on a dark metal and at IOR 1.1 equal the plain material,
tolerance 1) and `test_specular_extension_uses_its_f90` (with the extension, the rim is brighter
by more than 20, with and without clearcoat).

In the sample assets the effect is small: the pixel baseline between the paths changed only for
TransmissionRoughnessTest (maximum difference 1 to 0).

### Pixel baselines after the three fixes

`pixel_baseline.py`, 156 inputs, same frames as in phase 3, *measured*:

| Comparison | Identical in every frame | Maximum 1 or 2 | Over 2 | Not loadable |
| --- | ---: | ---: | ---: | ---: |
| Runtime, before and after | 155 | 0 | 0 | 1 |
| Archive, before and after | 151 | 3 | 1 | 1 |
| Runtime and archive, before | 93 | 54 | 8 | 1 |
| Runtime and archive, after | 95 | 53 | 7 | 1 |

The archive changes are IridescenceMetallicSpheres, TransmissionRoughnessTest, and
TransmissionThinwallTestGrid (1, point-light frame) and MeshPrimitiveModes (143), which now
equals the runtime path. MeshPrimitiveModes has no material and uses `LitCore`, whose shader text
did not change (the packages differ only in 12 bytes of material id). It draws a triangle fan as
points with degenerate normals, so its lit result is undefined and changed with the driver
compile. The remaining differences over 2 have the causes that phase 3 found (IOR default, shadow
edges). AnimationPointerUVs does not load on either path (unchanged). `compare_reference.py`
(DamagedHelmet, TransmissionTest, ClearCoatTest, SheenChair, IridescenceMetallicSpheres,
SpecGlossVsMetalRough): unchanged on both paths.

### First use with cold driver shader caches

**Method.** One fresh process per measurement: create a renderer, load six sample assets
(DamagedHelmet, SheenChair, ClearCoatCarPaint, TransmissionTest, IridescenceAbalone,
CompareAnisotropy) into one scene each with a fitted camera, a sun, and a 32 x 64 IBL, then render
and read each scene at 512 x 512 (first frames) and once more (second frames). "Total" is renderer
creation plus loading plus first frames.

- NVIDIA: the driver honors `__GL_SHADER_DISK_CACHE_PATH` on Windows (it wrote `GLCache\...\*.bin`
  and `*.toc` there). Cold runs used a new empty directory; warm runs used the same directory
  again. A scratch copy of the interpreter had `GpuPreference=2;` in
  `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`; the value was removed afterward.
- Intel: no per-process switch was found in the driver. Renaming
  `%USERPROFILE%\AppData\LocalLow\Intel\ShaderCache` failed with "access denied" (other processes
  hold files in it), so nothing was moved. The driver keeps one cache file per executable file
  name (*measured*: a copy of `python.exe` under a new name wrote a new 2.2 MB file and took
  2,042 ms for its first frames; a copy under another directory but the same name took 67 ms).
  Cold runs used a new copy of the interpreter with a new name; warm runs used the same name
  again. The runs added 47 files to that directory.

Median of 5 runs on Intel and 3 on NVIDIA, range in parentheses, *measured* (ms). The warmup and
program-cache rows are from the build before their removal; "no warmup" is the current behavior
(confirmed with the final build on Intel: 2,648 cold and 1,093 warm total).

| Case | GPU | Driver cache | Load | First frames | Total |
| --- | --- | --- | ---: | ---: | ---: |
| Runtime | Intel | cold | 2,423 | 2,807 (2,735 to 2,881) | 5,328 |
| Runtime | Intel | warm | 2,138 | 254 (251 to 255) | 2,517 |
| Archive, no warmup | Intel | cold | 1,223 | 1,284 (1,280 to 1,406) | 2,620 |
| Archive, no warmup | Intel | warm | 891 | 70 (66 to 118) | 1,100 |
| Archive, warmup | Intel | cold | 2,495 | 2,039 (1,472 to 2,627) | 4,651 |
| Archive, warmup | Intel | warm | 898 | 59 (58 to 67) | 1,095 |
| Archive, warmup, primed program cache | Intel | cold | 1,788 | 1,912 (67 to 2,176) | 3,818 |
| Archive, warmup, primed program cache | Intel | warm | 916 | 38 (33 to 2,051) | 1,092 |
| Runtime | NVIDIA | cold | 3,820 | 5,585 (2,638 to 5,958) | 10,867 |
| Runtime | NVIDIA | warm | 3,407 | 475 (248 to 529) | 5,416 |
| Archive, no warmup | NVIDIA | cold | 1,517 | 2,351 (1,160 to 2,637) | 5,365 |
| Archive, no warmup | NVIDIA | warm | 1,056 | 419 (234 to 633) | 2,917 |
| Archive, warmup | NVIDIA | cold | 1,797 | 2,862 (2,829 to 3,741) | 6,092 |
| Archive, warmup | NVIDIA | warm | 1,490 | 691 (656 to 865) | 3,746 |
| Archive, warmup, primed program cache | NVIDIA | cold | 1,999 | 2,673 (2,619 to 2,728) | 6,171 |
| Archive, warmup, primed program cache | NVIDIA | warm | 1,332 | 677 (623 to 701) | 3,510 |

Renderer creation took 116 to 136 ms on Intel and about 1.5 s on NVIDIA. The NVIDIA runs are
noisy: after the first repetition, creation and loading (CPU work) took about twice as long in
every case, which matches a CPU clock drop, so compare NVIDIA rows only with each other.

Warmup with an idle application (renderer created, 3 s pause, then loading), 3 runs, *measured*
(ms, total without the pause):

| Case | GPU | Driver cache | First frames | Total |
| --- | --- | --- | ---: | ---: |
| Archive, no warmup | Intel | cold | 1,240 | 2,489 |
| Archive, warmup | Intel | cold | 1,138 | 2,258 |
| Archive, no warmup | Intel | warm | 65 | 1,072 |
| Archive, warmup | Intel | warm | 61 | 1,063 |
| Archive, no warmup | NVIDIA | cold | 2,292 (1,173 to 2,727) | 5,073 |
| Archive, warmup | NVIDIA | cold | 2,151 (1,036 to 2,427) | 5,320 |

**Findings and decisions.**

- A cold driver cache costs 1.2 s (Intel) to 2 s (NVIDIA) of first frames for these six assets on
  the archive path, and 2.6 s to 5 s on the runtime path. The archive path halves it, because
  materials share entries and therefore programs, and it removes the load-time material compile.
- Warmup made cold first use slower when the application loads right after it creates the
  renderer (Intel +2.0 s total, NVIDIA +0.7 s) and warm first use slower on NVIDIA (+0.8 s): its
  programs compete with the ones that the frame needs. With a 3 s pause it saved 0.23 s on Intel
  and was within the noise on NVIDIA. **Decision: no warmup.** The code, `FILLY_MATERIAL_WARMUP`,
  and the `_material_warmup` property were removed.
- The program binary cache (`glProgramBinary` through `Platform::setBlobFunc()`) did not help
  reliably. With a cold driver cache and a primed program cache, 2 of 5 Intel runs had first
  frames of 67 ms or less, 3 of 5 had no gain, and 1 of 5 warm runs took 2,051 ms. On NVIDIA it
  saved 0.2 s of 2.9 s. On the warm-cache measurement of phase 3 it made the Intel first frame
  slower. It also cannot help after a driver update, when drivers can reject old binaries (from
  the GL specification; not measured). **Decision: removed** (`native/program_cache.cpp`,
  `FILLY_SHADER_CACHE`, `_program_cache`).
- The completion callback of `Material::compile()`: Filament's `CallbackManager` runs it when every
  program token issued up to the call is released. A token is released at the program's first
  use, when its material is destroyed, or at `Engine::destroy()`. The callback is therefore not a
  "compiled" signal, and it can run after the object that registered it is gone (next section).
  Without the warmup, filly registers no such callback.
- A warmup frame after loading remains the way to keep this cost out of a timing-critical trial;
  [the API reference](../reference/api.md) says so.

### Heap-use-after-free at renderer close (the Linux segfault)

**Cause.** The warmup's compile callback captured the `ArchiveProvider` and wrote its
`warmup_pending` counter. `State::close()` deletes the provider (`delete materials`) before
`Engine::destroy()`, and `FEngine::shutdown()` then runs the pending callbacks
(`DriverBase::purge()`), which wrote into freed memory. The corruption shows up later, at a
random allocation: in `scene.load()` of the next renderer (the WSLg run of phase 3), in a pyglet
window creation, or not at all.

**Evidence**, *measured*:

- Windows, cdb with the debug heap: "Free Heap block modified after it was freed" within the
  first three renderers of a 42-renderer script with warmup; none with `FILLY_MATERIAL_WARMUP=0` and
  none after the fix (42 renderers each). Without a debugger, the same script crashed in 2 of 8
  archive runs and in 0 of 7 runtime runs.
- Linux, the pre-fix source built with `-fsanitize=address` (Clang 21, ASan runtime through
  `LD_PRELOAD`) on WSLg: `heap-use-after-free`, a 4-byte read 160 bytes into a 736-byte block, in
  the warmup lambda (`ArchiveProvider::warm_up`), called from
  `FMaterialInstance::compile()::Callback::func` < `DriverBase::purge()` < `FEngine::shutdown()` <
  `Engine::destroy()` < `State::close()` (`renderer.cpp:895`); freed by `State::close()`
  (`renderer.cpp:880`). The fixed source under ASan: 40 renderers clean, and the full suite
  442 passed, 13 skipped, 1 failed (pyglet "failed to create drawable" on llvmpipe) with no ASan
  report.
- Loops on WSLg, `test_gl_hosts.py`: the pre-fix archive wheel failed 2 of 15 file runs (one
  segmentation fault in pyglet window creation, one "failed to create drawable"); the single
  test `test_close_after_window_close_releases_everything` passed 60 of 60 runs, because the
  corruption needs a later renderer in the same process. The fixed archive wheel and the runtime
  wheel each passed 60 of 60 single runs and 15 of 15 file runs.

**Fix.** First the callback got shared ownership of its state; then the warmup was removed (see
above), which removes the callback. Test: `test_lifecycle.py::test_many_renderers_close_cleanly`
(40 renderers in a subprocess; without the debug heap it catches the old defect only sometimes).

### Tests

| Build | Windows suite | PsychoPy suite |
| --- | --- | --- |
| Runtime (default) | 442 passed, 14 skipped | 23 passed |
| Archive | 443 passed, 13 skipped | 23 passed |

New: 11 tests (`test_material_paths.py`: emission, specular-glossiness texels, F90 with and
without the extension; `test_lifecycle.py`: renderer teardown). The WSLg loops above used wheels
built before the warmup was removed, with the callback already owning its state. The final
manylinux_2_28 wheels (archive 2,497,497 bytes, runtime 5,643,247 bytes) passed on WSLg with
D3D12: archive 443 passed, 13 skipped; runtime 442 passed, 14 skipped; `test_gl_hosts.py` 5 of 5
runs each.

### Still open

- One full runtime-path run on Windows hung for more than 2 minutes in
  `test_features.py::test_texture_pixels` (`scene.load()` of a WebP or KTX2 texture) while WSL
  tests used the same GPU. The main thread waited in `_native`; all `JobSystem::loop` threads were
  idle. It did not recur in 3 runs of the file and 1 full run. A full dump is in the scratch
  directory (`C:\tmp\gaps\hang_texture_pixels.dmp`); the module has no symbols.
- gltfio squares `KHR_materials_emissive_strength` (see above). Resolved: filly follows the
  extension and differs from `gltf_viewer` for strengths other than 1.
- NVIDIA timings are noisy (CPU clock drop). A real driver update was not tested.

## Findings

These came up during the investigation and apply to the current code:

- With `precompiled_shaders=True`, `Scene.create_mesh()` without `colors` renders an unlit fade
  plane with color (0, 0, 0) instead of its base color. The archive material requires `COLOR`, and
  `Model::attach_mesh()` omits that attribute. With an all-ones color array the plane is correct.
  *Measured* with the installed module. *Fixed in phase 1.*
- filly's extended material turns on the anisotropic lobe for iridescence-only materials. In the
  test scene this differs from the isotropic lobe by up to 8 of 255. *Fixed in phase 1.*
- With `precompiled_shaders=True`, specular-only materials use the SDK transmission entry and are
  drawn as refractive objects. *Fixed in phase 1.*
- The SDK archive keeps 8 MB decompressed in memory, twice in filly.

## Experiments

The scratch files are in `C:\tmp\mat` and are not part of the repository.

| Experiment | Method |
| --- | --- |
| Module size | Scratch copy of `native/` and `CMakeLists.txt`, own build directories and venv, Visual Studio 2022, `/MAP`. Baseline, then a copy with `filamat` and `shaders` removed from the link and the two compile paths stubbed. Map attribution by symbol address. |
| SDK archive | The zstd frame from `uberarchive.lib`, parsed with the `ReadableArchive` layout; packages extracted and read with `matinfo`. Flag order checked against exact matching for all specs and features. |
| Sample-asset variants | JSON of the 150 assets in `.deps/sample-audit`, with Python models of `getMaterialKey()`, `constrainMaterial()`, `prepareConfig()`, the archive match, and filly's routing. Not an instrumented build. |
| Sampler limits and sizes | SDK `matc` and `uberz` on `base.mat.in`, `filly_lit.mat`, `filly_refr.mat`, and a `.mat` port of the diffuse-transmission material, in desktop and mobile configurations. |
| Parity, GPU cost, latency | A C++ harness linked against the SDK, using a copy of `surface_material.cpp`: a 96 x 192 UV sphere, sun plus IBL, 1024 x 1024 headless swap chain, `readPixels`. Frame time over 2,000 pipelined frames. Latency with one material per process and a per-run constant in the shader source. |
| Generated mesh color | The installed filly, `create_mesh()` of an unlit fade plane in both shader modes, with and without `colors`. |
