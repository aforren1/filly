// Included inside the facade namespace, after its private resource definitions.
namespace {
auto light_instance(const std::shared_ptr<detail::Resource>& owner, uint32_t entity) {
    owner->state->check();
    if (auto model = detail::resource_cast<detail::ModelData>(owner)) model->check();
    if (auto light = detail::resource_cast<detail::LightData>(owner); light && !light->entity)
        throw FillyError("Light is closed");
    auto& manager = owner->state->engine->getLightManager();
    auto instance = manager.getInstance(utils::Entity::import(entity));
    if (!instance) throw FillyError("Light is no longer available");
    return instance;
}
void nonnegative(float value) {
    finite(value);
    if (value < 0) throw std::invalid_argument("Value must be nonnegative");
}
void light_color(Vec3 value) {
    finite(value);
    for (float v : value) if (v < 0 || v > 1) throw std::invalid_argument("Color must be in [0, 1]");
}
void cone(float inner, float outer) {
    finite(inner); finite(outer);
    if (inner < 0 || outer <= 0 || inner > outer || outer >= 1.5707964f)
        throw std::invalid_argument("Spot half-angles require 0 <= inner <= outer < pi/2 radians");
}
}

Light::Light(std::shared_ptr<detail::Resource> owner, uint32_t entity)
    : owner_(std::move(owner)), entity_(entity) {}
bool Light::same(const Light& other) const { return owner_ == other.owner_ && entity_ == other.entity_; }
uintptr_t Light::key() const { return reinterpret_cast<uintptr_t>(owner_.get()) ^ (uintptr_t(entity_) * 0x9E3779B97F4A7C15ull); }
bool Light::has_node() const {
    owner_->state->check_thread();
    return bool(detail::resource_cast<detail::ModelData>(owner_));
}
Node Light::node() const {
    auto model = detail::resource_cast<detail::ModelData>(owner_);
    if (!model) throw FillyError("Only imported lights have a node");
    model->check();
    // Closing an imported light keeps its node, so the node outlives the light component.
    return Node(model, entity_);
}
bool Light::closed() const {
    owner_->state->check_thread();
    if (!owner_->state->engine) return true;
    if (auto model = detail::resource_cast<detail::ModelData>(owner_); model && !model->asset) return true;
    if (auto light = detail::resource_cast<detail::LightData>(owner_); light && !light->entity) return true;
    return !owner_->state->engine->getLightManager().getInstance(utils::Entity::import(entity_));
}
void Light::close() {
    if (closed()) return;
    if (auto light = detail::resource_cast<detail::LightData>(owner_)) {
        light->release();
        if (auto scene = light->scene.lock()) std::erase(scene->children, owner_);
    } else {
        // Keep imported transform nodes alive for animation and asset destruction.
        owner_->state->engine->getLightManager().destroy(utils::Entity::import(entity_));
    }
    owner_->state->engine->flush();
}
std::string Light::type() const {
    auto i = light_instance(owner_, entity_);
    const auto type = owner_->state->engine->getLightManager().getType(i);
    if (type == f::LightManager::Type::POINT) return "point";
    if (type == f::LightManager::Type::SPOT || type == f::LightManager::Type::FOCUSED_SPOT) return "spot";
    if (type == f::LightManager::Type::SUN) return "sun";
    return "directional";
}
Vec3 Light::color() const { auto i = light_instance(owner_, entity_); return vec(owner_->state->engine->getLightManager().getColor(i)); }
void Light::set_color(Vec3 value) { auto i = light_instance(owner_, entity_); light_color(value); owner_->state->engine->getLightManager().setColor(i, vec(value)); }
float Light::intensity() const { auto i = light_instance(owner_, entity_); return owner_->state->engine->getLightManager().getIntensity(i); }
void Light::set_intensity(float value) { auto i = light_instance(owner_, entity_); nonnegative(value); owner_->state->engine->getLightManager().setIntensityCandela(i, value); }
Vec3 Light::position() const { auto i = light_instance(owner_, entity_); return vec(owner_->state->engine->getLightManager().getPosition(i)); }
void Light::set_position(Vec3 value) { auto i = light_instance(owner_, entity_); finite(value); owner_->state->engine->getLightManager().setPosition(i, vec(value)); }
Vec3 Light::direction() const { auto i = light_instance(owner_, entity_); return vec(owner_->state->engine->getLightManager().getDirection(i)); }
void Light::set_direction(Vec3 value) {
    auto i = light_instance(owner_, entity_); finite(value);
    if (length(vec(value)) < 1e-6f) throw std::invalid_argument("Direction must be nonzero");
    owner_->state->engine->getLightManager().setDirection(i, normalize(vec(value)));
}
float Light::range() const { auto i = light_instance(owner_, entity_); return owner_->state->engine->getLightManager().getFalloff(i); }
void Light::set_range(float value) {
    auto i = light_instance(owner_, entity_); finite(value);
    const auto kind = type();
    if (value <= 0 || (kind != "point" && kind != "spot")) throw std::invalid_argument("Range requires a point or spot light and a positive radius");
    owner_->state->engine->getLightManager().setFalloff(i, value);
}
bool Light::casts_shadows() const { auto i = light_instance(owner_, entity_); return owner_->state->engine->getLightManager().isShadowCaster(i); }
void Light::set_casts_shadows(bool value) {
    auto i = light_instance(owner_, entity_);
    owner_->state->engine->getLightManager().setShadowCaster(i, value);
}
void Light::set_shadow_options(uint32_t map_size, float constant_bias, float normal_bias) {
    auto i = light_instance(owner_, entity_);
    nonnegative(constant_bias); nonnegative(normal_bias);
    if (map_size < 8 || map_size > 4096 || (map_size & (map_size-1)))
        throw std::invalid_argument("Shadow map size must be a power of two in [8, 4096]");
    auto& manager = owner_->state->engine->getLightManager();
    auto options = manager.getShadowOptions(i);
    options.mapSize = map_size;
    options.constantBias = constant_bias;
    options.normalBias = normal_bias;
    manager.setShadowOptions(i, options);
}
void Light::set_spot_cone(float inner, float outer) {
    auto i = light_instance(owner_, entity_); cone(inner, outer);
    if (type() != "spot") throw std::invalid_argument("Cone applies only to spot lights");
    auto& manager = owner_->state->engine->getLightManager();
    // glTF keeps candela constant when the cone changes. gltfio imports spots as
    // FOCUSED_SPOT, which would otherwise keep luminous power constant instead.
    const float candela = manager.getIntensity(i);
    manager.setSpotLightCone(i, inner, outer);
    manager.setIntensityCandela(i, candela);
}
Light Scene::add_sun_light(Vec3 direction, float intensity, Vec3 color, float angular_radius,
                           float halo_size, float halo_falloff) {
    data_->check(); finite(direction); nonnegative(intensity); light_color(color);
    nonnegative(halo_size); nonnegative(halo_falloff); finite(angular_radius);
    if (length(vec(direction)) < 1e-6f) throw std::invalid_argument("Direction must be nonzero");
    // Filament clamps the radius to this range; reject instead of rendering a different disc.
    if (angular_radius < 0.25f || angular_radius > 20.0f)
        throw std::invalid_argument("Sun angular radius must be in [0.25, 20] degrees");
    auto entity = utils::EntityManager::get().create();
    auto result = f::LightManager::Builder(f::LightManager::Type::SUN)
        .direction(normalize(vec(direction))).color(vec(color)).intensity(intensity)
        .sunAngularRadius(angular_radius).sunHaloSize(halo_size).sunHaloFalloff(halo_falloff)
        .castShadows(false).build(*data_->state->engine, entity);
    if (result != f::LightManager::Builder::Success) { utils::EntityManager::get().destroy(entity); throw FillyError("Could not create sun light"); }
    return Light(detail::own_light(data_, entity), entity.getId());
}
Light Scene::add_point_light(Vec3 position, float intensity, Vec3 color, float range) {
    data_->check(); finite(position); nonnegative(intensity); light_color(color); finite(range);
    if (range <= 0) throw std::invalid_argument("Range must be positive");
    auto entity = utils::EntityManager::get().create();
    auto result = f::LightManager::Builder(f::LightManager::Type::POINT)
        .position(vec(position)).intensityCandela(intensity).color(vec(color)).falloff(range)
        .build(*data_->state->engine, entity);
    if (result != f::LightManager::Builder::Success) { utils::EntityManager::get().destroy(entity); throw FillyError("Could not create point light"); }
    return Light(detail::own_light(data_, entity), entity.getId());
}
Light Scene::add_spot_light(Vec3 position, Vec3 direction, float intensity, Vec3 color, float range, float inner, float outer) {
    data_->check(); finite(position); finite(direction); nonnegative(intensity); light_color(color); finite(range); cone(inner, outer);
    if (range <= 0 || length(vec(direction)) < 1e-6f) throw std::invalid_argument("Require positive range and nonzero direction");
    auto entity = utils::EntityManager::get().create();
    // SPOT keeps candela, not luminous power, when the cone changes, as imported glTF spots do.
    auto result = f::LightManager::Builder(f::LightManager::Type::SPOT)
        .position(vec(position)).direction(normalize(vec(direction))).intensityCandela(intensity)
        .color(vec(color)).falloff(range).spotLightCone(inner, outer).build(*data_->state->engine, entity);
    if (result != f::LightManager::Builder::Success) { utils::EntityManager::get().destroy(entity); throw FillyError("Could not create spot light"); }
    return Light(detail::own_light(data_, entity), entity.getId());
}
bool Model::closed() const { data_->state->check_thread(); return !data_->asset || !data_->state->engine; }
void Model::close() {
    data_->state->check_thread();
    if (!data_->asset) return;
    data_->release();
    if (auto scene = data_->scene.lock()) std::erase(scene->children, data_);
    if (data_->state->engine) data_->state->engine->flush();
}
namespace {
// Resolves a name against a list, as every by-name lookup does.
size_t named(const std::vector<std::string>& names, const std::string& name, const char* what) {
    size_t index = 0, count = 0;
    for (size_t i = 0; i < names.size(); ++i) if (names[i] == name) { index = i; ++count; }
    if (count == 1) return index;
    if (!count) throw unknown_name(what, name, names);
    std::string list;
    for (size_t i = 0; i < names.size(); ++i)
        if (names[i] == name) list += (list.empty() ? "" : ", ") + std::to_string(i);
    throw AssetError(std::string("Ambiguous ") + what + " name '" + name + "'; use an index: " + list);
}
size_t indexed(int64_t index, size_t count, const char* what) {
    if (index < 0 || uint64_t(index) >= count)
        throw AssetError(std::string("No ") + what + " with index " + std::to_string(index));
    return size_t(index);
}
// Position of a node's entity in a per-model list of light or camera entities.
size_t carried(const std::vector<utils::Entity>& entities, utils::Entity entity, int64_t index, const char* what) {
    const auto found = std::find(entities.begin(), entities.end(), entity);
    if (found == entities.end())
        throw AssetError("glTF node " + std::to_string(index) + " has no " + what);
    return size_t(found - entities.begin());
}
// The glTF index of the node named `name` among the nodes that carry one of `entities`. Only those
// nodes count, so the error lists the names that the lookup accepts.
int64_t carrier(const std::vector<utils::Entity>& nodes, const auto& sources,
                const std::vector<utils::Entity>& entities, const std::string& name, const char* what) {
    std::vector<std::string> names;
    std::string matches;
    int64_t found = -1;
    for (size_t i = 0; i < nodes.size() && i < sources.size(); ++i) {
        if (!nodes[i] || !sources[i].name || std::find(entities.begin(), entities.end(), nodes[i]) == entities.end())
            continue;
        names.push_back(*sources[i].name);
        if (*sources[i].name != name) continue;
        matches += (matches.empty() ? "" : ", ") + std::to_string(i);
        found = found < 0 ? int64_t(i) : -2;
    }
    if (found == -1) throw unknown_name(what, name, names);
    if (found == -2) throw AssetError(std::string("Ambiguous ") + what + " name '" + name + "'; use a glTF index: " + matches);
    return found;
}
}
std::vector<Light> Model::lights() const {
    data_->check();
    std::vector<Light> result;
    for (const auto entity : data_->lights) result.emplace_back(data_, entity.getId());
    return result;
}
Light Model::light(const std::string& name) const {
    data_->check();
    return light(carrier(data_->nodes, data_->shared->prepared->nodes, data_->lights, name, "light node"));
}
Light Model::light(int64_t index) const {
    node(index);
    const auto position = carried(data_->lights, data_->nodes[size_t(index)], index, "light");
    return Light(data_, data_->lights[position].getId());
}
std::vector<AnimationInfo> Model::animations() const {
    data_->check(); std::vector<AnimationInfo> result;
    for (const auto& clip : data_->animations) {
        AnimationInfo info{clip.name, clip.duration, {}};
        for (const auto& track : clip.tracks) {
            if (track.source.kind != 0 && track.source.kind != 3) continue;
            // Texture transform tracks name the matrix and the component after a slash.
            const auto& parameter = track.source.parameter;
            const auto property = parameter.substr(0, parameter.find('/'));
            for (const auto& target : track.targets) {
                const char* name = target.material ? target.material->getName() : nullptr;
                AnimatedProperty item{name ? name : "", property};
                const bool seen = std::any_of(info.material_properties.begin(), info.material_properties.end(),
                    [&](const AnimatedProperty& other) { return other.material == item.material && other.property == item.property; });
                if (!seen) info.material_properties.push_back(std::move(item));
            }
        }
        result.push_back(std::move(info));
    }
    return result;
}
std::vector<Camera> Model::cameras() const {
    data_->check();
    std::vector<Camera> result;
    for (size_t i = 0; i < data_->camera_entities.size(); ++i) result.push_back(camera_at(i));
    return result;
}
Camera Model::camera(const std::string& name) const {
    data_->check();
    return camera(carrier(data_->nodes, data_->shared->prepared->nodes, data_->camera_entities, name, "camera node"));
}
Camera Model::camera(int64_t index) const {
    node(index);
    return camera_at(carried(data_->camera_entities, data_->nodes[size_t(index)], index, "camera"));
}
Camera Model::camera_at(size_t position) const {
    auto& handle = data_->camera_handles[position];
    // One handle per camera node, so repeated lookups do not grow the resource registry.
    if (!handle) {
        auto camera = data_->state->track(std::make_shared<detail::CameraData>(data_->state));
        camera->entity = data_->camera_entities[position];
        camera->camera = data_->state->engine->getCameraComponent(camera->entity);
        camera->model = data_;
        camera->imported = true;
        for (size_t i = 0; i < data_->cameras.size(); ++i)
            if (data_->cameras[i].entity == camera->entity) camera->projection = i;
        handle = camera;
    }
    return Camera(handle);
}
void Model::reset_animation() {
    data_->check(); auto& tm = data_->state->engine->getTransformManager();
    // One world-transform update for the whole reset instead of one per node and subtree.
    tm.openLocalTransformTransaction();
    for (const auto& [entity, value] : data_->rest_transforms) tm.setTransform(tm.getInstance(entity), value);
    // gltfio's per-node translation, rotation, and scale must return to rest too; see RestSource.
    // The animator commits the transaction.
    if (data_->rest_animation >= 0) data_->animator->applyAnimation(size_t(data_->rest_animation), 0);
    tm.commitLocalTransformTransaction();
    auto& rm = data_->state->engine->getRenderableManager();
    for (const auto& [entity, weights] : data_->rest_morphs) rm.setMorphWeights(rm.getInstance(entity), weights.data(), weights.size());
    detail::reset_properties(*data_);
    detail::commit_properties(*data_);
    data_->animator->updateBoneMatrices();
    data_->bones_dirty = false;
}
void Model::apply_animation(const std::string& name, float time, bool loop) {
    data_->check();
    const auto& clips = data_->animations;
    size_t index = 0, count = 0;
    for (size_t i = 0; i < clips.size(); ++i) if (clips[i].name == name) { index = i; ++count; }
    // Only the error lists the names, so a valid name allocates nothing.
    if (count != 1) {
        std::vector<std::string> names;
        for (const auto& clip : clips) names.push_back(clip.name);
        index = named(names, name, "animation");
    }
    apply_animation(int64_t(index), time, loop);
}
void Model::apply_animation(int64_t index, float time, bool loop) {
    data_->check(); finite(time);
    const auto& clip = data_->animations[indexed(index, data_->animations.size(), "animation")];
    if (time < 0) throw std::invalid_argument("Animation time must be nonnegative");
    const float duration = clip.duration;
    if (duration > 0) time = loop ? std::fmod(time, duration) : std::min(time, duration);
    else time = 0;
    // Everything that any clip animates returns to rest first, so clip changes and time jumps do
    // not depend on history. The rest animation covers node transforms and morph weights; a clip
    // that writes all of those channels itself does not need it.
    auto* animator = data_->animator;
    if (data_->rest_animation >= 0 && (clip.needs_rest || clip.native_index < 0))
        animator->applyAnimation(size_t(data_->rest_animation), 0);
    detail::reset_properties(*data_, &clip);
    if (clip.native_index >= 0) {
        const float native_duration = animator->getAnimationDuration(clip.native_index);
        animator->applyAnimation(clip.native_index,
            native_duration > 0 ? std::min(time, std::nextafter(native_duration, 0.0f)) : 0);
    }
    detail::apply_properties(*data_, clip, time);
    animator->updateBoneMatrices();
    data_->bones_dirty = false;
}
std::vector<std::string> Model::variants() const {
    data_->check(); auto* instance = data_->instance; std::vector<std::string> result;
    for (size_t i = 0; i < instance->getMaterialVariantCount(); ++i) result.emplace_back(instance->getMaterialVariantName(i));
    return result;
}
void Model::apply_variant(const std::string& name) { apply_variant(int64_t(named(variants(), name, "variant"))); }
void Model::apply_variant(int64_t index) {
    data_->check(); auto* instance = data_->instance;
    instance->applyMaterialVariant(indexed(index, instance->getMaterialVariantCount(), "variant"));
    // A slot that the variant mapped now shows the variant's material, not its node-local copy.
    // Replace that copy with a copy of the variant's material so the slot stays node-local.
    // Shared materials with runtime textures keep them in the slots that now show the glTF instance.
    detail::apply_overrides(*data_);
    auto& rm = data_->state->engine->getRenderableManager();
    for (auto& local : data_->locals) {
        auto renderable = rm.getInstance(local.entity);
        auto* bound = rm.getMaterialInstanceAt(renderable, local.slot);
        if (bound == local.copy) continue;
        detail::Assignment assignment;
        auto* source = detail::glTF_source(*data_, bound, &assignment);
        auto* copy = f::MaterialInstance::duplicate(bound);
        data_->state->engine->destroy(local.copy);
        local.source = source;
        local.copy = copy;
        local.assignment = std::move(assignment);
        rm.setMaterialInstanceAt(renderable, local.slot, copy);
    }
    if (auto scene = data_->scene.lock()) detail::update_diffuse_environment(*scene);
}
size_t Node::morph_target_count() const {
    data_->check(); auto& rm = data_->state->engine->getRenderableManager();
    auto i = rm.getInstance(utils::Entity::import(entity_));
    return i ? rm.getMorphTargetCount(i) : 0;
}
void Node::set_morph_weights(const float* values, size_t count) {
    const auto targets = morph_target_count();
    if (!targets || count != targets) throw std::invalid_argument("Supply one weight per morph target on a mesh node");
    for (size_t i = 0; i < count; ++i) finite(values[i]);
    auto& rm = data_->state->engine->getRenderableManager();
    rm.setMorphWeights(rm.getInstance(utils::Entity::import(entity_)), values, count);
}
float Camera::exposure() const {
    data_->check(); auto* c = data_->camera;
    return std::log2(c->getAperture() * c->getAperture() / c->getShutterSpeed() * 100.0f / c->getSensitivity());
}
void Camera::set_exposure(float ev100) {
    data_->check(); finite(ev100);
    if (ev100 < -10 || ev100 > 24) throw std::invalid_argument("Exposure must be EV100 in [-10, 24]");
    float aperture = 1, shutter = 1, iso = 100;
    if (ev100 >= 0) { aperture = std::exp2(std::min(ev100, 12.0f) / 2); shutter = std::exp2(-std::max(ev100 - 12.0f, 0.0f)); }
    else iso = 100 * std::exp2(-ev100);
    data_->camera->setExposure(aperture, shutter, iso);
}
namespace {
// Owns the selected mapper for ColorGrading::Builder, which copies what it needs.
std::unique_ptr<f::ToneMapper> tone_mapper(const std::string& name) {
    using Look = f::AgxToneMapper::AgxLook;
    if (name == "linear") return std::make_unique<f::LinearToneMapper>();
    if (name == "aces_legacy") return std::make_unique<f::ACESLegacyToneMapper>();
    if (name == "aces") return std::make_unique<f::ACESToneMapper>();
    if (name == "filmic") return std::make_unique<f::FilmicToneMapper>();
    if (name == "pbr_neutral") return std::make_unique<f::PBRNeutralToneMapper>();
    if (name == "gt7") return std::make_unique<f::GT7ToneMapper>();
    if (name == "agx") return std::make_unique<f::AgxToneMapper>(Look::NONE);
    if (name == "agx_punchy") return std::make_unique<f::AgxToneMapper>(Look::PUNCHY);
    if (name == "agx_golden") return std::make_unique<f::AgxToneMapper>(Look::GOLDEN);
    if (name == "generic") return std::make_unique<f::GenericToneMapper>();
    if (name == "display_range") return std::make_unique<f::DisplayRangeToneMapper>();
    throw std::invalid_argument("tone_mapping must be linear, aces_legacy, aces, filmic, pbr_neutral, "
                                "gt7, agx, agx_punchy, agx_golden, generic, or display_range, got '" + name + "'");
}
// In Filament 1.77.1 only sRGB output takes the per-channel 1D LUT. Linear output falls back to
// a 32^3 10-bit LUT indexed in LogC space, which measured 242 instead of 255 for linear white.
// This wrapper keeps the sRGB path and undoes its transfer function inside the tone mapper, so
// the LUT holds the inverse and the output is linear to fp16 precision.
struct LinearOutput final : f::ToneMapper {
    explicit LinearOutput(std::unique_ptr<f::ToneMapper> inner) : inner(std::move(inner)) {}
    m::float3 operator()(m::float3 c) const noexcept override {
        c = saturate((*inner)(c));
        for (size_t i = 0; i < 3; ++i)
            c[i] = c[i] <= 0.04045f ? c[i] / 12.92f : std::pow((c[i] + 0.055f) / 1.055f, 2.4f);
        return c;
    }
    bool isOneDimensional() const noexcept override { return true; }
    bool isLDR() const noexcept override { return inner->isLDR(); }
    std::unique_ptr<f::ToneMapper> inner;
};
// Filament's color grading runs only when the scene uses its postprocessing. It applies the tone
// mapper and writes into filly's RGBA16F buffer; filly's encode pass then rounds, dithers, and
// sets alpha. Per-channel tone mappers (linear, filmic, generic) write scene-linear color and
// filly applies the transfer function exactly.
void apply_grading(detail::SceneData& data, const std::string& tone_mapping) {
    std::unique_ptr<f::ToneMapper> mapper = tone_mapper(tone_mapping);
    detail::require_exact(data, tone_mapping != "linear", "tone_mapping '" + tone_mapping + "'");
    // Tone mappers that mix channels run in Rec.2020 between gamut matrices, where the wrapper
    // would be wrong, so they go through Filament's 32^3 3D LUT. Its linear output, encoded by
    // filly, differed from gltf_viewer by up to 3 levels (DamagedHelmet, ACES legacy); its sRGB
    // output is what gltf_viewer writes. With sRGB encoding, Filament therefore encodes.
    const bool per_channel = mapper->isOneDimensional();
    const bool filament_encodes = !per_channel && data.encoding == "srgb";
    if (per_channel) mapper = std::make_unique<LinearOutput>(std::move(mapper));
    using namespace f::color;
    auto* grading = f::ColorGrading::Builder().toneMapper(mapper.get())
        .outputColorSpace(per_channel || filament_encodes ? Rec709-sRGB-D65 : Rec709-Linear-D65)
        .build(*data.state->engine);
    if (!grading) throw FillyError("Could not create color grading");
    data.view->setColorGrading(grading);
    if (data.grading) data.state->engine->destroy(data.grading);
    data.grading = grading;
    data.tone_mapping = tone_mapping;
    data.channel_mixing = !per_channel;
    data.filament_encodes = filament_encodes;
    detail::update_postprocessing(data);
    detail::update_dithering(data);
    // As with FXAA, building the graded encode pass here keeps that cost out of the first frame.
    if (filament_encodes) detail::output_pass(*data.state, data.state->encode_graded, detail::PassKind::encode_graded);
}
}
std::string Scene::encoding() const { data_->check(); return data_->encoding; }
void Scene::set_encoding(const std::string& value) {
    data_->check();
    if (value != "srgb" && value != "linear")
        throw std::invalid_argument("encoding must be 'srgb' or 'linear', got '" + value + "'");
    const bool changed = value != data_->encoding;
    data_->encoding = value;
    // A channel-mixing tone mapper's output color space follows the encoding.
    if (changed && data_->channel_mixing) apply_grading(*data_, data_->tone_mapping);
}
std::string Scene::tone_mapping() const { data_->check(); return data_->tone_mapping; }
void Scene::set_tone_mapping(const std::string& value) {
    data_->check();
    if (value != data_->tone_mapping || !data_->grading) apply_grading(*data_, value);
}
std::string Scene::output_path() const { data_->check(); return data_->direct ? "direct" : "exact"; }
void Scene::set_output_path(const std::string& value) {
    data_->check();
    if (value != "exact" && value != "direct")
        throw std::invalid_argument("output_path must be 'exact' or 'direct', got '" + value + "'");
    const bool direct = value == "direct";
    if (direct) {
        const auto conflicts = detail::direct_conflicts(*data_);
        if (!conflicts.empty())
            throw std::invalid_argument("output_path 'direct' skips filly's encode pass and Filament's "
                                        "postprocessing, which these settings need: " + conflicts);
        if (data_->masked)
            throw std::invalid_argument("output_path 'direct' cannot render alphaMode MASK materials, "
                                        "and a model in the scene has one");
    }
    data_->direct = direct;
    detail::update_postprocessing(*data_);
}
std::string Scene::antialiasing() const { data_->check(); return data_->antialiasing; }
void Scene::set_antialiasing(const std::string& value) {
    data_->check();
    if (value != "none" && value != "fxaa")
        throw std::invalid_argument("antialiasing must be 'none' or 'fxaa', got '" + value + "'");
    detail::require_exact(*data_, value != "none", "antialiasing '" + value + "'");
    // filly runs FXAA on the encoded image; Filament's FXAA stays off. Compiling the pass here
    // keeps that cost out of the first frame.
    if (value == "fxaa") detail::output_pass(*data_->state, data_->state->fxaa, detail::PassKind::fxaa);
    data_->antialiasing = value;
}
int Scene::msaa() const { data_->check(); return data_->msaa; }
void Scene::set_msaa(int value) {
    data_->check();
    if (value != 1 && value != 2 && value != 4 && value != 8)
        throw std::invalid_argument("msaa must be 1, 2, 4, or 8, got " + std::to_string(value));
    detail::require_exact(*data_, value != 1, "msaa " + std::to_string(value));
    f::MultiSampleAntiAliasingOptions options;
    options.enabled = value > 1; options.sampleCount = uint8_t(value);
    options.customResolve = false;
    data_->view->setMultiSampleAntiAliasingOptions(options);
    data_->msaa = value;
}
bool Scene::shadows() const { data_->check(); return data_->shadows; }
void Scene::set_shadows(bool value) { data_->check(); data_->view->setShadowingEnabled(value); data_->shadows = value; }
bool Scene::refraction() const { data_->check(); return data_->refraction; }
void Scene::set_refraction(bool value) {
    data_->check(); detail::require_exact(*data_, value, "refraction");
    data_->view->setScreenSpaceRefractionEnabled(value); data_->refraction = value;
}
bool Scene::transparent() const { data_->check(); return data_->transparent; }
void Scene::set_transparent(bool value) {
    data_->check(); detail::require_exact(*data_, value, "transparent");
    data_->view->setBlendMode(value ? f::View::BlendMode::TRANSLUCENT : f::View::BlendMode::OPAQUE);
    data_->transparent = value;
}
// Filament's defaults, as gltf_viewer uses them. TAA stays off, so none of these effects
// accumulates history; dithering noise still changes on every frame.
bool Scene::dithering() const { data_->check(); return data_->dithering; }
void Scene::set_dithering(bool value) {
    // The encode pass dithers after the transfer function, where one level of noise is one
    // 8-bit level; Filament's dithering would add it to linear color. Filament's runs only when
    // its color grading writes sRGB (see apply_grading()).
    data_->check(); detail::require_exact(*data_, value, "dithering");
    data_->dithering = value;
    detail::update_dithering(*data_);
}
bool Scene::ssao() const { data_->check(); return data_->ssao; }
void Scene::set_ssao(bool value) {
    data_->check(); detail::require_exact(*data_, value, "ssao");
    f::AmbientOcclusionOptions ao;
    ao.enabled = value;
    data_->view->setAmbientOcclusionOptions(ao);
    data_->ssao = value;
}
bool Scene::bloom() const { data_->check(); return data_->bloom; }
void Scene::set_bloom(bool value) {
    data_->check(); detail::require_exact(*data_, value, "bloom");
    f::BloomOptions glow;
    glow.enabled = value;
    data_->view->setBloomOptions(glow);
    data_->bloom = value;
    detail::update_postprocessing(*data_);
}
void Scene::clear_environment() {
    data_->check(); auto& engine = *data_->state->engine;
    data_->scene->setIndirectLight(nullptr);
    data_->scene->setSkybox(nullptr);
    auto* previous_irradiance = data_->irradiance;
    data_->irradiance = nullptr;
    data_->pending_irradiance.reset();
    detail::update_diffuse_environment(*data_);
    if (data_->environment) engine.destroy(data_->environment);
    if (data_->skybox) engine.destroy(data_->skybox);
    if (data_->skybox_texture) engine.destroy(data_->skybox_texture);
    if (data_->reflections) engine.destroy(data_->reflections);
    if (previous_irradiance) engine.destroy(previous_irradiance);
    data_->environment = nullptr; data_->reflections = data_->irradiance = nullptr;
    data_->skybox = nullptr; data_->skybox_texture = nullptr;
}
bool Scene::environment_visible() const { data_->check(); return data_->environment_visible; }
void Scene::set_environment_visible(bool value) {
    data_->check(); data_->environment_visible = value;
    data_->scene->setSkybox(value ? data_->skybox : nullptr);
}
float Scene::environment_rotation() const { data_->check(); return data_->environment_rotation; }
void Scene::set_environment_rotation(float value) {
    data_->check(); finite(value);
    if (!data_->environment) throw FillyError("Load an environment first");
    data_->environment->setRotation(m::mat3f::rotation(value * 0.01745329252f, m::float3{0,1,0}));
    data_->environment_rotation = value;
    detail::update_diffuse_environment(*data_);
}
float Scene::environment_intensity() const { data_->check(); return data_->environment ? data_->environment->getIntensity() : 0; }
void Scene::set_environment_intensity(float value) {
    data_->check(); nonnegative(value);
    if (!data_->environment) throw FillyError("Load an environment first");
    data_->environment->setIntensity(value);
    detail::update_diffuse_environment(*data_);
}
void Scene::set_environment(const float* pixels, size_t count, uint32_t width, uint32_t height, float intensity, float rotation) {
    data_->check(); nonnegative(intensity); finite(rotation);
    if (height < 2 || height > 4096 || width != height * 2 || count != size_t(width) * height * 3)
        throw std::invalid_argument("Environment must be a 2:1 RGB panorama, height 2..4096");
    for (size_t i = 0; i < count; ++i) nonnegative(pixels[i]);
    auto& engine = *data_->state->engine;
    bool uniform = true;
    for (size_t i = 3; i < count && uniform; ++i) uniform = pixels[i] == pixels[i % 3];
    if (uniform) {
        set_uniform_environment({pixels[0], pixels[1], pixels[2]}, intensity, rotation);
        return;
    }
    auto* source = f::Texture::Builder().width(width).height(height).levels(0xff)
        .sampler(f::Texture::Sampler::SAMPLER_2D).format(f::Texture::InternalFormat::R11F_G11F_B10F)
        .usage(f::Texture::Usage::SAMPLEABLE | f::Texture::Usage::COLOR_ATTACHMENT |
               f::Texture::Usage::GEN_MIPMAPPABLE | f::Texture::Usage::UPLOADABLE).build(engine);
    // Filament reads the pixels after this call returns, so it gets its own copy.
    auto* upload = new std::vector<float>(pixels, pixels + count);
    source->setImage(engine, 0, f::Texture::PixelBufferDescriptor(upload->data(), upload->size()*sizeof(float),
        f::Texture::Format::RGB, f::Texture::Type::FLOAT,
        [](void*, size_t, void* p) { delete static_cast<std::vector<float>*>(p); }, upload));
    source->generateMipmaps(engine);
    auto& prefilter = data_->state->prefilter;
    if (!prefilter) prefilter = std::make_unique<detail::State::Prefilter>(engine);
    auto* cube = prefilter->to_cube()(source);
    auto* reflections = prefilter->specular()(cube);
    auto* irradiance = prefilter->irradiance()(cube);
    auto* environment = f::IndirectLight::Builder().reflections(reflections).irradiance(irradiance)
        .intensity(intensity).rotation(m::mat3f::rotation(rotation * 0.01745329252f, m::float3{0,1,0})).build(engine);
    auto* skybox = f::Skybox::Builder().environment(cube).showSun(true).build(engine);
    clear_environment();
    data_->environment = environment; data_->reflections = reflections; data_->irradiance = irradiance;
    data_->skybox = skybox; data_->skybox_texture = cube;
    data_->scene->setIndirectLight(environment);
    data_->scene->setSkybox(data_->environment_visible ? skybox : nullptr);
    data_->environment_rotation = rotation;
    detail::update_diffuse_environment(*data_);
    engine.destroy(source);
    detail::flush_and_wait(engine);
}
// A constant panorama needs no prefiltering: its cubemap, its prefiltered reflections, and its
// irradiance are all the same constant. The prefilter path computes weighted averages of the
// constant and stores them in R11F_G11F_B10F, which reproduces the stored constant; 1 x 1
// cubemaps of the same format, uploaded the same way as the panorama, hold the same value. This
// saves the prefilter's shader compilation (about 260 ms in Chrome, 80 ms on the desktop, once
// per renderer) and its GPU work (about 50 ms on the desktop for each environment).
void Scene::set_uniform_environment(Vec3 radiance, float intensity, float rotation) {
    auto& engine = *data_->state->engine;
    auto cube = [&] {
        auto* texture = f::Texture::Builder().width(1).height(1).levels(1)
            .sampler(f::Texture::Sampler::SAMPLER_CUBEMAP).format(f::Texture::InternalFormat::R11F_G11F_B10F)
            .usage(f::Texture::Usage::SAMPLEABLE | f::Texture::Usage::UPLOADABLE).build(engine);
        // Six faces of one RGB float pixel each, in face order.
        auto* upload = new std::vector<float>();
        for (int face = 0; face < 6; ++face) upload->insert(upload->end(), radiance.begin(), radiance.end());
        texture->setImage(engine, 0, 0, 0, 0, 1, 1, 6, f::Texture::PixelBufferDescriptor(
            upload->data(), upload->size() * sizeof(float), f::Texture::Format::RGB, f::Texture::Type::FLOAT,
            [](void*, size_t, void* p) { delete static_cast<std::vector<float>*>(p); }, upload));
        return texture;
    };
    auto* sky = cube();
    auto* reflections = cube();
    auto* irradiance = cube();
    auto* environment = f::IndirectLight::Builder().reflections(reflections).irradiance(irradiance)
        .intensity(intensity).rotation(m::mat3f::rotation(rotation * 0.01745329252f, m::float3{0,1,0})).build(engine);
    auto* skybox = f::Skybox::Builder().environment(sky).showSun(true).build(engine);
    clear_environment();
    data_->environment = environment; data_->reflections = reflections; data_->irradiance = irradiance;
    data_->skybox = skybox; data_->skybox_texture = sky;
    data_->scene->setIndirectLight(environment);
    data_->scene->setSkybox(data_->environment_visible ? skybox : nullptr);
    data_->environment_rotation = rotation;
    detail::update_diffuse_environment(*data_);
    detail::flush_and_wait(engine);
}
namespace {
std::vector<uint8_t> read_file(const std::string& path, const char* what) {
    std::ifstream stream(std::filesystem::u8path(path), std::ios::binary | std::ios::ate);
    if (!stream) throw AssetError(std::string("Could not open ") + what + ": " + path);
    const auto size = stream.tellg();
    if (size <= 0 || size > std::numeric_limits<uint32_t>::max())
        throw AssetError(std::string("Invalid ") + what + " file size: " + path);
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    stream.seekg(0);
    if (!stream.read(reinterpret_cast<char*>(bytes.data()), size))
        throw AssetError(std::string("Could not read ") + what + ": " + path);
    return bytes;
}
// Filament's KTX parser and texture upload abort the process on malformed input, so the
// structure is checked here first. Only uncompressed floating-point cubemaps are accepted;
// cmgen writes these for IBL and skybox files.
std::unique_ptr<image::Ktx1Bundle> read_ktx_cubemap(const std::string& path, const char* what) {
    static constexpr uint8_t magic[12] = {0xAB, 'K', 'T', 'X', ' ', '1', '1', 0xBB, 0x0D, 0x0A, 0x1A, 0x0A};
    const auto bytes = read_file(path, what);
    auto fail = [&](const std::string& reason) { return AssetError(std::string(what) + " KTX file " + reason + ": " + path); };
    if (bytes.size() < 64 || std::memcmp(bytes.data(), magic, sizeof(magic)))
        throw AssetError(std::string(what) + " is not a KTX 1 file: " + path);
    auto word = [&](size_t offset) { uint32_t value; std::memcpy(&value, bytes.data() + offset, 4); return value; };
    if (word(12) != 0x04030201) throw fail("has big-endian data");
    // glType, glFormat, glInternalFormat, and bytes per pixel.
    struct Format { uint32_t type, format, internal, size; };
    static constexpr Format formats[] = {
        {0x8C3B, 0x1907, 0x8C3A, 4},   // R11F_G11F_B10F
        {0x8C3A, 0x1907, 0x8C3A, 4},   // cmgen writes the internal format as glType; Filament accepts it
        {0x140B, 0x1907, 0x881B, 6},   // RGB16F
        {0x140B, 0x1908, 0x881A, 8},   // RGBA16F
        {0x1406, 0x1907, 0x8815, 12},  // RGB32F
        {0x1406, 0x1908, 0x8814, 16},  // RGBA32F
    };
    const auto* format = std::find_if(std::begin(formats), std::end(formats), [&](const Format& f) {
        return f.type == word(16) && f.format == word(24) && f.internal == word(28); });
    if (format == std::end(formats)) throw fail("must use R11F_G11F_B10F, RGB16F, RGBA16F, RGB32F, or RGBA32F");
    const uint32_t edge = word(36), levels = std::max(word(56), 1u);
    if (edge == 0 || edge > 4096 || word(40) != edge || word(44) != 0 || word(48) > 1 || word(52) != 6)
        throw fail("must be a square cubemap from 1 to 4096 pixels, not an array or 3D texture");
    uint32_t max_levels = 1;
    while ((edge >> max_levels) != 0) ++max_levels;
    if (levels > max_levels) throw fail("has more mip levels than its size allows");
    const uint64_t metadata_end = 64 + uint64_t(word(60));
    if (metadata_end > bytes.size()) throw fail("is truncated");
    for (uint64_t entry = 64; entry < metadata_end;) {
        if (metadata_end - entry < 4) throw fail("has invalid metadata");
        const uint64_t length = word(size_t(entry)), start = entry + 4;
        const auto* key = bytes.data() + start;
        if (length > metadata_end - start || std::find(key, key + length, uint8_t(0)) == key + length)
            throw fail("has invalid metadata");
        entry = start + length + (3 - (length + 3) % 4);
        if (entry > metadata_end) throw fail("has invalid metadata");
    }
    uint64_t offset = metadata_end;
    for (uint32_t level = 0; level < levels; ++level) {
        const uint64_t side = std::max(edge >> level, 1u), expected = side * side * format->size;
        if (offset + 4 > bytes.size() || word(size_t(offset)) != expected) throw fail("has a missing or misaligned mip level");
        offset += 4 + 6 * expected;
    }
    if (offset > bytes.size()) throw fail("is truncated");
    return std::make_unique<image::Ktx1Bundle>(bytes.data(), uint32_t(bytes.size()));
}
f::Texture* ktx_texture(f::Engine& engine, std::unique_ptr<image::Ktx1Bundle> bundle, const std::string& path) {
    if (!f::Texture::isTextureFormatSupported(engine, ktxreader::Ktx1Reader::toTextureFormat(bundle->getInfo())))
        throw AssetError("The OpenGL driver does not support this KTX texture format: " + path);
    auto* raw = bundle.get();
    // The upload callback frees the bundle once the driver has consumed its pixels.
    auto* texture = ktxreader::Ktx1Reader::createTexture(&engine, *raw, false,
        [](void* user) { delete static_cast<image::Ktx1Bundle*>(user); }, raw);
    if (!texture) throw AssetError("KTX file lacks a face or mip level: " + path);
    bundle.release();
    return texture;
}
}
void Scene::load_environment_ktx(const std::string& ibl_path, const std::string& skybox_path,
                                 float intensity, float rotation) {
    data_->check(); nonnegative(intensity); finite(rotation);
    auto& engine = *data_->state->engine;
    auto ibl = read_ktx_cubemap(ibl_path, "IBL");
    m::float3 bands[9];
    // cmgen stores 3-band irradiance coefficients, already scaled for IndirectLight.
    if (!ibl->getSphericalHarmonics(bands))
        throw AssetError("IBL KTX has no 'sh' metadata; create it with cmgen --format=ktx: " + ibl_path);
    for (const auto& band : bands) finite(std::array<float, 3>{band.x, band.y, band.z});
    std::unique_ptr<image::Ktx1Bundle> sky;
    if (!skybox_path.empty()) sky = read_ktx_cubemap(skybox_path, "Skybox");
    // Only the custom diffuse-transmission material reads an irradiance cubemap, so its level 0
    // waits on the CPU until a material needs it (see update_diffuse_environment()).
    std::unique_ptr<detail::SceneData::PendingIrradiance> pending;
    const auto& info = ibl->getInfo();
    const auto format = ktxreader::Ktx1Reader::toTextureFormat(info);
    if (f::Texture::isTextureFormatMipmappable(engine, format)) {
        uint8_t* faces = nullptr;
        uint32_t face_size = 0;
        ibl->getBlob({0, 0, 0}, &faces, &face_size);
        pending = std::make_unique<detail::SceneData::PendingIrradiance>();
        pending->faces.assign(faces, faces + size_t(face_size) * 6);
        pending->size = info.pixelWidth;
        pending->format = format;
        pending->pixels = ktxreader::Ktx1Reader::toPixelDataFormat(info);
        pending->type = ktxreader::Ktx1Reader::toPixelDataType(info);
    }
    f::Texture* reflections = ktx_texture(engine, std::move(ibl), ibl_path);
    f::Texture* sky_texture = nullptr;
    if (sky) {
        try { sky_texture = ktx_texture(engine, std::move(sky), skybox_path); }
        catch (...) { engine.destroy(reflections); throw; }
    }
    auto* environment = f::IndirectLight::Builder().reflections(reflections).irradiance(3, bands)
        .intensity(intensity).rotation(m::mat3f::rotation(rotation * 0.01745329252f, m::float3{0,1,0})).build(engine);
    // Without a skybox file, level 0 of the IBL is the unfiltered environment at IBL resolution.
    auto* skybox = f::Skybox::Builder().environment(sky_texture ? sky_texture : reflections).showSun(true).build(engine);
    clear_environment();
    data_->environment = environment; data_->reflections = reflections; data_->pending_irradiance = std::move(pending);
    data_->skybox = skybox; data_->skybox_texture = sky_texture;
    data_->scene->setIndirectLight(environment);
    data_->scene->setSkybox(data_->environment_visible ? skybox : nullptr);
    data_->environment_rotation = rotation;
    detail::update_diffuse_environment(*data_);
    detail::flush_and_wait(engine);
}
void Scene::load_environment(const std::string& path, float intensity, float rotation) {
    data_->check();
    std::ifstream stream(std::filesystem::u8path(path), std::ios::binary | std::ios::ate);
    if (!stream) throw AssetError("Could not open environment: " + path);
    const auto size = stream.tellg();
    if (size <= 0 || size > std::numeric_limits<int>::max()) throw AssetError("Invalid environment file size");
    std::vector<unsigned char> bytes(size_t(size), 0); stream.seekg(0);
    if (!stream.read(reinterpret_cast<char*>(bytes.data()), size)) throw AssetError("Could not read environment");
    int width=0, height=0, channels=0;
    auto* decoded = stbi_loadf_from_memory(bytes.data(), int(size), &width, &height, &channels, 3);
    if (!decoded) throw AssetError("Could not decode environment (use Radiance HDR, PNG, or JPEG)");
    std::unique_ptr<float, decltype(&stbi_image_free)> owner(decoded, stbi_image_free);
    if (height < 2 || height > 4096 || width != 2*height) throw AssetError("Environment must be a 2:1 panorama with height 2..4096");
    set_environment(decoded, size_t(width) * height * 3, width, height, intensity, rotation);
}
float Camera::focus_distance() const { data_->check(); return data_->camera->getFocusDistance(); }
void Camera::set_focus_distance(float value) {
    data_->check(); finite(value);
    if (value <= 0) throw std::invalid_argument("Focus distance must be positive");
    data_->camera->setFocusDistance(value);
}
float Camera::aperture() const { data_->check(); return data_->aperture; }
void Camera::set_aperture(float value) {
    data_->check(); finite(value);
    // Filament's own aperture range.
    if (value < 0.5f || value > 64) throw std::invalid_argument("Aperture must be an f-number in [0.5, 64]");
    data_->aperture = value;
}
bool Scene::fog() const { data_->check(); return data_->fog; }
void Scene::set_fog(bool value) {
    data_->check();
    data_->fog = value;
    data_->fog_applied = {NAN, NAN, NAN};
    if (!value) {
        f::FogOptions off;
        off.enabled = false;
        data_->view->setFogOptions(off);
    }
}
void Scene::set_fog_options(Vec3 color, float density, float start) {
    data_->check(); light_color(color); nonnegative(density); nonnegative(start);
    data_->fog_color = color;
    data_->fog_density = density;
    data_->fog_start = start;
    data_->fog_applied = {NAN, NAN, NAN};
}
bool Scene::depth_of_field() const { data_->check(); return data_->depth_of_field; }
void Scene::set_depth_of_field(bool value) {
    data_->check(); detail::require_exact(*data_, value, "depth_of_field");
    data_->depth_of_field = value;
    data_->dof_scale = NAN;
    if (!value) {
        f::DepthOfFieldOptions off;
        off.enabled = false;
        data_->view->setDepthOfFieldOptions(off);
    }
    detail::update_postprocessing(*data_);
}
bool Scene::vignette() const { data_->check(); return data_->vignette; }
void Scene::set_vignette(bool value) {
    data_->check(); detail::require_exact(*data_, value, "vignette");
    auto options = data_->view->getVignetteOptions();
    options.enabled = value;
    data_->view->setVignetteOptions(options);
    data_->vignette = value;
    detail::update_postprocessing(*data_);
}
void Scene::set_vignette_options(float midpoint, float roundness, float feather, Vec3 color) {
    data_->check(); unit_interval(midpoint); unit_interval(roundness); light_color(color); finite(feather);
    // Filament clamps feather to [0.05, 1]; reject values it would change.
    if (feather < 0.05f || feather > 1) throw std::invalid_argument("Vignette feather must be in [0.05, 1]");
    f::VignetteOptions options;
    options.midPoint = midpoint;
    options.roundness = roundness;
    options.feather = feather;
    options.color = {color[0], color[1], color[2], 1};
    options.enabled = data_->vignette;
    data_->view->setVignetteOptions(options);
}
