#pragma once

#if defined(__APPLE__)
#include <backend/platforms/OpenGLPlatform.h>

#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace filly::detail {
// Why a host CGL context cannot share with Filament's, or empty if it can. Filament needs a
// core profile, and macOS shares objects only between contexts of the same profile.
std::string cgl_share_problem(void* host);

// OpenGL through CGL, for offscreen and shared engines. Filament's PlatformCocoaGL makes each
// headless swap chain an NSWindow, and it creates that window and updates its context with
// dispatch_sync to the main queue from the driver thread. A Python script that waits for the
// engine on the main thread then deadlocks, and a session without a window server cannot
// create the window. filly never presents, so a context without a drawable is enough: every
// render goes to a framebuffer object, and the swap chain only drives Filament's frames.
class CglPlatform : public filament::backend::OpenGLPlatform {
public:
    CglPlatform() noexcept = default;
    ~CglPlatform() noexcept override;

protected:
    // sharedContext is the host's CGLContextObj, or null for an offscreen engine.
    filament::backend::Driver* createDriver(void* sharedContext, const DriverConfig& driverConfig) override;
    int getOSVersion() const noexcept override { return 0; }
    void terminate() noexcept override;
    SwapChain* createSwapChain(void* nativeWindow, uint64_t flags) noexcept override;
    SwapChain* createSwapChain(uint32_t width, uint32_t height, uint64_t flags) noexcept override;
    void destroySwapChain(SwapChain* swapChain) noexcept override;
    bool makeCurrent(ContextType type, SwapChain* drawSwapChain, SwapChain* readSwapChain) override;
    void commit(SwapChain* swapChain) noexcept override;
    // Shared contexts for Filament's shader compiler thread, as in EglPlatform.
    bool isExtraContextSupported() const noexcept override { return true; }
    void createContext(bool shared) override;
    void releaseContext() noexcept override;

private:
    void* format_ = nullptr;   // CGLPixelFormatObj
    void* context_ = nullptr;  // CGLContextObj
    // Only the driver thread creates and destroys swap chains.
    std::vector<SwapChain*> chains_;
    std::mutex extra_lock_;
    std::unordered_map<std::thread::id, void*> extra_;
};
}
#endif
