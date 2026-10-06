# The web build

filly's web build is filly's C++ core compiled with Emscripten for WebGL2. It runs in a page
together with another WebGL renderer, such as PIXI in PsychoJS, and draws into a texture that
the page owns. This page explains how that works and how the web build differs from the desktop
module. For the steps, see [use filly in a web page](../how-to/web.md); for the build, see
[build for the web](../how-to/build.md#build-for-the-web).

## Why filly's own core

Filament has a JavaScript build of its own (`filament.js`), but it cannot give the same output as
desktop filly:

- Its npm package stops at Filament 1.53.4, and material packages are tied to the engine version.
- It has no material compiler and uses gltfio's precompiled materials. filly's extensions
  (anisotropy and iridescence parameters, diffuse transmission, the orthographic refraction LOD)
  and its encode pass would be missing.

The web build compiles filly's core with its precompiled [material archive](material-precompilation.md)
and its output passes, for GLSL ES 3.00 (`matc -p mobile`). The material path is therefore the
same as on the desktop.

## One context, no copy

WebGL has no sharing between contexts, so filly renders into the page's context:

1. The page's WebGL2 context is registered with Emscripten's GL layer, which Filament's
   `PlatformWebGL` uses.
2. The page allocates a texture (PIXI: a texture without a resource), and filly imports it as
   the color attachment of its render target.
3. filly renders. The page draws the texture.

The build is single-threaded. Filament runs its GL commands inside `render()`, so they reach the
context before the page's next draw, and no fences are needed. The page needs no
`SharedArrayBuffer` and thus no cross-origin isolation.

## Handing the context over

The page's renderer and Filament each cache GL state. Each must not trust state that the other
changed. `filly.js` therefore keeps the context in one of two modes:

| Step | When | What it does | Why |
| --- | --- | --- | --- |
| Enter | Before the first filly call after the page drew, except property reads | Sets the pixel-store values that the page changed to GL defaults; unbinds the page's vertex array object, array buffer, framebuffer, and program; queues `Engine::resetBackendState()` | PIXI leaves `UNPACK_PREMULTIPLY_ALPHA_WEBGL` set, which WebGL forbids for 3D texture uploads, such as the color-grading LUT in the spike. Filament assumes the default vertex array object when it binds buffers. `resetBackendState()` makes Filament set its state again |
| Hand back | Before the page draws (PIXI: its `prerender` event) | Unbinds textures and sampler objects on the units that the core used, buffers, framebuffers, and the program; restores GL defaults; restores the page's pixel-store values; calls the page's reset hook (PIXI: `renderer.reset()`) | After a render, Filament leaves sampler objects bound on units 0 to 7, and a sampler object overrides the filtering of the texture on its unit. PIXI's `reset()` forgets its texture bindings without unbinding them, and its batch shader declares a sampler for every unit |

Before these steps, the spike showed two failures in PsychoJS. PIXI's draws failed with
`INVALID_OPERATION` once Filament's engine existed, also without a render; unbinding PIXI's vertex
array object before each Filament call fixed it. Filament's image was black, and the browser
reported a rejected 3D texture upload with `UNPACK_PREMULTIPLY_ALPHA_WEBGL` set; resetting the
pixel-store state fixed it. The texture and sampler unbinding is a precaution: no failure was
traced to it.

### Cost of the hand-over

Each frame enters the context once and hands it back once. The steps use few WebGL calls and no
queries:

- **No queries.** Chrome answers queries of `PACK_ALIGNMENT`, `UNPACK_ROW_LENGTH`,
  `UNPACK_IMAGE_HEIGHT`, and `PACK_ROW_LENGTH` with a round trip to the GPU process, about 0.2 ms
  each. Firefox does the same for `ACTIVE_TEXTURE`, about 0.7 ms. Thus enter does not query the
  page's pixel-store state. `filly.js` reads it once, when it attaches to the context, and
  replaces `pixelStorei` on that context with a function that records the page's values. The
  core's calls skip that function. Hand back restores the page's values, because a page renderer
  can cache them: PIXI 6 sets its values before each upload, but Babylon.js caches
  `UNPACK_FLIP_Y_WEBGL`.
- **Only the units that the core used.** `web/gl_tracking.js` replaces Emscripten's
  `glBindTexture`, `glBindSampler`, `glActiveTexture`, and `glPixelStorei`. Only the core calls
  these, never the page. They record what the core binds and sets, and hand back undoes only
  that. A lit scene uses about 8 units.
- **Property reads.** Reads such as `target.width` or `renderer.stats` call no GL and queue no
  GL work, so they do not enter the context. A read after hand back thus does not take the
  context from the page again.

Filament's own reset still unbinds every unit at the next render: 6 calls per unit, with 32
units on ANGLE. The core reports at most 32 units to Filament, the limit of its samplers on
WebGL2, so drivers with more units do not add calls.

In a frame loop that rendered a lit box, with Chrome on the test laptop (provisional: measured
while other GPU work ran):

| | Before | After |
| --- | --- | --- |
| WebGL calls in enter | 30 (13 queries) | 14 (no queries) |
| WebGL calls in hand back | 224 | 44 |
| WebGL calls of a property read | 30 | 0 |
| Time of enter and hand back | 0.51 ms | 0.007 ms |

## Workarounds in the core

- **Buffer mapping.** WebGL2 cannot map buffers. Emscripten emulates mapping only with
  `FULL_ES3`, whose binding cache goes stale when the page calls WebGL itself. The web build
  links stubs that return no mapping; Filament then uses `glBufferSubData`.
- **`flushAndWait()`.** In Filament 1.77.1's single-threaded build, `Engine::flushAndWait()`
  runs only command buffers that an earlier flush submitted. Commands queued since then,
  including the `finish()` it adds, wait for the next flush. filly frees the source buffers of an
  asset after that wait, so the vertex uploads of the next render read freed memory, and models
  rendered garbled at random. filly flushes first (`detail::flush_and_wait()`); a browser test
  checks that no upload remains after a load.
- **RTTI.** Filament's WebAssembly libraries have no RTTI, and filly subclasses their types, so
  the core uses no `dynamic_pointer_cast`.
- **Exceptions.** The core throws C++ exceptions as on the desktop, as WebAssembly exceptions.
  `filly.js` turns them into `FillyError`, `AssetError`, `InteropError`, `BackendError`, or
  `RangeError`, with the message of the C++ exception.
- **Threads.** WebP images decode at once instead of in a `std::async` task.

## Shader compilation

A WebGL program compiles when the page first draws with it. A lit model in a new scene needs
several programs, and in Chrome its first render took about 200 ms; the page froze for 200 to
430 ms. Most of that time is in the GPU process, not in WebAssembly.

`renderer.prepare(scene)` moves this work off the frame:

- Filament compiles with `KHR_parallel_shader_compile` where the browser has it: it issues
  `glLinkProgram` and checks the result later. Chrome and Edge have the extension.
- The core compiles each material with `Material::compile()` and a variant mask from the scene:
  directional and dynamic lights, shadows, fog, and skinning. `Engine::compile(view)` would pick
  the exact variants, but in Filament 1.77.1 it takes the lighting specialization constants of the
  view's last render. Before a first render, it compiles programs without the directional light,
  and the first render compiles them again.
- Filament reports a program as compiled when its link starts. Its first draw then waits for the
  link. `prepare()` therefore also polls `COMPLETION_STATUS_KHR` of every program, which does not
  wait, and resolves when all of them are linked.

Between polls, `prepare()` returns control to the browser, so the page keeps drawing. In Chrome on
the test laptop, `prepare()` resolved after about 200 ms. The first render then took 33 to 40 ms,
and no frame was longer than 50 ms. A transparent scene still compiles one program at its first
render: Filament's blit of a translucent view, which `prepare()` cannot reach.

Firefox has no `KHR_parallel_shader_compile`. Filament then compiles at the first draw, and
`prepare()` resolves at once. The first render took 260 to 350 ms there, with or without
`prepare()`.

## Differences from the desktop module

- `gl_platform` is `"webgl"`. There is no offscreen renderer: every renderer uses the page's
  context.
- WebGL2 always encodes writes to sRGB attachments and has no sRGB texture views. Offscreen
  targets store RGBA8, and `output_path = "direct"` needs `encoding = "linear"`. The default exact
  path is unaffected.
- WebGL2 cannot report a texture's size or format, and it has no texture views. An imported
  target must have RGBA8 storage of the given size. A page texture that materials sample as sRGB
  must have `SRGB8_ALPHA8` storage; the desktop module makes an sRGB view of RGBA8 storage
  instead.
- WebGL2 has no texture swizzle, so single-channel runtime textures raise `RangeError`. Use 3 or
  4 channels.
- WebGL signals GPU fences only after control returns to the browser, so `OffscreenTarget.read()`
  is asynchronous: it starts the readback (`OffscreenTarget::begin_read()` in the core) and polls
  it on later turns of the event loop. `Target.read()` of a page-owned texture reads with
  `readPixels` in JavaScript and waits for the GPU; it is for tests.
- Host textures need no fences: `beginWrite()` and `endWrite()` keep only the bookkeeping.
- Files load from memory: `loadUrl()` fetches a `.glb`, or a `.gltf` and the buffers and images
  it names, and writes them to Emscripten's in-memory file system for the load.

## Output compared with the desktop

On one laptop (Intel Iris Xe; Chrome, Edge, and Firefox use ANGLE on Direct3D 11), a lit cube at
320 x 240 matched desktop filly within one 8-bit level on the 97% of pixels with a flat
neighborhood. Pixels on color edges differ by up to 79 levels, because the drivers rasterize
triangle edges differently. Other GPUs, browsers, and the sample assets of the
[reference comparison](../how-to/reference-comparison.md) are not measured yet.
