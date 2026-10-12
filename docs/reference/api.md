# API reference

The Python package is `filly`. This page describes the implemented subset of the spec. The
web build has a JavaScript API with the same structure; see
[use filly in a web page](../how-to/web.md).

## Logging

`filly.set_log_level(level)` sets the minimum Filament log severity for all renderers in the
process, including their driver threads. Accepted levels are `"verbose"`, `"debug"`, `"info"`,
`"warning"`, `"error"`, and `"off"`. Invalid levels raise `ValueError`.

Call `set_log_level("warning")` before creating a renderer to suppress startup details while
retaining warnings and errors. Use `"verbose"` to restore all native log streams. If the function
is not called, Filament's default output is unchanged. This setting does not affect PsychoPy
logging, Python exceptions, or direct output that bypasses Filament's log streams.

## Renderer

`Renderer(*, shared_context=None)` creates an OpenGL renderer.
`shared_context` accepts the current host WGL context on Windows or GLX context on Linux as an
integer. `current_gl_context()` returns that handle, or zero if no context is current. Passing
zero explicitly as `shared_context` is an error. On Linux, an offscreen renderer uses EGL or GLX;
see [Run on Linux](../how-to/build.md#run-on-linux). It needs no X display if EGL is available.

Every glTF material is an instance of an entry of filly's precompiled material archive, which
the build embeds in the module (see [Material archive](../how-to/build.md#material-archive)).
Nothing is compiled while loading, and the module has no material compiler. The archive has 39
entries: core lit; lit with clearcoat, sheen, and iridescence, with and without
`KHR_materials_specular`; lit with anisotropy; thin and solid refraction with and without
`KHR_materials_specular` and with anisotropy; specular-glossiness; unlit; and diffuse
transmission, each opaque, masked, and blended. Only materials with `KHR_materials_specular` get
an entry with specular inputs: with them, Filament uses the specular extension's F90 instead of
its F90 from F0, which is lower for dark metals and low IOR. See
[material precompilation](../explanation/material-precompilation.md).

The GPU driver compiles each GL program at its first draw, so render one warmup frame after
loading and before a timing-critical trial, or [prepare the scene](#prepare-a-scene) first. The
driver keeps compiled programs in its own disk cache, so the first frames are much slower on a
machine or driver that has not seen the programs. With six sample assets on the test machine,
the first frame of each took 1.3 s in total with cold driver caches (Intel Iris Xe), and 0.07 s
with warm caches. filly does not compile programs at renderer creation and does not keep its
own program binary cache: neither made the first frames faster in these measurements. See
[material precompilation](../explanation/material-precompilation.md#gap-closure-september-30-2026).

Transmission and volume shading is Filament's. For perspective cameras it is identical to
`gltf_viewer`, including its limits: rough-glass blur is an angular estimate from the vertical
field of view. It weakens toward 90 degrees and is undefined at 90 degrees and above.
Filament defines no blur for an orthographic camera. For orthographic cameras, filly uses
the blur of a 45-degree perspective view: a fixed fraction of the image height, independent of
camera distance, orthographic extents, and scene units.
Volume thickness is scaled by the complete model-to-world transform, as `KHR_materials_volume`
specifies. This includes parent nodes, animation, and model edits. The scalar is the mean of the
three transformed axis lengths, so nonuniform scale is approximate. Filament 1.77.1 uses only the
mesh node's own scale, an upstream TODO. Assets whose volume meshes have scaled parents therefore
differ from `gltf_viewer`; see [the comparison](../how-to/reference-comparison.md#results).

Both offscreen and shared-context engines reserve a 12 MiB backend command ring with the SDK's
1 MiB minimum batch size, and a 32 MiB backend handle arena. This supports the tested 10,000-mesh
asset, which queues 6.13 MiB in one load step. Windows commits the ring twice, 24 MiB per engine.
Assets with roughly 18,000 or more meshes can still exceed the ring and abort the process.

| Member | Behavior |
| --- | --- |
| `create_scene()` | Return an empty `Scene`. |
| `create_camera()` | Return a [`Camera`](#camera) with a default projection. Any scene of this renderer can use it. |
| `load_asset(source, *, strict=False)` | Return an [`Asset`](#assets) that any scene of this renderer can `add()`. |
| `load_environment(path)` | Return an [`Environment`](#environment-lighting) from a 2:1 Radiance HDR, PNG, or JPEG panorama. |
| `create_environment(pixels)` | Return an `Environment` from a NumPy panorama. |
| `load_environment_ktx(ibl_path, skybox_path=None)` | Return an `Environment` from `cmgen` KTX cubemaps. |
| `create_render_target(*, width, height, format="rgba8", depth=True)` | Return a `RenderTarget`. Dimensions must be 1 through 8192. |
| `import_gl_target(texture_id, *, width, height, format="rgba8", depth=True, on_renderer_close=None)` | Return an `ImportedTarget` for a host-owned 2D texture with `GL_RGBA8` storage. Requires a shared context. See [imported targets](#importedtarget). |
| `render(scene, target, *, camera=None, viewport=None, clear=True)` | Submit one frame to a `RenderTarget` or `ImportedTarget` and flush it to the driver. See [render calls](#render-calls). Return `None`. |
| `create_texture(pixels, *, color_space, mipmaps=False, filter="linear", wrap="repeat")` | Return a [`Texture`](#texture) with the pixels of a NumPy array. |
| `import_gl_texture(texture_id, *, width, height, color_space, filter="linear", wrap="repeat")` | Return an [`ImportedTexture`](#importedtexture) that materials sample. Requires a shared context. |
| `prepare(scene)` | Start to compile the programs of the scene and return a [`Preparation`](#prepare-a-scene). Does not block. |
| `finish()` | Block until prior GPU work finishes. |
| `close()` | Call the `on_renderer_close` callbacks of open imported targets, then release every resource of the renderer. Repeated calls are valid. |
| `closed` | Report whether the engine is closed. |
| `shared_context` | The host context handle given at creation, or `0` for an offscreen renderer. Read-only. |
| `gl_platform` | The OpenGL binding of the engine: `"wgl"`, `"glx"`, or `"egl"`. Read-only. |
| `stats` | Return a `Stats` snapshot of frame counters and CPU timings. |

The names tell what filly does with an object and who owns its storage. `render()` writes into a
target; materials sample a texture. `create_*` methods return objects whose storage filly owns:
`RenderTarget`, `Texture`, and `Environment`. `load_*` methods read a file or bytes into objects
that filly owns. `import_gl_*` methods wrap an OpenGL texture that the host owns:
`ImportedTarget` and `ImportedTexture`.

Target `width` and `height` accept Python and NumPy integers. They also accept floats with an
integral value, such as `1024.0` or `win.size[0] / 2` for an even width. A fractional or
non-finite float raises `ValueError`. Other types, including `bool`, raise `TypeError`.

Every object that has `close()` supports `with`: `Renderer`, `Scene`, `Model`, `Camera`,
`Light`, `Asset`, `Environment`, `RenderTarget`, `ImportedTarget`, `Texture`, `ImportedTexture`, and the host targets in
`filly.integrations`. Entering returns the object and raises `FillyError` if it is closed; leaving
calls `close()`, also when the block raises. An imported camera raises `FillyError` on entering,
because it closes with its model.

`close()` on the renderer closes everything that it created. Objects that it closed report
`closed=True`, and later calls on them raise `FillyError`. Use all objects on the thread that created the renderer.
An API call from another thread raises `FillyError`.
Close the renderer and release its children on the creating thread.

`Stats` contains the last measured CPU duration of each operation, in milliseconds:
`cpu_submit_ms`, `host_wait_ms`, `host_release_ms`, `finish_ms`, and `readback_ms`.
Values start at zero and change only when that operation completes. Submission includes the
incoming host-fence dependencies of the target and of imported textures, bone updates, native
rendering, and command flush. It can include
backpressure. Host wait measures fence publication and queuing the GPU dependency in
`ImportedTarget.acquire()`. It is not GPU completion time. Readback includes native allocation
and transfer, but excludes conversion to a NumPy array.
`live_models`, `live_lights`, and `material_copies` count live native resources, including imported
lights and every model of an asset. Closed Python handles do not contribute. `material_copies` counts node-local
materials. These counts do not measure GPU memory or shader caches.
`frames_rendered` counts submissions. Statistics do not measure display latency or GPU time.

### Prepare a scene

`prepare(scene)` starts to compile the GL programs that the scene needs and returns a
`Preparation` immediately. `Preparation.ready()` returns `True` when all of them are compiled.
Each call also lets Filament make progress, so call it once per frame, or poll it with a short
sleep. `Preparation.pending` is the number of materials and output passes that are not
compiled.

Call `prepare()` after you set the lights, shadows, fog, antialiasing, and output options of the
scene. It compiles the variants that those settings can use, for every material of the scene and
for the output passes. The materials of every material variant of a model count: gltfio creates
their instances at load, so `apply_variant()` at the start of a trial compiles nothing. In a test
with four variants of different material types (unlit, clearcoat, sheen, blend), the first frame
after each switch took 1.4 to 2.1 ms after `prepare()`, as steady frames did, and 19 to 38 ms
without it. It does not compile Filament's own post-processing materials, for example
the blit of a transparent scene or the SSAO and bloom passes. Those compile at their first use, once
for each renderer.

On the test machine (Intel Iris Xe, warm driver cache), `ready()` was `True` after 133 to 160 ms,
and the first render of a lit sphere took 11 to 13 ms instead of 17 to 24 ms. For browsers, see
[web](../explanation/web.md#shader-compilation).

### Render calls

`render(scene, target, *, camera=None, viewport=None, clear=True)` renders one frame.

- `camera`: a `Camera` of the same renderer. It replaces `scene.camera` for this call only. A
  scene without a camera can render with one. Without `camera`, `scene.camera` is required. A
  scene has no camera until you set one, and loses it when that camera closes; the `FillyError`
  tells which of these happened.
- `viewport`: `(x, y, width, height)` in target pixels, counted from the lower-left corner. It
  must lie inside the target. Values follow the target size rules above. Default: the whole
  target.
- `clear`: with `True`, the whole target is cleared to `scene.background` before the frame. With
  `False`, pixels outside the viewport keep their contents. The viewport still starts from the
  background, so the frame never shows an earlier frame through it.

A camera that follows the target aspect uses the viewport aspect. The camera, viewport, and
clear options allocate no memory.
Imported targets follow the usual [acquire rules](#importedtarget): render both halves, then
acquire once. Render into the same target without acquiring in between.

Side-by-side stereo into one target:

```python
renderer.render(scene, target, camera=left, viewport=(0, 0, w // 2, h))
renderer.render(scene, target, camera=right, viewport=(w // 2, 0, w - w // 2, h), clear=False)
```

Each call is a separate Filament frame: the second call adds one frame's fixed cost. On the exact
path, `clear=False` encodes only the viewport. On the direct path it renders one more pass that
fills the viewport with the background; that pass cost nothing measurable. Measured before the
exact path existed, at 1920 x 1080 with two 960 x 1080 eyes on the tested Intel Iris Xe GPU,
including a `finish()`: one full frame of a lit sphere took 1.8 ms on the direct output path and
5.4 ms with Filament's color grading and 4x MSAA; two eyes took 3.2 ms and 7.9 ms. CPU submission
was 0.1 to 0.3 ms per call.

## Scene

| Member | Behavior |
| --- | --- |
| `camera` | Get or set the active camera. It must belong to the same renderer. A new scene has no camera; getting it then raises `FillyError`. |
| `load(source, *, strict=False)` | Load glTF or GLB from a path, `Path`, or `bytes`, and return a `Model`. GLB is detected by its `glTF` magic, not by the file name. To show a file more than once, use an [`Asset`](#assets). |
| `add(asset)` | Return a new `Model` of an [`Asset`](#assets) of the same renderer. |
| `create_mesh(positions, indices, *, normals=None, uvs=None, colors=None, base_color=(1, 1, 1, 1), metallic=0, roughness=1, emissive=(0, 0, 0), unlit=False, double_sided=False, alpha_mode="opaque")` | Return a `Model` made from arrays. See [generated meshes](#generated-meshes). |
| `add_directional_light(*, direction, intensity=50000, color=(1, 1, 1))` | Return a `Light`. Intensity is in lux. |
| `add_sun_light(*, direction, intensity=100000, color=(1, 1, 1), angular_radius_deg=0.545, halo_size=10.0, halo_falloff=80.0)` | Return a sun `Light`: a directional light with a disc. Intensity is in lux. |
| `add_point_light(*, position, intensity=50000, color=(1, 1, 1), range=10)` | Return a point `Light`. Intensity is in candela; range is in scene units. See [default intensities](#default-intensities). |
| `add_spot_light(*, position, direction, intensity=50000, color=(1, 1, 1), range=10, cone_deg=(15, 30))` | Return a spot `Light`. `cone_deg` is the inner and outer half-angle in degrees; intensity is in candela. |
| `background` | Get or set linear RGBA values in `[0, 1]`. Default: `(0, 0, 0, 1)`. |
| [Rendering options](#rendering-options) | `encoding`, `output_path`, `tone_mapping`, `antialiasing`, `msaa`, `shadows`, `transparent`, `dithering`, `ssao`, `bloom`, `fog`, `depth_of_field`, `vignette`, and the [fog](#fog) and [vignette](#vignette) parameters. |
| [Environment members](#environment-lighting) | `environment`, `environment_intensity`, `environment_rotation_deg`, `environment_visible`. |
| `close()` | Release the scene's models, lights, and view. Renderer cameras, assets, and environments stay open. Repeated calls are valid. |
| `closed` | Report whether the scene or its renderer is closed. |

Byte assets must contain all resources. Path assets can use local external buffers and images,
also in folders with non-ASCII names. PNG, JPEG, Basis Universal KTX2, and WebP
(`EXT_texture_webp`) textures are supported. The extension decodes WebP with libwebp 1.5.0.
A scene keeps its models, its lights, and its active camera alive without Python handles.

After `close()`, the scene's models and lights, and the imported cameras of those models, report
`closed=True` and raise `FillyError` on use. A scene that uses one of those imported cameras loses
it, and its next `render()` raises `FillyError`. Renderer cameras stay open. Rendering a closed
scene, or any other call on it, raises `FillyError`. Other scenes are not affected.

### Rendering options

Each option is a scene property. An assignment validates the value and changes only that option;
an invalid value raises `ValueError` and keeps the previous value. The options take effect at the
next `render()`.

| Property | Values | Default |
| --- | --- | --- |
| `encoding` | `"srgb"`, `"linear"` | `"srgb"` |
| `output_path` | `"exact"`, `"direct"`. See [output path](#output-path). | `"exact"` |
| `tone_mapping` | See the table below. | `"linear"` |
| `antialiasing` | `"none"`, `"fxaa"` | `"none"` |
| `msaa` | `1`, `2`, `4`, `8` (effective counts depend on the driver) | `1` |
| `shadows` | `bool`. Lights also need `light.casts_shadows = True`. | `False` |
| `transparent` | `bool`. Keep alpha in the output. | `False` |
| `dithering` | `bool` | `False` |
| `ssao` | `bool` | `False` |
| `bloom` | `bool` | `False` |
| `fog` | `bool`. See [fog](#fog). | `False` |
| `depth_of_field` | `bool`. See [depth of field](#depth-of-field). | `False` |
| `vignette` | `bool`. See [vignette](#vignette). | `False` |

#### Output encoding

`encoding` sets how linear scene color is stored in the 8-bit target. It applies to the output
of a `RenderTarget` and of an `ImportedTarget`, and no other option changes it.

- `"srgb"`: apply the sRGB transfer function. A host that samples the texture as plain RGBA8 into
  a non-sRGB framebuffer, and a NumPy readback, both see sRGB values: an unlit 0.5 grey is 188.
- `"linear"`: store linear values: an unlit 0.5 grey is 128.

On the default output path, the stored level is the rounded analytic value of the scene-linear
color as filly's RGBA16F buffer holds it: exact for every fp16 value. A color that is not an fp16
value is first stored as one of its two fp16 neighbors, so an unlit color is within 0.54 levels of
its analytic value (one level at most after rounding). See
[output encoding](../explanation/design.md#output-encoding).

#### Output path

`output_path` selects how the output is written. Both paths are within one 8-bit level of the
analytic transfer function, but they round differently: the same pixel can differ by one level
between them.

- `"exact"` (default): the scene renders scene-linear color into an RGBA16F buffer, and filly's
  encode pass applies the linear tone mapper's clamp, the transfer function in fp32, and the alpha
  rule, and rounds to 8-bit levels. Every option and material works on this path, and every
  option ends in the same encode pass: an option that does not change the linear color does not
  change a stored level. Options that are part of Filament's postprocessing (tone mapping other
  than `"linear"`, bloom, depth of field, vignette) run it first; its output stays linear, with
  one exception: a [tone mapper that mixes channels](#tone-mapping) with `encoding = "srgb"`.
- `"direct"`: the shader output goes straight to the target, and the GPU applies the sRGB
  encoding on write. Linear output writes raw values. It saves the encode pass.

The scene never changes the path by itself, so a stimulus does not change by one level when
another option or a material changes.

The direct path cannot render some settings correctly. It never falls back to the exact path.
Instead, each conflict raises an error at the point where it occurs:

| Conflict | Error |
| --- | --- |
| Set `output_path = "direct"` while `tone_mapping` is not `"linear"`, `antialiasing` is `"fxaa"`, `msaa` is above 1, or `transparent`, `dithering`, `ssao`, `bloom`, `depth_of_field`, or `vignette` is `True` | `ValueError` that names each setting. The path does not change. |
| Set one of these options while `output_path` is `"direct"` | `ValueError`. The option does not change. |
| Set `output_path = "direct"` while a model in the scene has a material with `alphaMode` `MASK`, or a transmission or volume material | `ValueError` |
| Load or add an asset, or create a mesh, with a `MASK` material or a transmission or volume material while `output_path` is `"direct"` | `AssetError`. The scene gets no model. |
| Render with `encoding = "srgb"` to an [imported target](#importedtarget) whose texture has mutable storage (`glTexImage2D`) | `InteropError`. Nothing is rendered. |

Filament writes the sharpened edge alpha of `MASK` materials to the target, and only the encode
pass stores alpha one. Transmission and volume materials sample Filament's screen-space refraction
pass, which the direct path has excluded from the start. `fog`, `shadows`, `encoding`, lights,
environments, and every other material work on both paths. A scene that is not transparent
stores alpha one on both paths.

GPU frame time at 1920 x 1080 on the tested laptop, from Filament's timer in a 60 Hz loop, median
of five interleaved rounds of 600 frames:

| Scene | `"exact"` | `"direct"` |
| --- | ---: | ---: |
| Empty, offscreen, Intel Iris Xe | 0.80 ms | 0.11 ms |
| DamagedHelmet, offscreen, Intel Iris Xe | 2.32 ms | 1.61 ms |
| DamagedHelmet, pyglet shared texture, Intel Iris Xe | 2.33 ms | 1.63 ms |
| DamagedHelmet, offscreen, NVIDIA RTX A500 | 0.28 ms | 0.18 ms |

The exact path also takes about 0.1 ms more CPU time per `render()`. At 512 x 512 the difference
is 0.11 to 0.12 ms of GPU time on the Intel GPU. Use `"direct"` when the frame budget needs this
time and the scene uses none of the conflicting settings. See
[Performance](performance.md#output-path-september-30-2026).

Material factors, background colors, light colors, and environment values are linear inputs.

#### Tone mapping

| `tone_mapping` | Filament tone mapper |
| --- | --- |
| `"linear"` | `LinearToneMapper`: clamps each channel to `[0, 1]`. Neutral colors stay neutral. The default. |
| `"aces_legacy"` | `ACESLegacyToneMapper`. The default in `gltf_viewer`. It tints neutral grey. |
| `"aces"` | `ACESToneMapper` |
| `"filmic"` | `FilmicToneMapper` |
| `"pbr_neutral"` | `PBRNeutralToneMapper` (Khronos PBR Neutral) |
| `"gt7"` | `GT7ToneMapper` |
| `"agx"`, `"agx_punchy"`, `"agx_golden"` | `AgxToneMapper` with look `NONE`, `PUNCHY`, or `GOLDEN` |
| `"generic"` | `GenericToneMapper` with its default curve |
| `"display_range"` | `DisplayRangeToneMapper`, a false-color exposure check |

Set `tone_mapping = "aces_legacy"` to match `gltf_viewer`.

`"linear"`, `"filmic"`, and `"generic"` map each channel on its own. filly then applies the sRGB
transfer function exactly, as for every other option. The other tone mappers mix channels and
run in Filament's 3D LUT. With `encoding = "srgb"`, Filament's color grading encodes them to sRGB
and rounds to 8-bit levels, as `gltf_viewer` does, and filly's encode pass stores those levels
unchanged: the output equals `gltf_viewer`'s (DamagedHelmet, TransmissionTest, ClearCoatTest,
SheenChair: maximum difference 0). In this configuration:

- `dithering = True` uses Filament's dithering, the same one-level triangular noise before the
  8-bit rounding.
- Transparent output is srgb(c) * a, as on the linear route.
- FXAA runs on the encoded image, as on the linear route.
- With `encoding = "linear"`, Filament writes linear color from its 3D LUT and filly stores it
  without a transfer function. That LUT holds 10-bit linear values, so dark colors are coarse:
  encoded afterwards, levels are within 3 of the sRGB route, and more near black.

#### Effects

`antialiasing = "fxaa"` runs filly's FXAA pass on the encoded image (FXAA 3.11 console with the
G3D patches, as in Filament 1.77.1). A pixel without an edge keeps its exact level, so a flat
field is identical with and without FXAA. `dithering=True` adds one level of Filament's triangular
noise pattern after the transfer function, before rounding (in Filament's color grading for a
tone mapper that mixes channels). The pattern changes on every frame,
so identical frames can differ by one 8-bit level. `ssao=True` adds screen-space ambient
occlusion to environment lighting. `bloom=True` adds bloom with strength 0.1. SSAO and bloom use
Filament's default options, as `gltf_viewer` does. Temporal antialiasing, screen-space
reflections, and dynamic resolution are not available, so no effect accumulates history between
frames. Filament 1.77.1 has no motion blur. Fog, depth of field, and vignette read only the current frame: equal
inputs give equal output, whatever frames came before.

Glass needs no option. Transmission and volume materials refract through Filament's
screen-space refraction pass, which every scene has on the exact path. Filament runs the pass
only in frames that draw such a material, so a scene without glass renders the same pixels in
the same time: on the test laptop, a 1920 x 1080 frame of the horse took 0.40 to 0.48 ms with
the pass on and off, with identical pixels. Without the pass, these materials render as flat white instead of refracting,
which is why there is no option to turn it off.

#### Fog

`fog = True` blends each fragment toward a fog color by its distance from the camera:
`opacity = 1 - exp(-density * max(distance - start, 0))`. The distance is the length of the
camera-to-fragment vector, not the depth. Fog is uniform in height. The background color is
not fogged; a visible environment is infinitely far away and takes the fog color.

Each fog parameter is a scene property. A parameter keeps its value while `fog` is `False`.

| Property | Values | Default |
| --- | --- | --- |
| `fog_color` | Linear RGB in `[0, 1]`: the output color of a fully fogged fragment. | `(1, 1, 1)` |
| `fog_density` | Nonnegative, per scene unit. | `0.1` |
| `fog_start` | Nonnegative distance in scene units. | `0` |

Filament scales the fog color by the environment intensity (30000 lux without an environment)
and the camera exposure. The renderer divides that scale out at each `render()`, for the camera
of that call, so the color does not depend on lighting or exposure. With an environment intensity
of zero, fog is black. Fog is computed in the material shaders, so it works on both output paths.

#### Depth of field

`depth_of_field = True` blurs each pixel by its thin-lens circle of confusion. The camera
supplies the inputs: `camera.focus_distance` in scene units, read as meters, and
`camera.aperture` as an f-number. The focal length comes from the vertical field of view on
Filament's 24 mm sensor height (`gltf_viewer` uses the same sensor). The blur diameter on the
sensor is `f^2 / (N * (S - f)) * |1 - S / d|` for focal length `f`, f-number `N`, focus
distance `S`, and fragment distance `d`, scaled to pixels by the viewport height over 24 mm.
Filament clamps large blurs. The camera aperture for exposure is separate: `aperture` does not
change image brightness.

Depth of field needs a perspective camera. Only opaque materials write depth, so blended
materials take the blur of what is behind them. The effect is part of Filament's postprocessing.
Its passes store color as R11G11B10F at half the width and height, so flat areas can change by up
to 2 levels.
Its noise depends on the pixel position only.

#### Vignette

`vignette = True` darkens the image toward its edges in Filament's color-grading pass, which then
runs before filly's encode pass. The center pixel is unchanged. Each parameter is a scene property
with the default of Filament's `VignetteOptions`, and it keeps its value while `vignette` is
`False`.

| Property | Values | Default |
| --- | --- | --- |
| `vignette_midpoint` | `[0, 1]`. Moves the start of the falloff. | `0.5` |
| `vignette_roundness` | `[0, 1]`. From a rounded rectangle to a circle. | `0.5` |
| `vignette_feather` | `[0.05, 1]`. The width of the falloff. | `0.5` |
| `vignette_color` | Linear RGB in `[0, 1]` at the edges. | `(0, 0, 0)` |

#### Transparent output

Set `transparent = True` and `background = (0, 0, 0, 0)` to keep alpha. Transparent scenes use
the exact path. The output is premultiplied after encoding: RGB is the encoded straight color
multiplied by alpha. An unlit
0.5 grey at alpha 0.5 is `(94, 94, 94, 128)` with sRGB encoding. This is what OpenGL blending
with `GL_ONE, GL_ONE_MINUS_SRC_ALPHA` into a non-sRGB framebuffer expects. The background is
straight RGBA; the renderer multiplies its RGB by alpha. The PsychoPy adapter converts to its
normal ImageStim blending. Refraction samples the Filament scene; it cannot refract host
stimuli behind the shared image.

### Environment lighting

An `Environment` is image-based lighting that the renderer owns. Any of its scenes can use it,
and they share its filtered textures, so a panorama is filtered once, not once per scene:

```python
environment = renderer.load_environment("studio.hdr")
scene.environment = environment
other_scene.environment = environment
other_scene.environment_intensity = 15000  # this scene only
```

Renderer methods that return an `Environment`:

- `load_environment(path)`: load a 2:1 panorama. Radiance HDR, PNG, and JPEG are accepted. HDR
  values retain their dynamic range.
- `create_environment(pixels)`: accept a contiguous NumPy `float32` RGB array with shape
  `(height, 2*height, 3)`. Values must be finite, nonnegative, linear light.
- `load_environment_ktx(ibl_path, skybox_path=None)`: load a prefiltered cubemap made by
  Filament's `cmgen` (`cmgen --format=ktx`), as `gltf_viewer` does. The IBL file must contain
  spherical-harmonics metadata (`sh`); it supplies diffuse lighting. Without `skybox_path`, the
  sharpest IBL level is the skybox.

`Environment.close()` removes the environment from every scene that uses it and releases its
textures; `closed` reports it. Equal environments compare equal, also as separate handles.

Scene members:

- `environment`: get or set the scene's `Environment`, or `None`, the default. Setting `None`
  removes it from this scene only. An environment of another renderer raises `ValueError`, and
  a closed one `FillyError`.
- `environment_intensity`: intensity in lux. Default: `30000`.
- `environment_rotation_deg`: rotation about Y in degrees. Default: `0`. This rotates the
  skybox, reflections, diffuse lighting, and diffuse backlighting.
- `environment_visible`: draw the environment as an opaque skybox. Default: `False`.

The three settings belong to the scene: each scene that shares an environment has its own, and
they keep their values without an environment and when it changes. Without an environment, the
background color applies.

Panorama height must be 2 through 4096. Loading filters the panorama for diffuse lighting and
specular reflections and waits for GPU completion: 54 to 83 ms on the test machine for heights 64
through 512. Do this before the frame loop. A scene then takes the environment in 0.03 to 0.3 ms;
before environments were shared, each scene filtered the panorama again.

If all pixels of a panorama have the same value, `create_environment()` does not filter it: a
uniform environment is the same for all roughness levels. This took 0.8 ms instead of 54 ms on
the test machine, and 2 to 30 ms instead of about 260 ms in Chrome. The light is the same as
with filtering for values that R11F_G11F_B10F holds exactly, such as 1.0. For other values, 8-bit
output pixels can differ by up to 2 levels. The environment lights the scene even when hidden. Set
`scene.environment_visible = True` to show it instead of the background color. Skybox brightness
uses environment intensity and camera exposure; its output alpha is one, including in a transparent
view. Leave it hidden to composite over PsychoPy stimuli. The source cubemap remains resident so
visibility and rotation changes do not reload or filter the panorama.

KTX environments are cubemaps in R11F_G11F_B10F, RGB16F, RGBA16F, RGB32F, or RGBA32F.
Malformed files raise `AssetError` before Filament reads them. The IBL's prefiltered levels are
used without filtering. Diffuse lighting uses the 3-band spherical harmonics. `gltf_viewer`
1.77.1 reads the harmonics but does not pass them to `IndirectLight`, so its diffuse lighting
uses the roughest reflection level instead. Skyboxes draw the disc of a sun light.

### Asset compatibility

Unknown optional glTF extensions emit `filly.AssetCompatibilityWarning`, a `UserWarning`.
Unknown required extensions raise `AssetError`. `strict=True` turns compatibility warnings into errors. The metadata-only
`KHR_xmp` and `KHR_xmp_json_ld` extensions are ignored without a warning. The draft
`KHR_materials_volume_scatter` and `KHR_materials_retroreflection` extensions are unknown
extensions; a material that uses them renders without that effect. These checks are not a
complete glTF validator.

The native loader runs the checks and collects the warnings. `scene.load()` and
`renderer.load_asset()` issue the warnings after the native load returns, or before they raise an
error. If a warning filter turns a warning into an exception, the model or asset is closed and
the exception propagates.

### Texture limits

Every archive entry has the five core textures (base color,
metallic-roughness or specular-glossiness, normal, occlusion, emissive). Extension textures
(clearcoat, sheen, transmission, thickness, specular, anisotropy, iridescence) share the generic
samplers of the entry: 4 in non-refractive lit entries and 3 in refractive entries. Roles that
use the same glTF texture with the same color space share a sampler. A material with more
distinct extension textures than its entry has samplers loads without the least important ones,
and `AssetCompatibilityWarning` names the material and each dropped texture (`strict=True` raises
`AssetError`). Textures are dropped in this order: `clearcoatNormalTexture`,
`sheenRoughnessTexture`, `clearcoatRoughnessTexture`, `iridescenceThicknessTexture`,
`specularTexture`, `specularColorTexture`, `sheenColorTexture`, `clearcoatTexture`,
`iridescenceTexture`, `anisotropyTexture`, `thicknessTexture`, and `transmissionTexture` last.
Detail maps go first, and the maps that define where an effect exists or how strong it is go
last. No Khronos sample material needs a drop. The limits are the same in the web build.
Textures read `TEXCOORD_0` or `TEXCOORD_1`; a core texture on another set is not drawn, with a
warning, and an extension texture on another set raises `AssetError`. A specular-glossiness
material renders without clearcoat, sheen, specular, transmission, and volume, with a warning.

The loader supports punctual lights, unlit materials, clearcoat, sheen, transmission, volume, IOR,
specular, emissive strength, specular-glossiness, dispersion, variants, texture transforms,
mesh quantization and Draco through Filament. Both `EXT_meshopt_compression` and
`KHR_meshopt_compression` are decoded during loading with meshoptimizer 1.0, into the fallback
buffers that the compressed views name. This includes version 1 vertex data and the COLOR filter,
compressed pointer accessors, and compressed instance attributes.
This is not a glTF conformance claim. `KHR_materials_emissive_strength` scales the emissive
factor once, as the extension specifies. Filament 1.77.1's gltfio applies it twice: it multiplies
the factor by the strength and also passes the strength to the shader (factor 0.05 with strength 4
renders as factor 0.8). filly restores the unscaled factor after loading, so its output differs
from `gltf_viewer` for materials with a strength other than 1.
`KHR_animation_pointer` has the tested subset listed below.
Dispersion requires volume and rejects unlit or specular-glossiness combinations
before native shader compilation.

Anisotropy and iridescence use Filament's native shading inputs with added glTF parameters.
Anisotropy supports strength, rotation in radians, and a linear texture with direction in RG and
strength in B. Iridescence supports factor, IOR, and minimum/maximum thickness in nanometers.
Its linear factor texture uses R; its thickness texture uses G. Without a thickness texture,
the maximum thickness applies. These textures support UV sets 0 and 1, texture transforms, and
sampler settings. Both extensions work with standard PBR extensions, including clearcoat and
glass. Unlit, specular-glossiness, and custom diffuse-transmission combinations raise `AssetError`.
Results use Filament's shading approximations, including anisotropic environment reflections.
For iridescence, Filament 1.77.1 evaluates film Fresnel at the view-normal angle and fits it to
a view-dependent normal-incidence reflectance (F0). Its direct diffuse lobe omits the glTF
extension's maximum-channel energy weighting. Dielectric film colors respond to thickness,
but reference-image parity is not established. See the [sample comparison](sample-assets.md#dielectric-iridescence).

`KHR_node_visibility` combines each node's visibility with that of its ancestors. Hidden nodes
hide meshes and lights, including descendants, but imported cameras remain usable. Model visibility
is a separate override; turning a model back on preserves authored and animated node visibility.

`EXT_mesh_gpu_instancing` expands TRS attributes into child nodes that share mesh resources.
Parent transforms and animation apply to every instance. Sparse accessors and normalized signed
quaternions are supported. Application-defined attributes such as `_COLOR` are ignored.
This is a compatibility path, not hardware draw batching; large instance counts can remain expensive.

Diffuse transmission uses a custom shader with direct and environment backlighting.
It supports factor and color textures, UV sets 0 and 1, texture transforms, sampler filters,
alpha modes, normal maps, IOR, emissive strength, and volume attenuation with a G-channel thickness
texture. UV orientation follows glTF. Thickness uses the complete model-to-world scale, as for volume materials.
Its emission is not scaled by the camera exposure, as in the other lit materials; the
environment backlighting is. Dispersion has no effect on this diffuse-only transport path.
Other diffuse-transmission combinations raise `AssetError`.

Full Khronos reference-renderer parity is not implemented. Screen-space glass also cannot show
geometry outside the rendered view.

## Light

`type` is read-only: `"directional"`, `"sun"`, `"point"`, or `"spot"`.

| Property | Applies to | Values |
| --- | --- | --- |
| `color` | All | Linear RGB in `[0, 1]`. |
| `intensity` | All | Nonnegative. Lux for directional and sun lights, candela for point and spot lights. |
| `position` | Point, spot | Three coordinates. |
| `direction` | Directional, sun, spot | A nonzero vector. It reads back normalized. |
| `range` | Point, spot | Positive distance in scene units at which the light reaches zero. |
| `cone_deg` | Spot | `(inner, outer)` half-angles in degrees, `0 <= inner <= outer < 90`. |
| `casts_shadows` | All | `bool`. See below. |
| `shadow_map_size` | All | Power of two from 8 through 4096. Default: 1024. |
| `shadow_constant_bias` | All | Nonnegative, in world units. Default: 0.001. |
| `shadow_normal_bias` | All | Nonnegative. Scales Filament's sampling-error estimate. Default: 1. |

A property that does not apply to the light's type reads as `None`, and setting it raises
`ValueError`, because the light would ignore the value.

#### Default intensities

At the default camera exposure (EV100 about 15), the defaults light a white diffuse surface that
faces the light to the same level: 50000 lux for a directional light, and 50000 cd for a point or
spot light 1 m away (illuminance is intensity over distance squared). A linear tone mapper and sRGB
encoding store this surface as about 173 of 255. A sun light defaults to 100000 lux, as in
`gltf_viewer`. For a light at distance `d`, multiply the intensity by `d * d` to keep the same
brightness.
A sun light shades like a directional light with a disc of `angular_radius_deg`, from 0.25 through
20 degrees. Filament moves the light direction toward each pixel's reflection within that disc,
so specular highlights change the most. A visible skybox draws the disc and its halo;
`halo_size` multiplies the disc radius and `halo_falloff` sets how fast the halo fades.
`gltf_viewer` uses the defaults for its sun.
Directional, point, and spot lights can cast shadow maps. Set both `scene.shadows = True` and
`light.casts_shadows = True`. Imported glTF lights use the same controls. A point light uses six
shadow-map faces, so its cost can be higher than a spot light. Filament limits the total shadow
maps in a view.

The shadow properties change resolution and depth bias without enabling shadows, and each one
changes only its own value. `shadow_map_size` is the resolution per face. Set them before warmup;
resolution changes can reallocate maps. Shadow maps do not model colored light transmission
through glass or translucent volumes.

When `cone_deg` changes, the intensity in candela does not change, so the brightness on the cone
axis stays the same. This is the glTF rule. Scene-created spot lights and imported spot lights
behave the same way, and so do animated cones. `cone_deg` reads back the values that were set,
authored, or animated; Filament keeps only values derived from them.

Imported lights are available through `model.light(key)` and `model.lights`. The key is the
name or glTF index of the node that carries the light. `light.node` returns that `Node`; it is
`None` for lights that a scene created. Position and direction are local to the light entity.
Imported light nodes retain the glTF hierarchy; animate or move their node to change their
placement in that hierarchy.

`light.close()` removes a light. Repeated calls are valid. `closed` is read-only. Imported lights
lose their light component but retain the node for animation. `model.lights` leaves closed lights
out, as a scene's closed lights leave the scene; `model.light(key)` still returns the handle,
because the node remains, and the handle reports `closed=True`. Other calls on it raise
`FillyError`. Closing the model or its scene also invalidates its light handles.
Scene-created lights release their entity and scene ownership. Lights support `==` and `hash()`.
Two handles are equal when they refer to the same light.

## Camera

`renderer.create_camera()` returns a camera that belongs to the renderer, as in Filament. Several
scenes can use one camera. Closing a scene does not close the cameras that it uses. A camera
stays open until `camera.close()`, until the renderer closes, or until no handle and no scene
refers to it. Imported cameras belong to their model; see [imported cameras](#imported-cameras).

A new camera is at the origin and looks along -Z. It has a perspective projection with a vertical
field of view of 30 degrees, `near=0.1`, and `far=100`, and it follows the target aspect. A
desktop monitor at a typical viewing distance spans about 30 degrees vertically, so a framed model
looks as it would on that monitor. Usually you only aim the camera:

```python
scene.camera = renderer.create_camera()
scene.camera.frame(model)
```

- `close()`: release the camera. Scenes that use it have no active camera after that. Repeated
  calls are valid. Raises `FillyError` for imported cameras.
- `closed`: whether the camera, its model, or its renderer is closed.
- `set_perspective(*, fov_y_deg, aspect=None, near=None, far=None)`: vertical field of view in
  degrees, `0 < fov_y_deg < 180`.
- `set_lens_projection(*, focal_length_mm, aspect=None, near=None, far=None)`: perspective
  projection from a focal length on Filament's 24 mm sensor height. `gltf_viewer` uses 28 mm.
- `set_orthographic(*, left, right, bottom, top, near=None, far=None)`: explicit orthographic
  bounds.
- `set_orthographic(*, height, center=(0, 0), near=None, far=None)`: orthographic view of `height`
  scene units around `center`. The width is `height` times the target aspect.
- `position`: three coordinates.
- `look_at(target, up=(0, 1, 0))`: set the viewing direction.
- `frame(target, *, fill=0.8, direction=None, up=(0, 1, 0), fit="sphere", near=None, far=None, aspect=None)`:
  aim at a target and move the camera so that it fills part of the view. See
  [framing](#framing).
- `transform`: affine 4 by 4 model matrix.
- `view_matrix`: copy of the view matrix.
- `projection`: copy of Filament's projection matrix.
- `exposure`: camera EV100 in `[-10, 24]`. Lower values produce a brighter image.
- `focus_distance`: [depth-of-field](#depth-of-field) focus distance in scene units,
  nonnegative. Default: 0. Filament focuses no closer than the near plane, so 0 focuses there.
- `aperture`: depth-of-field f-number in `[0.5, 64]`. Default: 16. It does not change exposure.

With `aspect=None`, and with the `height` form of `set_orthographic`, the projection follows the
render target: each `render()` applies the target's width divided by its height if it differs
from the last applied value. An explicit `aspect` wins over the target. A positive `aspect` is
required when it is given. `set_orthographic` accepts either all four bounds or `height`, not
both; `center` requires `height`.

The `near` and `far` arguments of the projection methods and of `frame()` work as follows:

- A plane that you give stays until the next projection call. `frame()` does not change it.
- A plane that you do not give to a projection method keeps its current value: the default, or
  the value from the last `frame()`. The next `frame()` sets it so that it encloses the target.

The planes must satisfy `0 < near < far`, with given and kept planes together.

`projection` returns the matrix that the next render uses for the last target aspect. Before the
first render, a projection that follows the target uses aspect 1. After a render into a target
with another aspect, it reflects that target. The update does not allocate memory.

Default exposure is approximately EV100 15, equivalent to aperture 16, shutter time 1/125 second,
and ISO 100. Dim authored lights can require a much lower EV100.

Cameras support `==` and `hash()`. Two handles are equal when they refer to the same camera.
`camera.node` returns the `Node` of an imported camera, and `None` for a camera from `renderer.create_camera()`.

### Framing

`camera.frame(target, ...)` aims the camera at a target and sets its distance, so that the target
fills part of the view. It returns the distance from the camera to the target's center. It does
not follow the target: call it again after the target moves.

- `target`: a `Model` or a `Node`, which use their [`bounds`](#model) moved by the model's
  current transform, or a `(min, max)` box in world coordinates. `frame()` copies the box, so a
  later change of the model does not change the framing.
- `fit="sphere"` frames the sphere that circumscribes the box: its center is the box center, and
  its radius is half the box diagonal times the largest scale factor of the model transform. A
  rotation of the model does not change it, so a model that spins between trials keeps the same
  framing. The sphere of a box is up to 1.73 times larger than the object, so the object looks
  smaller than `fill`. `fit="box"` fits the box's corners tightly for the given direction.
- `fill` in `(0, 1]`: the fraction of the view that the target spans. For `"sphere"`, the
  sphere's projection spans `fill` of the narrower image axis (a sphere on the optical axis
  projects to a circle). For `"box"`, every projected corner lies within `fill` of the half-width
  and half-height from the image center, and at least one corner reaches it.
- `direction`: the viewing direction, from the camera toward the target. Default: the camera's
  current direction, which is -Z for a new camera. `up` must not be parallel to it. The camera
  then looks at the target's center with `up`, as `look_at()` does.
- A perspective camera keeps its field of view; `frame()` sets its distance. An orthographic
  camera gets the `height` form of `set_orthographic()`, so it follows the target aspect, and a
  distance that keeps the target between the clipping planes.
- `near` and `far`: planes to use. A plane that you give here stays, as one that you give to a
  projection method does. Each plane that you did not give since the last projection call
  encloses the target (its sphere, or the box corners). The margin is 5% of the target's depth
  along the view, or of its sphere radius for a flatter target. `projection` shows only `near`: Filament renders with the far plane at infinity and
  uses `far` for culling.
- `aspect`: the target aspect for the horizontal extent. See the next paragraphs.

The distance depends on the target aspect: for `"sphere"` when the render target is taller than wide,
and for `"box"` always. Without `aspect`, `render()` frames a camera that follows the target
aspect again when the target aspect differs from the aspect of the framing. The camera then
keeps the new framing. So the target fills `fill` of the view also when you frame before the
first render, or when you render into targets of different aspects. The new framing uses the same
copied box and arguments, and it allocates no memory. Setting `position` or `transform`, or
calling `look_at()`, a projection method, or `frame()`, ends it.

Until the first render, `position`, `transform`, and `projection` show the framing for aspect 1,
or for the aspect of the last render. If you read them before the render, for example to place
the camera relative to the framing, pass `aspect`. With `aspect`, `frame()` uses that aspect and
`render()` does not frame again. A camera with a fixed aspect uses its own aspect.

`frame()` raises `ValueError` for a `fill` outside `(0, 1]`, a target with empty, non-finite, or
zero-size bounds (such as a node without a mesh below it), a zero `direction` or `up`, a
parallel `up`, an unknown `fit`, invalid planes, or an aspect that is not positive; `TypeError`
for another target type; and `FillyError` for an imported camera, which keeps its glTF projection.

### Imported cameras

Use `scene.camera = model.camera(key)` to select an imported camera. The key is the name or glTF
index of the node that carries the camera. `model.cameras` lists all imported cameras in glTF
node order. Repeated calls return the same camera. Camera position, transform, and look-at inputs use world space. Imported cameras retain
their node hierarchy. Use `model.node(key)` for local transforms and ordinary glTF node channels
for camera motion.

Closing a model closes its imported cameras. Scenes that use one of them have no camera after
that, and their next `render()` raises `FillyError` that says so.

Imported cameras keep their glTF projection; `set_perspective()`, `set_lens_projection()`, and
`set_orthographic()` raise `FillyError` for them. A perspective camera without `aspectRatio`
follows the render target, as the glTF specification requires; an authored aspect ratio is kept.
Imported perspective cameras support an omitted far plane. Imported orthographic bounds are
`[-xmag, xmag]` and `[-ymag, ymag]`, and their near plane can be zero.

## Model

- `bounds`: minimum and maximum corners of the rest pose, shape `(2, 3)`, in the model's frame: `model.transform` does not apply, and glTF node transforms apply as loaded. A skinned mesh is skinned with the rest transforms of its joints, vertex by vertex; its own node transform does not apply, as the glTF specification requires. Other meshes use their accessor bounds. Each morph target counts at weight one. Computed once at load; not updated for node edits, deformation, or animation. For a model without geometry, each minimum is greater than its maximum. Skinned renderables get the same rest-pose box, in their node's frame, for culling and shadow fitting; animation that moves vertices far outside it can cull them.
- `transform`: affine 4 by 4 matrix applied to the model root.
- `position`: translation of the model root.
- `visible`: add or remove the model's entities from its scene.
- `scale`: three nonzero scale factors.
- `rotation_euler_deg`: rotations in degrees, applied about X, then Y, then Z.
- `quaternion`: `(x, y, z, w)`. The setter normalizes nonzero inputs.
- `nodes`: all nodes in glTF order.
- `node(key)`: return a node by name or glTF node index.
- `node_names`: names of the named nodes, in glTF order.
- `material(name)`: return the model's shared glTF material instance with this name.
- `material_names`: list of material names.
- `light(key)`: return the punctual light that a node carries.
- `lights`: all imported punctual lights, in glTF node order.
- `camera(key)`: return the camera that a node carries.
- `cameras`: all imported cameras, in glTF node order.
- `animations`: list of `AnimationInfo` objects with `name`, `duration` in seconds, and
  `material_properties`: the `(material_name, parameter)` pairs that the clip animates through
  `KHR_animation_pointer`, for example `("Body", "baseColorFactor")`. The material name is the
  one that `material(name)` accepts. A texture transform is the parameter of its matrix, for
  example `"baseColorUvMatrix"`. Applying or resetting any clip restores these parameters (see
  [Property animation](#property-animation)), so an edit to one of them must be written again
  after the clip.
- `apply_animation(key, time, *, loop=True)`: evaluate a clip at an explicit nonnegative time.
- `reset_animation()`: restore authored node transforms, morph weights, camera projections, and animated material/light/texture properties.
- `variants`: list of material variant names.
- `apply_variant(key)`: apply a material variant.
- `memory`: an `AssetMemory` estimate of the memory that the model's asset holds. See
  [Memory](#memory).
- `update_mesh(*, positions=None, normals=None, uvs=None, colors=None)`: replace vertex data of a
  [generated mesh](#generated-meshes).
- `close()`: remove the model from its scene and release its material copies. Repeated calls are valid.
- `closed`: report whether the model, its scene, or its renderer is closed.

### Keys

Every lookup accepts a name (`str`) or an index (`int`):

An integer key is always an index into the glTF array that the item comes from, and a name key
is the name of the same object. Nodes place lights and cameras, so their keys are node keys.

| Lookup | Name | Index |
| --- | --- | --- |
| `node(key)` | glTF node name | glTF node index |
| `light(key)` | name of the node that carries the light | glTF index of that node |
| `camera(key)` | name of the node that carries the camera | glTF index of that node |
| `apply_animation(key, ...)` | clip name | glTF animation index, also the position in `animations` |
| `apply_variant(key)` | variant name | glTF variant index, also the position in `variants` |

An unknown name or an index out of range raises `AssetError`. For an unknown name, the message
lists the names of that kind in the model (up to 20), for example
`Unknown animation 'Run'; the model has: 'Armature|HorseSpring'`. By name, `light()` and
`camera()` consider only the nodes that carry a light or a camera, and the message lists those.
A node index that carries no light or no camera also raises `AssetError`. A name that several
entries share raises `AssetError`, and the message lists the matching indices. `material(name)`
reports an unknown name in the same way. Use an index for unnamed or duplicated entries. Other key types, including
`bool` and `float`, raise `TypeError`.

Names are the names in the glTF document. A node without a name has the name `None`, even if its
mesh, light, or camera has a name. To find an unnamed node, use one of these:

- Its glTF node index: `model.node(3)`.
- The node tree: `model.node("Body").children` or `node.parent`.
- Its mesh name: `[n for n in model.nodes if n.mesh_name == "Helmet"]`.
- The item that it carries: `model.lights[0].node` or `model.cameras[0].node`.

`model.material(name)` edits the material that every mesh slot using it shares. Separate loaded
models, also models of one asset, have separate material instances. To change one mesh slot only, use
[`node.material(slot)`](#node-and-material).

### Assets

`renderer.load_asset(source, *, strict=False)` loads a glTF or GLB file, or `bytes`, and returns
an `Asset`. It takes the same sources and gives the same warnings as `scene.load()`. The asset
belongs to the renderer, so any of its scenes can show it: `scene.add(asset)` returns a new
`Model` of it in that scene. `scene.load(source)` is the same as loading an asset, adding it
once, and closing the asset.

```python
asset = renderer.load_asset("horse.glb")
left, right = scene.add(asset), scene.add(asset)
other_scene.add(asset)
```

| Member | Behavior |
| --- | --- |
| `memory` | An `AssetMemory` estimate of the memory that the asset holds, shared with its models. See [Memory](#memory). |
| `close()` | Release the source data that `scene.add()` reads. Models that exist keep rendering. Repeated calls are valid. |
| `closed` | Report whether the asset or its renderer is closed. |

Each model shares the asset's geometry, textures, and materials. It has its own entities,
transforms, material instances, node-local materials, visibility, light components, skinning,
morph target buffers, and animation state. It starts from the file's authored state: edits to
another model of the asset, including runtime textures, do not carry over. Each model creates
new material instances; it shares the decoded textures, including those of the custom
diffuse-transmission, anisotropy, and iridescence materials. Models with lights add their light
components to `stats.live_lights`. Add models before the frame loop: on the test laptop (Intel
Iris Xe), an `add()` took 0.1 to 8 ms where a load took 1 to 150 ms, for files of 0.07 to 7.4 MB.

Closing a model removes only that model from its scene; closing a scene closes only its own
models. The shared asset stays in memory until the asset and its last model close. Filament
needs the asset's source data to create models, so an open asset keeps it in CPU memory. This
costs about the size of the file: 3.7 MiB for the 3.6 MiB DamagedHelmet. `close()` releases it;
`scene.load()` releases it after its one model. After `close()`, `scene.add()` and `memory`
raise `FillyError`.

gltfio fills a model's morph target buffers only for the first model of an asset. filly fills
them for each later model in the same way: positions from the morph targets, and tangent frames
from gltfio's own tangent generation, for the same targets. This uses gltfio's `TangentsJob`,
which the Filament SDK library contains but does not declare in its headers; `native/renderer.cpp`
declares it for Filament 1.77.1.

### Memory

`asset.memory` and `model.memory` return an `AssetMemory` estimate of the memory that an asset
holds. The asset and its models share it, and the last of them to close frees it. Use it to keep
a model cache within a memory budget.

| Field | Meaning |
| --- | --- |
| `gpu_texture_bytes` | Every mip level of every texture of the asset, in the internal format that the texture has (for example, `SRGB8_A8` for a PNG, a compressed format for a KTX2 texture). |
| `gpu_geometry_bytes` | Vertex, index, morph target, and bone buffers, as gltfio lays them out. |
| `gpu_bytes` | The sum of the two. |
| `cpu_bytes` | The source data that `scene.add()` needs, while the `Asset` is open: the document, its buffers, and filly's tables for custom materials and property animation. Also the clip data that each instance's animator copies. A generated mesh adds its vertex arrays and staging buffers. |
| `instance_gpu_bytes`, `instance_cpu_bytes` | What each model adds: its bone and morph target buffers, and its animator's copy of the clips. These stay until the asset is freed (its `Asset` handle closed and its last model closed), also after the model closes. `asset.close()` releases only the source data. A loaded asset holds one instance before its first model, which that model uses. |
| `models` | The live models that share the asset. |

Models share the textures, the vertex and index buffers, and the materials. The
estimate leaves out what the driver adds. On the test laptop (Intel Iris Xe, an integrated GPU),
the GPU process memory counter measured 9 to 16% above `gpu_bytes` for the second load of an
asset: 18.6 against 16.1 MiB (wooden horse, 2.1 MiB file), 45.0 against 40.6 MiB
(DiffuseTransmissionPlant, 5.5 MiB), and 78.8 against 72.2 MiB (ChronographWatch, 7.1 MiB). The
driver allocates in blocks, so single models can add 0.5 to 3 MiB more than `instance_gpu_bytes`.
An integrated GPU uses system memory: there the process private bytes include the GPU memory,
about 22, 56, and 94 MiB for the same loads, against `cpu_bytes + gpu_bytes` of 18.2, 46.8, and
79.4 MiB. For a budget on such a machine, count `cpu_bytes + gpu_bytes` against system memory. A
discrete GPU holds `gpu_bytes` in its own memory instead. For an animated asset with many
keyframes, `instance_cpu_bytes` is the larger part of a model: 5.5 MiB estimated against about
7.4 MiB measured per model of BrainStem.

Closing the last model frees the asset to the driver and to the process heap, which reuse the
memory for later loads. The process counters do not always fall: after closing the horse, the
private bytes fell by about 2 MiB of the 22 MiB that its load added. Budget a cache by the sum of
its entries, not by the process counters.

Reading `memory` issues no GPU or engine commands.

Closing a model invalidates all its node, material, light, and imported camera handles. Scenes
that use one of its cameras lose their active camera. Other assets, renderer cameras,
and environment lighting remain available. Model and light close queue destruction without a
GPU completion wait. Call `renderer.finish()` when completion is required before the next trial.
Archive entry materials remain loaded until the renderer closes.

### Generated meshes

`scene.create_mesh(positions, indices, *, normals=None, uvs=None, colors=None, ...)` returns a
`Model` with one node and one material, both named `"mesh"`.

| Array | Shape | Notes |
| --- | --- | --- |
| `positions` | N x 3 | Scene units. At least 3 vertices. |
| `indices` | M x 3, `uint32` | Triangles, counterclockwise front faces, as in glTF. |
| `normals` | N x 3 | Optional. Normalized on input. |
| `uvs` | N x 2 | Optional. (0, 0) is the top-left corner of a texture, as in glTF. Default: zeros. |
| `colors` | N x 4 | Optional linear RGBA vertex colors, multiplied with the base color. |

Vertex arrays are `float32`; other float types are converted with a copy. All values must be
finite. Without `normals`, each vertex gets the area-weighted mean of the normals of its
triangles, so vertices that triangles share are smooth, and a vertex that only one face uses
takes that face's normal. For flat faces, give each face its own vertices, as `shapes.box()`
does. Tangent frames come from Filament's `SurfaceOrientation`: with UVs by Lengyel's method,
so normal maps and anisotropy have a defined tangent, and without UVs from the normal alone.

The material is the glTF metallic-roughness material that the loader makes for the same
factors, through the same material provider, so a generated mesh and a glTF mesh with equal
arrays and factors render the same: measured difference at most one 8-bit level on under 2% of
the pixels, from the different tangent code. `base_color`, `metallic`, and `roughness` are in
`[0, 1]`; `emissive` is nonnegative linear RGB. The `metallic` default is 0, not the glTF
default 1. `unlit`, `double_sided`, and `alpha_mode` (`"opaque"`, `"mask"`, or `"blend"`) are
fixed at creation and cannot change later. The other factors are
`model.material("mesh")` properties. A `"mask"` material cannot be created while
`output_path` is `"direct"`; see [output path](#output-path).

`model.update_mesh(*, positions=None, normals=None, uvs=None, colors=None)` replaces vertex data
in place. Each array must have N rows. Give at least one. The update:

- recomputes normals from new positions if the mesh was created without normals, and raises
  `ValueError` for `normals` then;
- recomputes tangent frames when positions, normals, or UVs change, with a copy of
  `SurfaceOrientation`'s method that gives the same frames without its allocations;
- updates the culling bounds; `model.bounds` keeps the creation-time bounds;
- raises `ValueError` for `colors` on a mesh created without colors.

The update copies the arrays into a ring of three staging buffers per attribute, so the
caller's arrays are free again when it returns. It allocates nothing after the first three
updates of an attribute. The new vertices appear at the next `render()`. `update_mesh()` blocks
only if the driver thread has not yet read the buffer from three updates earlier. Measured CPU
time per position update on the tested machine: 0.15 ms for 1,089 vertices and 6.9 ms for
66,049 vertices, most of it the tangent frames.

A generated mesh is one model in one scene; it has no `Asset`. For more copies, call
`create_mesh()` again: each copy has its own vertex buffers, which `update_mesh()` changes
independently.

`filly.shapes` returns arrays for `create_mesh(**shape)`:

| Function | Shape |
| --- | --- |
| `plane(width=1, height=1, *, segments=(1, 1))` | Rectangle in the XY plane facing +Z; `segments` is (columns, rows). |
| `box(width=1, height=1, depth=1)` | Axis-aligned box; each face has its own vertices and full UVs. |
| `uv_sphere(radius=0.5, *, segments=32, rings=16)` | Sphere with poles on Y; U from +Z around, V from the top pole. |
| `cylinder(radius=0.5, height=1, *, segments=32, caps=True)` | Cylinder on the Y axis; caps map a disc onto the UV square. |

Each returns a dict with `positions`, `normals`, `uvs` (`float32`), and `indices` (`uint32`),
centered on the origin. The functions are native (`filly._native.shapes`); `segments` and `rings`
must be integers.

### Animation

Animation evaluation first restores the authored values of every node translation, rotation,
scale, and morph weight that any clip of the model animates, and of every animated property.
It then applies the selected clip. This makes clip changes and arbitrary time jumps independent
of prior calls. It replaces manual edits of the nodes that a clip animates; apply those edits
after animation. Edits of other nodes stay. `reset_animation()` restores all nodes. Bone
matrices follow the edits at the next render. The model's outer root transform
is preserved. Looping wraps at the clip duration. With `loop=False`, times beyond the duration
hold the final pose. Node transforms, skinning, and morph animation use Filament's animator.
Supported light, material, camera, and texture properties also animate through `KHR_animation_pointer`.
No internal clock advances animations, and clip blending is not exposed.

### Property animation

`apply_animation()` and `reset_animation()` also handle this subset of
[KHR_animation_pointer](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_animation_pointer).
No extra call is required. Ordinary node animation and property channels can share a clip.
Clip names, indices, and durations follow the original asset, including property-only clips.

| Pointer prefix | Supported property suffixes |
| --- | --- |
| `/materials/{i}/pbrMetallicRoughness/` | `baseColorFactor`, `metallicFactor`, `roughnessFactor` |
| `/materials/{i}/` | `emissiveFactor`, `normalTexture/scale`, `occlusionTexture/strength` |
| `/materials/{i}/extensions/KHR_materials_emissive_strength/` | `emissiveStrength` |
| `/materials/{i}/extensions/KHR_materials_clearcoat/` | `clearcoatFactor`, `clearcoatRoughnessFactor`, `clearcoatNormalTexture/scale` |
| `/materials/{i}/extensions/KHR_materials_ior/` | `ior` (at least 1) |
| `/materials/{i}/extensions/KHR_materials_sheen/` | `sheenColorFactor`, `sheenRoughnessFactor` |
| `/materials/{i}/extensions/KHR_materials_specular/` | `specularFactor`, `specularColorFactor` |
| `/materials/{i}/extensions/KHR_materials_transmission/` | `transmissionFactor` |
| `/materials/{i}/extensions/KHR_materials_volume/` | `thicknessFactor` |
| `/materials/{i}/extensions/KHR_materials_dispersion/` | `dispersion` |
| `/materials/{i}/extensions/KHR_materials_diffuse_transmission/` | `diffuseTransmissionFactor`, `diffuseTransmissionColorFactor` |
| `/materials/{i}/extensions/KHR_materials_anisotropy/` | `anisotropyStrength`, `anisotropyRotation` |
| `/materials/{i}/extensions/KHR_materials_iridescence/` | `iridescenceFactor`, `iridescenceIor`, `iridescenceThicknessMinimum`, `iridescenceThicknessMaximum` |
| `/extensions/KHR_lights_punctual/lights/{i}/` | `color`, `intensity`, `range`, `spot/innerConeAngle`, `spot/outerConeAngle` |
| `/nodes/{i}/` | `translation`, `rotation`, `scale`, `weights` |
| `/nodes/{i}/extensions/KHR_node_visibility/` | `visible` (STEP, unnormalized unsigned-byte output) |
| `/cameras/{i}/perspective/` | `yfov` (radians), `aspectRatio`, `znear`, `zfar` |
| `/cameras/{i}/orthographic/` | `xmag`, `ymag`, `znear`, `zfar` |
| `/materials/{i}/{texture-info-path}/extensions/KHR_texture_transform/` | `offset`, `rotation` (radians), `scale` |

Texture paths cover base color, metallic-roughness, normal, occlusion, emissive, clearcoat,
sheen, transmission, volume thickness, specular, anisotropy, iridescence, and diffuse transmission.
The texture info and its `KHR_texture_transform` object must exist. Offset and scale use VEC2
outputs; rotation uses SCALAR. Channels for the same UV transform are combined before its matrix
is set. `texCoord` is not animatable. Camera channels are also combined before projection updates;
invalid combined clip planes raise `AssetError`. Perspective `zfar` can animate only when authored.

Light range requires an authored positive range on a point or spot light. Spot cone channels use
radians and require a spot light. They are combined before validation: `0 <= inner < outer <= pi/2`.
An invalid combined cone raises `AssetError`. Changing a cone preserves intensity in candela.
Node pointers use the same native animator as ordinary transform and morph-weight channels.

The enclosing object must exist. Missing properties with glTF defaults can be animated.
Targets use glTF indices, so duplicate names are safe. All native instances of a glTF material
or light, and all nodes that use a camera definition, receive the update. Node-local copies of a
material also receive it; see [node-local materials](#node-local-materials). Closed lights stay
closed when an animation is applied or reset.

Interpolation supports `STEP`, `LINEAR`, and `CUBICSPLINE`, with time-scaled cubic tangents.
Keys use seconds. Values before the first key and after the last key hold the nearest endpoint.
Non-looping playback includes the final property key exactly. Cubic results are clamped to the
property's valid range. Normalized integer outputs are converted before interpolation.
Loading supports dense, strided, and sparse accessors from embedded, data-URI, or local buffers.
Keyframe decoding and target lookup occur during loading; native evaluation allocates no keyframe arrays.

Applying any clip resets imported camera projections and all animated material/light/texture
properties to their load-time state first,
along with authored node transforms and morph weights. Manual edits to these properties must
follow animation evaluation. If a punctual light has no authored range, its finite Filament
range approximation follows animated intensity, including lights initially turned off.

Individual vector components, volume attenuation, alpha cutoff, the special IOR value zero, and
other unlisted properties remain unsupported. Unsupported pointer channels warn and are skipped; they
raise `AssetError` with `strict=True` or when the extension is required. Invalid keyframes
and unavailable native material parameters raise
`AssetError`. This is partial extension coverage, not a full conformance claim.

Matrices use column vectors. Translation is in `matrix[:3, 3]`.
Inputs accept NumPy `float32` and `float64` arrays. Native storage is `float32`.
Getters return independent arrays. The original glTF hierarchy is retained.
Rotation and scale properties reject sheared or degenerate matrices. Use `transform` directly
when such a matrix is required.

## Node and material

A `Node` has the same transform, position, scale, quaternion, and Euler properties as a model.
Its transform is relative to its original glTF parent.

| Member | Behavior |
| --- | --- |
| `name` | glTF node name, or `None`. Read-only. |
| `mesh_name` | Name of the node's glTF mesh, or `None`. Read-only. |
| `index` | glTF node index. Read-only. |
| `parent` | Parent `Node`, or `None` for a top-level node. Read-only. |
| `children` | Child nodes in glTF order. Read-only. |
| `bounds` | Rest-pose minimum and maximum corners of the node and its descendants, with the meaning of [`Model.bounds`](#model): in the model's frame, not changed by node edits or animation. Load computes one box per mesh node; each read joins the boxes of the subtree. Each minimum is greater than its maximum when no mesh is below the node. Read-only. |
| `material(slot=0)` | Return this node slot's own material. |
| `morph_target_count` | Number of morph targets. |
| `set_morph_weights(weights)` | Set one finite weight per target. Weights are not restricted to `[0,1]`. |

Nodes support `==` and `hash()`. `EXT_mesh_gpu_instancing` children, which loading adds, have
indices after the authored nodes and no name. Each child has the mesh, so its `mesh_name` is the
mesh name; the instancing node itself has no mesh. Use `children` of the instancing node to reach
one instance.

Moving a node of a skinned model marks its bone matrices for update. The next `render()` updates
them once. Frames without node edits do no bone work.

### Node-local materials

`node.material(slot)` returns a material that belongs to that node slot only. The first call
copies the slot's current material instance and assigns the copy to the slot. Later calls return
the same copy. A node without a mesh, or an invalid slot, raises `AssetError`. Textures remain
shared; factor edits apply to this slot only. The copy stays alive until the model closes. Create
node-local materials during trial setup, because the first call allocates.

Material animation reaches node-local copies. The rule applies to each material property that a
clip of the model animates through `KHR_animation_pointer`, including texture transforms:

- `apply_animation()` first restores all animated properties to their rest values. It then writes
  the clip's values to the shared material and to every node-local copy of that glTF material.
  An edit to an animated property of a copy therefore lasts until the next `apply_animation()`.
- `reset_animation()` restores the animated properties of the copies to the rest values of the
  shared material. Earlier edits of the copies are not restored.
- Edits to properties that no clip animates persist on the copy.

Models of one asset follow the same rule, each with its own materials and copies.

Rule for material variants: `apply_variant()` puts the variant's material in each slot that the
variant maps. If such a slot has a node-local material, the node shows the variant: a new copy
of the variant's material replaces the old copy, and edits to the old copy are lost. Existing
handles refer to the new copy. Slots that the variant does not map keep their node-local material
and its edits.

### Material

A `Material` exposes `base_color` as linear RGBA factors and `metallic` and `roughness` as scalar
factors. Values must be finite and in `[0, 1]`. `emissive` is the effective linear RGB emission,
nonnegative: the glTF emissive factor times the emissive strength. Setting it sets the factor and
resets the strength to 1. Changing a factor does not change the glTF alpha mode.
Unsupported material parameters raise `AssetError`, for example `emissive` on an unlit material.

### Runtime textures

| Member | Behavior |
| --- | --- |
| `base_color_texture` | Get or set a `Texture`, an `ImportedTexture`, or `None`. The texture multiplies `base_color`. |
| `emissive_texture` | Get or set a `Texture`, an `ImportedTexture`, or `None`. The texture multiplies `emissive`. Lit materials only. |
| `base_color_texture_offset`, `base_color_texture_scale`, `base_color_texture_rotation_deg` | Get or set the `KHR_texture_transform` of the runtime base color texture: offset and scale in UV units, rotation in degrees. Defaults: `(0, 0)`, `(1, 1)`, `0`. Each property changes only its own value. `None` without a runtime texture in the slot, where setting raises `FillyError`. |
| `emissive_texture_offset`, `emissive_texture_scale`, `emissive_texture_rotation_deg` | The same for the emissive texture. |

The getters return the texture that was assigned at run time, or `None`; they do not return
textures from the glTF file. `None` removes the assignment and resets the slot's transform. A
texture that replaces another keeps the transform. Textures sample UV set 0; a mesh
without `TEXCOORD_0` samples texel (0, 0) everywhere. Emissive values are output values: with
`emissive = (1, 1, 1)`, an emissive texel of linear 0.25 adds 0.25 to the output, whatever the
exposure.

Both `model.material(name)` and `node.material(slot)` handles support textures. On a shared
material, every mesh slot that shows it gets the texture; slots with a node-local material keep
their own. A node-local material made from a textured shared material starts with its texture.

Every material has both slots, including diffuse-transmission, anisotropy, and iridescence
materials and materials without glTF textures. An assignment changes parameters of a copy of the
glTF instance: nothing is compiled, other glTF textures stay, and the texture transform
properties always work. Removing a texture starts again from a copy of the glTF instance, which has the
glTF texture of that slot, and the factors carry over. Property animation of the glTF material
also reaches the textured material, and `apply_variant()` keeps runtime textures on shared
materials; on node-local materials that the variant maps, they are lost, as other edits are.

A texture can serve many materials. Closing it removes it from every material that uses it.

Cache node and material handles before the frame loop to avoid repeated name lookups.

## Texture

`renderer.create_texture(pixels, *, color_space, mipmaps=False, filter="linear", wrap="repeat")`
returns a `Texture`.

`pixels` is a C-contiguous NumPy array of shape (H, W), (H, W, 1), (H, W, 3), or (H, W, 4),
`uint8` or `float32`. H and W are 1 through 8192. The first row is the top of the image, as in
`target.read()`. Other dtypes raise `TypeError`; the texture never converts silently.

| Channels | Meaning |
| --- | --- |
| 1 | Grey: sampled as (v, v, v, 1). |
| 3 | RGB, alpha one. |
| 4 | RGBA with straight alpha. Alpha has an effect only in `"blend"` or `"mask"` materials. |

`color_space` is required:

- `"srgb"`: `uint8` values are sRGB-encoded color, as in PNG images. Sampling decodes them to
  linear light.
- `"linear"`: values are linear: `uint8` v means v / 255, and `float32` values are used as is.
  Use it for data and for stimuli defined in linear light.

`float32` textures must be `"linear"`. They are stored as 16-bit floats, which keep 11
significant bits: finer than 8-bit output, at half the memory. 1-channel sRGB data is stored as
RGBA, because GPUs have no 1-channel sRGB format.

`mipmaps=True` stores a full mipmap chain and regenerates it on the GPU after each update, so a
minified pattern averages instead of aliasing. `filter` is `"linear"` or `"nearest"`;
`"nearest"` shows single texels, for example noise at one texel per pixel. `wrap` is `"repeat"`,
`"clamp"`, or `"mirror"`, for both axes.

| Member | Behavior |
| --- | --- |
| `width`, `height`, `channels` | Size. Read-only. |
| `dtype` | `"uint8"` or `"float32"`. Read-only. |
| `color_space`, `mipmaps` | Settings from creation. Read-only. |
| `update(pixels)` | Replace all pixels. Same shape and dtype as at creation, else `ValueError`. |
| `close()` | Release the texture and remove it from all materials. Repeated calls are valid. |
| `closed` | Report whether the texture or its renderer is closed. |

`update()` copies the array into one of three staging buffers and queues the upload; the array
is free again when it returns. Filament reads a staging buffer on its driver thread and releases
it at a later flush, so the ring lets the caller run two uploads ahead of the driver thread.
`update()` waits only when all three buffers are still queued, for example after many updates
without a `render()`. There is no added latency: the next `render()` samples the new pixels. The
first update allocates the staging buffers (up to three times the image size in CPU memory);
later updates allocate nothing in the renderer. Filament 1.77.1 allocates one small callback
record per upload on its driver thread. The first upload at creation uses a buffer that is freed
after the upload, so a texture that never changes keeps no staging memory.

Measured on the tested machine (Intel Iris Xe, `tools/benchmark_uploads.py`, medians). CPU is
the `update()` call. Completion is the extra time until `finish()` returns after
`update()` and a render, which includes the driver thread and the GPU transfer:

| Size | `uint8` RGBA CPU | Completion | `float32` RGBA CPU | Completion |
| --- | ---: | ---: | ---: | ---: |
| 256 x 256 | 0.03 ms | 0.13 ms | 0.11 ms | 0.47 ms |
| 1024 x 1024 | 0.40 ms | 1.7 ms | 1.6 ms | 4.6 ms |
| 1920 x 1080 | 0.82 ms | 2.7 ms | 3.2 ms | 9.1 ms |

The completion time overlaps with the host's own work unless the host waits. Prefer `uint8`
for large per-frame stimuli. A drifting grating needs no upload at all: set
`base_color_texture_offset`.

Textures support `==` and `hash()`. Closing the renderer closes its textures.

## ImportedTexture

`renderer.import_gl_texture(texture_id, *, width, height, color_space, filter="linear", wrap="repeat")`
returns an `ImportedTexture`: a host OpenGL texture that materials sample, for zero-copy video or
host-drawn patterns on a 3D surface. The texture must be a `GL_TEXTURE_2D` with `GL_RGBA8`
storage in the shared context. For `color_space="srgb"`, Filament samples a `GL_SRGB8_ALPHA8`
view of it, so shaders read linear light. A view needs immutable storage from `glTexStorage2D`;
mutable storage from `glTexImage2D` raises `InteropError` for `"srgb"`. For `"linear"`, Filament
samples the texture itself, and either storage works. Filament samples level 0 only.

The host writes it inside `write()`:

```python
imported_texture = renderer.import_gl_texture(texture_id, width=256, height=256, color_space="srgb")
with imported_texture.write():
    ...  # Draw into the texture in the host context.
renderer.render(scene, target)  # Samples what the host drew.
```

| Member | Behavior |
| --- | --- |
| `write()` | Return a context manager for host writes. The host context must be current. |
| `writing` | `True` inside `write()`. Read-only. |
| `width`, `height`, `color_space` | Read-only. |
| `close()` | Remove the texture from all materials and release the Filament side. The host texture is not deleted. |
| `closed` | Report whether the texture or its renderer is closed. |

Imported textures support `==` and `hash()`.

The order is explicit in both directions and has no CPU wait for the GPU. Entering `write()`
waits until Filament's driver thread has placed a fence after the last frame, as `acquire()`
does, and queues a GPU wait on it in the host context, so the host does not overwrite pixels
that a submitted frame still reads. It releases the GIL while it waits. Leaving `write()`
places a host fence; the next `render()` of a scene whose models sample the texture queues a GPU
wait on it before the frame. `render()` inside `write()` raises `InteropError`. A render adds one
fence, shared by its target and by the imported textures that the scene samples.
`close()` fences the host's work if the host context is current, as `ImportedTarget.close()`
does.

## RenderTarget

`create_render_target()` returns a `RenderTarget`.

| Member | Behavior |
| --- | --- |
| `width`, `height` | Size in pixels. Read-only. |
| `read()` | Return a contiguous `(height, width, 4)` `uint8` RGBA array. The first row is the top of the image. Call `render()` at least once first. |
| `close()` | Release the target. Repeated calls are valid. |
| `closed` | Report whether the target or its renderer is closed. |

Readback waits for the GPU. It releases the GIL while it waits.

## ImportedTarget

`import_gl_target()` returns an `ImportedTarget`. This is the API for authors of host adapters:
the modules in `filly.integrations` are built on it. The host creates a `GL_TEXTURE_2D` with
`GL_RGBA8` storage in the context given as `shared_context`. The host samples its own texture as
plain RGBA8 values, which are sRGB-encoded with the default `encoding`.

The storage kind matters only for [`output_path = "direct"`](#output-path) with sRGB encoding:

- Immutable storage, from `glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, width, height)`: the
  import creates a `GL_SRGB8_ALPHA8` texture view of the same storage (OpenGL 4.3 or
  `ARB_texture_view`). Filament renders into the view, so the GPU can encode sRGB on write.
  `close()` deletes the view if the host context is current; otherwise the view goes away with
  the context. Both output paths work.
- Mutable storage, from `glTexImage2D`: Filament renders into the texture itself. The exact
  path works. A render with `output_path = "direct"` and `encoding = "srgb"` raises
  `InteropError`, because the GPU encodes only into sRGB storage.

| Member | Behavior |
| --- | --- |
| `ImportedTarget(imported)` | For subclasses, such as host adapters: initialize the new object from the `ImportedTarget` that `import_gl_target()` returned. Both objects refer to the same target. |
| `width`, `height` | Size in pixels. Read-only. |
| `acquire()` | Return a context manager that holds the texture for host sampling. `with target.acquire() as t` gives `t is target`. Nested calls only count depth. |
| `acquired` | `True` inside `acquire()`. Read-only. |
| `close()` | Release the Filament side. The host texture is not deleted. Repeated calls are valid. |
| `closed` | Report whether the target or its renderer is closed. |

`on_renderer_close` lets an adapter release the host objects around the texture, which the
renderer does not know about. `Renderer.close()` calls the callback without arguments before it
releases anything else, so the host context can still be made current, and newer targets come
first. A target that closes first drops its callback. The renderer keeps a strong reference to
the callback, so the callback must not refer to the target strongly: use `weakref`. If a
callback raises, `Renderer.close()` still calls the other callbacks and releases every resource,
then raises the first exception. The host integrations use the callback to close their targets.

Use this sequence for every frame, with the original host context current:

```python
renderer.render(scene, target)
with target.acquire():
    ...  # Bind and sample the texture in the host context.
```

The outermost `acquire()` waits for the driver to publish a GL fence and queues a GPU wait in
the host context. It releases the GIL during the CPU wait, and it does not wait for GPU
completion on the CPU. On exit, the outermost `acquire()` inserts and flushes a host fence; the
next render queues a GPU wait on that fence before it overwrites the texture.

- `acquire()` before the first render raises `InteropError`.
- `acquire()` after an earlier release, without a new render, is valid: the texture still holds
  the last frame and the host already waited for it.
- `render()` while the target is acquired raises `InteropError`.
- `render()` again before the host acquires the frame is valid; the unsampled frame is replaced.
- An imported target has no `read()`. Read the host texture in the host context instead.

The host texture must remain alive until the target closes, or until the host context is gone.
`close()` and `Renderer.close()` fence the host's GPU work when the host context is current. If
another context is current, or none is, they skip the host fence. Filament then waits only for
its own work before it releases resources. Host work that still samples a shared texture is not
fenced. Use this after a host window has closed.

`render()`, the outermost `acquire()` enter and exit, native asset loading, environment
filtering, `finish()`, `close()` of `Renderer`, `Scene`, `RenderTarget`, `ImportedTarget`, and
`ImportedTexture`, `Texture.update()`, texture assignment, mesh creation and updates, and entering
`ImportedTexture.write()` release the GIL. Other Python threads can run during these
calls. Native asset loading includes the glTF checks and preparation. Normal rendering does not
perform a full GPU completion wait.

## Host integrations

Each module in `filly.integrations` has a `HostTarget` class and a `create_renderer()`
function. `HostTarget` creates a host-owned texture with immutable RGBA8 storage, so both output
paths work, imports it with `import_gl_target()`, and is an `ImportedTarget`.
`create_renderer()` returns a `Renderer` that shares the window's OpenGL context.

| Module | Constructor | Default size | `texture` | Renderer factory |
| --- | --- | --- | --- | --- |
| `pyglet` | `HostTarget(renderer, window, *, width=None, height=None)` | `window.get_framebuffer_size()` | GL texture name (`int`) | `create_renderer(window)` |
| `moderngl` | `HostTarget(renderer, ctx, *, width=None, height=None)` | `ctx.fbo.size` | `moderngl.Texture` | `create_renderer()` |
| `zengl` | `HostTarget(renderer, ctx, *, width=None, height=None, framebuffer=None)` | size of `framebuffer[0]`; required for the window | `zengl.Image` | `create_renderer()` |
| `psychopy` | `HostTarget(renderer, win, *, width=None, height=None)` | `win.frameBufferSize` | GL texture name (`int`) | `create_renderer(win)` |

The pyglet and PsychoPy factories make the window's context current. moderngl and zengl cannot
make a context current, so their factories share the context that is current and raise
`InteropError` if none is. After `close()`, `texture` is `0` or `None`.

| Member | Behavior |
| --- | --- |
| `texture` | The host texture. See the table above. Read-only by convention. |
| `acquire()` | Make the host context current if no acquisition is open, then call `ImportedTarget.acquire()`. |
| `draw(*, viewport=None, blend=True)` | Draw the texture into `viewport`, `(x, y, width, height)` in window pixels from the lower-left corner. Outside `acquire()`, `draw()` acquires and releases the texture itself; inside one, it uses the open acquisition. The viewport defaults to the texture size at (0, 0). |
| `close()` | Close the Filament target, then delete the host objects. Repeated calls are valid. |
| `create_image_stim(*, size=None, pos=(0, 0), units="pix")` | PsychoPy only. Return a new ImageStim in the target's window that samples the texture. `size` is required unless `units` is `"pix"`; the default is the texture size in pixels. |

`draw()` composites the texture source-over for premultiplied color, which is what Filament
writes. `blend=False` copies the texture, so the window gets Filament's premultiplied RGBA.
The pyglet and moderngl adapters draw a quad in native code that restores the OpenGL state that
it changes; the depth test, the stencil test, and face culling do not apply, and the scissor
test does. The zengl adapter draws with a zengl pipeline for each mode. The PsychoPy adapter
draws with an ImageStim: blended draws convert the color to straight alpha for ImageStim's
blending and require the window's `blendMode="avg"`; copies disable blending for the draw.

`width` and `height` follow the rules for `import_gl_target()`, with the same error messages.
The adapter checks them before it creates the host texture.

`renderer.close()` closes the renderer's host targets first, as `close()` on each would, while
the window's context can still be made current. So only the window must close last: close the
renderer, or each target, before the window. If the window closes first, `close()` makes no host
OpenGL calls and releases the Filament target; `Renderer.close()` then also succeeds. The host
objects go away with the window's context.

A target that is garbage-collected without `close()` emits `ResourceWarning`. Collection runs at
arbitrary points, often while another window's context is current. It therefore never makes
another context current, and it deletes host objects only if the target's own context is
current. Otherwise the host objects stay until their window closes. The same rule applies to the
mask of a PsychoPy stimulus from `create_image_stim()`. The stimuli of a window share one shader
program.

## Sample model

`filly.samples.SUZANNE` is the path of `suzanne.glb`, a sample model installed with filly:
Blender's monkey head as one mesh with one untextured blue metal material (2,012 vertices, 73 KB).
It is in the public domain (CC0 1.0), so images rendered from it need no attribution. Its source
and changes are in `samples/ATTRIBUTION.md`. `tools/make_suzanne.py` makes it again from the
source.

```python
model = scene.load(filly.samples.SUZANNE)
```

Code that must not import filly's native module, such as a PsychoPy Builder component, finds the
file next to `importlib.util.find_spec("filly").origin`, in `samples/`.

## Exceptions

`FillyError` is the library exception base. `BackendError` and `AssetError` identify initialization
and asset failures. `InteropError` identifies invalid contexts, textures, or synchronization order.
Invalid numeric inputs, dimensions, formats, and option values raise `ValueError`. Wrong key types
raise `TypeError`. Calls after `close()` raise `FillyError`.

## Benchmark command

```powershell
uv run --no-sync python -m filly.benchmark stimulus.glb --frames 10000
```

The model should be near the origin and visible from `(0, 0, 3)`. Add `--output-path direct` to
measure the [direct output path](#output-path); the default is `exact`.
The command reports submission percentiles, a completion wait after the batch, average batch time
per frame, and one readback cost. Warmup frames are excluded.
It does not measure isolated GPU time, per-frame completion latency, or display deadlines.

An unthrottled loop, such as this command, logs "FrameInfo's circular queue is full" at warning
level on nearly every frame. Filament's `FrameInfoManager` keeps 16 frames of history and
releases an entry only after the driver thread has processed that frame. The CPU submits frames
more than 16 ahead of the driver thread. The warning does not affect rendering. The engine
feature `engine.frame_info.disable_gpu_complete_metric` removes only the GPU fence wait and does
not stop the warning; Filament has no other switch for it. A loop paced by the display or by a
1 ms sleep does not log it. Use `set_log_level("error")` to hide it.
