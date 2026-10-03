#include "renderer.h"
#include "gltf_prepare.h"

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/array.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <algorithm>
#include <cmath>
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
// filly.AssetCompatibilityWarning, created in the module initializer.
PyObject* compatibility_warning = nullptr;

std::string fs_path(nb::handle path) {
    auto os = nb::module_::import_("os");
    return nb::cast<std::string>(os.attr("fsdecode")(os.attr("fspath")(path)));
}

// Loads with the GIL released for the whole native load, including preparation. Messages
// become AssetCompatibilityWarning; if the warning filter turns one into an error, the model
// is closed, as the load would not have happened when preparation raised the warning itself.
Model load(Scene& scene, nb::handle source, bool strict, bool clonable) {
    std::vector<std::string> warnings;
    std::optional<Model> model;
    auto warn = [&] {
        for (const auto& message : warnings) {
            if (PyErr_WarnEx(compatibility_warning, message.c_str(), 1) == 0) continue;
            if (model) model->close();
            throw nb::python_error();
        }
    };
    try {
        if (nb::isinstance<nb::bytes>(source)) {
            const auto blob = nb::borrow<nb::bytes>(source);
            const auto* first = reinterpret_cast<const uint8_t*>(blob.c_str());
            std::vector<uint8_t> bytes(first, first + blob.size());
            nb::gil_scoped_release release;
            model = scene.load(std::move(bytes), strict, clonable, warnings);
        } else {
            const auto path = fs_path(source);
            nb::gil_scoped_release release;
            model = scene.load(path, strict, clonable, warnings);
        }
    } catch (...) {
        // Warnings raised before the error still reach the caller, as they did from Python.
        warn();
        throw;
    }
    warn();
    return *model;
}

nb::dict shape_dict(ShapeArrays&& shape) {
    auto array = [](auto&& values, size_t columns) {
        using T = typename std::decay_t<decltype(values)>::value_type;
        auto* owned = new std::vector<T>(std::move(values));
        nb::capsule owner(owned, [](void* p) noexcept { delete static_cast<std::vector<T>*>(p); });
        return nb::ndarray<nb::numpy, T>(owned->data(), {owned->size() / columns, columns}, owner);
    };
    nb::dict result;
    result["positions"] = array(std::move(shape.positions), 3);
    result["normals"] = array(std::move(shape.normals), 3);
    result["uvs"] = array(std::move(shape.uvs), 2);
    result["indices"] = array(std::move(shape.indices), 3);
    return result;
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
    // For tests of the decoder that preparation uses for EXT_ and KHR_meshopt_compression.
    module.def("_decode_meshopt", [](nb::bytes source, size_t count, size_t stride,
                                      const std::string& mode, const std::string& filter) {
        const auto output = detail::decode_meshopt(reinterpret_cast<const uint8_t*>(source.c_str()), source.size(),
                                                   count, stride, mode, filter);
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
    compatibility_warning = PyErr_NewExceptionWithDoc("filly.AssetCompatibilityWarning",
        "An optional asset feature cannot be reproduced by this renderer.", PyExc_UserWarning, nullptr);
    if (!compatibility_warning) throw nb::python_error();
    module.attr("AssetCompatibilityWarning") = nb::handle(compatibility_warning);
    // The build's glTF material path (CMake FILLY_MATERIALS), for tests and diagnostics.
    module.attr("_materials") = FILLY_MATERIALS_ARCHIVE ? "archive" : "runtime";

    auto shapes = module.def_submodule("shapes",
        "Vertex arrays for simple shapes, for ``Scene.create_mesh(**shape)``.\n\n"
        "Each function returns a dict with ``positions`` (N x 3), ``normals`` (N x 3), ``uvs`` (N x 2),\n"
        "all float32, and ``indices`` (M x 3, uint32). Front faces wind counterclockwise, as in glTF.\n"
        "UVs follow glTF: (0, 0) is the top-left corner of an image. Units are scene units.");
    shapes.def("plane", [](double width, double height, std::array<int64_t, 2> segments) {
        return shape_dict(shape_plane(width, height, segments[0], segments[1]));
    }, "width"_a = 1.0, "height"_a = 1.0, nb::kw_only(), "segments"_a = std::array<int64_t, 2>{1, 1},
       "A rectangle in the XY plane, centered on the origin, facing +Z.\n\n"
       "``segments`` is (columns, rows). More segments let ``Model.update_mesh()`` deform it.");
    shapes.def("box", [](double width, double height, double depth) {
        return shape_dict(shape_box(width, height, depth));
    }, "width"_a = 1.0, "height"_a = 1.0, "depth"_a = 1.0,
       "An axis-aligned box centered on the origin. Each face has its own vertices and full UVs.");
    shapes.def("uv_sphere", [](double radius, int64_t segments, int64_t rings) {
        return shape_dict(shape_uv_sphere(radius, segments, rings));
    }, "radius"_a = 0.5, nb::kw_only(), "segments"_a = 32, "rings"_a = 16,
       "A sphere centered on the origin with poles on the Y axis.\n\n"
       "U runs once around from +Z, V from the top pole (0) to the bottom pole (1). The seam and the\n"
       "poles have duplicate vertices so the UVs stay continuous.");
    shapes.def("cylinder", [](double radius, double height, int64_t segments, bool caps) {
        return shape_dict(shape_cylinder(radius, height, segments, caps));
    }, "radius"_a = 0.5, "height"_a = 1.0, nb::kw_only(), "segments"_a = 32, "caps"_a = true,
       "A cylinder centered on the origin with its axis on Y.\n\n"
       "The side has U around from +Z and V from top (0) to bottom (1). Caps map a disc onto the\n"
       "unit UV square.");
    shapes.attr("__all__") = nb::make_tuple("box", "cylinder", "plane", "uv_sphere");

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

    nb::class_<Preparation>(module, "Preparation")
        .def("ready", &Preparation::ready,
             "Let the backend make progress, then report whether every program is compiled.")
        .def_prop_ro("pending", &Preparation::pending);

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
        .def("prepare", &Renderer::prepare, "scene"_a)
        .def("finish", &Renderer::finish, nb::call_guard<nb::gil_scoped_release>())
        .def("close", &Renderer::close, nb::call_guard<nb::gil_scoped_release>())
        .def_prop_ro("closed", &Renderer::closed)
        .def_prop_ro("precompiled_shaders", &Renderer::precompiled_shaders)
        .def_prop_ro("shared_context", &Renderer::shared_context)
        .def_prop_ro("gl_platform", &Renderer::gl_platform)
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
        .def("load", &load, "source"_a, nb::kw_only(), "strict"_a = false, "clonable"_a = false)
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
            const MeshMaterial material{base_color, metallic, roughness, emissive, unlit, double_sided, alpha_mode};
            nb::gil_scoped_release release;
            return self.create_mesh(arrays, material);
        }, "positions"_a, "indices"_a, nb::kw_only(), "normals"_a.none() = nb::none(), "uvs"_a.none() = nb::none(),
           "colors"_a.none() = nb::none(), "base_color"_a = Vec4{1, 1, 1, 1}, "metallic"_a = 0.0f,
           "roughness"_a = 1.0f, "emissive"_a = Vec3{0, 0, 0}, "unlit"_a = false, "double_sided"_a = false,
           "alpha_mode"_a = "opaque")
        .def("load_environment", [](Scene& self, nb::object path, float intensity, float rotation) {
            const auto filename = fs_path(path);
            nb::gil_scoped_release release; self.load_environment(filename, intensity, rotation);
        }, "path"_a, nb::kw_only(), "intensity"_a = 30000.0f, "rotation_deg"_a = 0.0f)
        .def("load_environment_ktx", [](Scene& self, nb::object ibl, nb::object skybox, float intensity, float rotation) {
            const auto ibl_path = fs_path(ibl);
            const auto skybox_path = skybox.is_none() ? std::string() : fs_path(skybox);
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
        .def("frame", [](Camera& self, nb::handle target, double fill, std::optional<Vec3> direction, Vec3 up,
                         const std::string& fit, std::optional<double> near, std::optional<double> far,
                         std::optional<double> aspect) {
            const FrameOptions options{fill, direction, up, fit, near, far, aspect};
            if (nb::isinstance<Model>(target)) return self.frame(nb::cast<const Model&>(target), options);
            if (nb::isinstance<Node>(target)) return self.frame(nb::cast<const Node&>(target), options);
            std::array<Vec3, 2> box;
            if (!nb::try_cast(target, box))
                throw nb::type_error("target must be a Model, a Node, or a (min, max) box of two 3-vectors");
            return self.frame(box, options);
        }, "target"_a, nb::kw_only(), "fill"_a = 0.8, "direction"_a.none() = nb::none(), "up"_a = Vec3{0, 1, 0},
           "fit"_a = "sphere", "near"_a.none() = nb::none(), "far"_a.none() = nb::none(),
           "aspect"_a.none() = nb::none(),
           "Aim at the target's center and set the distance, and near and far, so that it fills `fill` "
           "of the view. Returns the distance to the center.")
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
        .def_prop_ro("bounds", &Node::bounds)
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
        .def_prop_ro("_shader", &Material::shader)
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
