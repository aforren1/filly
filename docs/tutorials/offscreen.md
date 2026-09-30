# Render an image

First, [build the package](../how-to/build.md). Select a GLB file.

```python
import numpy as np
import filly

with filly.Renderer() as renderer:
    scene = renderer.create_scene()
    scene.refraction = True
    scene.antialiasing = "fxaa"
    camera = scene.create_camera()
    # The width follows the aspect ratio of the render target. frame() sets the height.
    camera.set_orthographic(height=1, near=1, far=2)
    scene.camera = camera
    scene.add_directional_light(direction=(-1, -1, -1), intensity=50000)

    model = scene.load("stimulus.glb")
    # Look along -Z at the model's center; its bounding sphere spans 80% of the image.
    camera.frame(model, fill=0.8, direction=(0, 0, -1), aspect=1)
    target = renderer.create_render_target(width=1024, height=1024)
    renderer.render(scene, target)
    image = target.read()
    np.save("stimulus.npy", image)
```

`image` is an array of shape `(1024, 1024, 4)` with type `uint8`.
The first row is the top of the image. The array remains valid after the renderer closes.
The values are sRGB-encoded: a linear material value of 0.5 becomes 188.
Set `scene.encoding = "linear"` to store linear values (0.5 becomes 128) instead.

Readback waits for the GPU and copies image data to CPU memory.
Use it for images and diagnostics. For display without CPU readback, use the
[PsychoPy shared-texture adapter](../how-to/psychopy.md).

For reflective metals, add environment lighting before rendering:

```python
scene.load_environment("studio.hdr", intensity=30000)
```

For an animated asset, select the time explicitly:

```python
if model.animations:
    model.apply_animation(0, 1.5)
```

To rotate the model about the Y axis, assign a matrix before rendering:

```python
angle = np.deg2rad(30)
c, s = np.cos(angle), np.sin(angle)
model.transform = np.array([
    [c, 0, s, 0],
    [0, 1, 0, 0],
    [-s, 0, c, 0],
    [0, 0, 0, 1],
], dtype=np.float32)
```

Run this code while the renderer is open. Matrix getters return copies.
Assign the matrix back to the property after changing it.

`camera.frame()` places the camera once, for the model's transform at that time. After you move
or rotate the model, call it again, or rotate the model about its center as
`examples/screenshot.py` does. See [framing](../reference/api.md#framing).
