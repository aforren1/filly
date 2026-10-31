#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace filly {

void set_log_level(const std::string& level);

using Vec3 = std::array<float, 3>;
using Vec4 = std::array<float, 4>;
// Angles that wrap: the setters reduce them modulo 360 in double precision before any float
// conversion, so a caller can pass a large angle, such as a rate times a clock reading.
using Vec3d = std::array<double, 3>;
using Matrix = std::array<float, 16>;

struct FillyError : std::runtime_error { using runtime_error::runtime_error; };
struct BackendError : FillyError { using FillyError::FillyError; };
struct AssetError : FillyError { using FillyError::FillyError; };
struct InteropError : FillyError { using FillyError::FillyError; };

struct Stats {
    double cpu_submit_ms = 0;
    double host_wait_ms = 0;
    double host_release_ms = 0;
    double finish_ms = 0;
    double readback_ms = 0;
    size_t live_models = 0, live_lights = 0, material_copies = 0;
    uint64_t frames_rendered = 0;
    // Filament's GPU timer for one earlier frame: it reports a frame two or more frames late.
    std::optional<double> gpu_frame_ms;
    std::optional<uint64_t> gpu_frame_number;
};

namespace detail {
struct Resource;
struct State;
struct SceneData;
struct CameraData;
struct ModelData;
struct AssetData;
struct AssetRef;
struct EnvironmentData;
struct TargetData;
struct TextureData;
struct ReadbackData;
struct PrepareData;
struct PickData;
}

// A material parameter that a clip animates through KHR_animation_pointer: the material's name
// in Model::material_names() and the parameter, for example "baseColorFactor". Texture transforms
// are reported as the parameter of their matrix, for example "baseColorUvMatrix".
struct AnimatedProperty {
    std::string material, property;
};

struct AnimationInfo {
    std::string name;
    float duration;
    std::vector<AnimatedProperty> material_properties;
};

// Memory that an asset holds, which its Asset handle and every model made from it share. It is
// freed when the last of them closes. GPU sizes follow each texture's internal format and mip
// levels and each buffer's layout as gltfio uploads it; drivers add their own overhead.
struct AssetMemory {
    uint64_t gpu_texture_bytes = 0;
    // Vertex, index, and morph target buffers.
    uint64_t gpu_geometry_bytes = 0;
    // The source data that Scene::add() needs (the document, its buffers, and filly's tables for
    // custom materials and property animation) while the Asset is open, and the instances'
    // animators. Generated meshes also keep their vertex arrays and staging buffers.
    uint64_t cpu_bytes = 0;
    // Bytes that each instance adds: on the GPU its bone and morph target buffers, on the CPU its
    // animator's copy of the clips. gltfio keeps them until the asset goes, also after the
    // instance's model closes; cpu_bytes and gpu_geometry_bytes include them for every
    // instance created so far.
    uint64_t instance_gpu_bytes = 0, instance_cpu_bytes = 0;
    // Live models that share the asset.
    size_t models = 0;
};

// Borrowed views of caller arrays. Null members are absent.
struct MeshArrays {
    size_t vertices = 0, triangles = 0;
    const float* positions = nullptr;   // vertices x 3
    const float* normals = nullptr;     // vertices x 3
    const float* uvs = nullptr;         // vertices x 2
    const float* colors = nullptr;      // vertices x 4
    const uint32_t* indices = nullptr;  // triangles x 3
};

// The glTF metallic-roughness material of a generated mesh. alpha_mode: opaque, mask, or blend.
struct MeshMaterial {
    Vec4 base_color = {1, 1, 1, 1};
    float metallic = 0, roughness = 1;
    Vec3 emissive = {0, 0, 0};
    bool unlit = false, double_sided = false;
    std::string alpha_mode = "opaque";
};

// A borrowed array of float32 or float64 values for the batch calls. Element (i, j, k)
// is at index i * strides[0] + j * strides[1] + k * strides[2], counted in elements, so that each
// frontend passes its arrays without a copy: NumPy arrays are usually row-major, MATLAB arrays
// column-major. Unused trailing dimensions have size 1.
struct ValueArray {
    const void* data = nullptr;
    bool is_double = true;
    std::array<size_t, 3> shape = {1, 1, 1};
    std::array<int64_t, 3> strides = {0, 0, 0};
    double at(size_t i, size_t j = 0, size_t k = 0) const {
        const auto index = int64_t(i) * strides[0] + int64_t(j) * strides[1] + int64_t(k) * strides[2];
        return is_double ? static_cast<const double*>(data)[index] : static_cast<const float*>(data)[index];
    }
};

enum class MaterialParameter { base_color, metallic, roughness, emissive };

// Per-render overrides. A viewport is (x, y, width, height) from the lower-left corner.
struct RenderOptions {
    const class Camera* camera = nullptr;
    bool has_viewport = false;
    std::array<int64_t, 4> viewport = {};
    bool clear = true;
};

class Node;
class Model;

class Light {
public:
    Light(std::shared_ptr<detail::Resource> owner, uint32_t entity);
    void close();
    bool closed() const;
    // The glTF node that places an imported light; false for lights that a scene created.
    bool has_node() const;
    Node node() const;
    bool same(const Light& other) const;
    uintptr_t key() const;
    std::string type() const;
    Vec3 color() const;
    void set_color(Vec3 value);
    float intensity() const;
    void set_intensity(float value);
    // Each property that a light type lacks reads as empty, and setting it throws: position for
    // directional and sun lights, direction for point lights, range for directional and sun
    // lights, and the cone for every light but spots.
    std::optional<Vec3> position() const;
    void set_position(Vec3 value);
    std::optional<Vec3> direction() const;
    void set_direction(Vec3 value);
    std::optional<float> range() const;
    void set_range(float value);
    // Inner and outer half-angles in degrees, as they were set or authored. Filament keeps only
    // values derived from them, which do not give the inner angle back exactly.
    std::optional<std::array<float, 2>> cone_deg() const;
    void set_cone_deg(std::array<float, 2> value);
    bool casts_shadows() const;
    void set_casts_shadows(bool value);
    uint32_t shadow_map_size() const;
    void set_shadow_map_size(uint32_t value);
    float shadow_constant_bias() const;
    void set_shadow_constant_bias(float value);
    float shadow_normal_bias() const;
    void set_shadow_normal_bias(float value);
private:
    std::shared_ptr<detail::Resource> owner_;
    uint32_t entity_;
};

// KHR_texture_transform of a runtime texture: offset and scale in UV units, rotation in degrees.
struct UvTransform {
    std::array<float, 2> offset = {0, 0}, scale = {1, 1};
    double rotation_deg = 0;
};

class Material {
public:
    Material(std::shared_ptr<detail::ModelData> data, size_t index);
    Vec4 base_color() const;
    void set_base_color(Vec4 value);
    float metallic() const;
    void set_metallic(float value);
    float roughness() const;
    void set_roughness(float value);
    Vec3 emissive() const;
    void set_emissive(Vec3 value);
    // Slot 0 is base color and slot 1 is emissive. A null texture removes the assignment.
    std::shared_ptr<detail::TextureData> texture(int slot) const;
    void set_texture(int slot, std::shared_ptr<detail::TextureData> texture);
    // Empty for a slot without a runtime texture, where setting it throws. Assigning a texture
    // keeps the transform; removing it resets the transform.
    std::optional<UvTransform> texture_transform(int slot) const;
    void set_texture_transform(int slot, UvTransform value);
    // For tests: the name of the Filament material behind this handle, and whether it refracts
    // in screen space.
    std::pair<std::string, bool> shader() const;
    // Batch form of the parameter setters; see Node::set_transforms(). values has shape
    // (materials, components): 4 for base_color, 3 for emissive, else 1.
    static void set_parameters(const std::vector<Material>& materials, MaterialParameter parameter,
                               const ValueArray& values);
private:
    std::shared_ptr<detail::ModelData> data_;
    size_t index_;
};

class Node {
public:
    Node(std::shared_ptr<detail::ModelData> data, uint32_t entity);
    std::optional<std::string> name() const;
    std::optional<std::string> mesh_name() const;
    int64_t index() const;
    // False for a top-level node.
    bool has_parent() const;
    Node parent() const;
    std::vector<Node> children() const;
    Matrix transform() const;
    void set_transform(const Matrix& value);
    Vec3 position() const;
    void set_position(Vec3 value);
    Vec3 scale() const;
    void set_scale(Vec3 value);
    Vec4 quaternion() const;
    void set_quaternion(Vec4 value);
    Vec3 rotation_euler_deg() const;
    void set_rotation_euler_deg(Vec3d value);
    Material material(size_t slot) const;
    // Model.bounds restricted to this node and its descendants.
    std::array<Vec3, 2> bounds() const;
    size_t morph_target_count() const;
    void set_morph_weights(const float* values, size_t count);
    bool same(const Node& other) const;
    uintptr_t key() const;
    // Batch calls, for frontends where one call costs more than one update, such as MATLAB.
    // The items may come from any open models of one renderer. All inputs are checked before
    // the first update, so an error changes nothing.
    // transforms has shape (nodes, 4, 4): row, then column, of each set_transform() matrix.
    static void set_transforms(const std::vector<Node>& nodes, const ValueArray& transforms);
    // weights has shape (nodes, targets), and every node must have `targets` morph targets.
    static void set_morph_weights(const std::vector<Node>& nodes, const ValueArray& weights);
private:
    std::shared_ptr<detail::ModelData> data_;
    uint32_t entity_;
    friend class Camera;
};

// Camera::frame() inputs; see docs/reference/api.md.
struct FrameOptions {
    double fill = 0.8;
    std::optional<Vec3> direction;
    Vec3 up = {0, 1, 0};
    std::string fit = "sphere";
    std::optional<double> near, far, aspect;
};

// A new camera has a perspective projection with these values that follows the target aspect.
constexpr double default_fov_y_deg = 30, default_near = 0.1, default_far = 100;

class Camera {
public:
    explicit Camera(std::shared_ptr<detail::CameraData> data);
    // fov_y_deg is the vertical field of view in degrees. An aspect of zero selects the render
    // target's aspect, applied at render time. A plane that is given, here or to frame(), stays
    // until the next projection call; frame() sets the others.
    void set_perspective(double fov_y_deg, double aspect, std::optional<double> near, std::optional<double> far);
    void set_lens_projection(double focal_length, double aspect, std::optional<double> near,
                             std::optional<double> far);
    void set_orthographic(double left, double right, double bottom, double top,
                          std::optional<double> near, std::optional<double> far);
    void set_orthographic_height(double height, double center_x, double center_y,
                                 std::optional<double> near, std::optional<double> far);
    Vec3 position() const;
    void set_position(Vec3 value);
    void look_at(Vec3 target, Vec3 up);
    Matrix transform() const;
    void set_transform(const Matrix& value);
    Matrix view_matrix() const;
    Matrix projection() const;
    float exposure() const;
    void set_exposure(float ev100);
    // Depth-of-field inputs. The aperture is an f-number and does not change exposure. Filament
    // focuses no closer than the near plane, so the default focus distance of 0 focuses there.
    float focus_distance() const;
    void set_focus_distance(float value);
    float aperture() const;
    void set_aperture(float value);
    // Aims at the target's center and sets the distance and clipping planes so that it fills
    // `fill` of the view. Returns the distance from the camera to that center. Without
    // options.aspect, a camera that follows the target aspect is framed again at render time
    // for a target of another aspect, until its pose or projection changes.
    double frame(const Model& target, const FrameOptions& options);
    double frame(const Node& target, const FrameOptions& options);
    double frame(const std::array<Vec3, 2>& box, const FrameOptions& options);
    bool same(const Camera& other) const;
    uintptr_t key() const;
    // The glTF node of an imported camera; false for cameras from Renderer::create_camera().
    bool has_node() const;
    Node node() const;
    // Scenes that use the camera lose it. Imported cameras close with their model.
    void close();
    bool closed() const;
private:
    std::shared_ptr<detail::CameraData> data_;
    friend class Scene;
    friend class Renderer;
};

class Model {
public:
    explicit Model(std::shared_ptr<detail::ModelData> data);
    void close();
    bool closed() const;
    bool same(const Model& other) const;
    uintptr_t key() const;
    // The memory of the model's asset, which it shares with the other models of that asset.
    AssetMemory memory() const;
    std::array<Vec3, 2> bounds() const;
    Matrix transform() const;
    void set_transform(const Matrix& value);
    Vec3 position() const;
    void set_position(Vec3 value);
    bool visible() const;
    void set_visible(bool value);
    Node root() const;
    Node node(const std::string& name) const;
    Node node(int64_t index) const;
    std::vector<Node> nodes() const;
    std::vector<std::string> node_names() const;
    Material material(const std::string& name) const;
    std::vector<std::string> material_names() const;
    std::vector<AnimationInfo> animations() const;
    void apply_animation(const std::string& name, float time, bool loop);
    void apply_animation(int64_t index, float time, bool loop);
    void reset_animation();
    std::vector<std::string> variants() const;
    void apply_variant(const std::string& name);
    void apply_variant(int64_t index);
    // Lights and cameras are addressed by the node that places them: a node name or glTF index.
    Light light(const std::string& node) const;
    Light light(int64_t node) const;
    std::vector<Light> lights() const;
    Camera camera(const std::string& node) const;
    Camera camera(int64_t node) const;
    std::vector<Camera> cameras() const;
    // Generated meshes only. An update changes the mesh of the model's asset, so every model of
    // an asset from Renderer::create_mesh_asset() shows it.
    bool is_mesh() const;
    size_t vertex_count() const;
    void update_mesh(const MeshArrays& arrays);
private:
    Camera camera_at(size_t position) const;
    std::shared_ptr<detail::ModelData> data_;
    friend class Scene;
    friend class Camera;
};

// A glTF asset, or a mesh from Renderer::create_mesh_asset(), that the renderer keeps for
// Scene::add(): its GPU textures and geometry and the source data that each new instance reads. Any scene of the renderer can add it. close()
// releases the source data; models already made from it keep the GPU data until the last of
// them closes.
class Asset {
public:
    explicit Asset(std::shared_ptr<detail::AssetRef> data);
    AssetMemory memory() const;
    void close();
    bool closed() const;
private:
    std::shared_ptr<detail::AssetRef> data_;
    friend class Scene;
};

// Image-based lighting that any scene of the renderer can use: prefiltered reflections, diffuse
// lighting, and a skybox cubemap. Scenes share these textures; each scene has its own
// intensity and rotation. close() removes it from the scenes that use it.
class Environment {
public:
    explicit Environment(std::shared_ptr<detail::EnvironmentData> data);
    void close();
    bool closed() const;
    bool same(const Environment& other) const;
    uintptr_t key() const;
private:
    std::shared_ptr<detail::EnvironmentData> data_;
    friend class Scene;
};

class Scene {
public:
    explicit Scene(std::shared_ptr<detail::SceneData> data);
    void close();
    bool closed() const;
    Camera camera() const;
    void set_camera(const Camera& camera);
    // Renderer::load_asset() and add() in one step. The asset releases its source data after
    // this one instance, as no handle can add it again.
    Model load(const std::string& path, bool strict, std::vector<std::string>& warnings);
    Model load(const uint8_t* bytes, size_t size, bool strict, std::vector<std::string>& warnings);
    // A new instance of the asset, which must belong to the same renderer and be open.
    Model add(const Asset& asset);
    // Renderer::create_mesh_asset() and add() in one step, without a shared material: a model
    // with one node and one glTF material whose geometry is the caller's arrays.
    Model create_mesh(const MeshArrays& arrays, const MeshMaterial& material);
    Light add_directional_light(Vec3 direction, float intensity, Vec3 color);
    Light add_sun_light(Vec3 direction, float intensity, Vec3 color, float angular_radius_deg,
                        float halo_size, float halo_falloff);
    Light add_point_light(Vec3 position, float intensity, Vec3 color, float range);
    // cone_deg: inner and outer half-angles in degrees.
    Light add_spot_light(Vec3 position, Vec3 direction, float intensity, Vec3 color,
                         float range, std::array<float, 2> cone_deg);
    std::string encoding() const;
    void set_encoding(const std::string& value);
    // "exact" (the default) or "direct". Direct output skips filly's encode pass, so it rejects
    // every option and material that needs that pass instead of falling back.
    std::string output_path() const;
    void set_output_path(const std::string& value);
    std::string tone_mapping() const;
    void set_tone_mapping(const std::string& value);
    std::string antialiasing() const;
    void set_antialiasing(const std::string& value);
    int msaa() const;
    void set_msaa(int value);
    bool shadows() const;
    void set_shadows(bool value);
    bool transparent() const;
    void set_transparent(bool value);
    bool dithering() const;
    void set_dithering(bool value);
    bool ssao() const;
    void set_ssao(bool value);
    bool bloom() const;
    void set_bloom(bool value);
    // Effect parameters keep their values while the effect is off.
    bool fog() const;
    void set_fog(bool value);
    // The linear output color of fully fogged pixels.
    Vec3 fog_color() const;
    void set_fog_color(Vec3 value);
    // Per scene unit.
    float fog_density() const;
    void set_fog_density(float value);
    float fog_start() const;
    void set_fog_start(float value);
    bool depth_of_field() const;
    void set_depth_of_field(bool value);
    bool vignette() const;
    void set_vignette(bool value);
    float vignette_midpoint() const;
    void set_vignette_midpoint(float value);
    float vignette_roundness() const;
    void set_vignette_roundness(float value);
    float vignette_feather() const;
    void set_vignette_feather(float value);
    Vec3 vignette_color() const;
    void set_vignette_color(Vec3 value);
    // Empty without an environment. Null removes it.
    std::optional<Environment> environment() const;
    void set_environment(const Environment* environment);
    // Scene settings that apply to whichever environment the scene uses, and keep their values
    // without one. The intensity is in lux; the rotation is in degrees about +Y.
    float environment_intensity() const;
    void set_environment_intensity(float value);
    float environment_rotation_deg() const;
    void set_environment_rotation_deg(double value);
    bool environment_visible() const;
    void set_environment_visible(bool value);
    Vec4 background() const;
    void set_background(Vec4 color);
    // Whether picks hit surfaces of blended (alpha_mode "blend") materials, as if they were
    // opaque. Otherwise picks pass through them.
    bool pick_blended() const;
    void set_pick_blended(bool value);
private:
    std::shared_ptr<detail::SceneData> data_;
    friend class Renderer;
    friend class Model;
};

// Vertex arrays for simple shapes, as Scene::create_mesh takes them: positions and normals are
// vertices x 3, uvs vertices x 2, indices triangles x 3. Front faces wind counterclockwise.
struct ShapeArrays {
    std::vector<float> positions, normals, uvs;
    std::vector<uint32_t> indices;
};
ShapeArrays shape_plane(double width, double height, int64_t columns, int64_t rows);
ShapeArrays shape_box(double width, double height, double depth);
ShapeArrays shape_uv_sphere(double radius, int64_t segments, int64_t rings);
ShapeArrays shape_cylinder(double radius, double height, int64_t segments, bool caps);

// A sampled texture with pixels from the caller. Updates copy into a small ring of staging
// buffers, so the caller's array is free again when update() returns.
class Texture {
public:
    explicit Texture(std::shared_ptr<detail::TextureData> data);
    uint32_t width() const;
    uint32_t height() const;
    uint32_t channels() const;
    bool is_float() const;
    std::string color_space() const;
    bool mipmaps() const;
    // Pixels are rows from the top, tightly packed, in the type and channel count of creation.
    void update(const void* pixels, size_t bytes);
    // update() in two steps, for callers that can write the pixels straight into the upload
    // storage and save update()'s copy. begin_update() returns storage for `bytes` bytes in the
    // layout of update(); commit_update() uploads it. A second begin_update() before the commit
    // returns the same storage. 1-channel sRGB textures expand to RGBA on upload and must use
    // update().
    uint8_t* begin_update(size_t bytes);
    void commit_update();
    void close();
    bool closed() const;
    bool same(const Texture& other) const;
    uintptr_t key() const;
    std::shared_ptr<detail::TextureData> data() const { return data_; }
private:
    std::shared_ptr<detail::TextureData> data_;
};

// A host-owned OpenGL texture that Filament samples. The host writes it between begin_write()
// and end_write(); Filament waits for those writes, and the host waits for Filament's reads.
class ImportedTexture {
public:
    explicit ImportedTexture(std::shared_ptr<detail::TextureData> data);
    uint32_t width() const;
    uint32_t height() const;
    std::string color_space() const;
    void begin_write();
    void end_write();
    bool writing() const;
    void close();
    bool closed() const;
    bool same(const ImportedTexture& other) const;
    uintptr_t key() const;
    std::shared_ptr<detail::TextureData> data() const { return data_; }
private:
    std::shared_ptr<detail::TextureData> data_;
};

// Receives the pixels of a readback: `top` is the top row of RGBA bytes, and each next row starts
// `stride` bytes after the previous one. The stride is negative when the rows are stored
// bottom-up. On the desktop the sink runs on Filament's driver thread while the caller waits, so
// it must not call filly.
using RowSink = std::function<void(const uint8_t* top, ptrdiff_t stride)>;

// A copy of a RenderTarget's pixels that RenderTarget::begin_read() queued after the renders
// before it. Later renders into the target do not change it, so a loop can render the next
// frame before it reads this one (see docs/reference/api.md#readback).
class Readback {
public:
    explicit Readback(std::shared_ptr<detail::ReadbackData> data);
    // Reports whether the GPU has finished the copy, without waiting for it. On the desktop each
    // call queues a check on Filament's driver thread, whose answer a later call sees. WebGL
    // signals the copy only after control returns to the browser.
    bool ready();
    // Waits for the copy and passes the rows to the sink, then closes the readback. The web
    // build cannot wait, so there read() needs ready() to have returned true.
    void read(const RowSink& sink);
    // As read(sink), into width * height * 4 bytes, top row first.
    void read(uint8_t* pixels);
    uint32_t width() const;
    uint32_t height() const;
    // Stats::frames_rendered when begin_read() queued the copy.
    uint64_t frame_number() const;
    // Releases the copy without reading it. Repeated calls are valid.
    void close();
    bool closed() const;
private:
    std::shared_ptr<detail::ReadbackData> data_;
};

// Shader compilation that Renderer::prepare() started. Compilation runs in the background:
// on the desktop in Filament's compiler threads, in browsers with KHR_parallel_shader_compile.
class Preparation {
public:
    explicit Preparation(std::shared_ptr<detail::PrepareData> data);
    // Lets the backend make progress, then reports whether every material has its programs and
    // the renderer's first pick readback is done.
    bool ready();
    // Materials whose programs are not compiled yet, plus one while that readback runs.
    size_t pending() const;
private:
    std::shared_ptr<detail::PrepareData> data_;
};

// The model surface that a pick found. position is in world space; depth is the distance from
// the camera along its viewing direction, in scene units.
struct PickResult {
    Model model;
    Node node;
    Vec3 position;
    float depth;
};

// A pick that Renderer::pick() queued. The next render of its scene takes it; Filament answers
// it from that frame's depth pass, a frame or more later (see docs/reference/api.md#picking).
class PickQuery {
public:
    explicit PickQuery(std::shared_ptr<detail::PickData> data);
    // Delivers the answers that arrived, without waiting for the GPU, and reports whether this
    // one has. The first true call maps the answer to a model node.
    bool ready();
    // Empty for no model surface at the point. Throws until ready() returns true.
    std::optional<PickResult> result();
    // Stats::frames_rendered just after the render that took the query; empty before it.
    std::optional<uint64_t> frame_number() const;
private:
    std::shared_ptr<detail::PickData> data_;
};

// A Filament-owned color texture that supports readback.
class RenderTarget {
public:
    explicit RenderTarget(std::shared_ptr<detail::TargetData> data);
    uint32_t width() const;
    uint32_t height() const;
    // Queues a copy of the pixels and returns at once. On the desktop each readback in flight
    // holds a pixel buffer object of the target's size, which the target keeps for later
    // readbacks until it closes.
    Readback begin_read() const;
    // begin_read() followed by Readback::read(). Not available in the web build.
    void read(const RowSink& sink) const;
    void read(uint8_t* pixels) const;
    void close();
    bool closed() const;
private:
    std::shared_ptr<detail::TargetData> data_;
    friend class Renderer;
};

// A host-owned OpenGL texture. The host samples it between acquire() and release().
// Nested acquisitions only count depth.
class ImportedTarget {
public:
    explicit ImportedTarget(std::shared_ptr<detail::TargetData> data);
    uint32_t width() const;
    uint32_t height() const;
    void acquire();
    void release();
    bool acquired() const;
    void close();
    bool closed() const;
private:
    std::shared_ptr<detail::TargetData> data_;
    friend class Renderer;
};

#if defined(__APPLE__)
// A Filament-owned target whose frames reach a host OpenGL texture, for a host context that
// cannot share objects with Filament's. macOS shares objects only between contexts of one
// profile, and Filament needs the core profile, while PsychoPy windows have the legacy one.
// render() queues a copy of each frame; acquire() waits for it and copies it into the host
// texture, which the host then samples. The host context is the one current at creation.
class CopyTarget {
public:
    explicit CopyTarget(std::shared_ptr<detail::TargetData> data);
    uint32_t width() const;
    uint32_t height() const;
    // "iosurface": the GPU copies each frame through an IOSurface that both contexts bind.
    // "readback": the frame goes through CPU memory, as RenderTarget::read() reads it.
    std::string transfer() const;
    void acquire();
    void release();
    bool acquired() const;
    // With the host context current, also deletes the host-side objects; otherwise they go away
    // with the host context. The host texture stays the host's.
    void close();
    bool closed() const;
private:
    std::shared_ptr<detail::TargetData> data_;
    friend class Renderer;
};
#endif

class Renderer {
public:
    explicit Renderer(uintptr_t shared_context = 0);
    Scene create_scene();
    // Cameras belong to the renderer, as in Filament, so any of its scenes can use one. A camera
    // lives until close(), the renderer's close, or the last reference to it goes.
    Camera create_camera();
    // Loads a glTF or GLB file (UTF-8 path) or document bytes, which must then embed every
    // resource. Messages about features that load but render differently are appended to
    // warnings; with strict, the first one throws AssetError instead, as do unsupported
    // required extensions. The bytes are copied.
    Asset load_asset(const std::string& path, bool strict, std::vector<std::string>& warnings);
    Asset load_asset(const uint8_t* bytes, size_t size, bool strict, std::vector<std::string>& warnings);
    // An asset whose models share the vertex and index buffers of the caller's arrays, and one
    // material instance until a model's material handle is requested. arrays.indices and
    // arrays.triangles are required.
    Asset create_mesh_asset(const MeshArrays& arrays, const MeshMaterial& material);
    // Environments, which any scene of the renderer can use. A 2:1 panorama: a Radiance HDR, PNG,
    // or JPEG file, or width * height * 3 linear RGB values (count), rows from the top, which are
    // copied. Loading filters it for reflections and diffuse lighting and waits for the GPU.
    Environment load_environment(const std::string& path);
    Environment create_environment(const float* pixels, size_t count, uint32_t width, uint32_t height);
    // Prefiltered cubemaps from cmgen; an empty skybox_path uses the sharpest IBL level.
    Environment load_environment_ktx(const std::string& ibl_path, const std::string& skybox_path);
    RenderTarget create_render_target(int64_t width, int64_t height,
                                      const std::string& format, bool depth);
    // on_renderer_close lets a host integration release the host objects around the texture,
    // which the renderer does not know: close() calls it before it releases anything else,
    // while the host context can still be current. It is dropped when the target closes first.
    ImportedTarget import_gl_target(uint32_t texture, int64_t width, int64_t height,
                                    const std::string& format, bool depth,
                                    std::function<void()> on_renderer_close = {});
    void render(const Scene& scene, const RenderTarget& target, const RenderOptions& options = {});
    void render(const Scene& scene, const ImportedTarget& target, const RenderOptions& options = {});
#if defined(__APPLE__)
    // texture is a GL_TEXTURE_2D with GL_RGBA8 storage in the current host context; transfer is
    // "iosurface" or "readback". Throws InteropError when the transfer is unavailable, so that a
    // host adapter can choose the other one. on_renderer_close as for import_gl_target().
    CopyTarget create_copy_target(uint32_t texture, int64_t width, int64_t height, const std::string& transfer,
                                  std::function<void()> on_renderer_close = {});
    void render(const Scene& scene, const CopyTarget& target, const RenderOptions& options = {});
#endif
    // Queues a query for what the scene shows at target pixel (x, y), counted from the
    // lower-left corner like viewports. The scene's next render takes it.
    PickQuery pick(const Scene& scene, double x, double y);
    // channels is 1, 3, or 4. Float textures hold linear values. pixels as for Texture::update().
    Texture create_texture(int64_t width, int64_t height, int channels, bool is_float,
                           const std::string& color_space, bool mipmaps,
                           const std::string& filter, const std::string& wrap,
                           const void* pixels, size_t bytes);
    ImportedTexture import_gl_texture(uint32_t texture, int64_t width, int64_t height,
                                      const std::string& color_space, const std::string& filter,
                                      const std::string& wrap);
    void finish();
    // Starts compiling the GPU programs that rendering this scene needs, for its current models,
    // settings, and output path, without waiting. A render after ready() returns true does not
    // stop to compile them.
    Preparation prepare(const Scene& scene);
    // The web build shares the page's WebGL2 context: after the page changed GL state, this makes
    // Filament set the state it needs again instead of trusting its cache. On the desktop,
    // Filament has its own context, and this does nothing.
    void reset_gl_state();
    // Calls the on_renderer_close callbacks of open imported targets, newest first, then releases
    // every resource. The first exception from a callback is rethrown after the renderer closed.
    void close();
    bool closed() const;
    uintptr_t shared_context() const;
    // "wgl", "glx", "egl", "cgl", or "webgl".
    std::string gl_platform() const;
    Stats stats() const;
private:
    void submit(const Scene& scene, detail::TargetData& target, const RenderOptions& options);
    std::shared_ptr<detail::State> state_;
};

// Whether a material's texture is an ImportedTexture rather than a Texture.
bool is_imported_texture(const std::shared_ptr<detail::TextureData>& data);

uintptr_t current_gl_context();
// For host adapters: an immutable GL_RGBA8 texture in the current context, which
// import_gl_target() accepts, and its deletion. Pending host errors are discarded first.
uint32_t create_host_texture(int64_t width, int64_t height);
void delete_host_texture(uint32_t texture);
// For host adapters: a program and a vertex array in the current context that draw a texture as a
// quad. Vertex arrays are per context, so the quad works only in the context that created it.
struct HostQuad {
    uint32_t program = 0, vertex_array = 0;
};
HostQuad create_host_quad();
// Draws `texture` into the bound draw framebuffer at window pixels (x, y, width, height), from the
// lower-left corner. With blend, it composites premultiplied source-over; otherwise it copies.
// Depth, stencil, and face culling do not apply; the scissor test does. The GL state that the
// draw changes is restored, so hosts that cache GL state stay correct.
void draw_host_quad(const HostQuad& quad, uint32_t texture, int64_t x, int64_t y, int64_t width,
                    int64_t height, bool blend);
void delete_host_quad(const HostQuad& quad);

}
