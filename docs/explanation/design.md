# Design and current limits

The C++ interface in `native/renderer.h` does not include Python or Filament headers.
Nanobind converts Python values at the boundary. Filament types stay in the native implementation.
The Windows WGL and Linux GLX adapters are isolated in `native/gl_interop.cpp`.

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
viewport: on the direct path the view draws over the previous frame, and on the color-grading
path the intermediate buffer is left uncleared. Filament applies a target's clear only for the
first view of a frame, while the intermediate buffer's clear follows the current clear options.
So a `clear=False` call renders two views in one frame: first a view with an empty scene and a
constant-color skybox in the viewport, with clearing off, then the scene view with clearing on.
The skybox writes the same value that a clear would, through the same `GL_FRAMEBUFFER_SRGB`
state, and the scene view's clear reaches only its intermediate buffer. The fill pass cost
nothing measurable on the direct path and about 1 ms at 960 x 1080 with color grading and 4x
MSAA on the tested Intel GPU.

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

A generated mesh is loaded as a one-triangle glTF with the requested material and attributes,
then its renderable gets the mesh's own vertex and index buffers (`setGeometryAt()`). The loader
therefore gives it the same material as a glTF file with those factors, and the model gets
nodes, material handles, clones, and closing without a second code path. The placeholder's
accessor bounds are the mesh bounds. Positions, tangent frames, UVs, and colors are separate
vertex buffers, so an update uploads only what changed. Creation uses Filament's
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

## Output encoding

The encoding does not depend on other options. Earlier, disabling postprocessing wrote linear
values into the 8-bit target (0.5 grey became 128), and enabling it wrote sRGB values through the
selected tone mapper (188 with linear tone mapping, and a warm tint with the ACES legacy default).
The default tone mapper is now the neutral linear clamp.

Two paths write the output, selected by `Scene.output_path`:

- Color grading (`"graded"`, the default): postprocessing is on, `GL_FRAMEBUFFER_SRGB` is off,
  and the color-grading pass writes encoded values raw.
- Direct (`"direct"`, opt-in): postprocessing is off and `GL_FRAMEBUFFER_SRGB` is on for sRGB
  output, so the GPU encodes each write, clears included. Linear output writes raw values.

Offscreen targets have `SRGB8_A8` storage, and shared targets render into a `GL_SRGB8_ALPHA8`
view of the host's texture when it has immutable storage. Filament never sets
`GL_FRAMEBUFFER_SRGB`, so writes to such storage are raw unless the wrapper enables it. With color
grading it stays off and the storage kind does not matter. The wrapper's platform subclass exists
for the interop fences; setting `GL_FRAMEBUFFER_SRGB` is needed only for the direct path. The
subclass sets it on Filament's driver thread, in command order, through the same `createSync()`
hook that places the fences. It is queued only when the value changes, so steady frames add no
commands. Readback and host sampling see the stored bytes.

An earlier revision chose the direct path automatically whenever no option or material needed
postprocessing. Both paths are within one level of the analytic transfer function, but they round
differently by up to one level. A stimulus could then change by one level when an unrelated
setting changed, for example a material's alpha mode. Color grading is now always the default,
and the direct path is an explicit opt-in. The opt-in never falls back: each option or material
that it cannot render raises an error when it is set, loaded, or rendered. The options that need
postprocessing are non-linear tone mapping (linear tone mapping is a clamp, as 8-bit storage
is), FXAA, MSAA, refraction, SSAO, bloom, dithering, depth of field, vignette, and transparent
views.

Opaque views must store alpha one, as color grading does. Without postprocessing, Filament writes
the fragment shader's alpha. Lit materials write one unless they blend, and blending over a
cleared alpha of one keeps one, so the wrapper clears opaque views to alpha one. Two cases remain.
Filament's unlit shader passes base-color alpha through for `OPAQUE` materials. The wrapper
compiles unlit `OPAQUE` materials with its own generator, which sets the alpha to one, in both
shader modes; the base-color alpha of an `OPAQUE` material has no other effect. `MASK` materials
write a sharpened edge alpha that Filament computes after the material code, so the material
cannot correct it. Loading flags assets with `MASK` materials, and the direct path rejects
them. Rewriting them (for example `OPAQUE` as `MASK` with cutoff zero) was rejected: with MSAA,
alpha to coverage would then drop samples.

Transparent views need the color-grading path. That pass premultiplies after encoding,
srgb(c) * a, which is what hosts that blend in encoded space expect: PsychoPy and plain
`GL_ONE, GL_ONE_MINUS_SRC_ALPHA` blending into a non-sRGB framebuffer. The direct path would store
srgb(c * a), premultiplied in linear space, and the adapters' division by alpha would then be
wrong. A host that blends in linear space would need the other rule, but none of the supported
hosts does.

Two Filament 1.77.1 details needed handling. A per-channel tone mapper takes the precise path, a
512-entry fp16 LUT indexed in linear space, only with the engine feature
`engine.color_grading.use_1d_lut`; without it, unlit (1, 0, 0) became (247, 0, 0) and
(0, 1, 0) became (23, 247, 6), because a 32^3 10-bit LUT in Rec.2020 was used. The wrapper sets
the feature. Linear output from Filament itself always takes the 3D LUT path and wrote 242 for
linear white. For per-channel tone mappers, the wrapper therefore keeps sRGB output and wraps the
tone mapper with the inverse sRGB transfer function, so the LUT holds the linear result. Tone
mappers that mix channels keep Filament's own linear output. The render target uses an RGB16F
color buffer (`hdrColorBuffer = HIGH`); R11G11B10F keeps only 5 or 6 bits in `[0, 1]`.

A shared host texture cannot simply have sRGB storage: a host that samples it decodes the values
back to linear. The `EXT_texture_sRGB_decode` skip setting avoids that for plain texture binds,
but a sampler object overrides it, and zengl binds sampler objects. Instead, Filament renders
into a `GL_SRGB8_ALPHA8` texture view of the host's `GL_RGBA8` texture, and the host samples its
own texture. A view needs immutable storage, so the adapters allocate the host texture with
`glTexStorage2D` in native code and wrap it as a moderngl external texture or a zengl external
image. Third-party textures with mutable storage from `glTexImage2D` import without a view.
Filament then renders into the host texture itself, which works with color grading; the direct
path with sRGB encoding raises `InteropError` for such a target at render time. The wrapper
deletes a view only after Filament has finished with it and only while the host context is
current.

The rule for adding the direct path was that it must match the analytic transfer function within
one 8-bit level, offscreen and shared, and save at least 0.2 ms per 1080p frame. It meets both.
Every sweep value (the 1/64 grid and every 8-bit input level, 319 values) is within one level,
offscreen and in shared textures, for both paths and both encodings. The largest
deviation from the exact value was 0.68 levels for the direct path and 0.59 levels for color
grading. The direct path saves about 0.6 ms of GPU time per 1080p frame.

GPU time per frame from Filament's timer queries (`Renderer::getFrameInfoHistory()`), median of
five interleaved rounds, 1920 x 1080, Intel Iris Xe (driver 32.0.101.7088), offscreen target:

| Configuration | Empty (clear only) | DamagedHelmet filling the view |
| --- | ---: | ---: |
| Color grading, RGB16F intermediate (before) | 0.60 ms | 1.76 to 1.87 ms |
| Color grading, R11G11B10F intermediate | 0.57 to 0.59 ms | 1.76 to 1.90 ms |
| Color grading as a framebuffer-fetch subpass (wrong output) | 0.76 to 0.80 ms | 1.85 to 1.89 ms |
| Color grading plus FXAA | 0.93 ms | 2.23 ms |
| Color grading, transparent | 0.67 ms | 1.96 to 1.97 ms |
| No postprocessing, raw writes | 0.03 ms | 1.13 to 1.18 ms |
| No postprocessing, GPU sRGB encoding (direct path) | 0.03 ms | 1.18 to 1.20 ms |

In a shared 1080p texture the direct path took 0.05 ms and 0.95 ms, against 0.89 ms and 1.53 ms
for color grading. The breakdown shows where the postprocessing time goes. The intermediate
format does not matter, so the cost is not bandwidth to the HDR buffer. The separate pass that
`d.renderer.disable_subpasses` forces is not the cost either: the subpass is slower on this GPU.
There is no resolve and no final blit in the opaque case; the color-grading pass writes the target
directly. A transparent view adds a blending blit (0.07 to 0.2 ms). One more full-screen pass,
FXAA, costs 0.33 to 0.36 ms, so the color-grading full-screen pass itself is most of the 0.6 ms.
GPU encoding on write costs nothing measurable.

These GPU times are from the revision that added the direct path. In the current build,
`getFrameInfoHistory()` reported no GPU time after the first color-grading frame (cause not
investigated), so the current cost is measured as wall-clock time per `render()` plus `finish()`: color grading adds 0.55 ms for an empty scene and 0.66 to 0.80 ms for DamagedHelmet.
See [output path](../reference/api.md#output-path).

The pinned SDK's framebuffer-fetch color-grading subpass writes a black frame with a gradient
tile on the tested Intel Iris Xe driver, offscreen and in shared textures, with compiled and precompiled shaders.
The wrapper disables that subpass for every engine. Color grading then runs in a separate pass.
Drivers without `GL_EXT_shader_framebuffer_fetch`, such as the tested NVIDIA driver, never use
the subpass, so the setting has no effect there.

## Shared OpenGL textures

The default Filament WGL platform does not implement external sync creation. The wrapper supplies
a small WGL or GLX platform subclass through Filament's public platform interface, for offscreen
engines too. It places GL fences, waits, and the `GL_FRAMEBUFFER_SRGB` setting in the Filament
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

Transparent views retain alpha through color grading and produce premultiplied, encoded RGB. The PsychoPy
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
and the OpenGL/GLX backend. Vulkan, WebGPU, and EGL are disabled in that build.
The system provides the OpenGL driver and X11 libraries; the wheel contains the Filament code.

The Linux source build applies three local corrections. An expired polling deadline
returns before entering a condition-variable wait. Without this correction, the 10,000-frame
test can stop in Filament's UBO fence reclamation while the driver waits for more commands.
The correction preserves asynchronous rendering and does not add GPU completion waits.

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
