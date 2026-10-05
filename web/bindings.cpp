// Embind bindings of filly's core for the web build. They stay close to native/renderer.h;
// web/filly.js adds the JavaScript API: option objects, name-or-index keys, error types, and
// the hand-over of the page's WebGL2 context. C++ exceptions reach JavaScript as WebAssembly
// exceptions, which filly.js converts with getExceptionMessage().

#include "renderer.h"

#include <emscripten/bind.h>
#include <emscripten/val.h>

#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

using namespace emscripten;
using namespace filly;

namespace {
template <class T> val array_of(const std::vector<T>& items) {
    val out = val::array();
    for (const auto& item : items) out.call<void>("push", item);
    return out;
}

// A JavaScript copy of native data: a typed-array view of the heap would go stale when it grows.
template <class T> val typed_copy(const char* type, const std::vector<T>& values) {
    return val::global(type).new_(typed_memory_view(values.size(), values.data()));
}

// A native copy of a JavaScript array of numbers. convertJSArrayToNumberVector zero-fills its
// vector before the copy, which for a full-screen texture each frame costs as much as the copy.
template <class T> struct Numbers {
    std::unique_ptr<T[]> data;
    size_t size = 0;
};

template <class T> Numbers<T> copy_numbers(const val& array) {
    Numbers<T> out;
    out.size = array["length"].as<size_t>();
    out.data.reset(new T[out.size]);
    // For a typed array of the same type, set() is a memcpy.
    val(typed_memory_view(out.size, out.data.get())).call<void>("set", array);
    return out;
}

template <class T> Numbers<T> numbers(const val& array, const char* name) {
    if (array.isUndefined() || array.isNull()) throw std::invalid_argument(std::string(name) + " is required");
    return copy_numbers<T>(array);
}

val animations(const Model& model) {
    val out = val::array();
    for (const auto& clip : model.animations()) {
        val item = val::object();
        item.set("name", clip.name);
        item.set("duration", clip.duration);
        val properties = val::array();
        for (const auto& property : clip.material_properties) {
            val entry = val::object();
            entry.set("material", property.material);
            entry.set("property", property.property);
            properties.call<void>("push", entry);
        }
        item.set("materialProperties", properties);
        out.call<void>("push", item);
    }
    return out;
}

// Byte counts as doubles: JavaScript numbers hold integers exactly up to 2^53.
val model_memory(const Model& model) {
    const auto memory = model.memory();
    val out = val::object();
    out.set("gpuTextureBytes", double(memory.gpu_texture_bytes));
    out.set("gpuGeometryBytes", double(memory.gpu_geometry_bytes));
    out.set("gpuBytes", double(memory.gpu_texture_bytes + memory.gpu_geometry_bytes));
    out.set("cpuBytes", double(memory.cpu_bytes));
    out.set("cloneGpuBytes", double(memory.clone_gpu_bytes));
    out.set("cloneCpuBytes", double(memory.clone_cpu_bytes));
    out.set("models", double(memory.models));
    return out;
}

val loaded(Model model, const std::vector<std::string>& warnings) {
    val out = val::object();
    out.set("model", model);
    out.set("warnings", array_of(warnings));
    return out;
}

val load_bytes(Scene& scene, const val& bytes, bool strict, bool clonable) {
    std::vector<std::string> warnings;
    const auto copy = copy_numbers<uint8_t>(bytes);
    Model model = scene.load(copy.data.get(), copy.size, strict, clonable, warnings);
    return loaded(model, warnings);
}

val load_path(Scene& scene, const std::string& path, bool strict, bool clonable) {
    std::vector<std::string> warnings;
    Model model = scene.load(path, strict, clonable, warnings);
    return loaded(model, warnings);
}

FrameOptions frame_options(const val& options) {
    FrameOptions out;
    if (options.isUndefined() || options.isNull()) return out;
    if (!options["fill"].isUndefined()) out.fill = options["fill"].as<double>();
    if (!options["direction"].isUndefined()) out.direction = options["direction"].as<Vec3>();
    if (!options["up"].isUndefined()) out.up = options["up"].as<Vec3>();
    if (!options["fit"].isUndefined()) out.fit = options["fit"].as<std::string>();
    if (!options["near"].isUndefined()) out.near = options["near"].as<double>();
    if (!options["far"].isUndefined()) out.far = options["far"].as<double>();
    if (!options["aspect"].isUndefined()) out.aspect = options["aspect"].as<double>();
    return out;
}

// Positional arguments: each option of an options object costs a property lookup through the
// JavaScript bridge, in every frame.
RenderOptions render_options(const Camera* camera, const val& viewport, bool clear) {
    RenderOptions out;
    out.camera = camera;
    if (!viewport.isUndefined() && !viewport.isNull()) {
        out.has_viewport = true;
        for (int i = 0; i < 4; ++i) out.viewport[size_t(i)] = viewport[i].as<int64_t>();
    }
    out.clear = clear;
    return out;
}

// Vertex arrays from JavaScript, flat, kept alive while the core reads them.
struct MeshInput {
    Numbers<float> positions, normals, uvs, colors;
    Numbers<uint32_t> indices;
    MeshArrays arrays;
};

void attribute(const val& source, const char* name, size_t columns, size_t vertices,
               Numbers<float>& storage, const float*& pointer) {
    const val value = source[name];
    if (value.isUndefined() || value.isNull()) return;
    const size_t size = value["length"].as<size_t>();
    if (size != vertices * columns)
        throw std::invalid_argument(std::string(name) + " must have " + std::to_string(vertices * columns)
                                    + " numbers (" + std::to_string(vertices) + " x " + std::to_string(columns)
                                    + "), got " + std::to_string(size));
    storage = copy_numbers<float>(value);
    pointer = storage.data.get();
}

void mesh_input(const val& source, size_t vertices, MeshInput& input) {
    input.arrays.vertices = vertices;
    attribute(source, "positions", 3, vertices, input.positions, input.arrays.positions);
    attribute(source, "normals", 3, vertices, input.normals, input.arrays.normals);
    attribute(source, "uvs", 2, vertices, input.uvs, input.arrays.uvs);
    attribute(source, "colors", 4, vertices, input.colors, input.arrays.colors);
}

Model create_mesh(Scene& scene, const val& options) {
    MeshInput input;
    const val positions = options["positions"];
    if (positions.isUndefined() || positions.isNull()) throw std::invalid_argument("positions is required");
    const size_t position_count = positions["length"].as<size_t>();
    if (position_count % 3) throw std::invalid_argument("positions must have 3 numbers per vertex");
    mesh_input(options, position_count / 3, input);
    input.indices = numbers<uint32_t>(options["indices"], "indices");
    if (input.indices.size % 3) throw std::invalid_argument("indices must have 3 numbers per triangle");
    input.arrays.indices = input.indices.data.get();
    input.arrays.triangles = input.indices.size / 3;
    MeshMaterial material;
    if (!options["baseColor"].isUndefined()) material.base_color = options["baseColor"].as<Vec4>();
    if (!options["metallic"].isUndefined()) material.metallic = options["metallic"].as<float>();
    if (!options["roughness"].isUndefined()) material.roughness = options["roughness"].as<float>();
    if (!options["emissive"].isUndefined()) material.emissive = options["emissive"].as<Vec3>();
    if (!options["unlit"].isUndefined()) material.unlit = options["unlit"].as<bool>();
    if (!options["doubleSided"].isUndefined()) material.double_sided = options["doubleSided"].as<bool>();
    if (!options["alphaMode"].isUndefined()) material.alpha_mode = options["alphaMode"].as<std::string>();
    return scene.create_mesh(input.arrays, material);
}

void update_mesh(Model& model, const val& options) {
    MeshInput input;
    mesh_input(options, model.vertex_count(), input);
    model.update_mesh(input.arrays);
}

val shape(ShapeArrays shape) {
    val out = val::object();
    out.set("positions", typed_copy("Float32Array", shape.positions));
    out.set("normals", typed_copy("Float32Array", shape.normals));
    out.set("uvs", typed_copy("Float32Array", shape.uvs));
    out.set("indices", typed_copy("Uint32Array", shape.indices));
    return out;
}

// Pixels as a typed array: Uint8Array for uint8 textures, Float32Array for float textures.
Texture create_texture(Renderer& renderer, const val& pixels, double width, double height, int channels,
                       const std::string& color_space, bool mipmaps, const std::string& filter,
                       const std::string& wrap) {
    if (pixels["constructor"]["name"].as<std::string>() == "Float32Array") {
        const auto values = copy_numbers<float>(pixels);
        return renderer.create_texture(int64_t(width), int64_t(height), channels, true, color_space, mipmaps,
                                       filter, wrap, values.data.get(), values.size * sizeof(float));
    }
    const auto values = copy_numbers<uint8_t>(pixels);
    return renderer.create_texture(int64_t(width), int64_t(height), channels, false, color_space, mipmaps,
                                   filter, wrap, values.data.get(), values.size);
}

void update_texture(Texture& texture, const val& pixels) {
    const bool is_float = pixels["constructor"]["name"].as<std::string>() == "Float32Array";
    if (is_float != texture.is_float())
        throw std::invalid_argument(std::string("update() needs a ") + (texture.is_float() ? "Float32Array" : "Uint8Array"));
    const size_t expected = size_t(texture.width()) * texture.height() * texture.channels();
    if (pixels["length"].as<size_t>() != expected)
        throw std::invalid_argument("update() needs " + std::to_string(expected) + " values");
    // The pixels go straight from the JavaScript array into the upload storage. The detour
    // through a native copy cost a second full-image copy per update. 1-channel sRGB textures
    // expand to RGBA on upload, so they take the copy.
    const size_t bytes = expected * (is_float ? sizeof(float) : 1);
    if (texture.channels() == 1 && !is_float && texture.color_space() == "srgb") {
        const auto values = copy_numbers<uint8_t>(pixels);
        texture.update(values.data.get(), values.size);
        return;
    }
    uint8_t* storage = texture.begin_update(bytes);
    // For a typed array of the same type, set() is a memcpy into the wasm heap.
    if (is_float) val(typed_memory_view(expected, reinterpret_cast<float*>(storage))).call<void>("set", pixels);
    else val(typed_memory_view(expected, storage)).call<void>("set", pixels);
    texture.commit_update();
}

val material_texture(const Material& material, int slot) {
    auto data = material.texture(slot);
    if (!data) return val::null();
    if (is_host_texture(data)) return val(HostTexture(data));
    return val(Texture(data));
}

val optional_name(const std::optional<std::string>& name) { return name ? val(*name) : val::null(); }
}

EMSCRIPTEN_BINDINGS(filly) {
    value_array<Vec3>("Vec3").element(emscripten::index<0>()).element(emscripten::index<1>())
        .element(emscripten::index<2>());
    value_array<Vec4>("Vec4").element(emscripten::index<0>()).element(emscripten::index<1>())
        .element(emscripten::index<2>()).element(emscripten::index<3>());
    value_array<std::array<float, 2>>("Vec2").element(emscripten::index<0>()).element(emscripten::index<1>());
    value_array<std::array<Vec3, 2>>("Box").element(emscripten::index<0>()).element(emscripten::index<1>());
    {
        auto matrix = value_array<Matrix>("Matrix");
        matrix.element(emscripten::index<0>()).element(emscripten::index<1>()).element(emscripten::index<2>())
            .element(emscripten::index<3>()).element(emscripten::index<4>()).element(emscripten::index<5>())
            .element(emscripten::index<6>()).element(emscripten::index<7>()).element(emscripten::index<8>())
            .element(emscripten::index<9>()).element(emscripten::index<10>()).element(emscripten::index<11>())
            .element(emscripten::index<12>()).element(emscripten::index<13>()).element(emscripten::index<14>())
            .element(emscripten::index<15>());
    }
    value_object<Stats>("Stats")
        .field("cpuSubmitMs", &Stats::cpu_submit_ms)
        .field("hostWaitMs", &Stats::host_wait_ms)
        .field("hostReleaseMs", &Stats::host_release_ms)
        .field("finishMs", &Stats::finish_ms)
        .field("readbackMs", &Stats::readback_ms)
        .field("liveModels", &Stats::live_models)
        .field("liveLights", &Stats::live_lights)
        .field("materialCopies", &Stats::material_copies)
        .field("framesRendered", &Stats::frames_rendered);

    function("setLogLevel", &set_log_level);
    function("currentGlContext", optional_override([] { return double(current_gl_context()); }));
    function("shapePlane", optional_override([](double width, double height, double columns, double rows) {
        return shape(shape_plane(width, height, int64_t(columns), int64_t(rows)));
    }));
    function("shapeBox", optional_override([](double width, double height, double depth) {
        return shape(shape_box(width, height, depth));
    }));
    function("shapeUvSphere", optional_override([](double radius, double segments, double rings) {
        return shape(shape_uv_sphere(radius, int64_t(segments), int64_t(rings)));
    }));
    function("shapeCylinder", optional_override([](double radius, double height, double segments, bool caps) {
        return shape(shape_cylinder(radius, height, int64_t(segments), caps));
    }));

    class_<Light>("Light")
        .function("close", &Light::close)
        .function("closed", &Light::closed)
        .function("hasNode", &Light::has_node)
        .function("node", &Light::node)
        .function("same", &Light::same)
        .function("type", &Light::type)
        .function("color", &Light::color)
        .function("setColor", &Light::set_color)
        .function("intensity", &Light::intensity)
        .function("setIntensity", &Light::set_intensity)
        .function("position", &Light::position)
        .function("setPosition", &Light::set_position)
        .function("direction", &Light::direction)
        .function("setDirection", &Light::set_direction)
        .function("range", &Light::range)
        .function("setRange", &Light::set_range)
        .function("castsShadows", &Light::casts_shadows)
        .function("setCastsShadows", &Light::set_casts_shadows)
        .function("setShadowOptions", &Light::set_shadow_options)
        .function("setSpotCone", &Light::set_spot_cone);

    class_<Texture>("Texture")
        .function("width", &Texture::width)
        .function("height", &Texture::height)
        .function("channels", &Texture::channels)
        .function("isFloat", &Texture::is_float)
        .function("colorSpace", &Texture::color_space)
        .function("mipmaps", &Texture::mipmaps)
        .function("update", &update_texture)
        .function("close", &Texture::close)
        .function("closed", &Texture::closed)
        .function("same", &Texture::same);

    class_<HostTexture>("HostTexture")
        .function("width", &HostTexture::width)
        .function("height", &HostTexture::height)
        .function("colorSpace", &HostTexture::color_space)
        .function("beginWrite", &HostTexture::begin_write)
        .function("endWrite", &HostTexture::end_write)
        .function("writing", &HostTexture::writing)
        .function("close", &HostTexture::close)
        .function("closed", &HostTexture::closed)
        .function("same", &HostTexture::same);

    class_<Material>("Material")
        .function("baseColor", &Material::base_color)
        .function("setBaseColor", &Material::set_base_color)
        .function("metallic", &Material::metallic)
        .function("setMetallic", &Material::set_metallic)
        .function("roughness", &Material::roughness)
        .function("setRoughness", &Material::set_roughness)
        .function("emissive", &Material::emissive)
        .function("setEmissive", &Material::set_emissive)
        .function("texture", &material_texture)
        .function("setTexture", optional_override([](Material& material, int slot, const Texture& texture) {
            material.set_texture(slot, texture.data());
        }))
        .function("setHostTexture", optional_override([](Material& material, int slot, const HostTexture& texture) {
            material.set_texture(slot, texture.data());
        }))
        .function("clearTexture", optional_override([](Material& material, int slot) {
            material.set_texture(slot, nullptr);
        }))
        .function("setTextureTransform", &Material::set_texture_transform);

    class_<Node>("Node")
        .function("name", optional_override([](const Node& node) { return optional_name(node.name()); }))
        .function("meshName", optional_override([](const Node& node) { return optional_name(node.mesh_name()); }))
        .function("index", optional_override([](const Node& node) { return double(node.index()); }))
        .function("parent", optional_override([](const Node& node) {
            return node.has_parent() ? val(node.parent()) : val::null();
        }))
        .function("children", optional_override([](const Node& node) { return array_of(node.children()); }))
        .function("transform", &Node::transform)
        .function("setTransform", &Node::set_transform)
        .function("position", &Node::position)
        .function("setPosition", &Node::set_position)
        .function("scale", &Node::scale)
        .function("setScale", &Node::set_scale)
        .function("quaternion", &Node::quaternion)
        .function("setQuaternion", &Node::set_quaternion)
        .function("rotationEulerRad", &Node::rotation_euler_rad)
        .function("setRotationEulerRad", &Node::set_rotation_euler_rad)
        .function("rotationEulerDeg", &Node::rotation_euler_deg)
        .function("setRotationEulerDeg", &Node::set_rotation_euler_deg)
        .function("material", optional_override([](const Node& node, int slot) { return node.material(size_t(slot)); }))
        .function("bounds", &Node::bounds)
        .function("morphTargetCount", optional_override([](const Node& node) { return double(node.morph_target_count()); }))
        .function("setMorphWeights", optional_override([](Node& node, const val& weights) {
            const auto values = copy_numbers<float>(weights);
            node.set_morph_weights(values.data.get(), values.size);
        }))
        .function("same", &Node::same);

    class_<Camera>("Camera")
        .function("setPerspective", &Camera::set_perspective)
        .function("setLensProjection", &Camera::set_lens_projection)
        .function("setOrthographic", &Camera::set_orthographic)
        .function("setOrthographicHeight", &Camera::set_orthographic_height)
        .function("position", &Camera::position)
        .function("setPosition", &Camera::set_position)
        .function("lookAt", &Camera::look_at)
        .function("transform", &Camera::transform)
        .function("setTransform", &Camera::set_transform)
        .function("viewMatrix", &Camera::view_matrix)
        .function("projection", &Camera::projection)
        .function("exposure", &Camera::exposure)
        .function("setExposure", &Camera::set_exposure)
        .function("focusDistance", &Camera::focus_distance)
        .function("setFocusDistance", &Camera::set_focus_distance)
        .function("aperture", &Camera::aperture)
        .function("setAperture", &Camera::set_aperture)
        .function("frameModel", optional_override([](Camera& camera, const Model& model, const val& options) {
            return camera.frame(model, frame_options(options));
        }))
        .function("frameNode", optional_override([](Camera& camera, const Node& node, const val& options) {
            return camera.frame(node, frame_options(options));
        }))
        .function("frameBox", optional_override([](Camera& camera, const std::array<Vec3, 2>& box, const val& options) {
            return camera.frame(box, frame_options(options));
        }))
        .function("hasNode", &Camera::has_node)
        .function("node", &Camera::node)
        .function("same", &Camera::same);

    class_<Model>("Model")
        .function("close", &Model::close)
        .function("closed", &Model::closed)
        .function("clone", optional_override([](Model& model) { return model.clone(); }))
        .function("cloneInto", optional_override([](Model& model, const Scene& scene) { return model.clone(&scene); }))
        .function("memory", &model_memory)
        .function("bounds", &Model::bounds)
        .function("transform", &Model::transform)
        .function("setTransform", &Model::set_transform)
        .function("position", &Model::position)
        .function("setPosition", &Model::set_position)
        .function("visible", &Model::visible)
        .function("setVisible", &Model::set_visible)
        .function("root", &Model::root)
        // The root node's rotation and scale, without a Node handle for each access.
        .function("scale", optional_override([](const Model& model) { return model.root().scale(); }))
        .function("setScale", optional_override([](Model& model, Vec3 value) { model.root().set_scale(value); }))
        .function("quaternion", optional_override([](const Model& model) { return model.root().quaternion(); }))
        .function("setQuaternion", optional_override([](Model& model, Vec4 value) {
            model.root().set_quaternion(value);
        }))
        .function("rotationEulerRad", optional_override([](const Model& model) {
            return model.root().rotation_euler_rad();
        }))
        .function("setRotationEulerRad", optional_override([](Model& model, Vec3 value) {
            model.root().set_rotation_euler_rad(value);
        }))
        .function("rotationEulerDeg", optional_override([](const Model& model) {
            return model.root().rotation_euler_deg();
        }))
        .function("setRotationEulerDeg", optional_override([](Model& model, Vec3 value) {
            model.root().set_rotation_euler_deg(value);
        }))
        .function("nodeByName", select_overload<Node(const std::string&) const>(&Model::node))
        .function("nodeByIndex", optional_override([](const Model& model, double index) { return model.node(int64_t(index)); }))
        .function("nodes", optional_override([](const Model& model) { return array_of(model.nodes()); }))
        .function("nodeNames", optional_override([](const Model& model) { return array_of(model.node_names()); }))
        .function("material", &Model::material)
        .function("materialNames", optional_override([](const Model& model) { return array_of(model.material_names()); }))
        .function("animations", &animations)
        .function("applyAnimationByName", select_overload<void(const std::string&, float, bool)>(&Model::apply_animation))
        .function("applyAnimationByIndex", optional_override([](Model& model, double index, float time, bool loop) {
            model.apply_animation(int64_t(index), time, loop);
        }))
        .function("resetAnimation", &Model::reset_animation)
        .function("variants", optional_override([](const Model& model) { return array_of(model.variants()); }))
        .function("applyVariantByName", select_overload<void(const std::string&)>(&Model::apply_variant))
        .function("applyVariantByIndex", optional_override([](Model& model, double index) { model.apply_variant(int64_t(index)); }))
        .function("lightByName", select_overload<Light(const std::string&) const>(&Model::light))
        .function("lightByIndex", optional_override([](const Model& model, double index) { return model.light(int64_t(index)); }))
        .function("lights", optional_override([](const Model& model) { return array_of(model.lights()); }))
        .function("cameraByName", select_overload<Camera(const std::string&) const>(&Model::camera))
        .function("cameraByIndex", optional_override([](const Model& model, double index) { return model.camera(int64_t(index)); }))
        .function("cameras", optional_override([](const Model& model) { return array_of(model.cameras()); }))
        .function("isMesh", &Model::is_mesh)
        .function("vertexCount", optional_override([](const Model& model) { return double(model.vertex_count()); }))
        .function("updateMesh", &update_mesh);

    class_<Scene>("Scene")
        .function("close", &Scene::close)
        .function("closed", &Scene::closed)
        .function("createCamera", &Scene::create_camera)
        .function("camera", &Scene::camera)
        .function("setCamera", &Scene::set_camera)
        .function("loadBytes", &load_bytes)
        .function("loadPath", &load_path)
        .function("createMesh", &create_mesh)
        .function("addDirectionalLight", &Scene::add_directional_light)
        .function("addSunLight", &Scene::add_sun_light)
        .function("addPointLight", &Scene::add_point_light)
        .function("addSpotLight", &Scene::add_spot_light)
        .function("encoding", &Scene::encoding)
        .function("setEncoding", &Scene::set_encoding)
        .function("outputPath", &Scene::output_path)
        .function("setOutputPath", &Scene::set_output_path)
        .function("toneMapping", &Scene::tone_mapping)
        .function("setToneMapping", &Scene::set_tone_mapping)
        .function("antialiasing", &Scene::antialiasing)
        .function("setAntialiasing", &Scene::set_antialiasing)
        .function("msaa", &Scene::msaa)
        .function("setMsaa", &Scene::set_msaa)
        .function("shadows", &Scene::shadows)
        .function("setShadows", &Scene::set_shadows)
        .function("refraction", &Scene::refraction)
        .function("setRefraction", &Scene::set_refraction)
        .function("transparent", &Scene::transparent)
        .function("setTransparent", &Scene::set_transparent)
        .function("dithering", &Scene::dithering)
        .function("setDithering", &Scene::set_dithering)
        .function("ssao", &Scene::ssao)
        .function("setSsao", &Scene::set_ssao)
        .function("bloom", &Scene::bloom)
        .function("setBloom", &Scene::set_bloom)
        .function("fog", &Scene::fog)
        .function("setFog", &Scene::set_fog)
        .function("setFogOptions", &Scene::set_fog_options)
        .function("depthOfField", &Scene::depth_of_field)
        .function("setDepthOfField", &Scene::set_depth_of_field)
        .function("vignette", &Scene::vignette)
        .function("setVignette", &Scene::set_vignette)
        .function("setVignetteOptions", &Scene::set_vignette_options)
        .function("setEnvironment", optional_override([](Scene& scene, const val& pixels, uint32_t width,
                                                         uint32_t height, float intensity, float rotation) {
            const auto values = copy_numbers<float>(pixels);
            scene.set_environment(values.data.get(), values.size, width, height, intensity, rotation);
        }))
        .function("loadEnvironmentPath", &Scene::load_environment)
        .function("loadEnvironmentKtxPath", &Scene::load_environment_ktx)
        .function("clearEnvironment", &Scene::clear_environment)
        .function("environmentIntensity", &Scene::environment_intensity)
        .function("setEnvironmentIntensity", &Scene::set_environment_intensity)
        .function("environmentVisible", &Scene::environment_visible)
        .function("setEnvironmentVisible", &Scene::set_environment_visible)
        .function("environmentRotation", &Scene::environment_rotation)
        .function("setEnvironmentRotation", &Scene::set_environment_rotation)
        .function("background", &Scene::background)
        .function("setBackground", &Scene::set_background);

    class_<Preparation>("Preparation")
        .function("ready", &Preparation::ready)
        .function("pending", optional_override([](const Preparation& preparation) { return double(preparation.pending()); }));

    class_<Readback>("Readback")
        .function("ready", &Readback::ready)
        .function("take", optional_override([](Readback& readback) {
            return typed_copy("Uint8Array", readback.take());
        }));

    class_<OffscreenTarget>("OffscreenTarget")
        .function("width", &OffscreenTarget::width)
        .function("height", &OffscreenTarget::height)
        .function("beginRead", &OffscreenTarget::begin_read)
        .function("close", &OffscreenTarget::close)
        .function("closed", &OffscreenTarget::closed);

    class_<ImportedTarget>("ImportedTarget")
        .function("width", &ImportedTarget::width)
        .function("height", &ImportedTarget::height)
        .function("acquire", &ImportedTarget::acquire)
        .function("release", &ImportedTarget::release)
        .function("close", &ImportedTarget::close)
        .function("closed", &ImportedTarget::closed);

    class_<Renderer>("Renderer")
        .constructor(optional_override([](double context, bool precompiled) {
            return Renderer(uintptr_t(context), precompiled);
        }))
        .function("createScene", &Renderer::create_scene)
        .function("createRenderTarget", optional_override([](Renderer& renderer, double width, double height, bool depth) {
            return renderer.create_render_target(int64_t(width), int64_t(height), "rgba8", depth);
        }))
        .function("importGlTexture", optional_override([](Renderer& renderer, uint32_t texture, double width,
                                                          double height, bool depth) {
            return renderer.import_gl_texture(texture, int64_t(width), int64_t(height), "rgba8", depth);
        }))
        .function("render", optional_override([](Renderer& renderer, const Scene& scene, const ImportedTarget& target,
                                                 const Camera* camera, const val& viewport, bool clear) {
            renderer.render(scene, target, render_options(camera, viewport, clear));
        }), allow_raw_pointers())
        .function("renderOffscreen", optional_override([](Renderer& renderer, const Scene& scene,
                                                          const OffscreenTarget& target, const Camera* camera,
                                                          const val& viewport, bool clear) {
            renderer.render(scene, target, render_options(camera, viewport, clear));
        }), allow_raw_pointers())
        .function("createTexture", &create_texture)
        .function("importGlInput", optional_override([](Renderer& renderer, uint32_t texture, double width,
                                                        double height, const std::string& color_space,
                                                        const std::string& filter, const std::string& wrap) {
            return renderer.import_gl_input(texture, int64_t(width), int64_t(height), color_space, filter, wrap);
        }))
        .function("prepare", &Renderer::prepare)
        .function("finish", &Renderer::finish)
        .function("resetGlState", &Renderer::reset_gl_state)
        .function("close", &Renderer::close)
        .function("closed", &Renderer::closed)
        .function("glPlatform", &Renderer::gl_platform)
        .function("stats", &Renderer::stats);
}
