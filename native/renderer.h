#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace filly {

void set_log_level(const std::string& level);

using Vec3 = std::array<float, 3>;
using Vec4 = std::array<float, 4>;
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
};

namespace detail {
struct Resource;
struct State;
struct SceneData;
struct CameraData;
struct ModelData;
struct TargetData;
struct TextureData;
struct ReadbackData;
struct PrepareData;
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

// Memory that a model's asset holds, which the model shares with its clones and its prototype.
// It is freed when the last of them closes. GPU sizes follow each texture's internal format and
// mip levels and each buffer's layout as gltfio uploads it; drivers add their own overhead.
struct ModelMemory {
    uint64_t gpu_texture_bytes = 0;
    // Vertex, index, and morph target buffers.
    uint64_t gpu_geometry_bytes = 0;
    // The source data that clone() needs (the document, its buffers, and filly's tables for
    // custom materials and property animation) if the asset is clonable, and the instances'
    // animators. Generated meshes also keep their vertex arrays and staging buffers.
    uint64_t cpu_bytes = 0;
    // Bytes that each instance adds: on the GPU its bone and morph target buffers, on the CPU its
    // animator's copy of the clips. gltfio keeps them until the asset goes, also after the
    // instance's model closes; cpu_bytes and gpu_geometry_bytes include them for every
    // instance created so far.
    uint64_t clone_gpu_bytes = 0, clone_cpu_bytes = 0;
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
    Vec3 position() const;
    void set_position(Vec3 value);
    Vec3 direction() const;
    void set_direction(Vec3 value);
    float range() const;
    void set_range(float value);
    bool casts_shadows() const;
    void set_casts_shadows(bool value);
    void set_shadow_options(uint32_t map_size, float constant_bias, float normal_bias);
    void set_spot_cone(float inner, float outer);
private:
    std::shared_ptr<detail::Resource> owner_;
    uint32_t entity_;
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
    // Offset and scale in UV units, rotation in radians, as KHR_texture_transform.
    void set_texture_transform(int slot, std::array<float, 2> offset, std::array<float, 2> scale,
                               float rotation);
    // For tests: the name of the Filament material behind this handle, and whether it refracts
    // in screen space.
    std::pair<std::string, bool> shader() const;
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
    Vec3 rotation_euler_rad() const;
    void set_rotation_euler_rad(Vec3 value);
    Vec3 rotation_euler_deg() const;
    void set_rotation_euler_deg(Vec3 value);
    Material material(size_t slot) const;
    // Model.bounds restricted to this node and its descendants.
    std::array<Vec3, 2> bounds() const;
    size_t morph_target_count() const;
    void set_morph_weights(const float* values, size_t count);
    bool same(const Node& other) const;
    uintptr_t key() const;
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

class Camera {
public:
    explicit Camera(std::shared_ptr<detail::CameraData> data);
    // An aspect of zero selects the render target's aspect, applied at render time.
    void set_perspective(double fov_y, double aspect, double near, double far);
    void set_lens_projection(double focal_length, double aspect, double near, double far);
    void set_orthographic(double left, double right, double bottom, double top,
                          double near, double far);
    void set_orthographic_height(double height, double center_x, double center_y,
                                 double near, double far);
    Vec3 position() const;
    void set_position(Vec3 value);
    void look_at(Vec3 target, Vec3 up);
    Matrix transform() const;
    void set_transform(const Matrix& value);
    Matrix view_matrix() const;
    Matrix projection() const;
    float exposure() const;
    void set_exposure(float ev100);
    // Depth-of-field inputs. The aperture is an f-number and does not change exposure.
    float focus_distance() const;
    void set_focus_distance(float value);
    float aperture() const;
    void set_aperture(float value);
    // Aims at the target's center and sets the distance and clipping planes so that it fills
    // `fill` of the view. Returns the distance from the camera to that center.
    double frame(const Model& target, const FrameOptions& options);
    double frame(const Node& target, const FrameOptions& options);
    double frame(const std::array<Vec3, 2>& box, const FrameOptions& options);
    bool same(const Camera& other) const;
    uintptr_t key() const;
    // The glTF node of an imported camera; false for cameras that a scene created.
    bool has_node() const;
    Node node() const;
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
    // Another instance of the asset in `scene`, which must belong to the same renderer; null
    // selects this model's scene.
    Model clone(const class Scene* scene = nullptr);
    ModelMemory memory() const;
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
    // Generated meshes only.
    bool is_mesh() const;
    size_t vertex_count() const;
    void update_mesh(const MeshArrays& arrays);
private:
    Camera camera_at(size_t position) const;
    // Replaces the geometry of the placeholder asset that Scene::create_mesh loads.
    void attach_mesh(const MeshArrays& arrays);
    std::shared_ptr<detail::ModelData> data_;
    friend class Scene;
    friend class Camera;
};

class Scene {
public:
    explicit Scene(std::shared_ptr<detail::SceneData> data);
    void close();
    bool closed() const;
    Camera create_camera();
    Camera camera() const;
    void set_camera(const Camera& camera);
    // Loads a glTF or GLB file (UTF-8 path) or document bytes, which must then embed every
    // resource. Messages about features that load but render differently are appended to
    // warnings; with strict, the first one throws AssetError instead, as do unsupported
    // required extensions. clonable keeps the source data that Model::clone() needs.
    Model load(const std::string& path, bool strict, bool clonable, std::vector<std::string>& warnings);
    // Copies the bytes.
    Model load(const uint8_t* bytes, size_t size, bool strict, bool clonable, std::vector<std::string>& warnings);
    // A model with one node and one glTF material whose geometry is the caller's arrays.
    // arrays.indices and arrays.triangles are required.
    Model create_mesh(const MeshArrays& arrays, const MeshMaterial& material);
    Light add_directional_light(Vec3 direction, float intensity, Vec3 color);
    Light add_sun_light(Vec3 direction, float intensity, Vec3 color, float angular_radius,
                        float halo_size, float halo_falloff);
    Light add_point_light(Vec3 position, float intensity, Vec3 color, float range);
    Light add_spot_light(Vec3 position, Vec3 direction, float intensity, Vec3 color,
                         float range, float inner, float outer);
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
    bool refraction() const;
    void set_refraction(bool value);
    bool transparent() const;
    void set_transparent(bool value);
    bool dithering() const;
    void set_dithering(bool value);
    bool ssao() const;
    void set_ssao(bool value);
    bool bloom() const;
    void set_bloom(bool value);
    bool fog() const;
    void set_fog(bool value);
    // Fog color is the linear output color of fully fogged pixels; density is per scene unit.
    void set_fog_options(Vec3 color, float density, float start);
    bool depth_of_field() const;
    void set_depth_of_field(bool value);
    bool vignette() const;
    void set_vignette(bool value);
    void set_vignette_options(float midpoint, float roundness, float feather, Vec3 color);
    // pixels: width * height * 3 values (count), rows from the top. They are copied.
    void set_environment(const float* pixels, size_t count, uint32_t width, uint32_t height,
                         float intensity, float rotation);
    void load_environment(const std::string& path, float intensity, float rotation);
    void load_environment_ktx(const std::string& ibl_path, const std::string& skybox_path,
                              float intensity, float rotation);
    void clear_environment();
    float environment_intensity() const;
    void set_environment_intensity(float value);
    bool environment_visible() const;
    void set_environment_visible(bool value);
    float environment_rotation() const;
    void set_environment_rotation(float value);
    Vec4 background() const;
    void set_background(Vec4 color);
private:
    Model load_document(std::vector<uint8_t> bytes, const std::string& path, bool strict, bool clonable,
                        std::vector<std::string>& warnings);
    void set_uniform_environment(Vec3 radiance, float intensity, float rotation);
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
class HostTexture {
public:
    explicit HostTexture(std::shared_ptr<detail::TextureData> data);
    uint32_t width() const;
    uint32_t height() const;
    std::string color_space() const;
    void begin_write();
    void end_write();
    bool writing() const;
    void close();
    bool closed() const;
    bool same(const HostTexture& other) const;
    uintptr_t key() const;
    std::shared_ptr<detail::TextureData> data() const { return data_; }
private:
    std::shared_ptr<detail::TextureData> data_;
};

// RGBA bytes, rows from the top. The storage is not zero-filled before the GPU writes it: at
// 1920 x 1080 the fill cost about 2 ms per read.
struct PixelBuffer {
    std::unique_ptr<uint8_t[]> data;
    size_t size = 0;
};

// A readback that OffscreenTarget::begin_read() started. WebGL completes GPU readbacks only
// after control returns to the browser, so the web build polls ready() on later turns of its
// event loop; on the desktop the first ready() completes it.
class Readback {
public:
    explicit Readback(std::shared_ptr<detail::ReadbackData> data);
    // Runs pending GPU work, then reports whether the pixels arrived.
    bool ready();
    // RGBA rows from the top. Valid once, after ready() returned true.
    std::vector<uint8_t> take();
private:
    std::shared_ptr<detail::ReadbackData> data_;
    friend class OffscreenTarget;
};

// Shader compilation that Renderer::prepare() started. Compilation runs in the background:
// on the desktop in Filament's compiler threads, in browsers with KHR_parallel_shader_compile.
class Preparation {
public:
    explicit Preparation(std::shared_ptr<detail::PrepareData> data);
    // Lets the backend make progress, then reports whether every material has its programs.
    bool ready();
    // Materials whose programs are not compiled yet.
    size_t pending() const;
private:
    std::shared_ptr<detail::PrepareData> data_;
};

// A Filament-owned color texture that supports readback.
class OffscreenTarget {
public:
    explicit OffscreenTarget(std::shared_ptr<detail::TargetData> data);
    uint32_t width() const;
    uint32_t height() const;
    PixelBuffer read() const;
    Readback begin_read() const;
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

class Renderer {
public:
    explicit Renderer(uintptr_t shared_context = 0);
    Scene create_scene();
    OffscreenTarget create_render_target(int64_t width, int64_t height,
                                         const std::string& format, bool depth);
    ImportedTarget import_gl_texture(uint32_t texture, int64_t width, int64_t height,
                                     const std::string& format, bool depth);
    void render(const Scene& scene, const OffscreenTarget& target, const RenderOptions& options = {});
    void render(const Scene& scene, const ImportedTarget& target, const RenderOptions& options = {});
    // channels is 1, 3, or 4. Float textures hold linear values. pixels as for Texture::update().
    Texture create_texture(int64_t width, int64_t height, int channels, bool is_float,
                           const std::string& color_space, bool mipmaps,
                           const std::string& filter, const std::string& wrap,
                           const void* pixels, size_t bytes);
    HostTexture import_gl_input(uint32_t texture, int64_t width, int64_t height,
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
    void close();
    bool closed() const;
    uintptr_t shared_context() const;
    // "wgl", "glx", "egl", or "webgl".
    std::string gl_platform() const;
    Stats stats() const;
private:
    void submit(const Scene& scene, detail::TargetData& target, const RenderOptions& options);
    std::shared_ptr<detail::State> state_;
};

// Whether a material's texture is a HostTexture rather than a Texture.
bool is_host_texture(const std::shared_ptr<detail::TextureData>& data);

uintptr_t current_gl_context();
// For host adapters: an immutable GL_RGBA8 texture in the current context, which
// import_gl_texture() accepts, and its deletion. Pending host errors are discarded first.
uint32_t create_host_texture(int64_t width, int64_t height);
void delete_host_texture(uint32_t texture);

}
