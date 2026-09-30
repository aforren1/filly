#pragma once

#if defined(__linux__)
#include <backend/platforms/OpenGLPlatform.h>

#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace filly::detail {
// An initialized EGL display and an OpenGL 4.1 core context that is not current on any thread.
struct EglDevice;
// Destroys the context and its surfaces; the display stays initialized.
struct EglDeviceDeleter { void operator()(EglDevice* device) const noexcept; };
using EglDevicePtr = std::unique_ptr<EglDevice, EglDeviceDeleter>;

struct EglProbe {
    EglDevicePtr device;
    bool software = false;
    std::string renderer;
    std::string reason;  // why no device was found
};

// Finds an EGL display that needs no window system: a GPU from EGL_EXT_platform_device first,
// then Mesa's surfaceless platform, then a software device. libEGL is loaded at run time, so a
// host without it can still use GLX.
EglProbe probe_egl();

// Offscreen OpenGL through EGL. Headless swap chains are pbuffers. Filament loads GL entry points
// through BlueGL (libGL.so.1), which glvnd dispatches to the current EGL context.
class EglPlatform : public filament::backend::OpenGLPlatform {
public:
    explicit EglPlatform(EglDevicePtr device) noexcept;
    ~EglPlatform() noexcept override;

protected:
    filament::backend::Driver* createDriver(void* sharedContext, const DriverConfig& driverConfig) override;
    int getOSVersion() const noexcept override { return 0; }
    void terminate() noexcept override;
    SwapChain* createSwapChain(void* nativeWindow, uint64_t flags) noexcept override;
    SwapChain* createSwapChain(uint32_t width, uint32_t height, uint64_t flags) noexcept override;
    void destroySwapChain(SwapChain* swapChain) noexcept override;
    bool makeCurrent(ContextType type, SwapChain* drawSwapChain, SwapChain* readSwapChain) override;
    void commit(SwapChain* swapChain) noexcept override;
    // Shared contexts for Filament's shader compiler thread. Without them Filament compiles
    // programs one per frame on the driver thread, and Material::compile() does nothing.
    bool isExtraContextSupported() const noexcept override { return true; }
    void createContext(bool shared) override;
    void releaseContext() noexcept override;

private:
    EglDevicePtr device_;
    // Only the driver thread creates and destroys swap chains.
    std::vector<void*> surfaces_;
    // Extra contexts and their 1 x 1 pbuffers by thread.
    struct Extra { void* context; void* surface; };
    std::mutex extra_lock_;
    std::unordered_map<std::thread::id, Extra> extra_;
};
}
#endif
