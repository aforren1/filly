// Included in detail after animation.inc: runtime textures on glTF materials, and the geometry
// of generated meshes.

// The instance that a shared material handle shows.
f::MaterialInstance* shown(const ModelData& model, f::MaterialInstance* instance) {
    for (const auto& entry : model.overrides) if (entry.source == instance) return entry.current;
    return instance;
}
// The glTF instance behind a bound instance, and the runtime textures that the bound one has.
f::MaterialInstance* glTF_source(const ModelData& model, f::MaterialInstance* bound, Assignment* assignment) {
    for (const auto& entry : model.overrides)
        if (entry.current == bound) { *assignment = entry.assignment; return entry.source; }
    return bound;
}
const MaterialRecord& record_of(const ModelData& model, const f::MaterialInstance* instance) {
    for (const auto& record : model.records) if (record.instance == instance) return record;
    throw AssetError("Material has no glTF material key");
}

namespace {
constexpr const char* texture_maps[2] = {"baseColorMap", "emissiveMap"};
constexpr const char* texture_matrices[2] = {"baseColorUvMatrix", "emissiveUvMatrix"};
constexpr const char* texture_indices[2] = {"baseColorIndex", "emissiveIndex"};
#if !FILLY_MATERIALS_ARCHIVE
bool same_key(const g::MaterialKey& a, const g::MaterialKey& b) {
    // Filament 1.77.1's equality operator omits the dispersion bit.
    return a == b && a.hasDispersion == b.hasDispersion;
}
bool has_slot(const g::MaterialKey& key, int slot) {
    return slot ? key.hasEmissiveTexture : key.hasBaseColorTexture;
}
// Textures that a material rebuilt from its key would lose: no public API reads them back.
bool other_textures(const g::MaterialKey& k, const Assignment& a) {
    // gltfio leaves the texture bits of disabled features set; only enabled features sample them.
    return (k.hasBaseColorTexture && !a.textures[0]) || (k.hasEmissiveTexture && !a.textures[1])
        || k.hasMetallicRoughnessTexture || k.hasNormalTexture || k.hasOcclusionTexture
        || (k.hasClearCoat && (k.hasClearCoatTexture || k.hasClearCoatRoughnessTexture || k.hasClearCoatNormalTexture))
        || (k.hasSheen && (k.hasSheenColorTexture || k.hasSheenRoughnessTexture))
        || (k.hasTransmission && k.hasTransmissionTexture) || (k.hasVolume && k.hasVolumeThicknessTexture)
        || (k.hasSpecular && (k.hasSpecularTexture || k.hasSpecularColorTexture));
}
g::MaterialKey desired_key(const g::MaterialKey& base, const Assignment& a) {
    auto key = base;
    if (a.textures[0] && !key.hasBaseColorTexture) { key.hasBaseColorTexture = true; key.baseColorUV = 0; }
    if (a.textures[1] && !key.hasEmissiveTexture) { key.hasEmissiveTexture = true; key.emissiveUV = 0; }
    // Transforms need a rebuilt material, which is possible unless other glTF textures exist.
    if (a.any() && !key.hasTextureTransforms && (!same_key(key, base) || !other_textures(base, a)))
        key.hasTextureTransforms = true;
    return key;
}
#endif
// Copies factors and render state. Texture indices come from the provider's key, and the slot
// matrices belong to the runtime textures, so neither is copied.
void carry(const f::MaterialInstance* from, f::MaterialInstance* to) {
    const auto* source = from->getMaterial();
    const auto* target = to->getMaterial();
    std::vector<f::Material::ParameterInfo> infos(source->getParameterCount());
    source->getParameters(infos.data(), infos.size());
    using U = f::backend::UniformType;
    for (const auto& info : infos) {
        const std::string_view name(info.name);
        if (info.isSampler || info.isSubpass || info.count > 1 || name.empty() || name[0] == '_'
                || name.ends_with("Index") || name == texture_matrices[0] || name == texture_matrices[1]
                || !target->hasParameter(info.name))
            continue;
        const char* n = info.name;
        switch (info.type) {
            case U::FLOAT: to->setParameter(n, from->getParameter<float>(n)); break;
            case U::FLOAT2: to->setParameter(n, from->getParameter<m::float2>(n)); break;
            case U::FLOAT3: to->setParameter(n, from->getParameter<m::float3>(n)); break;
            case U::FLOAT4: to->setParameter(n, from->getParameter<m::float4>(n)); break;
            case U::INT: to->setParameter(n, from->getParameter<int32_t>(n)); break;
            case U::INT2: to->setParameter(n, from->getParameter<m::int2>(n)); break;
            case U::INT3: to->setParameter(n, from->getParameter<m::int3>(n)); break;
            case U::INT4: to->setParameter(n, from->getParameter<m::int4>(n)); break;
            case U::UINT: to->setParameter(n, from->getParameter<uint32_t>(n)); break;
            case U::UINT2: to->setParameter(n, from->getParameter<m::uint2>(n)); break;
            case U::UINT3: to->setParameter(n, from->getParameter<m::uint3>(n)); break;
            case U::UINT4: to->setParameter(n, from->getParameter<m::uint4>(n)); break;
            case U::MAT3: to->setParameter(n, from->getParameter<m::mat3f>(n)); break;
            case U::MAT4: to->setParameter(n, from->getParameter<m::mat4f>(n)); break;
            default: break;
        }
    }
    if (source->getBlendingMode() == f::BlendingMode::MASKED && target->getBlendingMode() == f::BlendingMode::MASKED)
        to->setMaskThreshold(from->getMaskThreshold());
    to->setCullingMode(from->getCullingMode(), from->getShadowCullingMode());
    to->setTransparencyMode(from->getTransparencyMode());
    to->setDepthWrite(from->isDepthWriteEnabled());
    to->setDepthCulling(from->isDepthCullingEnabled());
    to->setColorWrite(from->isColorWriteEnabled());
}
void bind(f::MaterialInstance* instance, const Assignment& a) {
    for (int slot = 0; slot < 2; ++slot) {
        if (!a.textures[slot]) continue;
        instance->setParameter(texture_maps[slot], a.textures[slot]->texture, a.textures[slot]->sampler);
        if (instance->getMaterial()->hasParameter(texture_matrices[slot]))
            instance->setParameter(texture_matrices[slot], a.transforms[slot]);
        // Archive entries select the UV set with a uniform; runtime textures use TEXCOORD_0.
        if (FILLY_MATERIALS_ARCHIVE) instance->setParameter(texture_indices[slot], 0);
    }
}
}

#if FILLY_MATERIALS_ARCHIVE
// Assigns or removes (null) the runtime texture of one material handle. Every archive entry has
// base color and emissive samplers, so a handle with runtime textures shows a copy of its glTF
// instance with changed parameters. A removal starts again from a copy of the glTF instance,
// which still has the glTF texture of that slot, and the factors carry over.
void assign_texture(ModelData& model, size_t index, int slot, std::shared_ptr<TextureData> texture) {
    auto& engine = *model.state->engine;
    const size_t count = model.instance->getMaterialInstanceCount();
    if (index >= count + model.locals.size()) throw AssetError("Invalid material index");
    LocalMaterial* local = index >= count ? &model.locals[index - count] : nullptr;
    f::MaterialInstance* base = local ? local->source : model.instance->getMaterialInstances()[index];
    SharedOverride* entry = nullptr;
    if (!local) for (auto& item : model.overrides) if (item.source == base) entry = &item;
    f::MaterialInstance* current = local ? local->copy : entry ? entry->current : base;
    const auto& record = record_of(model, base);
    const std::string name = base->getName() ? base->getName() : "";
    if (slot == 1 && record.key.unlit) throw AssetError("Unlit material '" + name + "' has no emissive input");
    const Assignment previous = local ? local->assignment : entry ? entry->assignment : Assignment{};
    if (previous.textures[slot] == texture) return;
    Assignment next = previous;
    next.textures[slot] = texture;
    if (!texture) next.transforms[slot] = m::mat3f{};
    f::MaterialInstance* replacement = current;
    if (!local && !next.any()) replacement = base;
    else if (!texture || current == base) replacement = f::MaterialInstance::duplicate(base, name.c_str());
    if (replacement != current) carry(current, replacement);
    bind(replacement, next);
#else
// Assigns or removes (null) the runtime texture of one material handle. A handle whose glTF
// material already has the slot shows a copy of it; otherwise the provider supplies the
// material for a key with the slot, and the factors carry over.
void assign_texture(ModelData& model, size_t index, int slot, std::shared_ptr<TextureData> texture) {
    auto& engine = *model.state->engine;
    const size_t count = model.instance->getMaterialInstanceCount();
    if (index >= count + model.locals.size()) throw AssetError("Invalid material index");
    LocalMaterial* local = index >= count ? &model.locals[index - count] : nullptr;
    f::MaterialInstance* base = local ? local->source : model.instance->getMaterialInstances()[index];
    SharedOverride* entry = nullptr;
    if (!local) for (auto& item : model.overrides) if (item.source == base) entry = &item;
    f::MaterialInstance* current = local ? local->copy : entry ? entry->current : base;
    const auto& record = record_of(model, base);
    const std::string name = base->getName() ? base->getName() : "";
    if (record.custom)
        throw AssetError("Material '" + name + "' uses a custom shader (diffuse transmission, anisotropy, "
                         "or iridescence), which has no runtime texture slots");
    if (slot == 1 && record.key.unlit) throw AssetError("Unlit material '" + name + "' has no emissive input");
    const Assignment previous = local ? local->assignment : entry ? entry->assignment : Assignment{};
    if (previous.textures[slot] == texture) return;
    Assignment next = previous;
    next.textures[slot] = texture;
    if (!texture) next.transforms[slot] = m::mat3f{};
    const auto& base_key = record.key;
    const auto key = desired_key(base_key, next);
    const bool copy_of_base = same_key(key, base_key);
    if (!copy_of_base && other_textures(base_key, next))
        throw AssetError("Material '" + name + "' has glTF textures in other slots; a material "
                         "compiled with a new texture slot cannot keep them");
    const auto current_key = previous.keyed ? previous.key : base_key;
    const bool rebuild = !same_key(key, current_key) || (!texture && has_slot(base_key, slot))
        || (!local && !entry);
    f::MaterialInstance* replacement = current;
    if (rebuild) {
        if (!next.any() && !local) replacement = base;
        else if (copy_of_base) replacement = f::MaterialInstance::duplicate(base, name.c_str());
        else {
            auto request = key;
            replacement = create_material_instance(model.state->materials, request, name.c_str());
        }
        try {
            // Animation writes every parameter of the glTF material to the replacement too.
            std::vector<f::Material::ParameterInfo> infos(base->getMaterial()->getParameterCount());
            base->getMaterial()->getParameters(infos.data(), infos.size());
            for (const auto& info : infos)
                if (!info.isSampler && !info.isSubpass && !replacement->getMaterial()->hasParameter(info.name))
                    throw AssetError("The textured variant of material '" + name + "' lacks parameter " + info.name);
            if (replacement != current) carry(current, replacement);
        } catch (...) {
            if (replacement != base) engine.destroy(replacement);
            throw;
        }
    }
    next.keyed = !copy_of_base;
    next.key = next.keyed ? key : g::MaterialKey{};
    if (!next.any()) next.keyed = false;
    bind(replacement, next);
#endif
    if (texture) {
        auto& users = texture->users;
        std::erase_if(users, [](const auto& user) { return user.expired(); });
        const bool known = std::any_of(users.begin(), users.end(), [&](const auto& user) { return user.lock().get() == &model; });
        if (!known) users.push_back(std::static_pointer_cast<ModelData>(model.shared_from_this()));
    }
    auto& rm = engine.getRenderableManager();
    if (local) {
        if (replacement != current) {
            rm.setMaterialInstanceAt(rm.getInstance(local->entity), local->slot, replacement);
            engine.destroy(current);
            local->copy = replacement;
        }
        local->assignment = std::move(next);
        return;
    }
    if (replacement != current) {
        model.swap_bound(current, replacement);
        if (current != base) engine.destroy(current);
    }
    if (!next.any()) {
        std::erase_if(model.overrides, [&](const SharedOverride& item) { return item.source == base; });
    } else if (entry) {
        entry->current = replacement;
        entry->assignment = std::move(next);
    } else {
        model.overrides.push_back({base, replacement, std::move(next)});
    }
}
// Removes a closing texture from every material handle of a model.
void detach_texture(ModelData& model, const TextureData* texture) {
    const size_t count = model.instance->getMaterialInstanceCount();
    for (bool found = true; found;) {
        found = false;
        for (size_t i = 0; i < model.overrides.size() && !found; ++i)
            for (int slot = 0; slot < 2 && !found; ++slot)
                if (model.overrides[i].assignment.textures[slot].get() == texture) {
                    const auto* instances = model.instance->getMaterialInstances();
                    const auto position = size_t(std::find(instances, instances + count, model.overrides[i].source) - instances);
                    assign_texture(model, position, slot, nullptr);
                    found = true;
                }
        for (size_t i = 0; i < model.locals.size() && !found; ++i)
            for (int slot = 0; slot < 2 && !found; ++slot)
                if (model.locals[i].assignment.textures[slot].get() == texture) {
                    assign_texture(model, count + i, slot, nullptr);
                    found = true;
                }
    }
}
// Variants put glTF instances back into their slots; textured shared materials follow them.
void apply_overrides(ModelData& model) {
    for (const auto& entry : model.overrides) model.swap_bound(entry.source, entry.current);
}

void TextureData::release() noexcept {
    if (texture && state->engine) {
        auto owners = std::move(users);
        users.clear();
        for (const auto& owner : owners) {
            auto model = owner.lock();
            if (!model || !model->asset) continue;
            try { detach_texture(*model, this); } catch (...) {}
        }
        if (host) {
            std::erase(state->host_inputs, this);
            state->interop->enqueue_wait(*state->engine, host_done);
            host_done = nullptr;
            state->interop->destroy(*state->engine, read_done);
        }
        if (upload.busy()) detail::flush_and_wait(*state->engine);
        state->engine->destroy(texture);
    }
    if (host_view) state->stale_views.push_back(host_view);
    host_view = 0;
    texture = nullptr;
    users.clear();
}

namespace {
// Copies of the helpers in Filament 1.77.1 libs/geometry/src/SurfaceOrientation.cpp.
m::float3 random_perpendicular(const m::float3& n) {
    m::float3 perp = cross(n, m::float3{1, 0, 0});
    float sqrlen = dot(perp, perp);
    if (sqrlen <= std::numeric_limits<float>::epsilon()) {
        perp = cross(n, m::float3{0, 1, 0});
        sqrlen = dot(perp, perp);
    }
    return perp / sqrlen;
}
void frisvad(const m::float3& N, m::float3& T, m::float3& B) {
    if (N.y < -1.0f + std::numeric_limits<float>::epsilon()) {
        T = m::float3{-1.0f, 0.0f, 0.0f};
        B = m::float3{0.0f, 0.0f, -1.0f};
        return;
    }
    const float a = 1.0f / (1.0f + N.y);
    const float b = -N.z * N.x * a;
    T = m::float3(b, -N.z, 1.0f - N.z * N.z * a);
    B = m::float3(1.0f - N.x * N.x * a, -N.x, b);
}
}

// Area-weighted vertex normals: the cross product of two edges has twice the triangle's area.
// Vertices that no triangle uses get +Z.
void smooth_normals(MeshGeometry& mesh) {
    std::fill(mesh.normals.begin(), mesh.normals.end(), m::float3{0, 0, 0});
    for (const auto& t : mesh.triangles) {
        const auto& a = mesh.positions[t.x];
        const auto n = cross(mesh.positions[t.y] - a, mesh.positions[t.z] - a);
        mesh.normals[t.x] += n;
        mesh.normals[t.y] += n;
        mesh.normals[t.z] += n;
    }
    for (auto& n : mesh.normals) {
        const float length = std::sqrt(dot(n, n));
        n = length > 0 && std::isfinite(length) ? n / length : m::float3{0, 0, 1};
    }
}
// The same frames as SurfaceOrientation (Lengyel's method with UVs, Frisvad's basis without),
// written into caller storage. SurfaceOrientation allocates on every build, so updates use this.
void tangent_frames(MeshGeometry& mesh, m::short4* out) {
    const size_t count = mesh.vertex_count;
    if (!mesh.has_uvs) {
        for (size_t i = 0; i < count; ++i) {
            m::float3 t, b;
            frisvad(mesh.normals[i], t, b);
            out[i] = m::packSnorm16(m::mat3f::packTangentFrame({t, b, mesh.normals[i]}).xyzw);
        }
        return;
    }
    std::fill(mesh.tan1.begin(), mesh.tan1.end(), m::float3{0, 0, 0});
    std::fill(mesh.tan2.begin(), mesh.tan2.end(), m::float3{0, 0, 0});
    for (const auto& tri : mesh.triangles) {
        const auto& v1 = mesh.positions[tri.x];
        const auto& v2 = mesh.positions[tri.y];
        const auto& v3 = mesh.positions[tri.z];
        const auto& w1 = mesh.uvs[tri.x];
        const auto& w2 = mesh.uvs[tri.y];
        const auto& w3 = mesh.uvs[tri.z];
        float x1 = v2.x - v1.x, x2 = v3.x - v1.x;
        float y1 = v2.y - v1.y, y2 = v3.y - v1.y;
        float z1 = v2.z - v1.z, z2 = v3.z - v1.z;
        float s1 = w2.x - w1.x, s2 = w3.x - w1.x;
        float t1 = w2.y - w1.y, t2 = w3.y - w1.y;
        float d = s1 * t2 - s2 * t1;
        m::float3 sdir, tdir;
        if (d == 0.0) {
            const auto& n1 = mesh.normals[tri.x];
            sdir = random_perpendicular(n1);
            tdir = cross(n1, sdir);
        } else {
            sdir = {t2 * x1 - t1 * x2, t2 * y1 - t1 * y2, t2 * z1 - t1 * z2};
            tdir = {s1 * x2 - s2 * x1, s1 * y2 - s2 * y1, s1 * z2 - s2 * z1};
            float r = 1.0f / d;
            sdir *= r;
            tdir *= r;
        }
        mesh.tan1[tri.x] += sdir; mesh.tan1[tri.y] += sdir; mesh.tan1[tri.z] += sdir;
        mesh.tan2[tri.x] += tdir; mesh.tan2[tri.y] += tdir; mesh.tan2[tri.z] += tdir;
    }
    for (size_t a = 0; a < count; ++a) {
        const auto& n = mesh.normals[a];
        const auto& t1 = mesh.tan1[a];
        const auto& t2 = mesh.tan2[a];
        m::float3 t = normalize(t1 - n * dot(n, t1));
        float w = (dot(cross(n, t1), t2) < 0.0f) ? -1.0f : 1.0f;
        m::float3 b = w < 0 ? cross(t, n) : cross(n, t);
        out[a] = m::packSnorm16(m::mat3f::packTangentFrame({t, b, n}).xyzw);
    }
}
void update_bounds(MeshGeometry& mesh) {
    mesh.low = mesh.high = mesh.positions.empty() ? m::float3{0, 0, 0} : mesh.positions[0];
    for (const auto& p : mesh.positions) { mesh.low = min(mesh.low, p); mesh.high = max(mesh.high, p); }
}
// Replaces a placeholder renderable's geometry with the mesh buffers.
void attach_geometry(ModelData& model) {
    auto& mesh = *model.shared->mesh;
    auto& rm = model.state->engine->getRenderableManager();
    const auto* entities = model.instance->getEntities();
    for (size_t i = 0; i < model.instance->getEntityCount(); ++i) {
        auto renderable = rm.getInstance(entities[i]);
        if (!renderable) continue;
        rm.setGeometryAt(renderable, 0, f::RenderableManager::PrimitiveType::TRIANGLES,
                         mesh.vertices, mesh.indices, 0, mesh.triangle_count * 3);
        rm.setAxisAlignedBoundingBox(renderable, f::Box().set(mesh.low, mesh.high));
        mesh.renderables.push_back(entities[i]);
        return;
    }
    throw AssetError("Mesh placeholder has no renderable");
}
