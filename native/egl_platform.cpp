#include "egl_platform.h"

#if defined(__linux__)
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <bluegl/BlueGL.h>
#include <dlfcn.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <thread>
#include <type_traits>

namespace filly::detail {
struct EglDevice {
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLConfig config = nullptr;
    EGLContext context = EGL_NO_CONTEXT;
    // Filament makes its context current before it has a swap chain.
    EGLSurface dummy = EGL_NO_SURFACE;
};

namespace {
struct Egl {
    PFNEGLGETPROCADDRESSPROC getProcAddress = nullptr;
    PFNEGLQUERYSTRINGPROC queryString = nullptr;
    PFNEGLGETERRORPROC getError = nullptr;
    PFNEGLINITIALIZEPROC initialize = nullptr;
    PFNEGLBINDAPIPROC bindApi = nullptr;
    PFNEGLCHOOSECONFIGPROC chooseConfig = nullptr;
    PFNEGLCREATECONTEXTPROC createContext = nullptr;
    PFNEGLDESTROYCONTEXTPROC destroyContext = nullptr;
    PFNEGLCREATEPBUFFERSURFACEPROC createPbuffer = nullptr;
    PFNEGLDESTROYSURFACEPROC destroySurface = nullptr;
    PFNEGLMAKECURRENTPROC makeCurrent = nullptr;
    PFNEGLRELEASETHREADPROC releaseThread = nullptr;
    PFNEGLGETPLATFORMDISPLAYEXTPROC getPlatformDisplay = nullptr;
    PFNEGLQUERYDEVICESEXTPROC queryDevices = nullptr;
    PFNEGLQUERYDEVICESTRINGEXTPROC queryDeviceString = nullptr;
    std::string clientExtensions;
    bool loaded = false;
};

bool has_extension(const char* list, const char* name) {
    if (!list) return false;
    const auto length = std::strlen(name);
    for (const char* at = list; (at = std::strstr(at, name)); at += length) {
        const bool starts = at == list || at[-1] == ' ';
        const bool ends = at[length] == ' ' || at[length] == '\0';
        if (starts && ends) return true;
    }
    return false;
}

const Egl& egl() {
    static const Egl value = [] {
        Egl result;
        // A link-time dependency would make libEGL mandatory for GLX users and would not be
        // covered by the manylinux library policy. The library is never closed: vendor
        // drivers run thread-local destructors after the driver thread exits.
        void* library = dlopen("libEGL.so.1", RTLD_LOCAL | RTLD_NOW);
        if (!library) return result;
        auto load = [library](auto& function, const char* name) {
            function = reinterpret_cast<std::remove_reference_t<decltype(function)>>(dlsym(library, name));
            return function != nullptr;
        };
        const bool core = load(result.getProcAddress, "eglGetProcAddress") && load(result.queryString, "eglQueryString")
            && load(result.getError, "eglGetError") && load(result.initialize, "eglInitialize")
            && load(result.bindApi, "eglBindAPI") && load(result.chooseConfig, "eglChooseConfig")
            && load(result.createContext, "eglCreateContext") && load(result.destroyContext, "eglDestroyContext")
            && load(result.createPbuffer, "eglCreatePbufferSurface") && load(result.destroySurface, "eglDestroySurface")
            && load(result.makeCurrent, "eglMakeCurrent") && load(result.releaseThread, "eglReleaseThread");
        if (!core) return result;
        const char* extensions = result.queryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
        result.clientExtensions = extensions ? extensions : "";
        result.getPlatformDisplay = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(
            result.getProcAddress("eglGetPlatformDisplayEXT"));
        result.queryDevices = reinterpret_cast<PFNEGLQUERYDEVICESEXTPROC>(result.getProcAddress("eglQueryDevicesEXT"));
        result.queryDeviceString = reinterpret_cast<PFNEGLQUERYDEVICESTRINGEXTPROC>(
            result.getProcAddress("eglQueryDeviceStringEXT"));
        result.loaded = result.getPlatformDisplay && has_extension(extensions, "EGL_EXT_platform_base");
        return result;
    }();
    return value;
}

struct Candidate {
    EGLenum platform;
    void* native;
    bool software;
    const char* label;
};

std::string egl_error(const Egl& e, const char* call) {
    char code[16];
    std::snprintf(code, sizeof(code), "0x%04X", unsigned(e.getError()));
    return std::string(call) + " failed (" + code + ")";
}

void destroy_device(const Egl& e, EglDevice& device) {
    if (device.dummy != EGL_NO_SURFACE) e.destroySurface(device.display, device.dummy);
    if (device.context != EGL_NO_CONTEXT) e.destroyContext(device.display, device.context);
    device.dummy = EGL_NO_SURFACE;
    device.context = EGL_NO_CONTEXT;
    // eglTerminate is not called: platform displays are process-wide handles that another
    // renderer may still use, and eglInitialize on them again is cheap.
}

EglDevicePtr open_device(const Egl& e, const Candidate& candidate, std::string& renderer,
                                       std::string& reason) {
    EglDevicePtr device(new EglDevice());
    device->display = e.getPlatformDisplay(candidate.platform, candidate.native, nullptr);
    if (device->display == EGL_NO_DISPLAY) {
        reason = egl_error(e, "eglGetPlatformDisplayEXT");
        return nullptr;
    }
    EGLint major = 0, minor = 0;
    if (!e.initialize(device->display, &major, &minor)) {
        reason = egl_error(e, "eglInitialize");
        return nullptr;
    }
    if (!e.bindApi(EGL_OPENGL_API)) {
        reason = egl_error(e, "eglBindAPI(EGL_OPENGL_API)");
        return nullptr;
    }
    const EGLint configAttributes[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_DEPTH_SIZE, 24,
        EGL_NONE};
    EGLint count = 0;
    if (!e.chooseConfig(device->display, configAttributes, &device->config, 1, &count) || count < 1) {
        reason = "no RGBA8 pbuffer configuration with desktop OpenGL";
        return nullptr;
    }
    // The same version and profile as Filament's GLX platform.
    const EGLint contextAttributes[] = {
        EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 1,
        EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
    device->context = e.createContext(device->display, device->config, EGL_NO_CONTEXT, contextAttributes);
    if (device->context == EGL_NO_CONTEXT) {
        reason = egl_error(e, "eglCreateContext(OpenGL 4.1 core)");
        return nullptr;
    }
    const EGLint pbufferAttributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    device->dummy = e.createPbuffer(device->display, device->config, pbufferAttributes);
    if (device->dummy == EGL_NO_SURFACE || !e.makeCurrent(device->display, device->dummy, device->dummy, device->context)) {
        reason = egl_error(e, "eglMakeCurrent");
        return nullptr;
    }
    using GetString = const unsigned char* (*)(unsigned);
    auto get_string = reinterpret_cast<GetString>(e.getProcAddress("glGetString"));
    const auto* name = get_string ? get_string(0x1F01) : nullptr;  // GL_RENDERER
    renderer = name ? reinterpret_cast<const char*>(name) : "";
    e.makeCurrent(device->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    return device;
}

bool software_renderer(const std::string& renderer) {
    for (const char* name : {"llvmpipe", "softpipe", "swrast", "SWR"})
        if (renderer.find(name) != std::string::npos) return true;
    return false;
}

EglProbe probe_on_this_thread() {
    EglProbe result;
    const auto& e = egl();
    if (!e.loaded) {
        result.reason = "libEGL.so.1 with EGL_EXT_platform_base is not available";
        return result;
    }
    std::vector<Candidate> candidates, software;
    const char* client = e.clientExtensions.c_str();
    if (e.queryDevices && e.queryDeviceString && has_extension(client, "EGL_EXT_platform_device")) {
        EGLDeviceEXT devices[16];
        EGLint count = 0;
        if (e.queryDevices(16, devices, &count)) {
            for (EGLint i = 0; i < count; ++i) {
                const bool is_software = has_extension(e.queryDeviceString(devices[i], EGL_EXTENSIONS),
                                                       "EGL_MESA_device_software");
                (is_software ? software : candidates).push_back(
                    {EGL_PLATFORM_DEVICE_EXT, devices[i], is_software, "device"});
            }
        }
    }
    if (has_extension(client, "EGL_MESA_platform_surfaceless"))
        candidates.push_back({EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, false, "surfaceless"});
    candidates.insert(candidates.end(), software.begin(), software.end());
    for (const auto& candidate : candidates) {
        std::string renderer, reason;
        auto device = open_device(e, candidate, renderer, reason);
        if (!device) {
            if (!result.reason.empty()) result.reason += "; ";
            result.reason += std::string(candidate.label) + ": " + reason;
            continue;
        }
        const bool is_software = candidate.software || software_renderer(renderer);
        if (!is_software || !result.device) {
            result.device = std::move(device);
            result.software = is_software;
            result.renderer = renderer;
            if (!is_software) break;
        }
    }
    e.releaseThread();
    if (!result.device && result.reason.empty()) result.reason = "no EGL device or surfaceless platform";
    return result;
}
}

EglProbe probe_egl() {
    // A fresh thread has no current context. glvnd refuses to make an EGL context current
    // on a thread where the host's GLX context is current.
    EglProbe result;
    std::thread([&result] {
        try {
            result = probe_on_this_thread();
        } catch (const std::exception& error) {
            result = {};
            result.reason = error.what();
        }
    }).join();
    return result;
}

void EglDeviceDeleter::operator()(EglDevice* device) const noexcept {
    // After terminate() the context is already gone and this only frees the record.
    if (egl().loaded) destroy_device(egl(), *device);
    delete device;
}

EglPlatform::EglPlatform(EglDevicePtr device) noexcept : device_(std::move(device)) {}

EglPlatform::~EglPlatform() noexcept = default;

filament::backend::Driver* EglPlatform::createDriver(void* sharedContext, const DriverConfig& driverConfig) {
    const auto& e = egl();
    // Shared contexts use GLX; this platform is for offscreen engines only.
    if (sharedContext || !device_) return nullptr;
    // eglBindAPI is per thread, and this is the driver thread.
    if (!e.bindApi(EGL_OPENGL_API)
            || !e.makeCurrent(device_->display, device_->dummy, device_->dummy, device_->context))
        return nullptr;
    if (bluegl::bind()) {
        e.makeCurrent(device_->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        return nullptr;
    }
    return createDefaultDriver(this, sharedContext, driverConfig);
}

void EglPlatform::terminate() noexcept {
    const auto& e = egl();
    e.makeCurrent(device_->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    for (void* surface : surfaces_) e.destroySurface(device_->display, surface);
    surfaces_.clear();
    destroy_device(e, *device_);
    e.releaseThread();
    bluegl::unbind();
}

filament::backend::Platform::SwapChain* EglPlatform::createSwapChain(void*, uint64_t) noexcept {
    return nullptr;
}

filament::backend::Platform::SwapChain* EglPlatform::createSwapChain(uint32_t width, uint32_t height,
                                                                    uint64_t) noexcept {
    const EGLint attributes[] = {EGL_WIDTH, EGLint(width), EGL_HEIGHT, EGLint(height), EGL_NONE};
    EGLSurface surface = egl().createPbuffer(device_->display, device_->config, attributes);
    if (surface == EGL_NO_SURFACE) return nullptr;
    surfaces_.push_back(surface);
    return static_cast<SwapChain*>(surface);
}

void EglPlatform::destroySwapChain(SwapChain* swapChain) noexcept {
    auto it = std::find(surfaces_.begin(), surfaces_.end(), static_cast<void*>(swapChain));
    if (it == surfaces_.end()) return;
    // Destroying the current draw surface would defer its release until the next makeCurrent.
    egl().makeCurrent(device_->display, device_->dummy, device_->dummy, device_->context);
    egl().destroySurface(device_->display, *it);
    surfaces_.erase(it);
}

bool EglPlatform::makeCurrent(ContextType, SwapChain* drawSwapChain, SwapChain* readSwapChain) {
    EGLSurface draw = drawSwapChain ? static_cast<EGLSurface>(drawSwapChain) : device_->dummy;
    EGLSurface read = readSwapChain ? static_cast<EGLSurface>(readSwapChain) : device_->dummy;
    return egl().makeCurrent(device_->display, draw, read, device_->context) == EGL_TRUE;
}

// Pbuffers have no front buffer, so there is nothing to present.
void EglPlatform::commit(SwapChain*) noexcept {}

void EglPlatform::createContext(bool shared) {
    const auto& e = egl();
    // eglBindAPI is per thread.
    e.bindApi(EGL_OPENGL_API);
    const EGLint contextAttributes[] = {
        EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 1,
        EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
    EGLContext context = e.createContext(device_->display, device_->config,
                                         shared ? device_->context : EGL_NO_CONTEXT, contextAttributes);
    // A pbuffer, not a surfaceless context: EGL_KHR_surfaceless_context is optional, and the
    // main context uses pbuffers too.
    const EGLint pbufferAttributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    EGLSurface surface = context == EGL_NO_CONTEXT ? EGL_NO_SURFACE
        : e.createPbuffer(device_->display, device_->config, pbufferAttributes);
    if (surface == EGL_NO_SURFACE || !e.makeCurrent(device_->display, surface, surface, context)) {
        // Filament then compiles with an unusable context; its link checks report the failure.
        if (surface != EGL_NO_SURFACE) e.destroySurface(device_->display, surface);
        if (context != EGL_NO_CONTEXT) e.destroyContext(device_->display, context);
        return;
    }
    std::lock_guard<std::mutex> lock(extra_lock_);
    extra_[std::this_thread::get_id()] = {context, surface};
}

void EglPlatform::releaseContext() noexcept {
    const auto& e = egl();
    Extra extra{};
    {
        std::lock_guard<std::mutex> lock(extra_lock_);
        auto it = extra_.find(std::this_thread::get_id());
        if (it == extra_.end()) return;
        extra = it->second;
        extra_.erase(it);
    }
    e.makeCurrent(device_->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    e.destroySurface(device_->display, extra.surface);
    e.destroyContext(device_->display, extra.context);
    e.releaseThread();
}
}
#endif
