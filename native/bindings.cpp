#include "renderer.h"
#include "vendor/meshoptimizer/meshoptimizer.h"

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/array.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <optional>

namespace nb = nanobind;
using namespace nb::literals;
using namespace filly;

namespace {
using MatrixInput = nb::ndarray<nb::numpy, const double, nb::shape<4, 4>, nb::c_contig>;
Matrix from_array(const MatrixInput& input) {
    Matrix result;
    std::transform(input.data(), input.data() + 16, result.begin(), [](double value) {
        if (value > std::numeric_limits<float>::max() || value < -std::numeric_limits<float>::max())
            throw std::invalid_argument("Transform exceeds float32 range");
        return float(value);
    });
    return result;
}
auto to_array(Matrix value) {
    auto result = std::make_unique<Matrix>(value);
    auto* ptr = result.get();
    nb::capsule owner(ptr, [](void* p) noexcept { delete static_cast<Matrix*>(p); });
    result.release();
    return nb::ndarray<nb::numpy, float>(ptr->data(), {4, 4}, owner);
}
// Sizes often come from NumPy or from float arithmetic such as `win.size[0] / 2`.
int64_t pixel_size(nb::handle value, const char* name) {
    PyObject* object = value.ptr();
    if (PyBool_Check(object)) throw nb::type_error((std::string(name) + " must be an integer, got bool").c_str());
    nb::object whole;
    if (PyIndex_Check(object)) {
        whole = nb::steal(PyNumber_Index(object));
    } else if (nb::hasattr(value, "is_integer") && PyNumber_Check(object)) {
        if (!nb::cast<bool>(value.attr("is_integer")()))
            throw std::invalid_argument(std::string(name) + " must be a whole number, got " + nb::cast<std::string>(nb::repr(value)));
        whole = nb::steal(PyNumber_Long(object));
    } else {
        throw nb::type_error((std::string(name) + " must be an integer, got " + nb::type_name(value.type()).c_str()).c_str());
    }
    if (!whole.is_valid()) throw nb::python_error();
    int overflow = 0;
    const long long result = PyLong_AsLongLongAndOverflow(whole.ptr(), &overflow);
    if (result == -1 && PyErr_Occurred()) throw nb::python_error();
    // Out-of-range values fail the renderer's own dimension check with its usual message.
    if (overflow) return overflow > 0 ? std::numeric_limits<int64_t>::max() : std::numeric_limits<int64_t>::min();
    return result;
}
// Explicit aspects must be positive; zero tells the camera to follow the render target.
double aspect_value(const std::optional<double>& aspect) {
    if (!aspect) return 0;
    if (!std::isfinite(*aspect) || *aspect <= 0) throw std::invalid_argument("aspect must be positive, or None for the target aspect");
    return *aspect;
}
// Accepts a name or a glTF-order index. bool is an int subclass but never a meaningful key.
template <class ByName, class ByIndex> auto by_key(nb::handle key, ByName by_name, ByIndex by_index) {
    if (nb::isinstance<nb::str>(key)) return by_name(nb::cast<std::string>(key));
    if (PyBool_Check(key.ptr()) || !PyIndex_Check(key.ptr()))
        throw nb::type_error("Key must be a name (str) or an index (int)");
    return by_index(nb::cast<int64_t>(nb::int_(key)));
}
Model load(Scene& scene, nb::object source, bool strict, bool clonable) {
    std::vector<uint8_t> bytes;
    std::string path;
    if (nb::isinstance<nb::bytes>(source)) {
        const auto blob = nb::cast<nb::bytes>(source);
        const auto* first = reinterpret_cast<const uint8_t*>(blob.c_str());
        bytes.assign(first, first + blob.size());
    } else {
        auto os = nb::module_::import_("os");
        path = nb::cast<std::string>(os.attr("fsdecode")(os.attr("fspath")(source)));
    }
    if (!path.empty()) {
        nb::gil_scoped_release release;
        const auto file = std::filesystem::absolute(std::filesystem::u8path(path));
        const auto utf8 = file.u8string();
        path.assign(reinterpret_cast<const char*>(utf8.data()), utf8.size());
        std::ifstream stream(file, std::ios::binary | std::ios::ate);
        if (!stream) throw AssetError("Could not open asset: " + path);
        auto size = stream.tellg();
        if (size <= 0 || uint64_t(size) > std::numeric_limits<uint32_t>::max())
            throw AssetError("Asset is empty or larger than 4 GiB");
        bytes.resize(size_t(size));
        stream.seekg(0);
        if (!stream.read(reinterpret_cast<char*>(bytes.data()), size))
            throw AssetError("Could not read asset: " + path);
    }
    // Preflight detects GLB by its magic, so the file name and source type do not matter.
    auto prepared = nb::module_::import_("filly._assets").attr("prepare")(
        nb::bytes(reinterpret_cast<const char*>(bytes.data()), bytes.size()), path,
        "strict"_a=strict, "refraction"_a=scene.refraction(), "precompiled"_a=scene.precompiled_shaders());
    auto normalized = nb::cast<nb::bytes>(prepared[0]);
    bytes.assign(reinterpret_cast<const uint8_t*>(normalized.c_str()), reinterpret_cast<const uint8_t*>(normalized.c_str())+normalized.size());
    auto texture = [](nb::handle value) {
        auto record = nb::borrow<nb::tuple>(value);
        AssetTexture result;
        auto data = nb::cast<nb::bytes>(record[0]);
        result.bytes.assign(reinterpret_cast<const uint8_t*>(data.c_str()), reinterpret_cast<const uint8_t*>(data.c_str())+data.size());
        result.mime = nb::cast<std::string>(record[1]); result.uv = nb::cast<int>(record[2]);
        result.transform = nb::cast<std::array<float,9>>(record[3]);
        result.wrap_s = nb::cast<int>(record[4]); result.wrap_t = nb::cast<int>(record[5]);
        result.min_filter = nb::cast<int>(record[6]); result.mag_filter = nb::cast<int>(record[7]);
        return result;
    };
    std::vector<DiffuseSource> diffuse;
    for (auto item : nb::borrow<nb::iterable>(prepared[1])) {
        auto record = nb::borrow<nb::tuple>(item);
        diffuse.push_back({nb::cast<float>(record[0]),nb::cast<Vec3>(record[1]),texture(record[2]),texture(record[3]),
            nb::cast<float>(record[4]), nb::cast<Vec3>(record[5]), texture(record[6])});
    }
    auto morphs = nb::cast<std::vector<std::vector<float>>>(prepared[2]);
    std::vector<AnimationSource> animations;
    for (auto item : nb::borrow<nb::iterable>(prepared[3])) {
        auto record = nb::borrow<nb::tuple>(item);
        AnimationSource animation;
        animation.name = nb::cast<std::string>(record[0]);
        animation.native_index = nb::cast<int>(record[1]);
        for (auto value : nb::borrow<nb::iterable>(record[2])) {
            auto track = nb::borrow<nb::tuple>(value);
            animation.tracks.push_back({nb::cast<int>(track[0]), nb::cast<size_t>(track[1]),
                nb::cast<std::string>(track[2]), nb::cast<int>(track[3]), nb::cast<int>(track[4]),
                nb::cast<float>(track[5]), nb::cast<float>(track[6]),
                nb::cast<std::vector<float>>(track[7]), nb::cast<std::vector<float>>(track[8]), nb::cast<std::vector<float>>(track[9])});
        }
        animations.push_back(std::move(animation));
    }
    std::vector<SurfaceSource> surfaces;
    for (auto value : nb::borrow<nb::iterable>(prepared[4])) {
        auto record = nb::borrow<nb::tuple>(value);
        const auto factors = nb::cast<std::array<float,6>>(record[0]);
        surfaces.push_back({factors[0], factors[1], factors[2], factors[3], factors[4], factors[5],
                           texture(record[1]), texture(record[2]), texture(record[3])});
    }
    std::vector<CameraSource> cameras;
    for (auto value : nb::borrow<nb::iterable>(prepared[5])) {
        auto record = nb::borrow<nb::tuple>(value);
        cameras.push_back({nb::cast<bool>(record[0]), nb::cast<Vec4>(record[1])});
    }
    std::vector<NodeSource> nodes;
    for (auto value : nb::borrow<nb::iterable>(prepared[6])) {
        auto record = nb::borrow<nb::tuple>(value);
        nodes.push_back({nb::cast<std::optional<std::string>>(record[0]), nb::cast<std::optional<std::string>>(record[1])});
    }
    const bool masked = nb::cast<bool>(prepared[7]);
    nb::gil_scoped_release release;
    return scene.load_asset(bytes, path, diffuse, morphs, animations, surfaces, cameras, nodes,
                            masked, clonable);
}

// Context manager returned by ImportedTarget.acquire(). It holds the Python object so that
// `with target.acquire() as t` yields the caller's own (possibly subclassed) target.
struct Acquisition {
    nb::object target;
};
// Context manager returned by HostTexture.write().
struct HostWrite {
    nb::object texture;
};

using Pixels = nb::ndarray<nb::c_contig, nb::device::cpu>;
// Returns channels and whether the array is float32. Other types would need a hidden copy.
std::pair<int, bool> pixel_layout(const Pixels& pixels) {
    const bool is_float = pixels.dtype() == nb::dtype<float>();
    if (!is_float && pixels.dtype() != nb::dtype<uint8_t>())
        throw nb::type_error("Texture pixels must be uint8 or float32");
    if (pixels.ndim() == 2) return {1, is_float};
    if (pixels.ndim() == 3) return {int(pixels.shape(2)), is_float};
    throw std::invalid_argument("Texture pixels must have shape (height, width) or (height, width, channels)");
}
size_t pixel_bytes(const Pixels& pixels) { return pixels.size() * pixels.itemsize(); }

using Vertices3 = nb::ndarray<nb::numpy, const float, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
using Vertices2 = nb::ndarray<nb::numpy, const float, nb::shape<-1, 2>, nb::c_contig, nb::device::cpu>;
using Vertices4 = nb::ndarray<nb::numpy, const float, nb::shape<-1, 4>, nb::c_contig, nb::device::cpu>;
using Triangles = nb::ndarray<nb::numpy, const uint32_t, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
MeshArrays mesh_arrays(size_t vertices, const std::optional<Vertices3>& positions, const std::optional<Vertices3>& normals,
                       const std::optional<Vertices2>& uvs, const std::optional<Vertices4>& colors) {
    MeshArrays result;
    result.vertices = vertices;
    auto rows = [&](size_t count, const char* name) {
        if (count != vertices)
            throw std::invalid_argument(std::string(name) + " must have " + std::to_string(vertices) + " rows, got " + std::to_string(count));
    };
    if (positions) { rows(positions->shape(0), "positions"); result.positions = positions->data(); }
    if (normals) { rows(normals->shape(0), "normals"); result.normals = normals->data(); }
    if (uvs) { rows(uvs->shape(0), "uvs"); result.uvs = uvs->data(); }
    if (colors) { rows(colors->shape(0), "colors"); result.colors = colors->data(); }
    return result;
}

nb::object wrap_texture(std::shared_ptr<detail::TextureData> data) {
    if (!data) return nb::none();
    return is_host_texture(data) ? nb::cast(HostTexture(data)) : nb::cast(Texture(data));
}
std::shared_ptr<detail::TextureData> unwrap_texture(nb::handle value) {
    if (value.is_none()) return nullptr;
    if (nb::isinstance<Texture>(value)) return nb::cast<const Texture&>(value).data();
    if (nb::isinstance<HostTexture>(value)) return nb::cast<const HostTexture&>(value).data();
    throw nb::type_error("Texture slots take a Texture, a HostTexture, or None");
}
int texture_slot(const std::string& slot) {
    if (slot == "base_color") return 0;
    if (slot == "emissive") return 1;
    throw std::invalid_argument("slot must be 'base_color' or 'emissive', got '" + slot + "'");
}
RenderOptions render_options(const std::optional<Camera>& camera, nb::handle viewport, bool clear) {
    RenderOptions options;
    if (camera) options.camera = &*camera;
    if (!viewport.is_none()) {
        const auto values = nb::cast<nb::tuple>(nb::module_::import_("builtins").attr("tuple")(viewport));
        if (values.size() != 4) throw std::invalid_argument("viewport must be (x, y, width, height)");
        options.has_viewport = true;
        const char* names[] = {"viewport x", "viewport y", "viewport width", "viewport height"};
        for (size_t i = 0; i < 4; ++i) options.viewport[i] = pixel_size(values[i], names[i]);
    }
    options.clear = clear;
    return options;
}
}

NB_MODULE(_native, module) {
    module.def("_decode_meshopt", [](nb::bytes source, size_t count, size_t stride,
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
        std::vector<unsigned char> output(count * stride);
        const auto* input = reinterpret_cast<const unsigned char*>(source.c_str());
        int status;
        if (mode == "ATTRIBUTES") status = fp_meshopt_decodeVertexBuffer(output.data(), count, stride, input, source.size());
        else if (mode == "TRIANGLES") status = fp_meshopt_decodeIndexBuffer(output.data(), count, stride, input, source.size());
        else if (mode == "INDICES") status = fp_meshopt_decodeIndexSequence(output.data(), count, stride, input, source.size());
        else throw AssetError("Unknown meshopt mode");
        if (status) throw AssetError("Invalid meshopt bitstream");
        if (filter == "OCTAHEDRAL") fp_meshopt_decodeFilterOct(output.data(), count, stride);
        else if (filter == "QUATERNION") fp_meshopt_decodeFilterQuat(output.data(), count, stride);
        else if (filter == "EXPONENTIAL") fp_meshopt_decodeFilterExp(output.data(), count, stride);
        else if (filter == "COLOR") fp_meshopt_decodeFilterColor(output.data(), count, stride);
        else if (filter != "NONE") throw AssetError("Unknown meshopt filter");
        return nb::bytes(output.data(), output.size());
    });
    module.def("set_log_level", &set_log_level, "level"_a,
               "Set the minimum Filament log severity for all renderers in this process.");
    module.def("current_gl_context", &current_gl_context);
    // Used by filly.integrations. Native calls avoid the host library's GL error checks,
    // which would report errors that other host code left pending.
    module.def("_create_host_texture", [](nb::handle width, nb::handle height) {
        return create_host_texture(pixel_size(width, "width"), pixel_size(height, "height"));
    }, "width"_a, "height"_a);
    module.def("_delete_host_texture", &delete_host_texture, "texture"_a);
    nb::exception<FillyError> error(module, "FillyError");
    nb::exception<BackendError>(module, "BackendError", error.ptr());
    nb::exception<AssetError>(module, "AssetError", error.ptr());
    nb::exception<InteropError>(module, "InteropError", error.ptr());

    nb::class_<Stats>(module, "Stats")
        .def_ro("cpu_submit_ms", &Stats::cpu_submit_ms)
        .def_ro("host_wait_ms", &Stats::host_wait_ms)
        .def_ro("host_release_ms", &Stats::host_release_ms)
        .def_ro("finish_ms", &Stats::finish_ms)
        .def_ro("readback_ms", &Stats::readback_ms)
        .def_ro("live_models", &Stats::live_models)
        .def_ro("live_lights", &Stats::live_lights)
        .def_ro("material_copies", &Stats::material_copies)
        .def_ro("frames_rendered", &Stats::frames_rendered);

    nb::class_<OffscreenTarget>(module, "OffscreenTarget")
        .def("close", &OffscreenTarget::close, nb::call_guard<nb::gil_scoped_release>())
        .def_prop_ro("closed", &OffscreenTarget::closed)
        .def_prop_ro("width", &OffscreenTarget::width)
        .def_prop_ro("height", &OffscreenTarget::height)
        .def("read", [](const OffscreenTarget& self) {
            const size_t width = self.width(), height = self.height();
            std::unique_ptr<std::vector<uint8_t>> pixels;
            {
                nb::gil_scoped_release release;
                pixels = std::make_unique<std::vector<uint8_t>>(self.read());
            }
            auto* ptr = pixels.get();
            nb::capsule owner(ptr, [](void* p) noexcept { delete static_cast<std::vector<uint8_t>*>(p); });
            pixels.release();
            return nb::ndarray<nb::numpy, uint8_t>(ptr->data(), {height, width, 4}, owner);
        });

    nb::class_<Acquisition>(module, "_Acquisition")
        .def("__enter__", [](Acquisition& self) {
            auto& target = nb::cast<ImportedTarget&>(self.target);
            {
                nb::gil_scoped_release release;
                target.acquire();
            }
            return self.target;
        })
        .def("__exit__", [](Acquisition& self, nb::handle, nb::handle, nb::handle) {
            auto& target = nb::cast<ImportedTarget&>(self.target);
            nb::gil_scoped_release release;
            target.release();
        }, nb::arg("exc_type").none(), nb::arg("exc_value").none(), nb::arg("traceback").none());

    nb::class_<Texture>(module, "Texture")
        .def_prop_ro("width", &Texture::width)
        .def_prop_ro("height", &Texture::height)
        .def_prop_ro("channels", &Texture::channels)
        .def_prop_ro("dtype", [](const Texture& self) { return self.is_float() ? "float32" : "uint8"; })
        .def_prop_ro("color_space", &Texture::color_space)
        .def_prop_ro("mipmaps", &Texture::mipmaps)
        .def("update", [](Texture& self, Pixels pixels) {
            const auto [channels, is_float] = pixel_layout(pixels);
            if (is_float != self.is_float() || uint32_t(channels) != self.channels()
                    || pixels.shape(0) != self.height() || pixels.shape(1) != self.width())
                throw std::invalid_argument("update() needs the shape and dtype of the texture: ("
                    + std::to_string(self.height()) + ", " + std::to_string(self.width()) + ", "
                    + std::to_string(self.channels()) + ") " + (self.is_float() ? "float32" : "uint8"));
            // The copy can wait for the driver thread when all staging buffers are queued.
            nb::gil_scoped_release release;
            self.update(pixels.data(), pixel_bytes(pixels));
        }, "pixels"_a)
        .def("close", &Texture::close)
        .def_prop_ro("closed", &Texture::closed)
        .def("__eq__", [](const Texture& self, nb::handle other) {
            return nb::isinstance<Texture>(other) && self.same(nb::cast<const Texture&>(other));
        })
        .def("__hash__", [](const Texture& self) { return self.key(); });

    nb::class_<HostWrite>(module, "_HostWrite")
        .def("__enter__", [](HostWrite& self) {
            auto& texture = nb::cast<HostTexture&>(self.texture);
            {
                nb::gil_scoped_release release;
                texture.begin_write();
            }
            return self.texture;
        })
        .def("__exit__", [](HostWrite& self, nb::handle, nb::handle, nb::handle) {
            nb::cast<HostTexture&>(self.texture).end_write();
        }, nb::arg("exc_type").none(), nb::arg("exc_value").none(), nb::arg("traceback").none());

    nb::class_<HostTexture>(module, "HostTexture")
        .def_prop_ro("width", &HostTexture::width)
        .def_prop_ro("height", &HostTexture::height)
        .def_prop_ro("color_space", &HostTexture::color_space)
        .def("write", [](nb::object self) {
            nb::cast<HostTexture&>(self);
            return HostWrite{self};
        })
        .def_prop_ro("writing", &HostTexture::writing)
        .def("close", &HostTexture::close, nb::call_guard<nb::gil_scoped_release>())
        .def_prop_ro("closed", &HostTexture::closed)
        .def("__eq__", [](const HostTexture& self, nb::handle other) {
            return nb::isinstance<HostTexture>(other) && self.same(nb::cast<const HostTexture&>(other));
        })
        .def("__hash__", [](const HostTexture& self) { return self.key(); });

    nb::class_<ImportedTarget>(module, "ImportedTarget")
        // Host adapters subclass this type and initialize it from import_gl_texture().
        .def(nb::init<const ImportedTarget&>())
        .def("acquire", [](nb::object self) {
            nb::cast<ImportedTarget&>(self);
            return Acquisition{self};
        })
        .def_prop_ro("acquired", &ImportedTarget::acquired)
        .def("close", &ImportedTarget::close, nb::call_guard<nb::gil_scoped_release>())
        .def_prop_ro("closed", &ImportedTarget::closed)
        .def_prop_ro("width", &ImportedTarget::width)
        .def_prop_ro("height", &ImportedTarget::height);

    nb::class_<Renderer>(module, "Renderer")
        .def("__init__", [](Renderer* self, nb::object context, bool precompiled) {
            uintptr_t handle = context.is_none() ? 0 : nb::cast<uintptr_t>(context);
            if (!context.is_none() && !handle) throw InteropError("shared_context cannot be zero; use None for offscreen rendering");
            new (self) Renderer(handle, precompiled);
        }, nb::kw_only(), nb::arg("shared_context").none() = nb::none(), "precompiled_shaders"_a = false)
        .def("create_scene", &Renderer::create_scene)
        .def("create_render_target", [](Renderer& self, nb::handle width, nb::handle height, const std::string& format, bool depth) {
            return self.create_render_target(pixel_size(width, "width"), pixel_size(height, "height"), format, depth);
        }, nb::kw_only(), "width"_a, "height"_a, "format"_a = "rgba8", "depth"_a = true)
        .def("import_gl_texture", [](Renderer& self, uint32_t texture, nb::handle width, nb::handle height, const std::string& format, bool depth) {
            return self.import_gl_texture(texture, pixel_size(width, "width"), pixel_size(height, "height"), format, depth);
        }, "texture_id"_a, nb::kw_only(), "width"_a, "height"_a, "format"_a = "rgba8", "depth"_a = true)
        .def("render", [](Renderer& self, const Scene& scene, const OffscreenTarget& target, std::optional<Camera> camera,
                          nb::handle viewport, bool clear) {
            const auto options = render_options(camera, viewport, clear);
            nb::gil_scoped_release release;
            self.render(scene, target, options);
        }, "scene"_a, "target"_a, nb::kw_only(), "camera"_a.none() = nb::none(), "viewport"_a.none() = nb::none(),
           "clear"_a = true)
        .def("render", [](Renderer& self, const Scene& scene, const ImportedTarget& target, std::optional<Camera> camera,
                          nb::handle viewport, bool clear) {
            const auto options = render_options(camera, viewport, clear);
            nb::gil_scoped_release release;
            self.render(scene, target, options);
        }, "scene"_a, "target"_a, nb::kw_only(), "camera"_a.none() = nb::none(), "viewport"_a.none() = nb::none(),
           "clear"_a = true)
        .def("create_texture", [](Renderer& self, Pixels pixels, const std::string& color_space, bool mipmaps,
                                  const std::string& filter, const std::string& wrap) {
            const auto [channels, is_float] = pixel_layout(pixels);
            return self.create_texture(int64_t(pixels.shape(1)), int64_t(pixels.shape(0)), channels, is_float,
                                       color_space, mipmaps, filter, wrap, pixels.data(), pixel_bytes(pixels));
        }, "pixels"_a, nb::kw_only(), "color_space"_a, "mipmaps"_a = false, "filter"_a = "linear", "wrap"_a = "repeat")
        .def("import_gl_input", [](Renderer& self, uint32_t texture, nb::handle width, nb::handle height,
                                   const std::string& color_space, const std::string& filter, const std::string& wrap) {
            return self.import_gl_input(texture, pixel_size(width, "width"), pixel_size(height, "height"),
                                        color_space, filter, wrap);
        }, "texture_id"_a, nb::kw_only(), "width"_a, "height"_a, "color_space"_a, "filter"_a = "linear",
           "wrap"_a = "repeat")
        .def("finish", &Renderer::finish, nb::call_guard<nb::gil_scoped_release>())
        .def("close", &Renderer::close, nb::call_guard<nb::gil_scoped_release>())
        .def_prop_ro("closed", &Renderer::closed)
        .def_prop_ro("precompiled_shaders", &Renderer::precompiled_shaders)
        .def_prop_ro("shared_context", &Renderer::shared_context)
        .def_prop_ro("stats", &Renderer::stats)
        .def("__enter__", [](Renderer& self) -> Renderer& {
            if (self.closed()) throw FillyError("Renderer is closed");
            return self;
        }, nb::rv_policy::reference_internal)
        .def("__exit__", [](Renderer& self, nb::handle, nb::handle, nb::handle) {
            nb::gil_scoped_release release;
            self.close();
        }, nb::arg("exc_type").none(), nb::arg("exc_value").none(), nb::arg("traceback").none());

    nb::class_<Scene>(module, "Scene")
        .def("close", &Scene::close, nb::call_guard<nb::gil_scoped_release>())
        .def_prop_ro("closed", &Scene::closed)
        .def("create_camera", &Scene::create_camera)
        .def_prop_rw("camera", &Scene::camera, &Scene::set_camera)
        .def("load", [](Scene& self, nb::object source, bool strict, bool clonable) {
            return load(self, source, strict, clonable);
        }, "source"_a, nb::kw_only(), "strict"_a = false, "clonable"_a = false)
        .def("add_directional_light", &Scene::add_directional_light, nb::kw_only(),
             "direction"_a, "intensity"_a = 50000.0f, "color"_a = Vec3{1, 1, 1})
        .def("add_sun_light", &Scene::add_sun_light, nb::kw_only(),
             "direction"_a, "intensity"_a = 100000.0f, "color"_a = Vec3{1, 1, 1},
             "angular_radius_deg"_a = 0.545f, "halo_size"_a = 10.0f, "halo_falloff"_a = 80.0f)
        .def("add_point_light", &Scene::add_point_light, nb::kw_only(), "position"_a,
             "intensity"_a = 100.0f, "color"_a = Vec3{1,1,1}, "range"_a = 10.0f)
        .def("add_spot_light", &Scene::add_spot_light, nb::kw_only(), "position"_a, "direction"_a,
             "intensity"_a = 100.0f, "color"_a = Vec3{1,1,1}, "range"_a = 10.0f,
             "inner"_a = 0.3f, "outer"_a = 0.6f)
        .def_prop_rw("encoding", &Scene::encoding, &Scene::set_encoding)
        .def_prop_rw("output_path", &Scene::output_path, &Scene::set_output_path)
        .def_prop_rw("tone_mapping", &Scene::tone_mapping, &Scene::set_tone_mapping)
        .def_prop_rw("antialiasing", &Scene::antialiasing, &Scene::set_antialiasing)
        .def_prop_rw("msaa", &Scene::msaa, [](Scene& self, nb::handle value) {
            if (PyBool_Check(value.ptr()) || !PyIndex_Check(value.ptr())) throw nb::type_error("msaa must be an integer");
            self.set_msaa(nb::cast<int>(nb::int_(value)));
        })
        .def_prop_rw("shadows", &Scene::shadows, &Scene::set_shadows)
        .def_prop_rw("refraction", &Scene::refraction, &Scene::set_refraction)
        .def_prop_rw("transparent", &Scene::transparent, &Scene::set_transparent)
        .def_prop_rw("dithering", &Scene::dithering, &Scene::set_dithering)
        .def_prop_rw("ssao", &Scene::ssao, &Scene::set_ssao)
        .def_prop_rw("bloom", &Scene::bloom, &Scene::set_bloom)
        .def_prop_rw("fog", &Scene::fog, &Scene::set_fog)
        .def("set_fog_options", &Scene::set_fog_options, nb::kw_only(), "color"_a = Vec3{1, 1, 1},
             "density"_a = 0.1f, "start"_a = 0.0f)
        .def_prop_rw("depth_of_field", &Scene::depth_of_field, &Scene::set_depth_of_field)
        .def_prop_rw("vignette", &Scene::vignette, &Scene::set_vignette)
        .def("set_vignette_options", &Scene::set_vignette_options, nb::kw_only(), "midpoint"_a = 0.5f,
             "roundness"_a = 0.5f, "feather"_a = 0.5f, "color"_a = Vec3{0, 0, 0})
        .def("create_mesh", [](Scene& self, Vertices3 positions, Triangles indices, std::optional<Vertices3> normals,
                               std::optional<Vertices2> uvs, std::optional<Vertices4> colors, Vec4 base_color,
                               float metallic, float roughness, Vec3 emissive, bool unlit, bool double_sided,
                               const std::string& alpha_mode) {
            auto arrays = mesh_arrays(positions.shape(0), positions, normals, uvs, colors);
            arrays.indices = indices.data();
            arrays.triangles = indices.shape(0);
            if (arrays.vertices < 3 || arrays.triangles < 1)
                throw std::invalid_argument("A mesh needs at least 3 positions and 1 triangle");
            for (size_t i = 0; i < arrays.vertices * 3; ++i)
                if (!std::isfinite(positions.data()[i])) throw std::invalid_argument("Positions must be finite");
            // A one-primitive glTF gives the mesh a node and the loader's own material; its
            // geometry is then replaced, so the model behaves like any loaded asset.
            float low[3], high[3];
            for (int axis = 0; axis < 3; ++axis) {
                low[axis] = high[axis] = positions.data()[axis];
                for (size_t i = 0; i < arrays.vertices; ++i) {
                    low[axis] = std::min(low[axis], positions.data()[3 * i + axis]);
                    high[axis] = std::max(high[axis], positions.data()[3 * i + axis]);
                }
            }
            auto document = nb::module_::import_("filly._mesh").attr("placeholder")(
                std::array<float, 3>{low[0], low[1], low[2]}, std::array<float, 3>{high[0], high[1], high[2]},
                "colors"_a = bool(colors), "base_color"_a = base_color, "metallic"_a = metallic,
                "roughness"_a = roughness, "emissive"_a = emissive, "unlit"_a = unlit,
                "double_sided"_a = double_sided, "alpha_mode"_a = alpha_mode);
            Model model = load(self, document, true, true);
            try {
                nb::gil_scoped_release release;
                model.attach_mesh(arrays);
            } catch (...) {
                model.close();
                throw;
            }
            return model;
        }, "positions"_a, "indices"_a, nb::kw_only(), "normals"_a.none() = nb::none(), "uvs"_a.none() = nb::none(),
           "colors"_a.none() = nb::none(), "base_color"_a = Vec4{1, 1, 1, 1}, "metallic"_a = 0.0f,
           "roughness"_a = 1.0f, "emissive"_a = Vec3{0, 0, 0}, "unlit"_a = false, "double_sided"_a = false,
           "alpha_mode"_a = "opaque")
        .def("load_environment", [](Scene& self, nb::object path, float intensity, float rotation) {
            auto os = nb::module_::import_("os");
            auto filename = nb::cast<std::string>(os.attr("fsdecode")(os.attr("fspath")(path)));
            nb::gil_scoped_release release; self.load_environment(filename, intensity, rotation);
        }, "path"_a, nb::kw_only(), "intensity"_a = 30000.0f, "rotation_deg"_a = 0.0f)
        .def("load_environment_ktx", [](Scene& self, nb::object ibl, nb::object skybox, float intensity, float rotation) {
            auto os = nb::module_::import_("os");
            auto name = [&](nb::object path) { return nb::cast<std::string>(os.attr("fsdecode")(os.attr("fspath")(path))); };
            const auto ibl_path = name(ibl);
            const auto skybox_path = skybox.is_none() ? std::string() : name(skybox);
            nb::gil_scoped_release release; self.load_environment_ktx(ibl_path, skybox_path, intensity, rotation);
        }, "ibl_path"_a, "skybox_path"_a.none() = nb::none(), nb::kw_only(), "intensity"_a = 30000.0f, "rotation_deg"_a = 0.0f)
        .def("set_environment", [](Scene& self, nb::ndarray<nb::numpy, const float, nb::shape<-1,-1,3>, nb::c_contig> pixels, float intensity, float rotation) {
            const auto height = pixels.shape(0), width = pixels.shape(1);
            if (height < 2 || height > 4096 || width != 2*height) throw std::invalid_argument("Environment must be a 2:1 RGB panorama with height 2..4096");
            std::vector<float> copy(pixels.data(), pixels.data()+pixels.size());
            nb::gil_scoped_release release; self.set_environment(copy, uint32_t(width), uint32_t(height), intensity, rotation);
        }, "pixels"_a, nb::kw_only(), "intensity"_a = 30000.0f, "rotation_deg"_a = 0.0f)
        .def("clear_environment", &Scene::clear_environment)
        .def_prop_rw("environment_intensity", &Scene::environment_intensity, &Scene::set_environment_intensity)
        .def_prop_rw("environment_visible", &Scene::environment_visible, &Scene::set_environment_visible)
        .def_prop_rw("environment_rotation", &Scene::environment_rotation, &Scene::set_environment_rotation)
        .def_prop_rw("background", &Scene::background, &Scene::set_background);

    nb::class_<Light>(module, "Light")
        .def("close", &Light::close)
        .def_prop_ro("closed", &Light::closed)
        .def_prop_ro("type", &Light::type)
        .def_prop_rw("color", &Light::color, &Light::set_color)
        .def_prop_rw("intensity", &Light::intensity, &Light::set_intensity)
        .def_prop_rw("position", &Light::position, &Light::set_position)
        .def_prop_rw("direction", &Light::direction, &Light::set_direction)
        .def_prop_rw("range", &Light::range, &Light::set_range)
        .def_prop_rw("casts_shadows", &Light::casts_shadows, &Light::set_casts_shadows)
        .def("set_shadow_options", &Light::set_shadow_options, nb::kw_only(),
             "map_size"_a = 1024, "constant_bias"_a = 0.001f, "normal_bias"_a = 1.0f)
        .def("set_spot_cone", &Light::set_spot_cone, "inner"_a, "outer"_a)
        .def_prop_ro("node", [](const Light& self) -> nb::object {
            return self.has_node() ? nb::cast(self.node()) : nb::none();
        })
        .def("__eq__", [](const Light& self, nb::handle other) {
            return nb::isinstance<Light>(other) && self.same(nb::cast<const Light&>(other));
        })
        .def("__hash__", [](const Light& self) { return self.key(); });

    nb::class_<AnimationInfo>(module, "AnimationInfo")
        .def_ro("name", &AnimationInfo::name)
        .def_ro("duration", &AnimationInfo::duration);

    nb::class_<Camera>(module, "Camera")
        .def_prop_rw("exposure", &Camera::exposure, &Camera::set_exposure)
        .def_prop_rw("focus_distance", &Camera::focus_distance, &Camera::set_focus_distance)
        .def_prop_rw("aperture", &Camera::aperture, &Camera::set_aperture)
        .def("set_perspective", [](Camera& self, double fov_y, std::optional<double> aspect, double near, double far) {
            self.set_perspective(fov_y, aspect_value(aspect), near, far);
        }, nb::kw_only(), "fov_y"_a, "aspect"_a.none() = nb::none(), "near"_a, "far"_a)
        .def("set_lens_projection", [](Camera& self, double focal, std::optional<double> aspect, double near, double far) {
            self.set_lens_projection(focal, aspect_value(aspect), near, far);
        }, nb::kw_only(), "focal_length_mm"_a, "aspect"_a.none() = nb::none(), "near"_a, "far"_a)
        .def("set_orthographic", [](Camera& self, std::optional<double> left, std::optional<double> right,
                                    std::optional<double> bottom, std::optional<double> top,
                                    std::optional<double> height, std::optional<std::array<double, 2>> center,
                                    double near, double far) {
            const bool bounds = left || right || bottom || top;
            if (height) {
                if (bounds) throw std::invalid_argument("Give either left/right/bottom/top or height, not both");
                const auto c = center.value_or(std::array<double, 2>{0, 0});
                self.set_orthographic_height(*height, c[0], c[1], near, far);
                return;
            }
            if (center) throw std::invalid_argument("center requires height");
            if (!(left && right && bottom && top))
                throw std::invalid_argument("Give all of left, right, bottom, and top, or give height");
            self.set_orthographic(*left, *right, *bottom, *top, near, far);
        }, nb::kw_only(), "left"_a.none() = nb::none(), "right"_a.none() = nb::none(),
           "bottom"_a.none() = nb::none(), "top"_a.none() = nb::none(),
           "height"_a.none() = nb::none(), "center"_a.none() = nb::none(), "near"_a, "far"_a)
        .def_prop_rw("position", &Camera::position, &Camera::set_position)
        .def("look_at", &Camera::look_at, "target"_a, "up"_a = Vec3{0, 1, 0})
        .def_prop_rw("transform", [](const Camera& self) { return to_array(self.transform()); },
            [](Camera& self, const MatrixInput& value) { self.set_transform(from_array(value)); }, nb::rv_policy::move)
        .def_prop_ro("view_matrix", [](const Camera& self) { return to_array(self.view_matrix()); }, nb::rv_policy::move)
        .def_prop_ro("projection", [](const Camera& self) { return to_array(self.projection()); }, nb::rv_policy::move)
        .def_prop_ro("node", [](const Camera& self) -> nb::object {
            return self.has_node() ? nb::cast(self.node()) : nb::none();
        })
        .def("__eq__", [](const Camera& self, nb::handle other) {
            return nb::isinstance<Camera>(other) && self.same(nb::cast<const Camera&>(other));
        })
        .def("__hash__", [](const Camera& self) { return self.key(); });

    nb::class_<Model>(module, "Model")
        .def("close", &Model::close)
        .def_prop_ro("closed", &Model::closed)
        .def("clone", &Model::clone)
        .def("camera", [](const Model& self, nb::handle key) {
            return by_key(key, [&](const std::string& n) { return self.camera(n); }, [&](int64_t i) { return self.camera(i); });
        }, "key"_a)
        .def_prop_ro("cameras", &Model::cameras)
        .def("light", [](const Model& self, nb::handle key) {
            return by_key(key, [&](const std::string& n) { return self.light(n); }, [&](int64_t i) { return self.light(i); });
        }, "key"_a)
        .def_prop_ro("lights", &Model::lights)
        .def_prop_ro("animations", &Model::animations)
        .def("apply_animation", [](Model& self, nb::handle key, float time, bool loop) {
            by_key(key, [&](const std::string& n) { self.apply_animation(n, time, loop); return 0; },
                   [&](int64_t i) { self.apply_animation(i, time, loop); return 0; });
        }, "key"_a, "time"_a, nb::kw_only(), "loop"_a = true)
        .def("reset_animation", &Model::reset_animation)
        .def_prop_ro("variants", &Model::variants)
        .def("apply_variant", [](Model& self, nb::handle key) {
            by_key(key, [&](const std::string& n) { self.apply_variant(n); return 0; },
                   [&](int64_t i) { self.apply_variant(i); return 0; });
        }, "key"_a)
        .def_prop_ro("bounds", &Model::bounds)
        .def("update_mesh", [](Model& self, std::optional<Vertices3> positions, std::optional<Vertices3> normals,
                               std::optional<Vertices2> uvs, std::optional<Vertices4> colors) {
            const auto arrays = mesh_arrays(self.vertex_count(), positions, normals, uvs, colors);
            nb::gil_scoped_release release;
            self.update_mesh(arrays);
        }, nb::kw_only(), "positions"_a.none() = nb::none(), "normals"_a.none() = nb::none(),
           "uvs"_a.none() = nb::none(), "colors"_a.none() = nb::none())
        .def_prop_rw("transform", [](const Model& self) { return to_array(self.transform()); },
            [](Model& self, const MatrixInput& value) { self.set_transform(from_array(value)); }, nb::rv_policy::move)
        .def_prop_rw("position", &Model::position, &Model::set_position)
        .def_prop_rw("visible", &Model::visible, &Model::set_visible)
        .def("node", [](const Model& self, nb::handle key) {
            return by_key(key, [&](const std::string& n) { return self.node(n); }, [&](int64_t i) { return self.node(i); });
        }, "key"_a)
        .def_prop_ro("nodes", &Model::nodes)
        .def_prop_ro("node_names", &Model::node_names)
        .def("material", &Model::material, "name"_a,
             "Return the glTF material shared by every mesh slot that uses it in this model.")
        .def_prop_ro("material_names", &Model::material_names)
        .def_prop_rw("scale", [](const Model& self) { return self.root().scale(); },
            [](Model& self, Vec3 value) { self.root().set_scale(value); })
        .def_prop_rw("quaternion", [](const Model& self) { return self.root().quaternion(); },
            [](Model& self, Vec4 value) { self.root().set_quaternion(value); })
        .def_prop_rw("rotation_euler_rad", [](const Model& self) { return self.root().rotation_euler_rad(); },
            [](Model& self, Vec3 value) { self.root().set_rotation_euler_rad(value); })
        .def_prop_rw("rotation_euler_deg", [](const Model& self) { return self.root().rotation_euler_deg(); },
            [](Model& self, Vec3 value) { self.root().set_rotation_euler_deg(value); });

    nb::class_<Node>(module, "Node")
        .def_prop_ro("name", &Node::name)
        .def_prop_ro("mesh_name", &Node::mesh_name)
        .def_prop_ro("index", &Node::index)
        .def_prop_ro("parent", [](const Node& self) -> nb::object {
            if (!self.has_parent()) return nb::none();
            return nb::cast(self.parent());
        })
        .def_prop_ro("children", &Node::children)
        .def_prop_ro("morph_target_count", &Node::morph_target_count)
        .def("set_morph_weights", &Node::set_morph_weights, "weights"_a)
        .def_prop_rw("transform", [](const Node& self) { return to_array(self.transform()); },
            [](Node& self, const MatrixInput& value) { self.set_transform(from_array(value)); }, nb::rv_policy::move)
        .def_prop_rw("position", &Node::position, &Node::set_position)
        .def_prop_rw("scale", &Node::scale, &Node::set_scale)
        .def_prop_rw("quaternion", &Node::quaternion, &Node::set_quaternion)
        .def_prop_rw("rotation_euler_rad", &Node::rotation_euler_rad, &Node::set_rotation_euler_rad)
        .def_prop_rw("rotation_euler_deg", &Node::rotation_euler_deg, &Node::set_rotation_euler_deg)
        .def("material", &Node::material, "slot"_a = 0,
             "Return this node slot's own material. The first call copies the shared material.")
        .def("__eq__", [](const Node& self, nb::handle other) {
            return nb::isinstance<Node>(other) && self.same(nb::cast<const Node&>(other));
        })
        .def("__hash__", [](const Node& self) { return self.key(); })
        .def("__repr__", [](const Node& self) {
            const auto name = self.name();
            return "<Node " + std::to_string(self.index()) + (name ? " '" + *name + "'" : std::string()) + ">";
        });
    nb::class_<Material>(module, "Material")
        .def_prop_rw("base_color", &Material::base_color, &Material::set_base_color)
        .def_prop_rw("metallic", &Material::metallic, &Material::set_metallic)
        .def_prop_rw("roughness", &Material::roughness, &Material::set_roughness)
        .def_prop_rw("emissive", &Material::emissive, &Material::set_emissive)
        .def_prop_rw("base_color_texture", [](const Material& self) { return wrap_texture(self.texture(0)); },
            [](Material& self, nb::handle value) {
                auto texture = unwrap_texture(value);
                // A new slot combination compiles a material.
                nb::gil_scoped_release release;
                self.set_texture(0, std::move(texture));
            },
            nb::for_setter(nb::arg("value").none()))
        .def_prop_rw("emissive_texture", [](const Material& self) { return wrap_texture(self.texture(1)); },
            [](Material& self, nb::handle value) {
                auto texture = unwrap_texture(value);
                // A new slot combination compiles a material.
                nb::gil_scoped_release release;
                self.set_texture(1, std::move(texture));
            },
            nb::for_setter(nb::arg("value").none()))
        .def("set_texture_transform", [](Material& self, const std::string& slot, std::array<float, 2> offset,
                                         std::array<float, 2> scale, float rotation_deg) {
            self.set_texture_transform(texture_slot(slot), offset, scale, rotation_deg * float(3.141592653589793 / 180.0));
        }, "slot"_a, nb::kw_only(), "offset"_a = std::array<float, 2>{0, 0}, "scale"_a = std::array<float, 2>{1, 1},
           "rotation_deg"_a = 0.0f);
}
