// Included inside the facade namespace after resources.inc: Model::memory().
namespace {
// Bytes per pixel of uncompressed formats, or 0 for block-compressed ones.
uint64_t pixel_bytes(f::Texture::InternalFormat format) {
    using F = f::Texture::InternalFormat;
    switch (format) {
        case F::R8: case F::R8_SNORM: case F::R8UI: case F::R8I: case F::STENCIL8: return 1;
        case F::R16F: case F::R16UI: case F::R16I: case F::RG8: case F::RG8_SNORM: case F::RG8UI: case F::RG8I:
        case F::RGB565: case F::RGB5_A1: case F::RGBA4: case F::DEPTH16: return 2;
        case F::RGB8: case F::SRGB8: case F::RGB8_SNORM: case F::RGB8UI: case F::RGB8I: case F::DEPTH24: return 3;
        case F::RGB16F: case F::RGB16UI: case F::RGB16I: return 6;
        case F::RG32F: case F::RG32UI: case F::RG32I: case F::RGBA16F: case F::RGBA16UI: case F::RGBA16I: return 8;
        case F::RGB32F: case F::RGB32UI: case F::RGB32I: return 12;
        case F::RGBA32F: case F::RGBA32UI: case F::RGBA32I: return 16;
        default: return f::backend::isCompressedFormat(format) ? 0 : 4;
    }
}
// Block width, height, and bytes of a block-compressed format.
std::array<uint64_t, 3> block(f::Texture::InternalFormat format) {
    using F = f::Texture::InternalFormat;
    switch (format) {
        case F::EAC_R11: case F::EAC_R11_SIGNED: case F::ETC2_RGB8: case F::ETC2_SRGB8:
        case F::ETC2_RGB8_A1: case F::ETC2_SRGB8_A1: case F::DXT1_RGB: case F::DXT1_RGBA:
        case F::DXT1_SRGB: case F::DXT1_SRGBA: case F::RED_RGTC1: case F::SIGNED_RED_RGTC1: return {4, 4, 8};
        default: break;
    }
    const auto first = uint16_t(F::RGBA_ASTC_4x4), srgb = uint16_t(F::SRGB8_ALPHA8_ASTC_4x4);
    const auto value = uint16_t(format);
    if (value >= first && value < srgb + 14) {
        static constexpr uint64_t sizes[14][2] = {{4, 4}, {5, 4}, {5, 5}, {6, 5}, {6, 6}, {8, 5}, {8, 6},
                                                  {8, 8}, {10, 5}, {10, 6}, {10, 8}, {10, 10}, {12, 10}, {12, 12}};
        const auto& size = sizes[(value - first) % 14];
        return {size[0], size[1], 16};
    }
    return {4, 4, 16};
}
// Bytes of every mip level, face, and layer that the texture allocates.
uint64_t texture_bytes(const f::Texture& texture) {
    const auto format = texture.getFormat();
    const uint64_t pixel = pixel_bytes(format);
    const auto [bw, bh, bb] = block(format);
    const auto sampler = texture.getTarget();
    const uint64_t layers = sampler == f::Texture::Sampler::SAMPLER_CUBEMAP ? 6
        : sampler == f::Texture::Sampler::SAMPLER_CUBEMAP_ARRAY ? 6 * uint64_t(texture.getDepth()) : texture.getDepth();
    uint64_t total = 0;
    for (size_t level = 0; level < texture.getLevels(); ++level) {
        const uint64_t w = std::max<uint64_t>(texture.getWidth() >> level, 1);
        const uint64_t h = std::max<uint64_t>(texture.getHeight() >> level, 1);
        const uint64_t d = sampler == f::Texture::Sampler::SAMPLER_3D ? std::max<uint64_t>(texture.getDepth() >> level, 1) : layers;
        total += d * (pixel ? w * h * pixel : ((w + bw - 1) / bw) * ((h + bh - 1) / bh) * bb);
    }
    return total;
}
uint64_t table_bytes(const detail::PreparedAsset& tables) {
    uint64_t total = 0;
    auto texture = [&](const detail::AssetTexture& item) { total += item.bytes.size(); };
    for (const auto& item : tables.diffuse) { texture(item.factor_texture); texture(item.color_texture); texture(item.thickness_texture); }
    for (const auto& item : tables.textures) texture(item);
    for (const auto& clip : tables.animations)
        for (const auto& track : clip.tracks) total += (track.times.size() + track.values.size()) * sizeof(float);
    return total;
}
}

ModelMemory Model::memory() const {
    data_->check();
    const auto& shared = *data_->shared;
    const auto& tables = *shared.prepared;
    ModelMemory result;
    for (const auto* texture : shared.loaded_textures) result.gpu_texture_bytes += texture_bytes(*texture);
    for (const auto* texture : shared.textures) result.gpu_texture_bytes += texture_bytes(*texture);
    result.gpu_geometry_bytes = tables.geometry_bytes + tables.instance_geometry_bytes * shared.instances;
    if (shared.clonable) result.cpu_bytes = shared.source_bytes + table_bytes(tables);
    result.cpu_bytes += tables.instance_cpu_bytes * shared.instances;
    if (const auto* mesh = shared.mesh.get()) {
        const uint64_t n = mesh->vertex_count;
        result.gpu_geometry_bytes += n * (12 + 8 + 8 + (mesh->has_colors ? 16 : 4)) + mesh->triangle_count * 12;
        result.cpu_bytes += (mesh->positions.size() + mesh->normals.size() + mesh->tan1.size() + mesh->tan2.size()) * 12
            + mesh->uvs.size() * 8 + mesh->triangles.size() * 12;
        for (const auto* ring : {&mesh->position_upload, &mesh->tangent_upload, &mesh->uv_upload, &mesh->color_upload})
            for (const auto& slot : ring->slots) if (slot.bytes) result.cpu_bytes += ring->size;
    }
    result.clone_gpu_bytes = tables.instance_geometry_bytes;
    result.clone_cpu_bytes = tables.instance_cpu_bytes;
    // Only models hold the asset, so its owners are the live models that share it.
    result.models = size_t(data_->shared.use_count());
    return result;
}
