#pragma once

#include <cstdint>
#include <memory>

namespace filament { class Engine; class Sync; namespace backend { class Platform; } }

namespace filly::detail {
struct Signal;
// Copies share one GL fence: one render's frame can be waited on by its target and by every host
// texture that it sampled. Release each copy with GlInterop::destroy().
struct SyncPoint {
    filament::Sync* sync = nullptr;
    std::shared_ptr<Signal> signal;
};

// Filament's OpenGL platform for every engine. A host context of zero selects an offscreen
// engine, for which the host-side calls do not apply.
class GlInterop {
public:
    explicit GlInterop(uintptr_t context);
    ~GlInterop();
    filament::backend::Platform* platform();
    filament::Engine* create_engine();
    // "wgl", "glx", or "egl": the window-system binding of Filament's OpenGL context.
    const char* platform_name() const;
    bool shared() const;
    void require_host() const;
    // False once the host has made another context current or destroyed its own.
    bool host_current() const;
    uintptr_t context() const;
    // A GL_SRGB8_ALPHA8 view of a host texture needs immutable storage (glTexStorage2D), which
    // third-party hosts often lack, so a view is made only where one is needed or possible.
    enum class SrgbView { NONE, IF_IMMUTABLE, REQUIRED };
    // Validates the host texture and returns an sRGB view of it, or the texture itself when no
    // view is made.
    uint32_t import_texture(uint32_t texture, uint32_t width, uint32_t height, SrgbView view) const;
    void delete_host_texture(uint32_t texture) const;
    void* host_fence();
    // Deletes a fence from host_fence() that no render consumed. Null is ignored.
    void delete_host_fence(void* fence);
    void enqueue_wait(filament::Engine& engine, void* fence);
    SyncPoint signal(filament::Engine& engine);
    void wait_on_host(const SyncPoint& point);
    void destroy(filament::Engine& engine, SyncPoint& point);
    void finish_host();
    // Sets GL_FRAMEBUFFER_SRGB in Filament's context, in command-stream order. Filament never
    // sets it, and it decides whether writes to sRGB attachments are encoded. Only scenes with
    // output_path 'direct' enable it; filly's encode pass writes encoded values raw.
    void set_srgb_writes(filament::Engine& engine, bool value);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
