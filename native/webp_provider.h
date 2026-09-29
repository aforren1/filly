#pragma once
#include <gltfio/TextureProvider.h>

namespace filament { class Engine; }

namespace filly::detail {
// A TextureProvider for image/webp; the caller owns it.
filament::gltfio::TextureProvider* create_webp_provider(filament::Engine* engine);
}
