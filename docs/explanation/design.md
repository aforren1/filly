# Design and current limits

The C++ interface in `native/renderer.h` does not include Python or Filament headers.
Nanobind converts Python values at the boundary. Filament types stay in the native implementation.
The Windows WGL and Linux GLX adapters are isolated in `native/gl_interop.cpp`, and the Linux
headless EGL platform in `native/egl_platform.cpp`.

## Ownership

Every native resource retains the engine state. The engine tracks resources through weak references.
This prevents ownership cycles. A scene retains its models, cameras, and added lights.
Objects can keep the engine alive after the Python `Renderer` reference is deleted.

`renderer.close()` waits for outstanding work and destroys resources before the engine.
Child objects then reject further calls. NumPy images and matrix copies own their storage.

Model and light `close()` release scene ownership and queue native destruction. Closed model
handles invalidate their nodes, node-local materials, and imported lights. A removed imported light
keeps its transform entity so animation can continue. Added lights use a separate owner whose
closed state prevents old handles from referring to a replacement light. `Scene.close()` releases
the scene's children, detaches its cameras from other scenes, and then destroys its view,
Filament scene, color grading, and environment.

A loaded glTF asset is a shared native object. The first model and each `clone()` are gltfio
instances of it: `AssetLoader::createInstance()` adds entities, material instances, lights,
skins, and an animator, and reuses vertex buffers, textures, and materials. gltfio 1.77.1 has no
way to destroy one instance, so closing a model only removes its entities from the scene; the
asset and all its instances are destroyed when the last model closes. `createInstance()` needs
the asset's source data, about the file size in CPU memory, and each instance needs the
preparation tables for the custom materials, property animation, cameras, and morph weights. Cloning is
therefore opt-in: `scene.load(..., clonable=True)` keeps them, and the default calls
`releaseSourceData()` and drops the tables after the first instance. For DamagedHelmet
(3.6 MiB file) this saves 3.7 MiB of private bytes per loaded asset. The process grows by
113 MiB per loaded DamagedHelmet on the tested Intel GPU, most of it texture memory that the
integrated GPU allocates in system memory, so the saving is small for textured assets. Name
lookups use each instance's own node table, because gltfio's name and entity lists cover all
instances. Node and mesh names come from the preparation, which reads them from the glTF
document; gltfio's names fall back to the mesh, light, or camera name for an unnamed node.

Node-local materials belong to the model. The first `node.material(slot)` duplicates the slot's
material instance. A variant that maps the slot gets a fresh copy of the variant's material, so
the node shows the variant and handles stay valid. Each copy records the instance that it was
copied from; property animation writes to that instance and to its copies, so a recolored node
keeps animating. Copies are released when the model closes, after the slot gets its source
instance back. Compiled shader definitions stay in the renderer's material cache.

Native destruction must happen on the creating thread. Cross-thread destruction and interpreter
shutdown order need further hardening. Call `close()` explicitly or use a context manager.

## Frame behavior

Offscreen and shared-context engines use the same command-buffer configuration: a 12 MiB ring
with the SDK's 1 MiB minimum batch size. `AssetLoader::createAsset()` queues all commands for an
asset without a flush point, about 640 bytes per mesh. NodePerformanceTest's 10,000 meshes queue
6.13 MiB; the 3 MiB SDK default aborts the process. On Windows the ring is committed twice
(mirror mapping), so the cost is 24 MiB per engine, 18 MiB more than the SDK default.
A flush between `createAsset()` and `loadResources()`, or from the material provider, does not
reduce the peak. Assets with roughly 18,000 or more meshes can still abort the process.
The backend handle arena is set to 32 MiB; the SDK default fills on the same asset and logs a
warning, but the heap fallback was not measurably slower.

Each frame uses `beginFrame()`, `render()`, and `endFrame()` on a 1 x 1 headless swap chain
that is never presented. The view draws into its own render target. `endFrame()` runs Filament's
per-frame upkeep: entity and texture-cache garbage collection, driver ticks, and frame fences.
`renderStandaloneView()` skips that upkeep. With it, entity creation slowed without bound after
2^17 total entities had been created and destroyed.
`beginFrame()` returns false when the GPU is more than a frame behind. The wrapper renders the
frame anyway, which the Renderer API permits. Unthrottled loops run about 20% slower than with
`renderStandaloneView()`. CPU submit time per frame is unchanged. They also log "FrameInfo's
circular queue is full" on nearly every frame. `FrameInfoManager` keeps 16 frames and releases
one only after the driver thread has processed it; an unthrottled caller runs more than 16 frames
ahead of that thread. Disabling the GPU-completion metric (an engine feature flag) does not stop
the warning, and Filament has no other switch. A 60 Hz loop, or a 1 ms sleep per frame, does not
log it. In embedded mode, the host owns the window and every
display-buffer swap. See [tested assumptions](assumptions.md).

Shadows, refraction, FXAA, MSAA, SSAO, bloom, dithering, fog, depth of field, and vignette are
scene properties and disabled by default. The PsychoPy demo enables refraction and FXAA.
Temporal antialiasing, TAA upscaling, screen-space reflections, and dynamic resolution are not
exposed. In Filament 1.77.1 `PostProcessManager`, TAA and screen-space reflections import the
previous frame's history buffer (`FrameHistory::getPrevious()`), and dynamic resolution scales
by earlier frame times. Filament 1.77.1 has no motion blur. The exposed effects were checked in the same source: fog is evaluated
in the material shaders from the current fragment and camera; the depth-of-field passes read
only the current color and depth, and their noise (`interleavedGradientNoise`) depends on the
pixel position only; the vignette is a parameter of the color-grading pass. Without temporal
antialiasing, SSAO and bloom keep no history either. Dithering noise changes on every frame by
design. Animation advances only when the caller supplies a time. The renderer has no animation
clock.

Fog color in Filament is multiplied by the IBL luminance: environment intensity (30000 lux for
the default IBL) times camera exposure. That makes a fog color depend on lighting and exposure,
which a stimulus cannot use. The wrapper divides the scale out at each `render()`, for the
camera of that call, and sets the fog options only when the result changes. Height falloff is
zero, so fog opacity is exactly `1 - exp(-density * (distance - start))`.

Filament computes the depth-of-field circle of confusion from the camera aperture, which also
sets exposure. Changing the aperture for blur would change brightness. The wrapper keeps
Filament's aperture for exposure, stores the depth-of-field f-number on the camera, and scales
`cocScale` by the ratio of the two. The circle of confusion is linear in the aperture diameter,
so the scaled result is the thin-lens value for the chosen f-number. The maximum aperture
diameter is zero, so the bokeh orientation does not follow the aperture.

Each `render()` can take a camera and a viewport. The view's camera is replaced for the call and
restored after it; a camera that follows the target aspect takes the viewport aspect. Filament
clears whole attachments (the OpenGL backend disables the scissor test for clears), so a clear
cannot be limited to a viewport. `clear=False` must still not show earlier content inside the
viewport. On the exact path this needs nothing extra: the scene view clears the scene-linear
buffer, and the output passes write only the viewport. On the direct path the view draws over
the previous frame, and Filament applies a target's clear only for the first view of a frame.
So a direct `clear=False` call renders two views in one frame: first a view with an empty scene
and a constant-color skybox in the viewport, with clearing off, then the scene view. The skybox
writes the same value that a clear would, through the same `GL_FRAMEBUFFER_SRGB` state. The fill
pass cost nothing measurable on the tested Intel GPU.

## Runtime textures and generated meshes

A texture from NumPy is a Filament texture with a sampler chosen at creation. `setImage()` reads
the caller's buffer on the driver thread until a callback, which Filament runs on the calling
thread during a later flush. `update()` therefore copies into one of three staging buffers and
reuses a buffer only after its callback; a fourth update in a row without a render waits with
`flushAndWait()`. The new pixels are visible at the next `render()`, so the ring adds no latency.
Float data is stored as 16-bit floats: 32-bit storage measured no faster to upload on the tested
driver and doubles GPU memory.

glTF materials are compiled for their texture slots, so a flat-colored glTF material has no base
color sampler. The material provider records the `MaterialKey` of each instance it creates. An
assignment asks the provider for the material of the same key with the slot and texture
transforms added, and copies the factors and render state from the old instance. The texture
indices are not copied, because the provider sets them from the key. The replacement shows in
every primitive that showed the glTF instance; gltfio keeps the glTF instance, and variants,
property animation, and removal go through it. Filament has no API to read a texture binding
back from a material instance, so a rebuilt material cannot keep other glTF textures; that case
raises an error. When the glTF material already has the slot and other textures, the handle shows
a duplicate of it instead, which keeps them.

On the archive material path (CMake `FILLY_MATERIALS=archive`), every archive entry has the
base-color and emissive samplers, with UV set and transform uniforms. An assignment therefore
always shows a duplicate of the glTF instance with changed parameters, for every material. A
removal duplicates the glTF instance again, because it still has the glTF texture of the slot,
and copies the factors and render state from the old handle. Nothing is compiled.

A generated mesh is loaded as a one-triangle glTF with the requested material and attributes,
then its renderable gets the mesh's own vertex and index buffers (`setGeometryAt()`). The loader
therefore gives it the same material as a glTF file with those factors, and the model gets
nodes, material handles, clones, and closing without a second code path. The placeholder's
accessor bounds are the mesh bounds. Positions, tangent frames, UVs, and colors are separate
vertex buffers, so an update uploads only what changed. A mesh without colors gets a white
`UBYTE4` color buffer, and `UV1` reads the `UV0` buffer: precompiled materials serve every key,
so they always read both attributes and multiply by the vertex color. Creation uses Filament's
`SurfaceOrientation` for tangent frames. It allocates on every build, so updates use a copy of
its method that writes into the staging buffer; a test checks that both give the same image.
Missing normals are area-weighted vertex normals, because shared vertices of an indexed mesh
should be smooth; flat faces need their own vertices.

A host texture input uses the same fences as a shared render target, in the other direction.
Leaving `write()` places a host fence, and the next render queues a Filament-side wait on it
before the frame. After each render, Filament places a fence for each host texture, and entering
`write()` queues a host-side wait on it. Neither side waits on the CPU.

Two kinds of per-frame work run only when something changed. A camera whose projection follows
the target compares the target aspect with the last applied one and sets a new projection only
on a change. A node edit on a skinned model puts the model in its scene's list of models with
stale bone matrices; `render()` updates those models once and clears the list, whose storage is
kept. Frames without such changes do no projection or bone work and allocate nothing.

`model.bounds` does not come from gltfio. `FilamentAsset::getBoundingBox()` moves each mesh's
accessor box by its node's world transform. For a skinned mesh that transform does not apply:
glTF places skinned vertices with the joints' world transforms times their inverse bind
matrices. Sketchfab rigs scale the armature node by 100 and put the 0.01 in the inverse bind
matrices, so gltfio's box for such a model is about 100 times too large, and a mesh node offset
from the bind frame moves it away from the geometry. Preparation computes the box once per
asset: accessor boxes through node transforms for rigid meshes, and every vertex skinned with the
rest pose for skinned meshes. A 1,000,000-vertex skinned mesh loaded in 120 ms against 65 ms
for the same mesh without a skin; the difference also includes gltfio's own skin work and the
joint and weight uploads (Iris Xe laptop, September 30, 2026). Clones share the box, and
frames do no bounds work. The same rest-pose box, in the mesh node's frame, replaces gltfio's
culling box for skinned renderables, which had the same fault and could cull a visible mesh.

## Output encoding

The encoding does not depend on other options. Earlier, disabling postprocessing wrote linear
values into the 8-bit target (0.5 grey became 128), and enabling it wrote sRGB values through the
selected tone mapper (188 with linear tone mapping, and a warm tint with the ACES legacy default).
The default tone mapper is now the neutral linear clamp.

Two paths write the output, selected by `Scene.output_path`:

- Exact (`"exact"`, the default): the scene view renders scene-linear color into an RGBA16F
  buffer, and filly's encode pass writes the target. The pass is one full-screen triangle with a
  filly material (`native/output_pass.cpp`). It clamps to `[0, 1]` (the linear tone mapper), applies
  the analytic sRGB transfer function in fp32 (or none for linear encoding), applies the alpha
  rule, and rounds explicitly to 8-bit levels. `GL_FRAMEBUFFER_SRGB` stays off, so the pass
  writes its values raw into any RGBA8 or sRGB8 storage.
- Direct (`"direct"`, opt-in): postprocessing is off and `GL_FRAMEBUFFER_SRGB` is on for sRGB
  output, so the GPU encodes each write, clears included. Linear output writes raw values.

Every configuration of the exact path ends in the same encode pass, so no option changes how a
value is encoded:

| Option | What runs before the encode pass | Needs Filament's postprocessing |
| --- | --- | --- |
| None (default) | The scene view, rendering straight into the RGBA16F buffer | No |
| MSAA | Filament's multisampled RGB16F buffer, resolved and copied into the RGBA16F buffer | No |
| Refraction, SSAO, shadows, fog | Their passes; the color pass as without them | No |
| Transparent view | Filament's RGBA16F color buffer, blended into the RGBA16F buffer | No |
| FXAA | Nothing: filly's FXAA pass runs after the encode pass, on the encoded image | No |
| Dithering | Nothing: the encode pass adds the noise after the transfer function | No |
| Per-channel tone mapping (`"filmic"`, `"generic"`), bloom, depth of field, vignette | Filament's postprocessing; its color grading writes scene-linear color into the RGBA16F buffer | Yes |
| Channel-mixing tone mapping (`"aces_legacy"`, `"aces"`, `"pbr_neutral"`, `"gt7"`, AgX, `"display_range"`) | Filament's postprocessing; with sRGB encoding its color grading encodes into an RGBA8 buffer, which the encode pass copies (see below) | Yes |

These are the Filament 1.77.1 conditions (`FRenderer::renderJob`): bloom, depth of field,
vignette, color grading, dithering, FXAA, and TAA are skipped when postprocessing is off; MSAA,
SSAO, screen-space refraction, and the blend of a transparent view run without it.

The encode pass rounds exactly. The linear buffer holds fp16 values, and every one of the 15,361
fp16 values in `[0, 1]` encodes to the rounded analytic value, in both encodings
(`test_exact_path_rounds_every_half_float_exactly`). An input that is not an fp16 value is first
stored as one of its two fp16 neighbors: the tested Intel driver truncates toward zero. The stored
level is then within 0.54 levels of the analytic value of the input: 9 of the 319 sweep values
move by one level. An RGBA32F buffer removes that step for the default configuration (0 of 319),
but not for MSAA, transparent views, or refraction, which pass through Filament's own fp16
buffers. With RGBA16F every configuration stores the same levels, so toggling an option does not
change a stimulus; RGBA32F would change 9 of 319 levels when MSAA or transparency toggles. The
RGBA32F buffer also cost 0.05 to 0.08 ms more GPU time per 1080p frame on the tested Intel GPU.

FXAA runs on the encoded image, as Filament's FXAA runs after its color grading. The encode pass
then writes an RGBA8 buffer with luma in alpha, and filly's FXAA pass (FXAA 3.11 console with the
G3D patches, as in Filament 1.77.1) writes the target. It fetches the center pixel without
filtering and rounds its result, so a pixel that FXAA leaves alone keeps its exact level: a flat
field is identical with and without FXAA. Filament's own FXAA would have forced color grading
before it, whose 1D LUT (512 fp16 entries) rounds differently: with color grading before the
encode pass, 19 of the 319 sweep values were one level off, against 9 without it. Taps are clamped to the rendered region, because outside it the buffer holds an earlier
frame after a `clear=False` call.

Dithering adds Filament's triangular noise pattern (`inline_dithering.fs`) of one level after the
transfer function, before rounding. Filament's own dithering would add it to scene-linear color,
where one level of noise in linear light is many levels near black. The pattern changes on every
frame by a golden-ratio sequence of the frame count.

When Filament's postprocessing runs, its color grading applies the tone mapper and must output
linear color. In Filament 1.77.1 a per-channel tone mapper takes the precise path, a 512-entry
fp16 LUT indexed in linear space, only with the engine feature `engine.color_grading.use_1d_lut`;
without it, unlit (1, 0, 0) became (247, 0, 0) and (0, 1, 0) became (23, 247, 6), because a
32^3 10-bit LUT in Rec.2020 was used. The wrapper sets the feature. Linear output from Filament
itself always takes the 3D LUT path and wrote 242 for linear white. For per-channel tone mappers,
the wrapper therefore keeps sRGB output and wraps the tone mapper with the inverse sRGB transfer
function, so the LUT holds the linear result. With the linear tone mapper and bloom or depth of
field, the sweep stays within one level of the analytic value (19 of 319 values move by one level).

Tone mappers that mix channels (`isOneDimensional()` is false: ACES, ACES legacy, PBR Neutral,
GT7, AgX, display range) run in Rec.2020 between gamut matrices, where the inverse-sRGB wrapper
is wrong, so they always use Filament's 32^3 3D LUT. Its linear output, encoded by filly, was up to
3 levels off `gltf_viewer` on DamagedHelmet (MAE 0.45) and up to 6 levels off on an unlit chart
(44 of 144 channels), because the LUT's 10-bit linear values are coarse near black. With sRGB
encoding such a scene therefore takes Filament's route, as `gltf_viewer` does: the color grading
outputs sRGB (`Rec709-sRGB-D65`) into an RGBA8 buffer of the target, the driver rounds to levels
as for `gltf_viewer`'s swap chain, and a variant of the encode pass copies those levels.
DamagedHelmet, TransmissionTest, ClearCoatTest, and SheenChair then match `gltf_viewer` exactly
(MAE 0, maximum 0). An RGBA16F buffer for this output was tried first: 1% of DamagedHelmet's
pixels were one level lower than `gltf_viewer`'s (MAE 0.011), consistent with the driver's
truncation to fp16. In this route:

- Filament's color grading also dithers (`View::Dithering::TEMPORAL`) before its 8-bit rounding,
  with the same triangular noise; filly's encode pass does not dither these pixels.
- Transparent views: Filament's translucent color grading divides by alpha, grades, and
  multiplies again (`colorGrading.mat`), so the buffer holds srgb(c) * a, the adapters' rule; the
  encode pass stores it unchanged.
- FXAA runs after the encode pass on the encoded image, as in the linear route.
- With a viewport and a clear, the RGBA8 buffer holds the clear color as 8-bit linear values
  outside the viewport. The encode pass stores a uniform there instead: the clear color rounded
  to fp16 and encoded on the CPU, so those pixels match the linear route (without dithering).
- `encoding = "linear"` keeps the linear route: Filament's linear 3D LUT output in the RGBA16F
  buffer, stored without a transfer function. Its accuracy is that LUT's: within 3 levels of the
  sRGB route once encoded, and coarse near black.
- The Intel framebuffer-fetch subpass stays disabled: `d.renderer.disable_subpasses` is set for
  the engine, so it applies to every view that runs color grading without MSAA.
- The encode pass stays, for alpha, luma, and the viewport rule. Its graded variant is a
  separate material, compiled on the first graded render, with no transfer function: the CPU
  encodes the clear color once. The viewport test in the default material cost 0.05 ms per
  1080p frame on the Intel GPU, and the transfer function behind it in the graded material
  1.0 ms. The route allocates the RGBA8 buffer
  instead of the RGBA16F one on a target's first graded render.

Opaque views store alpha one: the encode pass writes it. That covers the sharpened edge alpha
that Filament writes for `MASK` materials in an opaque view, which the material code cannot
correct. On the direct path, Filament writes the fragment shader's alpha. Lit materials write one
unless they blend, and blending over a cleared alpha of one keeps one, so the wrapper clears
opaque views to alpha one. Filament's unlit shader passes base-color alpha through for `OPAQUE`
materials; the wrapper compiles unlit `OPAQUE` materials with its own generator, which sets the
alpha to one, in both shader modes, and the archive path's opaque unlit entry does the same. The
direct path rejects `MASK` materials. Rewriting them (for example `OPAQUE` as `MASK` with cutoff
zero) was rejected: with MSAA, alpha to coverage would then drop samples.

Transparent views keep premultiplied linear color in the RGBA16F buffer. The encode pass divides
by alpha, encodes, and multiplies again: srgb(c) * a, which is what hosts that blend in encoded
space expect (PsychoPy, and plain `GL_ONE, GL_ONE_MINUS_SRC_ALPHA` blending into a non-sRGB
framebuffer). The direct path would store srgb(c * a), premultiplied in linear space, and the
adapters' division by alpha would then be wrong, so it rejects transparent views. A host that
blends in linear space would need the other rule, but none of the supported hosts does.

A render with a viewport encodes the whole target when it clears, because the scene view clears
the whole linear buffer as the first view of that buffer in the frame; outside the viewport it
holds the background. With `clear=False` the passes write only the viewport, so the rest of the
target keeps its contents, and the direct path's background fill view is not needed.

The pass allocates nothing per frame. The linear buffer (and the FXAA input, once FXAA is used)
is created on a target's first render and lives as long as the target, so it resizes only with
the target, which cannot change size. It shares the target's depth texture. The pass sets its
material parameters only when they change, before `beginFrame()`: Filament commits material
instances when the frame's first view renders, so a change set between views reached the GPU
inside a render pass and was lost. The encode material is compiled when the renderer is created,
and the FXAA material when a scene first sets `antialiasing = "fxaa"`, so neither compile falls on
a frame.

A shared host texture cannot simply have sRGB storage: a host that samples it decodes the values
back to linear. The `EXT_texture_sRGB_decode` skip setting avoids that for plain texture binds,
but a sampler object overrides it, and zengl binds sampler objects. Instead, Filament renders
into a `GL_SRGB8_ALPHA8` texture view of the host's `GL_RGBA8` texture, and the host samples its
own texture. A view needs immutable storage, so the adapters allocate the host texture with
`glTexStorage2D` in native code and wrap it as a moderngl external texture or a zengl external
image. Third-party textures with mutable storage from `glTexImage2D` import without a view.
Filament then renders into the host texture itself, which works on the exact path; the direct
path with sRGB encoding raises `InteropError` for such a target at render time. The wrapper
deletes a view only after Filament has finished with it and only while the host context is
current.

The direct path must match the analytic transfer function within one 8-bit level, offscreen and
shared. Every sweep value (the 1/64 grid and every 8-bit input level, 319 values) is within one
level; the largest deviation from the exact value was 0.68 levels.

### Cost of the output path

Measured on September 30, 2026; see [Performance](../reference/performance.md#output-path-september-30-2026).
Intel Iris Xe, 1920 x 1080, GPU frame time from Filament's timer, 60 Hz pacing, median of five
interleaved rounds:

| Scene | Color grading (before) | Exact | Direct |
| --- | ---: | ---: | ---: |
| Empty, offscreen | 1.62 ms | 0.80 ms | 0.11 ms |
| DamagedHelmet, offscreen | 3.16 ms | 2.32 ms | 1.61 ms |
| DamagedHelmet, pyglet shared texture | 3.19 ms | 2.33 ms | 1.63 ms |
| DamagedHelmet, offscreen, FXAA | 4.06 ms | 3.26 ms | - |

Color grading cost 1.5 ms on this GPU mostly because of its 1D LUT, three fetches of a 3D texture
per pixel; the buffer format made no difference. The encode pass costs about half of it in the
same capture. On NVIDIA the exact path takes 0.28 ms for DamagedHelmet, against 0.37 ms with color
grading and 0.18 ms direct. The encode pass is a second Filament view, which adds about 0.1 ms of
CPU time per frame.

The pinned SDK's framebuffer-fetch color-grading subpass writes a black frame with a gradient
tile on the tested Intel Iris Xe driver, offscreen and in shared textures, with compiled and
precompiled shaders. The wrapper disables that subpass for every engine. It matters only for
scenes that use Filament's postprocessing without MSAA, bloom, or depth of field, that is, with a
non-linear tone mapper or the vignette; the default scene never runs color grading. Drivers without
`GL_EXT_shader_framebuffer_fetch`, such as the tested NVIDIA driver, never use the subpass, so the
setting has no effect there.

## Shared OpenGL textures

The default Filament WGL platform does not implement external sync creation. The wrapper supplies
a small WGL, GLX, or EGL platform subclass through Filament's public platform interface, for
offscreen engines too. It places GL fences, waits, and the `GL_FRAMEBUFFER_SRGB` setting in the Filament
command stream through `Engine::createSync()`.

The host context is temporarily released while Filament creates its shared context on the driver
thread. It is then restored. Without the release, `wglCreateContextAttribsARB` fails: Intel reports
`ERROR_BUSY` (170) and NVIDIA reports 0xC00720DD.

After rendering, the driver inserts and flushes a GL fence. `ImportedTarget.acquire()` waits for
the handle to become available and queues `glWaitSync()` before sampling. Leaving the outermost
`acquire()` inserts and flushes another fence in the host context. Filament queues its wait
before writing the next frame. The core target counts nesting, so the adapters keep no acquisition
state. A second acquisition of the same frame needs no new wait, because the host context already
waited for it; its release replaces the unconsumed host fence.
This orders both producer and consumer GPU work. Normal frames do not use `glFinish()` or CPU
framebuffer copies. Explicit shutdown and image readback can block for completion.

The PsychoPy adapter uses `ImageStim`'s texture-provider interface and owns the host texture.
Its draw method runs inside `acquire()`. PsychoPy remains responsible for
`win.flip()`. PsychoPy skips a context switch when it believes its window is current, which is
wrong after other code switched contexts through pyglet, so the adapter also checks pyglet's
current context.

Garbage collection can run a target's or stimulus's finalizer while another window's context is
current. Names deleted in that context could belong to its objects, and switching contexts would
break the code that is running. Finalizers therefore never switch contexts and delete host
objects only if the owning context is current; otherwise the objects stay until their window
closes. Unclosed targets emit `ResourceWarning`. The context test compares WGL or GLX handles.
WGL handles carry a generation count, so a new context does not reuse a closed one's handle on
the tested driver; GLX contexts are pointers and could be reused, which is not tested. Close shared targets and the renderer before closing the host window. If the host
context is no longer current, the close skips the host fence and waits for Filament's work only.
This lets the engine close after its window. Host GPU work is then not fenced, which is safe only
because a closed window can no longer use the texture.

Import validation binds the host texture to check its target. Intel rejects the OpenGL 4.5
`GL_TEXTURE_TARGET` query, so there is no bind-free check. Errors that the host left pending are
discarded first, because they would otherwise read as a failed bind.

Transparent views retain alpha through the encode pass and produce premultiplied, encoded RGB. The PsychoPy
adapter converts sampled RGB to straight RGB in a shader, before ImageStim applies color, opacity, and masks.
The temporary shader selection is restored after each draw so regular PsychoPy stimuli keep their
normal rendering behavior. The host window uses average blending.

The adapter is pinned to psychopy-lib 2026.2.4 because it uses PsychoPy's context and texture
internals. Other versions and backends need separate tests.

## Dependencies

The initial build uses the [official Filament 1.77.1 SDK](https://github.com/google/filament/releases/tag/v1.77.1).
The default material provider compiles glTF configurations at load time. `precompiled_shaders=True`
selects Filament's precompiled archive, but only for materials that an archive entry matches
exactly. The wrapper reads the archive's feature table and copies the SDK's `prepareConfig()`
reductions. Without an exact match, the SDK drops features or substitutes a default material and
then sets parameters that the default lacks, which aborts the process (for example sheen,
specular, and IOR together). Those materials are compiled instead. The wheel includes the material
compiler in both modes. Review the copied reductions when upgrading the SDK.

A lit material at feature level 1 has 8 texture samplers. Filament's compiled provider aborts on
a 9th; this was measured with generated assets. Loading counts texture slots and raises
`AssetError` first. With precompiled shaders such a material is reduced as the archive would reduce it, and
a warning names it. Materials that the wrapper compiles itself raise `AssetError` from the shader
compiler instead of aborting.
The extension links only the selected static libraries. The SDK tools are not installed in the wheel.

The archive material path replaces both providers with filly's own
(`native/archive_materials.cpp`). The build compiles `native/materials` with `matc` and packs
the packages with `uberz` into one zstd archive, which the module embeds. The provider maps each
glTF material to an entry by its preparation plan (below), sets defaults, binds the extension
textures to the entry's generic samplers, and clears the extension texture bits and unsupported
features in the key that it returns, so that gltfio binds only the core textures and sets only
parameters that the entry has. It links no material compiler: `filamat` stays on the link line
in this phase, but the linker drops it (the module is 7.3 MB instead of 15.1 MB). See
[material precompilation](material-precompilation.md).

## glTF preparation

gltfio loads what Filament supports. The rest is preparation in the native core
(`native/gltf_prepare.cpp`), so that every frontend of the core gets the same features. Python
only binds it: the binding converts the source, releases the GIL for the whole load, and turns the
returned messages into `AssetCompatibilityWarning`. Unknown optional extensions produce warnings;
unsupported required extensions fail. Strict mode turns warnings into errors. Metadata-only XMP
extensions cannot change the image and are ignored. The checks do not replace a glTF validator.

The core reads the document with cgltf. It does not compile cgltf: the header in
`native/vendor/cgltf` is the one that Filament 1.77.1 builds into `gltfio_core`, which exports
the functions, so both parses have the same structure layout. Accessors are read with
`cgltf_accessor_unpack_floats`, which applies sparse values and normalization.

A load has these steps:

1. Parse the document, check extensions and material combinations, and count texture samplers.
2. Load every buffer: the GLB binary chunk in place, data URIs decoded, and files read through
   wide paths. gltfio's own buffer reads use narrow `fopen()`, which fails in folders with
   non-ASCII names. A buffer that only meshopt-compressed views use is allocated, not read.
3. Decode `EXT_meshopt_compression` and `KHR_meshopt_compression` views with the vendored
   meshoptimizer 1.0 into the buffers that the views name. Filament's meshoptimizer 0.18 has no
   version 1 vertex codec or COLOR filter, and cgltf does not know the KHR extension.
4. Build the tables: node names, visibility, morph weights, cameras, property animation tracks,
   and the anisotropy, iridescence, and diffuse-transmission materials with their texture bytes.
   On the archive material path, also a plan for each material: its archive entry, the generic
   sampler of each extension texture, and the image bytes of those textures. Textures over the
   entry's sampler count are dropped here, so the warning follows the strict setting.
5. `AssetLoader::createInstancedAsset()` with zero instances. gltfio parses the document and
   builds vertex buffers, but creates no entities or material instances yet.
6. Patch gltfio's parse (`FilamentAsset::getSourceAsset()`) by index: point its buffers at the
   loaded memory, clear the meshopt flag of decoded views, retarget pointer channels for node
   TRS and weights to their nodes, and let property-only samplers read a valid float accessor.
   gltfio's animator rejects every animation of an asset if one sampler has sparse or integer
   data. cgltf then skips the loaded buffers.
7. `AssetLoader::createInstance()` with index markers (below), then `ResourceLoader`.

The loaded buffer memory lives with the asset until gltfio releases its source data, which is
after the first instance unless `clonable=True`.

gltfio identifies nothing by glTF index. Its `MaterialProvider` callbacks get a key, a UV map, a
label (the material name), and the extras text, and its entity lists are not in node order. While
`createInstance()` runs, gltfio reads node and material extras as `json + offset`
(`AssetLoader.cpp`, `recurseEntities()` and `createMaterialInstance()`). filly points these
ranges at markers, `#n<index>` and `#m<index>`, for that call only, and then restores the parse.
The material markers reach the provider, which picks the provider-built material and its tables
by index and records the instance for property animation. The node markers become each
entity's extras, which filly reads back into a node-index table and does not expose. A missing
marker raises `AssetError`, so an SDK change that breaks this fails loudly. The previous design
rewrote the document with `filly*` keys in extras and searched them with `strstr`, which could
match user extras.

gltfio also calls `MaterialProvider::getMaterial()` in `createAsset()`, before any instance, to
choose each primitive's vertex layout; that call has only the key and the name. In an asset with
provider-built materials, the provider therefore gives every material the same UV layout rule:
the standard material's layout, then `TEXCOORD_0` and `TEXCOORD_1` in that order on the free UV
sets. The layout then does not depend on which material the primitive has. Of the returned
material, gltfio uses only the required attributes. The provider returns the material that the
name identifies, and builds it then, as gltfio's own providers do. For a name that a
provider-built material shares with another material, it returns a lit or unlit placeholder
with the same attributes. When the marker identifies the material, the provider checks that the
instance's layout is the one that the vertex buffers have. Other assets keep gltfio's layout.
The archive material path uses one fixed layout for every material instead: `TEXCOORD_0` in UV0
and `TEXCOORD_1` in UV1. It then needs no name lookup, and variant materials and runtime
textures always find their UV sets.

One document rewrite remains. `EXT_mesh_gpu_instancing` becomes one child node per instance.
gltfio renders one mesh per node and has no instancing, and new nodes cannot be added to a parse
that gltfio already holds pointers into. The core rewrites only the JSON chunk, through a small
JSON model that keeps the source text of numbers, and parses the result again. Clips keep their
indices.

WebP images decode through a `TextureProvider` for `image/webp` built on libwebp 1.5.0
(`native/webp_provider.cpp`), because the SDK is built without WebP. It follows gltfio's own
WebP provider: RGBA8 texels, sRGB when requested, and a full mip chain. Provider-built materials
use the same provider for their WebP textures. Runtime textures take pixel arrays, so they need
no image provider.

## Materials and effects beyond gltfio

Filament 1.77.1 does not supply glTF diffuse transmission. A custom material adds a
backward diffuse lobe, factor and color textures, and diffuse environment backlighting. This is a
tested subset, not a full Khronos conformance implementation. Unsupported extension combinations
on this material fail explicitly. See [API compatibility](../reference/api.md#asset-compatibility).
Volume thickness and absorption attenuate the transmitted lobe through a finite slab.
The unratified `KHR_materials_volume_scatter` extension is not implemented. The loader treats it
as an unknown extension.

The diffuse material also sets `flipUV(false)`, as the SDK's glTF materials do;
otherwise thickness and occlusion maps sample the wrong parts of an atlas.

The SDK has anisotropy and iridescence shading inputs, but its glTF loader does not expose them.
The extended provider adds their parameters and textures to a shader generator adapted from
Filament 1.77.1's `JitShaderProvider.cpp`. Filament still supplies direct lighting, environment
lighting, and the surface model. Both material modes compile these extended configurations.
Review the adapted generator when upgrading the SDK. Custom diffuse transmission remains a
separate material with its documented combination restrictions. The unratified
`KHR_materials_retroreflection` extension is not implemented.

Point lights use Filament's six-face shadow maps. The earlier wrapper rejection was incorrect;
the pinned SDK implements this path despite older wording in its LightManager header. Per-light
map size and bias settings use the same options for authored and imported lights. Shadows remain
explicit opt-in at both scene and light levels.

The scene retains the source environment cubemap and a Skybox object alongside the filtered IBL
textures. Visibility attaches or detaches the skybox without changing lighting. IndirectLight's
rotation controls both the skybox and native IBL; custom diffuse backlighting receives the same
rotation update. Clearing or replacing the environment detaches the skybox before destroying its
texture. A visible skybox writes opaque background pixels even in a transparent view.

Python normalizes modern meshopt buffers and expands instance attributes before animation
decoding and native loading. A namespaced meshoptimizer 1.0 decoder avoids symbol collisions
with the SDK's older decoder. Instance children share mesh resources, but this path does not
reduce draw calls with explicit GPU batching. Node visibility is evaluated in parent-first order
without per-frame allocation and controls scene membership for meshes and lights.

Volume materials also use the adapted generator in both material modes. Filament 1.77.1 passes
only a mesh node's local scale to its volume shader; its source marks this as a TODO.
`KHR_materials_volume` requires the complete node transform. The wrapper computes the scale from
the model-to-world transform in the vertex shader. This includes parent nodes, animation, and
application edits without modifying authored thickness or shared material instances. The scalar
is the mean of the three transformed axis lengths; nonuniform scale remains an approximation.
This is the one intended difference from `gltf_viewer` in Filament's own materials:
MosquitoInAmber, whose amber sits under a 0.1 parent scale, differs by up to 175 levels. Volume
assets without scaled parents match.
The material cache also compares the dispersion flag, which the pinned SDK's equality operator omits.

Transmission materials use this generator in both modes. For perspective cameras their shading
is the SDK's, so they match `gltf_viewer` (maximum difference 1 of 255 on the tested assets).
That includes the SDK's rough-glass blur: an angle converted to texels with `tan(full vertical
FOV)`, which fades toward 90 degrees and is undefined above it. For an orthographic camera the
SDK's value has no meaning. A sampler hook then replaces the level with the SDK's own formula at
`tan(FOV) = 1`, a 45-degree view. The blur is a fixed fraction of the image height, so it does not
change with camera distance, orthographic extents, or scene units. The hook targets only
transmission layer zero; reflection, shadow, and ambient-occlusion samples keep their levels.
It depends on the shader assembly and sampler uses in Filament 1.77.1. Review it on SDK upgrades.
It adds no render passes, textures, or samplers. An earlier version also spread the blur over an
estimated volume travel distance; it was removed because it changed perspective results.

External glTF resources are read through Unicode filesystem paths. Images are supplied to the
loader's URI cache under their original names. Desktop gltfio ignores that cache for buffers and
opens them with narrow `fopen()`, which fails for non-ASCII directories, so the preparation loads
buffers itself and gives gltfio's parse the loaded memory. Nothing is copied into a new GLB.
Percent escapes are decoded once when resolving local files.

Environment loading converts a linear panorama to a cubemap and filters it for diffuse and
specular illumination. `load_environment_ktx()` instead reads `cmgen` output: the prefiltered
cubemap is used as is, and the spherical harmonics in its metadata supply diffuse light.
`gltf_viewer` 1.77.1 reads those harmonics only to estimate a sun direction, so its diffuse light
comes from the roughest reflection level. Filament's KTX parser and upload abort on malformed
input, so the wrapper checks the file structure first. The custom diffuse-transmission material
needs an irradiance cubemap; it is filtered from a full-mip copy of the IBL's sharpest level. It performs GPU work and a completion wait during setup. Material
compilation, image decoding, and environment filtering belong outside a trial's frame loop.
The renderer creates Filament's IBL prefilter objects once and reuses them. When they were
destroyed after each call, models loaded after `set_environment()` could render black on
Intel and NVIDIA. The cause inside Filament is not isolated.

glTF animation restores authored transforms and morph weights before evaluating a clip. This
cost scales with node count but makes time jumps independent of frame history. Bone updates
follow evaluation. Imported light nodes use the same transform hierarchy as mesh nodes.

Property animation uses a separate native track list for the supported `KHR_animation_pointer`
targets. The preparation reads and validates accessors during loading. The material and node
markers described in [glTF preparation](#gltf-preparation) connect glTF indices to material
instances, light entities, and cameras without relying on names. Clips retain their original
indices even when all their channels are property channels. Native evaluation
uses binary search and fixed-size output storage, and resets authored property values before
each clip evaluation. Material writes also go to the node-local copies of the target instance;
the scan over a model's copies allocates nothing.

Camera projection and UV transform tracks update grouped values, then rebuild each matrix once
after channel evaluation. This prevents channel order from producing invalid intermediate clip
planes or overwriting another UV channel. Imported camera handles borrow glTF-owned components.
Closing an asset detaches its active cameras from every scene before destroying those components.

The build follows [nanobind's packaging interface](https://nanobind.readthedocs.io/en/latest/packaging.html)
with CMake and scikit-build-core. Windows links the pinned release SDK. Linux builds the same
Filament version from source inside manylinux_2_28, with position-independent code, libstdc++,
and the OpenGL/GLX backend. Vulkan, WebGPU, and Filament's EGL mode are disabled in that build.
The system provides the OpenGL driver and X11 libraries; the wheel contains the Filament code.

Filament's `FILAMENT_SUPPORTS_EGL_ON_LINUX` replaces GLX and compiles the OpenGL backend
against OpenGL ES headers, so one SDK cannot hold both. The wrapper adds its own EGL platform
for offscreen engines instead. It creates an OpenGL 4.1 core context, the same as Filament's
GLX platform, on an `EGL_EXT_platform_device` GPU or on Mesa's surfaceless platform, with pbuffers
as headless swap chains. Filament still loads GL entry points through BlueGL from `libGL.so.1`;
glvnd dispatches them to the current EGL context. `libEGL.so.1` is loaded with `dlopen`, so it
is not a link dependency: it is outside the manylinux library policy, and GLX users do not need
it. The device probe runs on a new thread, because glvnd does not make an EGL context current on
a thread where a GLX context is current. A software EGL renderer is chosen only without an X
display: on WSLg, GLX reaches the GPU where EGL can offer only llvmpipe. The EGL display is never
terminated, because it is a process-wide handle that other renderers may share.

The Linux module exports only `PyInit__native`. Filament marks its public API visible, and
template instantiations from the C++ library are visible regardless of `-fvisibility`; exported,
they could bind to another copy of Filament, libwebp, or libstdc++ in the process.

On Windows the extension uses the hybrid C runtime: the C++ standard library and vcruntime are
linked statically (`/MT`, the SDK's `lib/x86_64/mt` libraries), and the Universal CRT is linked
dynamically. Python ships `vcruntime140.dll` but not `msvcp140.dll`. A module that imports
`msvcp140.dll` fails to load on a PC without the Visual C++ Redistributable. It can also crash
in a host process that loaded an older copy first, such as MATLAB or a Qt application, because
code built with Visual Studio 2022 17.10 or later needs the newer `std::mutex` implementation.
The UCRT is part of Windows 10 and later, and one shared copy gives all modules in the process
one heap. The static runtime adds about 0.3 MB to the module.
`tools/check_native_imports.py` and `tests/test_native_imports.py` reject any other runtime import.

The Linux source build applies four local corrections. Two concern fence waits. glibc 2.28, the
manylinux_2_28 baseline, lacks `pthread_cond_clockwait`, so libstdc++ converts a steady-clock
deadline to the system clock. An expired polling deadline returns before entering a
condition-variable wait. Without this correction, the 10,000-frame test can stop in Filament's
UBO fence reclamation while the driver waits for more commands. A wait without a deadline
(`FENCE_WAIT_FOR_EVER`, a `time_point::max()` deadline) uses an untimed wait. The converted
deadline overflows, so `wait_until()` returns at once; Filament's frame-info thread then spins on
the fence mutex and starves the driver thread that would signal the fence. The first
`OffscreenTarget.read()` after a render hung this way. The corrections preserve asynchronous
rendering and do not add GPU completion waits.

Shared GLX contexts use the host's X display connection. Opening a separate connection can
produce incorrect shared-texture pixels under contention on Mesa. The platform borrows this
connection and does not close it. For an offscreen engine, the platform owns its display and
closes it after the driver thread exits. This keeps driver libraries loaded through thread-local
cleanup. The SDK build manifest records these patches. Review them when updating Filament.

Nanobind's linked limited-ABI mode starts at Python 3.12. One `cp312-abi3` wheel supports that
version and later standard CPython builds. Python 3.10 and 3.11 use version-specific wheels.
The wheel workflow repairs Linux dependencies, audits the stable ABI, and tests installed wheels.
It uploads artifacts without publishing to PyPI.

## Next milestones

The PsychoPy stress benchmark now records frame intervals, CPU timing, Windows process/GPU
memory samples, and resource counts over repeated asset trials. It samples memory outside the
frame loop and writes raw frames between trials. See [stress testing](../how-to/stress-test.md).
This provides a repeatable measurement tool; isolated GPU timing and display deadline validation
remain separate work.

1. Add isolated GPU timing and validate display deadlines at experimental display rates.
2. Add more Windows GPU and driver coverage.
3. Add PTB integration and macOS/native Wayland context adapters.
4. Validate Linux PsychoPy presentation and more Linux GPU drivers.
5. Measure resource growth over longer runs and larger scenes.
6. Report the pyglet 1.4.11 window-class defect to pyglet and PsychoPy (see
   [validation](../reference/validation.md)).
7. Capture a native stack for the rare crash when two processes create shared-context renderers
   at the same time, and find the owner of the thread-pool handle closed twice at exit.

The tests check functional sharing and image correctness on Windows, Linux Mesa software rendering,
and the WSLg Intel D3D12 driver. WSLg hardware tests also pass offscreen shutdown checks.
They do not establish display deadline performance or calibrated color behavior.
