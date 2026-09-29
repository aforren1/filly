#pragma once
// glTF preparation: the checks, tables, and source changes that gltfio cannot do itself.
// It reads the document with cgltf (the functions come from the SDK's gltfio_core library).

#include "renderer.h"
#include "vendor/cgltf/cgltf.h"

#include <array>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace filly::detail {

struct AssetTexture {
    std::vector<uint8_t> bytes;
    std::string mime;
    int uv = 0;
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
struct SurfaceSource {
    float anisotropy = 0, rotation = 0, iridescence = 0, ior = 1.3f, minimum = 100, maximum = 400;
    AssetTexture anisotropy_texture, iridescence_texture, thickness_texture;
};
enum class MaterialKind : uint8_t { standard, surface, diffuse };
struct MaterialSource {
    MaterialKind kind = MaterialKind::standard;
    // Index into PreparedAsset::surfaces or ::diffuse.
    size_t source = 0;
    // Instance name for provider-built materials: the glTF name, or material_<index>.
    std::string label;
    // The name that gltfio gives MaterialProvider::getMaterial(): the glTF name, or "material".
    std::string root_label;
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
    std::vector<PropertyTrackSource> tracks;
};

struct PreparedAsset {
    std::vector<NodeSource> nodes;
    std::vector<MaterialSource> materials;
    std::vector<DiffuseSource> diffuse;
    std::vector<SurfaceSource> surfaces;
    std::vector<AnimationSource> animations;
    std::vector<CameraSource> cameras;
    bool masked = false;
    bool custom_materials() const { return !diffuse.empty() || !surfaces.empty(); }
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
    bool strict = false, refraction = false, precompiled = false;
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
// path: absolute UTF-8 path of the document, or empty for bytes, which must be self-contained.
// Compatibility messages are appended to warnings; with strict, the first one throws.
Prepared prepare_asset(std::vector<uint8_t> bytes, const std::string& path, const PrepareOptions& options,
                       std::vector<std::string>& warnings);
// Applies the buffers and patches to gltfio's parse of prepared.bytes.
void patch_source(cgltf_data* data, const Prepared& prepared);
// Returns count * stride decoded bytes. mode: ATTRIBUTES, TRIANGLES, or INDICES; filter: NONE,
// OCTAHEDRAL, QUATERNION, EXPONENTIAL, or COLOR. Throws AssetError for invalid input.
std::vector<uint8_t> decode_meshopt(const uint8_t* source, size_t size, size_t count, size_t stride,
                                    const std::string& mode, const std::string& filter);
// The one-triangle GLB that gives a generated mesh its node and glTF material.
std::vector<uint8_t> mesh_placeholder(const std::array<float, 3>& low, const std::array<float, 3>& high,
                                      bool colors, const MeshMaterial& material);

}
