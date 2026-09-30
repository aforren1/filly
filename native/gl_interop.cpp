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
#endif
#include <filament/Engine.h>
#include <filament/Sync.h>

#include <chrono>
#include <condition_variable>
#include <deque>
#include <cstdlib>
#include <mutex>
#include <string_view>

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
    explicit Impl(uintptr_t context) : sync(context) {}
};

#if defined(__linux__)
namespace {
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
    // PlatformGLX exits the process when it cannot open a display.
    if (!x_display_available()) {
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
        impl_->platform = std::make_unique<SyncPlatform<filament::backend::PlatformGLX>>(
            impl_->sync, glXGetCurrentDisplay());
        impl_->name = "glx";
    } else {
        impl_->platform = select_offscreen(impl_->sync, impl_->name);
    }
#endif
}
GlInterop::~GlInterop() = default;
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
    if (point.sync) engine.destroy(point.sync);
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
