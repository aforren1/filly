# filly for MATLAB and Octave

filly renders glTF 3D models with Google Filament, offscreen or into Psychtoolbox windows. This
package is the MATLAB and Octave frontend. It uses the same native renderer as the Python package.

## Packages

| Package | Runs on |
| --- | --- |
| `filly-matlab-windows` | MATLAB R2021b and later, Windows x64 |
| `filly-matlab-linux` | MATLAB R2021b and later, Linux x64 with glibc 2.28 or later |
| `filly-octave-linux-6.4` | Octave 6.4 through 9, Linux x64 with glibc 2.28 or later |
| `filly-octave-linux-10` | Octave 10 and later, Linux x64 with glibc 2.28 or later |

Octave 10 changed the MEX ABI. Use the Octave package for your Octave version: the other one
does not load.

The renderer needs OpenGL 4.1 or later. Psychtoolbox is necessary only to draw into its windows.

## Install

1. Unzip the package into an empty folder.
2. In MATLAB or Octave, go to that folder and run `SetupFilly`. It adds the folder to the path
   and checks that the MEX file loads. To keep the folder on the path for later sessions, run
   `SetupFilly(true)`.
3. To see the version, run `filly.version`.

## Render offscreen

```matlab
r = filly.Renderer();
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
model = scene.load(filly.samples.horse());   % or the path of your own glTF file
cam.frame(model);
scene.addDirectionalLight([0 -1 -1]);
target = r.createRenderTarget(640, 480);
r.render(scene, target);
pixels = target.read();          % 480 x 640 x 4 uint8, first row at the top
close(r);
```

## More information

- Use filly from MATLAB, Octave, and Psychtoolbox:
  https://github.com/aforren1/filly/blob/main/docs/how-to/matlab.md
- Report problems: https://github.com/aforren1/filly/issues

filly is MIT licensed (`LICENSE`). The licenses of the libraries that the MEX file contains are
in `licenses/`.

`samples/horse.glb` is "Horse" by Quaternius, in the public domain (CC0 1.0); see
`samples/ATTRIBUTION.md`.
