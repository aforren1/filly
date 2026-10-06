# Thin Python Renderer over Google Filament

This document states the requirements. Notes marked **Decision** record choices made during
implementation and why. Items marked **Open** are not implemented. The API reference is
[docs/reference/api.md](docs/reference/api.md); this document does not repeat it.

## 1. Purpose

Implement a small Python-accessible real-time 3D renderer built on Google Filament, intended primarily for behavioral experiments, psychophysics, stimulus presentation, and other applications that need:

- lightweight 3D rendering without adopting a game engine;
- glTF/GLB loading;
- predictable per-frame control;
- low overhead for small scenes;
- orthographic and perspective cameras;
- direct manipulation of object transforms;
- rendering into textures;
- interoperability with existing OpenGL applications such as PsychoPy and Psychtoolbox;
- optional standalone/offscreen operation;
- Python bindings through nanobind.

The library is **not** intended to expose the complete Filament API or become a general-purpose scene/game engine.

Filament remains an implementation detail wherever practical.

---

## 2. Design goals

### 2.1 Primary goals

1. **Small API and binary footprint**

   Do not replicate Open3D or wrap every Filament class. Bind only functionality required for loading, manipulating, and rendering modest 3D scenes.

2. **Real-time rendering**

   Rendering should comfortably support normal experimental display rates such as 60, 120, 144, and higher refresh rates for modest scenes.

   Filament itself is designed as a real-time PBR renderer, including mobile targets.

3. **Deterministic host-controlled presentation**

   The application embedding the renderer owns experimental timing and presentation.

   In PsychoPy/PTB integration, Filament should render content, but PsychoPy/PTB should remain responsible for the actual display-buffer swap.

4. **Efficient GL interoperability**

   Under the OpenGL backend, Filament should be able to create its internal context in the same GL share group as an existing application context.

   Filament's `Engine::Builder::sharedContext(void*)` API supports supplying a platform-specific shared context when Filament creates its internal context.

5. **glTF as the primary asset format**

   Use Filament's `gltfio` rather than implementing another model importer.

6. **Minimal-copy operation**

   Shared-context rendering should allow Filament output to remain GPU-resident and be consumed directly by the host application.

7. **Explicit performance behavior**

   Features that can change workload or frame semantics dynamically must not silently activate.

---

## 3. Non-goals

The initial implementation does not attempt to provide:

- physics;
- collision detection;
- input/event handling;
- window management for embedded operation;
- GUI widgets;
- animation timelines beyond what is necessary to expose glTF animation;
- scene graphs beyond the hierarchy already present in imported assets;
- arbitrary shader authoring;
- a Python representation of every Filament class;
- Vulkan/Metal interoperability with PsychoPy/PTB in version 1;
- geometry processing, point clouds, ICP, reconstruction, tensor operations, or Open3D-equivalent functionality;
- game-engine systems such as ECS scripting, audio, networking, or asset databases.

---

# 4. Architecture

```text
Python application
        │
        ▼
    nanobind
        │
        ▼
Small C++ facade (native/renderer.h)
 ┌─────────────────────────┐
 │ Renderer                │
 │ Scene                   │
 │ Model, Node             │
 │ Camera                  │
 │ Light                   │
 │ Material                │
 │ Texture, HostTexture    │
 │ OffscreenTarget         │
 │ ImportedTarget          │
 └─────────────────────────┘
        │
        ▼
 Google Filament 1.77.1
 ┌─────────────────────────┐
 │ Engine                  │
 │ Renderer / View / Scene │
 │ TransformManager        │
 │ RenderTarget            │
 │ gltfio                  │
 └─────────────────────────┘
        │
        ▼
 OpenGL (WGL on Windows, GLX on Linux)
```

The C++ facade is the stable public implementation API. Its header includes neither Python nor Filament headers.

Python must not directly manage Filament resource lifetimes.

glTF preparation runs in the native core as part of loading (`native/gltf_prepare.cpp`). It checks extensions, decodes data that the pinned `gltfio` cannot read, and builds the tables for the features that this project adds (section 7). Python only binds it.

---

# 5. Python package

Package name:

```python
import filly
```

Minimum supported Python versions should follow the currently practical nanobind ecosystem rather than historical Python releases.

**Decision.** Python 3.12 and later use one limited-ABI (`cp312-abi3`) wheel. Python 3.10 and 3.11 use version-specific wheels; the PsychoPy adapter is tested on 3.11.

The extension uses nanobind directly rather than pybind11.

---

# 6. Core public API

## 6.1 Renderer

`Renderer` owns the Filament engine and renderer infrastructure.

```python
renderer = filly.Renderer()                          # offscreen
renderer = filly.Renderer(shared_context=handle)     # shares a host OpenGL context
```

Constructor:

```python
Renderer(*, shared_context=None)
```

**Decision.** There is no `backend`, `threaded`, or `debug` argument. The build contains only the OpenGL backend (section 25). Filament always runs its own driver thread. Every glTF material uses filly's precompiled material archive, so there is no shader option (see docs/explanation/material-precompilation.md).

`shared_context` is the current host WGL or GLX context handle, from `filly.current_gl_context()`.

Filament's engine is associated with a hardware rendering context and manages a render/driver thread internally.

### Methods

```python
renderer.create_scene()
renderer.create_render_target(width=..., height=...)
renderer.import_gl_texture(texture_id, width=..., height=...)
renderer.create_texture(pixels, color_space=...)
renderer.import_gl_input(texture_id, width=..., height=..., color_space=...)
renderer.render(scene, target, camera=None, viewport=None, clear=True)
renderer.finish()
renderer.close()
renderer.stats
```

**Decision.** `render()` flushes its commands to the driver, so there is no `flush()`. A target is required; there is no window target (section 11.1).

**Decision (per-render camera and viewport).** `camera` replaces the scene camera for one call. `viewport` is (x, y, width, height) in target pixels from the lower-left corner. `clear=False` keeps the target outside the viewport, so two eyes can render side by side into one target; the viewport itself always starts from the background, never from an earlier frame. Each call is one frame and allocates no memory for these options. Imported targets keep the acquire rules of section 14.

`finish()` is explicitly blocking and exists primarily for testing, shutdown, screenshots, and diagnostics.

Normal frame rendering must not implicitly perform a full GPU finish.

---

## 6.2 Scene

```python
scene = renderer.create_scene()
```

Responsibilities:

- collection of models;
- lights;
- environment;
- active camera;
- scene-level rendering options.

Example:

```python
model = scene.load("stimulus.glb")
scene.add_directional_light(direction=(-1, -1, -1))
scene.camera = camera
scene.antialiasing = "fxaa"
```

The wrapper maps this to Filament's `Scene`, `View`, camera, and entity infrastructure.

**Decision.** Rendering options are scene properties (`encoding`, `tone_mapping`, `antialiasing`, `msaa`, `shadows`, `refraction`, `transparent`, `dithering`, `ssao`, `bloom`, `fog`, `depth_of_field`, `vignette`). An assignment validates one value and changes only that option.

---

## 6.3 Model

A `Model` represents an instantiated glTF/GLB asset.

```python
model = scene.load("stimulus.glb")
```

Required properties:

```python
model.transform
model.position
model.rotation_euler_deg
model.scale
model.visible
```

Prefer NumPy-compatible transforms.

Example:

```python
model.position = (0.0, 0.0, -5.0)
model.rotation_euler_deg = (0.0, 30.0, 0.0)
```

Rotation units must be explicit:

```python
model.rotation_euler_deg
model.rotation_euler_rad
model.quaternion
```

There is no ambiguous `rotation` property.

Hierarchy from the original glTF asset is retained.

`Model` supports access to nodes:

```python
arm = model.node("left_arm")
arm.rotation_euler_deg = (0, 0, 45)
```

This is particularly useful for articulated experimental stimuli.

**Decision.** A lookup key is a name or an integer. An integer is always an index into the glTF array that the item comes from: nodes, animations, and variants. Nodes place lights and cameras, so `model.light(key)` and `model.camera(key)` take a node key, and `light.node` and `camera.node` return that node. Names are the glTF names; an unnamed node has the name `None`, not `gltfio`'s fallback to its mesh, light, or camera name. `node.mesh_name`, the node index, and the node tree reach unnamed nodes. Names that several items share raise an error that lists their indices.

**Decision.** `model.clone()` makes another instance that shares geometry, textures, and compiled materials. It needs the asset's source data, about the file size in CPU memory, so cloning is opt-in: `scene.load(source, clonable=True)`.

**Decision (geometry from arrays).** `scene.create_mesh(positions, indices, normals=..., uvs=..., colors=...)` returns a `Model` from NumPy arrays. It has the glTF metallic-roughness material that the loader makes for the same factors, through the same material provider, so it renders like a glTF mesh with equal parameters and supports nodes, material handles, and clones. Missing normals are area-weighted vertex normals. Tangent frames come from Filament's geometry library (`SurfaceOrientation`). `model.update_mesh()` replaces vertex data of the same size in place without per-frame allocation; clones share the vertex data. Pure-Python helpers in `filly.shapes` return arrays for a plane, a box, a UV sphere, and a cylinder. Geometry processing stays a non-goal (section 3).

---

# 7. glTF / GLB loading

Use Filament `gltfio`.

At minimum support:

- binary GLB;
- glTF with external buffers;
- embedded textures;
- PNG;
- JPEG;
- KTX2 where bundled support permits;
- node hierarchy;
- PBR materials;
- skins;
- morph targets where Filament supports them;
- animations.

Filament's own glTF viewer uses `gltfio::ResourceLoader` and registered image providers for PNG, JPEG, KTX2, and optionally WebP.

Public API:

```python
model = scene.load(path)           # GLB or glTF, detected by content
model = scene.load(data)           # bytes; all resources embedded
```

Loading from bytes is important for packaged experiment resources.

**Decision.** One `load()` replaces the format-specific loaders. Unknown optional extensions warn, unknown required extensions fail, and `strict=True` turns warnings into errors.

**Decision (glTF scope).**

- `gltfio` supplies core glTF 2.0 and `KHR_lights_punctual`, unlit, clearcoat, sheen, transmission, volume, IOR, specular, emissive strength, specular-glossiness, variants, dispersion, texture transforms, Basis Universal textures, mesh quantization, and Draco.
- This project adds `KHR_materials_diffuse_transmission` (a custom material), the glTF parameters of `KHR_materials_anisotropy` and `KHR_materials_iridescence` (on Filament's own shading inputs), a tested subset of `KHR_animation_pointer`, `KHR_node_visibility`, `EXT_mesh_gpu_instancing` (expanded into nodes), `EXT_meshopt_compression` and `KHR_meshopt_compression` (meshoptimizer 1.0), and WebP through a libwebp 1.5.0 texture provider.
- Volume thickness uses the complete node transform, as `KHR_materials_volume` requires; Filament 1.77.1 uses only the mesh node's scale.
- Draft extensions were removed: `KHR_materials_volume_scatter` and `KHR_materials_retroreflection` are unknown extensions.
- This is not a conformance claim.

**Decision (validation).** Filament's `gltf_viewer` 1.77.1 is the reference. A comparison tool renders the Khronos sample assets in both with the same camera, lights, environment, and `tone_mapping="aces_legacy"`. Filament-material assets match within two 8-bit levels, except assets whose volumes have scaled parents (the volume decision above). See [docs/how-to/reference-comparison.md](docs/how-to/reference-comparison.md).

---

# 8. Camera API

Support perspective and orthographic projection equally.

```python
camera = scene.create_camera()
```

Perspective:

```python
camera.set_perspective(
    fov_y=45,
    near=0.1,
    far=100,
)
```

Orthographic:

```python
camera.set_orthographic(
    left=-2,
    right=2,
    bottom=-2,
    top=2,
    near=0.1,
    far=100,
)
camera.set_orthographic(height=4, near=0.1, far=100)
```

**Decision.** Without an explicit `aspect`, and with the `height` form, the projection follows the render target's aspect at each `render()`, or the viewport's aspect when `render()` has a viewport. `render(..., camera=...)` uses another camera for one call (section 6.1). `set_lens_projection()` gives a perspective from a focal length. Imported glTF cameras are available through `model.camera(key)` and keep their glTF projection.

**Decision (depth-of-field inputs).** `camera.focus_distance` (scene units, read as meters) and `camera.aperture` (f-number) feed depth of field (section 16). The depth-of-field aperture is separate from the exposure aperture, so it does not change brightness.

Camera transform:

```python
camera.position
camera.look_at(target, up=(0, 1, 0))
camera.transform
```

Orthographic rendering must not be treated as a second-class/special mode because it is likely to be common in experimental stimulus presentation.

---

# 9. Lighting and environment

Version 1 should expose only straightforward lighting functionality:

```python
scene.add_directional_light(...)
scene.add_sun_light(...)
scene.add_point_light(...)
scene.add_spot_light(...)
```

Also support:

```python
scene.load_environment("studio.hdr")
scene.background = (0.5, 0.5, 0.5, 1)
```

Environment lighting should allow an IBL/environment map but should not require one.

A simple experiment should be renderable using one directional light and a constant background.

**Decision.** Environments come from a 2:1 panorama (file or NumPy array) or from prefiltered `cmgen` KTX files. Shadows are opt-in per scene and per light.

---

# 10. Materials

The first release should primarily use materials originating from glTF.

Expose limited overrides:

```python
model.material("body").base_color = (...)
model.material("body").metallic = ...
model.material("body").roughness = ...
```

It should also be possible to change colors on individual meshes/material slots, which is important for experimental stimuli:

```python
model.node("arm_a").material(0).base_color = (1, 0, 0, 1)
```

**Decision.** `node.material(slot)` copies the slot's material once and assigns the copy to that slot only. Material animation also drives the copies: an animated property follows the animation, and edits to properties that no clip animates persist.

**Decision (runtime textures).** A texture can come from a NumPy array (H x W x 1, 3, or 4; `uint8` or `float32`) with an explicit color space (`"srgb"` for color, `"linear"` for data) and optional mipmaps. It updates in place with the same shape and type for per-frame stimuli; updates copy into a ring of three staging buffers, add no frame of latency, and allocate nothing after the first three. A texture goes into the base color or emissive slot of a shared material (`model.material(name)`) or a node-local one, with an optional UV transform (offset, scale, rotation) for drifting patterns; `None` removes it. A glTF material compiled without the slot gets the material for the same glTF key with the slot added, through the material provider, and keeps its factors. Closing a texture removes it from its materials; the renderer closes its textures.

**Decision (host texture input).** `renderer.import_gl_input()` makes a host OpenGL texture a material input, for zero-copy video or host-drawn patterns on 3D surfaces. The host writes it inside `write()`. The order is explicit in both directions with the fences of section 14: the next render waits on the GPU for the host's writes, and the host waits on the GPU for Filament's last read before it writes again.

Do not expose Filament's complete material compiler/material-language system initially.

**Decision (custom shaders: closed).** Custom shader authoring is not provided. It would expose Filament's material language as the API, which contradicts section 33. Materials remain the glTF materials, the extension materials of section 7, their factors, and the runtime texture slots above.

---

# 11. Render targets

Support three modes.

## 11.1 Standalone window target

Optional convenience mode:

```python
window = renderer.create_window(...)
renderer.render(scene, window)
```

This is not necessary for the first experimental integration milestone.

**Open.** Not implemented. The host adapters (section 23) cover windowed use.

## 11.2 Filament-owned offscreen target

```python
target = renderer.create_render_target(
    width=1920,
    height=1080,
    format="rgba8",
    depth=True,
)
```

Filament directly supports rendering a `View` into an offscreen texture-backed `RenderTarget`; the upstream sample constructs color and depth textures and attaches them to a `RenderTarget`.

The result may be read into NumPy:

```python
image = target.read()
```

Readback is expected to be relatively expensive and is not the preferred per-frame PsychoPy/PTB path.

## 11.3 Imported/shared OpenGL target

Primary interoperability mode:

```python
target = renderer.import_gl_texture(
    texture_id,
    width=1920,
    height=1080,
    format="rgba8",
)
```

Filament supports importing a backend-native texture through `Texture::Builder::import()`. Under OpenGL, the identifier is a `GLuint`. Filament documents this facility as a last-resort API that may change, so this interoperability layer must remain isolated in the C++ facade.

The imported texture must be usable as a Filament render-target color attachment where supported.

This should be tested aggressively because upstream users have historically encountered backend-specific issues with rendering into imported OpenGL textures.

**Decision.** The host texture must have `GL_RGBA8` storage. With immutable storage (`glTexStorage2D`), Filament renders into a `GL_SRGB8_ALPHA8` texture view of it, so the GPU can apply the sRGB encoding on write while the host samples plain RGBA8 values (section 16). Mutable storage (`glTexImage2D`) from third-party hosts has no view; Filament renders into the texture itself, which works on the default exact output path, and the direct output path with sRGB encoding raises `InteropError` at render time. The adapters allocate immutable storage. Sizes accept integers and integral floats, because PsychoPy reports sizes as floats.

---

# 12. OpenGL context interoperability

## 12.1 Host ownership

When embedded into PsychoPy or Psychtoolbox:

```text
PsychoPy/PTB owns:
    display window
    host GL context
    swap interval
    presentation timing
    final buffer swap
```

Filament owns:

```text
Filament internal GL context
Filament rendering thread
Filament resources
scene rendering
```

Both contexts belong to the same GL share group.

## 12.2 Initialization

Host obtains its platform-native context handle.

Examples conceptually include:

```text
Windows: HGLRC
X11:     GLXContext
EGL:     EGLContext
```

The exact conversion lives in platform-specific integration code (`native/gl_interop.cpp`).

The handle is supplied to Filament through:

```cpp
filament::Engine::Builder()
    .backend(filament::Engine::Backend::OPENGL)
    .sharedContext(native_context)
    .build();
```

`sharedContext` is used as the platform-dependent shared context when Filament creates its internal context.

**Decision.** The host context is released while Filament creates its shared context and is then restored; drivers refuse to share a context that is current on another thread. A small WGL/GLX platform subclass places fences and waits in Filament's command stream.

**Open.** Shared EGL contexts, and therefore native Wayland hosts, are not supported. Offscreen renderers on Linux use headless EGL or GLX; see docs/how-to/build.md#run-on-linux.

---

# 13. Preferred PsychoPy/PTB rendering path

The host application creates an OpenGL texture.

Filament imports that texture and renders into it.

```text
Host GL context
    │
    ├── creates texture T
    │
    ▼
Filament imports T
    │
    ▼
Filament renders scene → T
    │
    ▼
GPU synchronization
    │
    ▼
Host draws T
    │
    ▼
PsychoPy win.flip()
or
PTB Screen('Flip')
```

This avoids CPU framebuffer copies.

The host retains responsibility for display timing.

---

# 14. Synchronization

Synchronization must be explicit.

Do not rely on accidental GL command ordering across contexts.

API:

```python
renderer.render(scene, target)
with target.acquire():
    ...  # host samples the texture
```

**Decision.** `acquire()` replaces the proposed token and `wait_ready()`. On entry it queues a GPU wait in the host context on a fence that Filament's driver thread placed after the frame. On exit it places a host fence, which the next `render()` waits for on the GPU before it overwrites the texture. Both directions are ordered, and neither waits for GPU completion on the CPU. Rendering while the texture is acquired raises an error. A host texture input (section 10) uses the same fences with the roles reversed: `write()` waits for Filament's last read, and the next `render()` waits for the host's writes.

The implementation should prefer GPU synchronization primitives where possible.

Avoid:

```cpp
glFinish();
```

on every frame.

A complete CPU/GPU synchronization operation can exist as a diagnostic fallback but should not be the normal path.

The exact cross-context synchronization implementation is a milestone requiring platform testing.

---

# 15. Frame semantics

`render()` means:

> submit exactly one requested scene rendering operation.

The wrapper should not silently decide to render at a lower resolution, skip experimental stimulus updates, or substitute prior frames.

Filament's `Renderer` normally manages frame latency and presentation-related behavior, so the wrapper needs to make the experimental semantics explicit.

Any Filament behavior designed primarily for game-style frame pacing should be either disabled, constrained, or surfaced explicitly.

**Decision.** Each `render()` runs Filament's `beginFrame()`, `render()`, and `endFrame()` on a headless swap chain that is never presented. The frame is rendered even when `beginFrame()` reports that the GPU is behind. Dynamic resolution is disabled.

---

# 16. Experimental rendering defaults

Default embedded configuration:

```text
Dynamic resolution:       disabled
TAA:                      disabled
Bloom:                    disabled
SSAO:                     disabled
Fog:                      disabled
Depth of field:           disabled
Motion blur:              disabled
Vignette:                 disabled
FXAA:                     disabled
MSAA:                     explicit opt-in
Shadows:                  explicit opt-in
Postprocessing:           off unless an option needs it
Render scale:             1.0
Output encoding:          sRGB
Tone mapping:             linear (clamp)
```

These are not claims that the effects are unsuitable for experiments. The principle is simply that expensive or temporally stateful effects should be explicitly requested.

Post-processing must be configurable per scene/view.

Filament supports disabling postprocessing on a `View`; its own render-target sample does so for the offscreen view.

**Decision (sRGB output).** Output is sRGB-encoded by default; `scene.encoding = "linear"` stores linear values. PsychoPy and the other hosts sample the texture as plain RGBA8 into a non-sRGB framebuffer, and readback users save or compare 8-bit images, so encoded values display and compare correctly without host changes. Linear 8-bit output wastes precision in dark tones. The encoding does not depend on other options, and it is within one 8-bit level of the analytic transfer function. On the default path it is exact for the value in the scene-linear RGBA16F buffer.

**Decision (encoding path).** filly's encode pass encodes by default (`Scene.output_path = "exact"`): the scene renders scene-linear color into an RGBA16F buffer, and one full-screen pass applies the linear tone mapper's clamp, the analytic transfer function in fp32, and the alpha rule, and rounds to 8-bit levels. Every option ends in this pass. Filament's postprocessing runs only for the options that are part of it (a non-linear tone mapper, bloom, depth of field, vignette), and then writes linear color into the same buffer; FXAA and dithering are filly's own, after the encoding. The pass replaced Filament's color grading as the default, which cost about 1.5 ms per 1080p frame on the tested Intel GPU (see docs/reference/performance.md). `Scene.output_path = "direct"` is an explicit opt-in that renders straight into the target, so the GPU encodes on write into sRGB storage and the encode pass is saved. The two paths round differently by up to one 8-bit level, so the renderer never switches between them on its own: an automatic choice let a stimulus change by one level when an unrelated setting, such as a material's alpha mode, changed. The opt-in never falls back. Each setting that it cannot render (a non-linear tone mapper, FXAA, MSAA, refraction, SSAO, bloom, dithering, depth of field, vignette, or a transparent view) raises `ValueError` when it meets the opt-in, in either order. `MASK` materials raise too, because only the encode pass stores alpha one for their sharpened edge alpha in an opaque view; unlit `OPAQUE` materials are compiled to output alpha one and work on both paths. Transparent views need the encode pass, because it premultiplies after encoding, which is what hosts that blend in encoded space expect.

**Decision (tone mapping).** The default tone mapper is a linear clamp, so neutral colors stay neutral and factors map directly to output levels. Filament's other tone mappers are available; `"aces_legacy"`, the `gltf_viewer` default, tints neutral grey.

**Decision (fog, depth of field, vignette).** These are opt-in scene properties, off by default. None reads frame history in Filament 1.77.1: fog is evaluated in the material shaders from the current fragment and camera, the depth-of-field passes read the current color and depth with position-only noise, and the vignette is a parameter of the color-grading pass. Fog is uniform in height with opacity `1 - exp(-density * (distance - start))`; its color is the output color of a fully fogged fragment, independent of lighting and exposure. Fog works on both output paths. Depth of field and vignette are part of Filament's postprocessing; they run before the encode pass on the default path. Depth of field uses the thin-lens circle of confusion from the camera's focal length, focus distance, and f-number (section 8).

---

# 17. Temporal effects

Temporally accumulated effects require special handling.

TAA, motion blur, temporal upscaling, and similar features depend on previous frames and may therefore make stimulus appearance dependent on frame history.

Such features must default to off.

If eventually exposed, the wrapper should provide:

```python
scene.reset_history()
```

for trial boundaries and discontinuous stimulus changes.

**Decision (temporal effects: closed).** TAA, TAA upscaling, screen-space reflections, and dynamic resolution are not exposed, so no effect keeps history and `reset_history()` is not needed. The reason is that stimulus appearance must not depend on earlier frames: in Filament 1.77.1 `PostProcessManager`, TAA and screen-space reflections read the previous frame's history buffer, and dynamic resolution and its upscaling follow earlier frame times. Filament 1.77.1 has no motion blur. Temporal dithering, when enabled, changes its noise on every frame by design.

---

# 18. NumPy integration

Nanobind exposes data directly as NumPy-compatible arrays where useful.

Examples:

```python
model.transform        # shape (4, 4)
camera.view_matrix     # shape (4, 4)
camera.projection      # shape (4, 4)
```

Accepted transform inputs include contiguous float32 and float64 arrays.

Internally convert to Filament's expected representation.

Framebuffer readback:

```python
image = target.read()
```

returns:

```text
H × W × 4 uint8
```

for RGBA8 targets.

Optional floating-point formats can be supported later.

---

# 19. Resource lifetime

Python objects should follow normal Python ownership expectations while protecting Filament's stricter destruction requirements.

A `Renderer` is the lifetime root.

```text
Renderer
 ├── Scene
 │    ├── Model
 │    ├── Camera
 │    └── Light
 ├── Texture / HostTexture
 └── OffscreenTarget / ImportedTarget
```

Child objects must maintain a reference to their owner or otherwise guarantee that their Filament resources cannot outlive the engine.

Explicit cleanup:

```python
renderer.close()
scene.close()
model.close()
```

Context manager:

```python
with filly.Renderer() as renderer:
    ...
```

Destructors should be defensive, but important cleanup should not depend solely on Python interpreter shutdown order.

**Decision.** Handles to closed objects raise `FillyError`. Host targets that are garbage-collected without `close()` emit `ResourceWarning`, never make another OpenGL context current, and delete host objects only in their own context.

---

# 20. Threading

The initial Python API should be callable from one application/control thread.

Filament retains its own internal rendering/driver thread. Filament's implementation launches a driver thread and initializes its driver/context there.

Python-facing mutation calls should therefore enqueue or perform Filament operations according to Filament's documented threading requirements.

Do not initially promise that arbitrary Python threads can mutate the same scene concurrently.

**Decision.** All objects must be used on the thread that created the renderer; other threads get `FillyError`.

Nanobind releases the GIL around potentially blocking operations:

```text
asset loading
GPU waits (render, acquire)
readback
finish()
close()
```

---

# 21. Performance instrumentation

Expose enough instrumentation to diagnose missed deadlines.

```python
stats = renderer.stats
```

Fields: `cpu_submit_ms`, `host_wait_ms`, `host_release_ms`, `finish_ms`, `readback_ms`, `frames_rendered`, and live resource counts (`live_models`, `live_lights`, `material_copies`).

Exact fields depend on what Filament reliably exposes.

Do not fabricate GPU timings if they cannot be measured robustly.

**Open.** GPU time per frame is not exposed. Filament's frame timer queries (`Renderer::getFrameInfoHistory()`) gave usable per-frame GPU times on the tested Intel driver in a profiling build; exposing them needs validation on more drivers. Application-level markers are not implemented:

```python
with renderer.marker("stimulus"):
    renderer.render(...)
```

---

# 22. Errors

Errors that should raise Python exceptions include:

```text
renderer initialization failure
unsupported backend
invalid shared context
asset decoding failure
missing glTF resources
invalid node/material names
incompatible imported texture
invalid dimensions/format
use of an object after Renderer.close()
```

Use library-specific exception types:

```python
FillyError
BackendError
AssetError
InteropError
```

**Decision.** Invalid values (dimensions, formats, option values) raise `ValueError`, and wrong key types raise `TypeError`. `AssetCompatibilityWarning` reports asset features that cannot be reproduced.

---

# 23. PsychoPy integration

The core renderer must not depend on PsychoPy.

Optional helper layer:

```python
from filly.integrations.psychopy import SharedTarget, create_renderer
```

Workflow:

```python
win = psychopy.visual.Window(...)

renderer = create_renderer(win)

scene = renderer.create_scene()
model = scene.load("stimulus.glb")

target = SharedTarget(renderer, win, 1024, 1024)
stim = target.as_psychopy_texture()

renderer.render(scene, target)
stim.draw()

win.flip()
```

The texture wrapper follows PsychoPy internals rather than requiring pixel copies.

**Decision.** `stim.draw()` acquires the texture itself, and `with target.acquire():` holds one frame for several draws. The adapter is pinned to psychopy-lib 2026.2.4 and the pyglet window backend, because it uses PsychoPy's context and texture internals.

**Decision (other hosts).** The same `SharedTarget` design exists for a plain pyglet window, moderngl, and zengl in `filly.integrations`, tested with pyglet 1.4.11, moderngl 5.12.0, and zengl 2.7.3 on Windows. Each is a thin subclass of `ImportedTarget`, which is the API for other host adapters.

---

# 24. Psychtoolbox integration

The core library similarly has no PTB dependency.

Python interoperability may be useful for Psychtoolbox Python.

A MATLAB MEX implementation could later reuse the C++ facade without changing the nanobind-facing design.

Preferred conceptual API:

```text
PTB opens window
PTB supplies OpenGL context / shared resource
Filament renders to shared texture
PTB draws texture
PTB Screen('Flip')
```

No Filament window swap should occur in embedded mode.

**Open.** No PTB integration exists.

---

# 25. Native backend strategy

## Version 1

```text
OpenGL:
    full standalone support
    offscreen rendering
    shared-context interoperability

Vulkan:
    standalone/offscreen rendering
    no PTB/PsychoPy sharing guarantee

Metal:
    standalone/offscreen rendering
    no PTB/PsychoPy sharing guarantee
```

Backend-native zero-copy interop can be explored later.

This deliberately keeps the first interop implementation focused on the graphics API actually exposed by existing PsychoPy/PTB workflows.

**Decision.** Only OpenGL is built, on Windows (WGL) and Linux (GLX). **Open.** Vulkan, Metal, and macOS.

**Status (Linux).** The Linux build compiles the pinned Filament from source in manylinux_2_28 with three local patches and passed the tests on Mesa software rendering and WSLg. It needs an X11 display. Linux was not retested after the API redesign.

---

# 26. Build system

Use CMake.

Expected dependencies:

```text
Filament
nanobind
Python development headers
```

Optional:

```text
image codecs required by selected gltfio configuration
```

Filament should preferably be built as part of the wheel process with an intentionally constrained configuration instead of requiring users to install a system Filament library.

OpenGL support must remain enabled; Filament itself has a build option controlling OpenGL support.

Do not build Filament samples/tools unless required for asset processing.

**Decision.** The build uses CMake with scikit-build-core. Windows links the official Filament 1.77.1 release SDK; Linux builds the same version from source. Only the needed static libraries are linked, and the material compiler is included for load-time shader compilation.

---

# 27. Packaging

Deliver ordinary wheels where practical:

```text
Windows x86_64
Linux x86_64
macOS arm64
macOS x86_64 if still warranted
```

The installed package should not require a separate Filament SDK.

Target:

```bash
pip install filly
```

followed by:

```python
import filly
```

without manual native-library setup.

Binary size should be measured and documented.

Reducing package footprint is a design objective, not merely an optimization task for later.

**Status.** Windows and manylinux_2_28 x86_64 wheels build and contain Filament; each is about 5.5 to 5.8 MB. **Open.** macOS wheels and publishing to PyPI.

---

# 28. Initial Python example

```python
import filly

renderer = filly.Renderer()

scene = renderer.create_scene()

camera = scene.create_camera()
camera.set_orthographic(
    left=-3,
    right=3,
    bottom=-3,
    top=3,
    near=0.01,
    far=100,
)
camera.position = (0, 0, 10)
camera.look_at((0, 0, 0))

scene.camera = camera

scene.add_directional_light(
    direction=(-1, -1, -1),
    intensity=50_000,
)

model = scene.load("stimulus.glb")
model.rotation_euler_deg = (20, 45, 0)

target = renderer.create_render_target(
    width=1024,
    height=1024,
)

renderer.render(scene, target)

image = target.read()
```

---

# 29. Embedded example

Without an adapter:

```python
import filly

renderer = filly.Renderer(shared_context=filly.current_gl_context())

scene = renderer.create_scene()
model = scene.load("stimulus.glb")

host_texture = create_host_gl_texture(1024, 1024)   # immutable GL_RGBA8 storage

target = renderer.import_gl_texture(
    host_texture,
    width=1024,
    height=1024,
    format="rgba8",
)

for angle in angles:
    model.rotation_euler_deg = (0, angle, 0)

    renderer.render(scene, target)
    with target.acquire():
        draw_host_texture(host_texture)
    host_flip()
```

The integration helpers (section 23) make the context and texture handling substantially less ugly than this example.

---

# 30. Benchmarks

Ship a benchmark utility:

```bash
python -m filly.benchmark stimulus.glb \
    --width 1920 \
    --height 1080 \
    --frames 10000
```

Measure at minimum:

```text
CPU submission time
GPU render time where available
total frame latency
synchronization wait
readback cost
```

Include representative tests at:

```text
60 Hz budget:   16.67 ms
120 Hz budget:   8.33 ms
144 Hz budget:   6.94 ms
240 Hz budget:   4.17 ms
```

These budgets are not performance targets for Filament alone; the host application's entire frame must remain below them.

**Status.** The benchmark reports CPU submission percentiles, batch time per frame, and readback cost. `examples/psychopy_stress.py` records per-frame CPU, synchronization, and flip timing and memory over repeated trials. **Open.** GPU render time (section 21), per-frame completion latency, and validated display deadlines at 120 and 144 Hz.

---

# 31. Acceptance tests

Version 1 should not be considered complete until all of the following work:

1. Load and render a GLB in an offscreen framebuffer.

2. Orthographic and perspective projections produce stable expected output.

3. Change a model transform every frame for at least 10,000 frames without resource growth.

4. Access and independently transform named glTF nodes.

5. Override a material/base color.

6. Render into an RGBA8 target and return the image as a NumPy array.

7. Create a Filament OpenGL engine sharing an existing native GL context.

8. Import a texture created in the host context.

9. Render Filament output into that texture.

10. Sample/draw that texture from the host context without CPU readback.

11. Synchronize the two contexts without an unconditional per-frame `glFinish()`.

12. Demonstrate PsychoPy or PTB controlling the actual display flip.

13. Confirm that disabling temporal/postprocessing features yields identical output for identical scene state regardless of preceding stimulus frames.

14. Benchmark a small GLB scene at 120 Hz and 144 Hz on a representative desktop machine and report CPU, GPU, and synchronization costs separately.

15. Repeatedly create and destroy renderers/scenes/assets without crashes or leaked GPU resources.

**Status.** Items 1 to 13 are covered by the test suite on Windows (item 12 with PsychoPy). Item 14 is open: GPU cost is not reported separately and display deadlines are not validated. Item 15 is covered for resource counts. Repeated window creation crashed rarely because of a pyglet 1.4.11 defect, not the renderer or the driver; the test suite works around it. A rarer crash when two processes create shared-context renderers at the same time is open (see [docs/reference/validation.md](docs/reference/validation.md)).

---

# 32. Development milestones

### Milestone 1: minimal renderer (done)

Implement:

```text
Renderer
Scene
Camera
Model
GLB loading
transforms
directional light
offscreen rendering
NumPy readback
```

### Milestone 2: useful stimulus API (done)

Add:

```text
node lookup
material overrides
orthographic helpers
background/environment control
animations if straightforward
performance statistics
```

### Milestone 3: GL interoperability proof (done)

Implement:

```text
shared native OpenGL context
host-created texture import
Filament RenderTarget backed by imported texture
cross-context synchronization
host texture sampling
```

This milestone should be treated as the main technical risk.

Filament exposes both shared-context creation and native OpenGL texture import, but texture import is documented as a last-resort/unstable API and has had interoperability edge cases, so it should be proven experimentally before designing higher-level integration around it.

### Milestone 4: PsychoPy integration (done)

Build a small adapter and demonstrate:

```text
PsychoPy owns window
Filament renders GLB
shared texture reaches PsychoPy
PsychoPy flip timing remains authoritative
```

### Milestone 5: PTB integration (open)

Demonstrate the same architecture with Psychtoolbox.

---

# 33. Key architectural rule

The project should remain:

```text
"a tiny experimental 3D renderer implemented using Filament"
```

and should explicitly avoid turning into:

```text
"Python bindings for Google Filament"
```

That distinction should guide API decisions.

If a Filament concept does not need to be visible to the Python caller, keep it inside the C++ facade.

---

# 34. Minimum viable release

A useful `0.1` release consists of:

```text
nanobind Python extension
GLB/glTF loading
orthographic + perspective cameras
node/model transforms
simple lighting
material color overrides
offscreen rendering
NumPy framebuffer output
OpenGL backend
shared-context construction
shared OpenGL render target
PsychoPy proof-of-concept
basic benchmark/timing tooling
```

Vulkan, Metal, elaborate materials, animation-control APIs, generalized geometry creation, and additional language bindings should not block `0.1`.

**Status.** Meshes from arrays, runtime textures, per-render cameras and viewports, fog, depth of field, and vignette were added after the milestones of section 32 (sections 6, 8, 10, and 16).

---

# 35. Main technical risk

The renderer itself is low risk.

The highest-risk component is **zero-copy synchronization and ownership of an imported OpenGL texture across the host context and Filament's internal context**.

Filament gives the project the two important primitives:

- shared-context engine creation;
- importing an OpenGL texture ID into a Filament texture.

But successful low-latency render-target use and synchronization need to be verified on Linux, Windows, and the specific GL context implementations used by PTB/PsychoPy.

Therefore the first serious prototype after basic rendering should be a tiny interop test, not more renderer features.

**Status.** Verified on Windows with Intel and NVIDIA drivers, and on Linux with Mesa and WSLg before the API redesign. The fence protocol is in section 14.

---

# 36. Success criterion

The project succeeds if an experiment can effectively do this:

```python
model = scene.load("shepard_metzler.glb")
stimulus = shared_target.as_psychopy_texture()

for trial in trials:
    model.node("arm_a").rotation_euler_deg = trial.arm_a
    model.node("arm_b").rotation_euler_deg = trial.arm_b

    renderer.render(scene, shared_target)

    stimulus.draw()
    win.flip()
```

with no image baking, no CPU framebuffer copies, no game engine, and with the experiment host retaining control over display timing.
