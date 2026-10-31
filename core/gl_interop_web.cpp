// GlInterop for the web build. Filament renders with PlatformWebGL into the page's WebGL2
// context, the one the host page draws with (PIXI in PsychoJS), so there is one context and no
// sharing. The build is single-threaded: Filament's GL calls run inside render() and land in the
// page's command stream before the host's later draws, so no fences are needed.
//
// The host and Filament each cache GL state. The JavaScript glue (web/filly.js) saves and
// resets the host's state before every filly call and resets it after; this file only assumes
// that the context is the current Emscripten context.

#include "gl_interop.h"
#include "renderer.h"
#include "engine_config.h"

#include <GLES3/gl3.h>
#include <backend/platforms/PlatformWebGL.h>
#include <emscripten/html5.h>
#include <filament/Engine.h>

#include <array>
#include <memory>
#include <string>

// WebGL2 has no buffer mapping, and filly links without Emscripten's FULL_ES3 emulation of it.
// Filament falls back to glBufferSubData when a mapping returns null.
extern "C" void* glMapBufferRange(GLenum, GLintptr, GLsizeiptr, GLbitfield) { return nullptr; }
extern "C" GLboolean glUnmapBuffer(GLenum) { return GL_TRUE; }

namespace filly {
uintptr_t current_gl_context() {
    return uintptr_t(emscripten_webgl_get_current_context());
}

uint32_t create_host_texture(int64_t width, int64_t height) {
    if (!current_gl_context()) throw InteropError("Register the page's WebGL2 context first");
    if (width < 1 || height < 1 || width > 8192 || height > 8192)
        throw std::invalid_argument("width and height must be from 1 through 8192, got "
                                    + std::to_string(width) + "x" + std::to_string(height));
    while (glGetError() != GL_NO_ERROR) {}
    GLint previous = 0;
    GLuint texture = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, GLsizei(width), GLsizei(height));
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, GLuint(previous));
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
struct Signal {};

// EXT_disjoint_timer_query_webgl2 tokens.
constexpr GLenum TIME_ELAPSED = 0x88BF;
constexpr GLenum GPU_DISJOINT = 0x8FBB;

struct GlInterop::Impl {
    uintptr_t context = 0;
    std::unique_ptr<filament::backend::PlatformWebGL> platform = std::make_unique<filament::backend::PlatformWebGL>();
    // Frame timer queries in a ring; the oldest in flight is at `next - count`. A result becomes
    // available only after the page returns to the event loop, so a few frames are in flight.
    // When all four are, a frame goes untimed instead of waiting.
    struct Query { GLuint name = 0; uint64_t frame = 0; };
    std::array<Query, 4> queries{};
    size_t next = 0, count = 0;
    std::optional<bool> timer_supported;
    bool timing = false;
    std::optional<std::pair<uint64_t, double>> latest;
};

GlInterop::GlInterop(uintptr_t context) : impl_(std::make_unique<Impl>()) {
    if (!context)
        throw InteropError("The web build renders only into the page's WebGL2 context; pass its handle");
    impl_->context = context;
}
GlInterop::~GlInterop() {
    // The page's context frees them with itself otherwise.
    if (current_gl_context() != impl_->context) return;
    if (impl_->timing) glEndQuery(TIME_ELAPSED);
    for (const auto& query : impl_->queries)
        if (query.name) glDeleteQueries(1, &query.name);
}
filament::backend::Platform* GlInterop::platform() { return impl_->platform.get(); }
const char* GlInterop::platform_name() const { return "webgl"; }
bool GlInterop::shared() const { return true; }
// WebGL2 has no texture views; see import_texture().
bool GlInterop::texture_views() const { return false; }
filament::Engine* GlInterop::create_engine() {
    require_host();
    const auto config = engine_config();
    return filament::Engine::Builder().backend(filament::Engine::Backend::OPENGL)
        .platform(platform()).config(&config).build();
}
void GlInterop::require_host() const {
    // One page context: make it current rather than fail, because only filly's calls use
    // Emscripten's notion of the current context.
    if (current_gl_context() != impl_->context
            && emscripten_webgl_make_context_current(EMSCRIPTEN_WEBGL_CONTEXT_HANDLE(impl_->context)) != EMSCRIPTEN_RESULT_SUCCESS)
        throw InteropError("Could not make the page's WebGL2 context current");
}
bool GlInterop::host_current() const { return true; }
uintptr_t GlInterop::context() const { return impl_->context; }
uint32_t GlInterop::import_texture(uint32_t texture, uint32_t, uint32_t, SrgbView view) const {
    require_host();
    // WebGL2 cannot report a texture's size or format, and it has no texture views. A texture
    // that must be sampled as sRGB needs SRGB8_ALPHA8 storage from the page itself.
    if (!texture || !glIsTexture(texture)) throw InteropError("Texture does not exist in the page's context");
    (void)view;
    return texture;
}
void GlInterop::delete_host_texture(uint32_t texture) const {
    const GLuint name = texture;
    if (name) glDeleteTextures(1, &name);
}
void* GlInterop::host_fence() { return nullptr; }
void GlInterop::delete_host_fence(void*) {}
void GlInterop::enqueue_wait(filament::Engine&, void*) {}
SyncPoint GlInterop::signal(filament::Engine&) { return {}; }
void GlInterop::wait_on_host(const SyncPoint&) {}
void GlInterop::destroy(filament::Engine& engine, SyncPoint& point) {
    // signal() makes no syncs here; this matches the desktop rule for shared points anyway.
    if (point.sync && point.signal.use_count() <= 1) engine.destroy(point.sync);
    point = {};
}
void GlInterop::set_srgb_writes(filament::Engine&, bool value) {
    // WebGL2 always encodes writes to sRGB attachments and cannot turn that off.
    if (value) throw InteropError("output_path 'direct' with sRGB encoding is not available in WebGL2");
}
void GlInterop::finish_host() {}
void GlInterop::on_host_display_close(std::function<void()>) {}
void GlInterop::begin_frame_timer() {
    auto& d = *impl_;
    if (!d.timer_supported)
        d.timer_supported = emscripten_webgl_enable_extension(
            EMSCRIPTEN_WEBGL_CONTEXT_HANDLE(d.context), "EXT_disjoint_timer_query_webgl2");
    if (!*d.timer_supported) return;
    // A frame that threw before end_frame_timer() leaves its query active.
    if (d.timing) {
        glEndQuery(TIME_ELAPSED);
        d.timing = false;
    }
    // Read the finished queries in order. A disjoint event (for example a GPU clock change)
    // makes the results of every query in flight unreliable, so they are dropped.
    while (d.count) {
        auto& query = d.queries[(d.next + d.queries.size() - d.count) % d.queries.size()];
        GLuint available = 0;
        glGetQueryObjectuiv(query.name, GL_QUERY_RESULT_AVAILABLE, &available);
        if (!available) break;
        GLint disjoint = 0;
        glGetIntegerv(GPU_DISJOINT, &disjoint);
        if (disjoint) {
            d.count = 0;
            break;
        }
        GLuint nanoseconds = 0;
        glGetQueryObjectuiv(query.name, GL_QUERY_RESULT, &nanoseconds);
        if (nanoseconds) d.latest = std::make_pair(query.frame, double(nanoseconds) * 1e-6);
        --d.count;
    }
    if (d.count == d.queries.size()) return;
    auto& query = d.queries[d.next];
    if (!query.name) glGenQueries(1, &query.name);
    glBeginQuery(TIME_ELAPSED, query.name);
    d.timing = true;
}
void GlInterop::end_frame_timer(uint64_t frame) {
    auto& d = *impl_;
    if (!d.timing) return;
    glEndQuery(TIME_ELAPSED);
    d.queries[d.next].frame = frame;
    d.next = (d.next + 1) % d.queries.size();
    ++d.count;
    d.timing = false;
}
std::optional<std::pair<uint64_t, double>> GlInterop::frame_time() const { return impl_->latest; }
}
}
