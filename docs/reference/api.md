# API reference

The Python package is `filly`. This page describes the implemented subset of the spec.

## Logging

`filly.set_log_level(level)` sets the minimum Filament log severity for all renderers in the
process, including their driver threads. Accepted levels are `"verbose"`, `"debug"`, `"info"`,
`"warning"`, `"error"`, and `"off"`. Invalid levels raise `ValueError`.

Call `set_log_level("warning")` before creating a renderer to suppress startup details while
retaining warnings and errors. Use `"verbose"` to restore all native log streams. If the function
is not called, Filament's default output is unchanged. This setting does not affect PsychoPy
logging, Python exceptions, or direct output that bypasses Filament's log streams.

## Renderer

`Renderer(*, shared_context=None, precompiled_shaders=False)` creates an OpenGL renderer.
`shared_context` accepts the current host WGL context on Windows or GLX context on Linux as an
integer. `current_gl_context()` returns that handle, or zero if no context is current. Passing
zero explicitly as `shared_context` is an error. On Linux, an offscreen renderer uses EGL or GLX;
see [Run on Linux](../how-to/build.md#run-on-linux). It needs no X display if EGL is available.

How glTF materials get their shaders depends on the material path of the build
(CMake `FILLY_MATERIALS`, see [Material path](../how-to/build.md#material-path)).
`filly._native._materials` reports it as `"runtime"` or `"archive"`.

On the **runtime** path (the default build), `precompiled_shaders` selects:

- `False` (default): compile each glTF material configuration during loading. This adds 0.1 to
  0.35 s for each asset with new configurations.
- `True`: use Filament's precompiled material archive where an archive entry matches exactly,
  and compile the other configurations. Loading is faster when the archive covers the asset.

Both settings render the same complete material, and both compile the custom
diffuse-transmission, anisotropy, iridescence, transmission, and volume materials. Both also
compile unlit `OPAQUE` materials, so that they store alpha one on the direct
[output path](#output-path): with `True`, the first unlit plane took 129 ms to load instead of
14 ms for a lit one. A renderer compiles each configuration once.
See [texture limits](#texture-limits) for the one case in which they differ. The material
compiler is in the wheel for both settings.

On the **archive** path, every glTF material is an instance of an entry of filly's precompiled
material archive. Nothing is compiled while loading, and `precompiled_shaders` has no effect. The
archive has 39 entries: core lit; lit with clearcoat, sheen, and iridescence, with and without
`KHR_materials_specular`; lit with anisotropy; thin and solid refraction with and without
`KHR_materials_specular` and with anisotropy; specular-glossiness; unlit; and diffuse
transmission, each opaque, masked, and blended. Only materials with `KHR_materials_specular` get
an entry with specular inputs: with them, Filament uses the specular extension's F90 instead of
its F90 from F0, which is lower for dark metals and low IOR. See
[material precompilation](../explanation/material-precompilation.md).

On both paths, the GPU driver compiles each GL program at its first draw, so render one warmup
frame after loading and before a timing-critical trial. The driver keeps compiled programs in its
own disk cache, so the first frames are much slower on a machine or driver that has not seen the
programs. With six sample assets on the test machine, the first frame of each took 1.3 s in
total on the archive path and 2.8 s on the runtime path with cold driver caches (Intel Iris Xe),
and 0.07 s and 0.25 s with warm caches. filly does not compile programs at renderer creation and
does not keep its own program binary cache: neither made the first frames faster in these
measurements. See
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
| `create_render_target(*, width, height, format="rgba8", depth=True)` | Return an `OffscreenTarget`. Dimensions must be 1 through 8192. |
| `import_gl_texture(texture_id, *, width, height, format="rgba8", depth=True)` | Return an `ImportedTarget` for a host-owned 2D texture with `GL_RGBA8` storage. Requires a shared context. See [imported targets](#importedtarget). |
| `render(scene, target, *, camera=None, viewport=None, clear=True)` | Submit one frame to an `OffscreenTarget` or `ImportedTarget` and flush it to the driver. See [render calls](#render-calls). Return `None`. |
| `create_texture(pixels, *, color_space, mipmaps=False, filter="linear", wrap="repeat")` | Return a [`Texture`](#texture) with the pixels of a NumPy array. |
| `import_gl_input(texture_id, *, width, height, color_space, filter="linear", wrap="repeat")` | Return a [`HostTexture`](#hosttexture) that materials sample. Requires a shared context. |
| `finish()` | Block until prior GPU work finishes. |
| `close()` | Release native resources. Repeated calls are valid. |
| `closed` | Report whether the engine is closed. |
| `precompiled_shaders` | The setting given at creation. Read-only. |
| `shared_context` | The host context handle given at creation, or `0` for an offscreen renderer. Read-only. |
| `gl_platform` | The OpenGL binding of the engine: `"wgl"`, `"glx"`, or `"egl"`. Read-only. |
| `stats` | Return a `Stats` snapshot of frame counters and CPU timings. |

Target `width` and `height` accept Python and NumPy integers. They also accept floats with an
integral value, such as `1024.0` or `win.size[0] / 2` for an even width. A fractional or
non-finite float raises `ValueError`. Other types, including `bool`, raise `TypeError`.

The renderer supports `with`. Use all objects on the thread that created the renderer.
An API call from another thread raises `FillyError`.
Close the renderer and release its children on the creating thread.

`Stats` contains the last measured CPU duration of each operation, in milliseconds:
`cpu_submit_ms`, `host_wait_ms`, `host_release_ms`, `finish_ms`, and `readback_ms`.
Values start at zero and change only when that operation completes. Submission includes the
incoming host-fence dependencies of the target and of host textures, bone updates, native
rendering, and command flush. It can include
backpressure. Host wait measures fence publication and queuing the GPU dependency in
`ImportedTarget.acquire()`. It is not GPU completion time. Readback includes native allocation
and transfer, but excludes conversion to a NumPy array.
`live_models`, `live_lights`, and `material_copies` count live native resources, including imported
lights and clones. Closed Python handles do not contribute. `material_copies` counts node-local
materials. These counts do not measure GPU memory or shader caches.
`frames_rendered` counts submissions. Statistics do not measure display latency or GPU time.

### Render calls

`render(scene, target, *, camera=None, viewport=None, clear=True)` renders one frame.

- `camera`: a `Camera` of the same renderer. It replaces `scene.camera` for this call only. A
  scene without a camera can render with one. Without `camera`, `scene.camera` is required.
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
| `create_camera()` | Create a `Camera`. |
| `camera` | Get or set the active camera. It must belong to the same renderer. |
| `load(source, *, strict=False, clonable=False)` | Load glTF or GLB from a path, `Path`, or `bytes`, and return a `Model`. GLB is detected by its `glTF` magic, not by the file name. Set `clonable=True` to allow [`model.clone()`](#clones). |
| `create_mesh(positions, indices, *, normals=None, uvs=None, colors=None, base_color=(1, 1, 1, 1), metallic=0, roughness=1, emissive=(0, 0, 0), unlit=False, double_sided=False, alpha_mode="opaque")` | Return a `Model` made from arrays. See [generated meshes](#generated-meshes). |
| `add_directional_light(*, direction, intensity=50000, color=(1, 1, 1))` | Return a `Light`. Intensity is in lux. |
| `add_sun_light(*, direction, intensity=100000, color=(1, 1, 1), angular_radius_deg=0.545, halo_size=10.0, halo_falloff=80.0)` | Return a sun `Light`: a directional light with a disc. Intensity is in lux. |
| `add_point_light(*, position, intensity=100, color=(1, 1, 1), range=10)` | Return a point `Light`. Intensity is in candela; range is in scene units. |
| `add_spot_light(*, position, direction, intensity=100, color=(1, 1, 1), range=10, inner=0.3, outer=0.6)` | Return a spot `Light`. Cone half-angles are in radians; intensity is in candela. |
| `background` | Get or set linear RGBA values in `[0, 1]`. Default: `(0, 0, 0, 1)`. |
| [Rendering options](#rendering-options) | `encoding`, `output_path`, `tone_mapping`, `antialiasing`, `msaa`, `shadows`, `refraction`, `transparent`, `dithering`, `ssao`, `bloom`, `fog`, `depth_of_field`, `vignette`, `set_fog_options()`, `set_vignette_options()`. |
| [Environment members](#environment-lighting) | `load_environment()`, `set_environment()`, `load_environment_ktx()`, `clear_environment()`, `environment_intensity`, `environment_visible`, `environment_rotation`. |
| `close()` | Release the scene's models, lights, cameras, environment, and view. Repeated calls are valid. |
| `closed` | Report whether the scene or its renderer is closed. |

Byte assets must contain all resources. Path assets can use local external buffers and images,
also in folders with non-ASCII names. PNG, JPEG, Basis Universal KTX2, and WebP
(`EXT_texture_webp`) textures are supported. The extension decodes WebP with libwebp 1.5.0.
Scenes retain their loaded models, cameras, and lights.

After `close()`, the scene's models, lights, and cameras report `closed=True` or raise
`FillyError` on use. A camera from the closed scene that is active in another scene is
detached from that scene. Rendering a closed scene, or any other call on it, raises
`FillyError`. Other scenes are not affected.

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
| `refraction` | `bool`. Set before loading glass assets. | `False` |
| `transparent` | `bool`. Keep alpha in the output. | `False` |
| `dithering` | `bool` | `False` |
| `ssao` | `bool` | `False` |
| `bloom` | `bool` | `False` |
| `fog` | `bool`. See [fog](#fog). | `False` |
| `depth_of_field` | `bool`. See [depth of field](#depth-of-field). | `False` |
| `vignette` | `bool`. See [vignette](#vignette). | `False` |

#### Output encoding

`encoding` sets how linear scene color is stored in the 8-bit target. It applies to offscreen
targets and to imported host textures, and no other option changes it.

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
| Set `output_path = "direct"` while `tone_mapping` is not `"linear"`, `antialiasing` is `"fxaa"`, `msaa` is above 1, or `refraction`, `transparent`, `dithering`, `ssao`, `bloom`, `depth_of_field`, or `vignette` is `True` | `ValueError` that names each setting. The path does not change. |
| Set one of these options while `output_path` is `"direct"` | `ValueError`. The option does not change. |
| Set `output_path = "direct"` while a model in the scene has a material with `alphaMode` `MASK` | `ValueError` |
| Load an asset or create a mesh with a `MASK` material while `output_path` is `"direct"` | `AssetError`. Nothing is loaded. |
| Render with `encoding = "srgb"` to an [imported target](#importedtarget) whose texture has mutable storage (`glTexImage2D`) | `InteropError`. Nothing is rendered. |

Filament writes the sharpened edge alpha of `MASK` materials to the target, and only the encode
pass stores alpha one. `fog`, `shadows`, `encoding`, lights, environments, and every other material
work on both paths. A scene that is not transparent stores alpha one on both paths.

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

#### Fog

`fog = True` blends each fragment toward a fog color by its distance from the camera:
`opacity = 1 - exp(-density * max(distance - start, 0))`. The distance is the length of the
camera-to-fragment vector, not the depth. Fog is uniform in height. The background color is
not fogged; a visible environment is infinitely far away and takes the fog color.

`set_fog_options(*, color=(1, 1, 1), density=0.1, start=0.0)` sets all three values.
`color` is the linear output color of a fully fogged fragment, in `[0, 1]`. Filament scales
the fog color by the environment intensity (30000 lux without an environment) and the camera
exposure. The renderer divides that scale out at each `render()`, for the camera of that call,
so the color does not depend on lighting or exposure. With an environment intensity of zero,
fog is black. `density` is per scene unit and `start` is in scene units; both must be
nonnegative. Fog is computed in the material shaders, so it works on both output paths.

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
runs before filly's encode pass.
`set_vignette_options(*, midpoint=0.5, roundness=0.5, feather=0.5, color=(0, 0, 0))` sets
Filament's `VignetteOptions`: `midpoint` in `[0, 1]` moves the start of the falloff, `roundness`
in `[0, 1]` goes from a rounded rectangle to a circle, `feather` in `[0.05, 1]` sets the width of
the falloff, and `color` is the linear color at the edges. The center pixel is unchanged.

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

- `load_environment(path, *, intensity=30000, rotation_deg=0)`: load a 2:1 panorama. Radiance HDR,
  PNG, and JPEG are accepted. HDR values retain their dynamic range.
- `set_environment(pixels, *, intensity=30000, rotation_deg=0)`: accept a contiguous NumPy
  `float32` RGB array with shape `(height, 2*height, 3)`. Values must be finite, nonnegative, linear light.
- `environment_intensity`: get or set intensity in lux. Setting requires a loaded environment.
- `environment_visible`: draw the environment as an opaque skybox. Default: `False`. The preference
  survives environment replacement and clearing; without an environment, the background color applies.
- `environment_rotation`: get or set rotation about Y in degrees. Setting requires a loaded
  environment. This updates the skybox, reflections, diffuse lighting, and diffuse backlighting.
- `load_environment_ktx(ibl_path, skybox_path=None, *, intensity=30000, rotation_deg=0)`: load a
  prefiltered cubemap made by Filament's `cmgen` (`cmgen --format=ktx`), as `gltf_viewer` does.
  The IBL file must contain spherical-harmonics metadata (`sh`); it supplies diffuse lighting.
  Without `skybox_path`, the sharpest IBL level is the skybox.
- `clear_environment()`: remove environment lighting.

Panorama height must be 2 through 4096. Rotation is about the Y axis in degrees. Loading filters
the panorama for diffuse lighting and specular reflections and waits for GPU completion. Do this
before the frame loop. The environment lights the scene even when hidden. Set
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
extensions; a material that uses them renders without that effect. Warnings also identify
glass loaded without refraction. These checks are not a complete glTF validator.

The native loader runs the checks and collects the warnings. `scene.load()` issues the warnings
after the native load returns, or before it raises an error. If a warning filter turns a
warning into an exception, the model is closed and the exception propagates.

### Texture limits

Filament 1.77.1 compiles glTF materials at feature level 1. A lit material then has 8 samplers
for textures. Each texture slot counts once, even when slots share an image: base color,
metallic-roughness, normal, occlusion, emissive, three clearcoat slots, two sheen slots,
transmission, volume thickness, and two specular slots. Anisotropy and iridescence materials
always use 3 more samplers, so their limit is 5. The limit was measured: a 9th texture aborts
the process in Filament's compiled material provider.

Loading checks the count first. With `precompiled_shaders=False`, a lit material with more than
8 textures raises `AssetError` with the material name. With `precompiled_shaders=True`, the same
material loads, and `AssetCompatibilityWarning` reports it: Filament's precompiled materials omit
some clearcoat, sheen, IOR, or specular inputs so that the rest fits. Transmission, volume,
anisotropy, and iridescence materials are always compiled and raise `AssetError` above their limit.

The archive path has other rules. Every entry has the five core textures (base color,
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
last. No Khronos sample material needs a drop. The limits are the same for the planned web build.
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
Mutable properties are `color` (linear RGB in `[0,1]`), `intensity` (nonnegative), `position`,
`direction` (nonzero), `range` (positive, point and spot only), and `casts_shadows`.
Directional and sun intensity is in lux; point and spot intensity is in candela.
A sun light shades like a directional light with a disc of `angular_radius_deg`, from 0.25 through
20 degrees. Filament moves the light direction toward each pixel's reflection within that disc,
so specular highlights change the most. A visible skybox draws the disc and its halo;
`halo_size` multiplies the disc radius and `halo_falloff` sets how fast the halo fades.
`gltf_viewer` uses the defaults for its sun.
Directional, point, and spot lights can cast shadow maps. Set both `scene.shadows = True` and
`light.casts_shadows = True`. Imported glTF lights use the same controls. A point light uses six
shadow-map faces, so its cost can be higher than a spot light. Filament limits the total shadow
maps in a view.

`light.set_shadow_options(*, map_size=1024, constant_bias=0.001, normal_bias=1.0)` changes
resolution and depth bias without enabling shadows. `map_size` is the resolution per face and
must be a power of two from 8 through 4096. Bias values must be finite and nonnegative.
Constant bias uses world units; normal bias scales Filament's sampling-error estimate. Each
call replaces these three settings. Set them before warmup; resolution changes can reallocate maps.
The settings also apply to imported lights. Shadow maps do not model colored light transmission
through glass or translucent volumes.

`set_spot_cone(inner, outer)` requires `0 <= inner <= outer < pi/2` in radians. The intensity in
candela does not change, so the brightness on the cone axis stays the same. This is the glTF rule.
Scene-created spot lights and imported spot lights behave the same way, and so do animated cones.

Imported lights are available through `model.light(key)` and `model.lights`. The key is the
name or glTF index of the node that carries the light. `light.node` returns that `Node`; it is
`None` for lights that a scene created. Position and direction are local to the light entity.
Imported light nodes retain the glTF hierarchy; animate or move their node to change their
placement in that hierarchy.

`light.close()` removes a light. Repeated calls are valid. `closed` is read-only. Imported lights
lose their light component but retain the node for animation. The handle stays in `model.lights`
and reports `closed=True`. Closing the model or its scene also invalidates its light handles.
Scene-created lights release their entity and scene ownership. Lights support `==` and `hash()`.
Two handles are equal when they refer to the same light.

## Camera

- `set_perspective(*, fov_y, aspect=None, near, far)`: vertical field of view in degrees.
- `set_lens_projection(*, focal_length_mm, aspect=None, near, far)`: perspective projection from
  a focal length on Filament's 24 mm sensor height. `gltf_viewer` uses 28 mm.
- `set_orthographic(*, left, right, bottom, top, near, far)`: explicit orthographic bounds.
- `set_orthographic(*, height, center=(0, 0), near, far)`: orthographic view of `height` scene
  units around `center`. The width is `height` times the target aspect.
- `position`: three coordinates.
- `look_at(target, up=(0, 1, 0))`: set the viewing direction.
- `frame(target, *, fill=0.8, direction=None, up=(0, 1, 0), fit="sphere", near=None, far=None, aspect=None)`:
  aim at a target and move the camera so that it fills part of the view. See
  [framing](#framing).
- `transform`: affine 4 by 4 model matrix.
- `view_matrix`: copy of the view matrix.
- `projection`: copy of Filament's projection matrix.
- `exposure`: camera EV100 in `[-10, 24]`. Lower values produce a brighter image.
- `focus_distance`: [depth-of-field](#depth-of-field) focus distance in scene units, positive.
  It is 0 until set, which Filament treats as the near plane.
- `aperture`: depth-of-field f-number in `[0.5, 64]`. Default: 16. It does not change exposure.

With `aspect=None`, and with the `height` form of `set_orthographic`, the projection follows the
render target: each `render()` applies the target's width divided by its height if it differs
from the last applied value. An explicit `aspect` wins over the target. A positive `aspect` is
required when it is given. `set_orthographic` accepts either all four bounds or `height`, not
both; `center` requires `height`.

`projection` returns the matrix that the next render uses for the last target aspect. Before the
first render, a projection that follows the target uses aspect 1. After a render into a target
with another aspect, it reflects that target. The update does not allocate memory.

Require `0 < near < far`. Default exposure is approximately EV100 15, equivalent to aperture 16,
shutter time 1/125 second, and ISO 100. Dim authored lights can require a much lower EV100.

Cameras support `==` and `hash()`. Two handles are equal when they refer to the same camera.
`camera.node` returns the `Node` of an imported camera, and `None` for a scene-created camera.

### Framing

`camera.frame(target, ...)` places the camera once. It never runs by itself: call it again after
the target moves. It returns the distance from the camera to the target's center.

- `target`: a `Model` or a `Node`, which use their [`bounds`](#model) moved by the model's
  current transform, or a `(min, max)` box in world coordinates.
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
  current direction. `up` must not be parallel to it. The camera then looks at the target's
  center with `up`, as `look_at()` does.
- A perspective camera keeps its field of view; `frame()` sets its distance. An orthographic
  camera gets the `height` form of `set_orthographic()`, so it follows the target aspect, and a
  distance that keeps the target between the clipping planes.
- `near` and `far`: default planes enclose the target (its sphere, or the box corners) with a
  margin of 5% of its depth along the view, or of its sphere radius for a flatter target. Given
  planes are used as they are. `projection` shows only `near`: Filament renders with the far
  plane at infinity and uses `far` for culling.
- `aspect`: the target aspect for the horizontal extent. Default: the aspect of the camera's
  current projection, which is 1 before the first render of a camera that follows its target.
  Pass the render target's width over height before the first render.

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
- `rotation_euler_deg` and `rotation_euler_rad`: rotations applied about X, then Y, then Z.
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
- `animations`: list of `AnimationInfo` objects with `name` and `duration` in seconds.
- `apply_animation(key, time, *, loop=True)`: evaluate a clip at an explicit nonnegative time.
- `reset_animation()`: restore authored node transforms, morph weights, camera projections, and animated material/light/texture properties.
- `variants`: list of material variant names.
- `apply_variant(key)`: apply a material variant.
- `clone()`: return another instance of an asset loaded with `clonable=True`, or of a
  generated mesh. See [clones](#clones).
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

An unknown name or an index out of range raises `AssetError`. So does a node that carries no
light or no camera. A name that several entries share raises `AssetError`, and the message lists
the matching indices. Use an index for unnamed or duplicated entries. Other key types, including
`bool` and `float`, raise `TypeError`.

Names are the names in the glTF document. A node without a name has the name `None`, even if its
mesh, light, or camera has a name. To find an unnamed node, use one of these:

- Its glTF node index: `model.node(3)`.
- The node tree: `model.node("Body").children` or `node.parent`.
- Its mesh name: `[n for n in model.nodes if n.mesh_name == "Helmet"]`.
- The item that it carries: `model.lights[0].node` or `model.cameras[0].node`.

`model.material(name)` edits the material that every mesh slot using it shares. Separate loaded
models and clones have separate material instances. To change one mesh slot only, use
[`node.material(slot)`](#node-and-material).

### Clones

`model.clone()` returns a new `Model` in the same scene. The asset must be loaded with
`scene.load(source, clonable=True)`, or be a [generated mesh](#generated-meshes); otherwise
`clone()` raises `FillyError`. A clone of a
clone is valid. The clone shares the loaded asset's geometry, textures, and compiled materials.
It has its own entities, transforms, material instances, node-local materials, visibility, light
components, skinning, and animation state. It starts from the asset's load-time state; edits to
the source model, including runtime textures, are not copied.

Closing a model removes only that instance from the scene. The other instances keep rendering.
The shared asset stays in memory until its last instance closes. Filament needs the asset's
source data to create instances. With `clonable=True`, the source data stays in CPU memory for
the life of the asset. This costs about the size of the asset file: 3.7 MiB for the 3.6 MiB
DamagedHelmet. With the default `clonable=False`, loading releases the source data. Each clone
creates new material instances and decodes the textures of the custom diffuse-transmission,
anisotropy, and iridescence materials again; on the archive path, it decodes all extension
textures again. Clones with lights add their light components to
`stats.live_lights`. Clone before the frame loop.

Closing a model invalidates all its node, material, light, and imported camera handles. Scenes
that use one of its cameras lose their active camera. Other assets, scene-created cameras,
and environment lighting remain available. Model and light close queue destruction without a
GPU completion wait. Call `renderer.finish()` when completion is required before the next trial.
Compiled material definitions remain cached until the renderer closes.

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
compiled into the material and cannot change later. The other factors are
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

`clone()` works without `clonable=True`. Clones share the vertex data, so `update_mesh()` on one
instance changes all of them. Each clone has its own transform and materials.

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

Animation evaluation restores the authored node state before applying the selected clip. This
makes arbitrary time jumps independent of prior frames. It replaces manual node edits; apply
those edits after animation. Bone matrices follow the edits at the next render. The model's outer root transform
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

Clones follow the same rule, each with its own materials and copies.

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
| `base_color_texture` | Get or set a `Texture`, a `HostTexture`, or `None`. The texture multiplies `base_color`. |
| `emissive_texture` | Get or set a `Texture`, a `HostTexture`, or `None`. The texture multiplies `emissive`. Lit materials only. |
| `set_texture_transform(slot, *, offset=(0, 0), scale=(1, 1), rotation_deg=0)` | Transform the UVs of the assigned texture in `slot`, `"base_color"` or `"emissive"`, as `KHR_texture_transform` does. Each call replaces the transform. |

The getters return the texture that was assigned at run time, or `None`; they do not return
textures from the glTF file. `None` removes the assignment. Textures sample UV set 0; a mesh
without `TEXCOORD_0` samples texel (0, 0) everywhere. Emissive values are output values: with
`emissive = (1, 1, 1)`, an emissive texel of linear 0.25 adds 0.25 to the output, whatever the
exposure.

Both `model.material(name)` and `node.material(slot)` handles support textures. On a shared
material, every mesh slot that shows it gets the texture; slots with a node-local material keep
their own. A node-local material made from a textured shared material starts with its texture.

A material from the glTF file is compiled for its texture slots. The first assignment picks
how the handle continues:

- If the compiled material lacks the slot, or lacks texture transforms, the material provider
  supplies the material for the same glTF key with the slot and transforms added. The factors
  and render state carry over, and back again when the last runtime texture is removed. This
  is the flat-colored stimulus case. It requires that the material has no glTF textures in
  other slots, because Filament has no API to read them back: otherwise `AssetError`.
- If the compiled material already has the slot, and has other glTF textures, the handle shows
  a copy with the runtime texture in that slot. Its other textures stay.
  `set_texture_transform()` then raises `AssetError` unless the glTF material has transforms.
- Removing the texture restores the glTF texture of the slot, if it had one.

A new combination of slots compiles a material the first time, 0.1 to 0.35 s. Assign textures
before a timing-critical trial; later assignments of the same combination reuse the compiled
material. Texture transforms and texture updates cost no compilation. Property animation of the
glTF material also reaches the textured material, and `apply_variant()` keeps runtime textures
on shared materials; on node-local materials that the variant maps, they are lost, as other
edits are. The custom diffuse-transmission, anisotropy, and iridescence materials have no
runtime texture slots: `AssetError`.

On the archive path, every material has both slots, including diffuse transmission,
anisotropy, and iridescence. An assignment changes parameters of a copy of the glTF instance:
nothing is compiled, other glTF textures stay, and `set_texture_transform()` always works.
Removing a texture starts again from a copy of the glTF instance, which has the glTF texture of
that slot, and the factors carry over. The rules for variants and animation are the same.

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
for large per-frame stimuli. A drifting grating needs no upload at all: use
`set_texture_transform()`.

Textures support `==` and `hash()`. Closing the renderer closes its textures.

## HostTexture

`renderer.import_gl_input(texture_id, *, width, height, color_space, filter="linear", wrap="repeat")`
returns a `HostTexture`: a host OpenGL texture that materials sample, for zero-copy video or
host-drawn patterns on a 3D surface. The texture must be a `GL_TEXTURE_2D` with `GL_RGBA8`
storage in the shared context. For `color_space="srgb"`, Filament samples a `GL_SRGB8_ALPHA8`
view of it, so shaders read linear light. A view needs immutable storage from `glTexStorage2D`;
mutable storage from `glTexImage2D` raises `InteropError` for `"srgb"`. For `"linear"`, Filament
samples the texture itself, and either storage works. Filament samples level 0 only.

The host writes it inside `write()`:

```python
with host_texture.write():
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

The order is explicit in both directions and has no CPU wait for the GPU. Entering `write()`
waits until Filament's driver thread has placed a fence after the last frame, as `acquire()`
does, and queues a GPU wait on it in the host context, so the host does not overwrite pixels
that a submitted frame still reads. It releases the GIL while it waits. Leaving `write()`
places a host fence; the next `render()` of any scene queues a GPU wait on it before the frame.
`render()` inside `write()` raises `InteropError`. Each render adds one fence per host texture.
`close()` fences the host's work if the host context is current, as `ImportedTarget.close()`
does.

## OffscreenTarget

`create_render_target()` returns an `OffscreenTarget`.

| Member | Behavior |
| --- | --- |
| `width`, `height` | Size in pixels. Read-only. |
| `read()` | Return a contiguous `(height, width, 4)` `uint8` RGBA array. The first row is the top of the image. Call `render()` at least once first. |
| `close()` | Release the target. Repeated calls are valid. |
| `closed` | Report whether the target or its renderer is closed. |

Readback waits for the GPU. It releases the GIL while it waits.

## ImportedTarget

`import_gl_texture()` returns an `ImportedTarget`. This is the API for authors of host adapters:
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
| `width`, `height` | Size in pixels. Read-only. |
| `acquire()` | Return a context manager that holds the texture for host sampling. `with target.acquire() as t` gives `t is target`. Nested calls only count depth. |
| `acquired` | `True` inside `acquire()`. Read-only. |
| `close()` | Release the Filament side. The host texture is not deleted. Repeated calls are valid. |
| `closed` | Report whether the target or its renderer is closed. |

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
filtering, `finish()`, `close()`, `Texture.update()`, texture assignment, mesh creation and
updates, and entering `HostTexture.write()` release the GIL. Other Python threads can run during these
calls. Native asset loading includes the glTF checks and preparation. Normal rendering does not
perform a full GPU completion wait.

## Host integrations

Each module in `filly.integrations` has a `SharedTarget` class. It creates a host-owned
texture with immutable RGBA8 storage, so both output paths work, imports it with `import_gl_texture()`, and is an
`ImportedTarget`. The moderngl and zengl adapters wrap that texture as an external texture or
image.

| Module | Constructor | Renderer factory |
| --- | --- | --- |
| `pyglet` | `SharedTarget(renderer, window, width, height)` | `create_renderer(window)` |
| `moderngl` | `SharedTarget(renderer, ctx, width, height)` | `pyglet.create_renderer(window)` |
| `zengl` | `SharedTarget(renderer, ctx, width, height, framebuffer=None)` | `pyglet.create_renderer(window)` |
| `psychopy` | `SharedTarget(renderer, win, width, height)` | `create_renderer(win)` |

| Member | Behavior |
| --- | --- |
| `acquire()` | Make the host context current if no acquisition is open, then call `ImportedTarget.acquire()`. |
| `draw(x=0, y=0, width=None, height=None)` | Draw the texture at window pixel (x, y) from the lower-left corner, inside `acquire()`. |
| `close()` | Close the Filament target, then delete the host texture. Repeated calls are valid. |
| `as_psychopy_texture(win=None, *, size=None, pos=(0, 0), units="pix")` | PsychoPy only. Return an ImageStim that samples the texture. |

`width` and `height` follow the rules for `import_gl_texture()`, with the same error messages.
The adapter checks them before it creates the host texture.

Close the target, then the renderer, then the window. If the window closes first, `close()`
makes no host OpenGL calls and releases the Filament target; `Renderer.close()` then also
succeeds. The host objects go away with the window's context. `SharedTarget` supports `with`
and closes on exit.

A target that is garbage-collected without `close()` emits `ResourceWarning`. Collection runs at
arbitrary points, often while another window's context is current. It therefore never makes
another context current, and it deletes host objects only if the target's own context is
current. Otherwise the host objects stay until their window closes. The same rule applies to the
mask and shader of a PsychoPy stimulus from `as_psychopy_texture()`.

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
