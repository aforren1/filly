# Upstream Filament issues

This page lists defects and gaps in Google Filament 1.77.1 that filly works around or inherits.
Each entry has the data for one upstream issue. Bugs come first, then feature requests. Each
group is in order of user impact, highest first.

For how filly relates to Filament in general, see [filly and Filament](../explanation/filament.md).

## Conventions

- **Source.** Citations of the form `path:line` refer to the Filament 1.77.1 source tarball
  (`.deps/filament-v1.77.1-source.tar.gz`). Paths are relative to the root of the Filament tree.
  Symbol checks use the 1.77.1 Windows SDK (`.deps/lib/x86_64/*/gltfio_core.lib`) and Linux SDK
  (`.deps/filament-linux/lib/x86_64/libgltfio_core.a`).
- **Upstream status.** Filament `main` was not available when this page was written (no network).
  Each entry is checked against 1.77.1 only. Before you file an issue, check `main` and the
  existing issues.
- **Specification.** Each entry names the section of the glTF 2.0 specification or of the
  extension README. The page does not quote the specification. Quote the current text in the
  issue.
- **Evidence.** "From the source" means that the behavior was found by reading the code. A
  named filly test or tool shows measured behavior.
- **filly code.** Paths are relative to the filly repository. The page names functions instead of
  line numbers, because filly's code changes often.

## Summary

| ID | Title | Type | Component | filly workaround |
| --- | --- | --- | --- | --- |
| [B1](#b1) | `KHR_materials_emissive_strength` is applied twice | Bug | gltfio | Yes |
| [B2](#b2) | Ubershader material matching selects wrong entries and can abort | Bug | gltfio | Own archive and provider |
| [B3](#b3) | Instances created after `loadResources()` have empty morph targets | Bug | gltfio | Yes |
| [B4](#b4) | One sampler with a sparse accessor disables all animations | Bug | gltfio | Partial |
| [B5](#b5) | Refraction IOR comes from F0 when `KHR_materials_ior` is absent | Bug | gltfio | Own materials |
| [B6](#b6) | Volume thickness uses the node's local scale, not its world scale | Bug | filament, gltfio | Yes |
| [B7](#b7) | Imported perspective cameras without `aspectRatio` get aspect 1 | Bug | gltfio | Yes |
| [B8](#b8) | The animator ignores samplers with one keyframe | Bug | gltfio | No |
| [B9](#b9) | `TRIANGLE_FAN` and `LINE_LOOP` primitives use an uninitialized type | Bug | gltfio | No |
| [B10](#b10) | Desktop OpenGL platforms return no `Sync` from `createSync()` | Bug | backend | Yes |
| [B11](#b11) | `AssetLoader::getNodeManager()` is declared but not defined | Bug | gltfio | Yes |
| [F1](#f1) | Map glTF node and material indices to instance objects | Feature request | gltfio | Yes |
| [F2](#f2) | Let the caller choose looping or clamping in `Animator` | Feature request | gltfio | Yes |
| [F3](#f3) | Support `KHR_animation_pointer` | Feature request | gltfio, cgltf | Yes |
| [F4](#f4) | Support `EXT_mesh_gpu_instancing` | Feature request | gltfio | Yes |
| [F5](#f5) | Support anisotropy, iridescence, and diffuse transmission | Feature request | gltfio | Own materials |

## Bugs

<a id="b1"></a>

### B1. gltfio applies KHR_materials_emissive_strength twice

- **Version and component:** Filament 1.77.1, gltfio (`AssetLoader`, every material provider).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** `AssetLoader` multiplies `emissiveFactor` by `emissiveStrength`
(`libs/gltfio/src/AssetLoader.cpp:1595-1600`). It then also sets the `emissiveStrength` parameter
(`AssetLoader.cpp:1786-1789`). Every gltfio material multiplies the factor by that parameter
again: `libs/gltfio/materials/base.mat.in:138`, `sheen.mat.in:113`, `specular.mat.in:114`,
`transmission.mat.in:111`, `volume.mat.in:123`, and the generated shader in
`libs/gltfio/src/JitShaderProvider.cpp:192` and `:199`. The emission is therefore
`factor * strength * strength`. filly's earlier investigation traced the defect to two changes:
google/filament#4975 baked the strength into the factor, and google/filament#5373 added the
shader parameter but did not remove the baking.

**Expected behavior.** `KHR_materials_emissive_strength`, section "Extending Materials": the
strength multiplies the emissive value once.

**Reproduction.** A black, lit triangle with `emissiveFactor` (0.05, 0.1, 0.02) and
`emissiveStrength` 4 must render like `emissiveFactor` (0.2, 0.4, 0.08) without the extension.
In 1.77.1 it renders like a factor of (0.8, 1.6, 0.32). filly test:
`tests/test_material_paths.py::test_emissive_strength_applies_once`. Sample assets:
EmissiveStrengthTest and CompareEmissiveStrength. With `tools/compare_reference.py`, filly differs
from `gltf_viewer` by MAE 0.0998 (max 32) and MAE 0.549 (max 87) on these assets. See
[compare with gltf_viewer](../how-to/reference-comparison.md).

**filly workaround.** `initialize_model()` in `native/renderer.cpp` sets the unscaled glTF
`emissiveFactor` on each material instance after gltfio creates the instance.
`MaterialSource::emissive_factor` in `native/gltf_prepare.h` keeps the unscaled value.

**If upstream fixes it.** Delete the restore loop in `initialize_model()` and
`MaterialSource::emissive_factor`. Update the extension table in
[filly and Filament](../explanation/filament.md#gltf-extensions).

<a id="b2"></a>

### B2. Ubershader material matching selects wrong entries and can abort

- **Version and component:** Filament 1.77.1, gltfio (`UbershaderProvider`, `ArchiveCache`, the
  shipped `uberarchive`). This is the provider of `gltf_viewer --ubershader` and of
  `filament.js`.
- **Type:** Bug (four faults; file them separately or as one issue with a list).
- **Upstream status:** Checked against 1.77.1 only.

**What happens.**

1. **Archive order decides ties.** `ArchiveCache::getMaterial()` returns the first entry whose
   blending, shading, and feature flags are compatible (`libs/gltfio/src/ArchiveCache.cpp:105-139`).
   The `base` entries mark `Ior` and `Specular` as unsupported
   (`libs/gltfio/materials/base.spec.in`). The `transmission` entries mark them optional
   (`transmission.spec.in`) and come before the `specular` entries in the archive
   (`libs/gltfio/CMakeLists.txt:145-154`). A material with only `KHR_materials_ior` or only
   `KHR_materials_specular` therefore gets a transmission material (`refractionMode:
   screenspace`, `transmission.mat.in:11`). The renderer then runs the screen-space refraction
   pass for an object that does not transmit light. filly's JSON model of the matching found 7
   of 191 sample-asset material keys on this path.
2. **Wrong UV set for the thickness texture.** `createMaterialInstance()` sets
   `volumeThicknessIndex` from `transmissionUV`, not from `volumeThicknessUV`
   (`libs/gltfio/src/UbershaderProvider.cpp:303-304`). A thickness texture on `TEXCOORD_1` with no
   transmission texture samples the wrong UV set.
3. **No match falls back to the lit opaque entry.** Without a match, `getMaterial()` returns
   entry 0 (`UbershaderProvider.cpp:210-221`). The blending mode of the glTF material is lost.
   Uniforms that entry 0 lacks are ignored without a message
   (`filament/src/MaterialInstance.cpp:51-55`). A texture for a sampler that entry 0 lacks fails
   a precondition in `SamplerInterfaceBlock::getSamplerInfo()`
   (`libs/filabridge/src/SamplerInterfaceBlock.cpp:121-126`), which aborts or throws. Example:
   the only sheen entry is lit and opaque (`sheen.spec.in`), so a sheen material with
   `alphaMode` `BLEND` or `MASK` and a sheen color texture reaches `addTextureBinding(mi,
   "sheenColorMap", ...)` (`AssetLoader.cpp:1713-1715`) on entry 0.
4. **Prefix comparison of flag names.** `strIsEqual()` compares only the length of the requested
   name (`ArchiveCache.cpp:57-59`). "Sheen" matches a "SheenColorTexture" flag if that flag comes
   first. For the 1.77.1 archive the result is the same as an exact comparison (checked for all
   19 entries and 23 features). The flag order is hash order, so a different archive can change
   the result.

Related limitation, not a bug: `prepareConfig()` removes features that no entry can hold together,
for example clearcoat with transmission, and logs a warning (`UbershaderProvider.cpp:41-102`).

**Expected behavior.** The provider selects an entry that implements the features of the glTF
material, with its blending mode, and it never aborts on a valid glTF material. A material
without transmission or volume does not refract (`KHR_materials_transmission`, section
"Extending Materials"). Each texture uses its own `texCoord` (glTF 2.0, `textureInfo` property
reference).

**Reproduction.** Run `gltf_viewer --ubershader` on:

1. A sphere whose material has only `KHR_materials_specular` (`specularFactor` 0.4). A frame
   capture shows the refraction pass and the transmission material.
2. A material with `KHR_materials_transmission` and `KHR_materials_volume`, a thickness texture
   with `texCoord` 1, and no transmission texture.
3. A sheen material with `alphaMode` `BLEND` and a `sheenColorTexture`.

filly test of its own provider for item 1:
`tests/test_material_paths.py::test_specular_only_material_is_not_refractive`. Analysis:
[material precompilation](../explanation/material-precompilation.md#matching).

**filly workaround.** filly does not use the SDK archive. It builds its own archive
(`native/materials/`) and uses its own `MaterialProvider` (`native/archive_materials.cpp`), in the
Python and the web builds.

**If upstream fixes it.** Nothing in filly. filly needs its own archive for other reasons
([B5](#b5), [B6](#b6), [F5](#f5), the orthographic refraction hook). The fix would help users of
`filament.js` and `gltf_viewer --ubershader`.

<a id="b3"></a>

### B3. Instances created after ResourceLoader::loadResources() have empty morph targets

- **Version and component:** Filament 1.77.1, gltfio (`AssetLoader::createInstance()`,
  `ResourceLoader`).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** Each instance gets its own `MorphTargetBuffer`. For the second and later
instances, `createRenderable()` appends buffer slots for `ResourceLoader` to fill
(`libs/gltfio/src/AssetLoader.cpp:817-824` and `:866-877`). `ResourceLoader::loadResources()`
fills slots only once per asset (`libs/gltfio/src/ResourceLoader.cpp:744-747`). An instance
that `createInstance()` makes after that call keeps a morph target buffer with no data. Setting
morph weights on it has no effect.

A second fault affects instances that exist before `loadResources()`: the morph tangent jobs read
the `MorphTargetBuffer` of the shared `Primitive`, which is the first instance's buffer
(`ResourceLoader.cpp:1059-1090`). Later instances get no morphed tangent frames, so lit morph
targets shade wrong. The NOTE at `AssetLoader.cpp:822-824` states this. The extended loader path
does not support morph targets on additional instances at all (`AssetLoader.cpp:880-884` and
`:904`).

**Expected behavior.** Each instance from `createInstance()` renders the same as the first
instance. The `createInstance()` documentation (`libs/gltfio/include/gltfio/AssetLoader.h:207-221`)
does not state a limit for morph targets. Morph targets are part of the mesh (glTF 2.0, section
"Morph Targets").

**Reproduction.** Load an asset with one morph target (for example, a triangle whose target
moves its vertices by 0.5 in Y). Call `loadResources()`. Call `createInstance()`. Set weight 1 on
the new instance. The triangle does not move. filly tests:
`tests/test_asset.py::test_morph_targets_of_every_model` and
`tests/test_asset.py::test_lit_morph_targets_match_across_models`.

**filly workaround.**

- Loading creates one spare instance before `loadResources()`, so the first model has gltfio's
  own morph data (`AssetData::Spare` in `native/renderer.cpp`).
- For each later instance, `fill_morph_targets()` in `native/renderer.cpp` uploads the positions
  of each target and computes tangent frames with gltfio's `TangentsJob`, under the same
  conditions as `loadResources()`.
- The SDK does not ship `TangentsJob.h`, so `native/renderer.cpp` declares `TangentsJob` with the
  1.77.1 layout. A layout change in a later SDK is undefined behavior.

**If upstream fixes it.** Delete the spare instance, `fill_morph_targets()`, and the local
`TangentsJob` declaration. Delete the two rows for them in the maintenance table of
[filly and Filament](../explanation/filament.md#maintenance-dependencies-on-filament-internals).

<a id="b4"></a>

### B4. One animation sampler with a sparse accessor disables all animations

- **Version and component:** Filament 1.77.1, gltfio (`Animator`).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** The `Animator` constructor validates every sampler of every animation. At the
first failure it logs "Disabling animation due to validation failure." and returns with no
animations (`libs/gltfio/src/Animator.cpp:263-269`). `validateSampler()` rejects sparse
accessors, accessors without a buffer view, and inputs that are not 32-bit floats
(`Animator.cpp:169-196`). One such sampler, in any animation, disables all animations of the
asset, including valid node animations. Samplers that only `KHR_animation_pointer` channels use
are also validated, although gltfio does not apply them.

**Expected behavior.** Sparse accessors and accessors without a buffer view are valid for
animation samplers (glTF 2.0, section "Sparse Accessors", and the `accessor.bufferView` property
reference). A loader applies valid animations. If it cannot read one sampler, it
skips that sampler or that animation, not all animations.

**Reproduction.** An asset with two animations. Animation 0 moves a node with a normal sampler.
Animation 1 uses a sampler whose output accessor is sparse. `Animator::getAnimationCount()`
returns 0. filly tests that load sparse and pointer samplers next to node animation:
`tests/test_animation_pointer.py::test_accessor_sparse_stride_external_and_data_uri` and
`tests/test_animation_pointer.py::test_mixed_node_and_pointer_timeline`.

**filly workaround.** Partial. `prepare_animations()` in `native/gltf_prepare.cpp` finds samplers
that only filly's property tracks use. `patch_source()` points their input and output at an
accessor that gltfio accepts (`quiet_samplers`), and `gltfio_reads()` repeats gltfio's check.
filly reads the real data itself. Node channels with sparse accessors are not worked around
(from the source; no filly test).

**If upstream fixes it.** Delete `quiet_samplers` and `gltfio_reads()`. Node animations with
sparse accessors start to work in filly.

<a id="b5"></a>

### B5. Refraction IOR comes from F0 when KHR_materials_ior is absent

- **Version and component:** Filament 1.77.1, gltfio (`JitShaderProvider`) and the surface
  shaders.
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** `JitShaderProvider` writes `material.ior` only when the material has
`KHR_materials_ior` (`libs/gltfio/src/JitShaderProvider.cpp:338-340`). Without it, the shader
derives the refraction IOR from F0 (`shaders/src/surface_shading_lit.fs:104-110`). F0 depends on
the metallic value and on `KHR_materials_specular`. A transmissive material with
`specularFactor` 0 gets an IOR of 1 and does not bend light. The ubershader path is correct: it
sets `ior` to 1.5 by default (`libs/gltfio/src/UbershaderProvider.cpp:362-363`).

**Expected behavior.** Without `KHR_materials_ior`, the IOR is 1.5 (`KHR_materials_ior`, section
"Extending Materials"). `KHR_materials_transmission` and `KHR_materials_volume` refract with that
IOR. Specular and metallic values do not change it.

**Reproduction.** Sample assets PotOfCoalsAnimationPointer (specular 0), IORTestGrid,
ABeautifulGame, and GlassHurricaneCandleHolder. filly's removed runtime material path used
gltfio's generator rules. It differed from filly's archive path by up to 148 levels on
PotOfCoalsAnimationPointer. With an explicit `KHR_materials_ior` of 1.5 on the refractive
materials, the two paths differed by at most 1. See
[material precompilation](../explanation/material-precompilation.md), cause "IOR default".

**filly workaround.** filly's provider sets `ior` to 1.5 when the material has no IOR
(`native/archive_materials.cpp`).

**If upstream fixes it.** Nothing to delete. filly output then matches `gltf_viewer` for these
assets.

<a id="b6"></a>

### B6. Volume thickness uses the node's local scale, not its world scale

- **Version and component:** Filament 1.77.1, filament (`Scene`) and gltfio (volume materials).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** gltfio scales `thicknessFactor` by `getObjectUserData()`
(`libs/gltfio/materials/volume.mat.in:119`, `libs/gltfio/src/JitShaderProvider.cpp:322-323`).
The engine fills that value with the average scale of the renderable's local transform,
`tcm.getTransform()`, not its world transform (`filament/src/details/Scene.cpp:234-237` and
`:263`). Scale on a parent node therefore does not change the thickness. A TODO and a FIXME mark
the value as temporary (`shaders/src/surface_types.glsl:32`, `Scene.cpp:234` and `:470`).

**Expected behavior.** `KHR_materials_volume`, section "Properties" (`thicknessFactor`): the
thickness is in the coordinate space of the mesh, so the node transformations that place the
mesh apply to it. A scale on the parent and the same scale on the mesh node give the same
result.

**Reproduction.** A glass mesh with `KHR_materials_volume` (`thicknessFactor` 0.6,
`attenuationDistance` 0.2) under a parent node. Scale the mesh node by 0.25 and render. Reset it,
scale the parent by 0.25, and render again. The images differ in 1.77.1. filly test:
`tests/test_volume.py::test_volume_thickness_uses_parent_and_model_scale`.

**filly workaround.** filly's volume entries compute the average scale of
`getWorldFromModelMatrix()` in the vertex shader and pass it in the `volumeScale` variable
(`_volume_vertex` in `native/materials/materials.cmake`, `native/materials/surface.mat.in`,
`native/materials/diffuse_transmission.mat.in`).

**If upstream fixes it.** If `getObjectUserData()` holds the world scale, or the engine gives a
dedicated world-scale value, replace `volumeScale` with it. That removes the vertex code from the
volume entries and gives them the engine's default depth program again.

<a id="b7"></a>

### B7. Imported perspective cameras without aspectRatio get aspect 1

- **Version and component:** Filament 1.77.1, gltfio (`AssetLoader::createCamera()`).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** When `aspectRatio` is missing, gltfio uses 1.0 and bakes it into the camera
scaling (`libs/gltfio/src/AssetLoader.cpp:1409-1413`). The application cannot tell a missing
aspect from an authored aspect of 1. Related: when `zfar` is missing, gltfio uses a far plane of
1e8 instead of an infinite projection (`AssetLoader.cpp:1403`).

**Expected behavior.** glTF 2.0, `camera.perspective` property reference: when `aspectRatio` is
undefined, the aspect ratio of the rendering viewport is used. When `zfar` is undefined, an
infinite projection is used.

**Reproduction.** An asset with a perspective camera with `yfov` 0.8 and no `aspectRatio`.
Render it into a 64 x 32 viewport. The image is stretched horizontally by a factor of 2. filly
test: `tests/test_camera_aspect.py::test_imported_camera_without_aspect_follows_target`.

**filly workaround.** filly reads the camera from its own parse (`CameraSource` in
`native/gltf_prepare.cpp`). A perspective camera without an authored aspect follows the render
target (`commit_properties()` and `initialize_cameras()` in `native/animation.inc`).

**If upstream fixes it.** Little. filly keeps its own camera table, because
`KHR_animation_pointer` can animate camera projections ([F3](#f3)).

<a id="b8"></a>

### B8. The animator ignores samplers with one keyframe

- **Version and component:** Filament 1.77.1, gltfio (`Animator::applyAnimation()`).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** `applyAnimation()` skips every channel whose sampler has fewer than two
keyframes (`libs/gltfio/src/Animator.cpp:339`). Such samplers also do not count toward the
animation duration (`Animator.cpp:288-291`). A channel with one keyframe never sets its
property.

**Expected behavior.** glTF 2.0, section "Animations", and the `animation.sampler` property
reference: the input accessor needs at least one element, not two. With one keyframe,
the property has that value at all times. (Cubic spline samplers need two keyframes.)

**Reproduction.** An animation with one channel: node translation, `LINEAR`, input `[0]`,
output `[[1, 0, 0]]`. After `applyAnimation(0, 0)` the node is still at its authored
translation. No filly test covers this case.

**filly workaround.** None. filly follows gltfio's rule so that its rest animation matches what
gltfio applies (`append_rest_animation()` in `native/gltf_prepare.cpp`).

**If upstream fixes it.** Change the rule in `append_rest_animation()` to match. One-keyframe
node animations then work in filly.

<a id="b9"></a>

### B9. TRIANGLE_FAN and LINE_LOOP primitives use an uninitialized primitive type

- **Version and component:** Filament 1.77.1, gltfio (`AssetLoader::createRenderable()`).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** `getPrimitiveType()` returns `false` for `TRIANGLE_FAN` and `LINE_LOOP`
(`libs/gltfio/src/GltfEnums.h:125-150`), because the Filament backend has no such primitive
types. `createRenderable()` logs "Unsupported primitive type" and continues
(`libs/gltfio/src/AssetLoader.cpp:761-764`). It then passes the uninitialized `primType` to
`builder.geometry()` (`AssetLoader.cpp:797`). The result is undefined. filly observed the fan of
MeshPrimitiveModes drawn as points.

**Expected behavior.** glTF 2.0, `mesh.primitive` property reference (`mode`): modes 2
(`LINE_LOOP`) and 6 (`TRIANGLE_FAN`) are valid. A loader converts them, for example a fan to a
triangle list and a loop to a closed line strip, or it skips the primitive.

**Reproduction.** Sample asset MeshPrimitiveModes. See
[material precompilation](../explanation/material-precompilation.md), cause "TRIANGLE_FAN".

**filly workaround.** None.

**If upstream fixes it.** Nothing to delete. These assets then render correctly in filly.

<a id="b10"></a>

### B10. Desktop OpenGL platforms return no Sync from createSync()

- **Version and component:** Filament 1.77.1, backend (`OpenGLPlatform`, `PlatformWGL`,
  `PlatformGLX`, `PlatformEGL`).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** `OpenGLPlatform::createSync()` is declared to return a non-null `Sync`
(`filament/backend/include/backend/platforms/OpenGLPlatform.h:311`, `UTILS_NONNULL`). The default
implementation returns `nullptr` (`filament/backend/src/opengl/OpenGLPlatform.cpp:156-158`).
Only `PlatformEGLAndroid` overrides it
(`filament/backend/src/opengl/platforms/PlatformEGLAndroid.cpp:675`). `PlatformWGL`,
`PlatformGLX`, and `PlatformEGL` do not. `Engine::createSync()` therefore produces a `Sync` with
no platform object on Windows and desktop Linux (`filament/backend/src/opengl/OpenGLDriver.cpp:2185-2200`).
A `Sync::getExternalHandle()` callback either receives a null handle or, if it is requested
after the driver ran `createSync`, waits in the list forever (`OpenGLDriver.cpp:2818-2840`). This
last point is from the source and was not measured.

**Expected behavior.** On platforms that have `GL_ARB_sync` (OpenGL 3.2 and later), `createSync()`
inserts a GL fence in Filament's command stream and returns it. If a platform cannot, the
documentation says so and `Engine::createSync()` reports it.

**Reproduction.** On Windows, create an engine with the default platform, call
`Engine::createSync()`, flush, and call `Sync::getExternalHandle()`. The callback receives no
fence. filly's shared-texture tests show why an application needs the fence:
`tests/test_interop.py`, `tests/test_gl_hosts.py`, and `tests/test_imported_texture.py`.

**filly workaround.** `SyncPlatform` in `native/gl_interop.cpp` subclasses `PlatformWGL`,
`PlatformGLX`, and filly's EGL platform. Its `createSync()` runs queued requests on the driver
thread: `glFenceSync`, `glWaitSync` on host fences, and the `GL_FRAMEBUFFER_SRGB` setting. See
[shared OpenGL textures](../explanation/design.md#shared-opengl-textures).

**If upstream fixes it.** Part of `SyncPlatform`: the fence that Filament signals for the host.
filly still needs a platform hook to wait on host fences and to set `GL_FRAMEBUFFER_SRGB` in
Filament's command stream.

<a id="b11"></a>

### B11. AssetLoader::getNodeManager() is declared but not defined

- **Version and component:** Filament 1.77.1, gltfio (`AssetLoader`).
- **Type:** Bug.
- **Upstream status:** Checked against 1.77.1 only.

**What happens.** `libs/gltfio/include/gltfio/AssetLoader.h:256` declares
`NodeManager& getNodeManager() noexcept;`. Only the private `FAssetLoader` defines a member with
that name (`libs/gltfio/src/AssetLoader.cpp:306-308`). The public forwarding functions at
`AssetLoader.cpp:1944-1995` do not include it. No SDK library exports the symbol: the four
Windows `gltfio_core.lib` variants and the Linux `libgltfio_core.a` contain
`AssetLoader::getNames()` but not `AssetLoader::getNodeManager()`.

**Expected behavior.** A public method in an SDK header links.

**Reproduction.** Call `loader->getNodeManager()` in an application that links the SDK's
`gltfio_core`. The link fails with an unresolved external symbol.

**filly workaround.** filly cannot restore entity extras after it reads its node markers
([F1](#f1)), so entities keep the marker strings. filly does not expose extras. See
`create_instance()` in `native/renderer.cpp`.

**If upstream fixes it.** filly can restore the authored node extras after `createInstance()` and
can then expose them. A fix for [F1](#f1) makes the markers unnecessary.

## Feature requests

<a id="f1"></a>

### F1. Map glTF node and material indices to instance objects

- **Version and component:** Filament 1.77.1, gltfio (`FilamentInstance`, `MaterialProvider`).
- **Type:** Feature request.
- **Upstream status:** Checked against 1.77.1 only.

**Current behavior.** Each instance has a private map from glTF node index to entity
(`FFilamentInstance::mNodeMap`, `libs/gltfio/src/FFilamentInstance.h:106`, filled at
`AssetLoader.cpp:633`). The animator uses it, but no public function returns it. Name lookups
cover all instances of an asset and fall back to the mesh, light, or camera name, so names do
not identify a node. `MaterialProvider::createMaterialInstance()` receives a key, a UV map, a
label, and the extras, but not the glTF material index
(`libs/gltfio/include/gltfio/MaterialProvider.h:156-157`).

**Request.** Add `FilamentInstance::getEntityForNode(size_t nodeIndex)` (or the full array) and
pass the glTF material index to `createMaterialInstance()`, or add an equivalent map per
instance.

**filly workaround.** While `createInstance()` runs, `create_instance()` in `native/renderer.cpp`
points gltfio's parse at a string of index markers (`#n<index>` for nodes, `#m<index>` for
materials). gltfio copies node extras to each entity and passes material extras to the provider
(`AssetLoader.cpp:623-628` and `:1564-1574`). filly reads the markers back with
`FilamentAsset::getExtras()` and `material_marker()` in `native/archive_materials.cpp`. The parse
is restored before the function returns. A missing marker raises `AssetError`. Test:
`tests/test_node_tree.py::test_nodes_names_indices_and_hierarchy`.

**If upstream adds it.** Delete the marker code in `create_instance()` and `material_marker()`,
and the first row of the maintenance table in
[filly and Filament](../explanation/filament.md#maintenance-dependencies-on-filament-internals).

<a id="f2"></a>

### F2. Let the caller choose looping or clamping in Animator

- **Version and component:** Filament 1.77.1, gltfio (`Animator::applyAnimation()`).
- **Type:** Feature request. The glTF specification does not define playback, so the wrap is an
  API gap, not a specification violation.
- **Upstream status:** Checked against 1.77.1 only.

**Current behavior.** `applyAnimation()` wraps the time with `fmod(time, duration)`, except when
the time equals the duration (`libs/gltfio/src/Animator.cpp:334`). A time after the end shows
the start of the clip, not the end. The header documents the time only as "Elapsed time of
interest in seconds" (`libs/gltfio/include/gltfio/Animator.h:41-48`).

**Request.** Document the wrap, and add a clamp option, for example
`applyAnimation(index, time, bool loop = true)`. glTF 2.0, section "Animations", defines
values from the first to the last keyframe and leaves looping to the application.

**filly workaround.** `Model::apply_animation()` in `native/features.inc` takes `loop`. With
`loop=False` it clamps the time to just below gltfio's duration (`std::nextafter`), so that
`fmod` does not wrap it. Tests: `tests/test_animation_pointer.py` and `tests/test_features.py`
use `loop=False`.

**If upstream adds it.** Replace the `nextafter` clamp with the option.

<a id="f3"></a>

### F3. Support KHR_animation_pointer

- **Version and component:** Filament 1.77.1, gltfio (`Animator`), cgltf 1.15
  (`third_party/cgltf`).
- **Type:** Feature request.
- **Upstream status:** Checked against 1.77.1 only.

**Current behavior.** cgltf 1.15 does not parse the extension. Its animation paths are only
translation, rotation, scale, and weights (`third_party/cgltf/cgltf.h:225-230`). A pointer channel
has no target node and an invalid path, and keeps the extension as raw JSON. The animator skips
channels without a target node (`libs/gltfio/src/Animator.cpp:478-480`). Pointer samplers can
still disable all animations ([B4](#b4)).

**Request.** Apply pointer channels for node properties at least (the same as the core paths),
and for material, light, and camera properties if possible.

**filly workaround.** `prepare_animations()` in `native/gltf_prepare.cpp` reads the pointer from
the raw JSON. Pointers to node translation, rotation, scale, and weights become ordinary node
channels in gltfio's parse (`patch_source()`). Other targets (materials, texture transforms,
lights, cameras, node visibility) are filly's property tracks. Tests:
`tests/test_animation_pointer.py`, `tests/test_light_pointer.py`.

**If upstream adds it.** Node pointers: delete the `node_channels` patch. Other targets: depends
on the scope of the upstream support.

<a id="f4"></a>

### F4. Support EXT_mesh_gpu_instancing

- **Version and component:** Filament 1.77.1, gltfio.
- **Type:** Feature request.
- **Upstream status:** Checked against 1.77.1 only.

**Current behavior.** cgltf parses the extension (`has_mesh_gpu_instancing`,
`third_party/cgltf/cgltf.h:708`). gltfio does not read it, so a node renders once instead of once
per instance.

**Request.** Create one renderable per instance, or use Filament's instancing
(`RenderableManager::Builder::instances()` with an `InstanceBuffer`).

**filly workaround.** `expand_instances()` in `native/gltf_prepare.cpp` rewrites the document
before gltfio reads it: each instance becomes a child node. The children share a primitive and
a material instance, so Filament's automatic instancing (`Engine::setAutomaticInstancingEnabled`,
on in filly) draws up to 64 of them per call. Tests:
`tests/test_scene_extensions.py::test_instances_match_explicit_hierarchy` and the other instance
tests in that file.

**If upstream adds it.** Delete `expand_instances()`.

<a id="f5"></a>

### F5. Support KHR_materials_anisotropy, KHR_materials_iridescence, and KHR_materials_diffuse_transmission

- **Version and component:** Filament 1.77.1, gltfio (`AssetLoader`, both material providers).
- **Type:** Feature request.
- **Upstream status:** Checked against 1.77.1 only.

**Current behavior.** cgltf parses all three (`has_iridescence`, `has_diffuse_transmission`,
`has_anisotropy`, `third_party/cgltf/cgltf.h:551-553`). gltfio does not read them; its sources
contain no reference to them. Filament's shading model has anisotropy and iridescence lobes
(`shaders/src/surface_brdf.fs`, `surface_shading_lit.fs`). Diffuse transmission has no engine
lobe.

**Request.** Map anisotropy and iridescence to the existing material inputs in `MaterialKey`, the
JIT generator, and the ubershader archive. Consider diffuse transmission as custom surface
shading.

**filly workaround.** filly's archive has entries for these extensions, and its provider binds
their textures through generic sampler slots (`native/materials/`,
`native/archive_materials.cpp`). Tests: `tests/test_gltf_surface_camera.py::test_anisotropy_iridescence_clearcoat_and_sheen_load_strictly`,
`tests/test_material_paths.py`, and `tests/test_features.py::test_diffuse_transmission_backlight`.

**If upstream adds it.** Little. filly keeps its own archive for the reasons in [B2](#b2). gltfio
would then decode these textures, and filly's provider could use gltfio's bindings.

## Items not listed

- **Shipped ubershader archive size.** filly measured that the SDK's archive packages contain
  Vulkan SPIR-V and unoptimized GLSL. filly does not use that archive, so this page does not list
  it.
- **Skinned mesh bounding boxes.** gltfio's culling boxes for skinned meshes under a scaled
  armature are wrong (see [validation](validation.md)). This page does not cover it, because
  filly's handling is changing to `FilamentInstance::recomputeBoundingBoxes()`.
