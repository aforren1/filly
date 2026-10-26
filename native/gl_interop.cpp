#include "gl_interop.h"
#include "renderer.h"
#include "engine_config.h"

#if defined(_WIN32)
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include <backend/platforms/PlatformWGL.h>
#else
#include <GL/gl.h>
#include <GL/glx.h>
#include <backend/platforms/PlatformGLX.h>
#include "egl_platform.h"
// libX11 declares this in X11/Xlibint.h, its header for extension implementations, which also
// defines macros that would leak into this file.
extern "C" int (*XESetCloseDisplay(Display* display, int extension, int (*hook)(Display*, XExtCodes*)))(
    Display*, XExtCodes*);
#endif
#include <filament/Engine.h>
#include <filament/Sync.h>

#include <chrono>
#include <condition_variable>
#include <deque>
#include <cstdlib>
#include <mutex>
#include <string_view>
#include <type_traits>
#include <vector>

namespace filly {
uintptr_t current_gl_context() {
#if defined(_WIN32)
    return reinterpret_cast<uintptr_t>(wglGetCurrentContext());
#else
    return reinterpret_cast<uintptr_t>(glXGetCurrentContext());
#endif
}

namespace detail {
namespace {
using StorageFn = void (APIENTRY*)(unsigned, int, unsigned, int, int);
constexpr unsigned SYNC_GPU_COMMANDS_COMPLETE = 0x9117;
constexpr unsigned ALREADY_SIGNALED = 0x911A;
constexpr unsigned CONDITION_SATISFIED = 0x911C;
constexpr unsigned RGBA8 = 0x8058;
constexpr unsigned SRGB8_ALPHA8 = 0x8C43;
constexpr unsigned TEXTURE_BINDING_2D = 0x8069;
constexpr unsigned TEXTURE_IMMUTABLE_FORMAT = 0x912F;
constexpr unsigned FRAMEBUFFER_SRGB = 0x8DB9;
using FenceFn = void* (APIENTRY*)(unsigned, unsigned);
using ViewFn = void (APIENTRY*)(unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned);
using WaitFn = void (APIENTRY*)(void*, unsigned, uint64_t);
using ClientWaitFn = unsigned (APIENTRY*)(void*, unsigned, uint64_t);
using DeleteFn = void (APIENTRY*)(void*);

template <class T> T procedure(const char* name) {
#if defined(_WIN32)
    auto ptr = wglGetProcAddress(name);
    const auto address = reinterpret_cast<intptr_t>(ptr);
    if (address == 0 || address == 1 || address == 2 || address == 3 || address == -1)
        throw InteropError(std::string("Host OpenGL context lacks ") + name);
#else
    auto ptr = glXGetProcAddressARB(reinterpret_cast<const GLubyte*>(name));
    if (!ptr) throw InteropError(std::string("Host OpenGL context lacks ") + name);
#endif
    return reinterpret_cast<T>(ptr);
}
void discard_errors() {
    for (int i = 0; i < 64 && glGetError() != GL_NO_ERROR; ++i) {}
}
}
}

using detail::discard_errors;
using detail::procedure;
using detail::RGBA8;
using detail::StorageFn;
using detail::TEXTURE_BINDING_2D;
uint32_t create_host_texture(int64_t width, int64_t height) {
    if (!current_gl_context()) throw InteropError("Make the host OpenGL context current first");
    if (width < 1 || height < 1 || width > 8192 || height > 8192)
        throw std::invalid_argument("width and height must be from 1 through 8192, got "
                                    + std::to_string(width) + "x" + std::to_string(height));
    static const auto storage = procedure<StorageFn>("glTexStorage2D");
    discard_errors();
    GLint previous = 0;
    GLuint texture = 0;
    glGetIntegerv(TEXTURE_BINDING_2D, &previous);
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    storage(GL_TEXTURE_2D, 1, RGBA8, GLint(width), GLint(height));
    // Without mipmaps, the default minification filter would leave the texture incomplete.
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, 0x812F);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, 0x812F);
    glBindTexture(GL_TEXTURE_2D, previous);
    if (glGetError() != GL_NO_ERROR) {
        glDeleteTextures(1, &texture);
        throw InteropError("Could not create the host RGBA8 texture");
    }
    return texture;
}
void delete_host_texture(uint32_t texture) {
    const GLuint name = texture;
    if (name && current_gl_context()) glDeleteTextures(1, &name);
}

namespace detail {
namespace {
// GL 2.0 to 3.3 entry points of the host quad. opengl32.dll exports only GL 1.1.
struct QuadGl {
    unsigned (APIENTRY* create_shader)(unsigned);
    void (APIENTRY* shader_source)(unsigned, int, const char* const*, const int*);
    void (APIENTRY* compile_shader)(unsigned);
    void (APIENTRY* get_shader)(unsigned, unsigned, int*);
    void (APIENTRY* shader_log)(unsigned, int, int*, char*);
    void (APIENTRY* delete_shader)(unsigned);
    unsigned (APIENTRY* create_program)();
    void (APIENTRY* attach_shader)(unsigned, unsigned);
    void (APIENTRY* link_program)(unsigned);
    void (APIENTRY* get_program)(unsigned, unsigned, int*);
    void (APIENTRY* program_log)(unsigned, int, int*, char*);
    void (APIENTRY* delete_program)(unsigned);
    void (APIENTRY* use_program)(unsigned);
    void (APIENTRY* gen_vertex_arrays)(int, unsigned*);
    void (APIENTRY* bind_vertex_array)(unsigned);
    void (APIENTRY* delete_vertex_arrays)(int, const unsigned*);
    void (APIENTRY* active_texture)(unsigned);
    void (APIENTRY* bind_sampler)(unsigned, unsigned);
    void (APIENTRY* blend_func)(unsigned, unsigned, unsigned, unsigned);
    void (APIENTRY* blend_equation)(unsigned, unsigned);
};
const QuadGl& quad_gl() {
    static const QuadGl gl = [] {
        QuadGl value;
        auto load = [](auto& slot, const char* name) { slot = procedure<std::decay_t<decltype(slot)>>(name); };
        load(value.create_shader, "glCreateShader");
        load(value.shader_source, "glShaderSource");
        load(value.compile_shader, "glCompileShader");
        load(value.get_shader, "glGetShaderiv");
        load(value.shader_log, "glGetShaderInfoLog");
        load(value.delete_shader, "glDeleteShader");
        load(value.create_program, "glCreateProgram");
        load(value.attach_shader, "glAttachShader");
        load(value.link_program, "glLinkProgram");
        load(value.get_program, "glGetProgramiv");
        load(value.program_log, "glGetProgramInfoLog");
        load(value.delete_program, "glDeleteProgram");
        load(value.use_program, "glUseProgram");
        load(value.gen_vertex_arrays, "glGenVertexArrays");
        load(value.bind_vertex_array, "glBindVertexArray");
        load(value.delete_vertex_arrays, "glDeleteVertexArrays");
        load(value.active_texture, "glActiveTexture");
        load(value.bind_sampler, "glBindSampler");
        load(value.blend_func, "glBlendFuncSeparate");
        load(value.blend_equation, "glBlendEquationSeparate");
        return value;
    }();
    return gl;
}
constexpr unsigned VERTEX_SHADER = 0x8B31, FRAGMENT_SHADER = 0x8B30, COMPILE_STATUS = 0x8B81,
                   LINK_STATUS = 0x8B82, CURRENT_PROGRAM = 0x8B8D, VERTEX_ARRAY_BINDING = 0x85B5,
                   ACTIVE_TEXTURE = 0x84E0, TEXTURE0 = 0x84C0, SAMPLER_BINDING = 0x8919,
                   BLEND_SRC_RGB = 0x80C9, BLEND_DST_RGB = 0x80C8, BLEND_SRC_ALPHA = 0x80CB,
                   BLEND_DST_ALPHA = 0x80CA, BLEND_EQUATION_RGB = 0x8009, BLEND_EQUATION_ALPHA = 0x883D,
                   FUNC_ADD = 0x8006;
// The quad covers the viewport; the texture's row 0 is its bottom row, as in OpenGL.
constexpr const char* QUAD_VERTEX = R"(#version 330 core
out vec2 uv;
void main() {
    uv = vec2(gl_VertexID & 1, gl_VertexID >> 1);
    gl_Position = vec4(uv * 2.0 - 1.0, 0.0, 1.0);
}
)";
constexpr const char* QUAD_FRAGMENT = R"(#version 330 core
uniform sampler2D image;
in vec2 uv;
layout(location = 0) out vec4 color;
void main() {
    color = texture(image, uv);
}
)";
unsigned compile(const QuadGl& gl, unsigned stage, const char* source) {
    const unsigned shader = gl.create_shader(stage);
    gl.shader_source(shader, 1, &source, nullptr);
    gl.compile_shader(shader);
    int status = 0;
    gl.get_shader(shader, COMPILE_STATUS, &status);
    if (!status) {
        char log[1024] = {};
        gl.shader_log(shader, sizeof(log), nullptr, log);
        gl.delete_shader(shader);
        throw InteropError(std::string("Could not compile the host quad shader: ") + log);
    }
    return shader;
}
}
}

HostQuad create_host_quad() {
    if (!current_gl_context()) throw InteropError("Make the host OpenGL context current first");
    const auto& gl = detail::quad_gl();
    const unsigned vertex = detail::compile(gl, detail::VERTEX_SHADER, detail::QUAD_VERTEX);
    unsigned fragment = 0;
    try { fragment = detail::compile(gl, detail::FRAGMENT_SHADER, detail::QUAD_FRAGMENT); }
    catch (...) { gl.delete_shader(vertex); throw; }
    HostQuad quad;
    quad.program = gl.create_program();
    gl.attach_shader(quad.program, vertex);
    gl.attach_shader(quad.program, fragment);
    gl.link_program(quad.program);
    // The program keeps the attached shaders until it is deleted.
    gl.delete_shader(vertex);
    gl.delete_shader(fragment);
    int status = 0;
    gl.get_program(quad.program, detail::LINK_STATUS, &status);
    if (!status) {
        char log[1024] = {};
        gl.program_log(quad.program, sizeof(log), nullptr, log);
        gl.delete_program(quad.program);
        throw InteropError(std::string("Could not link the host quad program: ") + log);
    }
    // Core profiles draw only with a vertex array bound; the quad needs no attributes.
    gl.gen_vertex_arrays(1, &quad.vertex_array);
    return quad;
}

void draw_host_quad(const HostQuad& quad, uint32_t texture, int64_t x, int64_t y, int64_t width,
                    int64_t height, bool blend) {
    if (!quad.program || !quad.vertex_array) throw InteropError("The host quad is deleted");
    const auto& gl = detail::quad_gl();
    GLint program = 0, vertex_array = 0, active = 0, bound = 0, sampler = 0, viewport[4] = {};
    GLint functions[4] = {}, equations[2] = {};
    glGetIntegerv(detail::CURRENT_PROGRAM, &program);
    glGetIntegerv(detail::VERTEX_ARRAY_BINDING, &vertex_array);
    glGetIntegerv(detail::ACTIVE_TEXTURE, &active);
    gl.active_texture(detail::TEXTURE0);
    glGetIntegerv(TEXTURE_BINDING_2D, &bound);
    glGetIntegerv(detail::SAMPLER_BINDING, &sampler);
    glGetIntegerv(GL_VIEWPORT, viewport);
    const GLenum switches[] = {GL_BLEND, GL_DEPTH_TEST, GL_STENCIL_TEST, GL_CULL_FACE};
    GLboolean enabled[4];
    for (int i = 0; i < 4; ++i) enabled[i] = glIsEnabled(switches[i]);
    if (blend) {
        const GLenum names[] = {detail::BLEND_SRC_RGB, detail::BLEND_DST_RGB, detail::BLEND_SRC_ALPHA,
                                detail::BLEND_DST_ALPHA};
        for (int i = 0; i < 4; ++i) glGetIntegerv(names[i], &functions[i]);
        glGetIntegerv(detail::BLEND_EQUATION_RGB, &equations[0]);
        glGetIntegerv(detail::BLEND_EQUATION_ALPHA, &equations[1]);
        glEnable(GL_BLEND);
        // Filament writes premultiplied color: source-over is ONE, ONE_MINUS_SRC_ALPHA.
        gl.blend_func(GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        gl.blend_equation(detail::FUNC_ADD, detail::FUNC_ADD);
    } else {
        glDisable(GL_BLEND);
    }
    for (int i = 1; i < 4; ++i) glDisable(switches[i]);
    glBindTexture(GL_TEXTURE_2D, texture);
    // A host sampler on unit 0 would override the texture's own filtering and wrapping.
    gl.bind_sampler(0, 0);
    glViewport(GLint(x), GLint(y), GLsizei(width), GLsizei(height));
    gl.use_program(quad.program);
    gl.bind_vertex_array(quad.vertex_array);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);

    gl.bind_vertex_array(vertex_array);
    gl.use_program(program);
    glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
    gl.bind_sampler(0, sampler);
    glBindTexture(GL_TEXTURE_2D, bound);
    gl.active_texture(active);
    for (int i = 0; i < 4; ++i) {
        if (enabled[i]) glEnable(switches[i]);
        else glDisable(switches[i]);
    }
    if (blend) {
        gl.blend_func(functions[0], functions[1], functions[2], functions[3]);
        gl.blend_equation(equations[0], equations[1]);
    }
}

void delete_host_quad(const HostQuad& quad) {
    if (!current_gl_context()) return;
    const auto& gl = detail::quad_gl();
    if (quad.vertex_array) gl.delete_vertex_arrays(1, &quad.vertex_array);
    if (quad.program) gl.delete_program(quad.program);
}

namespace detail {
struct Signal {
    std::mutex mutex;
    std::condition_variable ready;
    bool published = false;
    void* handle = nullptr;
};

// Host-side GL entry points and the requests that Filament's driver thread applies in
// command-stream order. It outlives the platform that calls into it.
struct SyncQueue {
    // srgb is -1 to leave GL_FRAMEBUFFER_SRGB unchanged, else its new state.
    struct Request { void* incoming = nullptr; std::shared_ptr<Signal> outgoing; int srgb = -1; };
    struct NativeSync : filament::backend::Platform::Sync { void* handle = nullptr; };
    uintptr_t context;
    // Offscreen engines have no host context to resolve these from and never use them.
    FenceFn fence = nullptr;
    WaitFn wait = nullptr;
    ClientWaitFn client_wait = nullptr;
    DeleteFn delete_sync = nullptr;
    ViewFn texture_view = nullptr;
    std::mutex mutex;
    std::deque<Request> requests;

    explicit SyncQueue(uintptr_t context) : context(context) {
        if (!context) return;
        fence = procedure<FenceFn>("glFenceSync");
        wait = procedure<WaitFn>("glWaitSync");
        client_wait = procedure<ClientWaitFn>("glClientWaitSync");
        delete_sync = procedure<DeleteFn>("glDeleteSync");
        texture_view = procedure<ViewFn>("glTextureView");
    }
    void queue(Request request) {
        std::lock_guard lock(mutex);
        requests.push_back(std::move(request));
    }
    // Filament calls this on its driver thread, in command-stream order.
    filament::backend::Platform::Sync* create_sync() noexcept {
        Request request{};
        {
            std::lock_guard lock(mutex);
            if (!requests.empty()) {
                request = std::move(requests.front());
                requests.pop_front();
            }
        }
        if (request.srgb >= 0) {
            if (request.srgb) glEnable(FRAMEBUFFER_SRGB);
            else glDisable(FRAMEBUFFER_SRGB);
        }
        if (request.incoming) {
            wait(request.incoming, 0, UINT64_MAX);
            delete_sync(request.incoming);
        }
        auto* result = new NativeSync();
        if (request.outgoing) {
            result->handle = fence(SYNC_GPU_COMMANDS_COMPLETE, 0);
            // A different context cannot wait on commands still buffered by the producer.
            glFlush();
            {
                std::lock_guard lock(request.outgoing->mutex);
                request.outgoing->handle = result->handle;
                request.outgoing->published = true;
            }
            request.outgoing->ready.notify_one();
        }
        return result;
    }
    void destroy_sync(filament::backend::Platform::Sync* value) noexcept {
        auto* sync = static_cast<NativeSync*>(value);
        if (sync->handle) delete_sync(sync->handle);
        delete sync;
    }
};

// Adds the synchronization hooks to any of Filament's OpenGL platforms.
template <class Base> struct SyncPlatform final : Base {
    template <class... Args>
    explicit SyncPlatform(SyncQueue& queue, Args&&... args) : Base(std::forward<Args>(args)...), queue(queue) {}
    filament::backend::Platform::Sync* createSync() noexcept override { return queue.create_sync(); }
    void destroySync(filament::backend::Platform::Sync* value) noexcept override { queue.destroy_sync(value); }
    // filly never presents: its only swap chain is the hidden 1x1 chain that drives Filament's
    // per-frame bookkeeping. SwapBuffers on it waits for the vertical blank on NVIDIA, which
    // pinned every frame to the refresh period (16.2 ms of GPU frame time for 0.37 ms of work).
    // A flush submits the frame the same way without waiting for the display.
    void commit(filament::backend::Platform::SwapChain*) noexcept override { glFlush(); }
    SyncQueue& queue;
};

struct GlInterop::Impl {
    SyncQueue sync;
    std::unique_ptr<filament::backend::Platform> platform;
    const char* name = nullptr;
#if defined(__linux__)
    // The host's X display connection, for shared engines.
    Display* display = nullptr;
#endif
    explicit Impl(uintptr_t context) : sync(context) {}
};

#if defined(__linux__)
namespace {
// Close handlers of shared engines, by host display connection. Filament's shared GLX context
// uses the host's connection (see build_filament_linux.py), and Mesa destroys every context of
// a connection when it closes: Psychtoolbox closes its connection when its Screen MEX file
// unloads (`clear mex`). Xlib runs close hooks newest first, so these run before GLX's own,
// while Filament's context is still usable.
struct DisplayHook {
    Display* display;
    int extension;
    const void* owner;
    std::function<void()> callback;
};
std::mutex display_hooks_mutex;
std::vector<DisplayHook> display_hooks;

int close_display(Display* display, XExtCodes* codes) {
    std::vector<std::function<void()>> callbacks;
    {
        std::lock_guard lock(display_hooks_mutex);
        std::erase_if(display_hooks, [&](DisplayHook& hook) {
            if (hook.display != display || hook.extension != codes->extension) return false;
            callbacks.push_back(std::move(hook.callback));
            return true;
        });
    }
    // Outside the lock: a callback closes a renderer, whose GlInterop removes its own hooks.
    for (auto& callback : callbacks) {
        try { callback(); } catch (...) {}
    }
    return 0;
}

bool x_display_available() {
    const char* name = std::getenv("DISPLAY");
    if (!name || !*name) return false;
    Display* display = XOpenDisplay(nullptr);
    if (!display) return false;
    XCloseDisplay(display);
    return true;
}
}

// Offscreen engines prefer a GPU through EGL, which needs no X server. A software EGL
// renderer is used only without an X display, because GLX on WSLg reaches the GPU while
// Mesa's EGL platforms there may offer only llvmpipe. FILLY_OFFSCREEN_GL=egl or glx
// overrides the choice.
using PlatformPtr = std::unique_ptr<filament::backend::Platform>;
PlatformPtr select_offscreen(SyncQueue& sync, const char*& name) {
    const char* forced = std::getenv("FILLY_OFFSCREEN_GL");
    const std::string_view choice = forced ? forced : "";
    if (!choice.empty() && choice != "egl" && choice != "glx")
        throw BackendError("FILLY_OFFSCREEN_GL must be 'egl' or 'glx', got '" + std::string(choice) + "'");
    EglProbe probe;
    if (choice != "glx") {
        probe = probe_egl();
        if (probe.device && (choice == "egl" || !probe.software || !x_display_available())) {
            name = "egl";
            return std::make_unique<SyncPlatform<EglPlatform>>(sync, std::move(probe.device));
        }
        if (choice == "egl") throw BackendError("EGL offscreen rendering is not available: " + probe.reason);
    }
    // PlatformGLX exits the process when it cannot open a display, so check again just before.
    // An X server can refuse a connection right after accepting one: Xvfb did once in CI
    // (2026-10-13). A software EGL device from the probe is a better answer than an error then.
    if (!x_display_available()) {
        if (probe.device) {
            name = "egl";
            return std::make_unique<SyncPlatform<EglPlatform>>(sync, std::move(probe.device));
        }
        if (choice == "glx") throw BackendError("FILLY_OFFSCREEN_GL=glx needs an X display; DISPLAY is unset or unreachable");
        throw BackendError("Offscreen rendering needs an EGL device or an X display. EGL: " + probe.reason);
    }
    name = "glx";
    return std::make_unique<SyncPlatform<filament::backend::PlatformGLX>>(sync, nullptr);
}
#endif

GlInterop::GlInterop(uintptr_t context) {
    if (context && current_gl_context() != context)
        throw InteropError("shared_context must be the current host OpenGL context");
    impl_ = std::make_unique<Impl>(context);
#if defined(_WIN32)
    impl_->platform = std::make_unique<SyncPlatform<filament::backend::PlatformWGL>>(impl_->sync);
    impl_->name = "wgl";
#else
    if (context) {
        impl_->display = glXGetCurrentDisplay();
        impl_->platform = std::make_unique<SyncPlatform<filament::backend::PlatformGLX>>(
            impl_->sync, impl_->display);
        impl_->name = "glx";
    } else {
        impl_->platform = select_offscreen(impl_->sync, impl_->name);
    }
#endif
}
GlInterop::~GlInterop() {
#if defined(__linux__)
    std::lock_guard lock(display_hooks_mutex);
    std::erase_if(display_hooks, [this](const DisplayHook& hook) { return hook.owner == this; });
#endif
}
void GlInterop::on_host_display_close(std::function<void()> callback) {
#if defined(__linux__)
    if (!shared() || !impl_->display) return;
    auto* codes = XAddExtension(impl_->display);
    if (!codes) throw InteropError("Could not watch the host X display connection");
    XESetCloseDisplay(impl_->display, codes->extension, close_display);
    std::lock_guard lock(display_hooks_mutex);
    display_hooks.push_back({impl_->display, codes->extension, this, std::move(callback)});
#else
    (void)callback;
#endif
}
filament::backend::Platform* GlInterop::platform() { return impl_->platform.get(); }
const char* GlInterop::platform_name() const { return impl_->name; }
bool GlInterop::shared() const { return impl_->sync.context != 0; }
filament::Engine* GlInterop::create_engine() {
    const auto config = engine_config();
    if (!shared())
        return filament::Engine::Builder().backend(filament::Engine::Backend::OPENGL)
            .platform(platform()).config(&config).build();
    require_host();
#if defined(_WIN32)
    auto dc = wglGetCurrentDC();
    auto context = reinterpret_cast<HGLRC>(impl_->sync.context);
    // Filament creates its shared context on its driver thread. wglCreateContextAttribsARB fails
    // while the host context is current on this thread: ERROR_BUSY (170) on Intel, 0xC00720DD on NVIDIA.
    if (!wglMakeCurrent(nullptr, nullptr)) throw InteropError("Could not release the host context for sharing");
#else
    auto* display = glXGetCurrentDisplay();
    auto draw = glXGetCurrentDrawable();
    auto read = glXGetCurrentReadDrawable();
    auto context = reinterpret_cast<GLXContext>(impl_->sync.context);
    if (!glXMakeContextCurrent(display, None, None, nullptr))
        throw InteropError("Could not release the host GLX context for sharing");
#endif
    auto restore = [&] {
#if defined(_WIN32)
        return bool(wglMakeCurrent(dc, context));
#else
        return bool(glXMakeContextCurrent(display, draw, read, context));
#endif
    };
    filament::Engine* engine = nullptr;
    try {
        engine = filament::Engine::Builder().backend(filament::Engine::Backend::OPENGL)
            .platform(platform()).sharedContext(context).config(&config).build();
    } catch (...) {
        restore();
        throw;
    }
    if (!restore()) {
        if (engine) filament::Engine::destroy(&engine);
        throw InteropError("Could not restore the host context after engine creation");
    }
    return engine;
}
void GlInterop::require_host() const {
    if (current_gl_context() != impl_->sync.context)
        throw InteropError("Make the original host OpenGL context current first");
}
bool GlInterop::host_current() const { return current_gl_context() == impl_->sync.context; }
uintptr_t GlInterop::context() const { return impl_->sync.context; }
uint32_t GlInterop::import_texture(uint32_t texture, uint32_t width, uint32_t height, SrgbView view) const {
    require_host();
    if (!texture || !glIsTexture(texture)) throw InteropError("Texture does not exist in the host context");
    // The bind below is the only portable target test; Intel rejects the OpenGL 4.5
    // GL_TEXTURE_TARGET query. Errors the host left pending would read as a bind failure, so
    // they are discarded first. A host that checks its own errors has none pending.
    discard_errors();
    GLint previous = 0, actual_width = 0, actual_height = 0, format = 0, immutable = 0;
    glGetIntegerv(TEXTURE_BINDING_2D, &previous);
    glBindTexture(GL_TEXTURE_2D, texture);
    const auto error = glGetError();
    if (error == GL_NO_ERROR) {
        glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &actual_width);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &actual_height);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT, &format);
        glGetTexParameteriv(GL_TEXTURE_2D, TEXTURE_IMMUTABLE_FORMAT, &immutable);
    }
    glBindTexture(GL_TEXTURE_2D, previous);
    if (error != GL_NO_ERROR || actual_width != GLint(width) || actual_height != GLint(height)
            || format != RGBA8)
        throw InteropError("Import requires a GL_TEXTURE_2D with matching dimensions and GL_RGBA8 storage");
    if (view == SrgbView::REQUIRED && !immutable)
        throw InteropError("An sRGB view of the host texture needs immutable storage from "
                           "glTexStorage2D; this texture was allocated with glTexImage2D");
    if (view == SrgbView::NONE || !immutable) return texture;
    // Filament renders into or samples an sRGB view of the same storage, so the GPU converts
    // while the host still sees plain RGBA8 values.
    GLuint name = 0;
    glGenTextures(1, &name);
    impl_->sync.texture_view(name, GL_TEXTURE_2D, texture, SRGB8_ALPHA8, 0, 1, 0, 1);
    if (glGetError() != GL_NO_ERROR || !glIsTexture(name)) {
        glDeleteTextures(1, &name);
        throw InteropError("Could not create an sRGB view of the host texture");
    }
    return name;
}
void GlInterop::delete_host_texture(uint32_t texture) const {
    const GLuint name = texture;
    if (name) glDeleteTextures(1, &name);
}
void* GlInterop::host_fence() {
    require_host();
    // Mesa does not flush buffered immediate-mode vertices into a fence (glFenceSync skips
    // FLUSH_VERTICES), so a host draw with glBegin/glEnd (pyglet, Psychtoolbox's DrawTexture)
    // would land after it. Zink exposes this; llvmpipe's submission order hides it.
    glFlush();
    void* result = impl_->sync.fence(SYNC_GPU_COMMANDS_COMPLETE, 0);
    if (!result) throw InteropError("Could not create host OpenGL fence");
    glFlush();
    return result;
}
void GlInterop::delete_host_fence(void* fence) {
    if (fence) impl_->sync.delete_sync(fence);
}
void GlInterop::enqueue_wait(filament::Engine& engine, void* fence) {
    if (!fence) return;
    SyncQueue::Request request;
    request.incoming = fence;
    impl_->sync.queue(std::move(request));
    auto* gate = engine.createSync();
    engine.destroy(gate);
}
SyncPoint GlInterop::signal(filament::Engine& engine) {
    auto ticket = std::make_shared<Signal>();
    SyncQueue::Request request;
    request.outgoing = ticket;
    impl_->sync.queue(std::move(request));
    return {engine.createSync(), std::move(ticket)};
}
void GlInterop::wait_on_host(const SyncPoint& point) {
    require_host();
    std::unique_lock lock(point.signal->mutex);
    if (!point.signal->ready.wait_for(lock, std::chrono::seconds(10), [&] { return point.signal->published; }))
        throw InteropError("Timed out waiting for Filament to publish its GL fence");
    if (!point.signal->handle) throw InteropError("Filament could not create its GL fence");
    // This queues a GPU dependency; it does not wait for GPU completion on the CPU.
    impl_->sync.wait(point.signal->handle, 0, UINT64_MAX);
}
void GlInterop::destroy(filament::Engine& engine, SyncPoint& point) {
    // The last copy of a point destroys its Filament sync, and with it the GL fence.
    if (point.sync && point.signal.use_count() == 1) engine.destroy(point.sync);
    point = {};
}
void GlInterop::set_srgb_writes(filament::Engine& engine, bool value) {
    SyncQueue::Request request;
    request.srgb = value;
    impl_->sync.queue(std::move(request));
    auto* gate = engine.createSync();
    engine.destroy(gate);
}
void GlInterop::finish_host() {
    auto* fence = host_fence();
    auto status = impl_->sync.client_wait(fence, 0, 10000000000ULL);
    impl_->sync.delete_sync(fence);
    if (status != ALREADY_SIGNALED && status != CONDITION_SATISFIED)
        throw InteropError("Host GPU work did not complete during close");
}
}
}
