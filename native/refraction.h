#pragma once

namespace filly::detail {
// Filament 1.77.1 converts transmission roughness to a mip level with a per-view offset that it
// derives from tan(full vertical FOV) and the image height. An orthographic projection has no
// field of view, so that offset has no meaning there. Perspective views keep the SDK's level,
// which keeps parity with gltf_viewer. Orthographic views use the SDK's offset for tan(FOV) = 1,
// a 45-degree view: the blur is then the same fraction of the image height as in that view,
// independent of camera distance, orthographic extents, and scene units.
// materials.cmake reads the three strings into the refraction entries, and matc inserts the code
// before the SDK's lighting functions. Nothing compiles this header.
// Keep the sampler dispatch in sync with the pinned SDK when upgrading it.
inline constexpr const char* refraction_prefix = R"SHADER(
#if defined(MATERIAL_HAS_LIGHTING) && !defined(VARIANT_HAS_DEPTH)
float fpTransmissionRoughness;
#endif
)SHADER";

inline constexpr const char* refraction_material = R"SHADER(
#if defined(MATERIAL_HAS_LIGHTING) && !defined(VARIANT_HAS_DEPTH)
float fpEta = 1.5;
#if defined(MATERIAL_HAS_IOR)
fpEta = max(material.ior, 1.0);
#endif
// Same IOR remapping as the SDK's evaluateRefraction().
fpTransmissionRoughness = clamp(material.roughness, 0.0, 1.0)
    * (1.0 - clamp(3.0 / fpEta - 2.0, 0.0, 1.0));
#endif
)SHADER";

inline constexpr const char* refraction_suffix = R"SHADER(
#if defined(MATERIAL_HAS_LIGHTING) && !defined(VARIANT_HAS_DEPTH)
vec4 fpTextureLod(highp sampler2D s, vec2 uv, float lod) { return textureLod(s, uv, lod); }
vec4 fpTextureLod(highp samplerCube s, vec3 uv, float lod) { return textureLod(s, uv, lod); }
vec4 fpTextureLod(highp sampler2DArray s, vec3 uv, float lod) { return textureLod(s, uv, lod); }
vec4 fpTextureLod(highp sampler2D s, bool ssr, vec2 uv, float lod) { return textureLod(s, uv, lod); }
vec4 fpTextureLod(highp sampler2DArray s, bool ssr, vec3 uv, float lod) {
    // Layer zero is the opaque scene used for transmission; layer one is SSR.
    // A perspective projection has w = -z, so its [3][3] element is zero.
    if (uv.z == 0.0 && getClipFromViewMatrix()[3][3] != 0.0) {
        const float sigma0 = 22.0 / 6.0;
        float texelAtOneMeter = 1.0 / float(textureSize(s, 0).y);
        float a = fpTransmissionRoughness * fpTransmissionRoughness;
        lod = max(0.0, log2(max(a, 1.0e-30) / (sqrt(2.0) * sigma0 * texelAtOneMeter)) * 0.8614);
    }
    return textureLod(s, uv, lod);
}
// Only the SSR sampler contains transmission. SSAO and shadow arrays must
// retain their original LODs, including their layer-zero samples.
// In the pinned SDK this sampler is used only as a textureLod argument here.
// The extra argument selects the transmission overload without changing other
// sampler arrays. ES GLSL does not support token-pasting dispatch macros.
#define sampler0_ssr sampler0_ssr, true
#define textureLod fpTextureLod
#endif
)SHADER";
}
