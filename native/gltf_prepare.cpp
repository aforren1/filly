#include "gltf_prepare.h"
#include "json.h"
#include "vendor/meshoptimizer/meshoptimizer.h"

#include <algorithm>
#include <cfloat>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <set>
#include <string_view>
#include <utility>

namespace filly::detail {
namespace {

constexpr double float_max = 3.402823466e38;
constexpr double pi = 3.141592653589793;

// Malformed documents: the message keeps the "Invalid glTF asset" prefix that callers match.
[[noreturn]] void invalid(const std::string& what) { throw AssetError("Invalid glTF asset: " + what); }

// Compatibility messages: warnings, or errors for strict loads and required extensions.
struct Issues {
    const PrepareOptions& options;
    std::vector<std::string>& warnings;
    void operator()(const std::string& message, bool required = false) const {
        if (options.strict || required) throw AssetError(message);
        warnings.push_back(message);
    }
};

// Python's repr() of a string, which the messages have always used for material names.
std::string repr(const std::string& text) {
    const char quote = text.find('\'') != std::string::npos && text.find('"') == std::string::npos ? '"' : '\'';
    std::string out(1, quote);
    for (const char c : text) {
        if (c == '\\') out += "\\\\";
        else if (c == quote) { out += '\\'; out += c; }
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else out += c;
    }
    return out + quote;
}

// cgltf keeps JSON escapes in strings.
std::optional<std::string> text(const char* value) {
    if (!value) return std::nullopt;
    std::string copy(value);
    copy.resize(cgltf_decode_string(copy.data()));
    return copy;
}

bool finite_float(double value) { return std::isfinite(value) && std::abs(value) <= float_max; }

const char* extension_text(const cgltf_extension* extensions, size_t count, std::string_view name) {
    for (size_t i = 0; i < count; ++i)
        if (extensions[i].name && name == extensions[i].name) return extensions[i].data ? extensions[i].data : "{}";
    return nullptr;
}
json::Value extension_value(const char* data) {
    try {
        return json::parse(data);
    } catch (const std::invalid_argument& error) {
        invalid(error.what());
    }
}

struct Document {
    cgltf_data* data = nullptr;
    Document() = default;
    Document(const Document&) = delete;
    Document& operator=(const Document&) = delete;
    ~Document() { if (data) cgltf_free(data); }
};

const char* parse_error(cgltf_result result) {
    switch (result) {
        case cgltf_result_data_too_short: return "GLB data is shorter than its header states";
        case cgltf_result_unknown_format: return "not a glTF 2.0 JSON or GLB file";
        case cgltf_result_invalid_json: return "malformed JSON";
        case cgltf_result_invalid_gltf: return "invalid glTF structure or index";
        case cgltf_result_legacy_gltf: return "glTF 1.0 is not supported";
        case cgltf_result_out_of_memory: return "out of memory";
        default: return "could not parse the document";
    }
}

void parse(Document& document, const std::vector<uint8_t>& bytes) {
    if (document.data) cgltf_free(std::exchange(document.data, nullptr));
    cgltf_options options{};
    const auto result = cgltf_parse(&options, bytes.data(), bytes.size(), &document.data);
    if (result != cgltf_result_success) {
        document.data = nullptr;
        invalid(parse_error(result));
    }
}

// Resources ---------------------------------------------------------------------------------

std::vector<uint8_t> base64(std::string_view text) {
    auto value = [](char c) -> int {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    };
    while (!text.empty() && text.back() == '=') text.remove_suffix(1);
    std::vector<uint8_t> out;
    out.reserve(text.size() * 3 / 4);
    uint32_t bits = 0;
    int count = 0;
    for (const char c : text) {
        const int v = value(c);
        if (v < 0) invalid("Invalid base64 data URI");
        bits = (bits << 6) | uint32_t(v);
        if ((count += 6) >= 8) {
            count -= 8;
            out.push_back(uint8_t(bits >> count));
        }
    }
    return out;
}

std::vector<uint8_t> data_uri(const std::string& uri) {
    const auto comma = uri.find(',');
    if (comma == std::string::npos) invalid("Data URI has no comma");
    const auto header = std::string_view(uri).substr(0, comma);
    const auto payload = std::string_view(uri).substr(comma + 1);
    if (header.find(";base64") != std::string_view::npos) return base64(payload);
    std::vector<uint8_t> out;
    for (size_t i = 0; i < payload.size(); ++i) {
        if (payload[i] == '%' && i + 2 < payload.size()) {
            unsigned value = 0;
            const auto result = std::from_chars(payload.data() + i + 1, payload.data() + i + 3, value, 16);
            if (result.ec == std::errc() && result.ptr == payload.data() + i + 3) {
                out.push_back(uint8_t(value));
                i += 2;
                continue;
            }
        }
        out.push_back(uint8_t(payload[i]));
    }
    return out;
}

// The bytes of a buffer or image URI.
std::vector<uint8_t> uri_bytes(const std::string& uri, const std::string& path) {
    if (uri.starts_with("data:")) return data_uri(uri);
    if (path.empty()) throw AssetError("Byte assets must contain all resources");
    const auto file = resource_path(path, uri);
    std::error_code error;
    if (!std::filesystem::is_regular_file(file, error)) throw AssetError("Missing glTF resource: " + uri);
    try {
        return read_file(file);
    } catch (const AssetError&) {
        throw AssetError("Could not read glTF resource: " + uri);
    }
}

// Accessors ---------------------------------------------------------------------------------

// Mirrors gltfio's Animator validation, which rejects every animation if one sampler fails it.
bool gltfio_reads(const cgltf_accessor* accessor) {
    const cgltf_buffer_view* view = accessor ? accessor->buffer_view : nullptr;
    if (!view || accessor->is_sparse || accessor->count == 0) return false;
    const size_t element = cgltf_calc_size(accessor->type, accessor->component_type);
    const size_t stride = accessor->stride ? accessor->stride : element;
    if (element == 0 || stride == 0 || accessor->offset > view->size) return false;
    const size_t available = view->size - accessor->offset;
    return element <= available && accessor->count <= (available - element) / stride + 1;
}

size_t component_size(cgltf_component_type type) {
    switch (type) {
        case cgltf_component_type_r_8u: return 1;
        case cgltf_component_type_r_16u: return 2;
        case cgltf_component_type_r_32u: return 4;
        default: return 0;
    }
}

// Reads an animation or instance accessor as floats, with sparse values and normalization.
std::vector<float> read_floats(const cgltf_accessor* accessor, int components, bool times = false) {
    static const cgltf_type types[] = {cgltf_type_invalid, cgltf_type_scalar, cgltf_type_vec2, cgltf_type_vec3, cgltf_type_vec4};
    if (accessor->type != types[components]) invalid("Animation accessor type does not match its property");
    if (accessor->count == 0 || accessor->count > 10'000'000) invalid("Invalid animation accessor count");
    const auto kind = accessor->component_type;
    if (kind == cgltf_component_type_invalid || kind == cgltf_component_type_max_enum)
        invalid("Invalid animation component type");
    if (times && (kind != cgltf_component_type_r_32f || accessor->normalized))
        invalid("Animation input must use floating-point seconds");
    if (accessor->normalized && (kind == cgltf_component_type_r_32u || kind == cgltf_component_type_r_32f))
        invalid("Invalid normalized animation component type");
    if (accessor->is_sparse) {
        const auto& sparse = accessor->sparse;
        const size_t size = component_size(sparse.indices_component_type);
        if (!sparse.count || sparse.count > accessor->count || !size) invalid("Invalid sparse animation accessor");
        const auto* view = sparse.indices_buffer_view;
        const auto* bytes = static_cast<const uint8_t*>(cgltf_buffer_view_data(view));
        if (!bytes || sparse.indices_byte_offset + sparse.count * size > view->size)
            invalid("Animation accessor extends past its buffer view");
        bytes += sparse.indices_byte_offset;
        int64_t previous = -1;
        for (size_t i = 0; i < sparse.count; ++i) {
            uint32_t index = 0;
            if (size == 1) index = bytes[i];
            else if (size == 2) { uint16_t v; std::memcpy(&v, bytes + 2*i, 2); index = v; }
            else std::memcpy(&index, bytes + 4*i, 4);
            if (int64_t(index) <= previous || index >= accessor->count)
                invalid("Sparse animation indices must increase within the accessor");
            previous = index;
        }
    }
    std::vector<float> values(accessor->count * size_t(components));
    if (cgltf_accessor_unpack_floats(accessor, values.data(), values.size()) != values.size())
        invalid("Animation accessor has no buffer data");
    for (auto& value : values) {
        if (accessor->normalized) value = std::max(value, -1.0f);
        if (!std::isfinite(value)) invalid("Animation values must be finite float32 values");
    }
    return values;
}

// Extension and material checks -------------------------------------------------------------

const std::set<std::string, std::less<>> supported = {
    "KHR_lights_punctual", "KHR_materials_unlit", "KHR_materials_clearcoat",
    "KHR_materials_sheen", "KHR_materials_transmission", "KHR_materials_volume",
    "KHR_materials_ior", "KHR_materials_specular", "KHR_materials_emissive_strength",
    "KHR_materials_pbrSpecularGlossiness", "KHR_materials_variants",
    "KHR_materials_dispersion", "KHR_materials_diffuse_transmission",
    "KHR_texture_transform", "KHR_texture_basisu", "EXT_texture_webp",
    "KHR_mesh_quantization", "KHR_draco_mesh_compression", "EXT_meshopt_compression",
    "KHR_animation_pointer", "KHR_materials_anisotropy", "KHR_materials_iridescence",
    "KHR_meshopt_compression", "EXT_mesh_gpu_instancing", "KHR_node_visibility",
};
// Metadata only: these cannot change the rendered image.
const std::set<std::string, std::less<>> ignored = {"KHR_xmp", "KHR_xmp_json_ld"};

std::string material_name(const cgltf_material& material, size_t index, const char* separator) {
    if (auto name = text(material.name)) return *name;
    return std::string("material") + separator + std::to_string(index);
}

// Buffers and meshopt -----------------------------------------------------------------------

struct Loader {
    cgltf_data* data;
    const std::string& path;
    std::shared_ptr<BufferStore> store = std::make_shared<BufferStore>();
    // Parsed only for documents with instancing, which cgltf reads incompletely.
    std::optional<json::Value> json;

    const json::Value& document() {
        if (!json) {
            try {
                json = json::parse(std::string_view(data->json, data->json_size));
            } catch (const std::invalid_argument& error) {
                invalid(error.what());
            }
        }
        return *json;
    }
    // Moving a vector keeps its data pointer, so entries stay valid as storage grows.
    uint8_t* own(std::vector<uint8_t>&& bytes) {
        if (bytes.empty()) bytes.resize(1);
        store->storage.push_back(std::move(bytes));
        return store->storage.back().data();
    }
};

void attach(cgltf_data* data, const BufferStore& store) {
    for (size_t i = 0; i < data->buffers_count && i < store.buffers.size(); ++i) {
        const auto& entry = store.buffers[i];
        if (entry.owned) data->buffers[i].data = entry.data;
        else if (i == 0 && data->bin) data->buffers[i].data = const_cast<void*>(data->bin);
        else continue;
        data->buffers[i].data_free_method = cgltf_data_free_method_none;
    }
}

struct MeshoptView {
    size_t view;
    bool khr;
    size_t buffer, start, size, count, stride;
    std::string mode, filter;
};

size_t meshopt_field(const json::Value& ext, const char* key, bool optional = false) {
    const auto* value = ext.find(key);
    if (!value) {
        if (optional) return 0;
        invalid(std::string("Meshopt extension lacks ") + key);
    }
    if (!value->is_index()) invalid("Invalid meshopt buffer range");
    return size_t(value->number);
}

std::vector<MeshoptView> meshopt_views(Loader& loader) {
    auto* data = loader.data;
    static const char* names[] = {"EXT_meshopt_compression", "KHR_meshopt_compression"};
    for (size_t i = 0; i < data->buffers_count; ++i) {
        const auto& buffer = data->buffers[i];
        if (extension_text(buffer.extensions, buffer.extensions_count, names[0])
                && extension_text(buffer.extensions, buffer.extensions_count, names[1]))
            invalid("A buffer cannot use both meshopt extensions");
    }
    std::vector<MeshoptView> result;
    // "filter" keys that cgltf or the KHR parse account for, and the EXT views that cgltf reads
    // as NONE.
    size_t known_filters = 0;
    std::vector<size_t> unfiltered;
    for (size_t i = 0; i < data->buffer_views_count; ++i) {
        const auto& view = data->buffer_views[i];
        const char* khr = extension_text(view.extensions, view.extensions_count, names[1]);
        if (view.has_meshopt_compression && khr) invalid("A buffer view cannot use both meshopt extensions");
        if (view.has_meshopt_compression) {
            static const char* modes[] = {"", "ATTRIBUTES", "TRIANGLES", "INDICES"};
            static const char* filters[] = {"NONE", "OCTAHEDRAL", "QUATERNION", "EXPONENTIAL"};
            const auto& mc = view.meshopt_compression;
            if (mc.mode == cgltf_meshopt_compression_mode_invalid || mc.mode >= std::size(modes)
                    || mc.filter >= std::size(filters))
                invalid("Invalid meshopt mode or filter");
            if (mc.filter == cgltf_meshopt_compression_filter_none) unfiltered.push_back(i);
            else ++known_filters;
            result.push_back({i, false, cgltf_buffer_index(data, mc.buffer), mc.offset, mc.size, mc.count, mc.stride,
                              modes[mc.mode], filters[mc.filter]});
        } else if (khr) {
            // cgltf does not read KHR_meshopt_compression, so only its own object is parsed.
            const json::Value ext = extension_value(khr);
            const auto* mode = ext.find("mode");
            const auto* filter = ext.find("filter");
            if (!mode || !mode->is_string() || (filter && !filter->is_string())) invalid("Invalid meshopt mode or filter");
            if (filter) ++known_filters;
            result.push_back({i, true, meshopt_field(ext, "buffer"), meshopt_field(ext, "byteOffset", true),
                              meshopt_field(ext, "byteLength"), meshopt_field(ext, "count"),
                              meshopt_field(ext, "byteStride"), mode->text, filter ? filter->text : "NONE"});
        }
    }
    // cgltf reads an unknown EXT filter, such as COLOR, as NONE, and does not keep the extension
    // object. The document is parsed only if it has more "filter" keys than the known ones.
    if (!unfiltered.empty()) {
        const std::string_view text(data->json, data->json_size);
        size_t keys = 0;
        for (size_t at = text.find("\"filter\""); at != text.npos; at = text.find("\"filter\"", at + 8)) ++keys;
        const auto* views = keys > known_filters ? loader.document().find("bufferViews") : nullptr;
        for (const size_t i : unfiltered) {
            if (!views || !views->is_array() || i >= views->items.size()) break;
            const auto* extensions = views->items[i].find("extensions");
            const auto* ext = extensions ? extensions->find(names[0]) : nullptr;
            const auto* filter = ext ? ext->find("filter") : nullptr;
            if (!filter || (filter->is_string() && filter->text == "NONE")) continue;
            if (filter->is_string() && filter->text == "COLOR")
                invalid("COLOR filtering requires KHR_meshopt_compression");
            invalid("Invalid meshopt mode or filter");
        }
    }
    return result;
}

// Loads every buffer, and decodes meshopt views into the buffers that they reference. A buffer
// that only compressed views use is allocated, not read, as its content is the decoded data.
void load_buffers(Loader& loader, SourcePatches& patches) {
    auto* data = loader.data;
    const auto views = meshopt_views(loader);
    std::vector<bool> content(data->buffers_count, false), decoded(data->buffers_count, false);
    std::vector<bool> compressed(data->buffer_views_count, false);
    // A buffer that only decoded views use needs memory up to their end, not its byteLength.
    std::vector<size_t> decoded_end(data->buffers_count, 0);
    for (const auto& item : views) {
        compressed[item.view] = true;
        const size_t source = item.buffer;
        if (source >= data->buffers_count) invalid("Meshopt decoded length does not match its buffer view");
        content[source] = true;
        const auto& view = data->buffer_views[item.view];
        const size_t target = cgltf_buffer_index(data, view.buffer);
        decoded[target] = true;
        if (view.offset + view.size > view.buffer->size) invalid("Meshopt decoded data exceeds its fallback buffer");
        decoded_end[target] = std::max(decoded_end[target], view.offset + view.size);
    }
    for (size_t i = 0; i < data->buffer_views_count; ++i)
        if (!compressed[i]) content[cgltf_buffer_index(data, data->buffer_views[i].buffer)] = true;
    auto& store = *loader.store;
    store.buffers.resize(data->buffers_count);
    for (size_t i = 0; i < data->buffers_count; ++i) {
        auto& buffer = data->buffers[i];
        auto& entry = store.buffers[i];
        if (decoded[i] && !content[i]) {
            entry = {loader.own(std::vector<uint8_t>(decoded_end[i])), decoded_end[i], true};
            continue;
        }
        if (buffer.uri) {
            auto bytes = uri_bytes(buffer.uri, loader.path);
            if (bytes.size() < buffer.size) invalid("Buffer is shorter than its byteLength");
            const size_t size = bytes.size();
            entry = {loader.own(std::move(bytes)), size, true};
        } else if (i == 0 && data->bin) {
            if (data->bin_size < buffer.size) invalid("Buffer is shorter than its byteLength");
            entry = {static_cast<uint8_t*>(const_cast<void*>(data->bin)), data->bin_size, false};
            // Decoded views write into this buffer, so it needs its own copy.
            if (decoded[i]) {
                std::vector<uint8_t> copy(entry.data, entry.data + entry.size);
                entry = {loader.own(std::move(copy)), data->bin_size, true};
            }
        } else {
            invalid("Only the first buffer of a GLB may omit its URI");
        }
    }
    attach(data, store);
    // Decode everything before writing, since a view may decode into its own source buffer.
    std::vector<std::vector<uint8_t>> results;
    for (const auto& item : views) {
        const size_t count = item.count, stride = item.stride, source = item.buffer, start = item.start, size = item.size;
        const auto& view = data->buffer_views[item.view];
        if (count * stride != view.size) invalid("Meshopt decoded length does not match its buffer view");
        if (view.stride && view.stride != stride) invalid("Meshopt stride does not match its buffer view");
        const auto& input = store.buffers[source];
        if (start + size > std::min(input.size, data->buffers[source].size))
            invalid("Meshopt range exceeds its source buffer");
        const uint8_t* payload = input.data + start;
        if (!item.khr && item.mode == "ATTRIBUTES" && size && payload[0] == 0xa1)
            invalid("Vertex version 1 requires KHR_meshopt_compression");
        results.push_back(decode_meshopt(payload, size, count, stride, item.mode, item.filter));
    }
    for (size_t i = 0; i < views.size(); ++i) {
        auto& view = data->buffer_views[views[i].view];
        const auto& target = store.buffers[cgltf_buffer_index(data, view.buffer)];
        if (view.offset + results[i].size() > std::min(target.size, view.buffer->size))
            invalid("Meshopt decoded data exceeds its fallback buffer");
        std::memcpy(target.data + view.offset, results[i].data(), results[i].size());
        if (view.has_meshopt_compression) {
            view.has_meshopt_compression = false;
            patches.decoded_views.push_back(views[i].view);
        }
    }
}

// EXT_mesh_gpu_instancing -------------------------------------------------------------------

// gltfio renders one mesh per node, so each instance becomes a child node that shares the mesh.
// Returns the rewritten document, or nothing when no node uses the extension.
std::optional<std::vector<uint8_t>> expand_instances(Loader& loader) {
    auto* data = loader.data;
    bool any = false;
    for (size_t i = 0; i < data->nodes_count; ++i) any = any || data->nodes[i].has_mesh_gpu_instancing;
    if (!any) return std::nullopt;
    json::Value doc = loader.document();
    auto& nodes = doc.at("nodes");
    const size_t original = nodes.items.size();
    std::vector<std::vector<size_t>> expanded(original);
    static const char* extension = "EXT_mesh_gpu_instancing";
    for (size_t index = 0; index < original; ++index) {
        auto* extensions = nodes.items[index].find("extensions");
        const auto* found = extensions ? extensions->find(extension) : nullptr;
        if (!found) continue;
        const json::Value ext = *found;
        extensions->erase(extension);
        const auto* attributes = ext.find("attributes");
        if (!nodes.items[index].find("mesh") || !attributes || !attributes->is_object() || attributes->items.empty())
            invalid("Instancing requires a mesh and at least one attribute");
        std::vector<std::pair<std::string, std::vector<float>>> values;
        std::optional<size_t> count;
        for (size_t a = 0; a < attributes->items.size(); ++a) {
            const auto& name = attributes->keys[a];
            const auto& reference = attributes->items[a];
            if (!reference.is_index() || reference.number >= double(data->accessors_count))
                invalid("Invalid instance accessor index");
            const auto* accessor = &data->accessors[size_t(reference.number)];
            if (count && *count != accessor->count) invalid("Instance attribute counts must match");
            count = accessor->count;
            if (name.starts_with("_")) continue;
            if (name != "TRANSLATION" && name != "ROTATION" && name != "SCALE") invalid("Unknown instance attribute");
            const auto kind = accessor->component_type;
            if (name == "ROTATION") {
                if (!(kind == cgltf_component_type_r_32f || ((kind == cgltf_component_type_r_8 || kind == cgltf_component_type_r_16)
                        && accessor->normalized)))
                    invalid("Instance rotation requires floats or normalized signed integers");
            } else if (kind != cgltf_component_type_r_32f || accessor->normalized) {
                invalid("Instance translation and scale require floats");
            }
            values.emplace_back(name, read_floats(accessor, name == "ROTATION" ? 4 : 3));
        }
        if (!count || *count == 0 || *count > 100'000) invalid("Invalid instance count");
        auto& node = nodes.items[index];
        const json::Value mesh = *node.find("mesh");
        node.erase("mesh");
        const json::Value* skin = node.find("skin");
        const json::Value* weights = node.find("weights");
        std::vector<json::Value> children;
        for (size_t instance = 0; instance < *count; ++instance) {
            // Unnamed, as in the source document; reach it through the parent's children.
            json::Value child = json::object();
            child.at("mesh") = mesh;
            if (skin) child.at("skin") = *skin;
            if (weights) child.at("weights") = *weights;
            for (const auto& [name, rows] : values) {
                const size_t n = name == "ROTATION" ? 4 : 3;
                std::array<double, 4> row{};
                std::copy_n(rows.begin() + ptrdiff_t(instance * n), n, row.begin());
                if (name == "ROTATION") {
                    const double norm = std::sqrt(row[0]*row[0] + row[1]*row[1] + row[2]*row[2] + row[3]*row[3]);
                    if (std::abs(norm - 1) > 0.01) invalid("Instance rotation must be a unit quaternion");
                    for (auto& value : row) value /= norm;
                }
                json::Value list = json::array();
                for (size_t c = 0; c < n; ++c) list.items.push_back(json::number(row[c]));
                std::string key = name;
                std::transform(key.begin(), key.end(), key.begin(), [](char c) { return char(std::tolower(c)); });
                child.at(key) = std::move(list);
            }
            children.push_back(std::move(child));
        }
        auto& list = node.at("children");
        if (!list.is_array()) list = json::array();
        for (size_t k = 0; k < children.size(); ++k) {
            expanded[index].push_back(nodes.items.size() + k);
            list.items.push_back(json::number(double(nodes.items.size() + k)));
        }
        node.erase("skin");
        node.erase("weights");
        // Appending invalidates node and list.
        for (auto& child : children) nodes.items.push_back(std::move(child));
    }
    // Morph animation belongs to each expanded mesh; TRS animation remains on the parent.
    if (auto* animations = doc.find("animations"); animations && animations->is_array()) {
        for (auto& animation : animations->items) {
            auto* channels = animation.find("channels");
            if (!channels || !channels->is_array()) continue;
            std::vector<json::Value> result;
            for (auto& channel : channels->items) {
                const auto* target = channel.find("target");
                std::optional<size_t> source;
                if (target) {
                    const auto* path = target->find("path");
                    const auto* node = target->find("node");
                    if (path && path->is_string() && path->text == "weights" && node && node->is_index())
                        source = size_t(node->number);
                    const auto* extensions = target->find("extensions");
                    const auto* pointer_ext = extensions ? extensions->find("KHR_animation_pointer") : nullptr;
                    const auto* pointer = pointer_ext ? pointer_ext->find("pointer") : nullptr;
                    if (pointer && pointer->is_string() && pointer->text.starts_with("/nodes/") && pointer->text.ends_with("/weights")) {
                        auto digits = std::string_view(pointer->text).substr(7);
                        digits = digits.substr(0, digits.find('/'));
                        size_t value = 0;
                        const auto parsed = std::from_chars(digits.data(), digits.data() + digits.size(), value);
                        if (parsed.ec != std::errc() || parsed.ptr != digits.data() + digits.size()) invalid("Invalid node pointer");
                        source = value;
                    }
                }
                if (source && *source < original && !expanded[*source].empty()) {
                    for (const auto child : expanded[*source]) {
                        json::Value replacement = channel;
                        json::Value retarget = json::object();
                        retarget.at("node") = json::number(double(child));
                        retarget.at("path") = json::string("weights");
                        replacement.at("target") = std::move(retarget);
                        result.push_back(std::move(replacement));
                    }
                } else {
                    result.push_back(std::move(channel));
                }
            }
            channels->items = std::move(result);
        }
    }
    for (const char* key : {"extensionsUsed", "extensionsRequired"}) {
        auto* list = doc.find(key);
        if (!list || !list->is_array()) continue;
        std::erase_if(list->items, [](const json::Value& v) { return v.is_string() && v.text == extension; });
    }
    std::string encoded = json::write(doc);
    std::vector<uint8_t> bytes;
    if (data->file_type != cgltf_file_type_glb) return std::vector<uint8_t>(encoded.begin(), encoded.end());
    encoded.append((4 - encoded.size() % 4) % 4, ' ');
    auto put = [&](uint32_t value) {
        for (int i = 0; i < 4; ++i) bytes.push_back(uint8_t(value >> (8 * i)));
    };
    const size_t bin = data->bin ? data->bin_size : 0;
    const size_t total = 12 + 8 + encoded.size() + (data->bin ? 8 + bin : 0);
    if (total > std::numeric_limits<uint32_t>::max()) invalid("Expanded GLB exceeds 4 GiB");
    bytes.reserve(total);
    put(0x46546C67); put(2); put(uint32_t(total));
    put(uint32_t(encoded.size())); put(0x4E4F534A);
    bytes.insert(bytes.end(), encoded.begin(), encoded.end());
    if (data->bin) {
        put(uint32_t(bin)); put(0x004E4942);
        const auto* first = static_cast<const uint8_t*>(data->bin);
        bytes.insert(bytes.end(), first, first + bin);
    }
    return bytes;
}

// Textures of provider-built materials -------------------------------------------------------

std::string sniff(const std::vector<uint8_t>& bytes) {
    static const uint8_t ktx2[] = {0xAB, 'K', 'T', 'X', ' ', '2', '0'};
    if (bytes.size() >= 7 && std::equal(ktx2, ktx2 + 7, bytes.begin())) return "image/ktx2";
    if (bytes.size() >= 12 && !std::memcmp(bytes.data(), "RIFF", 4) && !std::memcmp(bytes.data() + 8, "WEBP", 4))
        return "image/webp";
    if (bytes.size() >= 3 && bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF) return "image/jpeg";
    return "image/png";
}

std::vector<uint8_t> image_bytes(const cgltf_image& image, const std::string& path) {
    if (image.uri) return uri_bytes(image.uri, path);
    const auto* view = image.buffer_view;
    const auto* bytes = view ? static_cast<const uint8_t*>(cgltf_buffer_view_data(view)) : nullptr;
    if (!bytes) invalid("Image has no data");
    return std::vector<uint8_t>(bytes, bytes + view->size);
}

// With read false, the result has the UV set, transform, and sampler, but no image bytes.
AssetTexture texture_info(const cgltf_texture_view& info, const std::string& path, bool read = true) {
    AssetTexture result;
    if (!info.texture) return result;
    const auto& texture = *info.texture;
    const cgltf_image* image = texture.has_basisu && texture.basisu_image ? texture.basisu_image
        : texture.has_webp && texture.webp_image ? texture.webp_image : texture.image;
    if (!image) invalid("Texture has no source image");
    const auto& transform = info.transform;
    result.uv = info.has_transform && transform.has_texcoord ? transform.texcoord : info.texcoord;
    if (result.uv != 0 && result.uv != 1) throw AssetError("Extended materials support TEXCOORD_0 and TEXCOORD_1");
    const double angle = info.has_transform ? transform.rotation : 0;
    const double sx = info.has_transform ? transform.scale[0] : 1, sy = info.has_transform ? transform.scale[1] : 1;
    const double ox = info.has_transform ? transform.offset[0] : 0, oy = info.has_transform ? transform.offset[1] : 0;
    for (double value : {angle, sx, sy, ox, oy}) if (!finite_float(value)) invalid("Invalid texture transform");
    const double c = std::cos(angle), s = std::sin(angle);
    // The transpose of gltfio's matrixFromUvTransform(), for M * uv: a quarter turn maps (u, v) to
    // (v, -u), which renders TextureTransformMultiTest correctly.
    result.transform = {float(sx*c), float(-sy*s), 0, float(sx*s), float(sy*c), 0, float(ox), float(oy), 1};
    if (read) {
        result.bytes = image_bytes(*image, path);
        result.mime = image->mime_type ? image->mime_type : sniff(result.bytes);
    }
    if (texture.sampler) {
        const auto& sampler = *texture.sampler;
        result.wrap_s = sampler.wrap_s;
        result.wrap_t = sampler.wrap_t;
        if (sampler.min_filter) result.min_filter = sampler.min_filter;
        if (sampler.mag_filter) result.mag_filter = sampler.mag_filter;
    }
    auto wrap = [](int mode) { return mode == 10497 || mode == 33071 || mode == 33648; };
    if (!wrap(result.wrap_s) || !wrap(result.wrap_t)) invalid("Invalid texture wrap mode");
    const int min = result.min_filter, mag = result.mag_filter;
    if (!(min == 9728 || min == 9729 || (min >= 9984 && min <= 9987)) || !(mag == 9728 || mag == 9729))
        invalid("Invalid texture filter");
    return result;
}

// Animation ---------------------------------------------------------------------------------

struct MaterialProperty {
    const char* path;
    const char* parameter;
    int components;
    double lower, upper;
    // Writes the authored or default value; false when the property's parent object is absent,
    // which makes the pointer invalid.
    bool (*rest)(const cgltf_material&, float*);
};
template <size_t N> bool copy(const float (&values)[N], float* out) { std::copy_n(values, N, out); return true; }
bool one(float value, float* out) { *out = value; return true; }
constexpr double inf = std::numeric_limits<double>::infinity();

const MaterialProperty material_properties[] = {
    {"pbrMetallicRoughness/baseColorFactor", "baseColorFactor", 4, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_pbr_metallic_roughness && copy(m.pbr_metallic_roughness.base_color_factor, o); }},
    {"pbrMetallicRoughness/metallicFactor", "metallicFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_pbr_metallic_roughness && one(m.pbr_metallic_roughness.metallic_factor, o); }},
    {"pbrMetallicRoughness/roughnessFactor", "roughnessFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_pbr_metallic_roughness && one(m.pbr_metallic_roughness.roughness_factor, o); }},
    {"emissiveFactor", "emissiveFactor", 3, 0, 1,
        [](const cgltf_material& m, float* o) { return copy(m.emissive_factor, o); }},
    {"normalTexture/scale", "normalScale", 1, -inf, inf,
        [](const cgltf_material& m, float* o) { return m.normal_texture.texture && one(m.normal_texture.scale, o); }},
    {"occlusionTexture/strength", "aoStrength", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.occlusion_texture.texture && one(m.occlusion_texture.scale, o); }},
    {"extensions/KHR_materials_emissive_strength/emissiveStrength", "emissiveStrength", 1, 0, inf,
        [](const cgltf_material& m, float* o) { return m.has_emissive_strength && one(m.emissive_strength.emissive_strength, o); }},
    {"extensions/KHR_materials_transmission/transmissionFactor", "transmissionFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_transmission && one(m.transmission.transmission_factor, o); }},
    {"extensions/KHR_materials_clearcoat/clearcoatFactor", "clearCoatFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_clearcoat && one(m.clearcoat.clearcoat_factor, o); }},
    {"extensions/KHR_materials_clearcoat/clearcoatRoughnessFactor", "clearCoatRoughnessFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_clearcoat && one(m.clearcoat.clearcoat_roughness_factor, o); }},
    {"extensions/KHR_materials_clearcoat/clearcoatNormalTexture/scale", "clearCoatNormalScale", 1, -inf, inf,
        [](const cgltf_material& m, float* o) { return m.has_clearcoat && m.clearcoat.clearcoat_normal_texture.texture && one(m.clearcoat.clearcoat_normal_texture.scale, o); }},
    {"extensions/KHR_materials_ior/ior", "ior", 1, 1, inf,
        [](const cgltf_material& m, float* o) { return m.has_ior && one(m.ior.ior, o); }},
    {"extensions/KHR_materials_sheen/sheenColorFactor", "sheenColorFactor", 3, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_sheen && copy(m.sheen.sheen_color_factor, o); }},
    {"extensions/KHR_materials_sheen/sheenRoughnessFactor", "sheenRoughnessFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_sheen && one(m.sheen.sheen_roughness_factor, o); }},
    {"extensions/KHR_materials_specular/specularFactor", "specularStrength", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_specular && one(m.specular.specular_factor, o); }},
    {"extensions/KHR_materials_specular/specularColorFactor", "specularColorFactor", 3, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_specular && copy(m.specular.specular_color_factor, o); }},
    {"extensions/KHR_materials_volume/thicknessFactor", "volumeThicknessFactor", 1, 0, inf,
        [](const cgltf_material& m, float* o) { return m.has_volume && one(m.volume.thickness_factor, o); }},
    {"extensions/KHR_materials_dispersion/dispersion", "dispersion", 1, 0, inf,
        [](const cgltf_material& m, float* o) { return m.has_dispersion && one(m.dispersion.dispersion, o); }},
    {"extensions/KHR_materials_diffuse_transmission/diffuseTransmissionFactor", "diffuseFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_diffuse_transmission && one(m.diffuse_transmission.diffuse_transmission_factor, o); }},
    {"extensions/KHR_materials_diffuse_transmission/diffuseTransmissionColorFactor", "diffuseColor", 3, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_diffuse_transmission && copy(m.diffuse_transmission.diffuse_transmission_color_factor, o); }},
    {"extensions/KHR_materials_anisotropy/anisotropyStrength", "anisotropyStrength", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_anisotropy && one(m.anisotropy.anisotropy_strength, o); }},
    {"extensions/KHR_materials_anisotropy/anisotropyRotation", "anisotropyRotation", 1, -inf, inf,
        [](const cgltf_material& m, float* o) { return m.has_anisotropy && one(m.anisotropy.anisotropy_rotation, o); }},
    {"extensions/KHR_materials_iridescence/iridescenceFactor", "iridescenceFactor", 1, 0, 1,
        [](const cgltf_material& m, float* o) { return m.has_iridescence && one(m.iridescence.iridescence_factor, o); }},
    {"extensions/KHR_materials_iridescence/iridescenceIor", "iridescenceIor", 1, 1, inf,
        [](const cgltf_material& m, float* o) { return m.has_iridescence && one(m.iridescence.iridescence_ior, o); }},
    {"extensions/KHR_materials_iridescence/iridescenceThicknessMinimum", "iridescenceThicknessMinimum", 1, 0, inf,
        [](const cgltf_material& m, float* o) { return m.has_iridescence && one(m.iridescence.iridescence_thickness_min, o); }},
    {"extensions/KHR_materials_iridescence/iridescenceThicknessMaximum", "iridescenceThicknessMaximum", 1, 0, inf,
        [](const cgltf_material& m, float* o) { return m.has_iridescence && one(m.iridescence.iridescence_thickness_max, o); }},
};

struct UvProperty {
    const char* path;
    const char* matrix;
    // Null when the texture reference or its parent object is absent.
    const cgltf_texture_view* (*view)(const cgltf_material&);
};
template <class T> const cgltf_texture_view* when(bool present, const T& view) { return present && view.texture ? &view : nullptr; }
const UvProperty uv_properties[] = {
    {"pbrMetallicRoughness/baseColorTexture", "baseColorUvMatrix", [](const cgltf_material& m) { return when(m.has_pbr_metallic_roughness, m.pbr_metallic_roughness.base_color_texture); }},
    {"pbrMetallicRoughness/metallicRoughnessTexture", "metallicRoughnessUvMatrix", [](const cgltf_material& m) { return when(m.has_pbr_metallic_roughness, m.pbr_metallic_roughness.metallic_roughness_texture); }},
    {"normalTexture", "normalUvMatrix", [](const cgltf_material& m) { return when(true, m.normal_texture); }},
    {"occlusionTexture", "occlusionUvMatrix", [](const cgltf_material& m) { return when(true, m.occlusion_texture); }},
    {"emissiveTexture", "emissiveUvMatrix", [](const cgltf_material& m) { return when(true, m.emissive_texture); }},
    {"extensions/KHR_materials_clearcoat/clearcoatTexture", "clearCoatUvMatrix", [](const cgltf_material& m) { return when(m.has_clearcoat, m.clearcoat.clearcoat_texture); }},
    {"extensions/KHR_materials_clearcoat/clearcoatRoughnessTexture", "clearCoatRoughnessUvMatrix", [](const cgltf_material& m) { return when(m.has_clearcoat, m.clearcoat.clearcoat_roughness_texture); }},
    {"extensions/KHR_materials_clearcoat/clearcoatNormalTexture", "clearCoatNormalUvMatrix", [](const cgltf_material& m) { return when(m.has_clearcoat, m.clearcoat.clearcoat_normal_texture); }},
    {"extensions/KHR_materials_sheen/sheenColorTexture", "sheenColorUvMatrix", [](const cgltf_material& m) { return when(m.has_sheen, m.sheen.sheen_color_texture); }},
    {"extensions/KHR_materials_sheen/sheenRoughnessTexture", "sheenRoughnessUvMatrix", [](const cgltf_material& m) { return when(m.has_sheen, m.sheen.sheen_roughness_texture); }},
    {"extensions/KHR_materials_transmission/transmissionTexture", "transmissionUvMatrix", [](const cgltf_material& m) { return when(m.has_transmission, m.transmission.transmission_texture); }},
    {"extensions/KHR_materials_volume/thicknessTexture", "volumeThicknessUvMatrix", [](const cgltf_material& m) { return when(m.has_volume, m.volume.thickness_texture); }},
    {"extensions/KHR_materials_specular/specularTexture", "specularUvMatrix", [](const cgltf_material& m) { return when(m.has_specular, m.specular.specular_texture); }},
    {"extensions/KHR_materials_specular/specularColorTexture", "specularColorUvMatrix", [](const cgltf_material& m) { return when(m.has_specular, m.specular.specular_color_texture); }},
    {"extensions/KHR_materials_anisotropy/anisotropyTexture", "anisotropyUvMatrix", [](const cgltf_material& m) { return when(m.has_anisotropy, m.anisotropy.anisotropy_texture); }},
    {"extensions/KHR_materials_iridescence/iridescenceTexture", "iridescenceUvMatrix", [](const cgltf_material& m) { return when(m.has_iridescence, m.iridescence.iridescence_texture); }},
    {"extensions/KHR_materials_iridescence/iridescenceThicknessTexture", "iridescenceThicknessUvMatrix", [](const cgltf_material& m) { return when(m.has_iridescence, m.iridescence.iridescence_thickness_texture); }},
    {"extensions/KHR_materials_diffuse_transmission/diffuseTransmissionTexture", "diffuseUvMatrix", [](const cgltf_material& m) { return when(m.has_diffuse_transmission, m.diffuse_transmission.diffuse_transmission_texture); }},
    {"extensions/KHR_materials_diffuse_transmission/diffuseTransmissionColorTexture", "diffuseColorUvMatrix", [](const cgltf_material& m) { return when(m.has_diffuse_transmission, m.diffuse_transmission.diffuse_transmission_color_texture); }},
};

// Parses "<prefix><index>/<rest>" with a glTF-style index (no leading zeros).
bool pointer_index(std::string_view pointer, std::string_view prefix, size_t& index, std::string_view& rest) {
    if (!pointer.starts_with(prefix)) return false;
    pointer.remove_prefix(prefix.size());
    const auto slash = pointer.find('/');
    const auto digits = pointer.substr(0, slash);
    if (digits.empty() || (digits.size() > 1 && digits[0] == '0')) return false;
    const auto parsed = std::from_chars(digits.data(), digits.data() + digits.size(), index);
    if (parsed.ec != std::errc() || parsed.ptr != digits.data() + digits.size()) return false;
    rest = slash == std::string_view::npos ? std::string_view() : pointer.substr(slash + 1);
    return slash != std::string_view::npos;
}

void check_index(size_t index, size_t count) {
    if (index >= count) invalid("Animation index is out of range");
}

void prepare_cameras(const cgltf_data* data, PreparedAsset& tables) {
    for (size_t index = 0; index < data->cameras_count; ++index) {
        const auto& camera = data->cameras[index];
        const bool ortho = camera.type == cgltf_camera_type_orthographic;
        if (camera.type != cgltf_camera_type_orthographic && camera.type != cgltf_camera_type_perspective)
            invalid("Invalid camera type");
        double v[4];
        bool has_zfar = true, has_aspect = false;
        if (ortho) {
            const auto& p = camera.data.orthographic;
            v[0] = p.xmag; v[1] = p.ymag; v[2] = p.znear; v[3] = p.zfar;
        } else {
            const auto& p = camera.data.perspective;
            has_zfar = p.has_zfar;
            has_aspect = p.has_aspect_ratio;
            v[0] = p.yfov; v[1] = p.has_aspect_ratio ? p.aspect_ratio : 0; v[2] = p.znear; v[3] = p.has_zfar ? p.zfar : inf;
        }
        if (!finite_float(v[0]) || !finite_float(v[1]) || !finite_float(v[2]) || std::isnan(v[3]) || v[0] <= 0 || v[1] < 0
                || (v[1] == 0 && has_aspect) || v[2] < 0 || v[3] <= v[2])
            invalid("Invalid camera projection");
        if ((has_zfar && !finite_float(v[3])) || (!ortho && (v[0] >= pi || v[2] <= 0)))
            invalid("Invalid camera projection");
        tables.cameras.push_back({ortho, {float(v[0]), float(v[1]), float(v[2]), float(v[3])}});
    }
    for (size_t i = 0; i < data->nodes_count; ++i)
        if (data->nodes[i].camera) tables.nodes[i].camera = cgltf_camera_index(data, data->nodes[i].camera);
}

// Channels without KHR_animation_pointer stay with gltfio's node animator. Pointer channels
// that target node TRS or weights move to it too; the rest become property tracks.
void prepare_animations(cgltf_data* data, PreparedAsset& tables, SourcePatches& patches, const Issues& issue) {
    bool required = false;
    for (size_t i = 0; i < data->extensions_required_count; ++i)
        required = required || std::string_view(data->extensions_required[i]) == "KHR_animation_pointer";
    // Any sampler input that gltfio accepts, for quiet samplers whose own input it rejects.
    const cgltf_accessor* fallback = nullptr;
    for (size_t a = 0; a < data->animations_count && !fallback; ++a)
        for (size_t s = 0; s < data->animations[a].samplers_count && !fallback; ++s) {
            const auto* input = data->animations[a].samplers[s].input;
            if (input && input->type == cgltf_type_scalar && input->component_type == cgltf_component_type_r_32f && gltfio_reads(input))
                fallback = input;
        }
    for (size_t a = 0; a < data->animations_count; ++a) {
        auto& animation = data->animations[a];
        AnimationSource clip;
        if (auto name = text(animation.name)) clip.name = *name;
        std::vector<bool> node_sampler(animation.samplers_count, false);
        std::set<std::string, std::less<>> seen;
        bool node_channels = false;
        for (size_t c = 0; c < animation.channels_count; ++c) {
            auto& channel = animation.channels[c];
            const size_t sampler_index = cgltf_animation_sampler_index(&animation, channel.sampler);
            const char* extension = extension_text(channel.extensions, channel.extensions_count, "KHR_animation_pointer");
            if (!extension) {
                // cgltf reads path "pointer" as an unknown path.
                if (!channel.target_node && channel.target_path == cgltf_animation_path_type_invalid)
                    invalid("Pointer animation target lacks KHR_animation_pointer");
                node_sampler[sampler_index] = true;
                node_channels = true;
                continue;
            }
            const auto ext = extension_value(extension);
            const auto* pointer_value = ext.find("pointer");
            if (!pointer_value || !pointer_value->is_string()) invalid("KHR_animation_pointer lacks a pointer");
            const std::string& pointer = pointer_value->text;
            if (channel.target_path != cgltf_animation_path_type_invalid || channel.target_node)
                invalid("Pointer animation requires path='pointer' and no target node");
            size_t index = 0;
            std::string_view rest;
            if (pointer_index(pointer, "/nodes/", index, rest)) {
                static const std::pair<const char*, cgltf_animation_path_type> paths[] = {
                    {"translation", cgltf_animation_path_type_translation}, {"rotation", cgltf_animation_path_type_rotation},
                    {"scale", cgltf_animation_path_type_scale}, {"weights", cgltf_animation_path_type_weights}};
                const auto found = std::find_if(std::begin(paths), std::end(paths), [&](const auto& p) { return rest == p.first; });
                if (found != std::end(paths)) {
                    check_index(index, data->nodes_count);
                    if (seen.contains(pointer) || (data->nodes[index].has_matrix && found->second != cgltf_animation_path_type_weights))
                        invalid("Duplicate node pointer or animation of a matrix-authored node");
                    seen.insert(pointer);
                    // These properties have identical semantics to ordinary glTF node channels.
                    patches.node_channels.push_back({a, c, index, found->second});
                    node_sampler[sampler_index] = true;
                    node_channels = true;
                    continue;
                }
            }
            PropertyTrackSource track;
            track.index = index;
            track.components = 1;
            bool visibility = false, recognized = true;
            std::string_view field;
            if (pointer_index(pointer, "/nodes/", index, rest) && rest == "extensions/KHR_node_visibility/visible") {
                check_index(index, data->nodes_count);
                const auto& node = data->nodes[index];
                if (!extension_text(node.extensions, node.extensions_count, "KHR_node_visibility"))
                    invalid("Visibility pointer targets a node without KHR_node_visibility");
                visibility = true;
                track = {5, index, "visible", 1, 1, 0, 1};
            } else if (pointer_index(pointer, "/materials/", index, rest)) {
                const auto property = std::find_if(std::begin(material_properties), std::end(material_properties),
                                                   [&](const MaterialProperty& p) { return rest == p.path; });
                static constexpr std::string_view uv_suffix = "/extensions/KHR_texture_transform/";
                const auto at = rest.rfind(uv_suffix);
                const UvProperty* uv = nullptr;
                if (at != std::string_view::npos) {
                    field = rest.substr(at + uv_suffix.size());
                    const auto base = rest.substr(0, at);
                    if (field == "offset" || field == "rotation" || field == "scale")
                        for (const auto& candidate : uv_properties) if (base == candidate.path) uv = &candidate;
                }
                if (property != std::end(material_properties)) {
                    check_index(index, data->materials_count);
                    float rest_value[4];
                    if (!property->rest(data->materials[index], rest_value)) invalid("Animated material property has no authored parent object");
                    for (int i = 0; i < property->components; ++i)
                        if (!finite_float(rest_value[i]) || rest_value[i] < property->lower || rest_value[i] > property->upper)
                            invalid("Invalid authored animation property");
                    track = {0, index, property->parameter, property->components, 1, float(property->lower), float(property->upper)};
                } else if (uv) {
                    check_index(index, data->materials_count);
                    const auto* view = uv->view(data->materials[index]);
                    if (!view || !view->has_transform) invalid("Animated texture transform is not authored");
                    const auto& t = view->transform;
                    for (double value : {double(t.offset[0]), double(t.offset[1]), double(t.scale[0]), double(t.scale[1]), double(t.rotation)})
                        if (!finite_float(value)) invalid("Invalid animated texture transform");
                    const std::string matrix = uv->matrix;
                    const bool transpose = matrix != "anisotropyUvMatrix" && matrix != "iridescenceUvMatrix"
                        && matrix != "iridescenceThicknessUvMatrix" && matrix != "diffuseUvMatrix" && matrix != "diffuseColorUvMatrix";
                    track = {3, index, matrix + "/" + std::string(field), field == "rotation" ? 1 : 2, 1,
                             -std::numeric_limits<float>::infinity(), std::numeric_limits<float>::infinity(),
                             {}, {}, {t.offset[0], t.offset[1], t.scale[0], t.scale[1], t.rotation, float(transpose)}};
                } else {
                    recognized = false;
                }
            } else if (pointer_index(pointer, "/cameras/", index, rest)) {
                const auto slash = rest.find('/');
                const auto projection = rest.substr(0, slash);
                field = slash == std::string_view::npos ? std::string_view() : rest.substr(slash + 1);
                static const char* perspective[] = {"yfov", "aspectRatio", "znear", "zfar"};
                static const char* orthographic[] = {"xmag", "ymag", "znear", "zfar"};
                auto position = [&](const char* const (&fields)[4]) -> int {
                    for (int i = 0; i < 4; ++i) if (field == fields[i]) return i;
                    return -1;
                };
                const bool known = (projection == "perspective" || projection == "orthographic")
                    && (field == "yfov" || field == "aspectRatio" || field == "znear" || field == "zfar" || field == "xmag" || field == "ymag");
                if (known) {
                    check_index(index, data->cameras_count);
                    const auto& camera = data->cameras[index];
                    const bool ortho = projection == "orthographic";
                    const int slot = ortho ? position(orthographic) : position(perspective);
                    if (slot < 0 || (ortho ? camera.type != cgltf_camera_type_orthographic : camera.type != cgltf_camera_type_perspective)
                            || (field == "zfar" && !ortho && !camera.data.perspective.has_zfar))
                        invalid("Camera pointer does not name a defined property");
                    track = {2, index, std::to_string(slot), 1, 1, ortho && field == "znear" ? 0.0f : 1e-7f,
                             field == "yfov" ? float(pi - 1e-6) : std::numeric_limits<float>::infinity()};
                } else {
                    recognized = false;
                }
            } else if (pointer_index(pointer, "/extensions/KHR_lights_punctual/lights/", index, rest)
                       && (rest == "color" || rest == "intensity" || rest == "range" || rest == "spot/innerConeAngle"
                           || rest == "spot/outerConeAngle")) {
                check_index(index, data->lights_count);
                const auto& light = data->lights[index];
                track = {1, index, std::string(rest), rest == "color" ? 3 : 1, 1, 0,
                         rest == "color" ? 1.0f : std::numeric_limits<float>::infinity()};
                if (rest == "range") {
                    if (light.type == cgltf_light_type_directional || !(light.range > 0 && light.range <= float_max))
                        invalid("Range animation requires an authored point/spot light range");
                    track.minimum = 1e-30f;
                } else if (rest.starts_with("spot/")) {
                    if (light.type != cgltf_light_type_spot) invalid("Cone animation requires a spot light");
                    const float inner = light.spot_inner_cone_angle, outer = light.spot_outer_cone_angle;
                    // float32(pi/2) rounds upward; the authored double value was checked against pi/2.
                    if (!std::isfinite(inner) || !std::isfinite(outer) || !(0 <= inner && inner < outer && outer <= 1.5707963705062866))
                        invalid("Invalid authored spot light cones");
                    track.setup = {inner, outer};
                    track.minimum = rest.ends_with("innerConeAngle") ? 0.0f : 1e-30f;
                    track.maximum = float(1.5707963705062866);
                    track.kind = 4;
                }
            } else {
                recognized = false;
            }
            if (!recognized) {
                issue("Unsupported KHR_animation_pointer target: " + pointer, required);
                continue;
            }
            if (seen.contains(pointer)) invalid("Duplicate animation pointer target");
            seen.insert(pointer);
            const auto& sampler = *channel.sampler;
            if (visibility && (sampler.interpolation != cgltf_interpolation_type_step || !sampler.output
                    || sampler.output->component_type != cgltf_component_type_r_8u || sampler.output->normalized))
                invalid("Visibility animation requires STEP and unnormalized unsigned bytes");
            if (!sampler.input || !sampler.output) invalid("Animation sampler lacks input or output");
            const bool cubic = sampler.interpolation == cgltf_interpolation_type_cubic_spline;
            track.interpolation = sampler.interpolation == cgltf_interpolation_type_step ? 0 : cubic ? 2 : 1;
            track.times = read_floats(sampler.input, 1, true);
            const auto& times = track.times;
            if (times[0] < 0 || std::adjacent_find(times.begin(), times.end(), std::greater_equal<float>()) != times.end())
                invalid("Animation times must be nonnegative and strictly increasing");
            track.values = read_floats(sampler.output, track.components);
            if (visibility) for (auto& value : track.values) value = value != 0 ? 1.0f : 0.0f;
            if (cubic && times.size() < 2) invalid("Cubic animation requires at least two keyframes");
            const size_t rows = track.values.size() / size_t(track.components);
            if (rows != times.size() * (cubic ? 3 : 1)) invalid("Animation output count does not match its input");
            for (size_t row = cubic ? 1 : 0; row < rows; row += cubic ? 3 : 1)
                for (int c2 = 0; c2 < track.components; ++c2) {
                    const double value = track.values[row * size_t(track.components) + size_t(c2)];
                    if (value < track.minimum || value > track.maximum)
                        invalid("Animation keyframe value is outside its property's range");
                }
            clip.tracks.push_back(std::move(track));
        }
        clip.native_index = node_channels ? int(a) : -1;
        for (size_t s = 0; s < animation.samplers_count; ++s) {
            if (node_sampler[s]) continue;
            const auto& sampler = animation.samplers[s];
            const auto* quiet = sampler.input && sampler.input->type == cgltf_type_scalar
                && sampler.input->component_type == cgltf_component_type_r_32f && gltfio_reads(sampler.input)
                ? sampler.input : fallback;
            if (quiet) patches.quiet_samplers.push_back({a, s, cgltf_accessor_index(data, quiet)});
        }
        tables.animations.push_back(std::move(clip));
    }
}

void prepare_nodes(const cgltf_data* data, PreparedAsset& tables) {
    tables.nodes.resize(data->nodes_count);
    for (size_t i = 0; i < data->nodes_count; ++i) {
        const auto& node = data->nodes[i];
        auto& source = tables.nodes[i];
        if (const char* ext = extension_text(node.extensions, node.extensions_count, "KHR_node_visibility")) {
            const auto value = extension_value(ext);
            if (const auto* visible = value.find("visible")) {
                if (visible->type != json::Value::Type::boolean) invalid("Node visibility must be a boolean");
                source.visible = visible->boolean;
            }
        }
        source.name = text(node.name);
        if (node.mesh) source.mesh = text(node.mesh->name);
        if (node.light) {
            source.light = cgltf_light_index(data, node.light);
            source.automatic_range = node.light->type != cgltf_light_type_directional && !(node.light->range > 0);
        }
        size_t targets = 0;
        if (node.mesh)
            for (size_t p = 0; p < node.mesh->primitives_count; ++p) targets = std::max(targets, node.mesh->primitives[p].targets_count);
        if (!targets) continue;
        if (node.weights_count) source.weights.assign(node.weights, node.weights + node.weights_count);
        else if (node.mesh->weights_count) source.weights.assign(node.mesh->weights, node.mesh->weights + node.mesh->weights_count);
        else source.weights.assign(targets, 0.0f);
        if (source.weights.size() != targets) invalid("Morph weight count does not match targets");
    }
}

// Model bounds ------------------------------------------------------------------------------

// Column-major, like cgltf and Filament.
using Mat4 = std::array<float, 16>;

Mat4 multiply(const Mat4& a, const Mat4& b) {
    Mat4 out{};
    for (int c = 0; c < 4; ++c)
        for (int r = 0; r < 4; ++r) {
            float sum = 0;
            for (int k = 0; k < 4; ++k) sum += a[k * 4 + r] * b[c * 4 + k];
            out[c * 4 + r] = sum;
        }
    return out;
}

// World transforms of all nodes relative to the gltfio root. cgltf_node_transform_world walks
// every ancestor for each call; a skin asks for many joints that share ancestors.
std::vector<Mat4> node_worlds(const cgltf_data* data) {
    std::vector<Mat4> worlds(data->nodes_count);
    std::vector<uint8_t> done(data->nodes_count, 0);
    std::vector<size_t> chain;
    for (size_t i = 0; i < data->nodes_count; ++i) {
        for (const cgltf_node* node = &data->nodes[i]; node && !done[cgltf_node_index(data, node)]; node = node->parent)
            chain.push_back(cgltf_node_index(data, node));
        for (; !chain.empty(); chain.pop_back()) {
            const size_t index = chain.back();
            const auto& node = data->nodes[index];
            Mat4 local;
            cgltf_node_transform_local(&node, local.data());
            worlds[index] = node.parent ? multiply(worlds[cgltf_node_index(data, node.parent)], local) : local;
            done[index] = 1;
        }
    }
    return worlds;
}

struct Box {
    Vec3 low{FLT_MAX, FLT_MAX, FLT_MAX}, high{-FLT_MAX, -FLT_MAX, -FLT_MAX};
    void add(const Vec3& p) {
        for (int i = 0; i < 3; ++i) { low[i] = std::min(low[i], p[i]); high[i] = std::max(high[i], p[i]); }
    }
    void add(const Box& b) { if (b.low[0] <= b.high[0]) { add(b.low); add(b.high); } }
    bool empty() const { return low[0] > high[0]; }
};

Vec3 transform_point(const Mat4& m, const float* p) {
    Vec3 out;
    for (int r = 0; r < 3; ++r) out[r] = m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r];
    return out;
}

std::optional<Mat4> affine_inverse(const Mat4& m) {
    auto at = [&](int r, int c) { return double(m[c * 4 + r]); };
    const double c00 = at(1,1) * at(2,2) - at(1,2) * at(2,1), c01 = at(1,2) * at(2,0) - at(1,0) * at(2,2),
                 c02 = at(1,0) * at(2,1) - at(1,1) * at(2,0);
    const double det = at(0,0) * c00 + at(0,1) * c01 + at(0,2) * c02;
    if (!std::isfinite(det) || std::abs(det) < 1e-30) return std::nullopt;
    const double r[3][3] = {
        {c00 / det, (at(0,2) * at(2,1) - at(0,1) * at(2,2)) / det, (at(0,1) * at(1,2) - at(0,2) * at(1,1)) / det},
        {c01 / det, (at(0,0) * at(2,2) - at(0,2) * at(2,0)) / det, (at(0,2) * at(1,0) - at(0,0) * at(1,2)) / det},
        {c02 / det, (at(0,1) * at(2,0) - at(0,0) * at(2,1)) / det, (at(0,0) * at(1,1) - at(0,1) * at(1,0)) / det}};
    Mat4 out{};
    for (int row = 0; row < 3; ++row) {
        double t = 0;
        for (int c = 0; c < 3; ++c) {
            out[c * 4 + row] = float(r[row][c]);
            t -= r[row][c] * at(c, 3);
        }
        out[12 + row] = float(t);
    }
    out[15] = 1;
    return out;
}

// Arvo's method: the exact box of the transformed box, without eight corner transforms.
Box transform_box(const Mat4& m, const Box& box) {
    Box out;
    if (box.empty()) return out;
    for (int r = 0; r < 3; ++r) {
        float low = m[12 + r], high = m[12 + r];
        for (int c = 0; c < 3; ++c) {
            const float a = m[c * 4 + r] * box.low[c], b = m[c * 4 + r] * box.high[c];
            low += std::min(a, b);
            high += std::max(a, b);
        }
        out.low[r] = low;
        out.high[r] = high;
    }
    return out;
}

// Accessor min and max are in the stored component type, also for normalized integers.
std::optional<Box> accessor_box(const cgltf_accessor* accessor) {
    if (!accessor || !accessor->has_min || !accessor->has_max || cgltf_num_components(accessor->type) < 3)
        return std::nullopt;
    float scale = 1;
    bool is_signed = false;
    if (accessor->normalized) {
        switch (accessor->component_type) {
            case cgltf_component_type_r_8: scale = 127; is_signed = true; break;
            case cgltf_component_type_r_8u: scale = 255; break;
            case cgltf_component_type_r_16: scale = 32767; is_signed = true; break;
            case cgltf_component_type_r_16u: scale = 65535; break;
            default: break;
        }
    }
    Box box;
    for (int i = 0; i < 3; ++i) {
        box.low[i] = accessor->min[i] / scale;
        box.high[i] = accessor->max[i] / scale;
        if (is_signed) { box.low[i] = std::max(box.low[i], -1.0f); box.high[i] = std::max(box.high[i], -1.0f); }
    }
    return box;
}

const cgltf_accessor* attribute(const cgltf_attribute* attributes, size_t count, cgltf_attribute_type type, int index = 0) {
    for (size_t i = 0; i < count; ++i)
        if (attributes[i].type == type && attributes[i].index == index) return attributes[i].data;
    return nullptr;
}

// Accessor bounds of the base positions, grown by each morph target at weight one, as gltfio
// computes them for culling.
Box primitive_box(const cgltf_primitive& primitive) {
    Box box;
    const auto base = accessor_box(attribute(primitive.attributes, primitive.attributes_count, cgltf_attribute_type_position));
    if (!base) return box;
    box = *base;
    for (size_t t = 0; t < primitive.targets_count; ++t) {
        const auto& target = primitive.targets[t];
        const auto delta = accessor_box(attribute(target.attributes, target.attributes_count, cgltf_attribute_type_position));
        if (!delta) continue;
        Box moved;
        for (int i = 0; i < 3; ++i) { moved.low[i] = base->low[i] + delta->low[i]; moved.high[i] = base->high[i] + delta->high[i]; }
        box.add(moved);
    }
    return box;
}

std::vector<float> unpack(const cgltf_accessor* accessor, size_t components) {
    std::vector<float> values(accessor->count * components);
    if (cgltf_num_components(accessor->type) != components ||
            cgltf_accessor_unpack_floats(accessor, values.data(), values.size()) != values.size())
        values.clear();
    return values;
}

// Skinned vertices ignore their node's transform: each one moves by the weighted sum of its
// joints' world transforms times their inverse bind matrices. Skinning is not linear in the
// position box, so the vertices are skinned one by one, once per load.
Box skinned_box(const cgltf_data* data, const cgltf_node& node, const cgltf_primitive& primitive,
                const std::vector<Mat4>& worlds) {
    const auto& skin = *node.skin;
    std::vector<Mat4> joints(skin.joints_count);
    for (size_t j = 0; j < skin.joints_count; ++j) {
        Mat4 inverse_bind = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
        if (skin.inverse_bind_matrices) cgltf_accessor_read_float(skin.inverse_bind_matrices, j, inverse_bind.data(), 16);
        joints[j] = multiply(worlds[cgltf_node_index(data, skin.joints[j])], inverse_bind);
    }
    const auto* positions = attribute(primitive.attributes, primitive.attributes_count, cgltf_attribute_type_position);
    std::vector<float> points;
    // Draco accessors have no buffer view; cgltf would unpack zeros for them.
    if (positions && !primitive.has_draco_mesh_compression) points = unpack(positions, 3);
    std::vector<std::pair<std::vector<float>, std::vector<float>>> sets;
    for (int s = 0; !points.empty(); ++s) {
        const auto* j = attribute(primitive.attributes, primitive.attributes_count, cgltf_attribute_type_joints, s);
        const auto* w = attribute(primitive.attributes, primitive.attributes_count, cgltf_attribute_type_weights, s);
        if (!j || !w) break;
        auto indices = unpack(j, 4), weights = unpack(w, 4);
        if (indices.size() != points.size() / 3 * 4 || weights.size() != indices.size()) { points.clear(); break; }
        sets.emplace_back(std::move(indices), std::move(weights));
    }
    Box box;
    if (points.empty() || sets.empty()) {
        // Unreadable vertices: the union over joints of the transformed position box contains
        // every skinned vertex, since each one is a convex combination of those transforms.
        const Box local = primitive_box(primitive);
        for (const auto& joint : joints) box.add(transform_box(joint, local));
        return box;
    }
    std::vector<std::vector<float>> deltas;
    for (size_t t = 0; t < primitive.targets_count; ++t) {
        const auto& target = primitive.targets[t];
        if (const auto* d = attribute(target.attributes, target.attributes_count, cgltf_attribute_type_position))
            if (auto values = unpack(d, 3); values.size() == points.size()) deltas.push_back(std::move(values));
    }
    const size_t count = points.size() / 3;
    for (size_t v = 0; v < count; ++v) {
        Mat4 blend{};
        float total = 0;
        for (const auto& [indices, weights] : sets)
            for (size_t k = 0; k < 4; ++k) {
                const float weight = weights[v * 4 + k];
                const auto index = size_t(indices[v * 4 + k]);
                if (!(weight > 0) || index >= joints.size()) continue;
                total += weight;
                for (size_t e = 0; e < 16; ++e) blend[e] += weight * joints[index][e];
            }
        // A vertex without weight collapses to its node origin in Filament; it spans nothing.
        if (!(total > 0)) continue;
        // gltfio normalizes weights by default, so the bounds do too.
        for (auto& e : blend) e /= total;
        const float* p = &points[v * 3];
        box.add(transform_point(blend, p));
        for (const auto& delta : deltas) {
            const float moved[3] = {p[0] + delta[v * 3], p[1] + delta[v * 3 + 1], p[2] + delta[v * 3 + 2]};
            box.add(transform_point(blend, moved));
        }
    }
    return box;
}

// The rest-pose bounds in the model's frame, before model and node transforms change: glTF
// node transforms apply, skinned meshes are skinned with the joints' rest transforms, and each
// morph target counts at weight one, as in gltfio's culling boxes. Like gltfio, every node with
// a mesh counts, also nodes outside the scenes. gltfio's getBoundingBox() instead moves a skinned
// mesh's accessor box by the mesh node's transform, which the glTF specification says to ignore:
// Sketchfab rigs scale the armature by 100 and put the 0.01 in the inverse bind matrices.
// The buffers that gltfio 1.77.1 builds, as AssetLoader::createPrimitive() and
// createRenderable() lay them out: each attribute uploads its accessor's range of the buffer
// view (ResourceLoader, computeBindingSize()), normals become 8-byte tangent frames, one 4-byte
// dummy attribute fills missing UVs and colors, 8-bit indices widen to 16 bits, and each node
// with morph targets gets a morph target buffer of RGBA32F positions and RGBA16I tangents.
// Primitives belong to their mesh, so meshes count once. Per instance, each skinned or morphed
// node also gets a 16 KiB bone buffer (Filament sizes it for 256 bones), and the animator copies
// every sampler: a map node per keyframe time (about 64 bytes with its heap header) and the
// values as floats.
void estimate_geometry(const cgltf_data* data, PreparedAsset& tables) {
    auto binding = [](const cgltf_accessor* accessor) -> uint64_t {
        if (!accessor || !accessor->count) return 0;
        const uint64_t element = cgltf_calc_size(accessor->type, accessor->component_type);
        const uint64_t stride = accessor->stride ? accessor->stride : element;
        return stride * (accessor->count - 1) + element;
    };
    for (size_t m = 0; m < data->meshes_count; ++m) {
        const auto& mesh = data->meshes[m];
        for (size_t p = 0; p < mesh.primitives_count; ++p) {
            const auto& primitive = mesh.primitives[p];
            if (!primitive.attributes_count) continue;
            const uint64_t vertices = primitive.attributes[0].data ? primitive.attributes[0].data->count : 0;
            if (primitive.indices)
                tables.geometry_bytes += primitive.indices->count * (primitive.indices->component_type == cgltf_component_type_r_32u ? 4 : 2);
            else
                tables.geometry_bytes += vertices * 4;
            bool normals = false, uv0 = false, uv1 = false, color = false;
            for (size_t a = 0; a < primitive.attributes_count; ++a) {
                const auto& attribute = primitive.attributes[a];
                switch (attribute.type) {
                    case cgltf_attribute_type_tangent: continue;
                    case cgltf_attribute_type_normal: normals = true; tables.geometry_bytes += vertices * 8; continue;
                    case cgltf_attribute_type_texcoord: (attribute.index ? uv1 : uv0) = true; break;
                    case cgltf_attribute_type_color: color = true; break;
                    default: break;
                }
                tables.geometry_bytes += binding(attribute.data);
            }
            // Lit materials without normals get generated ones; filly's materials are all lit
            // or need the slot anyway, so this counts every primitive without normals.
            if (!normals) tables.geometry_bytes += vertices * 8;
            if (!uv0 || !uv1 || !color) tables.geometry_bytes += vertices * 4;
        }
    }
    constexpr uint64_t morph_width = 2048;
    std::vector<bool> reached(data->nodes_count, false);
    std::vector<const cgltf_node*> pending;
    for (size_t s = 0; s < data->scenes_count; ++s)
        for (size_t n = 0; n < data->scenes[s].nodes_count; ++n) pending.push_back(data->scenes[s].nodes[n]);
    if (!data->scenes_count) for (size_t n = 0; n < data->nodes_count; ++n) if (!data->nodes[n].parent) pending.push_back(&data->nodes[n]);
    while (!pending.empty()) {
        const auto* node = pending.back();
        pending.pop_back();
        const size_t index = cgltf_node_index(data, node);
        if (reached[index]) continue;
        reached[index] = true;
        for (size_t c = 0; c < node->children_count; ++c) pending.push_back(node->children[c]);
        if (!node->mesh || !node->mesh->primitives_count) continue;
        const uint64_t targets = std::min<uint64_t>(node->mesh->primitives[0].targets_count, 256);
        if (targets || node->skin) tables.instance_geometry_bytes += 256 * 64;
        if (!targets) continue;
        uint64_t vertices = 0;
        for (size_t p = 0; p < node->mesh->primitives_count; ++p) {
            const auto& primitive = node->mesh->primitives[p];
            if (primitive.attributes_count && primitive.attributes[0].data) vertices += primitive.attributes[0].data->count;
        }
        const uint64_t width = std::min(vertices, morph_width), height = (vertices + morph_width) / morph_width;
        tables.instance_geometry_bytes += width * height * targets * (16 + 8);
    }
    for (size_t a = 0; a < data->animations_count; ++a)
        for (size_t s = 0; s < data->animations[a].samplers_count; ++s) {
            const auto& sampler = data->animations[a].samplers[s];
            if (sampler.input) tables.instance_cpu_bytes += sampler.input->count * 64;
            if (sampler.output) tables.instance_cpu_bytes += sampler.output->count * cgltf_num_components(sampler.output->type) * 4;
        }
}

void compute_bounds(const cgltf_data* data, PreparedAsset& tables) {
    const auto worlds = node_worlds(data);
    Box bounds;
    tables.node_boxes.assign(data->nodes_count, {Box{}.low, Box{}.high});
    tables.parents.assign(data->nodes_count, UINT32_MAX);
    for (size_t i = 0; i < data->nodes_count; ++i) {
        const auto& node = data->nodes[i];
        if (node.parent) tables.parents[i] = uint32_t(cgltf_node_index(data, node.parent));
        if (!node.mesh) continue;
        const bool skinned = node.skin && node.skin->joints_count;
        Box box;
        for (size_t p = 0; p < node.mesh->primitives_count; ++p) {
            const auto& primitive = node.mesh->primitives[p];
            box.add(skinned ? skinned_box(data, node, primitive, worlds) : transform_box(worlds[i], primitive_box(primitive)));
        }
        bounds.add(box);
        tables.node_boxes[i] = {box.low, box.high};
        // Filament culls a renderable, and fits shadow maps, with its box in the node's frame.
        // gltfio gives skinned meshes the bind-space accessor box there, which can miss the
        // skinned vertices entirely or be 100 times too large.
        if (skinned && !box.empty())
            if (const auto inverse = affine_inverse(worlds[i])) {
                const Box local = transform_box(*inverse, box);
                tables.skinned_boxes.push_back({i, {local.low, local.high}});
            }
    }
    tables.bounds = {bounds.low, bounds.high};
}

void prepare_materials(const cgltf_data* data, PreparedAsset& tables, const std::string& path) {
    tables.materials.resize(data->materials_count);
    for (size_t index = 0; index < data->materials_count; ++index) {
        const auto& m = data->materials[index];
        auto& source = tables.materials[index];
        // Provider-built instances keep the name gltfio would give them; unnamed ones get an index.
        source.label = m.name ? std::string(m.name) : "material_" + std::to_string(index);
        source.emissive_factor = {m.emissive_factor[0], m.emissive_factor[1], m.emissive_factor[2]};
        if (!m.has_anisotropy && !m.has_iridescence) continue;
        if (m.unlit || m.has_pbr_specular_glossiness || m.has_diffuse_transmission)
            throw AssetError("Anisotropy/iridescence cannot use unlit, specular-glossiness, or custom diffuse transmission");
        SurfaceSource surface;
        surface.has_anisotropy = m.has_anisotropy;
        surface.has_iridescence = m.has_iridescence;
        if (m.has_anisotropy) {
            surface.anisotropy = m.anisotropy.anisotropy_strength;
            surface.rotation = m.anisotropy.anisotropy_rotation;
        }
        if (m.has_iridescence) {
            surface.iridescence = m.iridescence.iridescence_factor;
            surface.ior = m.iridescence.iridescence_ior;
            surface.minimum = m.iridescence.iridescence_thickness_min;
            surface.maximum = m.iridescence.iridescence_thickness_max;
        }
        const double v[] = {surface.anisotropy, surface.rotation, surface.iridescence, surface.ior, surface.minimum, surface.maximum};
        if (!std::all_of(std::begin(v), std::end(v), finite_float) || !(0 <= v[0] && v[0] <= 1 && 0 <= v[2] && v[2] <= 1
                && v[3] >= 1 && std::min(v[4], v[5]) >= 0))
            invalid("Invalid anisotropy or iridescence factors");
        source.kind = MaterialKind::surface;
        source.source = tables.surfaces.size();
        tables.surfaces.push_back(std::move(surface));
    }
    for (size_t index = 0; index < data->materials_count; ++index) {
        const auto& m = data->materials[index];
        if (!m.has_diffuse_transmission) continue;
        // Unknown extensions already produced a warning or error.
        std::vector<std::string> conflicts;
        const std::pair<bool, const char*> others[] = {
            {bool(m.has_clearcoat), "KHR_materials_clearcoat"}, {bool(m.unlit), "KHR_materials_unlit"},
            {bool(m.has_pbr_specular_glossiness), "KHR_materials_pbrSpecularGlossiness"},
            {bool(m.has_sheen), "KHR_materials_sheen"}, {bool(m.has_specular), "KHR_materials_specular"},
            {bool(m.has_transmission), "KHR_materials_transmission"}};
        for (const auto& [present, name] : others) if (present) conflicts.push_back(name);
        if (!conflicts.empty()) {
            std::sort(conflicts.begin(), conflicts.end());
            std::string list = "[";
            for (size_t i = 0; i < conflicts.size(); ++i) list += (i ? ", '" : "'") + conflicts[i] + "'";
            throw AssetError("Diffuse transmission material has unsupported combinations: " + list + "]");
        }
        const auto& ext = m.diffuse_transmission;
        DiffuseSource diffuse;
        diffuse.factor = ext.diffuse_transmission_factor;
        std::copy_n(ext.diffuse_transmission_color_factor, 3, diffuse.color.begin());
        if (!(diffuse.factor >= 0 && diffuse.factor <= 1)
                || !std::all_of(diffuse.color.begin(), diffuse.color.end(), [](float x) { return x >= 0 && x <= 1; }))
            invalid("Invalid diffuse transmission factor or color");
        const auto& volume = m.volume;
        const double thickness = m.has_volume ? volume.thickness_factor : 0;
        // cgltf stores an absent attenuationDistance as FLT_MAX.
        const bool authored = m.has_volume && volume.attenuation_distance < FLT_MAX;
        const double distance = authored ? double(volume.attenuation_distance) : inf;
        if (!std::isfinite(thickness) || thickness < 0 || thickness > float_max || !(distance > 0) || (authored && !std::isfinite(distance)))
            invalid("Invalid volume thickness or attenuation distance");
        const float white[3] = {1, 1, 1};
        const float* attenuation = m.has_volume ? volume.attenuation_color : white;
        if (!std::all_of(attenuation, attenuation + 3, [](float x) { return x >= 0 && x <= 1; }))
            invalid("Invalid volume attenuation color");
        for (int c = 0; c < 3; ++c) {
            const double absorption = -std::log(std::max(double(attenuation[c]), 1e-30)) / distance;
            if (!std::isfinite(absorption) || absorption > float_max) invalid("Volume absorption exceeds shader range");
            diffuse.absorption[c] = float(absorption);
        }
        diffuse.thickness = float(thickness);
        diffuse.factor_texture = texture_info(ext.diffuse_transmission_texture, path);
        diffuse.color_texture = texture_info(ext.diffuse_transmission_color_texture, path);
        if (m.has_volume) diffuse.thickness_texture = texture_info(volume.thickness_texture, path);
        auto& source = tables.materials[index];
        source.kind = MaterialKind::diffuse;
        source.source = tables.diffuse.size();
        tables.diffuse.push_back(std::move(diffuse));
    }
}

// Archive entry, texture slots, and UV sets of each glTF material. The provider applies the
// plan; warnings come from here, so strict loads raise them as errors.
void plan_archive_materials(const cgltf_data* data, PreparedAsset& tables, const std::string& path, const Issues& issue) {
    using R = ExtensionRole;
    using E = ArchiveEntry;
    tables.plans.resize(data->materials_count);
    tables.textures.resize(data->textures_count);
    for (size_t index = 0; index < data->materials_count; ++index) {
        const auto& m = data->materials[index];
        auto& plan = tables.plans[index];
        const auto name = repr(material_name(m, index, " "));
        if (m.has_clearcoat && m.clearcoat.clearcoat_normal_texture.texture)
            plan.clearcoat_normal_scale = m.clearcoat.clearcoat_normal_texture.scale;
        // Only the Specular entries and the anisotropic entries have KHR_materials_specular inputs.
        // With them, Filament's isotropic lobe uses glTF's F90 instead of its F90 from F0; see
        // native/materials/surface.mat.in.
        const bool anisotropy = m.has_anisotropy, specular = m.has_specular;
        const bool extended = m.has_clearcoat || m.has_sheen || m.has_iridescence;
        if (m.unlit) plan.entry = E::Unlit;
        else if (m.has_diffuse_transmission) plan.entry = E::DiffuseTransmission;
        else if (m.has_pbr_specular_glossiness) plan.entry = E::SpecularGlossiness;
        else if (m.has_volume) plan.entry = anisotropy ? E::RefractionSolidAnisotropy
                                            : specular ? E::RefractionSolidSpecular : E::RefractionSolid;
        else if (m.has_transmission) plan.entry = anisotropy ? E::RefractionThinAnisotropy
                                                  : specular ? E::RefractionThinSpecular : E::RefractionThin;
        else if (anisotropy) plan.entry = E::LitAnisotropy;
        else if (specular) plan.entry = E::LitSpecular;
        else if (extended) plan.entry = E::LitExtended;
        const bool lit = plan.entry != E::Unlit && plan.entry != E::SpecularGlossiness && plan.entry != E::DiffuseTransmission;
        if (plan.entry == E::SpecularGlossiness) {
            std::vector<std::string> dropped;
            const std::pair<bool, const char*> others[] = {
                {bool(m.has_clearcoat), "KHR_materials_clearcoat"}, {bool(m.has_sheen), "KHR_materials_sheen"},
                {bool(m.has_specular), "KHR_materials_specular"}, {bool(m.has_transmission), "KHR_materials_transmission"},
                {bool(m.has_volume), "KHR_materials_volume"}};
            for (const auto& [present, extension] : others) if (present) dropped.push_back(extension);
            if (!dropped.empty()) {
                std::string list;
                for (const auto& item : dropped) list += (list.empty() ? "" : ", ") + item;
                issue("Material " + name + " uses KHR_materials_pbrSpecularGlossiness, which renders without " + list);
            }
        }
        // Core textures on a third UV set: every archive entry reads TEXCOORD_0 and TEXCOORD_1.
        const std::pair<const cgltf_texture_view*, const char*> core[] = {
            {&m.pbr_metallic_roughness.base_color_texture, "baseColorTexture"},
            {&m.pbr_metallic_roughness.metallic_roughness_texture, "metallicRoughnessTexture"},
            {&m.normal_texture, "normalTexture"}, {&m.occlusion_texture, "occlusionTexture"},
            {&m.emissive_texture, "emissiveTexture"},
            {&m.pbr_specular_glossiness.diffuse_texture, "diffuseTexture"}};
        for (const auto& [view, role] : core) {
            const int uv = view->has_transform && view->transform.has_texcoord ? view->transform.texcoord : view->texcoord;
            if (view->texture && uv > 1)
                issue("Material " + name + " renders without its " + role + ", which uses TEXCOORD_"
                      + std::to_string(uv) + "; filly supports TEXCOORD_0 and TEXCOORD_1");
        }
        if (!lit) continue;
        const bool refraction = plan.entry >= E::RefractionThin && plan.entry <= E::RefractionSolidAnisotropy;
        const bool lobes = plan.entry != E::LitCore;
        struct Use { R role; const cgltf_texture_view* view; bool enabled; bool srgb; const char* label; };
        const auto same = [](const cgltf_texture_view& a, const cgltf_texture_view& b) { return a.texture && a.texture == b.texture; };
        const Use uses[] = {
            {R::transmission, &m.transmission.transmission_texture, m.has_transmission && refraction, false, "transmissionTexture"},
            {R::volumeThickness, &m.volume.thickness_texture, m.has_volume && refraction, false, "thicknessTexture"},
            {R::anisotropy, &m.anisotropy.anisotropy_texture, bool(m.has_anisotropy), false, "anisotropyTexture"},
            {R::iridescence, &m.iridescence.iridescence_texture, m.has_iridescence && lobes, false, "iridescenceTexture"},
            {R::clearCoat, &m.clearcoat.clearcoat_texture, m.has_clearcoat && lobes, false, "clearcoatTexture"},
            // gltfio decodes these in sRGB, and a roughness or specular map that shares the texture with them too.
            {R::sheenColor, &m.sheen.sheen_color_texture, m.has_sheen && lobes, true, "sheenColorTexture"},
            {R::specularColor, &m.specular.specular_color_texture, bool(m.has_specular), true, "specularColorTexture"},
            {R::specular, &m.specular.specular_texture, bool(m.has_specular),
                same(m.specular.specular_texture, m.specular.specular_color_texture), "specularTexture"},
            {R::iridescenceThickness, &m.iridescence.iridescence_thickness_texture, m.has_iridescence && lobes, false,
                "iridescenceThicknessTexture"},
            {R::clearCoatRoughness, &m.clearcoat.clearcoat_roughness_texture, m.has_clearcoat && lobes, false,
                "clearcoatRoughnessTexture"},
            {R::sheenRoughness, &m.sheen.sheen_roughness_texture, m.has_sheen && lobes,
                same(m.sheen.sheen_roughness_texture, m.sheen.sheen_color_texture), "sheenRoughnessTexture"},
            {R::clearCoatNormal, &m.clearcoat.clearcoat_normal_texture, m.has_clearcoat && lobes, false,
                "clearcoatNormalTexture"},
        };
        const size_t capacity = plan.entry == E::LitCore ? 0 : refraction ? 3 : 4;
        std::vector<std::string> dropped;
        size_t distinct = 0;
        for (const auto& use : uses) {
            if (!use.enabled || !use.view->texture) continue;
            const size_t texture = size_t(use.view->texture - data->textures);
            auto& target = plan.roles[size_t(use.role)];
            // texture_info() checks the image, sampler, and UV set.
            AssetTexture info = texture_info(*use.view, path, false);
            target.uv = uint8_t(info.uv);
            target.transform = info.transform;
            const auto found = std::find_if(plan.slots.begin(), plan.slots.end(), [&](const ExtensionSampler& s) {
                return s.texture == texture && s.srgb == use.srgb; });
            const bool known = std::any_of(uses, std::end(uses), [&](const Use& other) {
                return &other < &use && other.enabled && other.view->texture == use.view->texture && other.srgb == use.srgb; });
            if (!known) ++distinct;
            if (found != plan.slots.end()) {
                target.slot = int8_t(found - plan.slots.begin());
            } else if (plan.slots.size() < capacity) {
                target.slot = int8_t(plan.slots.size());
                plan.slots.push_back({texture, use.srgb});
                if (tables.textures[texture].bytes.empty()) tables.textures[texture] = texture_info(*use.view, path);
            } else {
                dropped.push_back(use.label);
            }
        }
        if (!dropped.empty()) {
            std::string list;
            for (const auto& item : dropped) list += (list.empty() ? "" : ", ") + item;
            issue("Material " + name + " has " + std::to_string(distinct) + " extension textures, but its shader has "
                  + std::to_string(capacity) + " extension texture samplers; it renders without its " + list);
        }
    }
}

}

std::vector<uint8_t> read_file(const std::filesystem::path& file) {
    // An unbuffered stdio read goes straight into the vector. With MSVC, std::ifstream took about
    // twice as long: 13.4 ms against 6.8 ms to prepare Sponza, most of it its 9.5 MB buffer.
#ifdef _WIN32
    std::FILE* stream = _wfopen(file.c_str(), L"rb");
#else
    std::FILE* stream = std::fopen(file.c_str(), "rb");
#endif
    if (!stream) throw AssetError("Could not open file");
    std::unique_ptr<std::FILE, int (*)(std::FILE*)> guard(stream, std::fclose);
    std::setvbuf(stream, nullptr, _IONBF, 0);
    std::error_code error;
    const auto size = std::filesystem::file_size(file, error);
    if (error) throw AssetError("Could not read file");
    if (size > std::numeric_limits<uint32_t>::max()) throw AssetError("File is larger than 4 GiB");
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    if (!bytes.empty() && std::fread(bytes.data(), 1, bytes.size(), stream) != bytes.size())
        throw AssetError("Could not read file");
    return bytes;
}

std::filesystem::path resource_path(const std::string& path, const std::string& uri) {
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
    auto u8 = [](const std::string& value) {
        const auto* first = reinterpret_cast<const char8_t*>(value.data());
        return std::filesystem::path(std::u8string(first, first + value.size()));
    };
    // An absolute path in the URI replaces the document folder.
    return u8(path).parent_path() / u8(decoded);
}

std::vector<uint8_t> decode_meshopt(const uint8_t* source, size_t size, size_t count, size_t stride,
                                    const std::string& mode, const std::string& filter) {
    if (!count || !stride || stride > 256 || count > (size_t(512) << 20) / stride)
        throw AssetError("Invalid meshopt decoded size");
    if ((mode == "ATTRIBUTES" && stride % 4) ||
            ((mode == "TRIANGLES" || mode == "INDICES") && stride != 2 && stride != 4) ||
            (mode == "TRIANGLES" && count % 3) || (mode != "ATTRIBUTES" && filter != "NONE"))
        throw AssetError("Invalid meshopt mode or stride");
    if ((filter == "OCTAHEDRAL" || filter == "COLOR") && stride != 4 && stride != 8)
        throw AssetError("Invalid meshopt filter stride");
    if (filter == "QUATERNION" && stride != 8) throw AssetError("Invalid quaternion stride");
    if (filter == "EXPONENTIAL" && stride % 4) throw AssetError("Invalid exponential stride");
    std::vector<uint8_t> output(count * stride);
    int status;
    if (mode == "ATTRIBUTES") status = fp_meshopt_decodeVertexBuffer(output.data(), count, stride, source, size);
    else if (mode == "TRIANGLES") status = fp_meshopt_decodeIndexBuffer(output.data(), count, stride, source, size);
    else if (mode == "INDICES") status = fp_meshopt_decodeIndexSequence(output.data(), count, stride, source, size);
    else throw AssetError("Unknown meshopt mode");
    if (status) throw AssetError("Invalid meshopt bitstream");
    if (filter == "OCTAHEDRAL") fp_meshopt_decodeFilterOct(output.data(), count, stride);
    else if (filter == "QUATERNION") fp_meshopt_decodeFilterQuat(output.data(), count, stride);
    else if (filter == "EXPONENTIAL") fp_meshopt_decodeFilterExp(output.data(), count, stride);
    else if (filter == "COLOR") fp_meshopt_decodeFilterColor(output.data(), count, stride);
    else if (filter != "NONE") throw AssetError("Unknown meshopt filter");
    return output;
}

Prepared prepare_asset(std::vector<uint8_t> bytes, const std::string& path, const PrepareOptions& options,
                       std::vector<std::string>& warnings) {
    if (bytes.empty() || bytes.size() > std::numeric_limits<uint32_t>::max())
        throw AssetError("Asset is empty or larger than 4 GiB");
    const Issues issue{options, warnings};
    Document document;
    parse(document, bytes);
    cgltf_data* data = document.data;
    Prepared prepared;
    prepared.tables = std::make_shared<PreparedAsset>();
    auto& tables = *prepared.tables;
    // In a view that is not transparent, Filament writes the sharpened edge alpha of MASK
    // materials to the target, and only the encode pass then stores alpha one. Variant materials
    // are included because a variant can switch a mesh to one of them.
    for (size_t i = 0; i < data->materials_count; ++i)
        tables.masked = tables.masked || data->materials[i].alpha_mode == cgltf_alpha_mode_mask;

    std::set<std::string, std::less<>> used, required;
    for (size_t i = 0; i < data->extensions_used_count; ++i) used.insert(data->extensions_used[i]);
    for (size_t i = 0; i < data->extensions_required_count; ++i) {
        used.insert(data->extensions_required[i]);
        required.insert(data->extensions_required[i]);
    }
    for (const auto& name : used)
        if (!supported.contains(name) && !ignored.contains(name))
            issue("Unsupported glTF extension: " + name, required.contains(name));
    if (used.contains("KHR_materials_transmission") && !options.refraction)
        issue("Asset uses glass transmission. Set scene.refraction = True before loading it.");
    for (size_t i = 0; i < data->materials_count; ++i) {
        const auto& m = data->materials[i];
        if (m.has_dispersion && (!m.has_volume || m.unlit || m.has_pbr_specular_glossiness))
            throw AssetError("Dispersion requires a volume material without unlit or specular-glossiness");
    }

    Loader loader{data, path};
    load_buffers(loader, prepared.patches);
    if (auto expanded = expand_instances(loader)) {
        bytes = std::move(*expanded);
        parse(document, bytes);
        data = document.data;
        attach(data, *loader.store);
        for (const auto view : prepared.patches.decoded_views) data->buffer_views[view].has_meshopt_compression = false;
        loader.data = data;
        loader.json.reset();
    }
    // gltfio runs the same check before it uploads anything.
    if (cgltf_validate(data) != cgltf_result_success) invalid("cgltf validation failed");

    prepare_nodes(data, tables);
    compute_bounds(data, tables);
    estimate_geometry(data, tables);
    prepare_cameras(data, tables);
    prepare_animations(data, tables, prepared.patches, issue);
    prepare_materials(data, tables, path);
    plan_archive_materials(data, tables, path, issue);
    for (size_t i = 0; i < data->images_count; ++i) {
        const auto& image = data->images[i];
        if (image.mime_type || !image.buffer_view) continue;
        const auto* first = static_cast<const uint8_t*>(cgltf_buffer_view_data(image.buffer_view));
        if (!first) continue;
        std::vector<uint8_t> head(first, first + std::min<size_t>(image.buffer_view->size, 16));
        prepared.patches.image_types.emplace_back(i, sniff(head));
    }
    prepared.bytes = std::move(bytes);
    prepared.buffers = loader.store;
    return prepared;
}

namespace {
// Appends the rest animation: for each node channel that gltfio applies in some clip, a linear
// sampler from 0 to 1 s that holds the authored value at both keyframes. Applying it at time 0
// restores those channels, and only those, in gltfio's own transform state. It also records
// which clips write every such channel themselves.
void append_rest_animation(cgltf_data* data, Prepared& prepared) {
    auto& tables = *prepared.tables;
    using Path = cgltf_animation_path_type;
    using Key = std::pair<size_t, Path>;
    auto targets = [&](const cgltf_node& node) -> size_t {
        return node.mesh && node.mesh->primitives_count ? node.mesh->primitives[0].targets_count : 0;
    };
    // gltfio skips channels whose sampler has fewer than two keyframes.
    auto applied = [&](const cgltf_animation_channel& channel) {
        if (!channel.target_node || !channel.sampler || !channel.sampler->input || channel.sampler->input->count < 2)
            return false;
        const auto& node = *channel.target_node;
        switch (channel.target_path) {
            case cgltf_animation_path_type_translation:
            case cgltf_animation_path_type_rotation:
            case cgltf_animation_path_type_scale: return !node.has_matrix;
            case cgltf_animation_path_type_weights: return targets(node) > 0;
            default: return false;
        }
    };
    std::vector<std::set<Key>> clips(data->animations_count);
    std::set<Key> all;
    for (size_t a = 0; a < data->animations_count; ++a) {
        const auto& animation = data->animations[a];
        for (size_t c = 0; c < animation.channels_count; ++c) {
            const auto& channel = animation.channels[c];
            if (!applied(channel)) continue;
            const Key key{cgltf_node_index(data, channel.target_node), channel.target_path};
            clips[a].insert(key);
            all.insert(key);
        }
    }
    for (size_t a = 0; a < data->animations_count && a < tables.animations.size(); ++a)
        tables.animations[a].needs_rest = clips[a] != all;
    if (all.empty()) return;

    auto rest = std::make_unique<RestSource>();
    auto& floats = rest->floats;
    floats = {0, 1};
    struct Range { size_t offset, count; cgltf_type type; };
    std::vector<Range> ranges;
    for (const auto& [index, path] : all) {
        const auto& node = data->nodes[index];
        std::vector<float> value;
        cgltf_type type = cgltf_type_scalar;
        if (path == cgltf_animation_path_type_translation) { value.assign(node.translation, node.translation + 3); type = cgltf_type_vec3; }
        else if (path == cgltf_animation_path_type_rotation) { value.assign(node.rotation, node.rotation + 4); type = cgltf_type_vec4; }
        else if (path == cgltf_animation_path_type_scale) { value.assign(node.scale, node.scale + 3); type = cgltf_type_vec3; }
        else {
            // The weights that initialize_model() restores.
            value.assign(targets(node), 0.0f);
            if (index < tables.nodes.size() && tables.nodes[index].weights.size() == value.size())
                value = tables.nodes[index].weights;
        }
        const size_t components = type == cgltf_type_scalar ? 1 : value.size();
        ranges.push_back({floats.size() * sizeof(float), 2 * value.size() / components, type});
        floats.insert(floats.end(), value.begin(), value.end());
        floats.insert(floats.end(), value.begin(), value.end());
    }
    rest->buffer.size = floats.size() * sizeof(float);
    rest->buffer.data = floats.data();
    rest->buffer.data_free_method = cgltf_data_free_method_none;
    rest->view.buffer = &rest->buffer;
    rest->view.size = rest->buffer.size;
    auto accessor = [&](size_t offset, size_t count, cgltf_type type) {
        cgltf_accessor result{};
        result.component_type = cgltf_component_type_r_32f;
        result.type = type;
        result.offset = offset;
        result.count = count;
        result.stride = cgltf_calc_size(type, cgltf_component_type_r_32f);
        result.buffer_view = &rest->view;
        return result;
    };
    rest->accessors.reserve(ranges.size() + 1);
    rest->accessors.push_back(accessor(0, 2, cgltf_type_scalar));
    for (const auto& range : ranges) rest->accessors.push_back(accessor(range.offset, range.count, range.type));

    // cgltf_free releases these arrays with the parse's allocator.
    auto allocate = [&](size_t size) {
        void* result = data->memory.alloc_func(data->memory.user_data, size);
        if (!result) throw std::bad_alloc();
        std::memset(result, 0, size);
        return result;
    };
    const size_t count = all.size();
    auto* samplers = static_cast<cgltf_animation_sampler*>(allocate(count * sizeof(cgltf_animation_sampler)));
    cgltf_animation_channel* channels = nullptr;
    cgltf_animation* animations = nullptr;
    try {
        channels = static_cast<cgltf_animation_channel*>(allocate(count * sizeof(cgltf_animation_channel)));
        animations = static_cast<cgltf_animation*>(allocate((data->animations_count + 1) * sizeof(cgltf_animation)));
    } catch (...) {
        data->memory.free_func(data->memory.user_data, samplers);
        data->memory.free_func(data->memory.user_data, channels);
        throw;
    }
    size_t k = 0;
    for (const auto& [index, path] : all) {
        samplers[k].input = &rest->accessors[0];
        samplers[k].output = &rest->accessors[k + 1];
        samplers[k].interpolation = cgltf_interpolation_type_linear;
        channels[k].sampler = &samplers[k];
        channels[k].target_node = &data->nodes[index];
        channels[k].target_path = path;
        ++k;
    }
    if (data->animations_count) std::memcpy(animations, data->animations, data->animations_count * sizeof(cgltf_animation));
    auto& added = animations[data->animations_count];
    added.samplers = samplers;
    added.samplers_count = count;
    added.channels = channels;
    added.channels_count = count;
    data->memory.free_func(data->memory.user_data, data->animations);
    data->animations = animations;
    tables.rest_animation = int(data->animations_count);
    ++data->animations_count;
    prepared.buffers->rest = std::move(rest);
}
}

void patch_source(cgltf_data* data, Prepared& prepared) {
    attach(data, *prepared.buffers);
    const auto& patches = prepared.patches;
    for (const auto view : patches.decoded_views) data->buffer_views[view].has_meshopt_compression = false;
    for (const auto& channel : patches.node_channels) {
        auto& target = data->animations[channel.animation].channels[channel.channel];
        target.target_node = &data->nodes[channel.node];
        target.target_path = channel.path;
    }
    for (const auto& sampler : patches.quiet_samplers) {
        auto& target = data->animations[sampler.animation].samplers[sampler.sampler];
        target.input = target.output = &data->accessors[sampler.accessor];
    }
    for (const auto& [index, mime] : patches.image_types) {
        // cgltf_free releases this string with the parse's allocator, which is the C runtime's.
        auto* copy = static_cast<char*>(std::malloc(mime.size() + 1));
        if (!copy) throw std::bad_alloc();
        std::memcpy(copy, mime.c_str(), mime.size() + 1);
        std::free(data->images[index].mime_type);
        data->images[index].mime_type = copy;
    }
    append_rest_animation(data, prepared);
}

std::vector<uint8_t> mesh_placeholder(const std::array<float, 3>& low, const std::array<float, 3>& high,
                                      bool colors, const MeshMaterial& material) {
    static const char* modes[][2] = {{"opaque", "OPAQUE"}, {"mask", "MASK"}, {"blend", "BLEND"}};
    const char* mode = nullptr;
    for (const auto& entry : modes) if (material.alpha_mode == entry[0]) mode = entry[1];
    if (!mode) throw std::invalid_argument("alpha_mode must be 'opaque', 'mask', or 'blend', got '" + material.alpha_mode + "'");
    auto unit = [](const char* name, float value) {
        if (!(std::isfinite(value) && value >= 0 && value <= 1)) throw std::invalid_argument(std::string(name) + " values must be in [0, 1]");
        return value;
    };
    for (float value : material.base_color) unit("base_color", value);
    unit("metallic", material.metallic);
    unit("roughness", material.roughness);
    for (float value : material.emissive)
        if (!(std::isfinite(value) && value >= 0)) throw std::invalid_argument("emissive values must be finite and nonnegative");
    // Accessor bounds become the model's load-time bounds, so the triangle spans the mesh.
    std::vector<float> floats = {low[0], low[1], low[2], high[0], high[1], high[2], low[0], low[1], low[2],
                                 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0};
    if (colors) floats.insert(floats.end(), 12, 1.0f);
    using json::number;
    auto list = [](std::initializer_list<double> values) {
        json::Value result = json::array();
        for (double value : values) result.items.push_back(number(value));
        return result;
    };
    auto accessor = [&](int view, const char* type, bool bounds) {
        json::Value result = json::object();
        result.at("bufferView") = number(double(view));
        result.at("componentType") = number(5126.0);
        result.at("count") = number(3.0);
        result.at("type") = json::string(type);
        if (bounds) {
            result.at("min") = list({low[0], low[1], low[2]});
            result.at("max") = list({high[0], high[1], high[2]});
        }
        return result;
    };
    json::Value attributes = json::object();
    attributes.at("POSITION") = number(0.0);
    attributes.at("NORMAL") = number(1.0);
    attributes.at("TEXCOORD_0") = number(2.0);
    json::Value accessors = json::array();
    accessors.items = {accessor(0, "VEC3", true), accessor(1, "VEC3", false), accessor(2, "VEC2", false)};
    json::Value views = json::array();
    auto view = [&](double offset, double size) {
        json::Value result = json::object();
        result.at("buffer") = number(0.0);
        result.at("byteOffset") = number(offset);
        result.at("byteLength") = number(size);
        views.items.push_back(std::move(result));
    };
    view(0, 36); view(36, 36); view(72, 24);
    if (colors) {
        attributes.at("COLOR_0") = number(3.0);
        view(96, 48);
        accessors.items.push_back(accessor(3, "VEC4", false));
    }
    json::Value pbr = json::object();
    pbr.at("baseColorFactor") = list({material.base_color[0], material.base_color[1], material.base_color[2], material.base_color[3]});
    pbr.at("metallicFactor") = number(material.metallic);
    pbr.at("roughnessFactor") = number(material.roughness);
    json::Value mat = json::object();
    mat.at("name") = json::string("mesh");
    mat.at("pbrMetallicRoughness") = std::move(pbr);
    mat.at("doubleSided").type = json::Value::Type::boolean;
    mat.at("doubleSided").boolean = material.double_sided;
    mat.at("alphaMode") = json::string(mode);
    json::Value used = json::array();
    const auto& e = material.emissive;
    // Factors in [0, 1] with the rest in emissive_strength, computed in double as before.
    const double strength = std::max({double(e[0]), double(e[1]), double(e[2]), 1.0});
    if (e[0] || e[1] || e[2]) mat.at("emissiveFactor") = list({e[0] / strength, e[1] / strength, e[2] / strength});
    if (strength > 1) {
        mat.at("extensions").type = json::Value::Type::object;
        mat.at("extensions").at("KHR_materials_emissive_strength").at("emissiveStrength") = number(strength);
        used.items.push_back(json::string("KHR_materials_emissive_strength"));
    }
    if (material.unlit) {
        mat.at("extensions").type = json::Value::Type::object;
        mat.at("extensions").at("KHR_materials_unlit").type = json::Value::Type::object;
        used.items.push_back(json::string("KHR_materials_unlit"));
    }
    json::Value doc = json::object();
    doc.at("asset").at("version") = json::string("2.0");
    doc.at("asset").type = json::Value::Type::object;
    doc.at("asset").at("generator") = json::string("filly create_mesh");
    doc.at("scene") = number(0.0);
    json::Value scene = json::object();
    scene.at("nodes") = list({0});
    doc.at("scenes") = json::array();
    doc.at("scenes").items.push_back(std::move(scene));
    json::Value node = json::object();
    node.at("mesh") = number(0.0);
    node.at("name") = json::string("mesh");
    doc.at("nodes") = json::array();
    doc.at("nodes").items.push_back(std::move(node));
    json::Value primitive = json::object();
    primitive.at("attributes") = std::move(attributes);
    primitive.at("material") = number(0.0);
    json::Value mesh = json::object();
    mesh.at("name") = json::string("mesh");
    mesh.at("primitives") = json::array();
    mesh.at("primitives").items.push_back(std::move(primitive));
    doc.at("meshes") = json::array();
    doc.at("meshes").items.push_back(std::move(mesh));
    doc.at("materials") = json::array();
    doc.at("materials").items.push_back(std::move(mat));
    json::Value buffer = json::object();
    buffer.at("byteLength") = number(double(floats.size() * 4));
    doc.at("buffers") = json::array();
    doc.at("buffers").items.push_back(std::move(buffer));
    doc.at("bufferViews") = std::move(views);
    doc.at("accessors") = std::move(accessors);
    if (!used.items.empty()) doc.at("extensionsUsed") = std::move(used);
    std::string encoded = json::write(doc);
    encoded.append((4 - encoded.size() % 4) % 4, ' ');
    std::vector<uint8_t> bytes;
    auto put = [&](uint32_t value) { for (int i = 0; i < 4; ++i) bytes.push_back(uint8_t(value >> (8 * i))); };
    const size_t binary = floats.size() * 4;
    put(0x46546C67); put(2); put(uint32_t(28 + encoded.size() + binary));
    put(uint32_t(encoded.size())); put(0x4E4F534A);
    bytes.insert(bytes.end(), encoded.begin(), encoded.end());
    put(uint32_t(binary)); put(0x004E4942);
    const auto* first = reinterpret_cast<const uint8_t*>(floats.data());
    bytes.insert(bytes.end(), first, first + binary);
    return bytes;
}

}
