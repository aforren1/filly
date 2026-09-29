import numpy as np
import pytest

import filly
from test_features import pack

pytestmark = pytest.mark.gpu


def striped_glass(*, roughness=0.6, ior=1.5, volume=False, thickness=0.15):
    positions = []
    primitives = [[], [], []]

    def quad(left, right, bottom, top, z, material):
        start = len(positions)
        positions.extend([(left, bottom, z), (right, bottom, z), (right, top, z),
                          (left, bottom, z), (right, top, z), (left, top, z)])
        primitives[material].extend(range(start, start + 6))

    for index in range(80):
        quad(-8 + index * 0.2, -8 + (index + 1) * 0.2, -8, 8, -0.01, index % 2)
    quad(-8, 8, -8, 8, 0, 2)
    positions = np.asarray(positions, dtype="<f4")
    binary = positions.tobytes()
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}]
    accessors = [{"bufferView": 0, "componentType": 5126, "count": len(positions),
                  "type": "VEC3", "min": positions.min(axis=0).tolist(),
                  "max": positions.max(axis=0).tolist()}]
    meshes = []
    for material, indices in enumerate(primitives):
        data = np.asarray(indices, dtype="<u2").tobytes()
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary += data
        accessors.append({"bufferView": len(views) - 1, "componentType": 5123,
                          "count": len(indices), "type": "SCALAR"})
        meshes.append({"primitives": [{"attributes": {"POSITION": 0},
                                       "indices": len(accessors) - 1, "material": material}]})
    extensions = {"KHR_materials_transmission": {"transmissionFactor": 1},
                  "KHR_materials_ior": {"ior": ior},
                  "KHR_materials_specular": {"specularFactor": 0}}
    if volume:
        extensions["KHR_materials_volume"] = {"thicknessFactor": thickness}
    materials = [{"extensions": {"KHR_materials_unlit": {}},
                  "pbrMetallicRoughness": {"baseColorFactor": color}}
                 for color in ([1, 0, 0, 1], [0, 0, 1, 1])]
    materials.append({"name": "glass", "extensions": extensions,
                      "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": roughness}})
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 1, 2]}],
           "nodes": [{"mesh": i, "name": "glass" if i == 2 else f"stripe{i}"} for i in range(3)],
           "meshes": meshes, "materials": materials, "extensionsUsed": ["KHR_materials_unlit", *extensions],
           "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors}
    return pack(doc, binary)


def configure_camera(scene, projection):
    camera = scene.create_camera()
    camera.position = (0, 0, 3)
    camera.look_at((0, 0, 0))
    if projection == "ortho":
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=20)
    else:
        camera.set_perspective(fov_y=projection, aspect=1, near=0.1, far=20)
    scene.camera = camera
    return camera


@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
@pytest.mark.parametrize("projection", ["ortho", 45])
@pytest.mark.parametrize("volume", [False, True])
def test_ior_one_preserves_background(mode, projection, volume):
    with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
        scene = renderer.create_scene()
        scene.refraction = True
        scene.tone_mapping = "aces_legacy"
        configure_camera(scene, projection)
        model = scene.load(striped_glass(ior=1, volume=volume))
        target = renderer.create_render_target(width=128, height=128)
        renderer.render(scene, target)
        glass = target.read()
        model.node("glass").position = (0, 0, 4)
        renderer.render(scene, target)
        bare = target.read()
        # No Fresnel, absorption, or refraction at this interface, even when rough.
        np.testing.assert_allclose(glass[8:-8, 8:-8], bare[8:-8, 8:-8], atol=2)


def stripe_contrast(renderer, scene, target, **glass):
    model = scene.load(striped_glass(**glass))
    renderer.render(scene, target)
    pixels = target.read()[16:-16, 16:-16, :3].astype(float)
    model.close()
    return np.std(pixels[:, :, 0] - pixels[:, :, 2])


@pytest.mark.parametrize("projection", ["ortho", 45])
@pytest.mark.parametrize("volume", [False, True])
def test_roughness_progressively_filters_transmission(projection, volume):
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        scene.refraction = True
        scene.tone_mapping = "aces_legacy"
        configure_camera(scene, projection)
        target = renderer.create_render_target(width=128, height=128)
        contrasts = [stripe_contrast(renderer, scene, target, roughness=roughness, volume=volume)
                     for roughness in (0, 0.4, 0.8)]
        # Bent rays need bilinear interpolation even before roughness filtering.
        assert contrasts[0] > 150
        assert contrasts[0] > contrasts[1] > contrasts[2]
        assert contrasts[2] < contrasts[0] * 0.7


def stripe_std(image):
    pixels = np.asarray(image, dtype=float)[16:-16, 16:-16, :3]
    return np.std(pixels[:, :, 0] - pixels[:, :, 2])


@pytest.mark.parametrize("mode", ["compiled", "precompiled"])
def test_orthographic_filter_is_invariant_to_scene_units(mode):
    with filly.Renderer(precompiled_shaders=mode == "precompiled") as renderer:
        scene = renderer.create_scene()
        scene.refraction = True
        scene.tone_mapping = "aces_legacy"
        camera = configure_camera(scene, "ortho")
        model = scene.load(striped_glass(roughness=0.3))
        target = renderer.create_render_target(width=192, height=192)
        images = []
        # Uniform scale of scene and view, then a camera 30 times farther away.
        for scale, distance in ((1, 3), (0.05, 3), (10, 3), (1, 90)):
            model.scale = (scale,) * 3
            camera.position = (0, 0, distance * scale)
            camera.look_at((0, 0, 0))
            camera.set_orthographic(left=-scale, right=scale, bottom=-scale, top=scale,
                                    near=0.1 * scale, far=(distance + 20) * scale)
            renderer.render(scene, target)
            images.append(target.read())
        # The stripes must be blurred but still visible, so the comparison is not trivial.
        assert 20 < stripe_std(images[0]) < 150
        for image in images[1:]:
            np.testing.assert_allclose(image[8:-8, 8:-8], images[0][8:-8, 8:-8], atol=2)


def test_orthographic_filter_matches_45_degree_perspective(renderer):
    """Orthographic views use the SDK's own filter for a 45-degree vertical field of view."""
    scene = renderer.create_scene()
    scene.refraction = True
    scene.tone_mapping = "aces_legacy"
    camera = configure_camera(scene, "ortho")
    target = renderer.create_render_target(width=192, height=192)
    blurred = {}
    for roughness in (0, 0.3):
        model = scene.load(striped_glass(roughness=roughness))
        images = []
        for projection in ("ortho", 45):
            # At this distance a 45-degree view spans the same 2 units as the orthographic view.
            camera.position = (0, 0, 1 / np.tan(np.radians(22.5)))
            camera.look_at((0, 0, 0))
            if projection == "ortho":
                camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=20)
            else:
                camera.set_perspective(fov_y=45, aspect=1, near=0.1, far=20)
            renderer.render(scene, target)
            images.append(target.read().astype(float))
        model.close()
        blurred[roughness] = [stripe_std(image) for image in images]
        difference = np.abs(images[1] - images[0]).mean()
        # Stripe edges shift by a fraction of a pixel between the two projections.
        assert difference < 3
    assert blurred[0.3][0] < blurred[0][0] * 0.8
    np.testing.assert_allclose(blurred[0.3][0], blurred[0.3][1], rtol=0.02)


def test_zero_thickness_keeps_thin_sheet_filter(renderer):
    scene = renderer.create_scene()
    scene.refraction = True
    scene.tone_mapping = "aces_legacy"
    configure_camera(scene, "ortho")
    target = renderer.create_render_target(width=128, height=128)
    images = []
    for volume in (False, True):
        model = scene.load(striped_glass(volume=volume, thickness=0))
        renderer.render(scene, target)
        images.append(target.read())
        model.close()
    np.testing.assert_array_equal(*images)


@pytest.mark.parametrize("kind", ["point", "directional"])
def test_zero_transmission_preserves_shadows_and_reflections(renderer, scene, triangle_glb, kind):
    from test_features import unpack
    from test_lighting import shadow_asset, panorama

    scene.refraction = True
    scene.shadows = True
    scene.tone_mapping = "aces_legacy"
    scene.set_environment(panorama(), intensity=10000)
    if kind == "point":
        light = scene.add_point_light(position=(-0.7, 0, 2), intensity=100000)
    else:
        light = scene.add_directional_light(direction=(0.35, 0, -1), intensity=100000)
    light.casts_shadows = True
    light.set_shadow_options(map_size=512)
    target = renderer.create_render_target(width=128, height=128)
    asset = shadow_asset(triangle_glb)
    model = scene.load(asset)
    renderer.render(scene, target)
    expected = target.read()
    model.close()
    doc, binary = unpack(asset)
    doc["materials"][0]["extensions"] = {"KHR_materials_transmission": {"transmissionFactor": 0}}
    doc.setdefault("extensionsUsed", []).append("KHR_materials_transmission")
    scene.load(pack(doc, binary))
    renderer.render(scene, target)
    np.testing.assert_allclose(target.read(), expected, atol=2)
