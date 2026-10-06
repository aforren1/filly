#pragma once
#include "renderer.h"
#include "gltf_prepare.h"
#include <gltfio/MaterialProvider.h>
#include <filament/Texture.h>

namespace filly::detail {
// filly's glTF material provider (native/archive_materials.cpp). Every glTF material is an
// instance of an entry of the material archive that the build compiles from native/materials.
filament::gltfio::MaterialProvider* create_material_provider(filament::Engine*);
// The asset that createAsset() or createInstance() is building, or null. The provider reads its
// material table for instances whose extras carry a glTF material index; see load_asset().
void set_prepared_asset(filament::gltfio::MaterialProvider*, const PreparedAsset*);
// Textures that provider-built materials sample; the caller destroys them with the asset.
std::vector<filament::Texture*> take_material_textures(filament::gltfio::MaterialProvider*);
using MaterialBinding = std::pair<size_t, filament::MaterialInstance*>;
std::vector<MaterialBinding> take_material_bindings(filament::gltfio::MaterialProvider*);
// The key that the provider used for each instance it created.
struct MaterialRecord {
    filament::MaterialInstance* instance;
    filament::gltfio::MaterialKey key;
};
std::vector<MaterialRecord> take_material_records(filament::gltfio::MaterialProvider*);
void configure_diffuse_environment(filament::gltfio::MaterialProvider*, filament::MaterialInstance*,
                                  filament::Texture*, float intensity, float rotation);
}
