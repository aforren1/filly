// filly's glTF material provider. Every glTF material gets an instance of an entry in filly's
// precompiled material archive (native/materials). Nothing is compiled at run time. See
// docs/explanation/material-precompilation.md.
#include "materials.h"
#include "webp_provider.h"
#include <filament/Engine.h>
#include <filament/Material.h>
#include <filament/MaterialInstance.h>
#include <filament/Texture.h>
#include <filament/TextureSampler.h>
#include <gltfio/TextureProvider.h>
#include <math/mat3.h>
#include <uberz/ReadableArchive.h>
#include <algorithm>
#include <array>
#include <charconv>
#include <cstring>
#include <memory>
#include <string>
#include <utility>

extern "C" {
// The SDK ships zstd.lib without its header. These are the stable zstd 1.x entry points.
unsigned long long ZSTD_getFrameContentSize(const void* src, size_t srcSize);
size_t ZSTD_decompress(void* dst, size_t dstCapacity, const void* src, size_t compressedSize);
unsigned ZSTD_isError(size_t code);
// Written by native/materials/embed.cmake.
extern const unsigned char filly_material_archive_data[];
extern const size_t filly_material_archive_size;
}

namespace filly::detail {
namespace f = filament;
namespace g = filament::gltfio;
namespace m = filament::math;
namespace {
constexpr size_t entry_count = size_t(ArchiveEntry::count);
// The uberz spec flag of each entry; see native/materials/materials.cmake.
constexpr const char* entry_names[entry_count] = {
    "LitCore", "LitExtended", "LitSpecular", "LitAnisotropy", "RefractionThin", "RefractionThinSpecular",
    "RefractionSolid", "RefractionSolidSpecular", "RefractionThinAnisotropy", "RefractionSolidAnisotropy",
    "SpecularGlossiness", "Unlit", "DiffuseTransmission"};
// Parameter prefixes of the extension roles, in ExtensionRole order.
constexpr const char* role_names[extension_role_count] = {
    "transmission", "volumeThickness", "anisotropy", "iridescence", "clearCoat", "sheenColor",
    "specularColor", "specular", "iridescenceThickness", "clearCoatRoughness", "sheenRoughness",
    "clearCoatNormal"};
constexpr const char* ext_samplers[] = {"ext0", "ext1", "ext2", "ext3"};

size_t blending_index(g::AlphaMode mode) {
    return mode == g::AlphaMode::MASK ? 1 : mode == g::AlphaMode::BLEND ? 2 : 0;
}
// Every entry reads TEXCOORD_0 as UV0 and TEXCOORD_1 as UV1. A fixed layout lets any entry,
// variant material, or runtime texture use the vertex buffers that gltfio builds.
g::UvMap archive_uvmap() {
    g::UvMap uv{};
    uv[0] = g::UvSet::UV0;
    uv[1] = g::UvSet::UV1;
    return uv;
}
// The entry for a key without a glTF material plan: gltfio's default material and runtime
// requests. Plans also decide anisotropy, iridescence, and diffuse transmission.
ArchiveEntry entry_for(const g::MaterialKey& key) {
    if (key.unlit) return ArchiveEntry::Unlit;
    if (key.useSpecularGlossiness) return ArchiveEntry::SpecularGlossiness;
    if (key.hasVolume) return key.hasSpecular ? ArchiveEntry::RefractionSolidSpecular : ArchiveEntry::RefractionSolid;
    if (key.hasTransmission) return key.hasSpecular ? ArchiveEntry::RefractionThinSpecular : ArchiveEntry::RefractionThin;
    if (key.hasSpecular) return ArchiveEntry::LitSpecular;
    if (key.hasClearCoat || key.hasSheen) return ArchiveEntry::LitExtended;
    return ArchiveEntry::LitCore;
}
// The glTF material index that load_asset() put in a material's extras while gltfio created the
// instance; SIZE_MAX for gltfio's default material and for runtime requests.
size_t material_marker(const char* extras) {
    if (!extras || extras[0] != '#' || extras[1] != 'm') return SIZE_MAX;
    const char* end = extras + std::strlen(extras);
    size_t index = SIZE_MAX;
    const auto result = std::from_chars(extras + 2, end, index);
    return result.ec == std::errc() && result.ptr == end ? index : SIZE_MAX;
}
m::mat3f matrix(const std::array<float, 9>& values) {
    m::mat3f result;
    for (int col = 0; col < 3; ++col) for (int row = 0; row < 3; ++row) result[col][row] = values[col * 3 + row];
    return result;
}
f::TextureSampler sampler_for(const AssetTexture& texture) {
    using S = f::TextureSampler;
    auto wrap = [](int mode) { return mode == 33071 ? S::WrapMode::CLAMP_TO_EDGE : mode == 33648 ? S::WrapMode::MIRRORED_REPEAT : S::WrapMode::REPEAT; };
    S sampler;
    sampler.setWrapModeS(wrap(texture.wrap_s));
    sampler.setWrapModeT(wrap(texture.wrap_t));
    const S::MinFilter filters[] = {S::MinFilter::NEAREST_MIPMAP_NEAREST, S::MinFilter::LINEAR_MIPMAP_NEAREST,
                                   S::MinFilter::NEAREST_MIPMAP_LINEAR, S::MinFilter::LINEAR_MIPMAP_LINEAR};
    sampler.setMinFilter(texture.min_filter == 9728 ? S::MinFilter::NEAREST : texture.min_filter == 9729
                         ? S::MinFilter::LINEAR : filters[texture.min_filter - 9984]);
    sampler.setMagFilter(texture.mag_filter == 9728 ? S::MagFilter::NEAREST : S::MagFilter::LINEAR);
    return sampler;
}
template <class T> void set_if(f::MaterialInstance* mi, const char* name, const T& value) {
    if (mi->getMaterial()->hasParameter(name)) mi->setParameter(name, value);
}
// Sets the UV set of each core texture, or -1. Textures on a third set are removed from the key,
// so that gltfio does not bind them; preparation warned about them.
void set_core_indices(f::MaterialInstance* mi, g::MaterialKey& k) {
    const auto use = [](bool present, uint8_t uv) { return present && uv < 2 ? int(uv) : -1; };
    const int base = use(k.hasBaseColorTexture, k.baseColorUV), mr = use(k.hasMetallicRoughnessTexture, k.metallicRoughnessUV);
    const int normal = use(k.hasNormalTexture, k.normalUV), ao = use(k.hasOcclusionTexture, k.aoUV);
    const int emissive = use(k.hasEmissiveTexture, k.emissiveUV);
    k.hasBaseColorTexture = base >= 0;
    k.hasMetallicRoughnessTexture = mr >= 0;
    k.hasNormalTexture = normal >= 0;
    k.hasOcclusionTexture = ao >= 0;
    k.hasEmissiveTexture = emissive >= 0;
    mi->setParameter("baseColorIndex", base);
    mi->setParameter("metallicRoughnessIndex", mr);
    mi->setParameter("normalIndex", normal);
    mi->setParameter("aoIndex", ao);
    mi->setParameter("emissiveIndex", emissive);
}

class ArchiveProvider final : public g::MaterialProvider {
public:
    f::Engine* engine;
    // The asset being created or cloned; null otherwise.
    const PreparedAsset* asset = nullptr;
    std::vector<MaterialBinding> bindings;
    std::vector<MaterialRecord> records;
    // Decoded extension and diffuse-transmission textures of the current asset. The asset owns
    // them from take_material_textures() on.
    std::vector<f::Texture*> textures;
    f::Texture* dummy = nullptr;
    f::Texture* dummy_cube = nullptr;

    explicit ArchiveProvider(f::Engine* e) : engine(e) {
        const auto length = ZSTD_getFrameContentSize(filly_material_archive_data, filly_material_archive_size);
        if (length == 0 || length > (256ull << 20)) throw BackendError("Invalid filly material archive");
        storage_.resize(size_t((length + 7) / 8));
        const auto written = ZSTD_decompress(storage_.data(), size_t(length), filly_material_archive_data,
                                             filly_material_archive_size);
        if (ZSTD_isError(written) || written != length) throw BackendError("Could not decompress the filly material archive");
        auto* archive = reinterpret_cast<f::uberz::ReadableArchive*>(storage_.data());
        if (!f::uberz::convertOffsetsToPointers(archive, size_t(length))) throw BackendError("Invalid filly material archive");
        for (auto& row : specs_) row.fill(nullptr);
        for (uint64_t i = 0; i < archive->specsCount; ++i) {
            const auto& spec = archive->specs[i];
            const size_t blending = spec.blendingMode == f::BlendingMode::MASKED ? 1
                : spec.blendingMode == f::BlendingMode::FADE ? 2 : 0;
            for (uint16_t j = 0; j < spec.flagsCount; ++j)
                for (size_t k = 0; k < entry_count; ++k)
                    if (!std::strcmp(spec.flags[j].name, entry_names[k])) specs_[k][blending] = &spec;
        }
        for (const auto& row : specs_)
            for (const auto* spec : row)
                if (!spec) throw BackendError("The filly material archive lacks an entry; rebuild the module");
        for (auto& row : materials_) row.fill(nullptr);
        dummy = f::Texture::Builder().width(1).height(1).levels(1).format(f::Texture::InternalFormat::RGBA8).build(*e);
        dummy_cube = f::Texture::Builder().width(1).height(1).levels(1).sampler(f::Texture::Sampler::SAMPLER_CUBEMAP)
            .format(f::Texture::InternalFormat::RGBA8).build(*e);
        static const uint8_t pixel[] = {255, 255, 255, 255};
        dummy->setImage(*e, 0, f::Texture::PixelBufferDescriptor(pixel, 4, f::Texture::Format::RGBA, f::Texture::Type::UBYTE));
    }
    ~ArchiveProvider() override {
        stb_.reset(); ktx2_.reset(); webp_.reset();
        for (auto* texture : textures) engine->destroy(texture);
        if (dummy) engine->destroy(dummy);
        if (dummy_cube) engine->destroy(dummy_cube);
    }

    f::Material* entry(ArchiveEntry kind, g::AlphaMode alpha) {
        auto& slot = materials_[size_t(kind)][blending_index(alpha)];
        if (slot) return slot;
        const auto* spec = specs_[size_t(kind)][blending_index(alpha)];
        slot = f::Material::Builder().package(spec->package, spec->packageByteCount).build(*engine);
        if (!slot) throw AssetError(std::string("Could not create archive material ") + entry_names[size_t(kind)]);
        built_.push_back(slot);
        return slot;
    }
    void set_asset(const PreparedAsset* value) { asset = value; }

    f::Material* getMaterial(g::MaterialKey* config, g::UvMap* uvmap, const char* label) override {
        // gltfio asks while it builds vertex buffers, before filly knows the glTF material; the
        // result only supplies required attributes, which all lit entries share.
        *uvmap = archive_uvmap();
        return entry(config->unlit ? ArchiveEntry::Unlit : ArchiveEntry::LitCore, g::AlphaMode::OPAQUE);
    }

    f::MaterialInstance* createMaterialInstance(g::MaterialKey* config, g::UvMap* uvmap, const char* label,
                                                const char* extras) override {
        const size_t index = material_marker(extras);
        const bool known = asset && index < asset->materials.size() && index < asset->plans.size();
        const MaterialSource* source = known ? &asset->materials[index] : nullptr;
        const ArchivePlan* plan = known ? &asset->plans[index] : nullptr;
        const auto kind = plan ? plan->entry : entry_for(*config);
        *uvmap = archive_uvmap();
        auto* instance = kind == ArchiveEntry::DiffuseTransmission && source && source->kind == MaterialKind::diffuse
            ? diffuse_instance(config, *source)
            : standard_instance(config, kind, plan, source, label);
        records.push_back({instance, *config});
        if (source) bindings.emplace_back(index, instance);
        return instance;
    }

    f::MaterialInstance* standard_instance(g::MaterialKey* config, ArchiveEntry kind, const ArchivePlan* plan,
                                           const MaterialSource* source, const char* label) {
        auto& k = *config;
        auto* material = entry(kind, k.alphaMode);
        // gltfio names an unnamed material "material"; an instance without a name would take
        // the entry's name instead.
        auto* mi = material->createInstance(label ? label : "material");
        // gltfio binds the textures and sets the factors of the features left in the key, so it
        // keeps only core textures on the first two UV sets and features the entry has.
        const auto has = [&](const char* name) { return material->hasParameter(name); };
        k.hasClearCoatTexture = k.hasClearCoatRoughnessTexture = k.hasClearCoatNormalTexture = false;
        k.hasSheenColorTexture = k.hasSheenRoughnessTexture = k.hasTransmissionTexture = false;
        k.hasVolumeThicknessTexture = k.hasSpecularTexture = k.hasSpecularColorTexture = false;
        k.hasClearCoat = k.hasClearCoat && has("clearCoatFactor");
        k.hasSheen = k.hasSheen && has("sheenColorFactor");
        k.hasTransmission = k.hasTransmission && has("transmissionFactor");
        k.hasVolume = k.hasVolume && has("volumeThicknessFactor");
        k.hasDispersion = k.hasDispersion && has("dispersion");
        k.hasSpecular = k.hasSpecular && has("specularStrength");
        set_core_indices(mi, k);
        mi->setDoubleSided(k.doubleSided);
        mi->setCullingMode(k.doubleSided ? f::MaterialInstance::CullingMode::NONE : f::MaterialInstance::CullingMode::BACK);
        mi->setTransparencyMode(k.doubleSided ? f::MaterialInstance::TransparencyMode::TWO_PASSES_TWO_SIDES
                                              : f::MaterialInstance::TransparencyMode::DEFAULT);
        const m::mat3f identity;
        const f::TextureSampler sampler;
        for (const char* name : {"baseColor", "metallicRoughness", "normal", "occlusion", "emissive"}) {
            mi->setParameter((std::string(name) + "UvMatrix").c_str(), identity);
            mi->setParameter((std::string(name) + "Map").c_str(), dummy, sampler);
        }
        // gltfio sets these for the features in the key; the rest keep glTF defaults.
        set_if(mi, "emissiveStrength", 1.0f);
        set_if(mi, "ior", 1.5f);
        set_if(mi, "specularStrength", 1.0f);
        set_if(mi, "specularColorFactor", m::float3(1.0f));
        // Volume without KHR_materials_transmission refracts completely, as the removed runtime
        // material path did.
        set_if(mi, "transmissionFactor", k.hasTransmission ? 0.0f : 1.0f);
        set_if(mi, "volumeThicknessFactor", 0.0f);
        set_if(mi, "volumeAbsorption", m::float3(0.0f));
        set_if(mi, "dispersion", 0.0f);
        set_if(mi, "clearCoatFactor", 0.0f);
        set_if(mi, "clearCoatRoughnessFactor", 0.0f);
        set_if(mi, "clearCoatNormalScale", plan ? plan->clearcoat_normal_scale : 1.0f);
        set_if(mi, "sheenColorFactor", m::float3(0.0f));
        set_if(mi, "sheenRoughnessFactor", 0.0f);
        set_if(mi, "anisotropyStrength", 0.0f);
        set_if(mi, "anisotropyRotation", 0.0f);
        set_if(mi, "iridescenceFactor", 0.0f);
        set_if(mi, "iridescenceIor", 1.3f);
        set_if(mi, "iridescenceThicknessMinimum", 100.0f);
        set_if(mi, "iridescenceThicknessMaximum", 400.0f);
        for (size_t role = 0; role < extension_role_count; ++role) {
            const std::string prefix = role_names[role];
            if (!has((prefix + "Slot").c_str())) continue;
            const ExtensionTexture* texture = plan && plan->roles[role].slot >= 0 ? &plan->roles[role] : nullptr;
            mi->setParameter((prefix + "Slot").c_str(), texture ? int(texture->slot) : -1);
            mi->setParameter((prefix + "Index").c_str(), texture ? int(texture->uv) : 0);
            // Anisotropy and iridescence use filly's column order (M * uv); see surface.mat.in.
            const bool column = role == size_t(ExtensionRole::anisotropy) || role == size_t(ExtensionRole::iridescence)
                || role == size_t(ExtensionRole::iridescenceThickness);
            const auto uv = texture ? matrix(texture->transform) : identity;
            mi->setParameter((prefix + "UvMatrix").c_str(), column ? uv : transpose(uv));
        }
        for (size_t slot = 0; slot < std::size(ext_samplers); ++slot) {
            if (!has(ext_samplers[slot])) break;
            if (plan && slot < plan->slots.size()) {
                const auto& use = plan->slots[slot];
                const auto& data = asset->textures[use.texture];
                mi->setParameter(ext_samplers[slot], decode_shared(use.texture, use.srgb), sampler_for(data));
            } else {
                mi->setParameter(ext_samplers[slot], dummy, sampler);
            }
        }
        if (source && source->kind == MaterialKind::surface) {
            const auto& data = asset->surfaces[source->source];
            if (data.has_anisotropy) {
                set_if(mi, "anisotropyStrength", data.anisotropy);
                set_if(mi, "anisotropyRotation", data.rotation);
            }
            if (data.has_iridescence) {
                set_if(mi, "iridescenceFactor", data.iridescence);
                set_if(mi, "iridescenceIor", data.ior);
                set_if(mi, "iridescenceThicknessMinimum", data.minimum);
                set_if(mi, "iridescenceThicknessMaximum", data.maximum);
            }
        }
        return mi;
    }

    f::MaterialInstance* diffuse_instance(g::MaterialKey* config, const MaterialSource& source) {
        // This material has its own volume inputs; gltfio would otherwise set the glTF volume
        // and dispersion parameters.
        config->hasVolume = config->hasVolumeThicknessTexture = config->hasDispersion = false;
        const auto& data = asset->diffuse[source.source];
        auto* mi = entry(ArchiveEntry::DiffuseTransmission, config->alphaMode)->createInstance(source.label.c_str());
        mi->setDoubleSided(config->doubleSided);
        mi->setCullingMode(config->doubleSided ? f::MaterialInstance::CullingMode::NONE : f::MaterialInstance::CullingMode::BACK);
        set_core_indices(mi, *config);
        const auto own = [](const AssetTexture& texture) { return texture.bytes.empty() ? -1 : texture.uv; };
        mi->setParameter("diffuseIndex", own(data.factor_texture));
        mi->setParameter("diffuseColorIndex", own(data.color_texture));
        for (const char* p : {"baseColorUvMatrix", "normalUvMatrix", "metallicRoughnessUvMatrix", "occlusionUvMatrix",
                              "emissiveUvMatrix", "backlightRotation"})
            mi->setParameter(p, m::mat3f{});
        mi->setParameter("diffuseUvMatrix", matrix(data.factor_texture.transform));
        mi->setParameter("diffuseColorUvMatrix", matrix(data.color_texture.transform));
        mi->setParameter("diffuseFactor", data.factor);
        mi->setParameter("diffuseColor", m::float3{data.color[0], data.color[1], data.color[2]});
        mi->setParameter("volumeThicknessFactor", data.thickness);
        mi->setParameter("volumeAbsorption", m::float3{data.absorption[0], data.absorption[1], data.absorption[2]});
        mi->setParameter("volumeThicknessIndex", own(data.thickness_texture));
        mi->setParameter("volumeThicknessUvMatrix", transpose(matrix(data.thickness_texture.transform)));
        mi->setParameter("reflectance", 0.5f);
        mi->setParameter("backlightIntensity", 0.0f);
        mi->setParameter("emissiveStrength", 1.0f);
        f::TextureSampler sampler(f::TextureSampler::MinFilter::LINEAR_MIPMAP_LINEAR, f::TextureSampler::MagFilter::LINEAR);
        for (const char* p : {"baseColorMap", "normalMap", "metallicRoughnessMap", "occlusionMap", "emissiveMap"})
            mi->setParameter(p, dummy, sampler);
        mi->setParameter("backlightIrradiance", dummy_cube, sampler);
        mi->setParameter("diffuseMap", decode(data.factor_texture, false), sampler_for(data.factor_texture));
        mi->setParameter("diffuseColorMap", decode(data.color_texture, true), sampler_for(data.color_texture));
        mi->setParameter("volumeThicknessMap", decode(data.thickness_texture, false), sampler_for(data.thickness_texture));
        return mi;
    }

    g::TextureProvider* decoder(const std::string& mime) {
        auto& slot = mime == "image/ktx2" ? ktx2_ : mime == "image/webp" ? webp_ : stb_;
        if (!slot) slot.reset(mime == "image/ktx2" ? g::createKtx2Provider(engine)
                              : mime == "image/webp" ? create_webp_provider(engine) : g::createStbProvider(engine));
        return slot.get();
    }
    f::Texture* decode(const AssetTexture& data, bool srgb) {
        if (data.bytes.empty()) return dummy;
        // Materials of one asset that use the same texture share its decoded copy, and so do
        // clones: decoding again cost each clone the decode time and the texture's GPU memory,
        // which stayed until the asset closed.
        if (asset)
            for (const auto& entry : asset->decoded)
                if (entry.source == &data && entry.srgb == srgb) return entry.texture;
        auto* provider = decoder(data.mime);
        auto* texture = provider->pushTexture(data.bytes.data(), data.bytes.size(), data.mime.c_str(),
            srgb ? g::TextureProvider::TextureFlags::sRGB : g::TextureProvider::TextureFlags::NONE);
        if (!texture) throw AssetError("Could not decode material texture");
        textures.push_back(texture);
        if (asset) asset->decoded.push_back({&data, srgb, texture});
        provider->waitForCompletion();
        provider->updateQueue();
        provider->popTexture();
        if (const char* error = provider->getPopMessage()) throw AssetError(error);
        return texture;
    }
    f::Texture* decode_shared(size_t texture, bool srgb) { return decode(asset->textures[texture], srgb); }

    const f::Material* const* getMaterials() const noexcept override { return built_.data(); }
    size_t getMaterialsCount() const noexcept override { return built_.size(); }
    void destroyMaterials() override {
        for (auto* material : built_) engine->destroy(material);
        built_.clear();
        for (auto& row : materials_) row.fill(nullptr);
    }
    bool needsDummyData(f::VertexAttribute attribute) const noexcept override {
        return attribute == f::VertexAttribute::UV0 || attribute == f::VertexAttribute::UV1
            || attribute == f::VertexAttribute::COLOR;
    }

private:
    std::vector<uint64_t> storage_;
    std::array<std::array<const f::uberz::ArchiveSpec*, 3>, entry_count> specs_;
    std::array<std::array<f::Material*, 3>, entry_count> materials_;
    std::vector<const f::Material*> built_;
    std::unique_ptr<g::TextureProvider> stb_, ktx2_, webp_;
};
ArchiveProvider* provider_of(g::MaterialProvider* provider) { return static_cast<ArchiveProvider*>(provider); }
}

g::MaterialProvider* create_material_provider(f::Engine* engine) { return new ArchiveProvider(engine); }
void set_prepared_asset(g::MaterialProvider* provider, const PreparedAsset* asset) { provider_of(provider)->set_asset(asset); }
std::vector<f::Texture*> take_material_textures(g::MaterialProvider* provider) {
    return std::exchange(provider_of(provider)->textures, {});
}
std::vector<MaterialBinding> take_material_bindings(g::MaterialProvider* provider) { return std::exchange(provider_of(provider)->bindings, {}); }
std::vector<MaterialRecord> take_material_records(g::MaterialProvider* provider) { return std::exchange(provider_of(provider)->records, {}); }
void configure_diffuse_environment(g::MaterialProvider* provider, f::MaterialInstance* instance,
                                   f::Texture* irradiance, float intensity, float rotation) {
    if (!instance->getMaterial()->hasParameter("backlightIntensity")) return;
    auto* p = provider_of(provider);
    f::TextureSampler sampler(f::TextureSampler::MinFilter::LINEAR, f::TextureSampler::MagFilter::LINEAR,
                              f::TextureSampler::WrapMode::CLAMP_TO_EDGE);
    instance->setParameter("backlightIrradiance", irradiance ? irradiance : p->dummy_cube, sampler);
    instance->setParameter("backlightIntensity", irradiance ? intensity : 0.0f);
    instance->setParameter("backlightRotation", m::mat3f::rotation(-rotation * 0.01745329252f, m::float3{0, 1, 0}));
}
}
