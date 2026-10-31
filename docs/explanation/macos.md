# macOS

filly runs on macOS with Apple silicon through OpenGL 4.1, which Apple implements on Metal.
This page explains how the macOS build differs from the Windows and Linux builds and why. For
the build steps, see [build on macOS](../how-to/build.md#build-on-macos). For PsychoPy, see
[use PsychoPy on macOS](../how-to/psychopy.md#use-psychopy-on-macos).

## filly's own CGL platform

Filament's OpenGL platform for macOS, `PlatformCocoaGL`, is not used. For each headless swap
chain, it creates a hidden `NSWindow`. It creates that window, and later updates its context,
with `dispatch_sync` to the main queue from Filament's driver thread. A Python script that
waits for the engine on the main thread then stops, because the main thread cannot run the
queued block. A session without a window server also cannot create the window.

filly never presents a frame: every render goes to a framebuffer object, and the swap chain only
drives Filament's frame bookkeeping. Thus `core/cgl_platform.cpp` uses plain CGL, as
`core/egl_platform.cpp` uses plain EGL on Linux:

- The context has no drawable. Framebuffer 0 is incomplete, and Filament never draws to it.
- An offscreen engine chooses a core profile pixel format: a GPU first, then any renderer.
- A shared engine creates its context from the host context's own pixel format. CGL shares
  objects only between contexts whose pixel formats have the same renderers, and the host's
  format is the one certain match.
- No call uses the main thread or AppKit.

`Renderer.gl_platform` reports `"cgl"`.

## Sharing needs a core profile

Filament needs a core profile context. CGL does not share objects between a core profile
context and a legacy profile (OpenGL 2.1) context. A test program got this result:

| Share context | New context | `CGLCreateContext` |
| --- | --- | --- |
| Legacy | Core 4.1 | 10009, `kCGLBadMatch` ("invalid share context") |
| Legacy | Legacy | 0, no error |

Thus a host must have a core profile context to share with filly. pyglet 2 makes one by
default. PsychoPy and Psychtoolbox make legacy contexts, because they draw with fixed-function
OpenGL. PsychoPy windows have a legacy context also with the pyglet 2 of PsychoPy's development
branch (2027.1.0): PsychoPy does not request a profile, and 12 modules in `psychopy.visual` call
functions such as `glBegin` and `glMatrixMode`, which a core profile does not have.

## OpenGL 4.1 limits

macOS has OpenGL 4.1 and no extensions above it that filly uses:

- No `glTextureView` (OpenGL 4.3 or `ARB_texture_view`). filly cannot make an sRGB view of a
  host texture, so a host texture cannot be sampled as sRGB, and an imported target cannot take
  `output_path="direct"` with `encoding="srgb"`. filly loads `glTextureView` as optional on
  every platform and reports these cases with `InteropError`.
- Legacy profile contexts have no `glTexStorage2D`. `create_host_texture` uses `glTexImage2D`
  there. Such textures reach Filament only through a copy target, which needs no immutable
  storage.

## Copies for legacy hosts

For a PsychoPy window, filly renders with an offscreen engine and copies each frame into a
texture in the window's context. PsychoPy's `ImageStim` then draws that texture as on Windows
and Linux. Two transfers do the copy.

### IOSurface

An IOSurface is GPU memory that more than one context can bind, also contexts that share
nothing. `CGLTexImageIOSurface2D` binds it as a `GL_TEXTURE_RECTANGLE` in both profiles.

1. Filament renders into its own 2D texture. Filament cannot render into a rectangle texture.
2. Filament's driver thread copies the texture into its rectangle texture on the surface with
   `glBlitFramebuffer`, then sets a fence and flushes.
3. The window's context copies its rectangle texture on the same surface into the window's 2D
   texture with `glBlitFramebuffer`. `ImageStim` samples only 2D textures.

The surface has the `BGRA` pixel format. Both contexts bind it as `GL_RGBA` with `GL_BGRA` and
`GL_UNSIGNED_INT_8_8_8_8_REV`, which CGL accepts in both profiles. Both contexts address texels
as RGBA, so the byte order in memory does not reach either side, and the rows have the same
order on both sides. A test program read back the same bytes on both sides.

The copies disable `GL_FRAMEBUFFER_SRGB` and the scissor test, and restore all state that they
change, because Filament and PsychoPy cache GL state.

### Readback

Filament's context reads the frame into a pixel buffer. The first draw maps the buffer, copies
the rows into memory that the target allocated once, and uploads them with `glTexSubImage2D`.
filly uses this transfer when it cannot create the IOSurface objects.

### Synchronization

The contexts share no objects, so no OpenGL fence can go from one context to the other. The CPU
synchronizes them:

1. The first draw of a frame waits until Filament's driver thread has issued the copy into the
   surface and the GPU has completed it.
2. The window's context copies the surface into its texture and sets a fence.
3. The next `render()` waits for that fence before Filament writes the surface again.

The tests showed what each wait prevents:

| Wait in step 1 | Result in 27 IOSurface test cases |
| --- | --- |
| None | 11 or 12 cases showed an earlier frame. The window's context read the surface before the driver thread had issued the copy. |
| Driver thread only, not the GPU | All passed, in 5 of 5 runs. |
| Driver thread and GPU | All passed. |

Apple's driver thus ordered the GPU work of the two contexts, but Apple does not document this
order for OpenGL. filly waits for the GPU too.

### Cost per frame

Measured on an Apple M2 with macOS 27.0.1, PsychoPy 2027.1.0, and pyglet 2.1.19, with the
wooden horse sample and its animation. Each value is the median of 300 frames after 60 warmup
frames, in milliseconds.

The window's swap interval stays at the display refresh (60 Hz here), also with
`set_vsync(False)`. To measure without it, each frame ends with `glFinish()` in the window's
context instead of `win.flip()`. "Offscreen" ends with `renderer.finish()` and draws nothing.

| Size | Transfer | `render()` | First draw | Frame, without flip |
| --- | --- | --- | --- | --- |
| 512 x 512 | Offscreen (reference) | 0.02 | n/a | 0.42 |
| 512 x 512 | `iosurface` | 0.03 | 0.49 | 0.78 |
| 512 x 512 | `readback` | 0.04 | 0.61 | 0.91 |
| 1920 x 1080 | Offscreen (reference) | 0.03 | n/a | 0.83 |
| 1920 x 1080 | `iosurface` | 0.03 | 1.01 | 1.73 |
| 1920 x 1080 | `readback` | 0.04 | 2.60 | 3.18 |

The first draw includes the wait for Filament's GPU work (0.15 to 0.4 ms here), the copy, and
the draw. The GPU wait of step 1 costs about 0.3 ms at 512 x 512 and 0.8 ms at 1920 x 1080.
With `win.flip()` at 60 Hz, the first draw took 1.1 ms (`iosurface`) and 1.3 ms (`readback`)
at 512 x 512, and 2.0 ms and 4.2 ms at 1920 x 1080. No frame was missed.

### Allocations per frame

A library that interposes libc++'s `operator new` counted the calls per frame by calling
image, over 200 frames of a 256 x 256 target in a PsychoPy window:

| Calls per frame | `iosurface` | `readback` |
| --- | --- | --- |
| filly's module, `render()` and copy | 86 | 89 |
| Of these, in filly's own code | 0 | 3 |
| Apple's OpenGL and Metal drivers | about 125 | about 120 |

Filament makes the other calls in filly's module, mostly in its frame graph. The copy target
keeps its objects for its lifetime, and its tasks for the driver thread fit in the inline
storage of `std::function`. The three readback calls are in the pixel buffer tasks, which the
`Readback` API on every platform also uses.

## Third-party issues

These issues are not in filly, but they affect filly's users and tests on macOS:

- **PsychoPy display link.** PsychoPy's `flip()` waits for a callback from the window's display
  link, without a time limit. A hidden window never sends the callback, so `flip()` does not
  return. On macOS 12, the display link API is missing, and the same wait does not return
  ([psychopy#7870](https://github.com/psychopy/psychopy/issues/7870)).
- **pyglet 1.5 windows.** With pyglet 1.5.27, which PsychoPy releases use on macOS, a window
  can fail to open with `ObjCInstance b'PygletDelegate' has no attribute b'initWithAttributes_'`
  after an earlier window in the same process closed. With PsychoPy 2026.1.3:

  | Windows in one process | Failed to open |
  | --- | --- |
  | One window per process, 20 processes | 0 of 20 |
  | Close each window, then open the next | 14 of 30 |
  | The same, with 0.5 s between windows | 10 of 30 |
  | Keep all windows open | 1 of 30 |

  The rate of creation and `useFBO` did not change the result. Plain pyglet 1.5.27, without
  PsychoPy, failed 1 of 40 times. pyglet 1.5 keeps its Objective-C wrappers in a dictionary by
  address, which can return the wrapper of a freed object when a new object gets the same
  address. pyglet 2 keeps them in a `WeakValueDictionary`, and all 40 windows opened. An
  experiment with one window per process is not affected.
- **pyglet 2 second window.** pyglet 2.1.19 stops with a segmentation fault when it creates a
  second window next to a PsychoPy window (`create_context` in `pyglet/gl/cocoa.py`), also
  without filly.
- **pyglet 1.4.** pyglet 1.4.11 does not load on Apple silicon
  (`class_getMethodImplementation_stret`).

## Other differences

- The Filament release archive for macOS has arm64 libraries only, so Intel Macs are not
  supported. The libraries are built for macOS 11.0, so wheels target macOS 11.0.
- Apple's C++ library has floating-point `std::from_chars` only from macOS 26 and
  `std::to_chars` from macOS 13.3. `core/json.cpp` uses `strtod_l` and `snprintf_l` with the C
  locale on macOS. Apple's `strtod_l` sets `ERANGE` also for subnormal and underflowing
  results, so only an infinite result counts as out of range.
- The release SDK is built without C++ RTTI, so the core library is compiled with `-fno-rtti`,
  as in the web build. The Python bindings keep RTTI, because nanobind needs it.
- Filament's backend library also contains its Metal and Cocoa platforms, so the module imports
  AppKit, Metal, QuartzCore, and CoreVideo, although filly renders with OpenGL.
