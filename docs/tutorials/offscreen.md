# Render an image

In this tutorial, you render the sample horse that comes with filly into an image file. Then
you look at the horse from a different side, pose it, and read its pixels as a NumPy array. You
need Python 3.10 or later and an OpenGL 4.1 driver. The code does not open a window.

## Install filly

Install filly, and Pillow to save images:

```sh
python -m pip install filly pillow
```

## Render the horse

Make a file `horse.py` with this code:

```python
import filly
from PIL import Image

with filly.Renderer() as renderer:
    scene = renderer.create_scene()
    horse = scene.load(filly.samples.HORSE)

    scene.camera = renderer.create_camera()
    scene.camera.frame(horse)
    scene.add_directional_light(direction=(-1, -1, -2), intensity=100_000)

    target = renderer.create_render_target(width=800, height=600)
    renderer.render(scene, target)
    Image.fromarray(target.read()).save("horse.png")
```

Run it:

```sh
python horse.py
```

Open `horse.png`. The horse is in the center of the image, on a black background. It looks
toward you.

The code does these steps:

1. `filly.Renderer()` starts the renderer. The `with` block closes it at the end.
2. `scene.load()` adds the model to the scene.
3. `camera.frame()` aims the camera at the center of the model and moves the camera back until
   a sphere around the model fills 80% of the image height.
4. `add_directional_light()` adds a light, like the sun.
5. `render()` draws the scene into the render target, and `read()` copies the image to an
   array.

## Look from a different side

A new camera looks along the -Z axis, and the horse faces +Z. To look at the horse from its
side and from slightly above, give `frame()` a viewing direction. Change the `frame()` line to:

```python
    scene.camera.frame(horse, direction=(-1, -0.3, -1))
```

Run the file again. You now see the side of the horse.

## Pose the horse

The horse has one animation: a gallop of 1.5 seconds. Before the call to `render()`, add this
line to select the pose at 0.5 seconds:

```python
    horse.apply_animation(0, 0.5)
```

Run the file again. The horse is now in the middle of a stride.

## Read the pixels

At the end of the `with` block, add these lines:

```python
    image = target.read()
    print(image.shape, image.dtype)
    print(image[0, 0])
```

Run the file again. The output shows the shape `(600, 800, 4)` and the type `uint8`: one RGBA
value for each pixel. The first row is the top of the image. The top-left pixel is the
background, `[  0   0   0 255]`: black and opaque. The values are sRGB-encoded, as on a monitor.

## Next steps

- To show the model in a window, see [use PsychoPy](../how-to/psychopy.md) or
  [use other OpenGL hosts](../how-to/gl-hosts.md).
- To use MATLAB, Octave, or Psychtoolbox, see
  [use MATLAB, Octave, and Psychtoolbox](../how-to/matlab.md).
- For environment lighting, other cameras, transforms, encoding, and fast readback, see the
  [API reference](../reference/api.md).
