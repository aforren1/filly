# filly for the web

filly renders glTF models with [Filament](https://github.com/google/filament), compiled to
WebAssembly. It draws into a WebGL2 context that your page and its own renderer (for example PIXI
in PsychoJS) also use. Filament renders into a texture that your page owns, and your page draws
that texture. filly does not copy the pixels.

filly also has Python and MATLAB frontends with the same objects and behavior. See the
[filly repository](https://github.com/aforren1/filly).

## Install

```sh
npm install @aforren1/filly
```

The package is an ES module for browsers. It does not run in Node.js. It loads
`filly-core.wasm` from the folder of `filly.js` with `new URL("filly-core.wasm", import.meta.url)`.
Bundlers that support this pattern, for example Vite and webpack 5, copy the file for you.

Without a bundler, load the files from a CDN, or copy `filly.js`, `filly-core.js`, and
`filly-core.wasm` into one folder on your server:

```javascript
import { createRenderer } from "https://cdn.jsdelivr.net/npm/@aforren1/filly@<version>/filly.js";
```

Each [GitHub release](https://github.com/aforren1/filly/releases) also has the files in
`filly-web-<version>.zip`.

## Requirements

- A browser with WebGL2.
- An HTTP server. Browsers do not load WebAssembly modules from `file://` pages.

## Render a model into a texture

```javascript
import { createRenderer } from "@aforren1/filly";

const gl = canvas.getContext("webgl2");
const renderer = await createRenderer(gl);

// A texture that your page owns, with RGBA8 storage.
const texture = gl.createTexture();
gl.bindTexture(gl.TEXTURE_2D, texture);
gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, 512, 512);
const target = renderer.importTarget(texture, 512, 512);

const scene = renderer.createScene();
const model = await scene.loadUrl("model.glb");
scene.camera = renderer.createCamera();
scene.camera.frame(model);
scene.addDirectionalLight({ direction: [-1, -1, -2], intensity: 100000 });

function frame(time) {
	model.rotationEulerDeg = [0, time / 20, 0];
	renderer.render(scene, target);
	renderer.handBack();  // before the page's own GL calls
	// ... draw `texture` with your renderer ...
	requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
```

Call `renderer.handBack()` after your last filly call and before your renderer draws. For the
other rules of a shared context and for the full API, see
[use filly in a web page](https://github.com/aforren1/filly/blob/main/docs/how-to/web.md).

## License

filly is MIT licensed. `licenses/` has the licenses of the code in `filly-core.wasm`, including
Filament (Apache 2.0).
