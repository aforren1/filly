#pragma once
// Materials for filly's own output passes: the encode pass, which turns the scene-linear
// intermediate into the 8-bit output, and FXAA on the encoded image.

namespace filament { class Engine; class Material; }

namespace filly::detail {
// Encode flags, in the material's int parameter "flags".
enum EncodeFlag : int {
    // Apply the sRGB transfer function. Otherwise store the input as it is: linear values, or sRGB
    // values that Filament's color grading already encoded for a channel-mixing tone mapper.
    ENCODE_SRGB = 1,
    ENCODE_TRANSPARENT = 2,  // input is premultiplied, in the space it is stored in; output is srgb(c) * a
    ENCODE_DITHER = 4,       // add triangular noise of one level before rounding
    ENCODE_LUMA = 8,         // store luma in alpha, as FXAA's input for opaque views
};
// graded: the source is Filament's 8-bit sRGB output, already dithered. Inside the "inner"
// rectangle it is stored as it is; outside it, the "background" color, which the CPU encoded.
filament::Material* build_encode_material(filament::Engine& engine, bool graded);
filament::Material* build_fxaa_material(filament::Engine& engine);
}
