#pragma once
#include "renderer.h"
#include "gltf_prepare.h"
#include <gltfio/MaterialProvider.h>
#include <filament/Texture.h>
#include <filament/TextureSampler.h>

namespace filly::detail {
filament::gltfio::MaterialProvider* create_material_provider(filament::Engine*, bool compiled);
// The asset that createAsset() or createInstance() is building, or null. The provider reads its
// material table for instances whose extras carry a glTF material index; see load_asset().
void set_prepared_asset(filament::gltfio::MaterialProvider*, const PreparedAsset*);
// Lobes of filly's surface material. With none, the material is the standard glTF material.
enum SurfaceLobes : unsigned { surface_anisotropy = 1, surface_iridescence = 2 };
unsigned surface_lobes(const SurfaceSource&);
filament::Material* create_surface_material(filament::Engine*, const filament::gltfio::MaterialKey&,
                                           const filament::gltfio::UvMap&, const char*, unsigned lobes);
// Textures that provider-built materials sample; the caller destroys them with the asset.
std::vector<filament::Texture*> take_material_textures(filament::gltfio::MaterialProvider*);
using MaterialBinding = std::pair<size_t, filament::MaterialInstance*>;
std::vector<MaterialBinding> take_material_bindings(filament::gltfio::MaterialProvider*);
// The key that the provider used for each instance it created. Custom materials cannot be
// rebuilt from a key and are marked as such.
struct MaterialRecord {
    filament::MaterialInstance* instance;
    filament::gltfio::MaterialKey key;
    bool custom;
};
std::vector<MaterialRecord> take_material_records(filament::gltfio::MaterialProvider*);
// An instance of the standard glTF material for key. The key is updated to the one used.
filament::MaterialInstance* create_material_instance(filament::gltfio::MaterialProvider*,
                                                    filament::gltfio::MaterialKey& key, const char* label);
void configure_diffuse_environment(filament::gltfio::MaterialProvider*, filament::MaterialInstance*,
                                  filament::Texture*, float intensity, float rotation);
}
