#include "cgl_platform.h"

#if defined(__APPLE__)
#define GL_SILENCE_DEPRECATION
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>

#include <algorithm>
#include <new>

// The macOS SDK archive ships libbluegl.a without its header.
namespace bluegl {
int bind();
void unbind();
}

namespace filly::detail {
namespace {
struct HeadlessChain : filament::backend::Platform::SwapChain {};

CGLContextObj as_context(void* value) { return static_cast<CGLContextObj>(value); }
CGLPixelFormatObj as_format(void* value) { return static_cast<CGLPixelFormatObj>(value); }

CGLPixelFormatObj offscreen_format() {
    // Offline renderers are GPUs that drive no display, as in a Mac mini without one. The
    // second set admits Apple's software renderer.
    const CGLPixelFormatAttribute gpu[] = {
        kCGLPFAOpenGLProfile, CGLPixelFormatAttribute(kCGLOGLPVersion_GL4_Core),
        kCGLPFAAccelerated, kCGLPFAAllowOfflineRenderers, CGLPixelFormatAttribute(0)};
    const CGLPixelFormatAttribute any[] = {
        kCGLPFAOpenGLProfile, CGLPixelFormatAttribute(kCGLOGLPVersion_GL4_Core),
        kCGLPFAAllowOfflineRenderers, CGLPixelFormatAttribute(0)};
    for (const auto* attributes : {gpu, any}) {
        CGLPixelFormatObj format = nullptr;
        GLint count = 0;
        if (CGLChoosePixelFormat(attributes, &format, &count) == kCGLNoError && format) return format;
    }
    return nullptr;
}
}

std::string cgl_share_problem(void* host) {
    CGLPixelFormatObj format = CGLGetPixelFormat(as_context(host));
    GLint profile = 0;
    if (!format || CGLDescribePixelFormat(format, 0, kCGLPFAOpenGLProfile, &profile) != kCGLNoError)
        return "Could not read the pixel format of the host CGL context";
    if (profile == kCGLOGLPVersion_Legacy)
        return "The host OpenGL context has the legacy profile (OpenGL 2.1). Sharing with filly "
               "needs a core profile context (OpenGL 3.2 or later), for example a pyglet window "
               "with pyglet 2 or a Config with major_version=3 and forward_compatible=True";
    return {};
}

CglPlatform::~CglPlatform() noexcept {
    if (context_) CGLReleaseContext(as_context(context_));
    if (format_) CGLReleasePixelFormat(as_format(format_));
}

filament::backend::Driver* CglPlatform::createDriver(void* sharedContext, const DriverConfig& driverConfig) {
    // The host's own pixel format: CGL shares objects only between contexts whose formats have
    // the same renderers, and the host context's format is the one certain match.
    CGLPixelFormatObj format = sharedContext ? CGLRetainPixelFormat(CGLGetPixelFormat(as_context(sharedContext)))
                                             : offscreen_format();
    if (!format) return nullptr;
    CGLContextObj context = nullptr;
    if (CGLCreateContext(format, as_context(sharedContext), &context) != kCGLNoError || !context) {
        CGLReleasePixelFormat(format);
        return nullptr;
    }
    format_ = format;
    context_ = context;
    // This is the driver thread.
    if (CGLSetCurrentContext(context) != kCGLNoError || bluegl::bind()) {
        CGLSetCurrentContext(nullptr);
        return nullptr;
    }
    return createDefaultDriver(this, sharedContext, driverConfig);
}

void CglPlatform::terminate() noexcept {
    CGLSetCurrentContext(nullptr);
    for (auto* chain : chains_) delete static_cast<HeadlessChain*>(chain);
    chains_.clear();
    if (context_) CGLReleaseContext(as_context(context_));
    if (format_) CGLReleasePixelFormat(as_format(format_));
    context_ = format_ = nullptr;
    bluegl::unbind();
}

filament::backend::Platform::SwapChain* CglPlatform::createSwapChain(void*, uint64_t) noexcept {
    return nullptr;
}

filament::backend::Platform::SwapChain* CglPlatform::createSwapChain(uint32_t, uint32_t, uint64_t) noexcept {
    auto* chain = new (std::nothrow) HeadlessChain();
    if (chain) chains_.push_back(chain);
    return chain;
}

void CglPlatform::destroySwapChain(SwapChain* swapChain) noexcept {
    auto it = std::find(chains_.begin(), chains_.end(), swapChain);
    if (it == chains_.end()) return;
    delete static_cast<HeadlessChain*>(*it);
    chains_.erase(it);
}

// Without a drawable, framebuffer 0 is incomplete; Filament never draws to it, because every
// view of filly's has its own render target.
bool CglPlatform::makeCurrent(ContextType, SwapChain*, SwapChain*) {
    return CGLSetCurrentContext(as_context(context_)) == kCGLNoError;
}

void CglPlatform::commit(SwapChain*) noexcept {}

void CglPlatform::createContext(bool shared) {
    CGLContextObj context = nullptr;
    if (CGLCreateContext(as_format(format_), shared ? as_context(context_) : nullptr, &context) != kCGLNoError
            || !context || CGLSetCurrentContext(context) != kCGLNoError) {
        // Filament then compiles with no context; its link checks report the failure.
        if (context) CGLReleaseContext(context);
        return;
    }
    std::lock_guard<std::mutex> lock(extra_lock_);
    extra_[std::this_thread::get_id()] = context;
}

void CglPlatform::releaseContext() noexcept {
    void* context = nullptr;
    {
        std::lock_guard<std::mutex> lock(extra_lock_);
        auto it = extra_.find(std::this_thread::get_id());
        if (it == extra_.end()) return;
        context = it->second;
        extra_.erase(it);
    }
    CGLSetCurrentContext(nullptr);
    CGLReleaseContext(as_context(context));
}
}
#endif
