// Included inside the facade namespace after features.inc: runtime textures, host texture
// inputs, material texture slots, and generated meshes.
namespace {
f::TextureSampler texture_sampler(const std::string& filter, const std::string& wrap, bool mipmaps) {
    using S = f::TextureSampler;
    if (filter != "linear" && filter != "nearest")
        throw std::invalid_argument("filter must be 'linear' or 'nearest', got '" + filter + "'");
    S::WrapMode mode;
    if (wrap == "repeat") mode = S::WrapMode::REPEAT;
    else if (wrap == "clamp") mode = S::WrapMode::CLAMP_TO_EDGE;
    else if (wrap == "mirror") mode = S::WrapMode::MIRRORED_REPEAT;
    else throw std::invalid_argument("wrap must be 'repeat', 'clamp', or 'mirror', got '" + wrap + "'");
    const bool linear = filter == "linear";
    S sampler;
    sampler.setMagFilter(linear ? S::MagFilter::LINEAR : S::MagFilter::NEAREST);
    sampler.setMinFilter(mipmaps ? (linear ? S::MinFilter::LINEAR_MIPMAP_LINEAR : S::MinFilter::NEAREST_MIPMAP_NEAREST)
                                 : (linear ? S::MinFilter::LINEAR : S::MinFilter::NEAREST));
    sampler.setWrapModeS(mode);
    sampler.setWrapModeT(mode);
    return sampler;
}
bool color_space(const std::string& value) {
    if (value != "srgb" && value != "linear")
        throw std::invalid_argument("color_space must be 'srgb' or 'linear', got '" + value + "'");
    return value == "srgb";
}
// Copies caller pixels into upload storage. Only 1-channel sRGB data changes layout.
void stage(const detail::TextureData& data, const uint8_t* pixels, uint8_t* out) {
    if (!data.expand) {
        std::memcpy(out, pixels, data.input_bytes);
        return;
    }
    for (size_t i = 0; i < data.input_bytes; ++i) {
        out[4 * i] = out[4 * i + 1] = out[4 * i + 2] = pixels[i];
        out[4 * i + 3] = 255;
    }
}
void upload(detail::TextureData& data, uint8_t* bytes, f::backend::BufferDescriptor::Callback done, void* user) {
    auto& engine = *data.state->engine;
    data.texture->setImage(engine, 0, f::Texture::PixelBufferDescriptor(bytes, data.upload.size,
        data.format, data.type, done, user));
    if (data.mipmaps) data.texture->generateMipmaps(engine);
}
}

Texture Renderer::create_texture(int64_t width, int64_t height, int channels, bool is_float,
                                 const std::string& space, bool mipmaps, const std::string& filter,
                                 const std::string& wrap, const void* pixels, size_t bytes) {
    state_->check();
    if (width < 1 || height < 1 || width > 8192 || height > 8192)
        throw std::invalid_argument("Texture width and height must be from 1 through 8192, got "
                                    + std::to_string(width) + "x" + std::to_string(height));
    if (channels != 1 && channels != 3 && channels != 4)
        throw std::invalid_argument("Texture arrays need 1, 3, or 4 channels, got " + std::to_string(channels));
    const bool srgb = color_space(space);
    if (is_float && srgb)
        throw std::invalid_argument("float32 textures hold linear values; use color_space='linear'");
    auto& engine = *state_->engine;
    auto data = state_->track(std::make_shared<detail::TextureData>(state_));
    data->width = uint32_t(width);
    data->height = uint32_t(height);
    data->channels = channels;
    data->is_float = is_float;
    data->srgb = srgb;
    data->mipmaps = mipmaps;
    data->sampler = texture_sampler(filter, wrap, mipmaps);
    using T = f::Texture;
    using IF = T::InternalFormat;
    IF format;
    bool grey = channels == 1;
    if (is_float) {
        // Half floats keep 11 significant bits, finer than 8-bit output, at half the memory.
        format = channels == 1 ? IF::R16F : channels == 3 ? IF::RGB16F : IF::RGBA16F;
        data->type = T::Type::FLOAT;
    } else if (channels == 1 && srgb) {
        format = IF::SRGB8_A8;
        data->expand = true;
        grey = false;
    } else {
        format = channels == 1 ? IF::R8 : channels == 3 ? (srgb ? IF::SRGB8 : IF::RGB8) : (srgb ? IF::SRGB8_A8 : IF::RGBA8);
        data->type = T::Type::UBYTE;
    }
    const int stored = data->expand ? 4 : channels;
    data->format = stored == 1 ? T::Format::R : stored == 3 ? T::Format::RGB : T::Format::RGBA;
    const size_t component = is_float ? 4 : 1;
    data->input_bytes = size_t(width) * size_t(height) * size_t(channels) * component;
    data->upload.size = size_t(width) * size_t(height) * size_t(stored) * component;
    if (mipmaps && !T::isTextureFormatMipmappable(engine, format))
        throw std::invalid_argument("This driver cannot generate mipmaps for " + std::to_string(channels)
                                    + "-channel " + (is_float ? "float32" : "uint8") + " textures; use 4 channels");
    if (grey && !T::isTextureSwizzleSupported(engine))
        throw std::invalid_argument("This driver cannot swizzle 1-channel textures to grey; use 3 channels");
    auto builder = T::Builder().width(uint32_t(width)).height(uint32_t(height))
        .levels(mipmaps ? 0xff : 1).sampler(T::Sampler::SAMPLER_2D).format(format)
        .usage(T::Usage::SAMPLEABLE | T::Usage::UPLOADABLE | (mipmaps ? T::Usage::GEN_MIPMAPPABLE : T::Usage::NONE));
    // One channel reads as grey with alpha one, as a luminance image should.
    if (grey) builder.swizzle(T::Swizzle::CHANNEL_0, T::Swizzle::CHANNEL_0, T::Swizzle::CHANNEL_0, T::Swizzle::SUBSTITUTE_ONE);
    data->texture = builder.build(engine);
    if (!data->texture) throw FillyError("Could not create texture");
    if (bytes != data->input_bytes)
        throw std::invalid_argument("Texture needs " + std::to_string(data->input_bytes) + " bytes, got " + std::to_string(bytes));
    // The first upload uses a one-off buffer, so a texture that never changes keeps no staging storage.
    auto* first = new uint8_t[data->upload.size];
    stage(*data, static_cast<const uint8_t*>(pixels), first);
    upload(*data, first, [](void* buffer, size_t, void*) { delete[] static_cast<uint8_t*>(buffer); }, nullptr);
    return Texture(data);
}

Texture::Texture(std::shared_ptr<detail::TextureData> data) : data_(std::move(data)) {}
uint32_t Texture::width() const { data_->check(); return data_->width; }
uint32_t Texture::height() const { data_->check(); return data_->height; }
uint32_t Texture::channels() const { data_->check(); return uint32_t(data_->channels); }
bool Texture::is_float() const { data_->check(); return data_->is_float; }
std::string Texture::color_space() const { data_->check(); return data_->srgb ? "srgb" : "linear"; }
bool Texture::mipmaps() const { data_->check(); return data_->mipmaps; }
bool Texture::same(const Texture& other) const { return data_ == other.data_; }
uintptr_t Texture::key() const { return reinterpret_cast<uintptr_t>(data_.get()); }
bool Texture::closed() const { data_->state->check_thread(); return !data_->texture || !data_->state->engine; }
void Texture::close() {
    data_->state->check_thread();
    if (!data_->texture || !data_->state->engine) return;
    data_->release();
    data_->state->engine->flush();
}
void Texture::update(const void* pixels, size_t bytes) {
    data_->check();
    if (bytes != data_->input_bytes)
        throw std::invalid_argument("Texture update needs " + std::to_string(data_->input_bytes)
                                    + " bytes, got " + std::to_string(bytes));
    auto& slot = data_->upload.take(*data_->state->engine);
    stage(*data_, static_cast<const uint8_t*>(pixels), slot.bytes.get());
    upload(*data_, slot.bytes.get(), &detail::Staging::done, &slot);
}

HostTexture Renderer::import_gl_input(uint32_t texture, int64_t width, int64_t height,
                                      const std::string& space, const std::string& filter,
                                      const std::string& wrap) {
    state_->check();
    if (!state_->interop->shared()) throw InteropError("Create the renderer with shared_context first");
    check_target(width, height, "rgba8");
    const bool srgb = color_space(space);
    const auto sampler = texture_sampler(filter, wrap, false);
    state_->delete_views();
    // sRGB data is sampled through an sRGB view, so shaders read linear values.
    using View = detail::GlInterop::SrgbView;
    const auto name = state_->interop->import_texture(texture, uint32_t(width), uint32_t(height),
                                                      srgb ? View::REQUIRED : View::NONE);
    std::shared_ptr<detail::TextureData> data;
    try { data = state_->track(std::make_shared<detail::TextureData>(state_)); }
    catch (...) { if (srgb) state_->interop->delete_host_texture(name); throw; }
    if (srgb) data->host_view = name;
    data->host = true;
    data->width = uint32_t(width);
    data->height = uint32_t(height);
    data->srgb = srgb;
    data->sampler = sampler;
    using T = f::Texture;
    data->texture = T::Builder().width(data->width).height(data->height).levels(1)
        .sampler(T::Sampler::SAMPLER_2D).format(srgb ? T::InternalFormat::SRGB8_A8 : T::InternalFormat::RGBA8)
        .usage(T::Usage::SAMPLEABLE).import(name).build(*state_->engine);
    if (!data->texture) throw FillyError("Could not import the host texture");
    state_->host_inputs.push_back(data.get());
    return HostTexture(data);
}

bool is_host_texture(const std::shared_ptr<detail::TextureData>& data) { return data && data->host; }
HostTexture::HostTexture(std::shared_ptr<detail::TextureData> data) : data_(std::move(data)) {}
uint32_t HostTexture::width() const { data_->check(); return data_->width; }
uint32_t HostTexture::height() const { data_->check(); return data_->height; }
std::string HostTexture::color_space() const { data_->check(); return data_->srgb ? "srgb" : "linear"; }
bool HostTexture::writing() const { data_->state->check_thread(); return data_->writing; }
bool HostTexture::same(const HostTexture& other) const { return data_ == other.data_; }
uintptr_t HostTexture::key() const { return reinterpret_cast<uintptr_t>(data_.get()); }
bool HostTexture::closed() const { data_->state->check_thread(); return !data_->texture || !data_->state->engine; }
void HostTexture::begin_write() {
    data_->check();
    auto& interop = *data_->state->interop;
    interop.require_host();
    if (data_->writing) throw InteropError("The host texture is already open for writing");
    // The host must not overwrite pixels that a submitted frame still reads.
    if (data_->read_done.signal) {
        interop.wait_on_host(data_->read_done);
        interop.destroy(*data_->state->engine, data_->read_done);
    }
    data_->writing = true;
}
void HostTexture::end_write() {
    data_->check();
    if (!data_->writing) throw InteropError("end_write() requires a matching begin_write()");
    auto& interop = *data_->state->interop;
    // A fence from an earlier write that no render consumed is superseded by this one.
    interop.delete_host_fence(data_->host_done);
    data_->host_done = nullptr;
    data_->host_done = interop.host_fence();
    data_->writing = false;
}
void HostTexture::close() {
    data_->state->check_thread();
    if (!data_->texture || !data_->state->engine) return;
    if (data_->state->interop->host_current()) data_->state->interop->finish_host();
    data_->writing = false;
    data_->release();
    data_->state->engine->flushAndWait();
    data_->state->delete_views();
}

namespace {
int texture_slot(int slot) {
    if (slot != 0 && slot != 1) throw std::invalid_argument("Texture slot must be 0 (base color) or 1 (emissive)");
    return slot;
}
detail::Assignment* assignment_of(detail::ModelData& model, size_t index) {
    const size_t count = model.instance->getMaterialInstanceCount();
    if (index >= count) return index - count < model.locals.size() ? &model.locals[index - count].assignment : nullptr;
    auto* base = model.instance->getMaterialInstances()[index];
    for (auto& entry : model.overrides) if (entry.source == base) return &entry.assignment;
    return nullptr;
}
}
// `emissive` is the effective emission, emissiveFactor times emissiveStrength, so that it means the
// same for materials with and without KHR_materials_emissive_strength.
Vec3 Material::emissive() const {
    auto* instance = material_instance(data_, index_, "emissiveFactor");
    auto color = instance->getParameter<m::float3>("emissiveFactor");
    if (instance->getMaterial()->hasParameter("emissiveStrength"))
        color *= instance->getParameter<float>("emissiveStrength");
    return {color.x, color.y, color.z};
}
void Material::set_emissive(Vec3 value) {
    for (auto v : value) { finite(v); if (v < 0) throw std::invalid_argument("Emissive values must be nonnegative"); }
    auto* instance = material_instance(data_, index_, "emissiveFactor");
    instance->setParameter("emissiveFactor", m::float3{value[0], value[1], value[2]});
    if (instance->getMaterial()->hasParameter("emissiveStrength")) instance->setParameter("emissiveStrength", 1.0f);
}
std::shared_ptr<detail::TextureData> Material::texture(int slot) const {
    data_->check();
    texture_slot(slot);
    const auto* assignment = assignment_of(*data_, index_);
    return assignment ? assignment->textures[slot] : nullptr;
}
void Material::set_texture(int slot, std::shared_ptr<detail::TextureData> texture) {
    data_->check();
    texture_slot(slot);
    if (texture) {
        texture->check();
        if (texture->state != data_->state) throw std::invalid_argument("Texture must belong to this renderer");
    }
    detail::assign_texture(*data_, index_, slot, std::move(texture));
}
void Material::set_texture_transform(int slot, std::array<float, 2> offset, std::array<float, 2> scale, float rotation) {
    data_->check();
    texture_slot(slot);
    finite(offset); finite(scale); finite(rotation);
    auto* assignment = assignment_of(*data_, index_);
    if (!assignment || !assignment->textures[slot])
        throw FillyError("Assign a texture to this slot before setting its transform");
    const auto matrix = g::matrixFromUvTransform(offset.data(), rotation, scale.data());
    auto* instance = material_instance(data_, index_, detail::texture_maps[slot]);
    if (!instance->getMaterial()->hasParameter(detail::texture_matrices[slot]))
        throw AssetError("This material was compiled without texture transforms because it keeps other glTF textures");
    instance->setParameter(detail::texture_matrices[slot], matrix);
    assignment->transforms[slot] = matrix;
}

bool Model::is_mesh() const { data_->check(); return bool(data_->shared->mesh); }
size_t Model::vertex_count() const {
    data_->check();
    if (!data_->shared->mesh) throw FillyError("Only generated meshes have vertex data");
    return data_->shared->mesh->vertex_count;
}
namespace {
template <class T> void upload_attribute(f::Engine& engine, f::VertexBuffer* vertices, uint8_t index,
                                         detail::Staging& ring, const T* values) {
    auto& slot = ring.take(engine);
    std::memcpy(slot.bytes.get(), values, ring.size);
    vertices->setBufferAt(engine, index, f::VertexBuffer::BufferDescriptor(slot.bytes.get(), ring.size,
        &detail::Staging::done, &slot));
}
void upload_tangents(f::Engine& engine, detail::MeshGeometry& mesh) {
    auto& slot = mesh.tangent_upload.take(engine);
    detail::tangent_frames(mesh, reinterpret_cast<m::short4*>(slot.bytes.get()));
    mesh.vertices->setBufferAt(engine, 1, f::VertexBuffer::BufferDescriptor(slot.bytes.get(),
        mesh.tangent_upload.size, &detail::Staging::done, &slot));
}
void check_mesh(const MeshArrays& a, size_t vertices) {
    if (a.vertices != vertices) throw std::invalid_argument("Vertex arrays must have " + std::to_string(vertices) + " rows");
    for (const float* values : {a.positions, a.normals, a.uvs, a.colors}) {
        if (!values) continue;
        const size_t width = values == a.positions || values == a.normals ? 3 : values == a.uvs ? 2 : 4;
        for (size_t i = 0; i < vertices * width; ++i) finite(values[i]);
    }
}
}
void Model::attach_mesh(const MeshArrays& a) {
    data_->check();
    if (data_->shared->mesh) throw FillyError("Model already has mesh geometry");
    if (!a.positions || !a.indices || a.vertices < 3 || a.triangles < 1)
        throw std::invalid_argument("A mesh needs at least 3 positions and 1 triangle");
    check_mesh(a, a.vertices);
    for (size_t i = 0; i < a.triangles * 3; ++i)
        if (a.indices[i] >= a.vertices) throw std::invalid_argument("Triangle index " + std::to_string(a.indices[i]) + " is out of range");
    auto& engine = *data_->state->engine;
    auto mesh = std::make_unique<detail::MeshGeometry>();
    const size_t n = a.vertices;
    mesh->vertex_count = n;
    mesh->triangle_count = a.triangles;
    mesh->has_uvs = a.uvs != nullptr;
    mesh->has_colors = a.colors != nullptr;
    mesh->computed_normals = a.normals == nullptr;
    auto* p = reinterpret_cast<const m::float3*>(a.positions);
    mesh->positions.assign(p, p + n);
    mesh->triangles.assign(reinterpret_cast<const m::uint3*>(a.indices), reinterpret_cast<const m::uint3*>(a.indices) + a.triangles);
    mesh->normals.resize(n);
    if (a.normals) {
        auto* normal = reinterpret_cast<const m::float3*>(a.normals);
        for (size_t i = 0; i < n; ++i) {
            const float length = std::sqrt(dot(normal[i], normal[i]));
            if (!(length > 0)) throw std::invalid_argument("Normals must be nonzero");
            mesh->normals[i] = normal[i] / length;
        }
    } else {
        detail::smooth_normals(*mesh);
    }
    if (a.uvs) {
        auto* uv = reinterpret_cast<const m::float2*>(a.uvs);
        mesh->uvs.assign(uv, uv + n);
        mesh->tan1.resize(n);
        mesh->tan2.resize(n);
    }
    detail::update_bounds(*mesh);
    mesh->position_upload.size = n * sizeof(m::float3);
    mesh->tangent_upload.size = n * sizeof(m::short4);
    mesh->uv_upload.size = n * sizeof(m::float2);
    mesh->color_upload.size = n * sizeof(m::float4);
    using V = f::VertexBuffer;
    using A = f::VertexAttribute;
    // Separate buffers let an update send only the attributes that changed. Material packages
    // that serve every key (Filament's archive, filly's) require COLOR and UV1 and multiply by
    // the vertex color, so a mesh without colors supplies white; UV1 reads the UV0 buffer.
    auto vertices = V::Builder().vertexCount(uint32_t(n)).bufferCount(4)
        .attribute(A::POSITION, 0, V::AttributeType::FLOAT3)
        .attribute(A::TANGENTS, 1, V::AttributeType::SHORT4).normalized(A::TANGENTS)
        .attribute(A::UV0, 2, V::AttributeType::FLOAT2)
        .attribute(A::UV1, 2, V::AttributeType::FLOAT2);
    if (mesh->has_colors) vertices.attribute(A::COLOR, 3, V::AttributeType::FLOAT4);
    else vertices.attribute(A::COLOR, 3, V::AttributeType::UBYTE4).normalized(A::COLOR);
    mesh->vertices = vertices.build(engine);
    mesh->indices = f::IndexBuffer::Builder().indexCount(uint32_t(a.triangles * 3))
        .bufferType(f::IndexBuffer::IndexType::UINT).build(engine);
    if (!mesh->vertices || !mesh->indices) {
        mesh->release(engine);
        throw FillyError("Could not create mesh buffers");
    }
    // Creation uses Filament's SurfaceOrientation; updates use its allocation-free copy.
    f::geometry::SurfaceOrientation::Builder orientation;
    orientation.vertexCount(n).normals(mesh->normals.data()).positions(mesh->positions.data())
        .triangleCount(a.triangles).triangles(mesh->triangles.data());
    if (a.uvs) orientation.uvs(mesh->uvs.data());
    std::unique_ptr<f::geometry::SurfaceOrientation> frames(orientation.build());
    if (!frames) { mesh->release(engine); throw FillyError("Could not compute tangent frames"); }
    // Creation uploads use one-off buffers, so meshes that never change keep no staging storage.
    auto owned = [](size_t size) { return new uint8_t[size]; };
    auto free_bytes = [](void* buffer, size_t, void*) { delete[] static_cast<uint8_t*>(buffer); };
    auto send = [&](uint8_t index, const void* values, size_t size) {
        auto* bytes = owned(size);
        std::memcpy(bytes, values, size);
        mesh->vertices->setBufferAt(engine, index, V::BufferDescriptor(bytes, size, free_bytes));
    };
    send(0, mesh->positions.data(), mesh->position_upload.size);
    {
        auto* bytes = owned(mesh->tangent_upload.size);
        frames->getQuats(reinterpret_cast<m::short4*>(bytes), n);
        mesh->vertices->setBufferAt(engine, 1, V::BufferDescriptor(bytes, mesh->tangent_upload.size, free_bytes));
    }
    if (a.uvs) send(2, a.uvs, mesh->uv_upload.size);
    else {
        auto* bytes = owned(mesh->uv_upload.size);
        std::memset(bytes, 0, mesh->uv_upload.size);
        mesh->vertices->setBufferAt(engine, 2, V::BufferDescriptor(bytes, mesh->uv_upload.size, free_bytes));
    }
    if (a.colors) send(3, a.colors, mesh->color_upload.size);
    else {
        auto* bytes = owned(n * 4);
        std::memset(bytes, 0xFF, n * 4);
        mesh->vertices->setBufferAt(engine, 3, V::BufferDescriptor(bytes, n * 4, free_bytes));
    }
    {
        const size_t size = a.triangles * 3 * sizeof(uint32_t);
        auto* bytes = owned(size);
        std::memcpy(bytes, a.indices, size);
        mesh->indices->setBuffer(engine, f::IndexBuffer::BufferDescriptor(bytes, size, free_bytes));
    }
    data_->shared->mesh = std::move(mesh);
    detail::attach_geometry(*data_);
    engine.flush();
}
void Model::update_mesh(const MeshArrays& a) {
    data_->check();
    auto* mesh = data_->shared->mesh.get();
    if (!mesh) throw FillyError("Only generated meshes have vertex data");
    if (!a.positions && !a.normals && !a.uvs && !a.colors) throw std::invalid_argument("Give at least one vertex array");
    check_mesh(a, mesh->vertex_count);
    if (a.colors && !mesh->has_colors) throw std::invalid_argument("This mesh was created without vertex colors");
    if (a.normals && mesh->computed_normals)
        throw std::invalid_argument("This mesh computes its normals; create it with normals to update them");
    auto& engine = *data_->state->engine;
    const size_t n = mesh->vertex_count;
    if (a.normals) {
        auto* normal = reinterpret_cast<const m::float3*>(a.normals);
        for (size_t i = 0; i < n; ++i) {
            const float length = std::sqrt(dot(normal[i], normal[i]));
            if (!(length > 0)) throw std::invalid_argument("Normals must be nonzero");
            mesh->normals[i] = normal[i] / length;
        }
    }
    if (a.positions) {
        std::memcpy(mesh->positions.data(), a.positions, n * sizeof(m::float3));
        if (mesh->computed_normals) detail::smooth_normals(*mesh);
        detail::update_bounds(*mesh);
        upload_attribute(engine, mesh->vertices, 0, mesh->position_upload, mesh->positions.data());
        auto& rm = engine.getRenderableManager();
        for (auto entity : mesh->renderables)
            if (auto renderable = rm.getInstance(entity)) rm.setAxisAlignedBoundingBox(renderable, f::Box().set(mesh->low, mesh->high));
    }
    if (a.uvs) {
        if (!mesh->has_uvs) {
            // The first UVs switch tangent frames from the normal-only basis to Lengyel's method.
            mesh->has_uvs = true;
            mesh->uvs.resize(n);
            mesh->tan1.resize(n);
            mesh->tan2.resize(n);
        }
        std::memcpy(mesh->uvs.data(), a.uvs, n * sizeof(m::float2));
        upload_attribute(engine, mesh->vertices, 2, mesh->uv_upload, mesh->uvs.data());
    }
    if (a.positions || a.normals || a.uvs) upload_tangents(engine, *mesh);
    if (a.colors) upload_attribute(engine, mesh->vertices, 3, mesh->color_upload, a.colors);
}
