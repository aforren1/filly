// Included in detail after the resource definitions. Tracks own all keyframes.

// A node-local copy follows the animated properties of the material it was copied from, so a
// recolored node keeps animating. So does the textured instance that replaces a shared glTF
// instance. Models have few of either; the scans allocate nothing.
template <class Write> void write_material(const ModelData& model, f::MaterialInstance* material, Write write) {
    write(material);
    for (const auto& entry : model.overrides) if (entry.source == material) write(entry.current);
    for (const auto& local : model.locals) if (local.source == material) write(local.copy);
}
void apply_projection(f::Engine& engine, const CameraProjection& projection) {
    auto* camera = engine.getCameraComponent(projection.entity);
    const auto& p = projection.value;
    camera->setScaling({1,1});
    if (projection.orthographic)
        camera->setProjection(f::Camera::Projection::ORTHO, -p[0], p[0], -p[1], p[1], p[2], p[3]);
    else camera->setProjection(double(p[0])*180.0/3.141592653589793, p[1] > 0 ? double(p[1]) : projection.aspect,
                               p[2], p[3], f::Camera::Fov::VERTICAL);
}

void commit_properties(ModelData& model) {
    for (const auto& cone : model.light_cones)
        if (!(cone.value[0] >= 0 && cone.value[0] < cone.value[1] && cone.value[1] <= 1.5707964f))
            throw AssetError("Animated spot light cones require 0 <= inner < outer <= pi/2");
    for (const auto& projection : model.cameras) {
        const auto& p = projection.value;
        // A perspective camera without an authored aspect ratio follows the render target.
        const bool fitted = !projection.orthographic && projection.rest[1] == 0 && p[1] == 0;
        if (p[0] <= 0 || (p[1] <= 0 && !fitted) || p[2] < 0 || p[3] <= p[2] ||
            (!projection.orthographic && (p[0] >= 3.141592653589793 || p[2] <= 0)))
            throw AssetError("Animated camera projection is invalid");
    }
    update_visibility(model);
    for (const auto& projection : model.cameras) apply_projection(*model.state->engine, projection);
    for (const auto& uv : model.texture_transforms) {
        const auto& v = uv.value;
        const float c = std::cos(v[4]), s = std::sin(v[4]);
        m::mat3f matrix{m::float3{c*v[2], s*v[2], 0}, m::float3{-s*v[3], c*v[3], 0}, m::float3{v[0], v[1], 1}};
        if (uv.transpose) matrix = transpose(matrix);
        write_material(model, uv.material, [&](f::MaterialInstance* material) {
            material->setParameter(uv.parameter.c_str(), matrix);
        });
    }
    auto& lights = model.state->engine->getLightManager();
    for (const auto& cone : model.light_cones) {
        auto instance = lights.getInstance(cone.entity);
        if (!instance) continue;
        const auto intensity = lights.getIntensity(instance);
        lights.setSpotLightCone(instance, cone.value[0], cone.value[1]);
        // glTF keeps candela constant when the cone changes, not total luminous flux.
        lights.setIntensityCandela(instance, intensity);
    }
}

void initialize_cameras(ModelData& model, const std::vector<CameraSource>& sources) {
    for (const auto entity : model.camera_entities) {
        const auto* node = node_source(model, entity);
        const auto index = node ? node->camera : SIZE_MAX;
        if (index == SIZE_MAX) continue;
        if (index >= sources.size()) throw AssetError("Invalid imported camera index");
        const auto& source = sources[index];
        model.cameras.push_back({index, entity, source.orthographic, source.projection, source.projection});
    }
    commit_properties(model);
}

void write_property(ModelData& model, const PropertyTrackSource& track,
                    const PropertyTarget& target, const Vec4& value) {
    if (track.kind == 5) {
        model.visibility[target.compound].value = value[0] != 0;
    } else if (track.kind == 4) {
        model.light_cones[target.compound].value[target.field] = value[0];
    } else if (track.kind == 2) {
        model.cameras[target.compound].value[target.field] = value[0];
    } else if (track.kind == 3) {
        auto& uv = model.texture_transforms[target.compound];
        if (target.field == 0) { uv.value[0]=value[0]; uv.value[1]=value[1]; }
        else if (target.field == 2) { uv.value[2]=value[0]; uv.value[3]=value[1]; }
        else uv.value[4]=value[0];
    } else if (target.material) {
        const char* name = track.parameter.c_str();
        write_material(model, target.material, [&](f::MaterialInstance* material) {
            if (track.components == 1) material->setParameter(name, value[0]);
            else if (track.components == 3) material->setParameter(name, m::float3{value[0], value[1], value[2]});
            else material->setParameter(name, m::float4{value[0], value[1], value[2], value[3]});
        });
    } else {
        auto& manager = model.state->engine->getLightManager();
        auto instance = manager.getInstance(target.light);
        // Closing an imported light must not let a later animation recreate it.
        if (!instance) return;
        if (track.parameter == "color") manager.setColor(instance, {value[0], value[1], value[2]});
        else if (track.parameter == "range") manager.setFalloff(instance, value[0]);
        else {
            manager.setIntensityCandela(instance, value[0]);
            // Match gltfio's finite-range approximation as animated intensity changes.
            // Keeping the load-time range would hide a light authored with zero intensity.
            if (target.automatic_range)
                manager.setFalloff(instance, float(std::max(0.001, std::pow(double(value[0]) / 0.05, 0.25))));
        }
    }
}

void initialize_animations(ModelData& model, const std::vector<AnimationSource>& sources,
                           const std::vector<MaterialBinding>& materials) {
    auto& lights = model.state->engine->getLightManager();
    for (const auto& source : sources) {
        AnimationClip clip{source.name, source.native_index};
        if (clip.native_index >= 0) {
            if (size_t(clip.native_index) >= model.animator->getAnimationCount())
                throw AssetError("glTF animation index was not retained by the native loader");
            clip.duration = model.animator->getAnimationDuration(clip.native_index);
        }
        for (const auto& curve : source.tracks) {
            PropertyTrack track{curve};
            if (curve.times.empty() || curve.components < 1 || curve.components > 4 ||
                    curve.values.size() != curve.times.size() * curve.components * (curve.interpolation == 2 ? 3 : 1))
                throw AssetError("Invalid property animation curve");
            clip.duration = std::max(clip.duration, curve.times.back());
            if (curve.kind == 0 || curve.kind == 3) {
                for (const auto& [index, material] : materials) {
                    if (index != curve.index) continue;
                    const auto parameter = curve.parameter.substr(0, curve.parameter.find('/'));
                    if (!material || !material->getMaterial()->hasParameter(parameter.c_str()))
                        throw AssetError("Animation parameter unavailable for this material: " + curve.parameter);
                    PropertyTarget target;
                    target.material = material;
                    if (curve.kind == 3) {
                        if (curve.setup.size() != 6) throw AssetError("Invalid texture animation setup");
                        size_t i=0;
                        for (; i<model.texture_transforms.size(); ++i)
                            if (model.texture_transforms[i].material == material && model.texture_transforms[i].parameter == parameter) break;
                        if (i == model.texture_transforms.size()) {
                            std::array<float,5> values;
                            std::copy_n(curve.setup.begin(), 5, values.begin());
                            model.texture_transforms.push_back({material, parameter, values, values, curve.setup[5] != 0});
                        }
                        target.compound = i;
                        const auto field = curve.parameter.substr(curve.parameter.find('/')+1);
                        target.field = field == "offset" ? 0 : field == "scale" ? 2 : 4;
                        track.targets.push_back(target);
                        continue;
                    }
                    const auto* name = curve.parameter.c_str();
                    if (curve.components == 1) target.rest[0] = material->getParameter<float>(name);
                    else if (curve.components == 3) {
                        auto value = material->getParameter<m::float3>(name);
                        target.rest = {value.x, value.y, value.z, 0};
                    } else {
                        auto value = material->getParameter<m::float4>(name);
                        target.rest = {value.x, value.y, value.z, value.w};
                    }
                    track.targets.push_back(target);
                }
            } else if (curve.kind == 5) {
                for (size_t i=0; i<model.visibility.size(); ++i) {
                    if (model.visibility[i].index != curve.index) continue;
                    PropertyTarget target;
                    target.compound = i;
                    target.rest[0] = model.visibility[i].rest;
                    track.targets.push_back(target);
                }
            } else if (curve.kind == 2) {
                for (size_t i=0; i<model.cameras.size(); ++i) {
                    if (model.cameras[i].index != curve.index) continue;
                    PropertyTarget target;
                    target.compound = i;
                    target.field = std::stoi(curve.parameter);
                    track.targets.push_back(target);
                }
            } else {
                for (const auto entity : model.lights) {
                    const auto* node = node_source(model, entity);
                    if (!node || node->light != curve.index) continue;
                    PropertyTarget target;
                    target.light = entity;
                    target.automatic_range = node->automatic_range;
                    auto instance = lights.getInstance(entity);
                    if (curve.kind == 4) {
                        size_t index = 0;
                        for (; index < model.light_cones.size(); ++index)
                            if (model.light_cones[index].entity == entity) break;
                        if (index == model.light_cones.size()) {
                            if (curve.setup.size() != 2) throw AssetError("Invalid spotlight animation setup");
                            std::array<float,2> values{curve.setup[0], curve.setup[1]};
                            model.light_cones.push_back({entity, values, values});
                        }
                        target.compound = index;
                        target.field = curve.parameter == "spot/innerConeAngle" ? 0 : 1;
                    } else
                    if (curve.parameter == "color") {
                        auto value = lights.getColor(instance);
                        target.rest = {value.x, value.y, value.z, 0};
                    } else target.rest[0] = curve.parameter == "range" ? lights.getFalloff(instance) : lights.getIntensity(instance);
                    track.targets.push_back(target);
                }
            }
            // Unreferenced materials and lights have no live target in this asset's scene.
            clip.tracks.push_back(std::move(track));
        }
        model.animations.push_back(std::move(clip));
    }
}

Vec4 sample_property(const PropertyTrackSource& curve, float time) {
    const auto& times = curve.times;
    const size_t n = curve.components, stride = n * (curve.interpolation == 2 ? 3 : 1);
    const size_t value_offset = curve.interpolation == 2 ? n : 0;
    const auto upper = std::upper_bound(times.begin(), times.end(), time);
    size_t left = upper == times.begin() ? 0 : size_t(upper - times.begin() - 1);
    size_t right = std::min(left + 1, times.size() - 1);
    Vec4 result{};
    const double dt = double(times[right]) - times[left];
    const double t = dt > 0 ? std::clamp((double(time)-times[left])/dt, 0.0, 1.0) : 0;
    for (size_t c = 0; c < n; ++c) {
        double a = curve.values[left*stride + value_offset + c];
        double b = curve.values[right*stride + value_offset + c];
        double value = a;
        if (curve.interpolation == 1) value = a + (b-a)*t;
        else if (curve.interpolation == 2 && left != right) {
            const double out = curve.values[left*stride + 2*n + c];
            const double in = curve.values[right*stride + c];
            value = (2*t*t*t-3*t*t+1)*a + (t*t*t-2*t*t+t)*dt*out
                    + (-2*t*t*t+3*t*t)*b + (t*t*t-t*t)*dt*in;
        }
        const double limit = std::numeric_limits<float>::max();
        result[c] = float(std::clamp(value, std::max(-limit, double(curve.minimum)),
                                          std::min(limit, double(curve.maximum))));
    }
    return result;
}

void reset_properties(ModelData& model) {
    for (auto& node : model.visibility) node.value = node.rest;
    for (auto& cone : model.light_cones) cone.value = cone.rest;
    for (auto& camera : model.cameras) camera.value = camera.rest;
    for (auto& uv : model.texture_transforms) uv.value = uv.rest;
    for (const auto& clip : model.animations)
        for (const auto& track : clip.tracks)
            if (track.source.kind < 2)
                for (const auto& target : track.targets) write_property(model, track.source, target, target.rest);
}

void apply_properties(ModelData& model, const AnimationClip& clip, float time) {
    for (const auto& track : clip.tracks) {
        const auto value = sample_property(track.source, time);
        for (const auto& target : track.targets) write_property(model, track.source, target, value);
    }
    commit_properties(model);
}
