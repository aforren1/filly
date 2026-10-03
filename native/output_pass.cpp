#include "output_pass.h"
#include "renderer.h"

#include <filament/Engine.h>
#include <filament/Material.h>

#include <cstddef>
#include <string>

// Packages compiled from native/materials/encode.mat.in and fxaa.mat (output.cmake).
extern "C" {
extern const unsigned char filly_encode_material_data[];
extern const size_t filly_encode_material_size;
extern const unsigned char filly_encode_graded_material_data[];
extern const size_t filly_encode_graded_material_size;
extern const unsigned char filly_fxaa_material_data[];
extern const size_t filly_fxaa_material_size;
}

namespace filly::detail {
namespace {
filament::Material* build(filament::Engine& engine, const unsigned char* data, size_t size, const char* what) {
    auto* material = filament::Material::Builder().package(data, size).build(engine);
    if (!material) throw BackendError(std::string("Could not create the ") + what + " material");
    return material;
}
}

filament::Material* build_encode_material(filament::Engine& engine, bool graded) {
    return graded ? build(engine, filly_encode_graded_material_data, filly_encode_graded_material_size, "graded encode")
                  : build(engine, filly_encode_material_data, filly_encode_material_size, "encode");
}

filament::Material* build_fxaa_material(filament::Engine& engine) {
    return build(engine, filly_fxaa_material_data, filly_fxaa_material_size, "FXAA");
}
}
