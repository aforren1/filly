# Use filly in a web page

This guide shows how to render a glTF model with filly in a browser, into a texture that your
page draws with its own WebGL2 renderer. For the design, see [the web build](../explanation/web.md).

## Requirements

- A browser with WebGL2.
- The files of filly's web build: `filly.mjs`, `filly-core.mjs`, and `filly-core.wasm`, in one
  folder that the page can load. See [build for the web](build.md#build-for-the-web).
- An HTTP server. Browsers do not load WebAssembly modules from `file://` pages. The server can
  send `.wasm` files as `application/wasm` for faster loading; other types also work.

The page does not have to be cross-origin isolated. If it is (COOP and COEP headers), serve
filly's files from the same origin.

## Render a model into a texture

```javascript
import { createRenderer } from "./filly/filly.mjs";

const gl = canvas.getContext("webgl2");
const renderer = await createRenderer(gl);

// A texture that your page owns, with RGBA8 storage.
const texture = gl.createTexture();
gl.bindTexture(gl.TEXTURE_2D, texture);
gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, 512, 512);
const target = renderer.importTexture(texture, 512, 512);

const scene = renderer.createScene();
scene.transparent = true;
scene.background = [0, 0, 0, 0];
const model = await scene.loadUrl("model.glb");
const camera = scene.createCamera();
camera.setPerspective({ fovY: 30, near: 0.1, far: 100 });
camera.frame(model, { aspect: 1 });
scene.camera = camera;
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

The texture's rows start at the bottom, as in OpenGL. Its color is sRGB encoded and, for a
transparent scene, premultiplied by alpha.

## Share the context with another renderer

filly and your renderer use the same WebGL2 context. Follow two rules:

1. Call `renderer.handBack()` after your last filly call and before your renderer draws. filly
   enters the context again by itself at its next call.
2. If your renderer caches GL state, give `createRenderer()` an `onHandBack` function that makes
   it forget the cache. filly calls it at the end of `handBack()`.

With PIXI 6:

```javascript
const renderer = await createRenderer(pixi.gl, { onHandBack: () => pixi.reset() });
pixi.on("prerender", () => renderer.handBack());
```

Allocate the texture with PIXI, so that PIXI can draw it as a sprite:

```javascript
const base = new PIXI.BaseTexture(null, { width: 512, height: 512, alphaMode: PIXI.ALPHA_MODES.PMA,
	mipmap: PIXI.MIPMAP_MODES.OFF, scaleMode: PIXI.SCALE_MODES.LINEAR });
renderer.handBack();
pixi.texture.bind(base);
const glTexture = base._glTextures[pixi.CONTEXT_UID].texture;
const target = renderer.importTexture(glTexture, 512, 512);
const sprite = new PIXI.Sprite(new PIXI.Texture(base));
```

PIXI deletes the GL texture of a texture that it has not drawn for a while (texture garbage
collection) and creates a new one at the next bind. Check `base._glTextures` before you render,
and import the new texture if it changed. psychopy-filly's `filly-psychojs.mjs` is a complete
example.

## Load models and environments

- `scene.load(bytes)` loads a GLB, or a glTF document with embedded resources, from a
  `Uint8Array`.
- `await scene.loadUrl(url)` fetches a `.glb`, or a `.gltf` and the buffers and images it names.
- `scene.loadFiles(main, files)` loads a glTF document whose files you already have: `files` maps
  each relative URI, decoded, to its bytes.
- `scene.loadEnvironment(bytes)` loads a 2:1 Radiance HDR, PNG, or JPEG panorama;
  `scene.loadEnvironmentKtx(ibl, skybox)` loads cubemaps from Filament's `cmgen`.

Each model has `loadWarnings`, the compatibility warnings of its load. filly also writes them to
the console.

Load a model with `{ clonable: true }` to make more instances with `model.clone()`.
`model.nodes`, `node.parent`, and `node.children` walk the glTF node tree; `model.light(key)`,
`model.camera(key)`, and `model.node(key)` take a name or a glTF index.

## Compile before the first frame

Set up the scene, including lights and effects, and then wait for `renderer.prepare(scene)`
before you render it in a timing-critical frame:

```js
scene.addDirectionalLight({ direction: [-1, -1, -2], intensity: 100000 });
await renderer.prepare(scene);
renderer.render(scene, target);
```

The page keeps drawing while the browser compiles. `prepare()` rejects with `FillyError` after
`timeoutMs` (default 60000). In Firefox, the first render still compiles; see
[shader compilation](../explanation/web.md#shader-compilation).

A uniform panorama, for example `new Float32Array(8 * 16 * 3).fill(1)`, skips environment
filtering and takes a few milliseconds instead of about 260 ms.

## Make meshes from arrays

`shapes.plane()`, `shapes.box()`, `shapes.uvSphere()`, and `shapes.cylinder()` return flat
vertex arrays. `scene.createMesh()` takes them, or your own arrays:

```javascript
import { shapes } from "./filly/filly.mjs";

const sphere = scene.createMesh({ ...shapes.uvSphere({ radius: 0.5 }), baseColor: [0.8, 0.2, 0.2, 1] });
const quad = scene.createMesh({
	positions: new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]),
	indices: new Uint32Array([0, 1, 2, 0, 2, 3]),
	unlit: true,
});
quad.updateMesh({ positions: newPositions });  // the same vertex count
```

Arrays are flat: 3 numbers per position and normal, 2 per UV, 4 per color, and 3 indices per
triangle. The `shapes` functions need the loaded module, so call them after `createRenderer()`.

## Use textures

```javascript
const grating = renderer.createTexture(pixels, { width: 256, height: 256, channels: 4, colorSpace: "srgb" });
quad.material("mesh").baseColorTexture = grating;
grating.update(nextPixels);  // each frame, the same size and type
```

`pixels` is a `Uint8Array`, or a `Float32Array` of linear values, with rows from the top. Use 3
or 4 channels: WebGL2 cannot show a 1-channel texture as grey.

A material can also sample a texture that your page draws into, without a copy:

```javascript
const input = renderer.importInput(pageTexture, 256, 256, { colorSpace: "linear" });
material.baseColorTexture = input;
input.write(() => { /* draw into pageTexture with your renderer */ });
```

For `colorSpace: "srgb"`, allocate the page texture with `SRGB8_ALPHA8` storage; for
`"linear"`, with `RGBA8` storage.

## Read pixels

Render into a Filament-owned target and read it. The read resolves on a later turn of the event
loop, because WebGL completes GPU readbacks only after control returns to the browser:

```javascript
const target = renderer.createRenderTarget(320, 240);
renderer.render(scene, target);
const rgba = await target.read();  // Uint8Array, rows from the top
```

A readback waits for the GPU. Do not read in a timing-critical frame.

## Handle errors

filly's errors are `FillyError` and its subclasses `AssetError`, `InteropError`, and
`BackendError`, exported by `filly.mjs`. Invalid values raise `RangeError`.

```javascript
import { AssetError } from "./filly/filly.mjs";
try {
	await scene.loadUrl("missing.glb");
} catch (error) {
	if (error instanceof AssetError) console.error(error.message);
}
```

## Release objects

`renderer.close()` releases the renderer and everything it made. Each JavaScript object holds a
handle to a filly object; `dispose()` releases the handle at once, and the garbage collector
releases the others. Closing and releasing are separate: `model.close()` removes the model from
its scene, and `model.dispose()` only drops the JavaScript handle.
