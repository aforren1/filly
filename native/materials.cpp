#include "materials.h"
#include "webp_provider.h"
#include <filament/Engine.h>
#include <filament/Material.h>
#include <filament/MaterialInstance.h>
#include <filament/TextureSampler.h>
#include <filamat/MaterialBuilder.h>
#include <gltfio/TextureProvider.h>
#include <gltfio/materials/uberarchive.h>
#include <math/mat3.h>
#include <uberz/ReadableArchive.h>
#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <utility>

// The SDK ships zstd.lib without its header. These are the stable zstd 1.x entry points.
extern "C" {
unsigned long long ZSTD_getFrameContentSize(const void* src, size_t srcSize);
size_t ZSTD_decompress(void* dst, size_t dstCapacity, const void* src, size_t compressedSize);
unsigned ZSTD_isError(size_t code);
}

namespace filly::detail {
namespace f = filament;
namespace g = filament::gltfio;
namespace m = filament::math;
namespace {
const char* source = R"MAT(
vec2 uvAt(int index, mat3 transform) {
    // gltfio supplies transposed matrices for its standard material parameters.
    return (vec3(index == 1 ? getUV1() : getUV0(), 1.0) * transform).xy;
}
vec2 diffuseUvAt(int index, mat3 transform) {
    return (transform * vec3(index == 1 ? getUV1() : getUV0(), 1.0)).xy;
}
float diffuseAmount() {
    float factor = materialParams.diffuseFactor;
    if (materialParams.diffuseIndex >= 0)
        factor *= texture(materialParams_diffuseMap, diffuseUvAt(materialParams.diffuseIndex, materialParams.diffuseUvMatrix)).a;
    return factor;
}
vec3 diffuseTint() {
    vec3 color = materialParams.diffuseColor;
    if (materialParams.diffuseColorIndex >= 0)
        color *= texture(materialParams_diffuseColorMap, diffuseUvAt(materialParams.diffuseColorIndex, materialParams.diffuseColorUvMatrix)).rgb;
    return color;
}
float volumeThickness() {
    float thickness = materialParams.volumeThicknessFactor * variable_volumeScale.x;
    if (materialParams.volumeThicknessIndex >= 0)
        thickness *= texture(materialParams_volumeThicknessMap, uvAt(materialParams.volumeThicknessIndex, materialParams.volumeThicknessUvMatrix)).g;
    return thickness;
}
vec3 transmittance() {
    return exp(-materialParams.volumeAbsorption * volumeThickness());
}
void material(inout MaterialInputs material) {
    if (materialParams.normalIndex >= 0) {
        material.normal = texture(materialParams_normalMap, uvAt(materialParams.normalIndex, materialParams.normalUvMatrix)).xyz * 2.0 - 1.0;
        material.normal.xy *= materialParams.normalScale;
    }
    prepareMaterial(material);
    material.baseColor = materialParams.baseColorFactor * getColor();
    if (materialParams.baseColorIndex >= 0)
        material.baseColor *= texture(materialParams_baseColorMap, uvAt(materialParams.baseColorIndex, materialParams.baseColorUvMatrix));
    material.metallic = materialParams.metallicFactor;
    material.roughness = materialParams.roughnessFactor;
    if (materialParams.metallicRoughnessIndex >= 0) {
        vec4 mr = texture(materialParams_metallicRoughnessMap, uvAt(materialParams.metallicRoughnessIndex, materialParams.metallicRoughnessUvMatrix));
        material.metallic *= mr.b; material.roughness *= mr.g;
    }
    material.reflectance = materialParams.reflectance;
    material.specularFactor = 1.0;
    material.specularColorFactor = vec3(1.0);
    // The reflected diffuse lobe loses the energy transmitted through the sheet.
    material.baseColor.rgb *= 1.0 - diffuseAmount() * (1.0 - material.metallic);
    if (materialParams.aoIndex >= 0)
        material.ambientOcclusion = mix(1.0, texture(materialParams_occlusionMap, uvAt(materialParams.aoIndex, materialParams.occlusionUvMatrix)).r, materialParams.aoStrength);
    material.emissive.rgb = materialParams.emissiveFactor * materialParams.emissiveStrength;
    if (materialParams.emissiveIndex >= 0)
        material.emissive.rgb *= texture(materialParams_emissiveMap, uvAt(materialParams.emissiveIndex, materialParams.emissiveUvMatrix)).rgb;
    if (materialParams.backlightIntensity > 0.0) {
        vec3 n = materialParams.backlightRotation * -getWorldNormalVector();
        material.emissive.rgb += texture(materialParams_backlightIrradiance, n).rgb * materialParams.backlightIntensity
            * diffuseAmount() * diffuseTint() * transmittance() * (1.0 - material.metallic)
            * ((1.0 - 0.16 * material.reflectance * material.reflectance) / 3.14159265);
    }
}
vec3 surfaceShading(const MaterialInputs inputs, const ShadingData shading, const LightData light) {
    vec3 n = getWorldNormalVector();
    vec3 v = getWorldViewVector();
    float nl = dot(n, light.l);
    float nv = max(abs(dot(n, v)), 0.0001);
    vec3 result;
    if (nl > 0.0) {
        vec3 h = normalize(v + light.l);
        float nh = max(dot(n, h), 0.0);
        float vh = max(dot(v, h), 0.0);
        float a2 = max(shading.roughness * shading.roughness, 0.00001);
        float d = a2 / (3.14159265 * pow(nh * nh * (a2 - 1.0) + 1.0, 2.0));
        float gv = nl * sqrt(nv * nv * (1.0-a2) + a2);
        float gl = nv * sqrt(nl * nl * (1.0-a2) + a2);
        vec3 fresnel = shading.f0 + (1.0 - shading.f0) * pow(1.0-vh, 5.0);
        vec3 specular = d * 0.5 / max(gv+gl, 0.0001) * fresnel;
        result = (shading.diffuseColor * (1.0-fresnel) / 3.14159265 + specular) * nl;
    } else {
        vec3 fresnel = shading.f0 + (1.0-shading.f0) * pow(1.0-nv, 5.0);
        result = diffuseAmount() * diffuseTint() * transmittance() * (1.0-inputs.metallic) * (1.0-fresnel) * (-nl / 3.14159265);
    }
    return result * light.colorIntensity.rgb * light.colorIntensity.w * light.attenuation * light.visibility;
}
)MAT";

// Reads the feature specs of Filament's precompiled material archive. The ubershader provider
// substitutes a default material when no spec matches a glTF material, and then sets parameters
// that the default material lacks, which aborts the process (for example sheen, specular, and
// IOR together). Those materials are compiled instead, so fast mode never uses the fallback.
class ArchiveSpecs {
public:
    ArchiveSpecs(const void* data, size_t size) {
        const auto length = ZSTD_getFrameContentSize(data, size);
        // Filament's own loader applies the same 256 MiB bound.
        if (length == 0 || length > (256ull << 20)) throw AssetError("Invalid material archive");
        storage_.resize(size_t((length + 7) / 8));
        const auto written = ZSTD_decompress(storage_.data(), size_t(length), data, size);
        if (ZSTD_isError(written) || written != length) throw AssetError("Could not decompress the material archive");
        archive_ = reinterpret_cast<filament::uberz::ReadableArchive*>(storage_.data());
        if (!filament::uberz::convertOffsetsToPointers(archive_, size_t(length)))
            throw AssetError("Invalid material archive");
    }
    // Mirrors UbershaderProvider::getMaterial() and ArchiveCache::getMaterial() in Filament 1.77.1.
    bool supports(const g::MaterialKey& key) const {
        using namespace filament::uberz;
        const auto shading = key.unlit ? f::Shading::UNLIT
            : key.useSpecularGlossiness ? f::Shading::SPECULAR_GLOSSINESS : f::Shading::LIT;
        const auto blending = key.alphaMode == g::AlphaMode::MASK ? f::BlendingMode::MASKED
            : key.alphaMode == g::AlphaMode::BLEND ? f::BlendingMode::FADE : f::BlendingMode::OPAQUE;
        const std::pair<const char*, bool> required[] = {
            {"Sheen", key.hasSheen}, {"Transmission", key.hasTransmission}, {"Volume", key.hasVolume},
            {"Ior", key.hasIOR}, {"VertexColors", key.hasVertexColors},
            {"BaseColorTexture", key.hasBaseColorTexture}, {"NormalTexture", key.hasNormalTexture},
            {"OcclusionTexture", key.hasOcclusionTexture}, {"EmissiveTexture", key.hasEmissiveTexture},
            {"MetallicRoughnessTexture", key.hasMetallicRoughnessTexture},
            {"ClearCoatTexture", key.hasClearCoatTexture},
            {"ClearCoatRoughnessTexture", key.hasClearCoatRoughnessTexture},
            {"ClearCoatNormalTexture", key.hasClearCoatNormalTexture}, {"ClearCoat", key.hasClearCoat},
            {"TextureTransforms", bool(key.hasTextureTransforms)},
            {"TransmissionTexture", key.hasTransmissionTexture},
            {"SheenColorTexture", key.hasSheenColorTexture},
            {"SheenRoughnessTexture", key.hasSheenRoughnessTexture},
            {"VolumeThicknessTexture", key.hasVolumeThicknessTexture}, {"Specular", key.hasSpecular},
            {"SpecularTexture", key.hasSpecularTexture}, {"SpecularColorTexture", key.hasSpecularColorTexture},
            {"Dispersion", key.hasDispersion},
        };
        for (uint64_t i = 0; i < archive_->specsCount; ++i) {
            const auto& spec = archive_->specs[i];
            if (spec.blendingMode != INVALID_BLENDING && spec.blendingMode != blending) continue;
            if (spec.shadingModel != INVALID_SHADING_MODEL && spec.shadingModel != shading) continue;
            auto flag = [&](const char* name) -> const ArchiveFlag* {
                for (uint16_t j = 0; j < spec.flagsCount; ++j)
                    if (!std::strcmp(spec.flags[j].name, name)) return &spec.flags[j];
                return nullptr;
            };
            bool suitable = true;
            for (const auto& [name, used] : required) {
                const auto* found = used ? flag(name) : nullptr;
                if (used && (!found || found->value == ArchiveFeature::UNSUPPORTED)) { suitable = false; break; }
            }
            for (uint16_t j = 0; suitable && j < spec.flagsCount; ++j) {
                if (spec.flags[j].value != ArchiveFeature::REQUIRED) continue;
                const auto match = std::find_if(std::begin(required), std::end(required),
                    [&](const auto& item) { return !std::strcmp(item.first, spec.flags[j].name); });
                suitable = match != std::end(required) && match->second;
            }
            if (suitable) return true;
        }
        return false;
    }
private:
    std::vector<uint64_t> storage_;
    filament::uberz::ReadableArchive* archive_ = nullptr;
};

// Filament 1.77.1 compiles glTF materials at feature level 1. A lit material with screen-space
// reflection or refraction has 8 of its 16 fragment samplers left for textures.
constexpr int max_lit_textures = 8;
int texture_count(const g::MaterialKey& k) {
    // Unlit shading samples only base color and has 12 samplers.
    if (k.unlit) return k.hasBaseColorTexture;
    int count = k.hasBaseColorTexture + k.hasMetallicRoughnessTexture + k.hasNormalTexture
        + k.hasOcclusionTexture + k.hasEmissiveTexture;
    if (k.hasClearCoat) count += k.hasClearCoatTexture + k.hasClearCoatRoughnessTexture + k.hasClearCoatNormalTexture;
    if (k.hasSheen) count += k.hasSheenColorTexture + k.hasSheenRoughnessTexture;
    if (k.hasTransmission) count += k.hasTransmissionTexture;
    if (k.hasVolume) count += k.hasVolumeThicknessTexture;
    if (k.hasSpecular) count += k.hasSpecularTexture + k.hasSpecularColorTexture;
    return count;
}
// Copy of UbershaderProvider.cpp prepareConfig() in Filament 1.77.1, without its log output.
// Returns whether the precompiled material would omit a feature of this key.
bool reduce_for_archive(g::MaterialKey& k) {
    const auto original = k;
    if ((k.hasVolume || k.hasTransmission) && k.hasSheen) k.hasSheen = false;
    if (k.hasClearCoat && (k.hasVolume || k.hasTransmission || k.hasSheen || k.hasIOR))
        k.hasVolume = k.hasTransmission = k.hasSheen = k.hasIOR = false;
    if (k.useSpecularGlossiness && k.hasSpecular) k.useSpecularGlossiness = false;
    if (k.unlit && k.hasSpecular) k.unlit = false;
    if ((k.hasClearCoatNormalTexture || k.hasClearCoatRoughnessTexture) && k.hasSpecular)
        k.hasClearCoatNormalTexture = k.hasClearCoatRoughnessTexture = false;
    if (k.hasSpecularColorTexture && (k.hasSheen || k.hasVolume)) k.hasSpecularColorTexture = false;
    return original.hasSheen != k.hasSheen || original.hasVolume != k.hasVolume
        || original.hasTransmission != k.hasTransmission || original.hasIOR != k.hasIOR
        || original.useSpecularGlossiness != k.useSpecularGlossiness || original.unlit != k.unlit
        || original.hasClearCoatNormalTexture != k.hasClearCoatNormalTexture
        || original.hasClearCoatRoughnessTexture != k.hasClearCoatRoughnessTexture
        || original.hasSpecularColorTexture != k.hasSpecularColorTexture;
}

// Maps TEXCOORD_0 and TEXCOORD_1, when unmapped, to the free UV sets in that order. In assets with
// provider-built materials every material uses this, so that the vertex layout gltfio builds in
// createAsset (before a material can be identified) serves whichever material the primitive has.
void extend_uvmap(g::UvMap& uv) {
    for (int texcoord = 0; texcoord < 2; ++texcoord) {
        if (uv[texcoord] != g::UvSet::UNUSED) continue;
        const auto maximum = *std::max_element(uv.begin(), uv.end());
        if (int(maximum) >= 2) return;
        uv[texcoord] = static_cast<g::UvSet>(int(maximum) + 1);
    }
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

class Provider final : public g::MaterialProvider {
public:
    f::Engine* engine;
    g::MaterialProvider* delegate;
    // The asset being created or cloned; null otherwise.
    const PreparedAsset* asset = nullptr;
    struct SurfaceCache { g::MaterialKey key; g::UvMap uv; bool extended; f::Material* material; };
    std::vector<SurfaceCache> surface_cache;
    std::vector<f::Texture*> textures;
    std::vector<MaterialBinding> bindings;
    std::vector<MaterialRecord> records;
    std::vector<f::Material*> custom;
    f::Texture* dummy;
    f::Texture* dummy_cube;
    std::unique_ptr<ArchiveSpecs> archive;
    std::unique_ptr<g::TextureProvider> stb, ktx2, webp;
    explicit Provider(f::Engine* e, bool compiled) : engine(e) {
        if (!compiled) archive = std::make_unique<ArchiveSpecs>(UBERARCHIVE_DEFAULT_DATA, UBERARCHIVE_DEFAULT_SIZE);
        // The material compiler has a reference-counted process-wide initialization.
        filamat::MaterialBuilder::init();
        delegate = compiled ? g::createJitShaderProvider(e) : g::createUbershaderProvider(e, UBERARCHIVE_DEFAULT_DATA, UBERARCHIVE_DEFAULT_SIZE);
        dummy = f::Texture::Builder().width(1).height(1).levels(1).format(f::Texture::InternalFormat::RGBA8).build(*e);
        dummy_cube = f::Texture::Builder().width(1).height(1).levels(1).sampler(f::Texture::Sampler::SAMPLER_CUBEMAP).format(f::Texture::InternalFormat::RGBA8).build(*e);
        static const uint8_t pixel[] = {255,255,255,255};
        dummy->setImage(*e, 0, f::Texture::PixelBufferDescriptor(pixel, 4, f::Texture::Format::RGBA, f::Texture::Type::UBYTE));
    }
    ~Provider() override {
        stb.reset(); ktx2.reset(); webp.reset();
        for (auto* texture : textures) engine->destroy(texture);
        engine->destroy(dummy); engine->destroy(dummy_cube); delete delegate;
        filamat::MaterialBuilder::shutdown();
    }
    bool extended() const { return asset && asset->custom_materials(); }
    // Returns true for standard glTF materials that the wrapper's generator must build. In fast
    // mode it can also reduce *config, as Filament's precompiled materials would, when the
    // complete material needs more samplers than a lit material has.
    bool generated(g::MaterialKey& config, const char* label) const {
        if (config.hasVolume || config.hasTransmission) return true;
        // Only the wrapper's generator stores alpha one for unlit OPAQUE; see shaderFromKey().
        if (config.unlit && config.alphaMode == g::AlphaMode::OPAQUE) return true;
        if (!archive) return false;
        auto key = config;
        g::UvMap uv{};
        const bool reduced = reduce_for_archive(key);
        g::constrainMaterial(&key, &uv);
        if (!reduced && archive->supports(key)) return false;
        // The archive has no exact material. Compile the complete one if its samplers fit.
        if (texture_count(config) <= max_lit_textures) return true;
        key = config;
        reduce_for_archive(key);
        // gltfio does not clear these with their features; unused, they break archive matching.
        if (!key.hasSheen) key.hasSheenColorTexture = key.hasSheenRoughnessTexture = false;
        if (!key.hasVolume) key.hasVolumeThicknessTexture = false;
        if (!key.hasTransmission) key.hasTransmissionTexture = false;
        if (texture_count(key) > max_lit_textures)
            throw AssetError(std::string("Material '") + (label ? label : "material") + "' needs more than 8 textures in fast mode");
        config = key;
        auto check = config;
        uv = {};
        g::constrainMaterial(&check, &uv);
        return !archive->supports(check);
    }
    // The UV layout that a standard material with this key gets, without building it: the
    // generator and the JIT provider constrain the key as is, the ubershader provider after
    // its prepareConfig(), which reduce_for_archive() copies.
    g::UvMap standard_uvmap(g::MaterialKey key, const char* label) const {
        g::UvMap uv{};
        if (!generated(key, label) && archive) reduce_for_archive(key);
        g::constrainMaterial(&key, &uv);
        if (extended()) extend_uvmap(uv);
        return uv;
    }
    f::Material* generated_material(g::MaterialKey* config, g::UvMap* uvmap, const char* label, bool surface) {
        for (const auto& cached : surface_cache)
            // Filament 1.77.1's equality operator omits the dispersion bit.
            if (cached.key == *config && cached.key.hasDispersion == config->hasDispersion
                    && cached.uv == *uvmap && cached.extended == surface) return cached.material;
        auto* material = create_surface_material(engine, *config, *uvmap, label, surface);
        surface_cache.push_back({*config, *uvmap, surface, material});
        return material;
    }
    f::Material* standard_material(g::MaterialKey* config, g::UvMap* uvmap, const char* label) {
        if (generated(*config, label)) {
            g::constrainMaterial(config, uvmap);
            return generated_material(config, uvmap, label, false);
        }
        return delegate->getMaterial(config, uvmap, label);
    }
    f::Material* getMaterial(g::MaterialKey* config, g::UvMap* uvmap, const char* label) override {
        if (!extended()) return standard_material(config, uvmap, label);
        // gltfio asks while it builds vertex buffers in createAsset, before filly can tell which
        // glTF material this is; label is only its name. The UV layout rule does not depend on
        // the material. The returned material only supplies required attributes, so a match by
        // name is enough, and a placeholder with the same attributes covers shared names.
        *uvmap = standard_uvmap(*config, label);
        const MaterialSource* match = nullptr;
        size_t matches = 0;
        bool custom_match = false;
        for (const auto& source : asset->materials) {
            if (source.root_label != label) continue;
            match = &source;
            ++matches;
            custom_match = custom_match || source.kind != MaterialKind::standard;
        }
        auto key = *config;
        g::UvMap uv{};
        if (!custom_match) return standard_material(&key, &uv, label);
        if (matches == 1 && match->kind == MaterialKind::diffuse) return diffuse_material(config->alphaMode);
        if (matches == 1) {
            // Built as surface_instance() builds it, so the instance reuses this material.
            g::constrainMaterial(&key, &uv);
            extend_uvmap(uv);
            return generated_material(&key, &uv, match->label.c_str(), true);
        }
        g::MaterialKey placeholder{};
        placeholder.unlit = config->unlit;
        return delegate->getMaterial(&placeholder, &uv, "filly placeholder");
    }
    // Checks that an instance's UV layout is the one its vertex buffers were built with.
    void check_layout(const g::MaterialKey& original, const g::UvMap& uvmap, const char* label) const {
        if (extended() && standard_uvmap(original, label) != uvmap)
            throw AssetError(std::string("Material '") + (label ? label : "material")
                             + "' needs a different UV layout than its vertex buffers");
    }
    void map_custom(const g::UvMap& uvmap, const AssetTexture& texture) const {
        if (!texture.bytes.empty() && uvmap.at(size_t(texture.uv)) == g::UvSet::UNUSED)
            throw AssetError("Material needs more than two UV sets");
    }
    f::Material* diffuse_material(g::AlphaMode alpha) {
        const size_t mode = size_t(alpha);
        if (custom.size() <= mode) custom.resize(mode+1);
        if (custom[mode]) return custom[mode];
        using B = filamat::MaterialBuilder;
        B builder;
        builder.name("filly diffuse transmission").shading(B::Shading::LIT)
            .postLightingBlending(B::BlendingMode::OPAQUE)
            // glTF texture coordinates already use the orientation expected by gltfio.
            .flipUV(false)
            .customSurfaceShading(true).doubleSided(true).material(source)
            .require(f::VertexAttribute::UV0).require(f::VertexAttribute::UV1).require(f::VertexAttribute::COLOR)
            .platform(B::Platform::DESKTOP).targetApi(B::TargetApi::OPENGL)
            .optimization(B::Optimization::NONE);
        // Thickness uses the complete model-to-world scale, as for the volume materials.
        builder.variable(B::Variable::CUSTOM0, "volumeScale").materialVertex(R"MAT(
            void materialVertex(inout MaterialVertexInputs material) {
                highp mat4 world = getWorldFromModelMatrix();
                material.volumeScale = vec4((length(world[0].xyz)+length(world[1].xyz)+length(world[2].xyz))/3.0);
            }
        )MAT");
        for (const char* p : {"volumeThicknessFactor", "dispersion"}) builder.parameter(p,B::UniformType::FLOAT);
        builder.parameter("volumeAbsorption",B::UniformType::FLOAT3);
        builder.parameter("volumeThicknessIndex",B::UniformType::INT);
        builder.parameter("volumeThicknessUvMatrix",B::UniformType::MAT3);
        builder.parameter("volumeThicknessMap",B::SamplerType::SAMPLER_2D);
        builder.blending(alpha == g::AlphaMode::MASK ? B::BlendingMode::MASKED :
                         alpha == g::AlphaMode::BLEND ? B::BlendingMode::FADE : B::BlendingMode::OPAQUE);
        for (const char* p : {"metallicFactor","roughnessFactor","normalScale","aoStrength","reflectance","diffuseFactor","backlightIntensity","emissiveStrength"}) builder.parameter(p,B::UniformType::FLOAT);
        for (const char* p : {"emissiveFactor","diffuseColor"}) builder.parameter(p,B::UniformType::FLOAT3);
        builder.parameter("baseColorFactor",B::UniformType::FLOAT4);
        for (const char* p : {"baseColorIndex","normalIndex","metallicRoughnessIndex","aoIndex","emissiveIndex","diffuseIndex","diffuseColorIndex"}) builder.parameter(p,B::UniformType::INT);
        for (const char* p : {"baseColorUvMatrix","normalUvMatrix","metallicRoughnessUvMatrix","occlusionUvMatrix","emissiveUvMatrix","diffuseUvMatrix","diffuseColorUvMatrix","backlightRotation"}) builder.parameter(p,B::UniformType::MAT3);
        for (const char* p : {"baseColorMap","normalMap","metallicRoughnessMap","occlusionMap","emissiveMap","diffuseMap","diffuseColorMap"}) builder.parameter(p,B::SamplerType::SAMPLER_2D);
        builder.parameter("backlightIrradiance",B::SamplerType::SAMPLER_CUBEMAP);
        auto package = builder.build(engine->getJobSystem());
        if (!package.isValid()) throw AssetError("Diffuse transmission shader compilation failed");
        custom[mode] = f::Material::Builder().package(package.getData(),package.getSize()).build(*engine);
        if (!custom[mode]) throw AssetError("Could not create diffuse transmission material");
        return custom[mode];
    }
    g::TextureProvider* decoder(const std::string& mime) {
        auto& slot = mime == "image/ktx2" ? ktx2 : mime == "image/webp" ? webp : stb;
        if (!slot) slot.reset(mime == "image/ktx2" ? g::createKtx2Provider(engine)
                              : mime == "image/webp" ? create_webp_provider(engine) : g::createStbProvider(engine));
        return slot.get();
    }
    f::Texture* decode(const AssetTexture& data, bool srgb) {
        if (data.bytes.empty()) return dummy;
        auto* provider = decoder(data.mime);
        auto* texture = provider->pushTexture(data.bytes.data(),data.bytes.size(),data.mime.c_str(),
            srgb ? g::TextureProvider::TextureFlags::sRGB : g::TextureProvider::TextureFlags::NONE);
        if (!texture) throw AssetError("Could not decode material texture");
        textures.push_back(texture);
        provider->waitForCompletion(); provider->updateQueue(); provider->popTexture();
        if (const char* error = provider->getPopMessage()) throw AssetError(error);
        return texture;
    }
    static f::TextureSampler sampler_for(const AssetTexture& texture) {
        using S = f::TextureSampler;
        auto wrap = [](int mode) { return mode == 33071 ? S::WrapMode::CLAMP_TO_EDGE : mode == 33648 ? S::WrapMode::MIRRORED_REPEAT : S::WrapMode::REPEAT; };
        S sampler;
        sampler.setWrapModeS(wrap(texture.wrap_s)); sampler.setWrapModeT(wrap(texture.wrap_t));
        const S::MinFilter filters[] = {S::MinFilter::NEAREST_MIPMAP_NEAREST, S::MinFilter::LINEAR_MIPMAP_NEAREST,
                                       S::MinFilter::NEAREST_MIPMAP_LINEAR, S::MinFilter::LINEAR_MIPMAP_LINEAR};
        sampler.setMinFilter(texture.min_filter == 9728 ? S::MinFilter::NEAREST : texture.min_filter == 9729 ? S::MinFilter::LINEAR : filters[texture.min_filter-9984]);
        sampler.setMagFilter(texture.mag_filter == 9728 ? S::MagFilter::NEAREST : S::MagFilter::LINEAR);
        return sampler;
    }
    static m::mat3f matrix(const std::array<float,9>& values) {
        m::mat3f result;
        for (int col=0; col<3; ++col) for (int row=0; row<3; ++row) result[col][row]=values[col*3+row];
        return result;
    }
    f::MaterialInstance* surface_instance(g::MaterialKey* config, g::UvMap* uvmap, const MaterialSource& source) {
        const auto original = *config;
        g::constrainMaterial(config, uvmap);
        extend_uvmap(*uvmap);
        check_layout(original, *uvmap, source.label.c_str());
        const auto& data = asset->surfaces[source.source];
        const AssetTexture* sources[] = {&data.anisotropy_texture, &data.iridescence_texture, &data.thickness_texture};
        for (const auto* texture : sources) map_custom(*uvmap, *texture);
        auto* mi = generated_material(config, uvmap, source.label.c_str(), true)->createInstance(source.label.c_str());
        mi->setParameter("anisotropyStrength", data.anisotropy);
        mi->setParameter("anisotropyRotation", data.rotation);
        mi->setParameter("iridescenceFactor", data.iridescence);
        mi->setParameter("iridescenceIor", data.ior);
        mi->setParameter("iridescenceThicknessMinimum", data.minimum);
        mi->setParameter("iridescenceThicknessMaximum", data.maximum);
        const char* prefixes[] = {"anisotropy", "iridescence", "iridescenceThickness"};
        for (int i=0; i<3; ++i) {
            const auto& texture = *sources[i];
            const std::string prefix = prefixes[i];
            mi->setParameter((prefix+"Index").c_str(), texture.bytes.empty() ? -1 : int(uvmap->at(size_t(texture.uv)))-1);
            mi->setParameter((prefix+"UvMatrix").c_str(), matrix(texture.transform));
            mi->setParameter((prefix+"Map").c_str(), decode(texture, false), sampler_for(texture));
        }
        return mi;
    }
    f::MaterialInstance* diffuse_instance(g::MaterialKey* config, g::UvMap* uvmap, const MaterialSource& source) {
        const auto original = *config;
        g::constrainMaterial(config, uvmap);
        extend_uvmap(*uvmap);
        check_layout(original, *uvmap, source.label.c_str());
        // This material has its own volume inputs; gltfio would otherwise set the glTF volume
        // and dispersion parameters and bind the thickness texture a second time.
        config->hasVolume = config->hasVolumeThicknessTexture = config->hasDispersion = false;
        const auto& data = asset->diffuse[source.source];
        for (const auto* texture : {&data.factor_texture, &data.color_texture, &data.thickness_texture}) map_custom(*uvmap, *texture);
        auto* mi = diffuse_material(config->alphaMode)->createInstance(source.label.c_str());
        mi->setDoubleSided(config->doubleSided);
        mi->setCullingMode(config->doubleSided ? f::MaterialInstance::CullingMode::NONE : f::MaterialInstance::CullingMode::BACK);
        auto mapped = [&](bool present,int uv) { return present ? int(uvmap->at(size_t(uv)))-1 : -1; };
        mi->setParameter("baseColorIndex",mapped(config->hasBaseColorTexture,config->baseColorUV));
        mi->setParameter("normalIndex",mapped(config->hasNormalTexture,config->normalUV));
        mi->setParameter("metallicRoughnessIndex",mapped(config->hasMetallicRoughnessTexture,config->metallicRoughnessUV));
        mi->setParameter("aoIndex",mapped(config->hasOcclusionTexture,config->aoUV));
        mi->setParameter("emissiveIndex",mapped(config->hasEmissiveTexture,config->emissiveUV));
        mi->setParameter("diffuseIndex",mapped(!data.factor_texture.bytes.empty(),data.factor_texture.uv));
        mi->setParameter("diffuseColorIndex",mapped(!data.color_texture.bytes.empty(),data.color_texture.uv));
        for (const char* p : {"baseColorUvMatrix","normalUvMatrix","metallicRoughnessUvMatrix","occlusionUvMatrix","emissiveUvMatrix","backlightRotation"}) mi->setParameter(p,m::mat3f{});
        mi->setParameter("diffuseUvMatrix",matrix(data.factor_texture.transform));
        mi->setParameter("diffuseColorUvMatrix",matrix(data.color_texture.transform));
        mi->setParameter("diffuseFactor",data.factor);
        mi->setParameter("diffuseColor",m::float3{data.color[0],data.color[1],data.color[2]});
        mi->setParameter("volumeThicknessFactor", data.thickness);
        mi->setParameter("volumeAbsorption", m::float3{data.absorption[0],data.absorption[1],data.absorption[2]});
        mi->setParameter("volumeThicknessIndex", mapped(!data.thickness_texture.bytes.empty(),data.thickness_texture.uv));
        mi->setParameter("volumeThicknessUvMatrix", transpose(matrix(data.thickness_texture.transform)));
        mi->setParameter("reflectance",0.5f); mi->setParameter("backlightIntensity",0.0f);
        f::TextureSampler sampler(f::TextureSampler::MinFilter::LINEAR_MIPMAP_LINEAR,f::TextureSampler::MagFilter::LINEAR);
        for (const char* p : {"baseColorMap","normalMap","metallicRoughnessMap","occlusionMap","emissiveMap"}) mi->setParameter(p,dummy,sampler);
        mi->setParameter("backlightIrradiance",dummy_cube,sampler);
        mi->setParameter("diffuseMap",decode(data.factor_texture,false),sampler_for(data.factor_texture));
        mi->setParameter("diffuseColorMap",decode(data.color_texture,true),sampler_for(data.color_texture));
        mi->setParameter("volumeThicknessMap",decode(data.thickness_texture,false),sampler_for(data.thickness_texture));
        return mi;
    }
    f::MaterialInstance* createMaterialInstance(g::MaterialKey* config, g::UvMap* uvmap, const char* label, const char* extras) override {
        const size_t index = material_marker(extras);
        const MaterialSource* source = asset && index < asset->materials.size() ? &asset->materials[index] : nullptr;
        // Texture assignment needs each instance's key to request a variant with a new slot.
        auto record = [&](f::MaterialInstance* instance, bool custom_material) {
            records.push_back({instance, *config, custom_material});
            if (source) bindings.emplace_back(index, instance);
            return instance;
        };
        if (source && source->kind == MaterialKind::surface) return record(surface_instance(config, uvmap, *source), true);
        if (source && source->kind == MaterialKind::diffuse) return record(diffuse_instance(config, uvmap, *source), true);
        const auto original = *config;
        // gltfio names an unnamed material "material" when it asks for it in createAsset, and
        // an instance without a name takes its material's name. Materials first built here, as
        // in assets with provider-built materials, get the same name.
        const char* material_label = label ? label : "material";
        f::MaterialInstance* instance;
        // Both material modes need the orthographic refraction filter.
        if (generated(*config, label)) {
            g::constrainMaterial(config, uvmap);
            instance = generated_material(config, uvmap, material_label, false)->createInstance(label);
        } else {
            if (!label && extended()) {
                auto key = *config;
                g::UvMap uv{};
                delegate->getMaterial(&key, &uv, material_label);
            }
            // The marker is filly's; gltfio's providers do not read extras.
            instance = delegate->createMaterialInstance(config, uvmap, label, nullptr);
        }
        if (instance && extended()) {
            extend_uvmap(*uvmap);
            check_layout(original, *uvmap, label);
        }
        return instance ? record(instance, false) : nullptr;
    }
    const f::Material* const* getMaterials() const noexcept override { return delegate->getMaterials(); }
    size_t getMaterialsCount() const noexcept override { return delegate->getMaterialsCount(); }
    void destroyMaterials() override {
        for (auto* p : custom) if(p)engine->destroy(p);
        custom.clear();
        for (const auto& entry : surface_cache) engine->destroy(entry.material);
        surface_cache.clear();
        delegate->destroyMaterials();
    }
    bool needsDummyData(f::VertexAttribute attribute) const noexcept override {
        return attribute == f::VertexAttribute::UV0 || attribute == f::VertexAttribute::UV1 || attribute == f::VertexAttribute::COLOR || delegate->needsDummyData(attribute);
    }
};
}
g::MaterialProvider* create_material_provider(f::Engine* engine,bool compiled) { return new Provider(engine,compiled); }
void set_prepared_asset(g::MaterialProvider* provider, const PreparedAsset* asset) { static_cast<Provider*>(provider)->asset = asset; }
std::vector<f::Texture*> take_material_textures(g::MaterialProvider* provider) { return std::exchange(static_cast<Provider*>(provider)->textures,{}); }
std::vector<MaterialBinding> take_material_bindings(g::MaterialProvider* provider) { return std::exchange(static_cast<Provider*>(provider)->bindings,{}); }
std::vector<MaterialRecord> take_material_records(g::MaterialProvider* provider) { return std::exchange(static_cast<Provider*>(provider)->records,{}); }
f::MaterialInstance* create_material_instance(g::MaterialProvider* provider, g::MaterialKey& key, const char* label) {
    auto* p = static_cast<Provider*>(provider);
    const size_t mark = p->records.size();
    g::UvMap uv{};
    auto* instance = p->createMaterialInstance(&key, &uv, label, nullptr);
    p->records.resize(mark);
    if (!instance) throw AssetError("No glTF material matches the requested texture slots");
    return instance;
}
void configure_diffuse_environment(g::MaterialProvider* provider, f::MaterialInstance* instance,
        f::Texture* irradiance, float intensity, float rotation) {
    if (!instance->getMaterial()->hasParameter("backlightIntensity")) return;
    auto* p = static_cast<Provider*>(provider);
    f::TextureSampler sampler(f::TextureSampler::MinFilter::LINEAR,f::TextureSampler::MagFilter::LINEAR,
                              f::TextureSampler::WrapMode::CLAMP_TO_EDGE);
    instance->setParameter("backlightIrradiance",irradiance ? irradiance : p->dummy_cube,sampler);
    instance->setParameter("backlightIntensity",irradiance ? intensity : 0.0f);
    instance->setParameter("backlightRotation",m::mat3f::rotation(-rotation*0.01745329252f,m::float3{0,1,0}));
}
}
