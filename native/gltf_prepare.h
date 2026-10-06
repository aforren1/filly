#pragma once
// glTF preparation: the checks, tables, and source changes that gltfio cannot do itself.
// It reads the document with cgltf (the functions come from the SDK's gltfio_core library).

#include "renderer.h"
#include "vendor/cgltf/cgltf.h"

#include <array>
#include <cfloat>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace filament { class Texture; }

namespace filly::detail {

struct AssetTexture {
    std::vector<uint8_t> bytes;
    std::string mime;
    int uv = 0;
    // Column-major KHR_texture_transform matrix for M * uv, the transpose of the matrix that
    // gltfio sets for its uv * M parameters.
    std::array<float, 9> transform = {1,0,0,0,1,0,0,0,1};
    int wrap_s = 10497, wrap_t = 10497;
    int min_filter = 9987, mag_filter = 9729;
};
struct DiffuseSource {
    float factor = 0;
    Vec3 color = {1,1,1};
    AssetTexture factor_texture, color_texture;
    float thickness = 0;
    Vec3 absorption = {};
    AssetTexture thickness_texture;
};
// Factors of KHR_materials_anisotropy and KHR_materials_iridescence. Their textures are in the
// material's ArchivePlan.
struct SurfaceSource {
    // The extensions present. The provider sets the factors of these only.
    bool has_anisotropy = false, has_iridescence = false;
    float anisotropy = 0, rotation = 0, iridescence = 0, ior = 1.3f, minimum = 100, maximum = 400;
};
// Material archive -------------------------------------------------------------------------------
// Entries of filly's material archive. The names match the uberz spec flags that materials.cmake
// writes.
// Refraction entries stay contiguous; preparation tests the range.
enum class ArchiveEntry : uint8_t {
    LitCore, LitExtended, LitSpecular, LitAnisotropy, RefractionThin, RefractionThinSpecular,
    RefractionSolid, RefractionSolidSpecular, RefractionThinAnisotropy, RefractionSolidAnisotropy,
    SpecularGlossiness, Unlit, DiffuseTransmission, count
};
// Extension texture roles in order of importance. When a material has more distinct extension
// textures than its entry has samplers, the last roles lose their textures first: detail maps
// (normals, roughness) before the maps that define where an effect exists or how strong it is.
enum class ExtensionRole : uint8_t {
    transmission, volumeThickness, anisotropy, iridescence, clearCoat, sheenColor, specularColor,
    specular, iridescenceThickness, clearCoatRoughness, sheenRoughness, clearCoatNormal, count
};
constexpr size_t extension_role_count = size_t(ExtensionRole::count);
struct ExtensionTexture {
    // Generic sampler of the entry, or -1 for no texture.
    int8_t slot = -1;
    // TEXCOORD_0 or TEXCOORD_1.
    uint8_t uv = 0;
    // Column-major KHR_texture_transform matrix for M * uv; see AssetTexture::transform.
    std::array<float, 9> transform = {1,0,0,0,1,0,0,0,1};
};
struct ExtensionSampler {
    // Index into PreparedAsset::textures.
    size_t texture = 0;
    bool srgb = false;
};
struct ArchivePlan {
    ArchiveEntry entry = ArchiveEntry::LitCore;
    std::array<ExtensionTexture, extension_role_count> roles;
    // Texture of each generic sampler; roles that use the same texture share one.
    std::vector<ExtensionSampler> slots;
    // gltfio sets it only with the clearcoat normal texture, which the provider binds instead.
    float clearcoat_normal_scale = 1;
};

enum class MaterialKind : uint8_t { standard, surface, diffuse };
struct MaterialSource {
    MaterialKind kind = MaterialKind::standard;
    // Index into PreparedAsset::surfaces or ::diffuse.
    size_t source = 0;
    // Instance name for provider-built materials: the glTF name, or material_<index>.
    std::string label;
    // The unscaled glTF emissiveFactor. gltfio 1.77.1 multiplies it by emissiveStrength and also
    // passes emissiveStrength to the shader, which squares the strength; this restores the factor.
    std::array<float, 3> emissive_factor{};
};
// Per glTF node. Names stay empty rather than taking gltfio's fallback of the mesh, light, or
// camera name.
struct NodeSource {
    std::optional<std::string> name, mesh;
    bool visible = true;
    // Authored morph weights, or zeros; empty for nodes without morph targets.
    std::vector<float> weights;
    size_t camera = SIZE_MAX, light = SIZE_MAX;
    // A point or spot light without an authored range, which gltfio gives a finite range.
    bool automatic_range = false;
};
struct CameraSource {
    bool orthographic = false;
    // Perspective: yfov, aspect (0 = render target aspect), znear, zfar. Orthographic: xmag, ymag, znear, zfar.
    Vec4 projection = {};
};
// kind: 0 material factor, 1 light, 2 camera, 3 texture transform, 4 spot cone, 5 visibility.
struct PropertyTrackSource {
    int kind = 0;
    size_t index = 0;
    std::string parameter;
    int components = 1, interpolation = 1;
    float minimum = 0, maximum = 1;
    std::vector<float> times, values;
    std::vector<float> setup;
};
struct AnimationSource {
    std::string name;
    // The gltfio animation that holds this clip's node channels, or -1 if it has none.
    int native_index = -1;
    // False when the clip writes every node channel that the rest animation writes, so the rest
    // animation need not run before it. Set by patch_source().
    bool needs_rest = true;
    std::vector<PropertyTrackSource> tracks;
};

struct PreparedAsset {
    std::vector<NodeSource> nodes;
    std::vector<MaterialSource> materials;
    std::vector<DiffuseSource> diffuse;
    std::vector<SurfaceSource> surfaces;
    std::vector<AnimationSource> animations;
    std::vector<CameraSource> cameras;
    // One plan per glTF material, and the glTF textures that extension roles use, by glTF
    // texture index (other entries stay empty).
    std::vector<ArchivePlan> plans;
    std::vector<AssetTexture> textures;
    // Model-space bounds of the rest pose; see compute_bounds. Minimum above maximum means no
    // geometry, which matches gltfio's empty box.
    std::array<Vec3, 2> bounds = {Vec3{FLT_MAX, FLT_MAX, FLT_MAX}, Vec3{-FLT_MAX, -FLT_MAX, -FLT_MAX}};
    // Rest-pose boxes of skinned mesh nodes in their own frames, by glTF node index, for the
    // renderables' culling boxes.
    std::vector<std::pair<size_t, std::array<Vec3, 2>>> skinned_boxes;
    // Per glTF node, for Node.bounds: the rest-pose box of its own mesh in the model's frame
    // (minimum above maximum without one), and its parent's index (UINT32_MAX at the top).
    std::vector<std::array<Vec3, 2>> node_boxes;
    std::vector<uint32_t> parents;
    // The gltfio animation that patch_source() appends, or -1: one constant keyframe pair for
    // each node channel that any clip animates, holding its authored value (see RestSource).
    int rest_animation = -1;
    // GPU bytes of the vertex and index buffers that gltfio uploads once per asset, of the
    // buffers that each instance adds, and the CPU bytes that each instance's animator copies.
    // See estimate_geometry().
    uint64_t geometry_bytes = 0, instance_geometry_bytes = 0, instance_cpu_bytes = 0;
    // Textures that the material provider decoded for custom materials, by source and color
    // space. Instances of the asset share them; the asset owns them.
    struct DecodedTexture { const AssetTexture* source; bool srgb; filament::Texture* texture; };
    mutable std::vector<DecodedTexture> decoded;
    bool masked = false;
};

// The accessors of the rest animation. gltfio's animator keeps a node's translation, rotation,
// and scale apart and writes back all three when a channel changes one of them, so a channel
// that one clip animates and the next clip does not would keep the first clip's value. Setting
// the TransformManager transform does not reach these values; only an animation does.
struct RestSource {
    std::vector<float> floats;
    cgltf_buffer buffer{};
    cgltf_buffer_view view{};
    // Fixed after patch_source(): the animation's samplers point into it.
    std::vector<cgltf_accessor> accessors;
};

// Buffer memory that cgltf reads in place. gltfio uses it until its source data is released.
struct BufferStore {
    struct Entry {
        uint8_t* data = nullptr;
        size_t size = 0;
        // False for a GLB binary chunk, which gltfio's own parse already holds.
        bool owned = false;
    };
    std::vector<Entry> buffers;
    std::vector<std::vector<uint8_t>> storage;
    std::unique_ptr<RestSource> rest;
};

// Changes to gltfio's parse of the document, by index, made before gltfio loads resources.
struct SourcePatches {
    // EXT_meshopt_compression views whose decoded bytes are in their own buffer.
    std::vector<size_t> decoded_views;
    // KHR_animation_pointer channels that target node properties, which gltfio animates.
    struct Channel { size_t animation, channel, node; cgltf_animation_path_type path; };
    std::vector<Channel> node_channels;
    // Samplers that only property channels use. gltfio validates every sampler of every
    // animation and disables all animation for one it cannot read, such as a sparse or integer
    // property curve, so these samplers read a valid float accessor instead.
    struct Sampler { size_t animation, sampler, accessor; };
    std::vector<Sampler> quiet_samplers;
    // Buffer-view images without a mimeType, which gltfio needs.
    std::vector<std::pair<size_t, std::string>> image_types;
};

struct PrepareOptions {
    bool strict = false, refraction = false;
};

struct Prepared {
    // The document for gltfio. It differs from the source only for EXT_mesh_gpu_instancing.
    std::vector<uint8_t> bytes;
    std::shared_ptr<PreparedAsset> tables;
    std::shared_ptr<BufferStore> buffers;
    SourcePatches patches;
};

// Reads a file through a wide path on Windows.
std::vector<uint8_t> read_file(const std::filesystem::path& file);
// Resolves a percent-encoded glTF URI against the folder of the UTF-8 document path.
// Throws AssetError for an invalid escape or a null byte.
std::filesystem::path resource_path(const std::string& path, const std::string& uri);
// path: absolute UTF-8 path of the document, or empty for bytes, which must be self-contained.
// Compatibility messages are appended to warnings; with strict, the first one throws.
Prepared prepare_asset(std::vector<uint8_t> bytes, const std::string& path, const PrepareOptions& options,
                       std::vector<std::string>& warnings);
// Applies the buffers and patches to gltfio's parse of prepared.bytes, and appends the rest
// animation (PreparedAsset::rest_animation).
void patch_source(cgltf_data* data, Prepared& prepared);
// Returns count * stride decoded bytes. mode: ATTRIBUTES, TRIANGLES, or INDICES; filter: NONE,
// OCTAHEDRAL, QUATERNION, EXPONENTIAL, or COLOR. Throws AssetError for invalid input.
std::vector<uint8_t> decode_meshopt(const uint8_t* source, size_t size, size_t count, size_t stride,
                                    const std::string& mode, const std::string& filter);
// The one-triangle GLB that gives a generated mesh its node and glTF material.
std::vector<uint8_t> mesh_placeholder(const std::array<float, 3>& low, const std::array<float, 3>& high,
                                      bool colors, const MeshMaterial& material);

}
