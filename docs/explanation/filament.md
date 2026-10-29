# filly and Filament

This page is for users and contributors who know Google Filament. It maps filly's concepts to
Filament 1.77.1 and gltfio, explains where filly differs, and lists the places where filly
depends on Filament internals. For the full design, see [design and limits](design.md). For the
Filament defects and gaps that filly works around, with the data for upstream issues, see
[upstream Filament issues](../reference/upstream-issues.md).

filly is a thin layer over Filament for most tasks. It exists for these reasons:

- It renders into a texture of the host's OpenGL context (PsychoPy, pyglet, moderngl, zengl)
  through a shared context. The host draws that texture. No pixels go through the CPU.
- Its output encoding is exact: each 8-bit level is the rounded analytic sRGB (or linear) value.
- The caller controls timing. Each `render()` is one Filament frame, and nothing runs between
  calls.
- Python and JavaScript users get errors instead of crashes. Each handle checks that it and its
  renderer are open, and that the call comes from the renderer's thread.
- The same C++ core compiles to WebAssembly and renders into the WebGL2 context of the page's own
  renderer (PIXI in PsychoJS).

## Concept mapping

| filly | Filament or gltfio | Relation |
| --- | --- | --- |
| `Renderer` | `Engine`, `Renderer`, a 1 x 1 headless `SwapChain`, gltfio `AssetLoader`, a `MaterialProvider` | One object owns all of them. Each `render()` is one `beginFrame()`, `render(view)`, `endFrame()` |
| `Scene` | `Scene` and one `View` | One object; see [why a scene is also a view](#why-a-scene-is-also-a-view) |
| Scene rendering options (`msaa`, `ssao`, `bloom`, `fog`, ...) | `View` options, `ColorGrading`, `ToneMapper` | The same Filament options, as flat properties. Some have different units; see [conventions](#conventions) |
| `renderer.render(scene, target, camera=, viewport=, clear=)` | `View::setCamera()`, `View::setViewport()`, `View::setRenderTarget()`, `Renderer::setClearOptions()` | Set for one call, then restored |
| `Camera` | `Camera` component | The same object. The renderer owns cameras, as the engine does in Filament |
| `Camera.frame()` | No equivalent | filly's own; see [what filly adds](#what-filly-adds) |
| `Asset` | gltfio `FilamentAsset` | Renamed. Closing it calls `releaseSourceData()` |
| `renderer.create_mesh_asset()` | A one-triangle gltfio asset whose renderables get filly's `VertexBuffer` and `IndexBuffer` (`setGeometryAt()`) | See [runtime textures and generated meshes](design.md#runtime-textures-and-generated-meshes) |
| `Model` | gltfio `FilamentInstance` in one scene | Renamed. `scene.add(asset)` calls `AssetLoader::createInstance()` |
| `scene.load()` | `createAsset()`, `ResourceLoader::loadResources()`, `Scene::addEntities()` | Load, one instance, and source release in one call |
| `Node` | An entity of the instance, with its `TransformManager` component | Addressed by glTF name or glTF index |
| `Material` | `MaterialInstance` of the instance | Addressed by glTF material name |
| `node.material(slot)` | `MaterialInstance::duplicate()` and `RenderableManager::setMaterialInstanceAt()` | A copy for one primitive of one node |
| `Texture` | `Texture` with a `TextureSampler` | Pixels from NumPy or JavaScript arrays |
| `ImportedTexture` | `Texture::Builder::import()` | A host OpenGL texture, with fences in both directions |
| `RenderTarget` | `RenderTarget` with color and depth `Texture` objects, `Renderer::readPixels()` | Same |
| `ImportedTarget` | `RenderTarget` on an imported host texture | Adds fences and the `acquire()` and `release()` protocol |
| `Light` | `LightManager` component | Scene lights and glTF lights (`KHR_lights_punctual`) |
| `Environment` | `IBLPrefilterContext` output, or `ktxreader` cubemaps | Shared textures. Each scene builds its own `IndirectLight` and `Skybox` |
| `model.apply_animation()` | gltfio `Animator::applyAnimation()` and `updateBoneMatrices()` | Adds a reset to the rest state and property animation |
| `model.apply_variant()` | `FilamentInstance::applyMaterialVariant()` | Same |
| `node.set_morph_weights()` | `RenderableManager::setMorphWeights()` | Same |
| `renderer.prepare()` | `Material::compile()` | Adds a poll of the browser's parallel shader compilation |
| `renderer.pick()`, `PickQuery`, `scene.pick_blended` | `View::pick()`, `View::setTransparentPickingEnabled()` | Queued on the scene and given to its view at its next render. filly adds the `Model` and `Node` of the renderable, the world position from the frame's camera, and the frame number. Answers arrive through `Engine::pumpMessageQueues()` in `ready()` |
| `renderer.reset_gl_state()` | `Engine::resetBackendState()` | Same |
| `Model.memory`, `Asset.memory` | No equivalent | filly's own estimate |
| `renderer.stats.gpu_frame_ms`, `gpu_frame_number` | `Renderer::getFrameInfoHistory()`, `gpuFrameDuration` | The GPU time of one earlier frame, named by filly's frame number. In WebGL, filly's own `EXT_disjoint_timer_query_webgl2` query, because Filament's OpenGL backend compiles out timer queries under Emscripten (`OpenGLContext.cpp`) |

## Why a scene is also a view

In Filament, a `Scene` holds entities, lights, and the environment, and a `View` holds the
camera, the viewport, the render target, and the rendering options. One scene can have several
views. In filly, a `Scene` has exactly one view, and the scene's properties are the view's
options.

What filly does not have:

- Several views of one scene with different options in one Filament frame. For example, filly
  cannot render one scene with bloom and without bloom in the same frame.
- View-level visibility layers (`View::setVisibleLayers()`).

What replaces them:

- Each `render()` call takes its own camera and viewport. `clear=False` keeps the rest of the
  target. Two calls give side-by-side stereo into one target. See
  [render calls](../reference/api.md#render-calls).
- For two sets of options, use two scenes. Cameras, assets, and environments belong to the
  renderer, so both scenes can use them. Each scene adds its own models from the shared asset.

Why filly merges them:

- A stimulus program renders one view into one target per call. A separate view object would add
  one more object to create, connect, and close, with no gain for that use.
- The options and the scene content usually change together between trials.
- Each `render()` is one Filament frame with one scene view. filly's
  [output passes](design.md#output-encoding) run as more views in the same frame, so filly
  controls the order of all views in a frame.

The cost: a second view of the same scene is a second `render()`, which is a second Filament
frame with its fixed cost. On the tested Intel GPU, one 1920 x 1080 frame of a lit sphere took
1.8 ms, and two 960 x 1080 eyes took 3.2 ms (direct output path, see
[render calls](../reference/api.md#render-calls)).

## Conventions

| Quantity | filly | Filament |
| --- | --- | --- |
| Matrices | Row-major 4 x 4 arrays for column vectors. Translation is in `matrix[:3, 3]` | Column-major `mat4f` for column vectors. The math is the same |
| Spot cone | Inner and outer half-angles in degrees, which read back as set | Radians. Filament keeps derived values only |
| Field of view, rotations | Degrees | Degrees for `setProjection()`, radians or matrices elsewhere |
| Exposure | EV100 (`camera.exposure`) | Aperture, shutter time, and ISO |
| Depth-of-field aperture | A separate f-number that does not change exposure | The exposure aperture |
| Fog color | Linear output color | Multiplied by the IBL luminance and the exposure |
| Point and spot intensity | Candela | Candela or lumens |
| Imported perspective camera without `aspectRatio` | Follows the render target, as glTF specifies | gltfio uses an aspect of 1 |

## What filly adds

Each item below has no equivalent in Filament 1.77.1, or the equivalent does not do what filly
needs. The linked sections give the measurements.

- **Output encoding.** By default, the scene view renders scene-linear color into an RGBA16F
  buffer. filly's encode pass then writes exact 8-bit levels, sets alpha, and applies dithering
  and FXAA after the encoding. Filament's `ColorGrading` runs only for options that need it, such
  as a non-linear tone mapper or bloom. See [output encoding](design.md#output-encoding).
- **Shared OpenGL textures.** Filament's desktop OpenGL platforms (WGL, GLX, and EGL) do not
  implement `createSync()`. filly subclasses the WGL, GLX, and EGL platforms to place fences in Filament's command stream, and
  renders into an sRGB texture view of the host's texture. See
  [shared OpenGL textures](design.md#shared-opengl-textures).
- **Material archive.** filly uses gltfio's archive format (`matc`, `uberz`) with its own
  materials and its own `MaterialProvider`. The SDK archive lacks anisotropy, iridescence,
  diffuse transmission, and the orthographic refraction blur, and its matching drops features.
  See [material precompilation](material-precompilation.md).
- **glTF preparation.** filly reads each document with cgltf before gltfio does. It checks
  extensions, decodes data that gltfio cannot read, and builds the tables for the features in the
  next section. See [glTF preparation](design.md#gltf-preparation).
- **Instances that close.** gltfio has no `destroyInstance()`. Closing a model removes its
  entities from the scene; the asset and its instances go when the asset handle and the last
  model close. Later instances also get morph target data, which gltfio uploads only for the
  instances that exist when `loadResources()` runs. See [ownership](design.md#ownership).
- **Lookups by glTF index.** gltfio's name lookup covers all instances of an asset, and its names
  fall back to the mesh, light, or camera name. filly maps each entity of an instance to its glTF
  node index and name.
- **Node-local materials and runtime textures.** A node can get its own copy of a material, and a
  material can get a texture from an array or from the host. The copies follow variants and
  property animation.
- **Animation from a known state.** gltfio's animator keeps the last clip's values for channels
  that the next clip does not animate. filly restores the authored state first, so a time jump or
  a clip change does not depend on history.
- **Camera framing.** `camera.frame()` moves the camera so that a model fills a fraction of the
  view. `gltf_viewer` instead scales the model into a unit cube (`fitIntoUnitCube()` in
  `libs/viewer`), and `camutils::Manipulator` has no fit to bounds.
- **Rest-pose bounds.** `Model.bounds`, `Node.bounds`, and the culling boxes of skinned
  renderables come from filly's preparation, not from gltfio. `FilamentAsset::getBoundingBox()`
  moves a skinned mesh's accessor box by its node transform, which glTF says to ignore.
  `FilamentInstance::recomputeBoundingBoxes()` skins correctly, but it ignores morph targets on
  skinned meshes and does not check joint attributes. It was also 2.7 times slower on a skinned
  primitive with 1,000,000 vertices. See
  [runtime textures and generated meshes](design.md#runtime-textures-and-generated-meshes).
- **Generated meshes and shapes.** Meshes from arrays get a glTF material from the archive and
  the same model API as loaded assets. The models of a mesh asset share its buffers and, until
  a model's material is edited, one material instance, so automatic instancing merges them.
- **Memory estimates.** Filament and gltfio report no memory per asset.
- **Host integrations.** Adapters for PsychoPy, pyglet, moderngl, and zengl.
- **Web build.** `filament.js` creates its engine from a canvas and binds neither
  `Texture::Builder::import()` nor `Engine::resetBackendState()`, and it uses gltfio's
  ubershader provider. filly's web build renders into a texture of the page's context with
  filly's own materials. See [the web build](web.md).

### glTF extensions

| Extension | gltfio 1.77.1 | filly |
| --- | --- | --- |
| `KHR_materials_unlit`, `clearcoat`, `sheen`, `transmission`, `volume`, `ior`, `specular`, `dispersion`, `pbrSpecularGlossiness`, `variants` | Yes | Through gltfio, with filly's material entries |
| `KHR_materials_emissive_strength` | Yes, but the strength applies twice | Corrected |
| `KHR_texture_transform`, `KHR_lights_punctual` | Yes | Through gltfio |
| `KHR_texture_basisu` | Yes (`Ktx2Provider`) | Through gltfio |
| `EXT_texture_webp` | Only in builds with `FILAMENT_SUPPORTS_WEBP_TEXTURES`, which the release SDK does not set | filly's WebP provider, which follows gltfio's |
| `KHR_draco_mesh_compression` | Yes | Through gltfio; `python/tests/test_draco.py` and a browser test check the decoder |
| `EXT_meshopt_compression` | Yes, with meshoptimizer 0.18 | Decoded by filly with meshoptimizer 1.0, which adds the version 1 codec and the COLOR filter |
| `KHR_meshopt_compression` | No | Decoded by filly |
| `EXT_mesh_gpu_instancing` | No | One child node per instance. The children share a primitive and a material instance, and filly turns on `Engine::setAutomaticInstancingEnabled(true)`, so Filament draws up to 64 per call (8 in WebGL). See [performance](../reference/performance.md#automatic-instancing) |
| `KHR_animation_pointer` | No | Node channels go to gltfio's animator; the other targets are filly's property animation |
| `KHR_node_visibility` | No | filly |
| `KHR_materials_anisotropy`, `iridescence`, `diffuse_transmission` | No (cgltf reads them; gltfio ignores them) | filly's material entries |
| Unknown extensions | Ignored | A warning, or an error for required extensions and strict loads |

## Maintenance: dependencies on Filament internals

Some code depends on Filament behavior that the SDK's public headers do not promise. Review
these items when you change the Filament version. If an upstream fix in the new version
removes the need for an item, see [upstream Filament issues](../reference/upstream-issues.md)
for what to delete. "Loud" means that a change makes filly fail
with an error at build or load time; "silent" means that a change can give wrong results without
an error.

| Dependency | filly code | Filament code | Failure |
| --- | --- | --- | --- |
| Node and material index markers: during `createInstance()`, gltfio reads extras as `json + offset` | `create_instance()` in `core/renderer.cpp`, `material_marker()` in `core/archive_materials.cpp` | `recurseEntities()` and `createMaterialInstance()` in `libs/gltfio/src/AssetLoader.cpp` | Loud: a missing marker raises `AssetError` |
| Changes to gltfio's cgltf parse: buffers, meshopt flags, animation channels, samplers, image types, and the appended rest animation | `patch_source()` in `core/gltf_prepare.cpp` | `FilamentAsset::getSourceAsset()`, and the cgltf version in `third_party/cgltf` | Silent. `core/vendor/cgltf/cgltf.h` must be identical to Filament's copy |
| `TangentsJob`, declared in filly because the SDK does not ship its header | `namespace filament::gltfio` block in `core/renderer.cpp` | `libs/gltfio/src/TangentsJob.h` | Silent: a layout change is undefined behavior |
| Morph target upload for later instances, which repeats the conditions of `loadResources()` | `fill_morph_targets()` in `core/renderer.cpp` | `libs/gltfio/src/ResourceLoader.cpp` | Silent; a pixel test compares two instances |
| Buffer layouts for the memory estimate | `estimate_geometry()` in `core/gltf_prepare.cpp` | `createPrimitive()` and `createRenderable()` in `AssetLoader.cpp`, `computeBindingSize()` in `ResourceLoader.cpp` | Silent: wrong estimates only |
| Animator rules: one sampler that it cannot read disables all animation; samplers with fewer than two keyframes are skipped; translation, rotation, and scale are kept per node | `gltfio_reads()` and `append_rest_animation()` in `core/gltf_prepare.cpp` | `libs/gltfio/src/Animator.cpp`, `TrsTransformManager` | Silent |
| gltfio's material parameter names and the order of `createMaterialInstance()` and texture binding | `core/archive_materials.cpp` | `AssetLoader.cpp` | Partly loud: `matc` rejects unknown material inputs |
| Orthographic refraction blur: the `sampler0_ssr` name and the order of the generated shader | `core/refraction.h` | `shaders/src/surface_light_indirect.fs` | Loud for a missing hook (`core/materials/check_refraction.cmake`); silent for changed semantics |
| World-scale volume thickness, which replaces the local scale in `getObjectUserData()` | `core/materials/materials.cmake` | `shaders/src/surface_types.glsl` (an upstream TODO) | Silent |
| Custom surface shading inputs (`ShadingData`, `LightData`) | `core/materials/diffuse_transmission.mat.in` | `shaders/src` | Loud: `matc` fails |
| Debug property `d.renderer.disable_subpasses` | `Renderer` constructor in `core/renderer.cpp` | `filament/src/details/Renderer.cpp` | Loud: `BackendError` |
| Feature flag `engine.color_grading.use_1d_lut` | `Renderer` constructor in `core/renderer.cpp` | `filament/src/details/ColorGrading.cpp` | Loud: `BackendError` |
| Fog color scale (IBL luminance times exposure) and the depth-of-field circle of confusion | `apply_fog()` and `apply_depth_of_field()` in `core/renderer.cpp` | `shaders/src/surface_fog.fs`, `filament/src/PostProcessManager.cpp` | Silent |
| Code copied from Filament: FXAA 3.11, the dithering pattern, `SurfaceOrientation` helpers, the WebP provider | `core/materials/fxaa.mat`, `encode.mat.in`, `core/slots.inc`, `core/webp_provider.cpp` | `shaders`, `libs/geometry`, `libs/gltfio` | Drift only |
| GPU frame times matched to `render()` calls by `beginFrame` steady-clock time (Filament records it in `beginFrameInternal`, which can run in `endFrame()`) | `Renderer::stats()` and `frame_spans` in `core/renderer.cpp` | `FrameInfoManager::beginFrame` and `updateUserHistory` in `filament/src/FrameInfo.cpp`; `FRenderer::beginFrame` in `filament/src/details/Renderer.cpp` | Loud: `test_gpu_frame_time_names_its_frame` alternates 16 x 16 and 2048 x 2048 frames |
| Picking: Filament answers from its structure buffer, which has the ambient-occlusion resolution and at least 32 texels per side, at texel `floor(x * resolution)`; the stored depth is clip z / w after the camera's scaling, shift, and reversed-Z remap | `submit_picks()` and `pick_minimum()` in `core/renderer.cpp`, `ao.resolution` in `Renderer::create_scene()` and `Scene::set_ssao()` | `PostProcessManager::structure()`, `transparentPicking()`, `FView::executePickingQueries()`, `FCamera::getProjectionMatrix()`, `shaders/src/surface_depth_main.fs` | Silent: positions off by up to a pixel or wrong in depth. `python/tests/test_pick.py` checks positions against analytic planes, also with SSAO |
| Library functions without SDK headers: zstd, `stb_image`, cgltf | `core/archive_materials.cpp`, `core/renderer.cpp`, `core/vendor/cgltf` | `zstd.lib`, `stb.lib`, `gltfio_core.lib` | Loud at link time, except the cgltf structure layout |
| Local corrections in the Linux source build | The SDK build manifest | Fence waits and others | See [design and limits](design.md#dependencies) |

Material packages carry the material version of the engine. The build compiles the archive with
the SDK's `matc`, so a version change rebuilds it.
