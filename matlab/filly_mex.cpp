// MATLAB and Octave gateway over the C++ facade in native/renderer.h. The gateway converts types
// and keeps the handle table; behavior belongs in the core, so that both frontends share it.
// Only the C MEX API is used: the same file builds with MATLAB's mex and with Octave's
// mkoctfile --mex.
//
// Conventions of the MATLAB API (see docs/how-to/matlab.md):
// - Positions in a list are 1-based: mesh triangle indices, animation and variant indices,
//   material slots. glTF node indices are identifiers from the file and stay 0-based.
// - Matrices are 4 x 4, column-major as MATLAB stores them, with the same mathematics as the
//   Python API's row-major arrays.
// - Images are H x W x C, first row at the top, as in the Python API.
#include "renderer.h"

#include <mex.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>
#include <unordered_map>
#include <variant>
#include <vector>

#if defined(octave_mexproto_h)
#define FILLY_OCTAVE 1
#endif

#if defined(__linux__) && !defined(FILLY_OCTAVE)
#include <dlfcn.h>
#endif

using namespace filly;

namespace {

using Object = std::variant<Renderer, Scene, Camera, Model, Node, Light, Material, Asset, Environment, Texture,
                            ImportedTexture, RenderTarget, ImportedTarget, PickQuery, Preparation>;
constexpr const char* kind_names[] = {"Renderer", "Scene", "Camera", "Model", "Node", "Light", "Material", "Asset",
                                      "Environment", "Texture", "ImportedTexture", "RenderTarget", "ImportedTarget",
                                      "PickQuery", "Preparation"};
template <class T, class... Ts> constexpr size_t index_in(std::variant<Ts...>*) {
    constexpr bool matches[] = {std::is_same_v<T, Ts>...};
    for (size_t i = 0; i < sizeof...(Ts); ++i) if (matches[i]) return i;
    return SIZE_MAX;
}
template <class T> constexpr size_t kind = index_in<T>(static_cast<Object*>(nullptr));

// Handles are never reused, so a handle from a deleted MATLAB object is an error, not a different
// object.
struct Table {
    std::unordered_map<uint64_t, Object> objects;
    uint64_t next = 1;
    // Every renderer that the gateway created, so that the exit handler can close them first.
    std::vector<Renderer> renderers;
};
// Never destroyed: destroying a Filament engine joins its threads, which must not happen under
// the loader lock when the DLL unloads. The exit handler empties the table instead.
Table& table() {
    static auto* value = new Table();
    return *value;
}

// Reused between calls so that per-frame conversions do not allocate. Handles in `nodes` and
// `materials` are cleared after each call, so that they do not keep models alive.
struct Scratch {
    std::vector<float> positions, normals, uvs, colors, floats;
    std::vector<uint32_t> indices;
    std::vector<uint8_t> bytes;
    std::vector<Node> nodes;
    std::vector<Material> materials;
};
Scratch& scratch() {
    static auto* value = new Scratch();
    return *value;
}

[[noreturn]] void invalid(const std::string& message) { throw std::invalid_argument(message); }

// ---------------------------------------------------------------- input conversions

void require_numeric(const mxArray* a, const char* name) {
    if (!(mxIsNumeric(a) || mxIsLogical(a)) || mxIsComplex(a)) invalid(std::string(name) + " must be real and numeric");
}
double element(const mxArray* a, size_t i) {
    const void* p = mxGetData(a);
    switch (mxGetClassID(a)) {
    case mxDOUBLE_CLASS: return static_cast<const double*>(p)[i];
    case mxSINGLE_CLASS: return static_cast<const float*>(p)[i];
    case mxLOGICAL_CLASS: return static_cast<const mxLogical*>(p)[i];
    case mxINT8_CLASS: return static_cast<const int8_t*>(p)[i];
    case mxUINT8_CLASS: return static_cast<const uint8_t*>(p)[i];
    case mxINT16_CLASS: return static_cast<const int16_t*>(p)[i];
    case mxUINT16_CLASS: return static_cast<const uint16_t*>(p)[i];
    case mxINT32_CLASS: return static_cast<const int32_t*>(p)[i];
    case mxUINT32_CLASS: return static_cast<const uint32_t*>(p)[i];
    case mxINT64_CLASS: return double(static_cast<const int64_t*>(p)[i]);
    case mxUINT64_CLASS: return double(static_cast<const uint64_t*>(p)[i]);
    default: invalid("Expected a numeric value");
    }
}
double number(const mxArray* a, const char* name) {
    require_numeric(a, name);
    if (mxGetNumberOfElements(a) != 1) invalid(std::string(name) + " must be a scalar");
    return element(a, 0);
}
float single(const mxArray* a, const char* name) { return float(number(a, name)); }
int64_t whole(const mxArray* a, const char* name) {
    const double value = number(a, name);
    if (!std::isfinite(value) || value != std::floor(value)) invalid(std::string(name) + " must be a whole number");
    // Out-of-range values reach the core's own range checks with their usual messages.
    if (value >= 9.2e18) return std::numeric_limits<int64_t>::max();
    if (value <= -9.2e18) return std::numeric_limits<int64_t>::min();
    return int64_t(value);
}
bool flag(const mxArray* a, const char* name) { return number(a, name) != 0; }
std::optional<double> maybe(const mxArray* a, const char* name) {
    if (mxIsEmpty(a)) return std::nullopt;
    return number(a, name);
}

#if !defined(FILLY_OCTAVE)
std::string utf8(const mxChar* s, size_t n) {
    std::string out;
    out.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        uint32_t c = uint16_t(s[i]);
        if (c >= 0xD800 && c < 0xDC00 && i + 1 < n && uint16_t(s[i + 1]) >= 0xDC00 && uint16_t(s[i + 1]) < 0xE000)
            c = 0x10000 + ((c - 0xD800) << 10) + (uint16_t(s[++i]) - 0xDC00);
        if (c < 0x80) {
            out += char(c);
        } else if (c < 0x800) {
            out += char(0xC0 | (c >> 6));
            out += char(0x80 | (c & 0x3F));
        } else if (c < 0x10000) {
            out += char(0xE0 | (c >> 12));
            out += char(0x80 | ((c >> 6) & 0x3F));
            out += char(0x80 | (c & 0x3F));
        } else {
            out += char(0xF0 | (c >> 18));
            out += char(0x80 | ((c >> 12) & 0x3F));
            out += char(0x80 | ((c >> 6) & 0x3F));
            out += char(0x80 | (c & 0x3F));
        }
    }
    return out;
}
#endif
// MATLAB stores char as UTF-16 and Octave as UTF-8 bytes; the core takes UTF-8.
std::string text(const mxArray* a, const char* name) {
    if (!mxIsChar(a) || (mxGetM(a) > 1 && mxGetN(a) > 1)) invalid(std::string(name) + " must be a char vector");
    const size_t n = mxGetNumberOfElements(a);
    if (n == 0) return {};
#if defined(FILLY_OCTAVE)
    return std::string(reinterpret_cast<const char*>(mxGetChars(a)), n);
#else
    return utf8(mxGetChars(a), n);
#endif
}
// Command and property names are ASCII; a stack buffer avoids an allocation per call.
struct Name {
    char value[64];
    std::string_view view;
};
void read_name(const mxArray* a, Name& name, const char* what) {
    if (!mxIsChar(a) || mxGetString(a, name.value, sizeof(name.value)) != 0)
        invalid(std::string(what) + " must be a char vector of at most 63 characters");
    name.view = name.value;
}
template <size_t N> std::array<float, N> vec(const mxArray* a, const char* name) {
    require_numeric(a, name);
    if (mxGetNumberOfElements(a) != N) invalid(std::string(name) + " must have " + std::to_string(N) + " elements");
    std::array<float, N> result;
    for (size_t i = 0; i < N; ++i) result[i] = float(element(a, i));
    return result;
}
// MATLAB's column-major 4 x 4 array into the facade's row-major matrix, with the same mathematics.
Matrix matrix_in(const mxArray* a, const char* name) {
    require_numeric(a, name);
    if (mxGetNumberOfDimensions(a) != 2 || mxGetM(a) != 4 || mxGetN(a) != 4) invalid(std::string(name) + " must be 4 x 4");
    Matrix result;
    for (size_t row = 0; row < 4; ++row)
        for (size_t col = 0; col < 4; ++col) result[row * 4 + col] = float(element(a, col * 4 + row));
    return result;
}
uint64_t handle_id(const mxArray* a) {
    if (!mxIsUint64(a) || mxGetNumberOfElements(a) != 1) invalid("Expected a filly handle (a uint64 scalar)");
    return *static_cast<const uint64_t*>(mxGetData(a));
}
Object& lookup(uint64_t id) {
    auto found = table().objects.find(id);
    if (found == table().objects.end())
        throw FillyError("Invalid filly handle: its object was deleted or belongs to an earlier session");
    return found->second;
}
Object& lookup(const mxArray* a) { return lookup(handle_id(a)); }
template <class T> T& get(Object& object) {
    if (auto* value = std::get_if<T>(&object)) return *value;
    invalid(std::string("Expected a ") + kind_names[kind<T>] + " handle, got a " + kind_names[object.index()]);
}
template <class T> T& get(const mxArray* a) { return get<T>(lookup(a)); }

// ---------------------------------------------------------------- output conversions

mxArray* id_out(uint64_t id) {
    auto* result = mxCreateNumericMatrix(1, 1, mxUINT64_CLASS, mxREAL);
    *static_cast<uint64_t*>(mxGetData(result)) = id;
    return result;
}
template <class T> mxArray* put(T value) {
    auto& t = table();
    const auto id = t.next++;
    t.objects.emplace(id, Object(std::move(value)));
    return id_out(id);
}
template <class T> mxArray* put_all(std::vector<T> values) {
    auto* result = mxCreateNumericMatrix(1, values.size(), mxUINT64_CLASS, mxREAL);
    auto* ids = static_cast<uint64_t*>(mxGetData(result));
    auto& t = table();
    for (size_t i = 0; i < values.size(); ++i) {
        ids[i] = t.next++;
        t.objects.emplace(ids[i], Object(std::move(values[i])));
    }
    return result;
}
mxArray* number_out(double value) { return mxCreateDoubleScalar(value); }
mxArray* bool_out(bool value) { return mxCreateLogicalScalar(value); }
mxArray* empty_out() { return mxCreateDoubleMatrix(0, 0, mxREAL); }
mxArray* text_out(std::string_view s) {
#if defined(FILLY_OCTAVE)
    const std::string copy(s);
    return mxCreateString(copy.c_str());
#else
    std::u16string units;
    units.reserve(s.size());
    for (size_t i = 0; i < s.size();) {
        const auto byte = uint8_t(s[i]);
        uint32_t c = byte;
        size_t extra = 0;
        if (byte >= 0xF0) { c = byte & 0x07; extra = 3; }
        else if (byte >= 0xE0) { c = byte & 0x0F; extra = 2; }
        else if (byte >= 0xC0) { c = byte & 0x1F; extra = 1; }
        ++i;
        for (size_t k = 0; k < extra && i < s.size(); ++k, ++i) c = (c << 6) | (uint8_t(s[i]) & 0x3F);
        if (c >= 0x10000) {
            c -= 0x10000;
            units += char16_t(0xD800 + (c >> 10));
            units += char16_t(0xDC00 + (c & 0x3FF));
        } else {
            units += char16_t(c);
        }
    }
    const mwSize dims[2] = {1, mwSize(units.size())};
    auto* result = mxCreateCharArray(2, dims);
    std::memcpy(mxGetChars(result), units.data(), units.size() * sizeof(char16_t));
    return result;
#endif
}
mxArray* maybe_text(const std::optional<std::string>& value) { return value ? text_out(*value) : empty_out(); }
mxArray* texts_out(const std::vector<std::string>& values) {
    auto* result = mxCreateCellMatrix(1, values.size());
    for (size_t i = 0; i < values.size(); ++i) mxSetCell(result, i, text_out(values[i]));
    return result;
}
mxArray* row_out(const float* values, size_t n) {
    auto* result = mxCreateDoubleMatrix(1, n, mxREAL);
    auto* out = static_cast<double*>(mxGetData(result));
    for (size_t i = 0; i < n; ++i) out[i] = values[i];
    return result;
}
template <size_t N> mxArray* row_out(const std::array<float, N>& values) { return row_out(values.data(), N); }
template <size_t N> mxArray* maybe_row(const std::optional<std::array<float, N>>& values) {
    return values ? row_out(*values) : empty_out();
}
mxArray* matrix_out(const Matrix& m) {
    auto* result = mxCreateDoubleMatrix(4, 4, mxREAL);
    auto* out = static_cast<double*>(mxGetData(result));
    for (size_t row = 0; row < 4; ++row)
        for (size_t col = 0; col < 4; ++col) out[col * 4 + row] = m[row * 4 + col];
    return result;
}
// Rows are the minimum and the maximum corner.
mxArray* box_out(const std::array<Vec3, 2>& box) {
    auto* result = mxCreateDoubleMatrix(2, 3, mxREAL);
    auto* out = static_cast<double*>(mxGetData(result));
    for (size_t row = 0; row < 2; ++row)
        for (size_t col = 0; col < 3; ++col) out[col * 2 + row] = box[row][col];
    return result;
}
mxArray* struct_out(std::initializer_list<std::pair<const char*, mxArray*>> fields) {
    std::array<const char*, 16> names{};
    size_t count = 0;
    for (const auto& field : fields) names[count++] = field.first;
    auto* result = mxCreateStructMatrix(1, 1, int(count), names.data());
    for (const auto& field : fields) mxSetField(result, 0, field.first, field.second);
    return result;
}

// ---------------------------------------------------------------- arrays

// Copies between MATLAB's planar, column-major H x W x C layout and the core's interleaved rows
// from the top, in one pass. Blocks of rows keep both sides of the copy in a few cache lines.
constexpr size_t copy_block = 64;
template <class E> void planar_to_rows(const E* in, E* out, size_t h, size_t w, size_t c) {
    const size_t plane = h * w;
    for (size_t y0 = 0; y0 < h; y0 += copy_block) {
        const size_t y1 = std::min(h, y0 + copy_block);
        for (size_t x = 0; x < w; ++x)
            for (size_t y = y0; y < y1; ++y) {
                E* dst = out + (y * w + x) * c;
                const E* src = in + x * h + y;
                for (size_t k = 0; k < c; ++k) dst[k] = src[k * plane];
            }
    }
}
template <class E> void rows_to_planar(const E* in, E* out, size_t h, size_t w, size_t c) {
    const size_t plane = h * w;
    for (size_t y0 = 0; y0 < h; y0 += copy_block) {
        const size_t y1 = std::min(h, y0 + copy_block);
        for (size_t x = 0; x < w; ++x)
            for (size_t y = y0; y < y1; ++y) {
                const E* src = in + (y * w + x) * c;
                E* dst = out + x * h + y;
                for (size_t k = 0; k < c; ++k) dst[k * plane] = src[k];
            }
    }
}
struct ImageShape {
    size_t height, width, channels;
    bool is_float;
};
ImageShape image_shape(const mxArray* a, const char* name) {
    if (mxIsComplex(a) || !(mxIsUint8(a) || mxIsSingle(a)))
        invalid(std::string(name) + " must be uint8 or single; other types would need a hidden conversion");
    const auto dims = mxGetDimensions(a);
    const auto n = mxGetNumberOfDimensions(a);
    if (n > 3) invalid(std::string(name) + " must be H x W or H x W x C");
    return {size_t(dims[0]), size_t(dims[1]), n == 3 ? size_t(dims[2]) : 1, bool(mxIsSingle(a))};
}
void image_to_rows(const mxArray* a, const ImageShape& shape, uint8_t* out) {
    if (shape.is_float)
        planar_to_rows(static_cast<const float*>(mxGetData(a)), reinterpret_cast<float*>(out), shape.height, shape.width, shape.channels);
    else
        planar_to_rows(static_cast<const uint8_t*>(mxGetData(a)), out, shape.height, shape.width, shape.channels);
}
// An R x columns array (double or single) as interleaved float rows.
const float* rows_in(const mxArray* a, size_t columns, size_t rows, const char* name, std::vector<float>& out) {
    if (mxIsComplex(a) || !(mxIsDouble(a) || mxIsSingle(a))) invalid(std::string(name) + " must be double or single");
    if (mxGetNumberOfDimensions(a) != 2 || mxGetN(a) != columns || (rows != SIZE_MAX && mxGetM(a) != rows))
        invalid(std::string(name) + " must be " + (rows == SIZE_MAX ? std::string("N") : std::to_string(rows)) + " x "
                + std::to_string(columns));
    const size_t m = mxGetM(a);
    out.resize(m * columns);
    if (mxIsDouble(a)) {
        const auto* in = static_cast<const double*>(mxGetData(a));
        for (size_t c = 0; c < columns; ++c) for (size_t r = 0; r < m; ++r) out[r * columns + c] = float(in[c * m + r]);
    } else {
        const auto* in = static_cast<const float*>(mxGetData(a));
        for (size_t c = 0; c < columns; ++c) for (size_t r = 0; r < m; ++r) out[r * columns + c] = in[c * m + r];
    }
    return out.data();
}
mxArray* rows_out(const float* in, size_t rows, size_t columns) {
    auto* result = mxCreateDoubleMatrix(rows, columns, mxREAL);
    auto* out = static_cast<double*>(mxGetData(result));
    for (size_t r = 0; r < rows; ++r) for (size_t c = 0; c < columns; ++c) out[c * rows + r] = in[r * columns + c];
    return result;
}
// A borrowed view of a double or single array for the core's batch calls.
ValueArray values_in(const mxArray* a, const char* name) {
    if (mxIsComplex(a) || !(mxIsDouble(a) || mxIsSingle(a))) invalid(std::string(name) + " must be double or single");
    ValueArray result;
    result.data = mxGetData(a);
    result.is_double = mxIsDouble(a);
    return result;
}
// Fills a scratch list of facade objects from a vector of handles; cleared when it goes.
template <class T> struct Batch {
    std::vector<T>& items;
    Batch(std::vector<T>& storage, const mxArray* ids, const char* name) : items(storage) {
        items.clear();
        if (!mxIsUint64(ids)) invalid(std::string(name) + " must be a vector of filly handles");
        const auto* values = static_cast<const uint64_t*>(mxGetData(ids));
        // A throwing constructor skips the destructor, so a bad handle must not leave the items
        // read so far holding their models.
        try {
            for (size_t i = 0; i < mxGetNumberOfElements(ids); ++i) items.push_back(get<T>(lookup(values[i])));
        } catch (...) {
            items.clear();
            throw;
        }
    }
    ~Batch() { items.clear(); }
};

// ---------------------------------------------------------------- calls

struct Call {
    int nlhs;
    mxArray** plhs;
    int nrhs;
    const mxArray** prhs;
    std::string_view command;
    const mxArray* operator[](int i) const {
        if (i >= nrhs) invalid(std::string(command) + " needs argument " + std::to_string(i + 1));
        return prhs[i];
    }
    bool given(int i) const { return i < nrhs && !mxIsEmpty(prhs[i]); }
    void out(int i, mxArray* value) {
        if (i == 0 || i < nlhs) plhs[i] = value;
        else mxDestroyArray(value);
    }
};

template <class Result, class Load> void load_source(Call& c, int index, bool strict, Load load) {
    std::vector<std::string> warnings;
    const mxArray* source = c[index];
    std::optional<Result> result;
    if (mxIsUint8(source)) {
        result = load(static_cast<const uint8_t*>(mxGetData(source)), mxGetNumberOfElements(source), strict, warnings);
    } else {
        result = load(text(source, "source"), strict, warnings);
    }
    c.out(0, put(std::move(*result)));
    c.out(1, texts_out(warnings));
}

MeshArrays mesh_arrays(Call& c, int first, size_t vertices) {
    auto& s = scratch();
    MeshArrays arrays;
    arrays.vertices = vertices;
    if (c.given(first)) arrays.positions = rows_in(c[first], 3, vertices, "positions", s.positions);
    if (c.given(first + 1)) arrays.normals = rows_in(c[first + 1], 3, vertices, "normals", s.normals);
    if (c.given(first + 2)) arrays.uvs = rows_in(c[first + 2], 2, vertices, "uvs", s.uvs);
    if (c.given(first + 3)) arrays.colors = rows_in(c[first + 3], 4, vertices, "colors", s.colors);
    return arrays;
}

RenderOptions render_options(Call& c, int first, std::optional<Camera>& camera) {
    RenderOptions options;
    if (c.given(first)) {
        camera = get<Camera>(c[first]);
        options.camera = &*camera;
    }
    if (c.given(first + 1)) {
        const auto* viewport = c[first + 1];
        require_numeric(viewport, "viewport");
        if (mxGetNumberOfElements(viewport) != 4) invalid("viewport must be [x y width height]");
        options.has_viewport = true;
        for (size_t i = 0; i < 4; ++i) {
            const double value = element(viewport, i);
            if (!std::isfinite(value) || value != std::floor(value)) invalid("viewport values must be whole numbers");
            options.viewport[i] = int64_t(value);
        }
    }
    if (c.nrhs > first + 2) options.clear = flag(c[first + 2], "clear");
    return options;
}

mxArray* stats_out(const Stats& s) {
    return struct_out({{"cpuSubmitMs", number_out(s.cpu_submit_ms)}, {"hostWaitMs", number_out(s.host_wait_ms)},
                       {"hostReleaseMs", number_out(s.host_release_ms)}, {"finishMs", number_out(s.finish_ms)},
                       {"readbackMs", number_out(s.readback_ms)}, {"liveModels", number_out(double(s.live_models))},
                       {"liveLights", number_out(double(s.live_lights))},
                       {"materialCopies", number_out(double(s.material_copies))},
                       {"framesRendered", number_out(double(s.frames_rendered))},
                       {"gpuFrameMs", s.gpu_frame_ms ? number_out(*s.gpu_frame_ms) : empty_out()},
                       {"gpuFrameNumber", s.gpu_frame_number ? number_out(double(*s.gpu_frame_number)) : empty_out()}});
}
mxArray* memory_out(const AssetMemory& m) {
    return struct_out({{"gpuTextureBytes", number_out(double(m.gpu_texture_bytes))},
                       {"gpuGeometryBytes", number_out(double(m.gpu_geometry_bytes))},
                       {"gpuBytes", number_out(double(m.gpu_texture_bytes + m.gpu_geometry_bytes))},
                       {"cpuBytes", number_out(double(m.cpu_bytes))},
                       {"instanceGpuBytes", number_out(double(m.instance_gpu_bytes))},
                       {"instanceCpuBytes", number_out(double(m.instance_cpu_bytes))},
                       {"models", number_out(double(m.models))}});
}
mxArray* shape_out(ShapeArrays&& shape) {
    const size_t vertices = shape.positions.size() / 3, triangles = shape.indices.size() / 3;
    auto* indices = mxCreateDoubleMatrix(triangles, 3, mxREAL);
    auto* out = static_cast<double*>(mxGetData(indices));
    for (size_t r = 0; r < triangles; ++r)
        for (size_t c = 0; c < 3; ++c) out[c * triangles + r] = double(shape.indices[r * 3 + c]) + 1;
    return struct_out({{"positions", rows_out(shape.positions.data(), vertices, 3)},
                       {"normals", rows_out(shape.normals.data(), vertices, 3)},
                       {"uvs", rows_out(shape.uvs.data(), vertices, 2)}, {"indices", indices}});
}
FrameOptions frame_options(Call& c, int first) {
    FrameOptions options;
    if (c.given(first)) options.fill = number(c[first], "fill");
    if (c.given(first + 1)) options.direction = vec<3>(c[first + 1], "direction");
    if (c.given(first + 2)) options.up = vec<3>(c[first + 2], "up");
    if (c.given(first + 3)) options.fit = text(c[first + 3], "fit");
    if (c.given(first + 4)) options.near = number(c[first + 4], "near");
    if (c.given(first + 5)) options.far = number(c[first + 5], "far");
    if (c.given(first + 6)) {
        const double aspect = number(c[first + 6], "aspect");
        if (!std::isfinite(aspect) || aspect <= 0) invalid("aspect must be positive, or [] for the target aspect");
        options.aspect = aspect;
    }
    return options;
}
double aspect_in(Call& c, int index) {
    if (!c.given(index)) return 0;
    const double aspect = number(c[index], "aspect");
    if (!std::isfinite(aspect) || aspect <= 0) invalid("aspect must be positive, or [] for the target aspect");
    return aspect;
}
// Model keys: a name, or a glTF index (0-based, as in the file).
template <class ByName, class ByIndex> auto by_key(const mxArray* key, ByName by_name, ByIndex by_index) {
    if (mxIsChar(key)) return by_name(text(key, "key"));
    return by_index(whole(key, "key"));
}
// Keys into a model's list (animations, variants): a name, or a 1-based position.
template <class ByName, class ByIndex> auto by_position(const mxArray* key, ByName by_name, ByIndex by_index) {
    if (mxIsChar(key)) return by_name(text(key, "key"));
    const auto position = whole(key, "key");
    if (position < 1) invalid("Indices are 1-based");
    return by_index(position - 1);
}
MaterialParameter material_parameter(std::string_view name) {
    if (name == "baseColor") return MaterialParameter::base_color;
    if (name == "metallic") return MaterialParameter::metallic;
    if (name == "roughness") return MaterialParameter::roughness;
    if (name == "emissive") return MaterialParameter::emissive;
    invalid("Material parameter must be 'baseColor', 'metallic', 'roughness', or 'emissive'");
}
mxArray* texture_kind(const std::shared_ptr<detail::TextureData>& data) {
    if (!data) return empty_out();
    return text_out(is_imported_texture(data) ? "ImportedTexture" : "Texture");
}
mxArray* texture_id(std::shared_ptr<detail::TextureData> data) {
    if (!data) return empty_out();
    if (is_imported_texture(data)) return put(ImportedTexture(std::move(data)));
    return put(Texture(std::move(data)));
}

// ---------------------------------------------------------------- properties

template <class T> struct Property {
    mxArray* (*get)(T&);
    void (*set)(T&, const mxArray*);
};
template <class T> using Properties = std::unordered_map<std::string_view, Property<T>>;
template <class T> const Properties<T>& properties() {
    static const Properties<T> none;
    return none;
}

template <> const Properties<Renderer>& properties() {
    static const Properties<Renderer> value = {
        {"closed", {[](Renderer& r) { return bool_out(r.closed()); }, nullptr}},
        {"sharedContext", {[](Renderer& r) { return id_out(r.shared_context()); }, nullptr}},
        {"glPlatform", {[](Renderer& r) { return text_out(r.gl_platform()); }, nullptr}},
        {"stats", {[](Renderer& r) { return stats_out(r.stats()); }, nullptr}},
    };
    return value;
}

template <> const Properties<Scene>& properties() {
    static const Properties<Scene> value = {
        {"closed", {[](Scene& s) { return bool_out(s.closed()); }, nullptr}},
        {"camera", {[](Scene& s) { return put(s.camera()); }, [](Scene& s, const mxArray* v) { s.set_camera(get<Camera>(v)); }}},
        {"encoding", {[](Scene& s) { return text_out(s.encoding()); }, [](Scene& s, const mxArray* v) { s.set_encoding(text(v, "encoding")); }}},
        {"outputPath", {[](Scene& s) { return text_out(s.output_path()); }, [](Scene& s, const mxArray* v) { s.set_output_path(text(v, "outputPath")); }}},
        {"toneMapping", {[](Scene& s) { return text_out(s.tone_mapping()); }, [](Scene& s, const mxArray* v) { s.set_tone_mapping(text(v, "toneMapping")); }}},
        {"antialiasing", {[](Scene& s) { return text_out(s.antialiasing()); }, [](Scene& s, const mxArray* v) { s.set_antialiasing(text(v, "antialiasing")); }}},
        {"msaa", {[](Scene& s) { return number_out(s.msaa()); }, [](Scene& s, const mxArray* v) {
            const auto samples = whole(v, "msaa");
            s.set_msaa(samples < INT32_MIN || samples > INT32_MAX ? 0 : int(samples));
        }}},
        {"shadows", {[](Scene& s) { return bool_out(s.shadows()); }, [](Scene& s, const mxArray* v) { s.set_shadows(flag(v, "shadows")); }}},
        {"transparent", {[](Scene& s) { return bool_out(s.transparent()); }, [](Scene& s, const mxArray* v) { s.set_transparent(flag(v, "transparent")); }}},
        {"dithering", {[](Scene& s) { return bool_out(s.dithering()); }, [](Scene& s, const mxArray* v) { s.set_dithering(flag(v, "dithering")); }}},
        {"ssao", {[](Scene& s) { return bool_out(s.ssao()); }, [](Scene& s, const mxArray* v) { s.set_ssao(flag(v, "ssao")); }}},
        {"bloom", {[](Scene& s) { return bool_out(s.bloom()); }, [](Scene& s, const mxArray* v) { s.set_bloom(flag(v, "bloom")); }}},
        {"fog", {[](Scene& s) { return bool_out(s.fog()); }, [](Scene& s, const mxArray* v) { s.set_fog(flag(v, "fog")); }}},
        {"fogColor", {[](Scene& s) { return row_out(s.fog_color()); }, [](Scene& s, const mxArray* v) { s.set_fog_color(vec<3>(v, "fogColor")); }}},
        {"fogDensity", {[](Scene& s) { return number_out(s.fog_density()); }, [](Scene& s, const mxArray* v) { s.set_fog_density(single(v, "fogDensity")); }}},
        {"fogStart", {[](Scene& s) { return number_out(s.fog_start()); }, [](Scene& s, const mxArray* v) { s.set_fog_start(single(v, "fogStart")); }}},
        {"depthOfField", {[](Scene& s) { return bool_out(s.depth_of_field()); }, [](Scene& s, const mxArray* v) { s.set_depth_of_field(flag(v, "depthOfField")); }}},
        {"vignette", {[](Scene& s) { return bool_out(s.vignette()); }, [](Scene& s, const mxArray* v) { s.set_vignette(flag(v, "vignette")); }}},
        {"vignetteMidpoint", {[](Scene& s) { return number_out(s.vignette_midpoint()); }, [](Scene& s, const mxArray* v) { s.set_vignette_midpoint(single(v, "vignetteMidpoint")); }}},
        {"vignetteRoundness", {[](Scene& s) { return number_out(s.vignette_roundness()); }, [](Scene& s, const mxArray* v) { s.set_vignette_roundness(single(v, "vignetteRoundness")); }}},
        {"vignetteFeather", {[](Scene& s) { return number_out(s.vignette_feather()); }, [](Scene& s, const mxArray* v) { s.set_vignette_feather(single(v, "vignetteFeather")); }}},
        {"vignetteColor", {[](Scene& s) { return row_out(s.vignette_color()); }, [](Scene& s, const mxArray* v) { s.set_vignette_color(vec<3>(v, "vignetteColor")); }}},
        {"environment", {[](Scene& s) {
            const auto environment = s.environment();
            return environment ? put(*environment) : empty_out();
        }, [](Scene& s, const mxArray* v) { s.set_environment(mxIsEmpty(v) ? nullptr : &get<Environment>(v)); }}},
        {"environmentIntensity", {[](Scene& s) { return number_out(s.environment_intensity()); }, [](Scene& s, const mxArray* v) { s.set_environment_intensity(single(v, "environmentIntensity")); }}},
        {"environmentRotationDeg", {[](Scene& s) { return number_out(s.environment_rotation_deg()); }, [](Scene& s, const mxArray* v) { s.set_environment_rotation_deg(single(v, "environmentRotationDeg")); }}},
        {"environmentVisible", {[](Scene& s) { return bool_out(s.environment_visible()); }, [](Scene& s, const mxArray* v) { s.set_environment_visible(flag(v, "environmentVisible")); }}},
        {"background", {[](Scene& s) { return row_out(s.background()); }, [](Scene& s, const mxArray* v) { s.set_background(vec<4>(v, "background")); }}},
        {"pickBlended", {[](Scene& s) { return bool_out(s.pick_blended()); }, [](Scene& s, const mxArray* v) { s.set_pick_blended(flag(v, "pickBlended")); }}},
    };
    return value;
}

template <> const Properties<Camera>& properties() {
    static const Properties<Camera> value = {
        {"closed", {[](Camera& c) { return bool_out(c.closed()); }, nullptr}},
        {"exposure", {[](Camera& c) { return number_out(c.exposure()); }, [](Camera& c, const mxArray* v) { c.set_exposure(single(v, "exposure")); }}},
        {"focusDistance", {[](Camera& c) { return number_out(c.focus_distance()); }, [](Camera& c, const mxArray* v) { c.set_focus_distance(single(v, "focusDistance")); }}},
        {"aperture", {[](Camera& c) { return number_out(c.aperture()); }, [](Camera& c, const mxArray* v) { c.set_aperture(single(v, "aperture")); }}},
        {"position", {[](Camera& c) { return row_out(c.position()); }, [](Camera& c, const mxArray* v) { c.set_position(vec<3>(v, "position")); }}},
        {"transform", {[](Camera& c) { return matrix_out(c.transform()); }, [](Camera& c, const mxArray* v) { c.set_transform(matrix_in(v, "transform")); }}},
        {"viewMatrix", {[](Camera& c) { return matrix_out(c.view_matrix()); }, nullptr}},
        {"projection", {[](Camera& c) { return matrix_out(c.projection()); }, nullptr}},
        {"node", {[](Camera& c) { return c.has_node() ? put(c.node()) : empty_out(); }, nullptr}},
    };
    return value;
}

template <> const Properties<Model>& properties() {
    static const Properties<Model> value = {
        {"closed", {[](Model& m) { return bool_out(m.closed()); }, nullptr}},
        {"memory", {[](Model& m) { return memory_out(m.memory()); }, nullptr}},
        {"cameras", {[](Model& m) { return put_all(m.cameras()); }, nullptr}},
        {"lights", {[](Model& m) { return put_all(m.lights()); }, nullptr}},
        {"animations", {[](Model& m) {
            const auto clips = m.animations();
            const char* names[] = {"name", "duration", "materialProperties"};
            auto* result = mxCreateStructMatrix(1, clips.size(), 3, names);
            for (size_t i = 0; i < clips.size(); ++i) {
                const auto& clip = clips[i];
                auto* targets = mxCreateCellMatrix(clip.material_properties.size(), 2);
                const size_t rows = clip.material_properties.size();
                for (size_t k = 0; k < rows; ++k) {
                    mxSetCell(targets, k, text_out(clip.material_properties[k].material));
                    mxSetCell(targets, rows + k, text_out(clip.material_properties[k].property));
                }
                mxSetField(result, i, "name", text_out(clip.name));
                mxSetField(result, i, "duration", number_out(clip.duration));
                mxSetField(result, i, "materialProperties", targets);
            }
            return result;
        }, nullptr}},
        {"variants", {[](Model& m) { return texts_out(m.variants()); }, nullptr}},
        {"bounds", {[](Model& m) { return box_out(m.bounds()); }, nullptr}},
        {"transform", {[](Model& m) { return matrix_out(m.transform()); }, [](Model& m, const mxArray* v) { m.set_transform(matrix_in(v, "transform")); }}},
        {"position", {[](Model& m) { return row_out(m.position()); }, [](Model& m, const mxArray* v) { m.set_position(vec<3>(v, "position")); }}},
        {"visible", {[](Model& m) { return bool_out(m.visible()); }, [](Model& m, const mxArray* v) { m.set_visible(flag(v, "visible")); }}},
        {"root", {[](Model& m) { return put(m.root()); }, nullptr}},
        {"nodes", {[](Model& m) { return put_all(m.nodes()); }, nullptr}},
        {"nodeNames", {[](Model& m) { return texts_out(m.node_names()); }, nullptr}},
        {"materialNames", {[](Model& m) { return texts_out(m.material_names()); }, nullptr}},
        {"scale", {[](Model& m) { return row_out(m.root().scale()); }, [](Model& m, const mxArray* v) { m.root().set_scale(vec<3>(v, "scale")); }}},
        {"quaternion", {[](Model& m) { return row_out(m.root().quaternion()); }, [](Model& m, const mxArray* v) { m.root().set_quaternion(vec<4>(v, "quaternion")); }}},
        {"rotationEulerDeg", {[](Model& m) { return row_out(m.root().rotation_euler_deg()); }, [](Model& m, const mxArray* v) { m.root().set_rotation_euler_deg(vec<3>(v, "rotationEulerDeg")); }}},
        {"isMesh", {[](Model& m) { return bool_out(m.is_mesh()); }, nullptr}},
        {"vertexCount", {[](Model& m) { return number_out(double(m.vertex_count())); }, nullptr}},
    };
    return value;
}

template <> const Properties<Node>& properties() {
    static const Properties<Node> value = {
        {"name", {[](Node& n) { return maybe_text(n.name()); }, nullptr}},
        {"meshName", {[](Node& n) { return maybe_text(n.mesh_name()); }, nullptr}},
        {"index", {[](Node& n) { return number_out(double(n.index())); }, nullptr}},
        {"parent", {[](Node& n) { return n.has_parent() ? put(n.parent()) : empty_out(); }, nullptr}},
        {"children", {[](Node& n) { return put_all(n.children()); }, nullptr}},
        {"bounds", {[](Node& n) { return box_out(n.bounds()); }, nullptr}},
        {"morphTargetCount", {[](Node& n) { return number_out(double(n.morph_target_count())); }, nullptr}},
        {"transform", {[](Node& n) { return matrix_out(n.transform()); }, [](Node& n, const mxArray* v) { n.set_transform(matrix_in(v, "transform")); }}},
        {"position", {[](Node& n) { return row_out(n.position()); }, [](Node& n, const mxArray* v) { n.set_position(vec<3>(v, "position")); }}},
        {"scale", {[](Node& n) { return row_out(n.scale()); }, [](Node& n, const mxArray* v) { n.set_scale(vec<3>(v, "scale")); }}},
        {"quaternion", {[](Node& n) { return row_out(n.quaternion()); }, [](Node& n, const mxArray* v) { n.set_quaternion(vec<4>(v, "quaternion")); }}},
        {"rotationEulerDeg", {[](Node& n) { return row_out(n.rotation_euler_deg()); }, [](Node& n, const mxArray* v) { n.set_rotation_euler_deg(vec<3>(v, "rotationEulerDeg")); }}},
    };
    return value;
}

template <int Slot> mxArray* uv_offset(Material& m) {
    const auto t = m.texture_transform(Slot);
    return t ? row_out(t->offset) : empty_out();
}
template <int Slot> mxArray* uv_scale(Material& m) {
    const auto t = m.texture_transform(Slot);
    return t ? row_out(t->scale) : empty_out();
}
template <int Slot> mxArray* uv_rotation(Material& m) {
    const auto t = m.texture_transform(Slot);
    return t ? number_out(t->rotation_deg) : empty_out();
}
template <int Slot> void set_uv_offset(Material& m, const mxArray* v) {
    auto t = m.texture_transform(Slot).value_or(UvTransform{});
    t.offset = vec<2>(v, "offset");
    m.set_texture_transform(Slot, t);
}
template <int Slot> void set_uv_scale(Material& m, const mxArray* v) {
    auto t = m.texture_transform(Slot).value_or(UvTransform{});
    t.scale = vec<2>(v, "scale");
    m.set_texture_transform(Slot, t);
}
template <int Slot> void set_uv_rotation(Material& m, const mxArray* v) {
    auto t = m.texture_transform(Slot).value_or(UvTransform{});
    t.rotation_deg = single(v, "rotationDeg");
    m.set_texture_transform(Slot, t);
}

template <> const Properties<Material>& properties() {
    static const Properties<Material> value = {
        {"baseColor", {[](Material& m) { return row_out(m.base_color()); }, [](Material& m, const mxArray* v) { m.set_base_color(vec<4>(v, "baseColor")); }}},
        {"metallic", {[](Material& m) { return number_out(m.metallic()); }, [](Material& m, const mxArray* v) { m.set_metallic(single(v, "metallic")); }}},
        {"roughness", {[](Material& m) { return number_out(m.roughness()); }, [](Material& m, const mxArray* v) { m.set_roughness(single(v, "roughness")); }}},
        {"emissive", {[](Material& m) { return row_out(m.emissive()); }, [](Material& m, const mxArray* v) { m.set_emissive(vec<3>(v, "emissive")); }}},
        {"baseColorTextureOffset", {uv_offset<0>, set_uv_offset<0>}},
        {"baseColorTextureScale", {uv_scale<0>, set_uv_scale<0>}},
        {"baseColorTextureRotationDeg", {uv_rotation<0>, set_uv_rotation<0>}},
        {"emissiveTextureOffset", {uv_offset<1>, set_uv_offset<1>}},
        {"emissiveTextureScale", {uv_scale<1>, set_uv_scale<1>}},
        {"emissiveTextureRotationDeg", {uv_rotation<1>, set_uv_rotation<1>}},
    };
    return value;
}

template <> const Properties<Light>& properties() {
    static const Properties<Light> value = {
        {"closed", {[](Light& l) { return bool_out(l.closed()); }, nullptr}},
        {"type", {[](Light& l) { return text_out(l.type()); }, nullptr}},
        {"color", {[](Light& l) { return row_out(l.color()); }, [](Light& l, const mxArray* v) { l.set_color(vec<3>(v, "color")); }}},
        {"intensity", {[](Light& l) { return number_out(l.intensity()); }, [](Light& l, const mxArray* v) { l.set_intensity(single(v, "intensity")); }}},
        {"position", {[](Light& l) { return maybe_row(l.position()); }, [](Light& l, const mxArray* v) { l.set_position(vec<3>(v, "position")); }}},
        {"direction", {[](Light& l) { return maybe_row(l.direction()); }, [](Light& l, const mxArray* v) { l.set_direction(vec<3>(v, "direction")); }}},
        {"range", {[](Light& l) {
            const auto range = l.range();
            return range ? number_out(*range) : empty_out();
        }, [](Light& l, const mxArray* v) { l.set_range(single(v, "range")); }}},
        {"coneDeg", {[](Light& l) { return maybe_row(l.cone_deg()); }, [](Light& l, const mxArray* v) { l.set_cone_deg(vec<2>(v, "coneDeg")); }}},
        {"castsShadows", {[](Light& l) { return bool_out(l.casts_shadows()); }, [](Light& l, const mxArray* v) { l.set_casts_shadows(flag(v, "castsShadows")); }}},
        {"shadowMapSize", {[](Light& l) { return number_out(l.shadow_map_size()); }, [](Light& l, const mxArray* v) {
            const auto size = whole(v, "shadowMapSize");
            // Out-of-range values reach the core's check, which names the valid range.
            l.set_shadow_map_size(size < 0 || size > 0xFFFFFFFFll ? 0 : uint32_t(size));
        }}},
        {"shadowConstantBias", {[](Light& l) { return number_out(l.shadow_constant_bias()); }, [](Light& l, const mxArray* v) { l.set_shadow_constant_bias(single(v, "shadowConstantBias")); }}},
        {"shadowNormalBias", {[](Light& l) { return number_out(l.shadow_normal_bias()); }, [](Light& l, const mxArray* v) { l.set_shadow_normal_bias(single(v, "shadowNormalBias")); }}},
        {"node", {[](Light& l) { return l.has_node() ? put(l.node()) : empty_out(); }, nullptr}},
    };
    return value;
}

template <> const Properties<Asset>& properties() {
    static const Properties<Asset> value = {
        {"closed", {[](Asset& a) { return bool_out(a.closed()); }, nullptr}},
        {"memory", {[](Asset& a) { return memory_out(a.memory()); }, nullptr}},
    };
    return value;
}
template <> const Properties<Environment>& properties() {
    static const Properties<Environment> value = {
        {"closed", {[](Environment& e) { return bool_out(e.closed()); }, nullptr}},
    };
    return value;
}
template <> const Properties<Texture>& properties() {
    static const Properties<Texture> value = {
        {"closed", {[](Texture& t) { return bool_out(t.closed()); }, nullptr}},
        {"width", {[](Texture& t) { return number_out(t.width()); }, nullptr}},
        {"height", {[](Texture& t) { return number_out(t.height()); }, nullptr}},
        {"channels", {[](Texture& t) { return number_out(t.channels()); }, nullptr}},
        {"dtype", {[](Texture& t) { return text_out(t.is_float() ? "single" : "uint8"); }, nullptr}},
        {"colorSpace", {[](Texture& t) { return text_out(t.color_space()); }, nullptr}},
        {"mipmaps", {[](Texture& t) { return bool_out(t.mipmaps()); }, nullptr}},
    };
    return value;
}
template <> const Properties<ImportedTexture>& properties() {
    static const Properties<ImportedTexture> value = {
        {"closed", {[](ImportedTexture& t) { return bool_out(t.closed()); }, nullptr}},
        {"width", {[](ImportedTexture& t) { return number_out(t.width()); }, nullptr}},
        {"height", {[](ImportedTexture& t) { return number_out(t.height()); }, nullptr}},
        {"colorSpace", {[](ImportedTexture& t) { return text_out(t.color_space()); }, nullptr}},
        {"writing", {[](ImportedTexture& t) { return bool_out(t.writing()); }, nullptr}},
    };
    return value;
}
template <> const Properties<RenderTarget>& properties() {
    static const Properties<RenderTarget> value = {
        {"closed", {[](RenderTarget& t) { return bool_out(t.closed()); }, nullptr}},
        {"width", {[](RenderTarget& t) { return number_out(t.width()); }, nullptr}},
        {"height", {[](RenderTarget& t) { return number_out(t.height()); }, nullptr}},
    };
    return value;
}
template <> const Properties<ImportedTarget>& properties() {
    static const Properties<ImportedTarget> value = {
        {"closed", {[](ImportedTarget& t) { return bool_out(t.closed()); }, nullptr}},
        {"width", {[](ImportedTarget& t) { return number_out(t.width()); }, nullptr}},
        {"height", {[](ImportedTarget& t) { return number_out(t.height()); }, nullptr}},
        {"acquired", {[](ImportedTarget& t) { return bool_out(t.acquired()); }, nullptr}},
    };
    return value;
}
template <> const Properties<PickQuery>& properties() {
    static const Properties<PickQuery> value = {
        {"frameNumber", {[](PickQuery& q) {
            const auto frame = q.frame_number();
            return frame ? number_out(double(*frame)) : empty_out();
        }, nullptr}},
        {"result", {[](PickQuery& q) {
            auto result = q.result();
            if (!result) return empty_out();
            auto* position = row_out(result->position);
            auto* depth = number_out(result->depth);
            auto* model = put(result->model);
            return struct_out({{"model", model}, {"node", put(result->node)}, {"position", position}, {"depth", depth}});
        }, nullptr}},
    };
    return value;
}
template <> const Properties<Preparation>& properties() {
    static const Properties<Preparation> value = {
        {"pending", {[](Preparation& p) { return number_out(double(p.pending())); }, nullptr}},
    };
    return value;
}

// ---------------------------------------------------------------- commands

void close_all() noexcept;

#if defined(__linux__) && !defined(FILLY_OCTAVE)
// MATLAB (R2021b at least, also with -nosoftwareopengl) loads its own copy of an old Mesa libGL
// from sys/opengl/lib, which exports Mesa's _glapi symbols. When the global lookup finds that
// copy first, the system's Mesa driver binds to it and crashes while the renderer starts. The
// symbol's provider is what matters: with the system's libraries preloaded, MATLAB's copy can
// stay loaded and do no harm. An error with the remedy is better than the crash.
void require_system_libgl() {
    void* symbol = dlsym(RTLD_DEFAULT, "_glapi_add_dispatch");
    Dl_info info{};
    if (!symbol || !dladdr(symbol, &info) || !info.dli_fname) return;
    const std::string_view provider = info.dli_fname;
    if (provider.find("/sys/opengl/lib/") == std::string_view::npos) return;
    throw InteropError("The system's OpenGL driver would bind to MATLAB's own Mesa library (" + std::string(provider)
                       + ") and crash. Start MATLAB with the system's GL libraries preloaded, for example "
                         "LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libGL.so.1:/usr/lib/x86_64-linux-gnu/libglapi.so.0; "
                         "see docs/how-to/matlab.md.");
}
#else
void require_system_libgl() {}
#endif

// `clear mex` must not unload code that Filament's driver thread runs. Locked for every renderer,
// not once: Octave 11 keeps the lock on a function object, and a call after a script that run()
// executed can get a new object for the same loaded file, without the lock. MATLAB counts the
// calls; nothing unlocks.
void lock() { mexLock(); }

template <class T> bool same(const T& a, const T& b) {
    if constexpr (requires { a.same(b); }) return a.same(b);
    else return &a == &b;
}

using Handler = void (*)(Call&);
const std::unordered_map<std::string_view, Handler>& commands() {
    static const std::unordered_map<std::string_view, Handler> value = {
        // ------------------------------------------------ module
        {"get", [](Call& c) {
            auto& object = lookup(c[0]);
            Name name;
            read_name(c[1], name, "Property name");
            std::visit([&](auto& item) {
                using T = std::decay_t<decltype(item)>;
                const auto& map = properties<T>();
                const auto found = map.find(name.view);
                if (found == map.end()) invalid(std::string(kind_names[kind<T>]) + " has no property '" + std::string(name.view) + "'");
                c.out(0, found->second.get(item));
            }, object);
        }},
        {"set", [](Call& c) {
            auto& object = lookup(c[0]);
            Name name;
            read_name(c[1], name, "Property name");
            const mxArray* value = c[2];
            std::visit([&](auto& item) {
                using T = std::decay_t<decltype(item)>;
                const auto& map = properties<T>();
                const auto found = map.find(name.view);
                if (found == map.end()) invalid(std::string(kind_names[kind<T>]) + " has no property '" + std::string(name.view) + "'");
                if (!found->second.set) invalid(std::string(kind_names[kind<T>]) + "." + std::string(name.view) + " is read-only");
                found->second.set(item, value);
            }, object);
        }},
        {"release", [](Call& c) {
            // Idempotent: MATLAB can delete an object after the exit handler emptied the table.
            table().objects.erase(handle_id(c[0]));
        }},
        {"handleCount", [](Call& c) { c.out(0, number_out(double(table().objects.size()))); }},
        // Octave 11 runs a subclass destructor again after an explicit delete, when the handle can
        // already be released; the destructors check first.
        {"valid", [](Call& c) { c.out(0, bool_out(table().objects.count(handle_id(c[0])) > 0)); }},
        {"version", [](Call& c) { c.out(0, text_out(FILLY_FRONTEND_VERSION)); }},
        {"kind", [](Call& c) { c.out(0, text_out(kind_names[lookup(c[0]).index()])); }},
        {"same", [](Call& c) {
            auto& a = lookup(c[0]);
            auto& b = lookup(c[1]);
            bool result = false;
            if (a.index() == b.index())
                std::visit([&](auto& first) {
                    using T = std::decay_t<decltype(first)>;
                    result = same(first, std::get<T>(b));
                }, a);
            c.out(0, bool_out(result));
        }},
        {"setLogLevel", [](Call& c) { set_log_level(text(c[0], "level")); }},
        {"currentContext", [](Call& c) { c.out(0, id_out(current_gl_context())); }},
        {"createHostTexture", [](Call& c) {
            c.out(0, number_out(create_host_texture(whole(c[0], "width"), whole(c[1], "height"))));
        }},
        {"deleteHostTexture", [](Call& c) { delete_host_texture(uint32_t(whole(c[0], "texture"))); }},
        {"shape", [](Call& c) {
            Name name;
            read_name(c[0], name, "Shape");
            if (name.view == "plane")
                c.out(0, shape_out(shape_plane(number(c[1], "width"), number(c[2], "height"), whole(c[3], "columns"), whole(c[4], "rows"))));
            else if (name.view == "box")
                c.out(0, shape_out(shape_box(number(c[1], "width"), number(c[2], "height"), number(c[3], "depth"))));
            else if (name.view == "uvSphere")
                c.out(0, shape_out(shape_uv_sphere(number(c[1], "radius"), whole(c[2], "segments"), whole(c[3], "rings"))));
            else if (name.view == "cylinder")
                c.out(0, shape_out(shape_cylinder(number(c[1], "radius"), number(c[2], "height"), whole(c[3], "segments"), flag(c[4], "caps"))));
            else invalid("Shape must be 'plane', 'box', 'uvSphere', or 'cylinder'");
        }},

        // ------------------------------------------------ Renderer
        {"Renderer.new", [](Call& c) {
            uint64_t context = 0;
            if (c.given(0)) {
                context = mxIsUint64(c[0]) ? handle_id(c[0]) : uint64_t(whole(c[0], "sharedContext"));
                if (!context) throw InteropError("sharedContext cannot be zero; omit it for offscreen rendering");
            }
            require_system_libgl();
            // Locked before the engine starts its threads.
            lock();
            auto& t = table();
            std::erase_if(t.renderers, [](const Renderer& r) { return r.closed(); });
            Renderer renderer(context);
            t.renderers.push_back(renderer);
            c.out(0, put(std::move(renderer)));
        }},
        {"Renderer.close", [](Call& c) { get<Renderer>(c[0]).close(); }},
        {"Renderer.createScene", [](Call& c) { c.out(0, put(get<Renderer>(c[0]).create_scene())); }},
        {"Renderer.createCamera", [](Call& c) { c.out(0, put(get<Renderer>(c[0]).create_camera())); }},
        {"Renderer.loadAsset", [](Call& c) {
            auto& r = get<Renderer>(c[0]);
            load_source<Asset>(c, 1, c.nrhs > 2 && flag(c[2], "strict"), [&](auto&&... args) { return r.load_asset(args...); });
        }},
        {"Renderer.loadEnvironment", [](Call& c) { c.out(0, put(get<Renderer>(c[0]).load_environment(text(c[1], "path")))); }},
        {"Renderer.loadEnvironmentKtx", [](Call& c) {
            const auto skybox = c.given(2) ? text(c[2], "skyboxPath") : std::string();
            c.out(0, put(get<Renderer>(c[0]).load_environment_ktx(text(c[1], "iblPath"), skybox)));
        }},
        {"Renderer.createEnvironment", [](Call& c) {
            auto& r = get<Renderer>(c[0]);
            const auto* pixels = c[1];
            if (mxIsComplex(pixels) || !(mxIsSingle(pixels) || mxIsDouble(pixels)) || mxGetNumberOfDimensions(pixels) != 3
                    || mxGetDimensions(pixels)[2] != 3)
                invalid("Environment pixels must be an H x W x 3 single or double array of linear RGB values");
            const size_t h = mxGetDimensions(pixels)[0], w = mxGetDimensions(pixels)[1];
            if (h < 2 || h > 4096 || w != 2 * h) invalid("Environment must be a 2:1 RGB panorama with height 2..4096");
            // The core copies the panorama once more; loading runs once per environment.
            auto& rows = scratch().floats;
            rows.resize(h * w * 3);
            if (mxIsSingle(pixels)) planar_to_rows(static_cast<const float*>(mxGetData(pixels)), rows.data(), h, w, 3);
            else {
                const auto* in = static_cast<const double*>(mxGetData(pixels));
                for (size_t y = 0; y < h; ++y) for (size_t x = 0; x < w; ++x) for (size_t k = 0; k < 3; ++k)
                    rows[(y * w + x) * 3 + k] = float(in[k * h * w + x * h + y]);
            }
            c.out(0, put(r.create_environment(rows.data(), rows.size(), uint32_t(w), uint32_t(h))));
        }},
        {"Renderer.createRenderTarget", [](Call& c) {
            const auto format = c.given(3) ? text(c[3], "format") : std::string("rgba8");
            const bool depth = c.nrhs > 4 ? flag(c[4], "depth") : true;
            c.out(0, put(get<Renderer>(c[0]).create_render_target(whole(c[1], "width"), whole(c[2], "height"), format, depth)));
        }},
        {"Renderer.importGlTarget", [](Call& c) {
            const auto texture = whole(c[1], "texture");
            if (texture < 1 || texture > UINT32_MAX) invalid("texture must be an OpenGL texture name");
            const auto format = c.given(4) ? text(c[4], "format") : std::string("rgba8");
            const bool depth = c.nrhs > 5 ? flag(c[5], "depth") : true;
            // No on_renderer_close callback: the MATLAB classes release PTB's objects after the
            // renderer closes, when Filament no longer uses them.
            c.out(0, put(get<Renderer>(c[0]).import_gl_target(uint32_t(texture), whole(c[2], "width"), whole(c[3], "height"),
                                                                format, depth)));
        }},
        {"Renderer.render", [](Call& c) {
            auto& r = get<Renderer>(c[0]);
            auto& scene = get<Scene>(c[1]);
            auto& target = lookup(c[2]);
            std::optional<Camera> camera;
            const auto options = render_options(c, 3, camera);
            if (auto* offscreen = std::get_if<RenderTarget>(&target)) r.render(scene, *offscreen, options);
            else if (auto* imported = std::get_if<ImportedTarget>(&target)) r.render(scene, *imported, options);
            else invalid("target must be a RenderTarget or an ImportedTarget");
        }},
        {"Renderer.pick", [](Call& c) {
            c.out(0, put(get<Renderer>(c[0]).pick(get<Scene>(c[1]), number(c[2], "x"), number(c[3], "y"))));
        }},
        {"Renderer.createTexture", [](Call& c) {
            auto& r = get<Renderer>(c[0]);
            const auto* pixels = c[1];
            const auto shape = image_shape(pixels, "pixels");
            const auto color_space = text(c[2], "colorSpace");
            const bool mipmaps = c.nrhs > 3 && flag(c[3], "mipmaps");
            const auto filter = c.given(4) ? text(c[4], "filter") : std::string("linear");
            const auto wrap = c.given(5) ? text(c[5], "wrap") : std::string("repeat");
            const size_t bytes = shape.height * shape.width * shape.channels * (shape.is_float ? 4 : 1);
            auto& rows = scratch().bytes;
            rows.resize(bytes);
            image_to_rows(pixels, shape, rows.data());
            c.out(0, put(r.create_texture(int64_t(shape.width), int64_t(shape.height), int(shape.channels), shape.is_float,
                                          color_space, mipmaps, filter, wrap, rows.data(), bytes)));
        }},
        {"Renderer.importGlTexture", [](Call& c) {
            const auto texture = whole(c[1], "texture");
            if (texture < 1 || texture > UINT32_MAX) invalid("texture must be an OpenGL texture name");
            const auto filter = c.given(5) ? text(c[5], "filter") : std::string("linear");
            const auto wrap = c.given(6) ? text(c[6], "wrap") : std::string("repeat");
            c.out(0, put(get<Renderer>(c[0]).import_gl_texture(uint32_t(texture), whole(c[2], "width"), whole(c[3], "height"),
                                                                 text(c[4], "colorSpace"), filter, wrap)));
        }},
        {"Renderer.prepare", [](Call& c) { c.out(0, put(get<Renderer>(c[0]).prepare(get<Scene>(c[1])))); }},
        {"Renderer.finish", [](Call& c) { get<Renderer>(c[0]).finish(); }},

        // ------------------------------------------------ Scene
        {"Scene.close", [](Call& c) { get<Scene>(c[0]).close(); }},
        {"Scene.load", [](Call& c) {
            auto& s = get<Scene>(c[0]);
            load_source<Model>(c, 1, c.nrhs > 2 && flag(c[2], "strict"), [&](auto&&... args) { return s.load(args...); });
        }},
        {"Scene.add", [](Call& c) { c.out(0, put(get<Scene>(c[0]).add(get<Asset>(c[1])))); }},
        {"Scene.createMesh", [](Call& c) {
            // (scene, positions, indices, normals, uvs, colors, baseColor, metallic, roughness,
            //  emissive, unlit, doubleSided, alphaMode)
            auto& s = get<Scene>(c[0]);
            const auto* positions = c[1];
            if (mxGetNumberOfDimensions(positions) != 2 || mxGetN(positions) != 3) invalid("positions must be N x 3");
            const size_t vertices = mxGetM(positions);
            MeshArrays arrays;
            arrays.vertices = vertices;
            arrays.positions = rows_in(positions, 3, vertices, "positions", scratch().positions);
            arrays.normals = c.given(3) ? rows_in(c[3], 3, vertices, "normals", scratch().normals) : nullptr;
            arrays.uvs = c.given(4) ? rows_in(c[4], 2, vertices, "uvs", scratch().uvs) : nullptr;
            arrays.colors = c.given(5) ? rows_in(c[5], 4, vertices, "colors", scratch().colors) : nullptr;
            const auto* indices = c[2];
            require_numeric(indices, "indices");
            if (mxGetNumberOfDimensions(indices) != 2 || mxGetN(indices) != 3) invalid("indices must be M x 3");
            const size_t triangles = mxGetM(indices);
            auto& converted = scratch().indices;
            converted.resize(triangles * 3);
            for (size_t col = 0; col < 3; ++col)
                for (size_t row = 0; row < triangles; ++row) {
                    const double value = element(indices, col * triangles + row);
                    if (!(value >= 1) || value != std::floor(value) || value > double(vertices))
                        invalid("indices must be whole numbers from 1 through the vertex count");
                    converted[row * 3 + col] = uint32_t(value - 1);
                }
            arrays.indices = converted.data();
            arrays.triangles = triangles;
            MeshMaterial material;
            if (c.given(6)) material.base_color = vec<4>(c[6], "baseColor");
            if (c.given(7)) material.metallic = single(c[7], "metallic");
            if (c.given(8)) material.roughness = single(c[8], "roughness");
            if (c.given(9)) material.emissive = vec<3>(c[9], "emissive");
            if (c.given(10)) material.unlit = flag(c[10], "unlit");
            if (c.given(11)) material.double_sided = flag(c[11], "doubleSided");
            if (c.given(12)) material.alpha_mode = text(c[12], "alphaMode");
            c.out(0, put(s.create_mesh(arrays, material)));
        }},
        {"Scene.addDirectionalLight", [](Call& c) {
            c.out(0, put(get<Scene>(c[0]).add_directional_light(vec<3>(c[1], "direction"), single(c[2], "intensity"), vec<3>(c[3], "color"))));
        }},
        {"Scene.addSunLight", [](Call& c) {
            c.out(0, put(get<Scene>(c[0]).add_sun_light(vec<3>(c[1], "direction"), single(c[2], "intensity"), vec<3>(c[3], "color"),
                                                         single(c[4], "angularRadiusDeg"), single(c[5], "haloSize"),
                                                         single(c[6], "haloFalloff"))));
        }},
        {"Scene.addPointLight", [](Call& c) {
            c.out(0, put(get<Scene>(c[0]).add_point_light(vec<3>(c[1], "position"), single(c[2], "intensity"), vec<3>(c[3], "color"),
                                                           single(c[4], "range"))));
        }},
        {"Scene.addSpotLight", [](Call& c) {
            c.out(0, put(get<Scene>(c[0]).add_spot_light(vec<3>(c[1], "position"), vec<3>(c[2], "direction"), single(c[3], "intensity"),
                                                          vec<3>(c[4], "color"), single(c[5], "range"), vec<2>(c[6], "coneDeg"))));
        }},
        // ------------------------------------------------ Camera
        {"Camera.close", [](Call& c) { get<Camera>(c[0]).close(); }},
        {"Camera.setPerspective", [](Call& c) {
            auto& camera = get<Camera>(c[0]);
            const double aspect = aspect_in(c, 2);
            camera.set_perspective(number(c[1], "fovYDeg"), aspect, c.given(3) ? maybe(c[3], "near") : std::nullopt,
                                   c.given(4) ? maybe(c[4], "far") : std::nullopt);
        }},
        {"Camera.setLensProjection", [](Call& c) {
            auto& camera = get<Camera>(c[0]);
            const double aspect = aspect_in(c, 2);
            camera.set_lens_projection(number(c[1], "focalLengthMm"), aspect, c.given(3) ? maybe(c[3], "near") : std::nullopt,
                                       c.given(4) ? maybe(c[4], "far") : std::nullopt);
        }},
        {"Camera.setOrthographic", [](Call& c) {
            get<Camera>(c[0]).set_orthographic(number(c[1], "left"), number(c[2], "right"), number(c[3], "bottom"),
                                               number(c[4], "top"), c.given(5) ? maybe(c[5], "near") : std::nullopt,
                                               c.given(6) ? maybe(c[6], "far") : std::nullopt);
        }},
        {"Camera.setOrthographicHeight", [](Call& c) {
            const auto center = c.given(2) ? vec<2>(c[2], "center") : std::array<float, 2>{0, 0};
            get<Camera>(c[0]).set_orthographic_height(number(c[1], "height"), center[0], center[1],
                                                      c.given(3) ? maybe(c[3], "near") : std::nullopt,
                                                      c.given(4) ? maybe(c[4], "far") : std::nullopt);
        }},
        {"Camera.lookAt", [](Call& c) {
            const auto up = c.given(2) ? vec<3>(c[2], "up") : Vec3{0, 1, 0};
            get<Camera>(c[0]).look_at(vec<3>(c[1], "target"), up);
        }},
        {"Camera.frame", [](Call& c) {
            auto& camera = get<Camera>(c[0]);
            const auto options = frame_options(c, 2);
            const auto* target = c[1];
            double distance = 0;
            if (mxIsUint64(target)) {
                auto& object = lookup(target);
                if (auto* model = std::get_if<Model>(&object)) distance = camera.frame(*model, options);
                else if (auto* node = std::get_if<Node>(&object)) distance = camera.frame(*node, options);
                else invalid("target must be a Model, a Node, or a 2 x 3 box [min; max]");
            } else {
                require_numeric(target, "target");
                if (mxGetM(target) != 2 || mxGetN(target) != 3) invalid("target must be a Model, a Node, or a 2 x 3 box [min; max]");
                std::array<Vec3, 2> box;
                for (size_t row = 0; row < 2; ++row)
                    for (size_t col = 0; col < 3; ++col) box[row][col] = float(element(target, col * 2 + row));
                distance = camera.frame(box, options);
            }
            c.out(0, number_out(distance));
        }},

        // ------------------------------------------------ Model, Node, Material, Light
        {"Model.close", [](Call& c) { get<Model>(c[0]).close(); }},
        {"Model.node", [](Call& c) {
            auto& m = get<Model>(c[0]);
            c.out(0, put(by_key(c[1], [&](const std::string& n) { return m.node(n); }, [&](int64_t i) { return m.node(i); })));
        }},
        {"Model.camera", [](Call& c) {
            auto& m = get<Model>(c[0]);
            c.out(0, put(by_key(c[1], [&](const std::string& n) { return m.camera(n); }, [&](int64_t i) { return m.camera(i); })));
        }},
        {"Model.light", [](Call& c) {
            auto& m = get<Model>(c[0]);
            c.out(0, put(by_key(c[1], [&](const std::string& n) { return m.light(n); }, [&](int64_t i) { return m.light(i); })));
        }},
        {"Model.material", [](Call& c) { c.out(0, put(get<Model>(c[0]).material(text(c[1], "name")))); }},
        {"Model.applyAnimation", [](Call& c) {
            auto& m = get<Model>(c[0]);
            const float time = single(c[2], "time");
            const bool loop = c.nrhs > 3 ? flag(c[3], "loop") : true;
            by_position(c[1], [&](const std::string& n) { m.apply_animation(n, time, loop); return 0; },
                        [&](int64_t i) { m.apply_animation(i, time, loop); return 0; });
        }},
        {"Model.resetAnimation", [](Call& c) { get<Model>(c[0]).reset_animation(); }},
        {"Model.applyVariant", [](Call& c) {
            auto& m = get<Model>(c[0]);
            by_position(c[1], [&](const std::string& n) { m.apply_variant(n); return 0; },
                        [&](int64_t i) { m.apply_variant(i); return 0; });
        }},
        {"Model.updateMesh", [](Call& c) {
            auto& m = get<Model>(c[0]);
            m.update_mesh(mesh_arrays(c, 1, m.vertex_count()));
        }},
        {"Node.setMorphWeights", [](Call& c) {
            Batch<Node> nodes(scratch().nodes, c[0], "nodes");
            const auto* input = c[1];
            if (nodes.items.size() == 1) {
                // One node takes its weights as any vector.
                require_numeric(input, "weights");
                auto& weights = scratch().floats;
                weights.resize(mxGetNumberOfElements(input));
                for (size_t i = 0; i < weights.size(); ++i) weights[i] = float(element(input, i));
                nodes.items[0].set_morph_weights(weights.data(), weights.size());
                return;
            }
            auto values = values_in(input, "weights");
            if (mxGetNumberOfDimensions(input) != 2) invalid("weights must be N x K");
            const size_t rows = mxGetM(input), columns = mxGetN(input);
            values.shape = {rows, columns, 1};
            values.strides = {1, int64_t(rows), 0};
            Node::set_morph_weights(nodes.items, values);
        }},
        {"Node.setTransforms", [](Call& c) {
            Batch<Node> nodes(scratch().nodes, c[0], "nodes");
            const auto* transforms = c[1];
            auto values = values_in(transforms, "transforms");
            const auto* dims = mxGetDimensions(transforms);
            const auto n = mxGetNumberOfDimensions(transforms);
            const size_t count = mxIsEmpty(transforms) ? 0 : n == 2 ? 1 : size_t(dims[2]);
            if ((count && (dims[0] != 4 || dims[1] != 4 || n > 3)) || count != nodes.items.size())
                invalid("transforms must be 4 x 4 x N for N nodes, with " + std::to_string(nodes.items.size()) + " nodes");
            // MATLAB's 4 x 4 x N, column-major, is the core's (N, 4, 4) with these strides.
            values.shape = {nodes.items.size(), 4, 4};
            values.strides = {16, 1, 4};
            Node::set_transforms(nodes.items, values);
        }},
        {"Node.material", [](Call& c) {
            const auto slot = c.given(1) ? whole(c[1], "slot") : 1;
            if (slot < 1) invalid("Material slots are 1-based");
            c.out(0, put(get<Node>(c[0]).material(size_t(slot - 1))));
        }},
        {"Material.setParameters", [](Call& c) {
            Batch<Material> materials(scratch().materials, c[0], "materials");
            Name name;
            read_name(c[1], name, "Parameter name");
            const auto parameter = material_parameter(name.view);
            const auto* input = c[2];
            auto values = values_in(input, "values");
            const size_t rows = mxGetM(input), columns = mxGetN(input), count = materials.items.size();
            if (mxGetNumberOfDimensions(input) != 2) invalid("values must be N x K");
            // One scalar per material may come as a row or a column vector.
            const bool scalar = parameter == MaterialParameter::metallic || parameter == MaterialParameter::roughness;
            if (scalar && mxGetNumberOfElements(input) == count) {
                values.shape = {count, 1, 1};
                values.strides = {1, 0, 0};
            } else {
                values.shape = {rows, columns, 1};
                values.strides = {1, int64_t(rows), 0};
            }
            Material::set_parameters(materials.items, parameter, values);
        }},
        {"Material.texture", [](Call& c) {
            auto& m = get<Material>(c[0]);
            const auto slot = int(whole(c[1], "slot"));
            auto data = m.texture(slot);
            c.out(1, texture_kind(data));
            c.out(0, texture_id(std::move(data)));
        }},
        {"Material.setTexture", [](Call& c) {
            auto& m = get<Material>(c[0]);
            const auto slot = int(whole(c[1], "slot"));
            std::shared_ptr<detail::TextureData> data;
            if (c.given(2)) {
                auto& object = lookup(c[2]);
                if (auto* texture = std::get_if<Texture>(&object)) data = texture->data();
                else if (auto* imported = std::get_if<ImportedTexture>(&object)) data = imported->data();
                else invalid("Texture slots take a Texture, an ImportedTexture, or []");
            }
            m.set_texture(slot, std::move(data));
        }},
        {"Light.close", [](Call& c) { get<Light>(c[0]).close(); }},

        // ------------------------------------------------ resources
        {"Asset.close", [](Call& c) { get<Asset>(c[0]).close(); }},
        {"Environment.close", [](Call& c) { get<Environment>(c[0]).close(); }},
        {"Texture.close", [](Call& c) { get<Texture>(c[0]).close(); }},
        {"Texture.update", [](Call& c) {
            auto& texture = get<Texture>(c[0]);
            const auto* pixels = c[1];
            const auto shape = image_shape(pixels, "pixels");
            if (shape.is_float != texture.is_float() || shape.channels != texture.channels() || shape.height != texture.height()
                    || shape.width != texture.width())
                invalid("update() needs the size and class of the texture: " + std::to_string(texture.height()) + " x "
                        + std::to_string(texture.width()) + " x " + std::to_string(texture.channels()) + " "
                        + (texture.is_float() ? "single" : "uint8"));
            const size_t bytes = shape.height * shape.width * shape.channels * (shape.is_float ? 4 : 1);
            if (shape.channels == 1 && !shape.is_float && texture.color_space() == "srgb") {
                // These expand to RGBA on upload, which begin_update() does not do.
                auto& rows = scratch().bytes;
                rows.resize(bytes);
                image_to_rows(pixels, shape, rows.data());
                texture.update(rows.data(), bytes);
                return;
            }
            // The conversion writes straight into the core's upload storage.
            image_to_rows(pixels, shape, texture.begin_update(bytes));
            texture.commit_update();
        }},
        {"ImportedTexture.close", [](Call& c) { get<ImportedTexture>(c[0]).close(); }},
        {"ImportedTexture.beginWrite", [](Call& c) { get<ImportedTexture>(c[0]).begin_write(); }},
        {"ImportedTexture.endWrite", [](Call& c) { get<ImportedTexture>(c[0]).end_write(); }},
        {"RenderTarget.close", [](Call& c) { get<RenderTarget>(c[0]).close(); }},
        {"RenderTarget.read", [](Call& c) {
            auto& target = get<RenderTarget>(c[0]);
            const size_t h = target.height(), w = target.width();
            const auto pixels = target.read();
            const mwSize dims[3] = {mwSize(h), mwSize(w), 4};
            auto* result = mxCreateUninitNumericArray(3, const_cast<mwSize*>(dims), mxUINT8_CLASS, mxREAL);
            rows_to_planar(pixels.data.get(), static_cast<uint8_t*>(mxGetData(result)), h, w, 4);
            c.out(0, result);
        }},
        {"ImportedTarget.close", [](Call& c) { get<ImportedTarget>(c[0]).close(); }},
        {"ImportedTarget.acquire", [](Call& c) { get<ImportedTarget>(c[0]).acquire(); }},
        {"ImportedTarget.release", [](Call& c) { get<ImportedTarget>(c[0]).release(); }},
        {"PickQuery.ready", [](Call& c) { c.out(0, bool_out(get<PickQuery>(c[0]).ready())); }},
        {"Preparation.ready", [](Call& c) { c.out(0, bool_out(get<Preparation>(c[0]).ready())); }},
    };
    return value;
}

// Closes every renderer, newest first, then drops the handles. Renderer::close() releases each
// renderer's resources in the core's order. With the host window gone (PTB's `sca` before
// exit), it makes no host OpenGL calls.
void close_all() noexcept {
    auto& t = table();
    for (auto it = t.renderers.rbegin(); it != t.renderers.rend(); ++it) {
        try { it->close(); } catch (...) {}
    }
    try {
        t.objects.clear();
        t.renderers.clear();
        scratch().nodes.clear();
        scratch().materials.clear();
    } catch (...) {}
}
void at_exit() { close_all(); }

void copy_text(char* buffer, size_t size, const char* text) {
    std::snprintf(buffer, size, "%s", text ? text : "");
}

// Runs one call. Every C++ object lives inside this function, so that the caller can raise the
// MATLAB error after they are gone: a mexErrMsgIdAndTxt that uses longjmp skips destructors, and
// Octave 8.4's, which throws a C++ exception, would be caught by the catch (...) below.
bool run(int nlhs, mxArray* plhs[], int nrhs, const mxArray* prhs[], char* identifier, size_t identifier_size,
         char* message, size_t message_size) noexcept {
    const char* id = "filly:error";
    try {
        static bool registered = false;
        if (!registered) {
            mexAtExit(at_exit);
            registered = true;
        }
        if (nrhs < 1) invalid("Usage: filly_mex(command, ...)");
        Name command;
        read_name(prhs[0], command, "Command");
        const auto& table = commands();
        const auto found = table.find(command.view);
        if (found == table.end()) invalid("Unknown filly_mex command '" + std::string(command.view) + "'");
        Call call{nlhs, plhs, nrhs - 1, prhs + 1, command.view};
        found->second(call);
        return true;
    } catch (const AssetError& error) {
        id = "filly:asset";
        copy_text(message, message_size, error.what());
    } catch (const BackendError& error) {
        id = "filly:backend";
        copy_text(message, message_size, error.what());
    } catch (const InteropError& error) {
        id = "filly:interop";
        copy_text(message, message_size, error.what());
    } catch (const FillyError& error) {
        copy_text(message, message_size, error.what());
    } catch (const std::invalid_argument& error) {
        id = "filly:invalidArgument";
        copy_text(message, message_size, error.what());
    } catch (const std::out_of_range& error) {
        id = "filly:invalidArgument";
        copy_text(message, message_size, error.what());
    } catch (const std::bad_alloc&) {
        copy_text(message, message_size, "Out of memory");
    } catch (const std::exception& error) {
        copy_text(message, message_size, error.what());
    } catch (...) {
        copy_text(message, message_size, "Unknown native error");
    }
    copy_text(identifier, identifier_size, id);
    return false;
}

}

#if defined(FILLY_OCTAVE_MEX_SOVERSION)
// Octave 10 and later check this symbol for the MEX API version, as mkoctfile --mex writes it.
extern "C" const int __octave_mex_soversion__ = FILLY_OCTAVE_MEX_SOVERSION;
#endif

void mexFunction(int nlhs, mxArray* plhs[], int nrhs, const mxArray* prhs[]) {
    char identifier[32];
    char message[2048];
    if (!run(nlhs, plhs, nrhs, prhs, identifier, sizeof(identifier), message, sizeof(message)))
        mexErrMsgIdAndTxt(identifier, "%s", message);
}
