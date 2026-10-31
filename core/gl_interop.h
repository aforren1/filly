#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <utility>
#include <vector>

namespace filament { class Engine; class Sync; namespace backend { class Platform; } }

namespace filly::detail {
struct Signal;
#if !defined(__EMSCRIPTEN__)
// A pixel buffer object for one readback at a time. Filament's driver thread owns the GL name and
// the fence; the caller's thread reuses the buffer for later readbacks of the same target.
struct PackBuffer {
    uint32_t name = 0;
    void* fence = nullptr;
    // Each copy into the buffer has a number, so that a check of an earlier copy, or a check that
    // is still queued from a readback closed unread, cannot report a later copy as finished.
    // `copies` belongs to the caller's thread, `fenced` to the driver thread.
    uint64_t copies = 0, fenced = 0;
    // The number of the last copy that a poll found finished.
    std::atomic<uint64_t> finished{0};
};
#endif
#if defined(__APPLE__)
struct SurfaceLink;
// A copy target's objects (see CopyTarget in renderer.h). The host context shares no objects
// with Filament's, so each frame crosses through an IOSurface that both contexts bind as a
// rectangle texture, or through CPU memory. The caller's thread owns this; `link` and `buffer`
// hold Filament's side, which its driver thread uses.
struct HostCopy {
    enum class Transfer { IOSURFACE, READBACK };
    Transfer transfer = Transfer::IOSURFACE;
    uintptr_t context = 0;
    uint32_t texture = 0, width = 0, height = 0;
    // IOSURFACE: the surface (IOSurfaceRef), the host's rectangle texture on it, and the read and
    // draw framebuffers that copy it into `texture`.
    void* surface = nullptr;
    uint32_t rectangle = 0, framebuffers[2] = {};
    std::shared_ptr<SurfaceLink> link;
    // The fence after the host's last copy out of the surface. The next frame's copy into the
    // surface must wait for it, and no GL fence crosses between the contexts.
    void* copied = nullptr;
    // READBACK: the pixel buffer in Filament's context and the rows, bottom row first, that
    // reach the host texture through glTexSubImage2D.
    std::shared_ptr<PackBuffer> buffer;
    std::unique_ptr<uint8_t[]> rows;
};
#endif
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
    // "wgl", "glx", "egl", or "cgl": the window-system binding of Filament's OpenGL context.
    const char* platform_name() const;
    bool shared() const;
    void require_host() const;
    // False once the host has made another context current or destroyed its own.
    bool host_current() const;
    uintptr_t context() const;
    // A GL_SRGB8_ALPHA8 view of a host texture needs immutable storage (glTexStorage2D), which
    // third-party hosts often lack, so a view is made only where one is needed or possible.
    enum class SrgbView { NONE, IF_IMMUTABLE, REQUIRED };
    // Whether the host context can make sRGB views at all: OpenGL 4.3 or ARB_texture_view.
    bool texture_views() const;
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
    // Runs the callback when the host closes the window-system connection that Filament's shared
    // context uses (Linux: the X display, which takes Filament's context with it). Elsewhere, and
    // for offscreen engines, it does nothing.
    void on_host_display_close(std::function<void()> callback);
    // Sets GL_FRAMEBUFFER_SRGB in Filament's context, in command-stream order. Filament never
    // sets it, and it decides whether writes to sRGB attachments are encoded. Only scenes with
    // output_path 'direct' enable it; filly's encode pass writes encoded values raw.
    void set_srgb_writes(filament::Engine& engine, bool value);
#if !defined(__EMSCRIPTEN__)
    // Runs the task on Filament's driver thread, with its context current, in command-stream
    // order. The task must not throw.
    void run(filament::Engine& engine, std::function<void()> task);
    // A texture with immutable GL_SRGB8_ALPHA8 storage in Filament's context, for
    // Texture::Builder::import(). Waits for the driver thread, not for the GPU. Zero on failure.
    uint32_t create_texture(filament::Engine& engine, uint32_t width, uint32_t height);
    // Deletes a texture from create_texture() (zero for none) and the buffers, after the commands
    // queued before this call.
    void delete_objects(filament::Engine& engine, uint32_t texture, std::vector<std::shared_ptr<PackBuffer>> buffers);
    // Queues copy number `copy` of the texture into the buffer, followed by a fence. Does not
    // flush.
    void pack(filament::Engine& engine, uint32_t texture, uint32_t width, uint32_t height,
              std::shared_ptr<PackBuffer> buffer, uint64_t copy);
    // Queues a check that sets `finished` to `copy` if that copy is the buffer's latest and has
    // finished. Does not flush.
    void poll(filament::Engine& engine, std::shared_ptr<PackBuffer> buffer, uint64_t copy);
    // Waits for the copy, then passes the mapped rows to the sink on the driver thread: the top
    // row and the stride from one row to the next, which is negative. False if the copy failed,
    // if the GPU did not finish it within 10 s, or if the driver thread did not start the task
    // within 10 s. The sink is never called after unpack() returns.
    bool unpack(filament::Engine& engine, const std::shared_ptr<PackBuffer>& buffer, uint32_t width, uint32_t height,
                const std::function<void(const uint8_t*, ptrdiff_t)>& sink);
#endif
#if defined(__APPLE__)
    // Validates the host texture in the current host context and creates both sides of the
    // transfer. Throws InteropError with the reason if the transfer is unavailable.
    std::unique_ptr<HostCopy> create_host_copy(filament::Engine& engine, uint32_t texture, uint32_t width,
                                               uint32_t height, HostCopy::Transfer transfer);
    // Before a render: waits until the host's last copy out of the surface is done.
    void before_copy(HostCopy& copy);
    // After a render: queues the copy of Filament's color texture toward the host.
    void publish(filament::Engine& engine, HostCopy& copy, uint32_t texture);
    // In the host context: waits for the last published frame and copies it into the host texture.
    void take(filament::Engine& engine, HostCopy& copy);
    // Deletes Filament's side after the commands queued before it. Host names are deleted only
    // with host_current; otherwise they go away with the host context.
    void delete_host_copy(filament::Engine& engine, HostCopy& copy, bool host_current);
#endif
#if defined(__EMSCRIPTEN__)
    // Filament's WebGL backend compiles out its GPU timer queries, so the web build brackets
    // each frame with its own EXT_disjoint_timer_query_webgl2 query. The single-threaded
    // build runs a frame's GL calls between the two calls. Without the extension, both do
    // nothing.
    void begin_frame_timer();
    void end_frame_timer(uint64_t frame);
    // The newest frame whose query has finished, and its GPU time in milliseconds.
    std::optional<std::pair<uint64_t, double>> frame_time() const;
#endif
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
