#include "renderer.h"
#include "gl_interop.h"
#include "gltf_prepare.h"
#include "materials.h"
#include "output_pass.h"
#include "engine_config.h"
#include "webp_provider.h"

#include <backend/PixelBufferDescriptor.h>
#include <filament/Camera.h>
#include <filament/ColorGrading.h>
#include <filament/ColorSpace.h>
#include <filament/ToneMapper.h>
#include <filament/IndirectLight.h>
#include <filament/Skybox.h>
#include <filament-iblprefilter/IBLPrefilterContext.h>
#include <filament/Engine.h>
#include <filament/DebugRegistry.h>
#include <filament/Exposure.h>
#include <filament/IndexBuffer.h>
#include <filament/TextureSampler.h>
#include <filament/VertexBuffer.h>
#include <geometry/SurfaceOrientation.h>
#include <math/norm.h>
#include <filament/LightManager.h>
#include <filament/MaterialInstance.h>
#include <filament/Material.h>
#include <filament/RenderableManager.h>
#include <utils/tribool.h>
#include <filament/Options.h>
#include <filament/Renderer.h>
#include <filament/RenderTarget.h>
#include <filament/Scene.h>
#include <filament/Texture.h>
#include <filament/TransformManager.h>
#include <filament/View.h>
#include <filament/Viewport.h>
#include <gltfio/AssetLoader.h>
#include <gltfio/Animator.h>
#include <gltfio/FilamentAsset.h>
#include <gltfio/FilamentInstance.h>
#include <gltfio/ResourceLoader.h>
#include <gltfio/TextureProvider.h>
#include <gltfio/math.h>
#include <ktxreader/Ktx1Reader.h>
#include <math/mat4.h>
#include <utils/EntityManager.h>
#include <utils/Log.h>
#include <utils/NameComponentManager.h>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <string_view>
#include <filesystem>
#include <fstream>
#include <limits>
#include <thread>
#include <utility>

extern "C" {
float* stbi_loadf_from_memory(const unsigned char*, int, int*, int*, int*, int);
void stbi_image_free(void*);
}

namespace filly {
namespace f = filament;
namespace g = filament::gltfio;
namespace m = filament::math;

void set_log_level(const std::string& level) {
    const std::array<std::string, 6> levels = {"verbose", "debug", "info", "warning", "error", "off"};
    const auto found = std::find(levels.begin(), levels.end(), level);
    if (found == levels.end())
        throw std::invalid_argument("Log level must be verbose, debug, info, warning, error, or off");
    const auto threshold = size_t(found - levels.begin());
    utils::io::ostream* streams[] = {
        &utils::slog.v, &utils::slog.d, &utils::slog.i, &utils::slog.w, &utils::slog.e
    };
    // Native consumers also cover messages emitted by Filament's driver thread.
    // A null consumer restores Filament's normal output for the selected stream.
    for (size_t i = 0; i < 5; ++i) {
        utils::io::ostream::ConsumerCallback consumer = nullptr;
        if (i < threshold) consumer = [](void*, const char*) {};
        streams[i]->setConsumer(consumer, nullptr);
    }
}

namespace {
// The error for a name that a model does not have. It lists the names that it has, because users
// often know a clip or material only from a modeling tool, where names can differ.
AssetError unknown_name(const char* what, const std::string& name, const std::vector<std::string>& names) {
    constexpr size_t shown = 20;
    std::string list;
    size_t count = 0;
    for (const auto& candidate : names) {
        if (candidate.empty()) continue;
        if (count++ < shown) list += (list.empty() ? "'" : ", '") + candidate + "'";
    }
    if (count > shown) list += ", and " + std::to_string(count - shown) + " more";
    return AssetError(std::string("Unknown ") + what + " '" + name + "'; " +
                      (count ? "the model has: " + list : std::string("the model has no named ") + what + "s"));
}
template <class T> void finite(T value) {
    if (!std::isfinite(value)) throw std::invalid_argument("Values must be finite");
}
template <class T, size_t N> void finite(const std::array<T, N>& values) {
    for (auto value : values) finite(value);
}
m::float3 vec(Vec3 v) { return {v[0], v[1], v[2]}; }
Vec3 vec(m::float3 v) { return {v.x, v.y, v.z}; }
template <class T> Matrix matrix(const T& v) {
    Matrix result;
    for (size_t row = 0; row < 4; ++row)
        for (size_t col = 0; col < 4; ++col)
            result[row * 4 + col] = float(v[col][row]);
    return result;
}
m::mat4f matrix(const Matrix& v) {
    finite(v);
    if (v[12] != 0 || v[13] != 0 || v[14] != 0 || v[15] != 1)
        throw std::invalid_argument("Transform must be affine with last row [0, 0, 0, 1]");
    m::mat4f result;
    for (size_t row = 0; row < 4; ++row)
        for (size_t col = 0; col < 4; ++col)
            result[col][row] = v[row * 4 + col];
    return result;
}
void clipping(double near, double far) {
    finite(near); finite(far);
    if (!(near > 0 && far > near))
        throw std::invalid_argument("Require 0 < near < far");
}
}

namespace detail {
struct Resource;
struct TextureData;
struct State {
    f::Engine* engine = nullptr;
    f::Renderer* renderer = nullptr;
    // Never presented. beginFrame()/endFrame() need it to run Filament's per-frame upkeep.
    f::SwapChain* frame_chain = nullptr;
    // Reused for every environment. Destroying these objects after each prefilter made later
    // frames black when set_environment() ran before a model was loaded.
    // Each filter builds its material on first use: KTX environments need only the irradiance
    // filter, and only for diffuse-transmission materials.
    struct Prefilter {
        IBLPrefilterContext context;
        std::unique_ptr<IBLPrefilterContext::EquirectangularToCubemap> cube_filter;
        std::unique_ptr<IBLPrefilterContext::SpecularFilter> specular_filter;
        std::unique_ptr<IBLPrefilterContext::IrradianceFilter> irradiance_filter;
        explicit Prefilter(f::Engine& engine) : context(engine) {}
        template <class T> T& filter(std::unique_ptr<T>& value) {
            if (!value) value = std::make_unique<T>(context);
            return *value;
        }
        auto& to_cube() { return filter(cube_filter); }
        auto& specular() { return filter(specular_filter); }
        auto& irradiance() { return filter(irradiance_filter); }
    };
    std::unique_ptr<Prefilter> prefilter;
    g::MaterialProvider* materials = nullptr;
    g::AssetLoader* loader = nullptr;
    std::unique_ptr<utils::NameComponentManager> names;
    std::vector<std::weak_ptr<Resource>> resources;
    std::thread::id thread = std::this_thread::get_id();
    Stats stats;
    std::unique_ptr<GlInterop> interop;
    // GL_FRAMEBUFFER_SRGB in Filament's context, as last queued.
    bool srgb_writes = false;
    // sRGB views of host textures whose targets closed while the host context was not current.
    std::vector<uint32_t> stale_views;
    // Host textures that materials may sample; each render orders them against host writes.
    std::vector<TextureData*> host_inputs;
    // Fills a viewport with the background for render(..., clear=False), created on first use.
    f::View* fill_view = nullptr;
    f::Scene* fill_scene = nullptr;
    f::Skybox* fill_sky = nullptr;
    // filly's output passes (see output_pass.h). Each draws one full-screen triangle in its own
    // view, after the scene view in the same frame.
    struct Pass {
        f::Material* material = nullptr;
        f::MaterialInstance* instance = nullptr;
        utils::Entity entity;
        f::Scene* scene = nullptr;
        f::View* view = nullptr;
        // Parameters as last set, so that steady frames set none. A target's release clears
        // `input`, because a new texture can reuse a destroyed one's address.
        f::Texture* input = nullptr;
        int flags = -1;
        m::float4 values{NAN};
        m::float4 bounds{NAN};
        m::float4 inner{NAN};
        m::float4 background{NAN};
    };
    Pass encode, encode_graded, fxaa;
    f::VertexBuffer* triangle = nullptr;
    f::IndexBuffer* triangle_indices = nullptr;
    utils::Entity pass_camera;

    void check_thread() const {
        if (thread != std::this_thread::get_id())
            throw FillyError("Use the renderer on its creating thread");
    }
    void check() const {
        check_thread();
        if (!engine) throw FillyError("Renderer is closed");
    }
    void close() noexcept;
    // Views can be deleted only in the host context, which is gone once its window closes, and
    // only after Filament's driver thread has destroyed the framebuffer that uses them. GL allows
    // an earlier delete, but it is not needed and leaves more to the driver.
    void delete_views() noexcept {
        if (stale_views.empty() || !interop->shared() || !interop->host_current()) return;
        if (engine) flush_and_wait(*engine);
        for (auto name : stale_views) interop->delete_host_texture(name);
        stale_views.clear();
    }
    ~State() { close(); }
    template <class T> std::shared_ptr<T> track(std::shared_ptr<T> value) {
        // Remove dead entries outside the frame loop to bound registry growth.
        std::erase_if(resources, [](const auto& item) { return item.expired(); });
        resources.push_back(value);
        return value;
    }
};

struct Resource {
    // The resource types that code looks up from a Resource pointer.
    enum class Kind { other, scene, model, light };
    std::shared_ptr<State> state;
    explicit Resource(std::shared_ptr<State> owner) : state(std::move(owner)) {}
    virtual Kind kind() const noexcept { return Kind::other; }
    virtual void release() noexcept = 0;
    virtual ~Resource() = default;
};

// dynamic_pointer_cast without RTTI: the web build compiles with -fno-rtti, as Filament does.
template <class T> std::shared_ptr<T> resource_cast(const std::shared_ptr<Resource>& resource) noexcept {
    return resource && resource->kind() == T::tag ? std::static_pointer_cast<T>(resource) : nullptr;
}

struct CameraData : Resource {
    using Resource::Resource;
    utils::Entity entity;
    f::Camera* camera = nullptr;
    // Imported cameras borrow a glTF-owned component. The model owns their handles, so this
    // back-reference is weak to avoid a cycle.
    std::weak_ptr<ModelData> model;
    bool imported = false;
    size_t projection = SIZE_MAX;
    // A fitted projection follows the render target's aspect; FIXED keeps the caller's.
    enum class Fit : uint8_t { FIXED, PERSPECTIVE, LENS, ORTHOGRAPHIC } fit = Fit::FIXED;
    double size = 0, center_x = 0, center_y = 0, near = 0, far = 0;
    // Aspect of the current fitted projection. It is 1 until the first render.
    double aspect = 1;
    // Depth-of-field f-number. Filament's own aperture sets exposure and stays unchanged.
    float aperture = 16;
    void check() const;
    void apply_fit() {
        if (fit == Fit::PERSPECTIVE) camera->setProjection(size, aspect, near, far, f::Camera::Fov::VERTICAL);
        else if (fit == Fit::LENS) camera->setLensProjection(size, aspect, near, far);
        else if (fit == Fit::ORTHOGRAPHIC) {
            const double half_width = size * aspect / 2, half_height = size / 2;
            camera->setProjection(f::Camera::Projection::ORTHO, center_x - half_width, center_x + half_width,
                                  center_y - half_height, center_y + half_height, near, far);
        }
    }
    void fit_aspect(double target);
    void set_world_transform(const m::mat4& world) {
        auto& tm = state->engine->getTransformManager();
        const auto parent = tm.getParent(tm.getInstance(entity));
        // Filament reads camera poses in world space but writes local entity transforms.
        const auto local = parent ? inverse(m::mat4(tm.getWorldTransform(tm.getInstance(parent)))) * world : world;
        for (size_t col=0; col<4; ++col) for (size_t row=0; row<4; ++row) finite(local[col][row]);
        camera->setModelMatrix(local);
    }
    void release() noexcept override {
        if (camera && state->engine && !imported) {
            state->engine->destroyCameraComponent(entity);
            utils::EntityManager::get().destroy(entity);
        }
        camera = nullptr;
    }
    ~CameraData() override { release(); }
};

struct ModelData;
struct SceneData : Resource {
    using Resource::Resource;
    static constexpr Kind tag = Kind::scene;
    Kind kind() const noexcept override { return tag; }
    f::Scene* scene = nullptr;
    f::View* view = nullptr;
    std::shared_ptr<CameraData> active_camera;
    std::vector<std::shared_ptr<Resource>> children;
    // Skinned models whose node transforms changed since the last render.
    std::vector<std::shared_ptr<ModelData>> dirty_bones;
    Vec4 background = {0, 0, 0, 1};
    f::ColorGrading* grading = nullptr;
    // The tone mapper mixes channels, so it runs in Filament's 3D LUT (see apply_grading()).
    bool channel_mixing = false;
    // Filament's color grading writes sRGB values, so the encode pass stores them as they are.
    bool filament_encodes = false;
    f::IndirectLight* environment = nullptr;
    f::Texture* reflections = nullptr;
    f::Texture* irradiance = nullptr;
    // Level 0 of a KTX environment's IBL, kept until a material needs `irradiance`.
    struct PendingIrradiance {
        std::vector<uint8_t> faces;
        uint32_t size = 0;
        f::Texture::InternalFormat format{};
        f::Texture::Format pixels{};
        f::Texture::Type type{};
    };
    std::unique_ptr<PendingIrradiance> pending_irradiance;
    f::Texture* skybox_texture = nullptr;
    f::Skybox* skybox = nullptr;
    bool environment_visible = false;
    float environment_rotation = 0;
    std::string encoding = "srgb", tone_mapping = "linear", antialiasing = "none";
    int msaa = 1;
    bool shadows = false, refraction = false, transparent = false, dithering = false, ssao = false, bloom = false;
    bool fog = false, depth_of_field = false, vignette = false;
    Vec3 fog_color = {1, 1, 1};
    float fog_density = 0.1f, fog_start = 0;
    // Fog color last sent to Filament, after the exposure compensation. NaN forces an update.
    m::float3 fog_applied = {NAN, NAN, NAN};
    float dof_scale = NAN;
    // The caller's opt-in to skip filly's encode pass: the view writes shaded color straight into
    // the target and the GPU applies the sRGB encoding on write. It is never chosen
    // automatically, because the two paths round differently by up to one 8-bit level.
    bool direct = false;
    // Live models with MASK materials. Filament writes their sharpened edge alpha to the target,
    // and only the encode pass stores alpha one for an opaque view.
    size_t masked = 0;
    void check() const {
        state->check();
        if (!scene) throw FillyError("Scene is closed");
    }
    void release() noexcept override {
        if (state->engine) {
            if (view) state->engine->destroy(view);
            if (scene) state->engine->destroy(scene);
            if (grading) state->engine->destroy(grading);
            if (environment) state->engine->destroy(environment);
            if (skybox) state->engine->destroy(skybox);
            if (skybox_texture) state->engine->destroy(skybox_texture);
            if (reflections) state->engine->destroy(reflections);
            if (irradiance) state->engine->destroy(irradiance);
        }
        view = nullptr;
        scene = nullptr;
        grading = nullptr;
        environment = nullptr;
        skybox = nullptr; skybox_texture = nullptr;
        reflections = irradiance = nullptr;
        pending_irradiance.reset();
        active_camera.reset();
        children.clear();
        dirty_bones.clear();
    }
    ~SceneData() override { release(); }
};

// The direct path writes shaded color straight into the target, so it cannot render these
// options: Filament's postprocessing (tone mapping other than the linear clamp, bloom, depth of
// field, vignette), filly's passes (FXAA, dithering, premultiplying after encoding for
// transparent views, which hosts that blend in encoded space expect), and, by its original
// contract, MSAA, refraction, and SSAO. Returns the conflicting settings, or an empty string.
std::string direct_conflicts(const SceneData& scene) {
    std::string names;
    auto add = [&](bool conflict, const char* name) {
        if (conflict) names += (names.empty() ? "" : ", ") + std::string(name);
    };
    add(scene.tone_mapping != "linear", "tone_mapping");
    add(scene.antialiasing != "none", "antialiasing");
    add(scene.msaa != 1, "msaa");
    add(scene.refraction, "refraction");
    add(scene.transparent, "transparent");
    add(scene.dithering, "dithering");
    add(scene.ssao, "ssao");
    add(scene.bloom, "bloom");
    add(scene.depth_of_field, "depth_of_field");
    add(scene.vignette, "vignette");
    return names;
}
// Called before an option that the direct path cannot render is applied, so a conflict changes
// nothing.
void require_exact(const SceneData& scene, bool needs_exact, const std::string& option) {
    if (scene.direct && needs_exact)
        throw std::invalid_argument(option + " needs the exact output path, but output_path is "
                                    "'direct'; set output_path = 'exact' first");
}
// Filament's postprocessing runs only for the options that are part of it. Its color grading
// then writes scene-linear color, or sRGB color for a channel-mixing tone mapper with sRGB
// encoding (see apply_grading()); filly's encode pass still rounds, dithers, and sets alpha.
void update_postprocessing(SceneData& scene) {
    scene.view->setPostProcessingEnabled(!scene.direct && (scene.tone_mapping != "linear" || scene.bloom
                                                            || scene.depth_of_field || scene.vignette));
}
// Dithering belongs before the rounding to 8-bit levels: in filly's encode pass, or in Filament's
// color grading when that encodes.
void update_dithering(SceneData& scene) {
    scene.view->setDithering(scene.dithering && scene.filament_encodes ? f::View::Dithering::TEMPORAL
                                                                       : f::View::Dithering::NONE);
}

// Scenes must not keep a camera whose component is gone.
void detach_closed_cameras(State& state) noexcept {
    for (const auto& entry : state.resources) {
        auto scene = resource_cast<SceneData>(entry.lock());
        if (scene && scene->active_camera && !scene->active_camera->camera) {
            if (scene->view) scene->view->setCamera(nullptr);
            scene->active_camera.reset();
        }
    }
}

struct PropertyTarget {
    f::MaterialInstance* material = nullptr;
    utils::Entity light;
    Vec4 rest = {};
    bool automatic_range = false;
    size_t compound = 0;
    int field = 0;
};
struct CameraProjection {
    size_t index;
    utils::Entity entity;
    bool orthographic;
    Vec4 rest, value;
    // Used when the glTF camera omits aspectRatio: the last render target's aspect.
    double aspect = 1;
};
struct TextureTransform {
    f::MaterialInstance* material;
    std::string parameter;
    std::array<float,5> rest, value;
    bool transpose;
};
struct PropertyTrack {
    PropertyTrackSource source;
    std::vector<PropertyTarget> targets;
};
struct LightCone {
    utils::Entity entity;
    std::array<float,2> rest, value;
};
struct AnimationClip {
    std::string name;
    int native_index = -1;
    // See AnimationSource::needs_rest.
    bool needs_rest = true;
    float duration = 0;
    std::vector<PropertyTrack> tracks;
};
struct NodeVisibility {
    utils::Entity entity;
    size_t index, parent;
    bool rest, value, effective = false;
};

// Buffers for repeated uploads. Filament reads a buffer until its callback, which it runs on
// this thread during a later flush, so a slot is reused only after that callback. Three slots
// let the caller stay up to two uploads ahead of the driver thread without waiting.
struct Staging {
    struct Slot {
        std::unique_ptr<uint8_t[]> bytes;
        bool busy = false;
    };
    std::array<Slot, 3> slots;
    size_t size = 0, next = 0;
    static void done(void*, size_t, void* user) { static_cast<Slot*>(user)->busy = false; }
    // Storage is allocated on first use of each slot; later calls do not allocate.
    Slot& take(f::Engine& engine) {
        auto& slot = slots[next];
        next = (next + 1) % slots.size();
        if (slot.busy) flush_and_wait(engine);
        if (slot.busy) throw FillyError("A previous upload did not complete");
        if (!slot.bytes) slot.bytes = std::make_unique<uint8_t[]>(size);
        slot.busy = true;
        return slot;
    }
    bool busy() const {
        return std::any_of(slots.begin(), slots.end(), [](const Slot& slot) { return slot.busy; });
    }
};

// Geometry of a generated mesh. Clones share it, so an update changes every instance.
struct MeshGeometry {
    f::VertexBuffer* vertices = nullptr;
    f::IndexBuffer* indices = nullptr;
    size_t vertex_count = 0, triangle_count = 0;
    bool computed_normals = false, has_uvs = false, has_colors = false;
    // CPU copies that tangent frames depend on, and scratch space for recomputing them.
    std::vector<m::float3> positions, normals, tan1, tan2;
    std::vector<m::float2> uvs;
    std::vector<m::uint3> triangles;
    Staging position_upload, tangent_upload, uv_upload, color_upload;
    m::float3 low, high;
    // Renderables of every instance, so bounds updates reach clones.
    std::vector<utils::Entity> renderables;
    void release(f::Engine& engine) noexcept {
        for (const auto* ring : {&position_upload, &tangent_upload, &uv_upload, &color_upload})
            if (ring->busy()) { flush_and_wait(engine); break; }
        if (vertices) engine.destroy(vertices);
        if (indices) engine.destroy(indices);
        vertices = nullptr;
        indices = nullptr;
    }
};

// One glTF asset and the preparation tables that each instance needs. A model and its clones
// share it; the last one to close destroys the asset.
struct AssetData : Resource {
    using Resource::Resource;
    g::FilamentAsset* asset = nullptr;
    // False once the source data and instance tables are released after the first instance.
    bool clonable = false;
    bool masked = false;
    // Node names stay for every asset: name lookups need them, and they are small.
    std::shared_ptr<PreparedAsset> prepared;
    // Buffer memory that gltfio's parse points into, until its source data is released.
    std::shared_ptr<BufferStore> buffers;
    // Provider-built material textures of every instance. Their material instances live until
    // the asset is destroyed, so the textures must too.
    std::vector<f::Texture*> textures;
    // The textures that gltfio decoded for the asset, which gltfio owns. Only Model::memory()
    // reads them; Filament's texture getters issue no commands.
    std::vector<const f::Texture*> loaded_textures;
    // CPU bytes of gltfio's source data, measured at load; 0 once it is released.
    uint64_t source_bytes = 0;
    // Instances created, each of which keeps per-instance buffers until the asset goes.
    size_t instances = 0;
    // Set for generated meshes, whose renderables use these buffers instead of the asset's.
    std::unique_ptr<MeshGeometry> mesh;
    void release() noexcept override {
        if (state->engine) {
            if (asset) state->loader->destroyAsset(asset);
            for (auto* texture : textures) state->engine->destroy(texture);
            if (mesh) mesh->release(*state->engine);
        }
        asset = nullptr;
        buffers.reset();
        textures.clear();
        loaded_textures.clear();
        if (prepared) prepared->decoded.clear();
        mesh.reset();
    }
    ~AssetData() override { release(); }
};

// Runtime textures of one material handle: base color (0) and emissive (1).
struct Assignment {
    std::array<std::shared_ptr<TextureData>, 2> textures;
    std::array<m::mat3f, 2> transforms;
    bool any() const { return textures[0] || textures[1]; }
};

struct LocalMaterial {
    utils::Entity entity;
    size_t slot;
    f::MaterialInstance* source;
    f::MaterialInstance* copy;
    Assignment assignment;
};

// A shared glTF material with runtime textures. The model shows `current` wherever it showed
// the glTF instance `source`; gltfio keeps `source` for variants, animation, and restores.
struct SharedOverride {
    f::MaterialInstance* source;
    f::MaterialInstance* current;
    Assignment assignment;
};

struct ModelData : Resource, std::enable_shared_from_this<ModelData> {
    using Resource::Resource;
    static constexpr Kind tag = Kind::model;
    Kind kind() const noexcept override { return tag; }
    std::weak_ptr<SceneData> scene;
    std::shared_ptr<AssetData> shared;
    // Null once the model is closed.
    g::FilamentAsset* asset = nullptr;
    g::FilamentInstance* instance = nullptr;
    bool visible = true, skinned = false, bones_dirty = false;
    g::Animator* animator = nullptr;
    // The animator's rest animation (PreparedAsset::rest_animation), or -1.
    int rest_animation = -1;
    // Entities by glTF node index; null for nodes that gltfio did not instantiate.
    std::vector<utils::Entity> nodes;
    // (entity id, glTF node index) for this instance's node entities, sorted by entity id.
    std::vector<std::pair<uint32_t, uint32_t>> node_indices;
    // Light and camera nodes in glTF node order.
    std::vector<utils::Entity> lights, camera_entities;
    std::vector<std::shared_ptr<CameraData>> camera_handles;
    std::vector<AnimationClip> animations;
    std::vector<CameraProjection> cameras;
    std::vector<TextureTransform> texture_transforms;
    std::vector<LightCone> light_cones;
    std::vector<NodeVisibility> visibility;
    std::vector<std::pair<utils::Entity, m::mat4f>> rest_transforms;
    std::vector<std::pair<utils::Entity, std::vector<float>>> rest_morphs;
    std::vector<LocalMaterial> locals;
    std::vector<SharedOverride> overrides;
    // Provider keys of this instance's glTF material instances.
    std::vector<MaterialRecord> records;
    void check() const {
        state->check();
        if (!asset) throw FillyError("Model is closed");
    }
    void release() noexcept override {
        if (asset && state->engine) {
            for (auto& handle : camera_handles) if (handle) handle->camera = nullptr;
            detach_closed_cameras(*state);
            if (auto owner = scene.lock(); owner && owner->scene) {
                owner->scene->removeEntities(instance->getEntities(), instance->getEntityCount());
                owner->scene->remove(instance->getRoot());
                if (shared->masked) --owner->masked;
            }
            // The instance's renderables outlive this model until the shared asset goes.
            auto& rm = state->engine->getRenderableManager();
            for (const auto& local : locals) {
                rm.setMaterialInstanceAt(rm.getInstance(local.entity), local.slot, local.source);
                state->engine->destroy(local.copy);
            }
            for (const auto& entry : overrides) {
                swap_bound(entry.current, entry.source);
                state->engine->destroy(entry.current);
            }
            if (shared && shared->mesh) {
                const auto* entities = instance->getEntities();
                const auto* end = entities + instance->getEntityCount();
                std::erase_if(shared->mesh->renderables, [&](utils::Entity e) { return std::find(entities, end, e) != end; });
            }
        }
        // Dropping runtime textures can release them, which scans their models; this model must
        // already read as closed then, so the handles go out of scope at the end.
        auto dropped_locals = std::move(locals);
        auto dropped_overrides = std::move(overrides);
        locals.clear();
        overrides.clear();
        records.clear();
        camera_handles.clear();
        light_cones.clear();
        visibility.clear();
        asset = nullptr;
        instance = nullptr;
        animator = nullptr;
        animations.clear();
        cameras.clear();
        texture_transforms.clear();
        rest_transforms.clear();
        rest_morphs.clear();
        nodes.clear();
        node_indices.clear();
        lights.clear();
        camera_entities.clear();
        shared.reset();
    }
    ~ModelData() override { release(); }
    // Puts `to` in every primitive of this instance that shows `from`.
    void swap_bound(f::MaterialInstance* from, f::MaterialInstance* to) noexcept {
        auto& rm = state->engine->getRenderableManager();
        const auto* entities = instance->getEntities();
        for (size_t i = 0; i < instance->getEntityCount(); ++i) {
            auto renderable = rm.getInstance(entities[i]);
            if (!renderable) continue;
            for (size_t p = 0; p < rm.getPrimitiveCount(renderable); ++p)
                if (rm.getMaterialInstanceAt(renderable, p) == from) rm.setMaterialInstanceAt(renderable, p, to);
        }
    }
    void mark_bones(utils::Entity entity) {
        // Bone matrices are relative to the skinned mesh, so the model root does not affect them.
        if (!skinned || bones_dirty || entity == instance->getRoot()) return;
        auto owner = scene.lock();
        if (!owner) return;
        bones_dirty = true;
        owner->dirty_bones.push_back(shared_from_this());
    }
};
// The glTF node index of one of this instance's entities, or SIZE_MAX.
size_t node_index(const ModelData& model, utils::Entity entity) {
    const auto id = entity.getId();
    const auto found = std::lower_bound(model.node_indices.begin(), model.node_indices.end(), std::pair<uint32_t, uint32_t>{id, 0});
    return found != model.node_indices.end() && found->first == id ? found->second : SIZE_MAX;
}
// glTF names, not gltfio's, which fall back to the mesh, light, or camera name.
const NodeSource* node_source(const ModelData& model, utils::Entity entity) {
    const auto index = node_index(model, entity);
    const auto& nodes = model.shared->prepared->nodes;
    return index < nodes.size() ? &nodes[index] : nullptr;
}
void initialize_visibility(ModelData& model) {
    auto& transforms = model.state->engine->getTransformManager();
    std::vector<std::pair<utils::Entity, size_t>> pending{{model.instance->getRoot(), SIZE_MAX}};
    while (!pending.empty()) {
        const auto [entity, parent] = pending.back();
        pending.pop_back();
        const size_t position = model.visibility.size();
        const size_t index = node_index(model, entity);
        const auto* source = node_source(model, entity);
        const bool visible = !source || source->visible;
        model.visibility.push_back({entity, index, parent, visible, visible});
        for (auto child : transforms.getChildrenRange(transforms.getInstance(entity)))
            pending.emplace_back(transforms.getEntity(child), position);
    }
}
void update_visibility(ModelData& model) {
    auto scene = model.scene.lock();
    if (!scene || !scene->scene) return;
    // Parents precede children. No allocations or hierarchy searches occur during animation.
    for (auto& node : model.visibility) {
        const bool effective = model.visible && node.value &&
            (node.parent == SIZE_MAX || model.visibility[node.parent].effective);
        if (node.effective != effective) {
            if (effective) scene->scene->addEntity(node.entity);
            else scene->scene->remove(node.entity);
            node.effective = effective;
        }
    }
}
void CameraData::check() const {
    state->check();
    if (imported) {
        auto owner = model.lock();
        if (!owner) throw FillyError("Camera is closed");
        owner->check();
    }
    if (!camera) throw FillyError("Camera is closed");
}

struct LightData : Resource {
    using Resource::Resource;
    static constexpr Kind tag = Kind::light;
    Kind kind() const noexcept override { return tag; }
    std::weak_ptr<SceneData> scene;
    utils::Entity entity;
    void release() noexcept override {
        if (entity && state->engine) {
            if (auto owner = scene.lock(); owner && owner->scene) owner->scene->remove(entity);
            state->engine->destroy(entity);
            utils::EntityManager::get().destroy(entity);
        }
        entity = {};
    }
    ~LightData() override { release(); }
};

std::shared_ptr<LightData> own_light(const std::shared_ptr<SceneData>& scene, utils::Entity entity) {
    auto light = scene->state->track(std::make_shared<LightData>(scene->state));
    light->scene = scene;
    light->entity = entity;
    scene->children.push_back(light);
    scene->scene->addEntity(entity);
    return light;
}

struct TargetData : Resource {
    using Resource::Resource;
    f::Texture* color = nullptr;
    f::Texture* depth = nullptr;
    f::RenderTarget* target = nullptr;
    uint32_t width = 0, height = 0;
    bool rendered = false;
    enum class Access { IDLE, SUBMITTED, HOST, RELEASED };
    bool imported = false;
    Access access = Access::IDLE;
    int held = 0;
    void* host_done = nullptr;
    // The sRGB view of the host texture that Filament renders into, or zero when Filament
    // renders into the host texture itself, which is mutable and has no view.
    uint32_t host_view = 0;
    SyncPoint ready;
    // The exact output path, created on its first render: the scene renders scene-linear color
    // into `linear` (with the target's depth, or `linear_depth` if the target has none), and
    // filly's encode pass writes `color` through `output`. With FXAA, the encode pass writes
    // `ldr` and FXAA writes `color`. When Filament's color grading encodes (a channel-mixing tone
    // mapper with sRGB encoding), the scene renders into the RGBA8 `graded` instead, as
    // gltf_viewer renders into its RGBA8 swap chain, and the encode pass copies it. Allocating
    // the default route with the target instead made no measurable difference to the first
    // frame, and would cost a target that only the direct path renders 8 bytes per pixel.
    f::Texture* linear = nullptr;
    f::Texture* linear_depth = nullptr;
    f::RenderTarget* linear_target = nullptr;
    f::Texture* graded = nullptr;
    f::RenderTarget* graded_target = nullptr;
    f::RenderTarget* output = nullptr;
    f::Texture* ldr = nullptr;
    f::RenderTarget* ldr_target = nullptr;
    void check() const {
        state->check();
        if (!target) throw FillyError("Render target is closed");
    }
    void release() noexcept override {
        if (state->engine) {
            if (imported) {
                state->interop->enqueue_wait(*state->engine, host_done);
                host_done = nullptr;
                state->interop->destroy(*state->engine, ready);
            }
            for (auto* rt : {target, linear_target, graded_target, output, ldr_target}) if (rt) state->engine->destroy(rt);
            for (auto* texture : {linear, graded, linear_depth, ldr, depth, color}) if (texture) state->engine->destroy(texture);
        }
        if (state->encode.input && state->encode.input == linear) state->encode.input = nullptr;
        if (state->encode_graded.input && state->encode_graded.input == graded) state->encode_graded.input = nullptr;
        if (state->fxaa.input && state->fxaa.input == ldr) state->fxaa.input = nullptr;
        if (host_view) state->stale_views.push_back(host_view);
        host_view = 0;
        target = linear_target = graded_target = output = ldr_target = nullptr;
        color = depth = linear = graded = linear_depth = ldr = nullptr;
        held = 0;
    }
    ~TargetData() override { release(); }
};

// A texture that materials sample: pixels from the caller, or a host OpenGL texture.
struct TextureData : Resource {
    using Resource::Resource;
    f::Texture* texture = nullptr;
    uint32_t width = 0, height = 0;
    int channels = 4;
    bool is_float = false, srgb = true, mipmaps = false, host = false;
    f::TextureSampler sampler;
    // Upload layout. 1-channel sRGB data expands to RGBA: there is no 1-channel sRGB format.
    f::Texture::Format format = f::Texture::Format::RGBA;
    f::Texture::Type type = f::Texture::Type::UBYTE;
    bool expand = false;
    size_t input_bytes = 0;
    Staging upload;
    // The slot that begin_update() handed out and commit_update() has not uploaded.
    Staging::Slot* pending = nullptr;
    // Models whose materials sample this texture. Closing the texture removes it from them.
    std::vector<std::weak_ptr<ModelData>> users;
    // Host input: its sRGB view (or zero), the fence of the last host write, and the frame
    // that the host waits for before it writes again.
    uint32_t host_view = 0;
    void* host_done = nullptr;
    SyncPoint read_done;
    bool writing = false;
    // Whether a model of the scene being rendered samples it; set by each render.
    bool sampled = false;
    void check() const {
        state->check();
        if (!texture) throw FillyError("Texture is closed");
    }
    void release() noexcept override;
    ~TextureData() override { release(); }
};

#include "animation.inc"
#include "slots.inc"

void CameraData::fit_aspect(double target) {
    if (imported) {
        auto owner = model.lock();
        if (!owner || projection == SIZE_MAX) return;
        auto& p = owner->cameras[projection];
        if (p.orthographic || p.rest[1] != 0 || p.value[1] != 0 || p.aspect == target) return;
        p.aspect = target;
        apply_projection(*state->engine, p);
        return;
    }
    if (fit == Fit::FIXED || aspect == target) return;
    aspect = target;
    apply_fit();
}

// Prefilters a KTX environment's irradiance cubemap. The IBL file has fewer mip levels than the
// irradiance filter needs, so its level 0 goes into a texture with a full mip chain, in the
// same face layout as the KTX upload.
void build_pending_irradiance(SceneData& scene) {
    auto pending = std::move(scene.pending_irradiance);
    auto& engine = *scene.state->engine;
    auto* faces = new std::vector<uint8_t>(std::move(pending->faces));
    auto* source = f::Texture::Builder().width(pending->size).height(pending->size).levels(0xff)
        .sampler(f::Texture::Sampler::SAMPLER_CUBEMAP).format(pending->format)
        .usage(f::Texture::Usage::SAMPLEABLE | f::Texture::Usage::COLOR_ATTACHMENT |
               f::Texture::Usage::GEN_MIPMAPPABLE | f::Texture::Usage::UPLOADABLE).build(engine);
    source->setImage(engine, 0, 0, 0, 0, pending->size, pending->size, 6,
        f::Texture::PixelBufferDescriptor(faces->data(), faces->size(), pending->pixels, pending->type,
            [](void*, size_t, void* user) { delete static_cast<std::vector<uint8_t>*>(user); }, faces));
    source->generateMipmaps(engine);
    auto& prefilter = scene.state->prefilter;
    if (!prefilter) prefilter = std::make_unique<State::Prefilter>(engine);
    IBLPrefilterContext::IrradianceFilter::Options options;
    options.generateMipmap = false;
    scene.irradiance = prefilter->irradiance()(options, source);
    engine.destroy(source);
    // Loading, not the first frame, pays for the filter's GPU work.
    flush_and_wait(engine);
}

void update_diffuse_environment(SceneData& scene) {
    if (scene.pending_irradiance) {
        bool needed = false;
        auto needs = [](const f::MaterialInstance* instance) { return instance->getMaterial()->hasParameter("backlightIntensity"); };
        for (const auto& child : scene.children) {
            auto model = resource_cast<ModelData>(child);
            if (!model || !model->asset) continue;
            for (size_t i = 0; i < model->instance->getMaterialInstanceCount(); ++i)
                needed = needed || needs(model->instance->getMaterialInstances()[i]);
            for (const auto& local : model->locals) needed = needed || needs(local.copy);
        }
        if (needed) build_pending_irradiance(scene);
    }
    const float intensity = scene.environment ? scene.environment->getIntensity() : 0;
    for (const auto& child : scene.children) {
        auto model = resource_cast<ModelData>(child);
        if (!model || !model->asset) continue;
        auto* instance = model->instance;
        for (size_t i=0; i<instance->getMaterialInstanceCount(); ++i)
            configure_diffuse_environment(scene.state->materials, instance->getMaterialInstances()[i],
                scene.irradiance, intensity, scene.environment_rotation);
        for (const auto& local : model->locals)
            configure_diffuse_environment(scene.state->materials, local.copy, scene.irradiance,
                intensity, scene.environment_rotation);
    }
}

// Builds the per-instance tables and restores authored state. Shared by loading and cloning.
// model.node_indices must be set.
void initialize_model(ModelData& model, const std::vector<MaterialBinding>& bindings) {
    auto& engine = *model.state->engine;
    auto& transforms = engine.getTransformManager();
    auto& renderables = engine.getRenderableManager();
    auto& light_manager = engine.getLightManager();
    const auto& prepared = *model.shared->prepared;
    model.animator = model.instance->getAnimator();
    model.skinned = model.instance->getSkinCount() > 0;
    model.nodes.assign(prepared.nodes.size(), utils::Entity{});
    for (const auto& [entity, index] : model.node_indices)
        if (index < model.nodes.size()) model.nodes[index] = utils::Entity::import(entity);
    for (const auto entity : model.nodes) {
        if (!entity) continue;
        if (light_manager.hasComponent(entity)) model.lights.push_back(entity);
        if (engine.getCameraComponent(entity)) model.camera_entities.push_back(entity);
    }
    model.camera_handles.resize(model.camera_entities.size());
    // Follow KHR_materials_emissive_strength: emission is factor times strength, once. gltfio
    // bakes the strength into emissiveFactor and every shader multiplies by emissiveStrength again.
    // This runs before animation reads rest values, so animated factor and strength stay correct.
    for (const auto& [index, instance] : bindings) {
        if (!instance || index >= prepared.materials.size()) continue;
        if (!instance->getMaterial()->hasParameter("emissiveFactor")) continue;
        const auto& factor = prepared.materials[index].emissive_factor;
        instance->setParameter("emissiveFactor", m::float3{factor[0], factor[1], factor[2]});
    }
    for (const auto& [index, box] : prepared.skinned_boxes) {
        if (index >= model.nodes.size() || !model.nodes[index]) continue;
        const auto renderable = renderables.getInstance(model.nodes[index]);
        if (!renderable) continue;
        const auto& [low, high] = box;
        renderables.setAxisAlignedBoundingBox(renderable, f::Box().set(m::float3{low[0], low[1], low[2]}, m::float3{high[0], high[1], high[2]}));
    }
    initialize_visibility(model);
    initialize_cameras(model, prepared.cameras);
    initialize_animations(model, prepared.animations, bindings);
    const auto* entities = model.instance->getEntities();
    const size_t count = model.instance->getEntityCount();
    for (size_t i = 0; i < count; ++i) {
        auto entity = entities[i];
        model.rest_transforms.emplace_back(entity, transforms.getTransform(transforms.getInstance(entity)));
        auto renderable = renderables.getInstance(entity);
        if (renderable && renderables.getMorphTargetCount(renderable)) {
            const auto targets = renderables.getMorphTargetCount(renderable);
            std::vector<float> weights(targets);
            if (const auto* source = node_source(model, entity); source && source->weights.size() == targets)
                weights = source->weights;
            renderables.setMorphWeights(renderable, weights.data(), weights.size());
            model.rest_morphs.emplace_back(entity, std::move(weights));
        }
    }
    model.animator->updateBoneMatrices();
}

// gltfio builds entities and material instances from its own parse and exposes no map back to
// glTF indices. While createInstance() runs, it reads node and material extras from
// json + offset (AssetLoader.cpp, recurseEntities() and createMaterialInstance()). Pointing those
// ranges at index markers gives exact maps: node markers become each entity's extras, material
// markers reach the MaterialProvider. The parse is restored before this returns. The SDK does
// not export AssetLoader::getNodeManager(), so entity extras keep the markers; filly does not
// expose extras.
g::FilamentInstance* create_instance(State& state, AssetData& shared, std::vector<std::pair<uint32_t, uint32_t>>& nodes) {
    auto* data = static_cast<cgltf_data*>(const_cast<void*>(shared.asset->getSourceAsset()));
    if (!data) throw FillyError("Asset source data was released");
    std::string markers;
    std::vector<cgltf_extras> node_extras(data->nodes_count), material_extras(data->materials_count);
    auto mark = [&](cgltf_extras& extras, cgltf_extras& saved, char kind, size_t index) {
        saved = extras;
        extras.start_offset = markers.size();
        markers += '#';
        markers += kind;
        markers += std::to_string(index);
        extras.end_offset = markers.size();
    };
    for (size_t i = 0; i < data->nodes_count; ++i) mark(data->nodes[i].extras, node_extras[i], 'n', i);
    for (size_t i = 0; i < data->materials_count; ++i) mark(data->materials[i].extras, material_extras[i], 'm', i);
    const char* json = data->json;
    struct Restore {
        cgltf_data* data; const char* json;
        std::vector<cgltf_extras>& nodes; std::vector<cgltf_extras>& materials;
        g::MaterialProvider* provider;
        ~Restore() {
            data->json = json;
            for (size_t i = 0; i < nodes.size(); ++i) data->nodes[i].extras = nodes[i];
            for (size_t i = 0; i < materials.size(); ++i) data->materials[i].extras = materials[i];
            set_prepared_asset(provider, nullptr);
        }
    };
    g::FilamentInstance* instance = nullptr;
    {
        Restore restore{data, json, node_extras, material_extras, state.materials};
        data->json = markers.data();
        set_prepared_asset(state.materials, shared.prepared.get());
        instance = state.loader->createInstance(shared.asset);
    }
    if (!instance) return nullptr;
    const auto* entities = instance->getEntities();
    nodes.clear();
    nodes.reserve(instance->getEntityCount());
    for (size_t i = 0; i < instance->getEntityCount(); ++i) {
        const char* marker = shared.asset->getExtras(entities[i]);
        marker = marker ? marker : "";
        const char* end = marker + std::strlen(marker);
        size_t index = SIZE_MAX;
        if (marker[0] == '#' && marker[1] == 'n') std::from_chars(marker + 2, end, index);
        if (index >= data->nodes_count)
            throw AssetError("gltfio created an entity without a node marker; filly needs updating for this Filament SDK");
        nodes.emplace_back(entities[i].getId(), uint32_t(index));
    }
    std::sort(nodes.begin(), nodes.end());
    return instance;
}

void State::close() noexcept {
    if (!engine) return;
    flush_and_wait(*engine);
    prefilter.reset();
    // Dependents are registered after their owners and must be destroyed first.
    for (auto it = resources.rbegin(); it != resources.rend(); ++it)
        if (auto resource = it->lock()) resource->release();
    resources.clear();
    if (loader) g::AssetLoader::destroy(&loader);
    if (materials) {
        materials->destroyMaterials();
        delete materials;
        materials = nullptr;
    }
    names.reset();
    if (fill_view) engine->destroy(fill_view);
    if (fill_scene) engine->destroy(fill_scene);
    if (fill_sky) engine->destroy(fill_sky);
    fill_view = nullptr;
    fill_scene = nullptr;
    fill_sky = nullptr;
    for (auto* pass : {&encode, &encode_graded, &fxaa}) {
        if (pass->view) engine->destroy(pass->view);
        if (pass->scene) engine->destroy(pass->scene);
        if (pass->entity) {
            engine->destroy(pass->entity);
            utils::EntityManager::get().destroy(pass->entity);
        }
        if (pass->instance) engine->destroy(pass->instance);
        if (pass->material) engine->destroy(pass->material);
        *pass = {};
    }
    if (triangle) engine->destroy(triangle);
    if (triangle_indices) engine->destroy(triangle_indices);
    triangle = nullptr;
    triangle_indices = nullptr;
    if (pass_camera) {
        engine->destroyCameraComponent(pass_camera);
        utils::EntityManager::get().destroy(pass_camera);
        pass_camera = {};
    }
    host_inputs.clear();
    if (frame_chain) engine->destroy(frame_chain);
    frame_chain = nullptr;
    if (renderer) engine->destroy(renderer);
    renderer = nullptr;
    f::Engine::destroy(&engine);
    engine = nullptr;
    delete_views();
}

// Builds a pass on first use: its material, one full-screen triangle, and a view that
// writes the shader output raw (no postprocessing).
enum class PassKind { encode, encode_graded, fxaa };
State::Pass& output_pass(State& state, State::Pass& pass, PassKind kind) {
    if (pass.view) return pass;
    auto& engine = *state.engine;
    if (!state.triangle) {
        // Clip-space corners: one triangle that covers the whole viewport.
        static const m::float4 corners[3] = {{-1, -1, 1, 1}, {3, -1, 1, 1}, {-1, 3, 1, 1}};
        static const uint16_t order[3] = {0, 1, 2};
        state.triangle = f::VertexBuffer::Builder().vertexCount(3).bufferCount(1)
            .attribute(f::VertexAttribute::POSITION, 0, f::VertexBuffer::AttributeType::FLOAT4).build(engine);
        state.triangle->setBufferAt(engine, 0, f::VertexBuffer::BufferDescriptor(corners, sizeof(corners)));
        state.triangle_indices = f::IndexBuffer::Builder().indexCount(3)
            .bufferType(f::IndexBuffer::IndexType::USHORT).build(engine);
        state.triangle_indices->setBuffer(engine, f::IndexBuffer::BufferDescriptor(order, sizeof(order)));
        state.pass_camera = utils::EntityManager::get().create();
        engine.createCamera(state.pass_camera)->setProjection(f::Camera::Projection::ORTHO, -1, 1, -1, 1, 0, 1);
    }
    pass.material = kind == PassKind::fxaa ? build_fxaa_material(engine)
                                           : build_encode_material(engine, kind == PassKind::encode_graded);
    pass.instance = pass.material->createInstance();
    pass.entity = utils::EntityManager::get().create();
    f::RenderableManager::Builder(1).boundingBox({{-1, -1, -1}, {1, 1, 1}}).material(0, pass.instance)
        .geometry(0, f::RenderableManager::PrimitiveType::TRIANGLES, state.triangle, state.triangle_indices, 0, 3)
        .culling(false).castShadows(false).receiveShadows(false).build(engine, pass.entity);
    pass.scene = engine.createScene();
    pass.scene->addEntity(pass.entity);
    pass.view = engine.createView();
    pass.view->setScene(pass.scene);
    pass.view->setCamera(engine.getCameraComponent(state.pass_camera));
    pass.view->setPostProcessingEnabled(false);
    pass.view->setShadowingEnabled(false);
    // The triangle always covers the viewport, so culling has nothing to reject.
    pass.view->setFrustumCullingEnabled(false);
    return pass;
}

// Allocates a target's exact-path buffers on first use; targets never change size. The linear
// and graded buffers are allocated only for the routes that the target's scenes take.
void prepare_exact(State& state, TargetData& target, bool fxaa, bool graded) {
    auto& engine = *state.engine;
    using T = f::Texture;
    using A = f::RenderTarget::AttachmentPoint;
    if (!target.output) {
        target.output = f::RenderTarget::Builder().texture(A::COLOR, target.color).build(engine);
        if (!target.depth)
            target.linear_depth = T::Builder().width(target.width).height(target.height).levels(1)
                .sampler(T::Sampler::SAMPLER_2D).format(T::InternalFormat::DEPTH24)
                .usage(T::Usage::DEPTH_ATTACHMENT).build(engine);
        if (!target.output || (!target.depth && !target.linear_depth))
            throw FillyError("Could not create the output render target");
    }
    auto* depth = target.depth ? target.depth : target.linear_depth;
    if (!graded && !target.linear_target) {
        // RGBA16F: alpha for transparent views, and 11 significant bits keep the encoded value
        // within 0.06 levels of the exact one. See docs/explanation/design.md.
        target.linear = T::Builder().width(target.width).height(target.height).levels(1)
            .sampler(T::Sampler::SAMPLER_2D).format(T::InternalFormat::RGBA16F)
            .usage(T::Usage::COLOR_ATTACHMENT | T::Usage::SAMPLEABLE).build(engine);
        target.linear_target = f::RenderTarget::Builder().texture(A::COLOR, target.linear)
            .texture(A::DEPTH, depth).build(engine);
        if (!target.linear || !target.linear_target)
            throw FillyError("Could not create the scene-linear render target");
    }
    if (graded && !target.graded_target) {
        // RGBA8, so the driver rounds Filament's sRGB output to levels as it does for
        // gltf_viewer's swap chain. An RGBA16F buffer moved 1% of DamagedHelmet's pixels by one
        // level against gltf_viewer.
        target.graded = T::Builder().width(target.width).height(target.height).levels(1)
            .sampler(T::Sampler::SAMPLER_2D).format(T::InternalFormat::RGBA8)
            .usage(T::Usage::COLOR_ATTACHMENT | T::Usage::SAMPLEABLE).build(engine);
        target.graded_target = f::RenderTarget::Builder().texture(A::COLOR, target.graded)
            .texture(A::DEPTH, depth).build(engine);
        if (!target.graded || !target.graded_target)
            throw FillyError("Could not create the graded render target");
    }
    if (graded) output_pass(state, state.encode_graded, PassKind::encode_graded);
    if (fxaa) output_pass(state, state.fxaa, PassKind::fxaa);
    if (fxaa && !target.ldr_target) {
        target.ldr = T::Builder().width(target.width).height(target.height).levels(1)
            .sampler(T::Sampler::SAMPLER_2D).format(T::InternalFormat::RGBA8)
            .usage(T::Usage::COLOR_ATTACHMENT | T::Usage::SAMPLEABLE).build(engine);
        target.ldr_target = f::RenderTarget::Builder().texture(A::COLOR, target.ldr).build(engine);
        if (!target.ldr || !target.ldr_target) throw FillyError("Could not create the FXAA input target");
    }
}

// The region that the output passes write. With clear, they cover the whole target: outside
// the viewport the linear buffer holds the cleared background, because the scene view clears
// it as the first view of that target in the frame.
f::Viewport output_region(const TargetData& target, const f::Viewport& viewport, bool clear) {
    return clear ? f::Viewport{0, 0, target.width, target.height} : viewport;
}

// Sets the output passes' parameters. Filament commits material instances when a frame's first
// view renders, so this runs before beginFrame(); a change after the scene view would reach the
// GPU inside the render pass, or not at all.
void configure_output(State& state, const SceneData& scene, const TargetData& target, const f::Viewport& region,
                      const f::Viewport& viewport, const m::float4& clear) {
    const bool fxaa = scene.antialiasing == "fxaa";
    const bool graded = scene.filament_encodes;
    const int flags = (scene.encoding == "srgb" ? ENCODE_SRGB : 0) | (scene.transparent ? ENCODE_TRANSPARENT : 0)
                      | (scene.dithering ? ENCODE_DITHER : 0);
    auto& encode = graded ? state.encode_graded : state.encode;
    auto* source = graded ? target.graded : target.linear;
    if (encode.input != source) {
        encode.instance->setParameter("source", source,
            f::TextureSampler(f::TextureSampler::MinFilter::NEAREST, f::TextureSampler::MagFilter::NEAREST));
        encode.input = source;
    }
    if (encode.flags != flags) {
        encode.instance->setParameter("flags", int32_t(flags));
        encode.flags = flags;
    }
    if (graded) {
        // Outside the viewport the graded buffer holds the clear color as 8-bit linear values.
        // The pass stores this instead: the clear color as the RGBA16F buffer would hold it,
        // encoded as the default pass would encode it (without dithering).
        const m::float4 inner{float(viewport.left), float(viewport.bottom), float(viewport.left + int32_t(viewport.width)),
                              float(viewport.bottom + int32_t(viewport.height))};
        m::float4 background{m::half4(clear)};
        const float a = scene.transparent ? std::clamp(background.a, 0.0f, 1.0f) : 1.0f;
        for (size_t i = 0; i < 3; ++i) {
            float s = scene.transparent ? (a > 0 ? background[i] / a : 0.0f) : background[i];
            s = std::clamp(s, 0.0f, 1.0f);
            if (scene.encoding == "srgb") s = s <= 0.0031308f ? 12.92f * s : 1.055f * std::pow(s, 1 / 2.4f) - 0.055f;
            background[i] = s * a;
        }
        background.a = a;
        if (!all(equal(inner, encode.inner))) {
            encode.instance->setParameter("inner", inner);
            encode.inner = inner;
        }
        if (!all(equal(background, encode.background))) {
            encode.instance->setParameter("background", background);
            encode.background = background;
        }
    }
    // A golden-ratio sequence gives each frame a different dithering pattern.
    const float noise = scene.dithering ? float(std::fmod(double(state.stats.frames_rendered) * 0.6180339887498949, 1.0)) : 0.0f;
    const m::float4 frame{noise, 1.0f / float(region.width), 1.0f / float(region.height), 0};
    if (!all(equal(frame, encode.values))) {
        encode.instance->setParameter("frame", frame);
        encode.values = frame;
    }
    if (!fxaa) return;
    auto& pass = state.fxaa;
    if (pass.input != target.ldr) {
        pass.instance->setParameter("ldr", target.ldr,
            f::TextureSampler(f::TextureSampler::MinFilter::LINEAR, f::TextureSampler::MagFilter::LINEAR));
        pass.input = target.ldr;
    }
    if (pass.flags != int(scene.transparent)) {
        pass.instance->setParameter("transparent", int32_t(scene.transparent));
        pass.flags = int(scene.transparent);
    }
    const float w = float(target.width), h = float(target.height);
    const m::float4 texel{1 / w, 1 / h, 0, 0};
    const m::float4 bounds{(float(region.left) + 0.5f) / w, (float(region.bottom) + 0.5f) / h,
                           (float(region.left + int32_t(region.width)) - 0.5f) / w,
                           (float(region.bottom + int32_t(region.height)) - 0.5f) / h};
    if (!all(equal(texel, pass.values))) {
        pass.instance->setParameter("texel", texel);
        pass.values = texel;
    }
    if (!all(equal(bounds, pass.bounds))) {
        pass.instance->setParameter("bounds", bounds);
        pass.bounds = bounds;
    }
}

// Encodes the scene-linear buffer into the target, then runs FXAA if the scene has it.
void render_output(State& state, const SceneData& scene, TargetData& target, const f::Viewport& region) {
    const bool fxaa = scene.antialiasing == "fxaa";
    const bool whole = region.left == 0 && region.bottom == 0 && region.width == target.width
                       && region.height == target.height;
    // Every pixel of the region is written, so the old contents need not be loaded.
    f::Renderer::ClearOptions keep;
    keep.clear = false;
    keep.discard = whole;
    state.renderer->setClearOptions(keep);
    auto& encode = scene.filament_encodes ? state.encode_graded : state.encode;
    encode.view->setViewport(region);
    encode.view->setRenderTarget(fxaa ? target.ldr_target : target.output);
    state.renderer->render(encode.view);
    if (!fxaa) return;
    state.fxaa.view->setViewport(region);
    state.fxaa.view->setRenderTarget(target.output);
    state.renderer->render(state.fxaa.view);
}
}

Renderer::Renderer(uintptr_t shared_context) {
    state_ = std::make_shared<detail::State>();
    state_->interop = std::make_unique<detail::GlInterop>(shared_context);
    state_->engine = state_->interop->create_engine();
    if (!state_->engine) throw BackendError("Could not create the OpenGL engine");
    state_->renderer = state_->engine->createRenderer();
    state_->frame_chain = state_->engine->createSwapChain(1, 1, 0);
    if (!state_->frame_chain) throw BackendError("Could not create the headless frame swap chain");
    // With GL_EXT_shader_framebuffer_fetch (Intel Iris Xe 32.0.101.7088), the color-grading
    // subpass writes a black frame with a gradient tile. Only scenes that use Filament's
    // postprocessing (tone mapping other than linear, bloom, depth of field, vignette) run color
    // grading. Drivers without the extension never take that path, so the flag changes nothing
    // there. See docs/explanation/assumptions.md.
    if (!state_->engine->getDebugRegistry().setProperty("d.renderer.disable_subpasses", true))
        throw BackendError("Filament SDK lacks the required separate postprocessing pass control");
    // Without this flag, a per-channel tone mapper still goes through a 32^3 10-bit LUT in a
    // wide working gamut: unlit (1, 0, 0) became (247, 0, 0) and (0, 1, 0) became (23, 247, 6).
    // The 1D LUT applies the tone mapper per channel in fp16.
    if (!state_->engine->setFeatureFlag("engine.color_grading.use_1d_lut", true))
        throw BackendError("Filament SDK lacks the one-dimensional color-grading LUT");
    state_->materials = detail::create_material_provider(state_->engine);
    state_->names = std::make_unique<utils::NameComponentManager>(utils::EntityManager::get());
    state_->loader = g::AssetLoader::create({state_->engine, state_->materials, state_->names.get()});
    if (!state_->renderer || !state_->materials || !state_->loader)
        throw BackendError("Could not initialize Filament resources");
    // Every default render needs the encode pass; compiling it here keeps that cost out of the
    // first frame.
    detail::output_pass(*state_, state_->encode, detail::PassKind::encode);
    // No material warmup: compiling the common archive entries at renderer creation made the
    // first frames slower on both test GPUs unless the application stayed idle for seconds.
    // See docs/explanation/material-precompilation.md.
}

Scene Renderer::create_scene() {
    state_->check();
    auto data = state_->track(std::make_shared<detail::SceneData>(state_));
    data->scene = state_->engine->createScene();
    data->view = state_->engine->createView();
    data->view->setScene(data->scene);
    f::RenderQuality quality;
    // Filament's own intermediate buffers, used only with its postprocessing, MSAA, refraction,
    // or transparent views: RGB16F keeps 10 bits in [0, 1]; R11G11B10F keeps 5 or 6 and shifts
    // 8-bit output levels.
    quality.hdrColorBuffer = f::QualityLevel::HIGH;
    data->view->setRenderQuality(quality);
    data->view->setShadowingEnabled(false);
    data->view->setScreenSpaceRefractionEnabled(false);
    data->view->setAntiAliasing(f::View::AntiAliasing::NONE);
    data->view->setDithering(f::View::Dithering::NONE);
    data->view->setSampleCount(1);
    f::DynamicResolutionOptions resolution;
    resolution.enabled = false;
    data->view->setDynamicResolutionOptions(resolution);
    f::TemporalAntiAliasingOptions taa;
    taa.enabled = false;
    data->view->setTemporalAntiAliasingOptions(taa);
    f::AmbientOcclusionOptions ao;
    ao.enabled = false;
    data->view->setAmbientOcclusionOptions(ao);
    f::BloomOptions bloom;
    bloom.enabled = false;
    data->view->setBloomOptions(bloom);
    Scene scene(data);
    scene.set_tone_mapping(data->tone_mapping);
    return scene;
}

namespace {
void check_target(int64_t width, int64_t height, const std::string& format) {
    // A conservative bound also prevents overflow and accidental multi-GB readbacks.
    if (width < 1 || height < 1 || width > 8192 || height > 8192)
        throw std::invalid_argument("width and height must be from 1 through 8192, got "
                                    + std::to_string(width) + "x" + std::to_string(height));
    if (format != "rgba8") throw std::invalid_argument("format must be 'rgba8', got '" + format + "'");
}
void attach(detail::TargetData& data, f::Engine& engine, bool depth) {
    using T = f::Texture;
    auto builder = f::RenderTarget::Builder();
    builder.texture(f::RenderTarget::AttachmentPoint::COLOR, data.color);
    if (depth) {
        data.depth = T::Builder().width(data.width).height(data.height).levels(1)
            .sampler(T::Sampler::SAMPLER_2D).format(T::InternalFormat::DEPTH24)
            .usage(T::Usage::DEPTH_ATTACHMENT).build(engine);
        builder.texture(f::RenderTarget::AttachmentPoint::DEPTH, data.depth);
    }
    data.target = builder.build(engine);
    if (!data.target) throw FillyError("Could not create render target");
}
}
OffscreenTarget Renderer::create_render_target(int64_t width, int64_t height,
                                               const std::string& format, bool depth) {
    state_->check();
    check_target(width, height, format);
    auto data = state_->track(std::make_shared<detail::TargetData>(state_));
    data->width = uint32_t(width);
    data->height = uint32_t(height);
    using T = f::Texture;
    // sRGB storage lets the GPU encode on write for the direct path; read() returns the stored
    // bytes unchanged. WebGL2 always encodes writes to sRGB storage, which would encode the
    // exact path's output twice, so the web build stores RGBA8 and has no direct sRGB path.
#if defined(__EMSCRIPTEN__)
    constexpr auto color_format = T::InternalFormat::RGBA8;
#else
    constexpr auto color_format = T::InternalFormat::SRGB8_A8;
#endif
    data->color = T::Builder().width(data->width).height(data->height).levels(1)
        .sampler(T::Sampler::SAMPLER_2D).format(color_format)
        .usage(T::Usage::COLOR_ATTACHMENT | T::Usage::SAMPLEABLE | T::Usage::BLIT_SRC)
        .build(*state_->engine);
    attach(*data, *state_->engine, depth);
    return OffscreenTarget(data);
}

ImportedTarget Renderer::import_gl_texture(uint32_t texture, int64_t width, int64_t height,
                                           const std::string& format, bool depth) {
    state_->check();
    if (!state_->interop->shared()) throw InteropError("Create the renderer with shared_context first");
    check_target(width, height, format);
    state_->delete_views();
    // The encode pass writes encoded values raw, so only output_path 'direct' needs the view.
    const auto view = state_->interop->import_texture(texture, uint32_t(width), uint32_t(height),
                                                      detail::GlInterop::SrgbView::IF_IMMUTABLE);
    const bool has_view = view != texture;
    std::shared_ptr<detail::TargetData> data;
    try { data = state_->track(std::make_shared<detail::TargetData>(state_)); }
    catch (...) { if (has_view) state_->interop->delete_host_texture(view); throw; }
    if (has_view) data->host_view = view;
    data->imported = true;
    data->width = uint32_t(width);
    data->height = uint32_t(height);
    state_->interop->enqueue_wait(*state_->engine, state_->interop->host_fence());
    using T = f::Texture;
    data->color = T::Builder().width(data->width).height(data->height).levels(1)
        .sampler(T::Sampler::SAMPLER_2D).format(has_view ? T::InternalFormat::SRGB8_A8 : T::InternalFormat::RGBA8)
        .usage(T::Usage::COLOR_ATTACHMENT | T::Usage::SAMPLEABLE | T::Usage::BLIT_SRC)
        .import(view).build(*state_->engine);
    attach(*data, *state_->engine, depth);
    return ImportedTarget(data);
}

void Renderer::render(const Scene& scene, const OffscreenTarget& target, const RenderOptions& options) {
    state_->check();
    if (target.data_->state != state_) throw std::invalid_argument("Scene and target must belong to this renderer");
    target.data_->check();
    submit(scene, *target.data_, options);
}
void Renderer::render(const Scene& scene, const ImportedTarget& target, const RenderOptions& options) {
    state_->check();
    auto& data = *target.data_;
    if (data.state != state_) throw std::invalid_argument("Scene and target must belong to this renderer");
    data.check();
    state_->interop->require_host();
    if (data.access == detail::TargetData::Access::HOST)
        throw InteropError("Release the target (leave acquire()) before rendering to it again");
    const auto& settings = *scene.data_;
    if (settings.direct && settings.encoding == "srgb" && !data.host_view)
        throw InteropError("output_path 'direct' with sRGB encoding needs an imported texture with "
                           "immutable storage from glTexStorage2D; this texture was allocated with "
                           "glTexImage2D. Use output_path 'exact' or allocate it with glTexStorage2D");
    const auto start = std::chrono::steady_clock::now();
    // The previous frame was never sampled, so only Filament's own ordering applies to it.
    if (data.access == detail::TargetData::Access::SUBMITTED) state_->interop->destroy(*state_->engine, data.ready);
    state_->interop->enqueue_wait(*state_->engine, data.host_done);
    data.host_done = nullptr;
    submit(scene, data, options);
    data.access = detail::TargetData::Access::SUBMITTED;
    state_->engine->flush();
    state_->stats.cpu_submit_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
}
namespace {
// Filament scales fog color by the IBL luminance (intensity times camera exposure). Dividing
// it out makes the fog color the output color of fully fogged pixels.
void apply_fog(detail::SceneData& data, const f::Camera& camera) {
    // Without an IBL, Filament uses its default IBL at FIndirectLight::DEFAULT_INTENSITY (30000 lux).
    const float intensity = data.environment ? data.environment->getIntensity() : 30000.0f;
    const float luminance = intensity * f::Exposure::exposure(camera);
    const float scale = luminance > 0 && std::isfinite(luminance) ? 1 / luminance : 0;
    const m::float3 color{data.fog_color[0] * scale, data.fog_color[1] * scale, data.fog_color[2] * scale};
    if (all(equal(color, data.fog_applied))) return;
    f::FogOptions fog;
    fog.enabled = true;
    fog.color = color;
    fog.density = data.fog_density;
    fog.distance = data.fog_start;
    fog.heightFalloff = 0;
    fog.maximumOpacity = 1;
    data.view->setFogOptions(fog);
    data.fog_applied = color;
}
// Filament computes the circle of confusion from its camera aperture, which also sets exposure.
// Scaling it by the ratio of f-numbers applies the depth-of-field aperture instead.
void apply_depth_of_field(detail::SceneData& data, const detail::CameraData& camera) {
    const float scale = camera.camera->getAperture() / camera.aperture;
    if (scale == data.dof_scale) return;
    f::DepthOfFieldOptions dof;
    dof.enabled = true;
    dof.cocScale = scale;
    // A zero maximum keeps the bokeh orientation fixed instead of following the aperture.
    dof.maxApertureDiameter = 0;
    data.view->setDepthOfFieldOptions(dof);
    data.dof_scale = scale;
}
f::View* fill_view(detail::State& state) {
    if (state.fill_view) return state.fill_view;
    auto& engine = *state.engine;
    state.fill_scene = engine.createScene();
    state.fill_sky = f::Skybox::Builder().color({0, 0, 0, 1}).showSun(false).build(engine);
    state.fill_view = engine.createView();
    state.fill_scene->setSkybox(state.fill_sky);
    state.fill_view->setScene(state.fill_scene);
    state.fill_view->setPostProcessingEnabled(false);
    state.fill_view->setShadowingEnabled(false);
    return state.fill_view;
}
}
void Renderer::submit(const Scene& scene, detail::TargetData& target, const RenderOptions& options) {
    auto& data = *scene.data_;
    if (data.state != state_) throw std::invalid_argument("Scene and target must belong to this renderer");
    data.check();
    detail::CameraData* camera = data.active_camera.get();
    if (options.camera) {
        camera = options.camera->data_.get();
        if (camera->state != state_) throw std::invalid_argument("Camera must belong to this renderer");
    }
    if (!camera) throw FillyError("Set scene.camera or pass camera= before rendering");
    camera->check();
    uint32_t x = 0, y = 0, width = target.width, height = target.height;
    if (options.has_viewport) {
        const auto& v = options.viewport;
        if (v[0] < 0 || v[1] < 0 || v[2] < 1 || v[3] < 1 || v[0] + v[2] > target.width || v[1] + v[3] > target.height)
            throw std::invalid_argument("viewport (x, y, width, height) must lie inside the "
                + std::to_string(target.width) + "x" + std::to_string(target.height) + " target");
        x = uint32_t(v[0]); y = uint32_t(v[1]); width = uint32_t(v[2]); height = uint32_t(v[3]);
    }
    for (const auto* input : state_->host_inputs)
        if (input->writing) throw InteropError("Leave write() on every host texture before rendering");
    const auto start = std::chrono::steady_clock::now();
    for (const auto& model : data.dirty_bones) {
        if (model->asset && model->bones_dirty) model->animator->updateBoneMatrices();
        model->bones_dirty = false;
    }
    data.dirty_bones.clear();
    camera->fit_aspect(double(width) / double(height));
    if (data.fog) apply_fog(data, *camera->camera);
    if (data.depth_of_field) apply_depth_of_field(data, *camera);
    const auto& color = data.background;
    f::Renderer::ClearOptions clear;
    clear.clear = true;
    clear.discard = true;
    // An opaque view stores alpha one; the background's alpha applies only to transparent views.
    clear.clearColor = {color[0], color[1], color[2], 1};
    if (data.transparent)
        clear.clearColor = {color[0] * color[3], color[1] * color[3], color[2] * color[3], color[3]};
    const bool gpu_srgb = data.direct && data.encoding == "srgb";
    if (gpu_srgb != state_->srgb_writes) {
        state_->interop->set_srgb_writes(*state_->engine, gpu_srgb);
        state_->srgb_writes = gpu_srgb;
    }
    // Host writes to sampled textures finish before this frame reads them. Inputs that no model
    // of this scene samples keep their write fence for a frame that does.
    bool sampling = false;
    for (auto* input : state_->host_inputs) {
        input->sampled = std::any_of(input->users.begin(), input->users.end(), [&](const auto& user) {
            const auto model = user.lock();
            return model && model->asset && model->scene.lock().get() == &data;
        });
        if (!input->sampled) continue;
        sampling = true;
        state_->interop->enqueue_wait(*state_->engine, input->host_done);
        input->host_done = nullptr;
    }
    const bool exact = !data.direct;
    auto* view = data.view;
    const f::Viewport viewport{int32_t(x), int32_t(y), width, height};
    const f::Viewport region = detail::output_region(target, viewport, options.clear);
    if (exact) {
        detail::prepare_exact(*state_, target, data.antialiasing == "fxaa", data.filament_encodes);
        detail::configure_output(*state_, data, target, region, viewport,
            {clear.clearColor.r, clear.clearColor.g, clear.clearColor.b, clear.clearColor.a});
    }
    view->setViewport(viewport);
    view->setRenderTarget(!exact ? target.target : data.filament_encodes ? target.graded_target : target.linear_target);
    if (camera != data.active_camera.get()) view->setCamera(camera->camera);
    f::View* fill = nullptr;
    if (!options.clear && !exact) {
        // Filament clears whole attachments, and without a clear the direct path would keep the
        // previous frame in the viewport. The first view of a frame sets the target's clear, so
        // a background-only view goes first without one. The exact path needs no fill: its
        // scene view clears the linear buffer, and only the viewport is encoded.
        fill = fill_view(*state_);
        state_->fill_sky->setColor({clear.clearColor.r, clear.clearColor.g, clear.clearColor.b, clear.clearColor.a});
        fill->setViewport(viewport);
        fill->setRenderTarget(target.target);
        fill->setCamera(camera->camera);
        auto keep = clear;
        keep.clear = false;
        keep.discard = false;
        state_->renderer->setClearOptions(keep);
    } else {
        state_->renderer->setClearOptions(clear);
    }
    // renderStandaloneView() skips endFrame()'s garbage collection, so destroyed entity indices
    // are never recycled. A false beginFrame() only means the GPU is behind; the caller asked
    // for this frame, so it is rendered anyway, as the Renderer API permits.
    state_->renderer->beginFrame(state_->frame_chain);
    if (fill) {
        state_->renderer->render(fill);
        state_->renderer->setClearOptions(clear);
    }
    state_->renderer->render(view);
    if (exact) detail::render_output(*state_, data, target, region);
    state_->renderer->endFrame();
    view->setRenderTarget(nullptr);
    // Views must not keep a render target that the caller may close before the next call.
    if (exact) {
        state_->encode.view->setRenderTarget(nullptr);
        if (state_->encode_graded.view) state_->encode_graded.view->setRenderTarget(nullptr);
        if (state_->fxaa.view) state_->fxaa.view->setRenderTarget(nullptr);
    }
    // The fill view must not keep a camera that the caller may close before the next call.
    if (fill) { fill->setRenderTarget(nullptr); fill->setCamera(nullptr); }
    if (camera != data.active_camera.get())
        view->setCamera(data.active_camera ? data.active_camera->camera : nullptr);
    // The host waits for this frame before it samples the target, or writes a texture that the
    // frame sampled. Each fence costs a glFlush() on Filament's thread, so they share one.
    if (target.imported || sampling) {
        auto done = state_->interop->signal(*state_->engine);
        for (auto* input : state_->host_inputs) {
            if (!input->sampled) continue;
            state_->interop->destroy(*state_->engine, input->read_done);
            input->read_done = done;
        }
        if (target.imported) target.ready = std::move(done);
        else state_->interop->destroy(*state_->engine, done);
    }
    if (!target.imported) state_->engine->flush();
    target.rendered = true;
    ++state_->stats.frames_rendered;
    state_->stats.cpu_submit_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
}
void Renderer::finish() {
    state_->check();
    const auto start = std::chrono::steady_clock::now();
    detail::flush_and_wait(*state_->engine);
    state_->stats.finish_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
}
namespace detail {
struct PrepareData {
    std::shared_ptr<State> state;
    size_t pending = 0;
};
}
Preparation Renderer::prepare(const Scene& scene) {
    state_->check();
    auto& data = *scene.data_;
    if (data.state != state_) throw std::invalid_argument("Scene must belong to this renderer");
    data.check();
    auto result = std::make_shared<detail::PrepareData>();
    result->state = state_;
    auto& engine = *state_->engine;
    std::vector<const f::Material*> materials;
    auto add = [&](const f::MaterialInstance* instance) {
        if (!instance) return;
        const auto* material = instance->getMaterial();
        if (std::find(materials.begin(), materials.end(), material) == materials.end()) materials.push_back(material);
    };
    bool skinned = false;
    auto& renderables = engine.getRenderableManager();
    for (const auto& child : data.children) {
        auto model = detail::resource_cast<detail::ModelData>(child);
        if (!model || !model->asset || !model->instance) continue;
        const auto* instances = model->instance->getMaterialInstances();
        for (size_t i = 0; i < model->instance->getMaterialInstanceCount(); ++i) add(instances[i]);
        // Node-local copies and variants can bind instances that the list above lacks.
        const auto* entities = model->instance->getEntities();
        for (size_t i = 0; i < model->instance->getEntityCount(); ++i) {
            const auto renderable = renderables.getInstance(entities[i]);
            if (!renderable) continue;
            for (size_t p = 0; p < renderables.getPrimitiveCount(renderable); ++p)
                add(renderables.getMaterialInstanceAt(renderable, p));
        }
        skinned = skinned || model->instance->getSkinCount() > 0;
    }
    auto done = [result] { return [result](f::Material*) { --result->pending; }; };
    // Engine::compile() would pick the exact variants, but in Filament 1.77.1 it takes the
    // lighting specialization constants from the view's last render, so before a first render it
    // compiles programs without the directional light. Material::compile() instead compiles both
    // settings of each variant bit that the mask allows; the mask allows only what this scene
    // can use.
    bool directional = false, dynamic = false;
    auto& lights = engine.getLightManager();
    data.scene->forEach([&](utils::Entity entity) {
        const auto light = lights.getInstance(entity);
        if (!light) return;
        (lights.isDirectional(light) ? directional : dynamic) = true;
    });
    using Bit = f::UserVariantFilterBit;
    f::UserVariantFilterMask mask = 0;
    if (directional) mask |= f::UserVariantFilterMask(Bit::DIRECTIONAL_LIGHTING);
    if (dynamic) mask |= f::UserVariantFilterMask(Bit::DYNAMIC_LIGHTING);
    if (data.shadows) mask |= f::UserVariantFilterMask(Bit::SHADOW_RECEIVER);
    if (data.fog) mask |= f::UserVariantFilterMask(Bit::FOG);
    if (skinned) mask |= f::UserVariantFilterMask(Bit::SKINNING);
    for (const auto* material : materials) {
        ++result->pending;
        const_cast<f::Material*>(material)->compile(f::backend::CompilerPriorityQueue::HIGH, mask, nullptr, done());
    }
    // The output passes are unlit and have one variant each, so the view decides them exactly.
    auto compile_pass = [&](const detail::State::Pass& pass) {
        ++result->pending;
        engine.compile(f::backend::CompilerPriorityQueue::HIGH, pass.material, pass.view, utils::tribool(false),
                       utils::tribool(false), nullptr, done());
    };
    if (!data.direct) {
        compile_pass(data.filament_encodes
            ? detail::output_pass(*state_, state_->encode_graded, detail::PassKind::encode_graded) : state_->encode);
        if (data.antialiasing == "fxaa") compile_pass(detail::output_pass(*state_, state_->fxaa, detail::PassKind::fxaa));
    }
    engine.flush();
    return Preparation(result);
}
Preparation::Preparation(std::shared_ptr<detail::PrepareData> data) : data_(std::move(data)) {}
size_t Preparation::pending() const { return data_->pending; }
bool Preparation::ready() {
    if (!data_->pending) return true;
    auto& state = *data_->state;
    state.check();
    // A frame begin ticks the backend, which advances compilation and queues the completion
    // callbacks; the message queue delivers them. The frame renders nothing.
    if (state.renderer->beginFrame(state.frame_chain)) state.renderer->endFrame();
    state.engine->flush();
    state.engine->pumpMessageQueues();
    return !data_->pending;
}
void Renderer::reset_gl_state() {
    state_->check();
#if defined(__EMSCRIPTEN__)
    state_->engine->resetBackendState();
#endif
}
void Renderer::close() {
    state_->check_thread();
    // A closed host window takes its context with it. Filament's own work is still fenced by
    // State::close(); host work that sampled shared textures can no longer be fenced.
    if (state_->engine && state_->interop->shared() && state_->interop->host_current()) state_->interop->finish_host();
    state_->close();
}
uintptr_t Renderer::shared_context() const {
    state_->check_thread();
    return state_->interop->context();
}
std::string Renderer::gl_platform() const {
    state_->check_thread();
    return state_->interop->platform_name();
}
bool Renderer::closed() const { state_->check_thread(); return !state_->engine; }
Stats Renderer::stats() const {
    state_->check();
    auto result = state_->stats;
    auto& lights = state_->engine->getLightManager();
    for (const auto& entry : state_->resources) {
        auto resource = entry.lock();
        if (auto model = detail::resource_cast<detail::ModelData>(resource); model && model->asset) {
            ++result.live_models;
            result.material_copies += model->locals.size();
            for (const auto entity : model->lights) result.live_lights += bool(lights.getInstance(entity));
        }
        if (auto light = detail::resource_cast<detail::LightData>(resource); light && light->entity)
            ++result.live_lights;
    }
    return result;
}

Scene::Scene(std::shared_ptr<detail::SceneData> data) : data_(std::move(data)) {}
bool Scene::closed() const { data_->state->check_thread(); return !data_->scene || !data_->state->engine; }
void Scene::close() {
    data_->state->check_thread();
    if (!data_->scene || !data_->state->engine) return;
    // Models detach cameras that they own; scene-created cameras are detached below.
    for (auto& child : data_->children) child->release();
    detail::detach_closed_cameras(*data_->state);
    data_->release();
    data_->state->engine->flush();
}
Camera Scene::create_camera() {
    data_->check();
    auto state = data_->state;
    auto camera = state->track(std::make_shared<detail::CameraData>(state));
    camera->entity = utils::EntityManager::get().create();
    camera->camera = state->engine->createCamera(camera->entity);
    camera->camera->setExposure(16.0f, 1.0f / 125.0f, 100.0f);
    data_->children.push_back(camera);
    return Camera(camera);
}
Camera Scene::camera() const {
    data_->check();
    if (!data_->active_camera) throw FillyError("Scene has no active camera");
    return Camera(data_->active_camera);
}
void Scene::set_camera(const Camera& camera) {
    data_->check();
    camera.data_->check();
    if (camera.data_->state != data_->state)
        throw std::invalid_argument("Camera must belong to the same renderer");
    data_->active_camera = camera.data_;
    data_->view->setCamera(camera.data_->camera);
}

namespace {
// Provider-built material textures, bindings, and records from one createInstance() call. On
// failure the textures still belong to the asset, whose material instances may use them.
struct ProviderResults {
    std::vector<detail::MaterialBinding> bindings;
    std::vector<detail::MaterialRecord> records;
    void take(g::MaterialProvider* provider, detail::AssetData& shared) {
        auto textures = detail::take_material_textures(provider);
        shared.textures.insert(shared.textures.end(), textures.begin(), textures.end());
        bindings = detail::take_material_bindings(provider);
        records = detail::take_material_records(provider);
    }
};
// Forwards to a gltfio texture provider and records the textures it creates, for
// Model::memory(). gltfio exposes no list of an asset's textures.
struct RecordingProvider final : g::TextureProvider {
    RecordingProvider(g::TextureProvider* inner, std::vector<const f::Texture*>& out) : inner(inner), out(out) {}
    f::Texture* pushTexture(const uint8_t* data, size_t size, const char* mime, TextureFlags flags) override {
        auto* texture = inner->pushTexture(data, size, mime, flags);
        if (texture) out.push_back(texture);
        return texture;
    }
    f::Texture* popTexture() override { return inner->popTexture(); }
    void updateQueue() override { inner->updateQueue(); }
    const char* getPushMessage() const override { return inner->getPushMessage(); }
    const char* getPopMessage() const override { return inner->getPopMessage(); }
    void waitForCompletion() override { inner->waitForCompletion(); }
    void cancelDecoding() override { inner->cancelDecoding(); }
    size_t getPushedCount() const override { return inner->getPushedCount(); }
    size_t getPoppedCount() const override { return inner->getPoppedCount(); }
    size_t getDecodedCount() const override { return inner->getDecodedCount(); }
    g::TextureProvider* inner;
    std::vector<const f::Texture*>& out;
};
// Approximate CPU bytes of a cgltf parse beyond the document bytes that it points into.
uint64_t parse_bytes(const cgltf_data& d) {
    uint64_t total = sizeof(d) + d.nodes_count * sizeof(cgltf_node) + d.meshes_count * sizeof(cgltf_mesh)
        + d.accessors_count * sizeof(cgltf_accessor) + d.buffer_views_count * sizeof(cgltf_buffer_view)
        + d.buffers_count * sizeof(cgltf_buffer) + d.materials_count * sizeof(cgltf_material)
        + d.textures_count * sizeof(cgltf_texture) + d.images_count * sizeof(cgltf_image)
        + d.samplers_count * sizeof(cgltf_sampler) + d.skins_count * sizeof(cgltf_skin)
        + d.cameras_count * sizeof(cgltf_camera) + d.lights_count * sizeof(cgltf_light)
        + d.animations_count * sizeof(cgltf_animation);
    for (size_t i = 0; i < d.meshes_count; ++i) {
        total += d.meshes[i].primitives_count * sizeof(cgltf_primitive);
        for (size_t p = 0; p < d.meshes[i].primitives_count; ++p)
            total += d.meshes[i].primitives[p].attributes_count * sizeof(cgltf_attribute)
                     + d.meshes[i].primitives[p].targets_count * sizeof(cgltf_morph_target);
    }
    for (size_t i = 0; i < d.animations_count; ++i)
        total += d.animations[i].samplers_count * sizeof(cgltf_animation_sampler)
                 + d.animations[i].channels_count * sizeof(cgltf_animation_channel);
    return total;
}
std::filesystem::path utf8_path(const std::string& value) {
    const auto* first = reinterpret_cast<const char8_t*>(value.data());
    return std::filesystem::path(std::u8string(first, first + value.size()));
}
}

Model Scene::load(const std::string& path, bool strict, bool clonable, std::vector<std::string>& warnings) {
    data_->check();
    std::error_code error;
    const auto file = std::filesystem::absolute(utf8_path(path), error);
    if (error) throw AssetError("Could not open asset: " + path);
    const auto utf8 = file.u8string();
    const std::string absolute(reinterpret_cast<const char*>(utf8.data()), utf8.size());
    std::vector<uint8_t> bytes;
    try {
        bytes = detail::read_file(file);
    } catch (const AssetError&) {
        throw AssetError("Could not open asset: " + absolute);
    }
    return load_document(std::move(bytes), absolute, strict, clonable, warnings);
}
Model Scene::load(const uint8_t* bytes, size_t size, bool strict, bool clonable, std::vector<std::string>& warnings) {
    data_->check();
    return load_document(std::vector<uint8_t>(bytes, bytes + size), std::string(), strict, clonable, warnings);
}

Model Scene::load_document(std::vector<uint8_t> bytes, const std::string& path, bool strict, bool clonable,
                           std::vector<std::string>& warnings) {
    auto state = data_->state;
    auto prepared = detail::prepare_asset(std::move(bytes), path,
        {strict, data_->refraction}, warnings);
    const auto& tables = *prepared.tables;
    if (tables.masked && data_->direct)
        throw AssetError("Asset has alphaMode MASK materials, which need the exact output path, but "
                         "output_path is 'direct'; set output_path = 'exact' first");
    auto shared = state->track(std::make_shared<detail::AssetData>(state));
    shared->prepared = prepared.tables;
    shared->buffers = prepared.buffers;
    shared->masked = tables.masked;
    ProviderResults results;
    std::vector<std::pair<uint32_t, uint32_t>> node_indices;
    g::FilamentInstance* instance = nullptr;
    try {
        // With no instances, gltfio parses the document and builds vertex buffers but creates no
        // material instances, so its parse can be patched and read before createInstance().
        detail::set_prepared_asset(state->materials, prepared.tables.get());
        g::FilamentInstance* none = nullptr;
        shared->asset = state->loader->createInstancedAsset(prepared.bytes.data(), uint32_t(prepared.bytes.size()), &none, 0);
        detail::set_prepared_asset(state->materials, nullptr);
        if (!shared->asset) throw AssetError("Could not decode glTF/GLB asset");
        auto* source = static_cast<cgltf_data*>(const_cast<void*>(shared->asset->getSourceAsset()));
        detail::patch_source(source, prepared);
        instance = detail::create_instance(*state, *shared, node_indices);
        results.take(state->materials, *shared);
    } catch (...) {
        detail::set_prepared_asset(state->materials, nullptr);
        results.take(state->materials, *shared);
        throw;
    }
    if (!instance) throw AssetError("Could not create an instance of this asset");
    auto* asset = shared->asset;
    const auto* source = static_cast<const cgltf_data*>(asset->getSourceAsset());
    // Buffers are already in gltfio's parse. Images still load through ResourceLoader, which
    // would open files with narrow paths, so their bytes are read here.
    std::vector<std::pair<std::string, std::filesystem::path>> image_files;
    for (size_t i = 0; i < source->images_count; ++i) {
        const char* uri = source->images[i].uri;
        if (!uri || std::string_view(uri).starts_with("data:")) continue;
        if (path.empty()) throw AssetError("Byte assets must contain all resources");
        const auto resource = detail::resource_path(path, uri);
        std::error_code error;
        if (!std::filesystem::is_regular_file(resource, error)) throw AssetError("Missing glTF resource: " + std::string(uri));
        image_files.emplace_back(uri, resource);
    }
    std::unique_ptr<g::TextureProvider> stb(g::createStbProvider(state->engine));
    std::unique_ptr<g::TextureProvider> ktx2(g::createKtx2Provider(state->engine));
    std::unique_ptr<g::TextureProvider> webp_decoder(detail::create_webp_provider(state->engine));
    RecordingProvider decoder(stb.get(), shared->loaded_textures), ktx(ktx2.get(), shared->loaded_textures),
        webp(webp_decoder.get(), shared->loaded_textures);
    {
        g::ResourceLoader resources({state->engine, path.empty() ? nullptr : path.c_str(), true});
        for (const auto& [uri, file] : image_files) {
            std::unique_ptr<std::vector<uint8_t>> bytes;
            try {
                bytes = std::make_unique<std::vector<uint8_t>>(detail::read_file(file));
            } catch (const AssetError&) {
                throw AssetError("Could not read glTF resource: " + uri);
            }
            auto* payload = bytes.release();
            resources.addResourceData(uri.c_str(), g::ResourceLoader::BufferDescriptor(
                payload->data(), payload->size(), [](void*, size_t, void* user) {
                    delete static_cast<std::vector<uint8_t>*>(user);
                }, payload));
        }
        resources.addTextureProvider("image/png", &decoder);
        resources.addTextureProvider("image/jpeg", &decoder);
        resources.addTextureProvider("image/ktx2", &ktx);
        resources.addTextureProvider("image/webp", &webp);
        if (!resources.loadResources(asset)) throw AssetError("Could not load glTF resources");
        // Upload lifetimes do not need this wait. It keeps the upload out of the first trial frame.
        detail::flush_and_wait(*state->engine);
    }
    auto model = state->track(std::make_shared<detail::ModelData>(state));
    model->scene = data_;
    model->shared = shared;
    model->asset = asset;
    model->instance = instance;
    model->records = std::move(results.records);
    model->node_indices = std::move(node_indices);
    detail::initialize_model(*model, results.bindings);
    if (tables.masked) ++data_->masked;
    // Only AssetLoader::createInstance() reads the source data (about the file size), the
    // buffers it points into, and the instance tables after this point, so they are kept only
    // for assets that clone() may copy.
    shared->clonable = clonable;
    shared->instances = 1;
    if (clonable) {
        // gltfio keeps its own copy of the document, and the parse points into it and into the
        // buffers that preparation loaded.
        shared->source_bytes = prepared.bytes.size() + parse_bytes(*source);
        for (const auto& storage : shared->buffers->storage) shared->source_bytes += storage.size();
    } else {
        asset->releaseSourceData();
        shared->buffers.reset();
        auto& kept = *shared->prepared;
        kept.diffuse = {};
        kept.surfaces = {};
        kept.plans = {};
        kept.textures = {};
        kept.animations = {};
        kept.cameras = {};
        kept.materials = {};
        kept.decoded = {};
    }
    data_->children.push_back(model);
    detail::update_visibility(*model);
    detail::update_diffuse_environment(*data_);
    return Model(model);
}

Model Scene::create_mesh(const MeshArrays& arrays, const MeshMaterial& material) {
    data_->check();
    if (!arrays.positions || !arrays.indices || arrays.vertices < 3 || arrays.triangles < 1)
        throw std::invalid_argument("A mesh needs at least 3 positions and 1 triangle");
    for (size_t i = 0; i < arrays.vertices * 3; ++i)
        if (!std::isfinite(arrays.positions[i])) throw std::invalid_argument("Positions must be finite");
    // A one-primitive glTF gives the mesh a node and the loader's own material; its geometry is
    // then replaced, so the model behaves like any loaded asset.
    std::array<float, 3> low, high;
    for (int axis = 0; axis < 3; ++axis) {
        low[axis] = high[axis] = arrays.positions[axis];
        for (size_t i = 0; i < arrays.vertices; ++i) {
            low[axis] = std::min(low[axis], arrays.positions[3 * i + axis]);
            high[axis] = std::max(high[axis], arrays.positions[3 * i + axis]);
        }
    }
    auto document = detail::mesh_placeholder(low, high, arrays.colors != nullptr, material);
    std::vector<std::string> warnings;
    Model model = load_document(std::move(document), std::string(), true, true, warnings);
    try {
        model.attach_mesh(arrays);
    } catch (...) {
        model.close();
        throw;
    }
    return model;
}

Model Model::clone(const Scene* into) {
    data_->check();
    auto state = data_->state;
    // The asset belongs to the renderer, not to a scene, so a clone can go into any scene of it.
    // Each model holds the asset, so a scene that closes takes only its own models with it.
    auto scene = into ? into->data_ : data_->scene.lock();
    if (!scene || !scene->scene) throw FillyError("Scene is closed");
    if (scene->state != state) throw std::invalid_argument("Clone into a scene of the same renderer");
    auto& shared = *data_->shared;
    if (!shared.clonable)
        throw FillyError("This asset released its source data after loading; "
                            "load it with scene.load(source, clonable=True) to clone it");
    if (shared.masked && scene->direct)
        throw AssetError("Asset has alphaMode MASK materials, which need the exact output path, but "
                         "the scene's output_path is 'direct'; set output_path = 'exact' first");
    ProviderResults results;
    std::vector<std::pair<uint32_t, uint32_t>> node_indices;
    g::FilamentInstance* instance = nullptr;
    try {
        instance = detail::create_instance(*state, shared, node_indices);
    } catch (...) {
        results.take(state->materials, shared);
        throw;
    }
    results.take(state->materials, shared);
    if (!instance) throw AssetError("Could not create another instance of this asset");
    auto model = state->track(std::make_shared<detail::ModelData>(state));
    model->scene = scene;
    model->shared = data_->shared;
    model->asset = shared.asset;
    model->instance = instance;
    model->records = std::move(results.records);
    model->node_indices = std::move(node_indices);
    detail::initialize_model(*model, results.bindings);
    ++shared.instances;
    if (shared.mesh) detail::attach_geometry(*model);
    if (shared.masked) ++scene->masked;
    scene->children.push_back(model);
    detail::update_visibility(*model);
    detail::update_diffuse_environment(*scene);
    state->engine->flush();
    return Model(model);
}

Light Scene::add_directional_light(Vec3 direction, float intensity, Vec3 color) {
    data_->check();
    finite(direction); finite(intensity); finite(color);
    if (length(vec(direction)) < 1e-6f || intensity < 0)
        throw std::invalid_argument("Light requires a nonzero direction and nonnegative intensity");
    for (float value : color)
        if (value < 0 || value > 1) throw std::invalid_argument("Light color must be in [0, 1]");
    auto entity = utils::EntityManager::get().create();
    auto result = f::LightManager::Builder(f::LightManager::Type::DIRECTIONAL)
        .direction(normalize(vec(direction))).color(vec(color)).intensity(intensity)
        .castShadows(false).build(*data_->state->engine, entity);
    if (result != f::LightManager::Builder::Success) {
        utils::EntityManager::get().destroy(entity);
        throw FillyError("Could not create light");
    }
    return Light(detail::own_light(data_, entity), entity.getId());
}
Vec4 Scene::background() const { data_->check(); return data_->background; }
void Scene::set_background(Vec4 color) {
    data_->check(); finite(color);
    for (float value : color)
        if (value < 0 || value > 1) throw std::invalid_argument("Background must be in [0, 1]");
    data_->background = color;
}

Camera::Camera(std::shared_ptr<detail::CameraData> data) : data_(std::move(data)) {}
bool Camera::same(const Camera& other) const { return data_ == other.data_; }
uintptr_t Camera::key() const { return reinterpret_cast<uintptr_t>(data_.get()); }
bool Camera::has_node() const { data_->state->check_thread(); return data_->imported; }
Node Camera::node() const {
    data_->check();
    if (!data_->imported) throw FillyError("Only imported cameras have a node");
    return Node(data_->model.lock(), data_->entity.getId());
}
namespace {
void owned_projection(const detail::CameraData& data, const char* what) {
    if (data.imported)
        throw FillyError(std::string("Imported cameras keep their glTF projection; ") + what
                            + " applies to cameras from scene.create_camera()");
}
}
void Camera::set_perspective(double fov_y, double aspect, double near, double far) {
    data_->check(); owned_projection(*data_, "set_perspective()");
    clipping(near, far); finite(fov_y); finite(aspect);
    if (!(fov_y > 0 && fov_y < 180 && aspect >= 0))
        throw std::invalid_argument("Require 0 < fov_y < 180 degrees and aspect > 0");
    data_->size = fov_y; data_->near = near; data_->far = far;
    if (aspect > 0) {
        data_->fit = detail::CameraData::Fit::FIXED;
        data_->camera->setProjection(fov_y, aspect, near, far, f::Camera::Fov::VERTICAL);
    } else {
        data_->fit = detail::CameraData::Fit::PERSPECTIVE;
        data_->apply_fit();
    }
}
void Camera::set_lens_projection(double focal_length, double aspect, double near, double far) {
    data_->check(); owned_projection(*data_, "set_lens_projection()");
    clipping(near, far); finite(focal_length); finite(aspect);
    if (!(focal_length > 0 && aspect >= 0))
        throw std::invalid_argument("Require focal_length_mm > 0 and aspect > 0");
    data_->size = focal_length; data_->near = near; data_->far = far;
    if (aspect > 0) {
        data_->fit = detail::CameraData::Fit::FIXED;
        data_->camera->setLensProjection(focal_length, aspect, near, far);
    } else {
        data_->fit = detail::CameraData::Fit::LENS;
        data_->apply_fit();
    }
}
void Camera::set_orthographic(double left, double right, double bottom, double top,
                               double near, double far) {
    data_->check(); owned_projection(*data_, "set_orthographic()");
    clipping(near, far);
    finite(left); finite(right); finite(bottom); finite(top);
    if (!(left < right && bottom < top))
        throw std::invalid_argument("Require left < right and bottom < top");
    data_->fit = detail::CameraData::Fit::FIXED;
    data_->camera->setProjection(f::Camera::Projection::ORTHO, left, right, bottom, top, near, far);
}
void Camera::set_orthographic_height(double height, double center_x, double center_y,
                                      double near, double far) {
    data_->check(); owned_projection(*data_, "set_orthographic()");
    clipping(near, far);
    finite(height); finite(center_x); finite(center_y);
    if (!(height > 0)) throw std::invalid_argument("Require height > 0");
    data_->fit = detail::CameraData::Fit::ORTHOGRAPHIC;
    data_->size = height; data_->center_x = center_x; data_->center_y = center_y;
    data_->near = near; data_->far = far;
    data_->apply_fit();
}
namespace {
// A framing target in world space: the sphere that circumscribes its box, and the box corners.
struct FrameTarget {
    m::double3 center;
    double radius = 0;
    std::array<m::double3, 8> corners;
};
FrameTarget frame_target(const std::array<Vec3, 2>& box, const m::mat4& world) {
    const auto& [low, high] = box;
    for (size_t i = 0; i < 3; ++i)
        if (!(std::isfinite(low[i]) && std::isfinite(high[i]) && low[i] <= high[i]))
            throw std::invalid_argument("The target's bounds are empty or not finite");
    auto point = [&](double x, double y, double z) { return (world * m::double4{x, y, z, 1}).xyz; };
    FrameTarget target;
    const m::double3 lo{low[0], low[1], low[2]}, hi{high[0], high[1], high[2]};
    const auto middle = (lo + hi) * 0.5;
    target.center = point(middle.x, middle.y, middle.z);
    // The largest column norm is the largest scale factor of a rotation-scale transform, so a
    // rotation of the target does not change the sphere.
    double scale = 0;
    for (size_t c = 0; c < 3; ++c) scale = std::max(scale, length(world[c].xyz));
    target.radius = length(hi - lo) * 0.5 * scale;
    for (size_t k = 0; k < 8; ++k)
        target.corners[k] = point(k & 1 ? hi.x : lo.x, k & 2 ? hi.y : lo.y, k & 4 ? hi.z : lo.z);
    if (!(target.radius > 0) || !std::isfinite(target.radius))
        throw std::invalid_argument("The target has zero size");
    return target;
}

double frame_camera(detail::CameraData& data, const FrameTarget& target, const FrameOptions& options) {
    owned_projection(data, "frame()");
    const double fill = options.fill;
    if (!(fill > 0 && fill <= 1)) throw std::invalid_argument("fill must be in (0, 1]");
    const bool sphere = options.fit == "sphere";
    if (!sphere && options.fit != "box")
        throw std::invalid_argument("fit must be 'sphere' or 'box', got '" + options.fit + "'");
    if (options.near) {
        finite(*options.near);
        if (!(*options.near > 0)) throw std::invalid_argument("Require near > 0");
    }
    if (options.far) finite(*options.far);
    if (options.aspect && !(std::isfinite(*options.aspect) && *options.aspect > 0))
        throw std::invalid_argument("aspect must be finite and positive");
    auto* camera = data.camera;
    m::double3 forward = camera->getForwardVector();
    if (options.direction) {
        finite(*options.direction);
        forward = {(*options.direction)[0], (*options.direction)[1], (*options.direction)[2]};
    }
    if (!(length(forward) > 1e-12)) throw std::invalid_argument("direction must be a nonzero vector");
    forward = normalize(forward);
    finite(options.up);
    const m::double3 up{options.up[0], options.up[1], options.up[2]};
    if (!(length(up) > 1e-12) || length(cross(forward, normalize(up))) < 1e-6)
        throw std::invalid_argument("up must be nonzero and not parallel to the viewing direction");
    const auto right = normalize(cross(forward, up));
    const auto upward = cross(right, forward);

    // Filament's projection holds the field of view and the aspect that the last render applied
    // (1 before the first render of a camera that follows its target).
    const m::mat4 projection = camera->getProjectionMatrix();
    const bool orthographic = projection[2][3] == 0;
    const double fixed_aspect = projection[1][1] / projection[0][0];
    const double aspect = options.aspect.value_or(fixed_aspect);
    const double tan_v = 1 / projection[1][1], tan_h = tan_v * aspect;

    // Corner coordinates relative to the center along the camera's right, up, and forward axes.
    double extent_x = 0, extent_y = 0, z_min = 0, z_max = 0;
    std::array<m::double3, 8> local;
    for (size_t k = 0; k < 8; ++k) {
        const auto q = target.corners[k] - target.center;
        local[k] = {dot(q, right), dot(q, upward), dot(q, forward)};
        extent_x = std::max(extent_x, std::abs(local[k].x));
        extent_y = std::max(extent_y, std::abs(local[k].y));
        z_min = std::min(z_min, local[k].z);
        z_max = std::max(z_max, local[k].z);
    }
    if (sphere) {
        extent_x = extent_y = target.radius;
        z_min = -target.radius;
        z_max = target.radius;
    }
    // A flat target has no depth; the margins scale with its size instead.
    const double depth = std::max(z_max - z_min, target.radius);

    double distance = 0, height = 0;
    if (!orthographic) {
        if (sphere) {
            // The sphere's silhouette spans `fill` of the narrower view axis: the tangent of its
            // angular radius is `fill` times the tangent of that axis's half-angle.
            const double t = fill * std::min(tan_v, tan_h);
            distance = target.radius * std::sqrt(1 + 1 / (t * t));
        } else {
            // Every corner projects within `fill` of the half-extent on both axes.
            for (const auto& q : local)
                distance = std::max(distance, std::max(std::abs(q.x) / (fill * tan_h), std::abs(q.y) / (fill * tan_v)) - q.z);
            // Keep every corner in front of the camera.
            distance = std::max(distance, -z_min + 0.05 * depth);
        }
    } else {
        height = sphere ? 2 * target.radius / (fill * std::min(1.0, aspect))
                        : std::max(2 * extent_y / fill, 2 * extent_x / (fill * aspect));
        // Far enough that the near plane has room in front of the target.
        distance = -z_min + 0.1 * depth;
    }
    const double margin = 0.05 * depth;
    const double near = options.near.value_or(std::max(distance + z_min - margin, 0.5 * (distance + z_min)));
    const double far = options.far.value_or(distance + z_max + margin);
    clipping(near, far);

    const m::double3 eye = target.center - forward * distance;
    data.set_world_transform(m::mat4::lookAt(eye, target.center, upward));
    using Fit = detail::CameraData::Fit;
    if (orthographic) {
        if (data.fit != Fit::ORTHOGRAPHIC) data.aspect = aspect;
        data.fit = Fit::ORTHOGRAPHIC;
        data.size = height;
        data.center_x = data.center_y = 0;
        data.near = near;
        data.far = far;
        data.apply_fit();
    } else if (data.fit == Fit::PERSPECTIVE || data.fit == Fit::LENS) {
        data.near = near;
        data.far = far;
        data.apply_fit();
    } else {
        // A fixed projection keeps its field of view and aspect.
        const double fov_y = 2 * std::atan(tan_v) * 180 / 3.141592653589793;
        camera->setProjection(fov_y, fixed_aspect, near, far, f::Camera::Fov::VERTICAL);
    }
    return distance;
}

m::mat4 model_world(const detail::ModelData& model) {
    auto& tm = model.state->engine->getTransformManager();
    return tm.getWorldTransformAccurate(tm.getInstance(model.instance->getRoot()));
}
}
double Camera::frame(const Model& target, const FrameOptions& options) {
    data_->check();
    target.data_->check();
    return frame_camera(*data_, frame_target(target.bounds(), model_world(*target.data_)), options);
}
double Camera::frame(const Node& target, const FrameOptions& options) {
    data_->check();
    target.data_->check();
    return frame_camera(*data_, frame_target(target.bounds(), model_world(*target.data_)), options);
}
double Camera::frame(const std::array<Vec3, 2>& box, const FrameOptions& options) {
    data_->check();
    return frame_camera(*data_, frame_target(box, m::mat4()), options);
}
Vec3 Camera::position() const { data_->check(); return vec(data_->camera->getPosition()); }
void Camera::set_position(Vec3 value) {
    data_->check(); finite(value);
    auto transform = data_->camera->getModelMatrix();
    transform[3] = {value[0], value[1], value[2], 1};
    data_->set_world_transform(transform);
}
void Camera::look_at(Vec3 target, Vec3 up) {
    data_->check(); finite(target); finite(up);
    auto eye = data_->camera->getPosition();
    auto direction = vec(target) - eye;
    if (length(direction) < 1e-6f || length(vec(up)) < 1e-6f ||
        length(cross(normalize(direction), normalize(vec(up)))) < 1e-6f)
        throw std::invalid_argument("Look-at requires distinct eye/target and a nonparallel up vector");
    data_->set_world_transform(m::mat4::lookAt(eye, vec(target), vec(up)));
}
Matrix Camera::transform() const { data_->check(); return matrix(data_->camera->getModelMatrix()); }
void Camera::set_transform(const Matrix& value) {
    data_->check(); data_->set_world_transform(m::mat4(matrix(value)));
}
Matrix Camera::view_matrix() const { data_->check(); return matrix(data_->camera->getViewMatrix()); }
Matrix Camera::projection() const { data_->check(); return matrix(data_->camera->getProjectionMatrix()); }

Model::Model(std::shared_ptr<detail::ModelData> data) : data_(std::move(data)) {}
std::array<Vec3, 2> Model::bounds() const {
    data_->check();
    // Not gltfio's getBoundingBox(), which is wrong for skinned meshes; see compute_bounds.
    return data_->shared->prepared->bounds;
}
Node Model::root() const {
    data_->check();
    return Node(data_, data_->instance->getRoot().getId());
}
Node Model::node(int64_t index) const {
    data_->check();
    if (index < 0 || size_t(index) >= data_->nodes.size() || !data_->nodes[size_t(index)])
        throw AssetError("No node with glTF index " + std::to_string(index));
    return Node(data_, data_->nodes[size_t(index)].getId());
}
Node Model::node(const std::string& name) const {
    data_->check();
    std::vector<size_t> matches;
    const auto& sources = data_->shared->prepared->nodes;
    for (size_t i = 0; i < data_->nodes.size() && i < sources.size(); ++i)
        if (data_->nodes[i] && sources[i].name == name) matches.push_back(i);
    if (matches.empty()) throw unknown_name("node", name, node_names());
    if (matches.size() > 1) {
        std::string list;
        for (auto i : matches) list += (list.empty() ? "" : ", ") + std::to_string(i);
        throw AssetError("Ambiguous node name '" + name + "'; use a glTF index: " + list);
    }
    return Node(data_, data_->nodes[matches[0]].getId());
}
std::vector<Node> Model::nodes() const {
    data_->check();
    std::vector<Node> result;
    for (const auto entity : data_->nodes) if (entity) result.emplace_back(data_, entity.getId());
    return result;
}
std::vector<std::string> Model::node_names() const {
    data_->check();
    std::vector<std::string> names;
    const auto& sources = data_->shared->prepared->nodes;
    for (size_t i = 0; i < data_->nodes.size() && i < sources.size(); ++i)
        if (data_->nodes[i] && sources[i].name) names.push_back(*sources[i].name);
    return names;
}
std::vector<std::string> Model::material_names() const {
    data_->check();
    auto* instance = data_->instance;
    std::vector<std::string> names;
    for (size_t i = 0; i < instance->getMaterialInstanceCount(); ++i)
        names.emplace_back(instance->getMaterialInstances()[i]->getName());
    return names;
}
Material Model::material(const std::string& name) const {
    data_->check();
    const auto* instances = data_->instance->getMaterialInstances();
    size_t index = 0, count = 0;
    for (size_t i = 0; i < data_->instance->getMaterialInstanceCount(); ++i) {
        const char* candidate = instances[i]->getName();
        if (name == (candidate ? candidate : "")) { index = i; ++count; }
    }
    if (!count) throw unknown_name("material", name, material_names());
    if (count != 1) throw AssetError("Ambiguous material name: " + name);
    return Material(data_, index);
}
Matrix Model::transform() const {
    data_->check();
    auto& tm = data_->state->engine->getTransformManager();
    return matrix(tm.getTransform(tm.getInstance(data_->instance->getRoot())));
}
void Model::set_transform(const Matrix& value) {
    data_->check();
    const auto transform = matrix(value);
    auto& tm = data_->state->engine->getTransformManager();
    tm.setTransform(tm.getInstance(data_->instance->getRoot()), transform);
}
Vec3 Model::position() const { auto t = transform(); return {t[3], t[7], t[11]}; }
void Model::set_position(Vec3 value) {
    finite(value);
    auto t = transform();
    t[3] = value[0]; t[7] = value[1]; t[11] = value[2];
    set_transform(t);
}
bool Model::visible() const { data_->check(); return data_->visible; }
void Model::set_visible(bool value) {
    data_->check();
    if (value == data_->visible) return;
    data_->visible = value;
    detail::update_visibility(*data_);
}

namespace {
struct Trs { m::float3 position, scale; m::quatf rotation; };
Trs decompose(const Matrix& value) {
    const auto transform = matrix(value);
    Trs result;
    g::decomposeMatrix(transform, &result.position, &result.rotation, &result.scale);
    const auto rebuilt = matrix(g::composeMatrix(result.position, result.rotation, result.scale));
    for (size_t i = 0; i < value.size(); ++i)
        if (std::abs(value[i] - rebuilt[i]) > 1e-4f * std::max(1.0f, std::abs(value[i])))
            throw std::invalid_argument("Rotation/scale properties require a transform without shear");
    for (float scale : vec(result.scale))
        if (std::abs(scale) < 1e-6f) throw std::invalid_argument("Rotation/scale properties require nonzero scale");
    return result;
}
f::MaterialInstance* material_instance(const std::shared_ptr<detail::ModelData>& data, size_t index,
                                      const char* parameter) {
    data->check();
    auto* instance = data->instance;
    const size_t count = instance->getMaterialInstanceCount();
    if (index >= count + data->locals.size()) throw AssetError("Invalid material index");
    auto* material = index < count ? detail::shown(*data, instance->getMaterialInstances()[index])
                                   : data->locals[index - count].copy;
    if (!material->getMaterial()->hasParameter(parameter))
        throw AssetError(std::string("Material does not support ") + parameter);
    return material;
}
void unit_interval(float value) {
    finite(value);
    if (value < 0 || value > 1) throw std::invalid_argument("Value must be in [0, 1]");
}
}

Node::Node(std::shared_ptr<detail::ModelData> data, uint32_t entity)
    : data_(std::move(data)), entity_(entity) {}
bool Node::same(const Node& other) const { return data_ == other.data_ && entity_ == other.entity_; }
uintptr_t Node::key() const { return reinterpret_cast<uintptr_t>(data_.get()) ^ (uintptr_t(entity_) * 0x9E3779B97F4A7C15ull); }
std::optional<std::string> Node::name() const {
    data_->check();
    const auto* source = detail::node_source(*data_, utils::Entity::import(entity_));
    return source ? source->name : std::nullopt;
}
std::optional<std::string> Node::mesh_name() const {
    data_->check();
    const auto* source = detail::node_source(*data_, utils::Entity::import(entity_));
    return source ? source->mesh : std::nullopt;
}
int64_t Node::index() const {
    data_->check();
    const auto entity = utils::Entity::import(entity_);
    for (size_t i = 0; i < data_->nodes.size(); ++i) if (data_->nodes[i] == entity) return int64_t(i);
    return -1;
}
std::array<Vec3, 2> Node::bounds() const {
    data_->check();
    const auto& prepared = *data_->shared->prepared;
    const auto index = detail::node_index(*data_, utils::Entity::import(entity_));
    // The model root is not a glTF node; its subtree is the whole model.
    if (index == SIZE_MAX) return prepared.bounds;
    std::array<Vec3, 2> result = {Vec3{FLT_MAX, FLT_MAX, FLT_MAX}, Vec3{-FLT_MAX, -FLT_MAX, -FLT_MAX}};
    for (size_t i = 0; i < prepared.node_boxes.size(); ++i) {
        const auto& [low, high] = prepared.node_boxes[i];
        if (low[0] > high[0]) continue;
        size_t ancestor = i;
        while (ancestor != index && ancestor < prepared.parents.size()) ancestor = prepared.parents[ancestor];
        if (ancestor != index) continue;
        for (size_t a = 0; a < 3; ++a) {
            result[0][a] = std::min(result[0][a], low[a]);
            result[1][a] = std::max(result[1][a], high[a]);
        }
    }
    return result;
}
bool Node::has_parent() const {
    data_->check();
    auto& tm = data_->state->engine->getTransformManager();
    const auto parent = tm.getParent(tm.getInstance(utils::Entity::import(entity_)));
    return parent && parent != data_->instance->getRoot();
}
Node Node::parent() const {
    if (!has_parent()) throw AssetError("Node has no parent node");
    auto& tm = data_->state->engine->getTransformManager();
    return Node(data_, tm.getParent(tm.getInstance(utils::Entity::import(entity_))).getId());
}
std::vector<Node> Node::children() const {
    data_->check();
    auto& tm = data_->state->engine->getTransformManager();
    std::vector<Node> result;
    for (auto child : tm.getChildrenRange(tm.getInstance(utils::Entity::import(entity_))))
        result.emplace_back(data_, tm.getEntity(child).getId());
    // Filament keeps children in reverse insertion order; glTF order is easier to reason about.
    std::sort(result.begin(), result.end(), [](const Node& a, const Node& b) { return a.index() < b.index(); });
    return result;
}
Matrix Node::transform() const {
    data_->check();
    auto& tm = data_->state->engine->getTransformManager();
    return matrix(tm.getTransform(tm.getInstance(utils::Entity::import(entity_))));
}
void Node::set_transform(const Matrix& value) {
    data_->check();
    auto transform = matrix(value);
    auto& tm = data_->state->engine->getTransformManager();
    const auto entity = utils::Entity::import(entity_);
    tm.setTransform(tm.getInstance(entity), transform);
    data_->mark_bones(entity);
}
Vec3 Node::position() const { auto t = transform(); return {t[3], t[7], t[11]}; }
void Node::set_position(Vec3 value) {
    finite(value);
    auto t = transform();
    t[3] = value[0]; t[7] = value[1]; t[11] = value[2];
    set_transform(t);
}
Vec3 Node::scale() const { return vec(decompose(transform()).scale); }
void Node::set_scale(Vec3 value) {
    finite(value);
    for (auto v : value) if (std::abs(v) < 1e-6f) throw std::invalid_argument("Scale must be nonzero");
    auto trs = decompose(transform());
    set_transform(matrix(g::composeMatrix(trs.position, trs.rotation, vec(value))));
}
Vec4 Node::quaternion() const {
    auto q = decompose(transform()).rotation;
    return {q.x, q.y, q.z, q.w};
}
void Node::set_quaternion(Vec4 value) {
    finite(value);
    double norm = 0;
    for (auto v : value) norm += double(v) * v;
    if (norm < 1e-12) throw std::invalid_argument("Quaternion must be nonzero");
    const double inverse = 1 / std::sqrt(norm);
    m::quatf q;
    q.x = float(value[0] * inverse); q.y = float(value[1] * inverse);
    q.z = float(value[2] * inverse); q.w = float(value[3] * inverse);
    auto trs = decompose(transform());
    set_transform(matrix(g::composeMatrix(trs.position, q, trs.scale)));
}
Vec3 Node::rotation_euler_rad() const {
    auto trs = decompose(transform());
    auto r = matrix(g::composeMatrix({0, 0, 0}, trs.rotation, {1, 1, 1}));
    float y = std::asin(std::clamp(-r[8], -1.0f, 1.0f));
    if (std::abs(std::cos(y)) < 1e-6f) return {std::atan2(-r[6], r[5]), y, 0};
    return {std::atan2(r[9], r[10]), y, std::atan2(r[4], r[0])};
}
void Node::set_rotation_euler_rad(Vec3 value) {
    finite(value);
    double x = value[0] / 2.0, y = value[1] / 2.0, z = value[2] / 2.0;
    double cx = std::cos(x), sx = std::sin(x), cy = std::cos(y), sy = std::sin(y);
    double cz = std::cos(z), sz = std::sin(z);
    set_quaternion({float(sx*cy*cz - cx*sy*sz), float(cx*sy*cz + sx*cy*sz),
                    float(cx*cy*sz - sx*sy*cz), float(cx*cy*cz + sx*sy*sz)});
}
Vec3 Node::rotation_euler_deg() const {
    auto angles = rotation_euler_rad();
    for (auto& angle : angles) angle *= float(180.0 / 3.141592653589793);
    return angles;
}
void Node::set_rotation_euler_deg(Vec3 value) {
    for (auto& angle : value) angle *= float(3.141592653589793 / 180.0);
    set_rotation_euler_rad(value);
}
Material Node::material(size_t slot) const {
    data_->check();
    auto& rm = data_->state->engine->getRenderableManager();
    const auto entity = utils::Entity::import(entity_);
    auto renderable = rm.getInstance(entity);
    if (!renderable || slot >= rm.getPrimitiveCount(renderable)) throw AssetError("Invalid material slot on node");
    const size_t count = data_->instance->getMaterialInstanceCount();
    for (size_t i = 0; i < data_->locals.size(); ++i)
        if (data_->locals[i].entity == entity && data_->locals[i].slot == slot) return Material(data_, count + i);
    auto* bound = rm.getMaterialInstanceAt(renderable, slot);
    // A copy of a textured shared material keeps its textures, but animation still reaches it
    // through the glTF instance.
    detail::Assignment assignment;
    auto* source = detail::glTF_source(*data_, bound, &assignment);
    auto* copy = f::MaterialInstance::duplicate(bound);
    try { data_->locals.push_back({entity, slot, source, copy, std::move(assignment)}); }
    catch (...) { data_->state->engine->destroy(copy); throw; }
    rm.setMaterialInstanceAt(renderable, slot, copy);
    return Material(data_, count + data_->locals.size() - 1);
}

Material::Material(std::shared_ptr<detail::ModelData> data, size_t index) : data_(std::move(data)), index_(index) {}
Vec4 Material::base_color() const {
    auto color = material_instance(data_, index_, "baseColorFactor")->getParameter<m::float4>("baseColorFactor");
    return {color.x, color.y, color.z, color.w};
}
void Material::set_base_color(Vec4 value) {
    for (auto v : value) unit_interval(v);
    material_instance(data_, index_, "baseColorFactor")->setParameter("baseColorFactor",
        m::float4{value[0], value[1], value[2], value[3]});
}
float Material::metallic() const {
    return material_instance(data_, index_, "metallicFactor")->getParameter<float>("metallicFactor");
}
void Material::set_metallic(float value) {
    unit_interval(value);
    material_instance(data_, index_, "metallicFactor")->setParameter("metallicFactor", value);
}
std::pair<std::string, bool> Material::shader() const {
    const auto* material = material_instance(data_, index_, "baseColorFactor")->getMaterial();
    return {material->getName() ? material->getName() : "",
            material->getRefractionMode() == f::RefractionMode::SCREEN_SPACE};
}
float Material::roughness() const {
    return material_instance(data_, index_, "roughnessFactor")->getParameter<float>("roughnessFactor");
}
void Material::set_roughness(float value) {
    unit_interval(value);
    material_instance(data_, index_, "roughnessFactor")->setParameter("roughnessFactor", value);
}

OffscreenTarget::OffscreenTarget(std::shared_ptr<detail::TargetData> data) : data_(std::move(data)) {}
uint32_t OffscreenTarget::width() const { data_->check(); return data_->width; }
uint32_t OffscreenTarget::height() const { data_->check(); return data_->height; }
bool OffscreenTarget::closed() const { data_->state->check_thread(); return !data_->target || !data_->state->engine; }
void OffscreenTarget::close() {
    data_->state->check_thread();
    if (!data_->target || !data_->state->engine) return;
    data_->release();
    detail::flush_and_wait(*data_->state->engine);
}
namespace detail {
struct ReadbackData {
    std::shared_ptr<State> state;
    PixelBuffer pixels;
    bool complete = false, taken = false;
    std::chrono::steady_clock::time_point start;
};
}
Readback OffscreenTarget::begin_read() const {
    auto state = data_->state;
    data_->check();
    if (!data_->rendered) throw FillyError("Render to the target before reading it");
    auto result = std::make_shared<detail::ReadbackData>();
    result->state = state;
    result->start = std::chrono::steady_clock::now();
    result->pixels.size = size_t(data_->width) * data_->height * 4;
    result->pixels.data.reset(new uint8_t[result->pixels.size]);
    // The callback retains storage even if the driver cannot complete the read.
    auto* owner = new std::shared_ptr<detail::ReadbackData>(result);
    f::backend::PixelBufferDescriptor buffer(result->pixels.data.get(), result->pixels.size,
        f::backend::PixelDataFormat::RGBA, f::backend::PixelDataType::UBYTE,
        [](void*, size_t, void* user) {
            std::unique_ptr<std::shared_ptr<detail::ReadbackData>> hold(static_cast<std::shared_ptr<detail::ReadbackData>*>(user));
            (*hold)->complete = true;
        }, owner);
    state->renderer->readPixels(data_->target, 0, 0, data_->width, data_->height, std::move(buffer));
    state->engine->flush();
    return Readback(result);
}
Readback::Readback(std::shared_ptr<detail::ReadbackData> data) : data_(std::move(data)) {}
bool Readback::ready() {
    if (!data_->complete) {
        data_->state->check();
        // The driver's finish() checks the readback's fence; the user callback then arrives
        // through the message queue.
        detail::flush_and_wait(*data_->state->engine);
        data_->state->engine->pumpMessageQueues();
        if (data_->complete)
            data_->state->stats.readback_ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - data_->start).count();
    }
    return data_->complete;
}
namespace {
PixelBuffer take_pixels(detail::ReadbackData& data) {
    if (!data.complete) throw FillyError("The readback has not completed");
    if (data.taken) throw FillyError("The readback's pixels were already taken");
    data.taken = true;
    // Filament already normalizes readPixels output to an upper-left origin.
    return std::move(data.pixels);
}
}
std::vector<uint8_t> Readback::take() {
    auto pixels = take_pixels(*data_);
    return std::vector<uint8_t>(pixels.data.get(), pixels.data.get() + pixels.size);
}
PixelBuffer OffscreenTarget::read() const {
    auto readback = begin_read();
#if defined(__EMSCRIPTEN__)
    if (!readback.ready())
        throw FillyError("WebGL completes readbacks only after control returns to the browser; "
                         "use begin_read() and poll ready()");
#else
    if (!readback.ready()) throw FillyError("GPU readback did not complete");
#endif
    return take_pixels(*readback.data_);
}

ImportedTarget::ImportedTarget(std::shared_ptr<detail::TargetData> data) : data_(std::move(data)) {}
uint32_t ImportedTarget::width() const { data_->check(); return data_->width; }
uint32_t ImportedTarget::height() const { data_->check(); return data_->height; }
bool ImportedTarget::closed() const { data_->state->check_thread(); return !data_->target || !data_->state->engine; }
bool ImportedTarget::acquired() const { data_->state->check_thread(); return data_->held > 0; }
void ImportedTarget::acquire() {
    data_->check();
    auto& interop = *data_->state->interop;
    interop.require_host();
    if (data_->held > 0) { ++data_->held; return; }
    using Access = detail::TargetData::Access;
    if (data_->access == Access::IDLE) throw InteropError("Render to the target before acquiring it");
    const auto start = std::chrono::steady_clock::now();
    // After a release without a new render, the host context already waited for the last
    // frame, so it can sample again without another dependency.
    if (data_->access == Access::SUBMITTED) interop.wait_on_host(data_->ready);
    data_->state->stats.host_wait_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    data_->access = Access::HOST;
    data_->held = 1;
}
void ImportedTarget::release() {
    data_->check();
    if (data_->held == 0) throw InteropError("release() requires a matching acquire()");
    if (--data_->held > 0) return;
    auto& interop = *data_->state->interop;
    const auto start = std::chrono::steady_clock::now();
    // A fence from an earlier release that no render consumed is superseded by this one.
    interop.delete_host_fence(data_->host_done);
    data_->host_done = interop.host_fence();
    interop.destroy(*data_->state->engine, data_->ready);
    data_->access = detail::TargetData::Access::RELEASED;
    data_->state->stats.host_release_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
}
void ImportedTarget::close() {
    data_->state->check_thread();
    if (!data_->target || !data_->state->engine) return;
    // Without the host context, only Filament's side can be fenced; see Renderer::close().
    if (data_->state->interop->host_current()) data_->state->interop->finish_host();
    data_->release();
    detail::flush_and_wait(*data_->state->engine);
    data_->state->delete_views();
}
#include "features.inc"
#include "resources.inc"
#include "memory.inc"
}
