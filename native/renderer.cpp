#include "renderer.h"
#include "gl_interop.h"
#include "materials.h"
#include "engine_config.h"

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
#include <gltfio/materials/uberarchive.h>
#include <math/mat4.h>
#include <utils/EntityManager.h>
#include <utils/Log.h>
#include <utils/NameComponentManager.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <cstdlib>
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
// Reads an unsigned integer that preflight wrote into a glTF extras object.
size_t extras_field(const char* extras, const char* name, size_t fallback) {
    const char* key = extras ? std::strstr(extras, name) : nullptr;
    const char* colon = key ? std::strchr(key, ':') : nullptr;
    return colon ? size_t(std::strtoull(colon + 1, nullptr, 10)) : fallback;
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
    struct Prefilter {
        IBLPrefilterContext context;
        IBLPrefilterContext::EquirectangularToCubemap to_cube;
        IBLPrefilterContext::SpecularFilter specular;
        IBLPrefilterContext::IrradianceFilter irradiance;
        explicit Prefilter(f::Engine& engine)
            : context(engine), to_cube(context), specular(context), irradiance(context) {}
    };
    std::unique_ptr<Prefilter> prefilter;
    g::MaterialProvider* materials = nullptr;
    g::AssetLoader* loader = nullptr;
    std::unique_ptr<utils::NameComponentManager> names;
    std::vector<std::weak_ptr<Resource>> resources;
    std::thread::id thread = std::this_thread::get_id();
    Stats stats;
    std::unique_ptr<GlInterop> interop;
    bool precompiled = false;
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
        if (engine) engine->flushAndWait();
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
    std::shared_ptr<State> state;
    explicit Resource(std::shared_ptr<State> owner) : state(std::move(owner)) {}
    virtual void release() noexcept = 0;
    virtual ~Resource() = default;
};

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
    f::Scene* scene = nullptr;
    f::View* view = nullptr;
    std::shared_ptr<CameraData> active_camera;
    std::vector<std::shared_ptr<Resource>> children;
    // Skinned models whose node transforms changed since the last render.
    std::vector<std::shared_ptr<ModelData>> dirty_bones;
    Vec4 background = {0, 0, 0, 1};
    f::ColorGrading* grading = nullptr;
    f::IndirectLight* environment = nullptr;
    f::Texture* reflections = nullptr;
    f::Texture* irradiance = nullptr;
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
    // The caller's opt-in to skip postprocessing: the view writes shaded color straight into the
    // target and the GPU applies the sRGB encoding on write. It is never chosen automatically,
    // because the two paths round differently by up to one 8-bit level.
    bool direct = false;
    // Live models with MASK materials. Filament writes their sharpened edge alpha to the target,
    // and only color grading stores alpha one for an opaque view.
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
        active_camera.reset();
        children.clear();
        dirty_bones.clear();
    }
    ~SceneData() override { release(); }
};

// Linear tone mapping clamps to [0, 1], as 8-bit storage does, so only these options need
// Filament's postprocessing. Transparent views need color grading because it premultiplies after
// encoding, which is what hosts that blend in encoded space expect; the GPU would encode the
// premultiplied linear color instead. Returns the conflicting settings, or an empty string.
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
// Called before an option that needs postprocessing is applied, so a conflict changes nothing.
void require_graded(const SceneData& scene, bool needs_grading, const std::string& option) {
    if (scene.direct && needs_grading)
        throw std::invalid_argument(option + " needs color grading, but output_path is "
                                    "'direct'; set output_path = 'graded' first");
}

// Scenes must not keep a camera whose component is gone.
void detach_closed_cameras(State& state) noexcept {
    for (const auto& entry : state.resources) {
        auto scene = std::dynamic_pointer_cast<SceneData>(entry.lock());
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
        if (slot.busy) engine.flushAndWait();
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
            if (ring->busy()) { engine.flushAndWait(); break; }
        if (vertices) engine.destroy(vertices);
        if (indices) engine.destroy(indices);
        vertices = nullptr;
        indices = nullptr;
    }
};

// One glTF asset and the preflight data that each instance needs. A model and its clones
// share it; the last one to close destroys the asset.
struct AssetData : Resource {
    using Resource::Resource;
    g::FilamentAsset* asset = nullptr;
    // False once the source data and preflight tables are released after the first instance.
    bool clonable = false;
    bool masked = false;
    // Kept for every asset: name lookups need them, and they are small.
    std::vector<NodeSource> nodes;
    std::vector<DiffuseSource> diffuse;
    std::vector<SurfaceSource> surfaces;
    std::vector<AnimationSource> animations;
    std::vector<CameraSource> cameras;
    std::vector<std::vector<float>> morphs;
    // Custom-material textures of every instance. Their material instances live until the
    // asset is destroyed, so the textures must too.
    std::vector<f::Texture*> textures;
    // Set for generated meshes, whose renderables use these buffers instead of the asset's.
    std::unique_ptr<MeshGeometry> mesh;
    void release() noexcept override {
        if (state->engine) {
            if (asset) state->loader->destroyAsset(asset);
            for (auto* texture : textures) state->engine->destroy(texture);
            if (mesh) mesh->release(*state->engine);
        }
        asset = nullptr;
        textures.clear();
        mesh.reset();
    }
    ~AssetData() override { release(); }
};

// Runtime textures of one material handle: base color (0) and emissive (1).
struct Assignment {
    std::array<std::shared_ptr<TextureData>, 2> textures;
    std::array<m::mat3f, 2> transforms;
    // Key of the instance in use; unset while it is the glTF instance or a copy of it.
    bool keyed = false;
    g::MaterialKey key{};
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
    std::weak_ptr<SceneData> scene;
    std::shared_ptr<AssetData> shared;
    // Null once the model is closed.
    g::FilamentAsset* asset = nullptr;
    g::FilamentInstance* instance = nullptr;
    bool visible = true, skinned = false, bones_dirty = false;
    g::Animator* animator = nullptr;
    // Entities by glTF node index; null for nodes that gltfio did not instantiate.
    std::vector<utils::Entity> nodes;
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
// Preflight names, not gltfio's, which fall back to the mesh, light, or camera name.
const NodeSource* node_source(const ModelData& model, utils::Entity entity) {
    const auto index = extras_field(model.asset->getExtras(entity), "\"fillyVisibility\"", SIZE_MAX);
    return index < model.shared->nodes.size() ? &model.shared->nodes[index] : nullptr;
}
void initialize_visibility(ModelData& model) {
    auto& transforms = model.state->engine->getTransformManager();
    std::vector<std::pair<utils::Entity, size_t>> pending{{model.instance->getRoot(), SIZE_MAX}};
    while (!pending.empty()) {
        const auto [entity, parent] = pending.back();
        pending.pop_back();
        const char* extras = model.asset->getExtras(entity);
        const size_t position = model.visibility.size();
        const bool visible = extras_field(extras, "\"fillyVisible\"", 1) != 0;
        model.visibility.push_back({entity, extras_field(extras, "\"fillyVisibility\"", SIZE_MAX), parent, visible, visible});
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
            if (target) state->engine->destroy(target);
            if (depth) state->engine->destroy(depth);
            if (color) state->engine->destroy(color);
        }
        if (host_view) state->stale_views.push_back(host_view);
        host_view = 0;
        target = nullptr;
        color = depth = nullptr;
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
    // Models whose materials sample this texture. Closing the texture removes it from them.
    std::vector<std::weak_ptr<ModelData>> users;
    // Host input: its sRGB view (or zero), the fence of the last host write, and the frame
    // that the host waits for before it writes again.
    uint32_t host_view = 0;
    void* host_done = nullptr;
    SyncPoint read_done;
    bool writing = false;
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

void update_diffuse_environment(SceneData& scene) {
    const float intensity = scene.environment ? scene.environment->getIntensity() : 0;
    for (const auto& child : scene.children) {
        auto model = std::dynamic_pointer_cast<ModelData>(child);
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
void initialize_model(ModelData& model, const std::vector<MaterialBinding>& bindings) {
    auto& engine = *model.state->engine;
    auto& transforms = engine.getTransformManager();
    auto& renderables = engine.getRenderableManager();
    auto& light_manager = engine.getLightManager();
    const auto& shared = *model.shared;
    model.animator = model.instance->getAnimator();
    model.skinned = model.instance->getSkinCount() > 0;
    const auto* entities = model.instance->getEntities();
    const size_t count = model.instance->getEntityCount();
    for (size_t i = 0; i < count; ++i) {
        const auto index = extras_field(model.asset->getExtras(entities[i]), "\"fillyVisibility\"", SIZE_MAX);
        if (index == SIZE_MAX) continue;
        if (index >= model.nodes.size()) model.nodes.resize(index + 1);
        model.nodes[index] = entities[i];
    }
    for (const auto entity : model.nodes) {
        if (!entity) continue;
        if (light_manager.hasComponent(entity)) model.lights.push_back(entity);
        if (engine.getCameraComponent(entity)) model.camera_entities.push_back(entity);
    }
    model.camera_handles.resize(model.camera_entities.size());
    initialize_visibility(model);
    initialize_cameras(model, shared.cameras);
    initialize_animations(model, shared.animations, bindings);
    for (size_t i = 0; i < count; ++i) {
        auto entity = entities[i];
        model.rest_transforms.emplace_back(entity, transforms.getTransform(transforms.getInstance(entity)));
        auto renderable = renderables.getInstance(entity);
        if (renderable && renderables.getMorphTargetCount(renderable)) {
            const auto targets = renderables.getMorphTargetCount(renderable);
            std::vector<float> weights(targets);
            const auto index = extras_field(model.asset->getExtras(entity), "\"fillyNode\"", SIZE_MAX);
            if (index < shared.morphs.size() && shared.morphs[index].size() == targets) weights = shared.morphs[index];
            renderables.setMorphWeights(renderable, weights.data(), weights.size());
            model.rest_morphs.emplace_back(entity, std::move(weights));
        }
    }
    model.animator->updateBoneMatrices();
}

void State::close() noexcept {
    if (!engine) return;
    engine->flushAndWait();
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
    host_inputs.clear();
    if (frame_chain) engine->destroy(frame_chain);
    frame_chain = nullptr;
    if (renderer) engine->destroy(renderer);
    renderer = nullptr;
    f::Engine::destroy(&engine);
    engine = nullptr;
    delete_views();
}
}

Renderer::Renderer(uintptr_t shared_context, bool precompiled_shaders) {
    state_ = std::make_shared<detail::State>();
    state_->precompiled = precompiled_shaders;
    state_->interop = std::make_unique<detail::GlInterop>(shared_context);
    state_->engine = state_->interop->create_engine();
    if (!state_->engine) throw BackendError("Could not create the OpenGL engine");
    state_->renderer = state_->engine->createRenderer();
    state_->frame_chain = state_->engine->createSwapChain(1, 1, 0);
    if (!state_->frame_chain) throw BackendError("Could not create the headless frame swap chain");
    // With GL_EXT_shader_framebuffer_fetch (Intel Iris Xe 32.0.101.7088), the color-grading
    // subpass writes a black frame with a gradient tile. Drivers without the extension never
    // take that path, so the flag changes nothing there. See docs/explanation/assumptions.md.
    if (!state_->engine->getDebugRegistry().setProperty("d.renderer.disable_subpasses", true))
        throw BackendError("Filament SDK lacks the required separate postprocessing pass control");
    // Without this flag, a per-channel tone mapper still goes through a 32^3 10-bit LUT in a
    // wide working gamut: unlit (1, 0, 0) became (247, 0, 0) and (0, 1, 0) became (23, 247, 6).
    // The 1D LUT applies the tone mapper and transfer function per channel in fp16.
    if (!state_->engine->setFeatureFlag("engine.color_grading.use_1d_lut", true))
        throw BackendError("Filament SDK lacks the one-dimensional color-grading LUT");
    state_->materials = detail::create_material_provider(state_->engine, !precompiled_shaders);
    state_->names = std::make_unique<utils::NameComponentManager>(utils::EntityManager::get());
    state_->loader = g::AssetLoader::create({state_->engine, state_->materials, state_->names.get()});
    if (!state_->renderer || !state_->materials || !state_->loader)
        throw BackendError("Could not initialize Filament resources");
}
bool Renderer::precompiled_shaders() const { state_->check(); return state_->precompiled; }

Scene Renderer::create_scene() {
    state_->check();
    auto data = state_->track(std::make_shared<detail::SceneData>(state_));
    data->scene = state_->engine->createScene();
    data->view = state_->engine->createView();
    data->view->setScene(data->scene);
    f::RenderQuality quality;
    // RGB16F keeps 10 bits in [0, 1]; R11G11B10F keeps 5 or 6 and shifts 8-bit output levels.
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
    // sRGB storage lets the GPU encode on write; read() returns the stored bytes unchanged.
    data->color = T::Builder().width(data->width).height(data->height).levels(1)
        .sampler(T::Sampler::SAMPLER_2D).format(T::InternalFormat::SRGB8_A8)
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
    // Color grading writes encoded values raw, so only output_path 'direct' needs the view.
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
                           "glTexImage2D. Use output_path 'graded' or allocate it with glTexStorage2D");
    const auto start = std::chrono::steady_clock::now();
    // The previous frame was never sampled, so only Filament's own ordering applies to it.
    if (data.access == detail::TargetData::Access::SUBMITTED) state_->interop->destroy(*state_->engine, data.ready);
    state_->interop->enqueue_wait(*state_->engine, data.host_done);
    data.host_done = nullptr;
    submit(scene, data, options);
    data.ready = state_->interop->signal(*state_->engine);
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
    // Host writes to sampled textures finish before this frame reads them.
    for (auto* input : state_->host_inputs) {
        state_->interop->enqueue_wait(*state_->engine, input->host_done);
        input->host_done = nullptr;
    }
    auto* view = data.view;
    const f::Viewport viewport{int32_t(x), int32_t(y), width, height};
    view->setViewport(viewport);
    view->setRenderTarget(target.target);
    if (camera != data.active_camera.get()) view->setCamera(camera->camera);
    f::View* fill = nullptr;
    if (!options.clear) {
        // Filament clears whole attachments. Without a clear, the viewport would keep the previous
        // frame on the direct path and an uncleared buffer on the color-grading path. The first
        // view of a frame sets the target's clear, so a background-only view goes first without
        // one; the scene view then clears only its own intermediate buffer.
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
    state_->renderer->endFrame();
    view->setRenderTarget(nullptr);
    // The fill view must not keep a camera that the caller may close before the next call.
    if (fill) { fill->setRenderTarget(nullptr); fill->setCamera(nullptr); }
    if (camera != data.active_camera.get())
        view->setCamera(data.active_camera ? data.active_camera->camera : nullptr);
    // The host waits for this frame before it writes a sampled texture again.
    for (auto* input : state_->host_inputs) {
        state_->interop->destroy(*state_->engine, input->read_done);
        input->read_done = state_->interop->signal(*state_->engine);
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
    state_->engine->flushAndWait();
    state_->stats.finish_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
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
bool Renderer::closed() const { state_->check_thread(); return !state_->engine; }
Stats Renderer::stats() const {
    state_->check();
    auto result = state_->stats;
    auto& lights = state_->engine->getLightManager();
    for (const auto& entry : state_->resources) {
        auto resource = entry.lock();
        if (auto model = std::dynamic_pointer_cast<detail::ModelData>(resource); model && model->asset) {
            ++result.live_models;
            result.material_copies += model->locals.size();
            for (const auto entity : model->lights) result.live_lights += bool(lights.getInstance(entity));
        }
        if (auto light = std::dynamic_pointer_cast<detail::LightData>(resource); light && light->entity)
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

bool Scene::precompiled_shaders() const { data_->check(); return data_->state->precompiled; }
Model Scene::load_asset(const std::vector<uint8_t>& bytes, const std::string& path,
        const std::vector<DiffuseSource>& diffuse, const std::vector<std::vector<float>>& morphs,
        const std::vector<AnimationSource>& animations, const std::vector<SurfaceSource>& surfaces,
        const std::vector<CameraSource>& cameras, const std::vector<NodeSource>& nodes,
        bool masked, bool clonable) {
    data_->check();
    if (masked && data_->direct)
        throw AssetError("Asset has alphaMode MASK materials, which need color grading, but "
                         "output_path is 'direct'; set output_path = 'graded' first");
    auto state = data_->state;
    if (bytes.empty() || bytes.size() > std::numeric_limits<uint32_t>::max())
        throw AssetError("Asset is empty or larger than 4 GiB");
    auto shared = state->track(std::make_shared<detail::AssetData>(state));
    shared->diffuse = diffuse;
    shared->surfaces = surfaces;
    shared->animations = animations;
    shared->cameras = cameras;
    shared->morphs = morphs;
    shared->nodes = nodes;
    shared->masked = masked;
    detail::set_diffuse_sources(state->materials, diffuse);
    detail::set_surface_sources(state->materials, surfaces);
    try {
        shared->asset = state->loader->createAsset(bytes.data(), uint32_t(bytes.size()));
    } catch (...) {
        shared->textures = detail::take_diffuse_textures(state->materials);
        detail::take_material_bindings(state->materials);
        detail::take_material_records(state->materials);
        detail::set_diffuse_sources(state->materials, {});
        detail::set_surface_sources(state->materials, {});
        throw;
    }
    shared->textures = detail::take_diffuse_textures(state->materials);
    auto material_bindings = detail::take_material_bindings(state->materials);
    auto material_records = detail::take_material_records(state->materials);
    detail::set_diffuse_sources(state->materials, {});
    detail::set_surface_sources(state->materials, {});
    if (!shared->asset) throw AssetError("Could not decode glTF/GLB asset");
    auto* asset = shared->asset;
    // Fail before resource upload, which can otherwise leave incomplete assets.
    std::vector<std::pair<std::string, std::filesystem::path>> external_resources;
    for (size_t i = 0; i < asset->getResourceUriCount(); ++i) {
        const std::string uri = asset->getResourceUris()[i];
        if (uri.starts_with("data:")) continue;
        if (path.empty()) throw AssetError("Byte assets must contain all resources");
        std::string decoded;
        for (size_t j = 0; j < uri.size(); ++j) {
            if (uri[j] != '%') { decoded += uri[j]; continue; }
            auto hex = [](char c) -> int {
                if (c >= '0' && c <= '9') return c - '0';
                if (c >= 'a' && c <= 'f') return c - 'a' + 10;
                if (c >= 'A' && c <= 'F') return c - 'A' + 10;
                return -1;
            };
            if (j + 2 >= uri.size() || hex(uri[j+1]) < 0 || hex(uri[j+2]) < 0)
                throw AssetError("Invalid resource URI escape: " + uri);
            const char value = char(hex(uri[j+1]) * 16 + hex(uri[j+2]));
            if (!value) throw AssetError("Resource URI contains a null byte");
            decoded += value;
            j += 2;
        }
        const auto resource = std::filesystem::u8path(path).parent_path() / std::filesystem::u8path(decoded);
        if (!std::filesystem::is_regular_file(resource))
            throw AssetError("Missing glTF resource: " + uri);
        external_resources.emplace_back(uri, resource);
    }
    std::unique_ptr<g::TextureProvider> decoder(g::createStbProvider(state->engine));
    std::unique_ptr<g::TextureProvider> ktx(g::createKtx2Provider(state->engine));
    std::unique_ptr<g::TextureProvider> webp(g::createWebpProvider(state->engine));
    {
        g::ResourceLoader resources({state->engine, path.empty() ? nullptr : path.c_str(), true});
        // Supply image bytes under the original URI; gltfio's own file reads use narrow paths.
        // Buffers never reach here: preflight packs them into the GLB binary chunk.
        for (const auto& [uri, resource] : external_resources) {
            std::ifstream stream(resource, std::ios::binary | std::ios::ate);
            if (!stream) throw AssetError("Could not open glTF resource: " + uri);
            const auto size = stream.tellg();
            if (size <= 0 || uint64_t(size) > std::numeric_limits<uint32_t>::max())
                throw AssetError("glTF resource is empty or larger than 4 GiB: " + uri);
            auto bytes = std::make_unique<std::vector<uint8_t>>(size_t(size));
            stream.seekg(0);
            if (!stream.read(reinterpret_cast<char*>(bytes->data()), size))
                throw AssetError("Could not read glTF resource: " + uri);
            auto* payload = bytes.release();
            resources.addResourceData(uri.c_str(), g::ResourceLoader::BufferDescriptor(
                payload->data(), payload->size(), [](void*, size_t, void* user) {
                    delete static_cast<std::vector<uint8_t>*>(user);
                }, payload));
        }
        resources.addTextureProvider("image/png", decoder.get());
        resources.addTextureProvider("image/jpeg", decoder.get());
        resources.addTextureProvider("image/ktx2", ktx.get());
        if (webp) resources.addTextureProvider("image/webp", webp.get());
        if (!resources.loadResources(asset)) throw AssetError("Could not load glTF resources");
        // Upload lifetimes do not need this wait. It keeps the upload out of the first trial frame.
        state->engine->flushAndWait();
    }
    auto model = state->track(std::make_shared<detail::ModelData>(state));
    model->scene = data_;
    model->shared = shared;
    model->asset = asset;
    model->instance = asset->getInstance();
    model->records = std::move(material_records);
    detail::initialize_model(*model, material_bindings);
    if (masked) ++data_->masked;
    // Only AssetLoader::createInstance() reads the source data (about the file size) and the
    // preflight tables after this point, so they are kept only for assets that clone() may copy.
    shared->clonable = clonable;
    if (!clonable) {
        asset->releaseSourceData();
        shared->diffuse = {};
        shared->surfaces = {};
        shared->animations = {};
        shared->cameras = {};
        shared->morphs = {};
    }
    data_->children.push_back(model);
    detail::update_visibility(*model);
    detail::update_diffuse_environment(*data_);
    return Model(model);
}

Model Model::clone() {
    data_->check();
    auto state = data_->state;
    auto scene = data_->scene.lock();
    if (!scene || !scene->scene) throw FillyError("Scene is closed");
    auto& shared = *data_->shared;
    if (!shared.clonable)
        throw FillyError("This asset released its source data after loading; "
                            "load it with scene.load(source, clonable=True) to clone it");
    detail::set_diffuse_sources(state->materials, shared.diffuse);
    detail::set_surface_sources(state->materials, shared.surfaces);
    g::FilamentInstance* instance = nullptr;
    std::vector<detail::MaterialBinding> bindings;
    try {
        instance = state->loader->createInstance(shared.asset);
    } catch (...) {
        auto textures = detail::take_diffuse_textures(state->materials);
        shared.textures.insert(shared.textures.end(), textures.begin(), textures.end());
        detail::take_material_bindings(state->materials);
        detail::take_material_records(state->materials);
        detail::set_diffuse_sources(state->materials, {});
        detail::set_surface_sources(state->materials, {});
        throw;
    }
    auto textures = detail::take_diffuse_textures(state->materials);
    shared.textures.insert(shared.textures.end(), textures.begin(), textures.end());
    bindings = detail::take_material_bindings(state->materials);
    auto records = detail::take_material_records(state->materials);
    detail::set_diffuse_sources(state->materials, {});
    detail::set_surface_sources(state->materials, {});
    if (!instance) throw AssetError("Could not create another instance of this asset");
    auto model = state->track(std::make_shared<detail::ModelData>(state));
    model->scene = scene;
    model->shared = data_->shared;
    model->asset = shared.asset;
    model->instance = instance;
    model->records = std::move(records);
    detail::initialize_model(*model, bindings);
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
    const auto box = data_->asset->getBoundingBox();
    return {vec(box.min), vec(box.max)};
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
    const auto& sources = data_->shared->nodes;
    for (size_t i = 0; i < data_->nodes.size() && i < sources.size(); ++i)
        if (data_->nodes[i] && sources[i].name == name) matches.push_back(i);
    if (matches.empty()) throw AssetError("Unknown node: " + name);
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
    const auto& sources = data_->shared->nodes;
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
    auto names = material_names();
    size_t index = 0, count = 0;
    for (size_t i = 0; i < names.size(); ++i) if (names[i] == name) { index = i; ++count; }
    if (count != 1) throw AssetError(count ? "Ambiguous material name: " + name : "Unknown material: " + name);
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
    data_->state->engine->flushAndWait();
}
std::vector<uint8_t> OffscreenTarget::read() const {
    auto state = data_->state;
    data_->check();
    if (!data_->rendered) throw FillyError("Render to the target before reading it");
    const auto start = std::chrono::steady_clock::now();
    struct Readback { std::vector<uint8_t> pixels; bool complete = false; };
    auto result = std::make_shared<Readback>();
    result->pixels.resize(size_t(data_->width) * data_->height * 4);
    // The callback retains storage even if the driver cannot complete the read.
    auto* owner = new std::shared_ptr<Readback>(result);
    f::backend::PixelBufferDescriptor buffer(result->pixels.data(), result->pixels.size(),
        f::backend::PixelDataFormat::RGBA, f::backend::PixelDataType::UBYTE,
        [](void*, size_t, void* user) {
            std::unique_ptr<std::shared_ptr<Readback>> hold(static_cast<std::shared_ptr<Readback>*>(user));
            (*hold)->complete = true;
        }, owner);
    state->renderer->readPixels(data_->target, 0, 0, data_->width, data_->height, std::move(buffer));
    state->engine->flushAndWait();
    state->engine->pumpMessageQueues();
    if (!result->complete) throw FillyError("GPU readback did not complete");
    state->stats.readback_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    // Filament already normalizes readPixels output to an upper-left origin.
    return std::move(result->pixels);
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
    data_->state->engine->flushAndWait();
    data_->state->delete_views();
}
#include "features.inc"
#include "resources.inc"
}
