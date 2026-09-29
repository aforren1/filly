# Use PsychoPy with shared textures

The adapter uses OpenGL and PsychoPy's `pyglet` backend, with Windows WGL or Linux GLX contexts.
Windows presentation has been tested. Linux shared-texture tests use pyglet directly; a full
Linux PsychoPy presentation test remains necessary. See [Linux setup](build.md#build-on-linux).
Raw GLX sharing passed on Mesa software rendering and the WSLg Intel D3D12 driver.
It uses `psychopy-lib==2026.2.4`. The full PsychoPy package and Builder GUI are not required.
The library package still includes dependencies used by dialogs and other runtime features.

## Install

First, download the Filament SDK as described in [Build and test](build.md).
Use Python 3.11 for a new PsychoPy environment. The `pywinhook` dependency provides a Windows wheel
for that Python version, which avoids a source build.

```powershell
uv venv --python 3.11 .deps/psychopy311
uv pip install --python .deps/psychopy311/Scripts/python.exe nanobind scikit-build-core ninja pytest
uv pip install --python .deps/psychopy311/Scripts/python.exe --no-build-isolation -e ".[psychopy,examples]"
uv run --no-project --python .deps/psychopy311/Scripts/python.exe python examples/psychopy_shared.py
```

To display a local GLB instead of Suzanne, pass its path:

```powershell
uv run --no-project --python .deps/psychopy311/Scripts/python.exe python examples/psychopy_shared.py "C:/models/stimulus.glb" --frames 600
```

The demo centers custom assets and fits the camera without changing physical scale. It plays the
first glTF animation and shows the file stem in the upper-left label. Custom assets do not spin
unless `--spin` is supplied. Press Escape to stop. Custom assets do not trigger the Suzanne download.

Use these options to inspect an asset:

| Option | Effect |
| --- | --- |
| `--animation 1` | Select animation clip 1. Clip indices start at zero. |
| `--no-animation` | Keep the authored pose. |
| `--time 4` | Evaluate a fixed animation time in seconds. |
| `--variant "Khronos Red"` | Select a material variant by name or index. |
| `--lighting auto` | Use authored lights when present, otherwise studio lighting. This is the default. |
| `--lighting studio` | Disable authored lights and add the demo's directional light. |
| `--lighting asset` | Retain authored lights without adding a directional light. |
| `--lighting environment` | Disable authored lights and light the model with the panorama. Default intensity: 30000 lux; exposure: EV100 15. |
| `--view 35 22` | Fit the camera at yaw 35 and pitch 22 degrees. Overrides the default imported camera; cannot be combined with `--camera`. |
| `--exposure 0` | Set camera EV100. Lower values make the image brighter. |
| `--camera "CameraNode"` | Use an imported camera by the name or glTF index of its node. |
| `--projection perspective` | Set the fitted camera projection. Files default to perspective; Suzanne defaults to orthographic. |
| `--background 0.5 0.5 0.5` | Set a neutral linear RGB background to inspect glass. |
| `--environment studio.hdr` | Load a 2:1 HDR panorama for reflections and diffuse lighting. |
| `--environment-intensity 30000` | Set environment intensity in lux. |
| `--spin 20` | Rotate the whole model at 20 degrees per second. |

The demo enables glass refraction and FXAA. ChronographWatch needs refraction to show its dial
through the glass. DiffuseTransmissionPlant uses animated point-light nodes and diffuse
transmission. Its dim authored lights need a lower camera EV100 than the watch's studio lighting.
Auto lighting selects EV100 0 for authored lights and EV100 15 for studio lighting. These are
preview defaults; adjust exposure for your stimulus. Animation clip names and variants print at startup.

For MosquitoInAmber, use a perspective camera and a brighter background:

```powershell
uv run --no-sync python examples/psychopy_shared.py path/to/MosquitoInAmber.glb --background 0.5 0.5 0.5
```

The environment lights the object. Add `--show-environment` to display it as an opaque skybox,
and `--environment-rotation 45` to rotate it. Glass samples
the scene behind it, so the default dark background can hide interior details. The volume-distance
filter restores interior detail with `--projection orthographic`. See the
[Khronos sample audit](../reference/sample-assets.md) for the current limits.

Use the [stress benchmark](stress-test.md) to repeat asset trials, estimate missed refreshes,
and sample process/GPU memory between trials.

Python 3.12 was also tested. On that version, pywinhook 1.6.2 needs SWIG, the Microsoft C++
toolchain, and a compiler definition that maps its obsolete `PyInt_AsLong` call to `PyLong_AsLong`.
The Python 3.11 environment above avoids that dependency repair.

Use `visual.TextBox2` for text. It draws glyphs with its own FreeType atlas. On Windows with
Python 3.12 or later and pyglet 1.4.11, `visual.TextStim` fails with a ctypes error because
pyglet passes a signed-byte glyph buffer
([upstream issue](https://github.com/psychopy/psychopy/issues/7589)). The adapter does not
change pyglet, and `TextBox2` does not use that path.

## Render and display

```python
from psychopy import core, event, visual
import filly
from filly.integrations.psychopy import SharedTarget, create_renderer

filly.set_log_level("warning")
win = visual.Window(size=(960, 640), units="pix", winType="pyglet", useFBO=False)
renderer = create_renderer(win)
scene = renderer.create_scene()
scene.refraction = True
scene.antialiasing = "fxaa"
camera = scene.create_camera()
# Without an aspect, the projection follows the render target.
camera.set_perspective(fov_y=45, near=0.1, far=100)
camera.position = (0, 0, 5)
camera.look_at((0, 0, 0))
scene.camera = camera
scene.add_directional_light(direction=(-1, -1, -1))
model = scene.load("stimulus.glb")
target = SharedTarget(renderer, win, 960, 640)
stimulus = target.as_psychopy_texture(win)

clock = core.Clock()
clips = model.animations
for frame in range(120):
    if event.getKeys(keyList=["escape"]):
        break
    if clips:
        model.apply_animation(0, clock.getTime())
    renderer.render(scene, target)
    stimulus.draw()
    win.flip()

target.close()
renderer.close()
win.close()
core.quit()
```

The target can be passed directly to `renderer.render()`. The stimulus samples the target on the
GPU. Its draw method waits on the renderer's fence and places a host fence after the draw.
The shared target has no `read()`: no image upload or CPU framebuffer copy is needed.
The texture holds sRGB-encoded values, which PsychoPy samples as plain RGBA8 and shows as they are.

Use `useFBO=False`, which is PsychoPy's default. With PsychoPy 2026.2.4 and pyglet 1.4.11,
`useFBO=True` does not present stimuli: the window alternates between the clear color and black.
This occurs on Intel and NVIDIA GPUs, with a plain PsychoPy rectangle and no Filament renderer.
The shared texture does not require PsychoPy's extra FBO. The adapter leaves the host window's
setting unchanged.

`target.as_psychopy_texture()` accepts `size`, `pos`, and `units`. Size defaults to the texture's
dimensions in pixels. The returned stimulus supports normal ImageStim position, size, and
orientation controls. Its image cannot be replaced. It can draw the last frame again without
a new render.
Opacity and masks are supported with the window's default `blendMode="avg"`.
The adapter does not resize targets when the window changes size.

To draw one frame with several stimuli, draw inside one `target.acquire()`. Nested acquisitions,
such as each stimulus's own draw, then do nothing:

```python
renderer.render(scene, target)
with target.acquire():
    left.draw()
    right.draw()
```

`target.draw(x=0, y=0, width=None, height=None)` draws the texture with an internal stimulus.
`x` and `y` are window pixels from the lower-left corner, as in the
[pyglet, moderngl, and zengl integrations](gl-hosts.md).

`SharedTarget` accepts any integer type for the width and height, including the `numpy`
integers in `win.size`. It also accepts floats with no fractional part, such as `960.0`.
It rejects a fractional size, such as `960.5`, with `ValueError`, because a truncated texture
does not match the window's pixel size. Sizes must be from 1 through 8192.

Close targets first, then the renderer, then the window. The host window must remain alive for
all shared drawing. With the window open, Filament fences host work in the window's OpenGL
context before it releases a target. If you close the window first, `target.close()` and
`renderer.close()` still succeed and release everything. They make no PsychoPy OpenGL calls, and
Filament waits for its own GPU work only; the host's last GPU work is not fenced.
Context managers are optional; the example uses explicit cleanup.

On Windows, pyglet 1.4.11, which PsychoPy pins, can crash when a program closes a window and then opens another.
It does not unregister a closed window's child view class, and a new window can reuse that
class and its freed callback. This does not depend on filly. If your program opens windows one after another,
keep a reference to each closed window object until the program exits, so that Python cannot
reuse its address. See [validation](../reference/validation.md) for the details and the test-suite
workaround.

`filly.set_log_level("warning")` suppresses Filament's startup details and keeps warnings and errors.
Use `"verbose"` to restore full native logging or `"off"` to silence its log streams. The setting
applies to all Filament renderers in the process. PsychoPy logging is separate.

## Composite over PsychoPy stimuli

Keep `scene.environment_visible` false when the PsychoPy underlay must remain visible.

Set `scene.background = (0, 0, 0, 0)` and `scene.transparent = True`. Draw regular stimuli
first, then the shared stimulus,
then any overlays. The adapter converts premultiplied texture color for ImageStim's blending.

```powershell
uv run --no-sync python examples/psychopy_shared.py --transparent --frames 300
```

This draws Suzanne over a regular PsychoPy grating, with the text label above both.
Glass can refract geometry in the Filament scene, but cannot refract the PsychoPy underlay.

## Lighting and shadows

Enable shadows on the scene and on each light that needs them:

```python
scene.shadows = True
light.casts_shadows = True
light.set_shadow_options(map_size=512, constant_bias=0.001, normal_bias=1.0)
scene.environment_visible = True
scene.environment_rotation = 45  # Requires a loaded environment.
```

Point lights use six shadow faces. A smaller map reduces their rendering cost. Shadow resolution
and bias also apply to directional and spot lights. Set these options before warmup.
The visible environment replaces the background with opaque pixels. Its rotation affects both
lighting and the skybox. For asset lights, the demo uses a dim environment intensity of 1 when
`--show-environment` is requested without an explicit intensity.

To run the cached TrafficCone asset with point shadows and the studio skybox:

```powershell
uv run --no-sync python examples/psychopy_shared.py .deps/sample-audit/assets/Models/TrafficCone/glTF/TrafficCone.gltf --shadows --shadow-map-size 512 --show-environment --environment-rotation 45 --frames 90 --warmup 10 --profile lighting.csv
```

## Inspect dielectric iridescence

To inspect `IridescenceDielectricSpheres`, use an angled camera and environment lighting.
The front view overlaps the sphere rows. The default directional light can wash out the film colors.

```powershell
uv run --no-sync python examples/psychopy_shared.py .deps/sample-audit/assets/Models/IridescenceDielectricSpheres/glTF/IridescenceDielectricSpheres.gltf --lighting environment --projection orthographic --view 35 22 --spin 0 --frames 600
```

This improves the preview. Filament's remaining thin-film shading approximation is described in
the [sample audit](../reference/sample-assets.md#dielectric-iridescence).

## Record frame timing

```powershell
uv run --no-sync python examples/psychopy_shared.py --frames 600 --profile timing.csv --warmup 60
```

The CSV records every frame. The printed p50, p95, and p99 summary excludes warmup frames.
Columns separate native submission, CPU fence publication wait, host release, host drawing,
window flip, and the interval between returns from consecutive flips. Host drawing includes
the underlay, shared draw, label, wait, and release; these costs overlap and must not be added.
Profiling includes Python bookkeeping and runs with the window's normal flip pacing.
The first flip interval is unavailable. Early Escape writes the frames recorded so far.
These CPU measurements do not establish GPU duration, physical display onset, or 120/144 Hz deadlines.

## Verify

```powershell
uv run --no-project --python .deps/psychopy311/Scripts/python.exe python -m pytest -q
```

The tests check pixels sampled by PsychoPy and verify that rendering does not swap the host
buffers. For the example's `useFBO=False` path, they also check the front buffer after each flip.
A separate test queues 256 host samples without intermediate readback, then checks every
image region. This exercises the synchronization needed before texture reuse.

These tests establish functional behavior. They do not measure display timing or color calibration.
No display color transform is added by the adapter.
