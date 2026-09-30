#include "output_pass.h"
#include "renderer.h"

#include <filament/Engine.h>
#include <filament/Material.h>
#include <filamat/MaterialBuilder.h>


namespace filly::detail {
namespace {
using B = filamat::MaterialBuilder;

// Both passes draw one triangle in clip space that covers the viewport, so they need no camera
// transform, no culling, and no depth.
void full_screen(B& builder, const char* name, const char* code) {
    builder.name(name).shading(B::Shading::UNLIT).vertexDomain(B::VertexDomain::DEVICE)
        .blending(B::BlendingMode::OPAQUE).culling(B::CullingMode::NONE)
        .depthWrite(false).depthCulling(false).flipUV(false)
        .platform(B::Platform::DESKTOP).targetApi(B::TargetApi::OPENGL)
        // No lighting, skinning, fog, shadows, or stereo: fewer variants to compile.
        .variantFilter(filament::UserVariantFilterMask(filament::UserVariantFilterBit::ALL))
        .material(code);
}

filament::Material* build(filament::Engine& engine, B& builder, const char* what) {
    auto package = builder.build(engine.getJobSystem());
    if (!package.isValid()) throw BackendError(std::string("Could not compile the ") + what + " material");
    auto* material = filament::Material::Builder().package(package.getData(), package.getSize()).build(engine);
    if (!material) throw BackendError(std::string("Could not create the ") + what + " material");
    return material;
}

// The analytic transfer function in fp32, then an explicit rounding to 8-bit levels, so the
// stored value does not depend on how a driver converts floats to UNORM8.
constexpr const char* encode_code = R"SHADER(
float triangleNoise(highp vec2 n) {
    // Filament's temporal dithering noise (inline_dithering.fs), in [-1, 1).
    n = fract(n * vec2(5.3987, 5.4421));
    n += dot(n.yx, n.xy + vec2(21.5351, 14.3137));
    highp float xy = n.x * n.y;
    return fract(xy * 95.4307) + fract(xy * 75.04961) - 1.0;
}

void material(inout MaterialInputs material) {
    prepareMaterial(material);
    int flags = materialParams.flags;
    bool transparent = (flags & 2) != 0;
    highp vec2 p = gl_FragCoord.xy;
    highp vec4 c = texelFetch(materialParams_source, ivec2(p), 0);
#if FILLY_GRADED
    // Filament's color grading already encoded, dithered, and rounded the viewport's pixels;
    // when transparent, as srgb(c) * a. Outside the viewport the CPU encoded the clear color.
    // With the transfer function behind the viewport test, this pass cost 1.0 ms more per 1080p
    // frame on the Intel GPU; the test in the default material cost 0.05 ms.
    bool inside = all(greaterThanEqual(p, materialParams.inner.xy)) && all(lessThan(p, materialParams.inner.zw));
    highp vec4 o = inside ? (transparent ? c : vec4(c.rgb, 1.0)) : materialParams.background;
#else
    highp float a = transparent ? clamp(c.a, 0.0, 1.0) : 1.0;
    // Transparent input is premultiplied in linear space; the encoding applies to the straight
    // color, and the output is premultiplied again after encoding.
    highp vec3 s = transparent ? (a > 0.0 ? c.rgb / a : vec3(0.0)) : c.rgb;
    // The linear tone mapper is a clamp. Other tone mappers already ran in Filament.
    s = clamp(s, 0.0, 1.0);
    if ((flags & 1) != 0) {
        for (int i = 0; i < 3; ++i)
            s[i] = s[i] <= 0.0031308 ? 12.92 * s[i] : 1.055 * pow(s[i], 1.0 / 2.4) - 0.055;
    }
    highp vec4 o = vec4(s * a, a);
    if ((flags & 4) != 0) {
        highp vec2 uv = gl_FragCoord.xy * materialParams.frame.yz + vec2(0.07 * materialParams.frame.x);
        highp float n = triangleNoise(uv) / 255.0;
        o += transparent ? vec4(n) : vec4(n, n, n, 0.0);
    }
#endif
    o = floor(clamp(o, 0.0, 1.0) * 255.0 + 0.5) * (1.0 / 255.0);
    // FXAA reads perceptual luma from alpha for opaque views, as after Filament's color grading.
    if ((flags & 8) != 0) o.a = dot(o.rgb, vec3(0.2126, 0.7152, 0.0722));
    material.baseColor = o;
}
)SHADER";

// FXAA 3.11 console variant with the G3D patches, as in Filament 1.77.1
// (filament/src/materials/antiAliasing/fxaa/fxaa.fs):
//   NVIDIA FXAA 3.11 by Timothy Lottes. Copyright (c) 2010, 2011 NVIDIA Corporation. All rights
//   reserved. Provided "as is"; NVIDIA and its suppliers disclaim all warranties.
//   G3D Innovation Engine, Copyright 2000-2018 Morgan McGuire. All rights reserved. BSD License.
// It runs on filly's encoded image instead of Filament's color-grading output, so a flat field
// keeps exactly the encoded value that the image has without FXAA.
constexpr const char* fxaa_code = R"SHADER(
highp vec4 fxaaTap(highp vec2 uv) {
    // Keep taps inside the rendered viewport; outside it the input holds an earlier frame.
    return textureLod(materialParams_ldr, clamp(uv, materialParams.bounds.xy, materialParams.bounds.zw), 0.0);
}

float fxaaLuma(highp vec4 c) {
    // Transparent views keep alpha, so luma is green there, as in Filament.
    return materialParams.transparent != 0 ? c.g : c.a;
}

highp vec4 fxaa(highp vec2 pos, highp vec4 corners, highp vec4 rgbyM, highp vec2 rcpFrame) {
    const float edgeSharpness = 8.0;
    const float edgeThreshold = 0.08;
    const float edgeThresholdMin = 0.04;
    float lumaNw = fxaaLuma(fxaaTap(corners.xy));
    float lumaSw = fxaaLuma(fxaaTap(corners.xw));
    float lumaNe = fxaaLuma(fxaaTap(corners.zy));
    float lumaSe = fxaaLuma(fxaaTap(corners.zw));
    float lumaM = fxaaLuma(rgbyM);
    float lumaMax = max(max(lumaNe, lumaSe), max(lumaNw, lumaSw));
    float lumaMin = min(min(lumaNe, lumaSe), min(lumaNw, lumaSw));
    float lumaMinM = min(lumaMin, lumaM);
    float lumaMaxM = max(lumaMax, lumaM);
    float range = lumaMaxM - lumaMinM;
    if (range < max(edgeThresholdMin, lumaMax * edgeThreshold)) return rgbyM;
    float dirSwMinusNe = lumaSw - lumaNe;
    float dirSeMinusNw = lumaSe - lumaNw;
    highp vec2 dir = vec2(dirSwMinusNe + dirSeMinusNw, dirSwMinusNe - dirSeMinusNw);
    float dirLength = length(dir);
    if (dirLength < 1.0e-6) return rgbyM;
    highp vec2 dir1 = dir / dirLength;
    highp vec4 rgbyN1 = fxaaTap(pos - dir1 * rcpFrame);
    highp vec4 rgbyP1 = fxaaTap(pos + dir1 * rcpFrame);
    float dirAbsMinTimesC = max(abs(dir1.x), abs(dir1.y)) * edgeSharpness * 0.015;
    highp vec2 dir2 = dir1 * min(range / dirAbsMinTimesC, 3.0);
    highp vec4 rgbyN2 = fxaaTap(pos - dir2 * 2.0 * rcpFrame);
    highp vec4 rgbyP2 = fxaaTap(pos + dir2 * 2.0 * rcpFrame);
    highp vec4 rgbyA = rgbyN1 + rgbyP1;
    highp vec4 rgbyB = (rgbyN2 + rgbyP2) * 0.25 + rgbyA * 0.25;
    float lumaB = fxaaLuma(rgbyB);
    if (lumaB < lumaMin || lumaB > lumaMax) rgbyB.xyz = rgbyA.xyz * 0.5;
    return rgbyB * 0.75 + rgbyM * 0.25;
}

void material(inout MaterialInputs material) {
    prepareMaterial(material);
    highp vec2 texel = materialParams.texel.xy;
    highp vec2 pos = gl_FragCoord.xy * texel;
    highp vec4 corners = vec4(pos - 0.5 * texel, pos + 0.5 * texel);
    // The center is fetched, not filtered, so an untouched pixel keeps its exact level.
    highp vec4 rgbyM = texelFetch(materialParams_ldr, ivec2(gl_FragCoord.xy), 0);
    highp vec4 o = fxaa(pos, corners, rgbyM, texel);
    if (materialParams.transparent == 0) o.a = 1.0;
    material.baseColor = floor(clamp(o, 0.0, 1.0) * 255.0 + 0.5) * (1.0 / 255.0);
}
)SHADER";
}

filament::Material* build_encode_material(filament::Engine& engine, bool graded) {
    B::init();
    struct Shutdown { ~Shutdown() { B::shutdown(); } } shutdown;
    B builder;
    const std::string code = std::string(graded ? "#define FILLY_GRADED 1\n" : "#define FILLY_GRADED 0\n") + encode_code;
    full_screen(builder, graded ? "filly encode graded" : "filly encode", code.c_str());
    builder.parameter("source", B::SamplerType::SAMPLER_2D, B::SamplerFormat::FLOAT, B::ParameterPrecision::HIGH)
        .parameter("flags", B::UniformType::INT)
        // x: temporal noise in [0, 1); yz: 1 / viewport size, for the dithering pattern.
        .parameter("frame", B::UniformType::FLOAT4, B::ParameterPrecision::HIGH);
    // The scene viewport (x0, y0, x1, y1) in pixels, and the clear color.
    if (graded)
        builder.parameter("inner", B::UniformType::FLOAT4, B::ParameterPrecision::HIGH)
            .parameter("background", B::UniformType::FLOAT4, B::ParameterPrecision::HIGH);
    return build(engine, builder, graded ? "graded encode" : "encode");
}

filament::Material* build_fxaa_material(filament::Engine& engine) {
    B::init();
    struct Shutdown { ~Shutdown() { B::shutdown(); } } shutdown;
    B builder;
    full_screen(builder, "filly fxaa", fxaa_code);
    builder.parameter("ldr", B::SamplerType::SAMPLER_2D, B::SamplerFormat::FLOAT, B::ParameterPrecision::HIGH)
        .parameter("texel", B::UniformType::FLOAT4, B::ParameterPrecision::HIGH)
        .parameter("bounds", B::UniformType::FLOAT4, B::ParameterPrecision::HIGH)
        .parameter("transparent", B::UniformType::INT);
    return build(engine, builder, "FXAA");
}
}
