"""Which archive entry a glTF material gets, and that the choice does not change its pixels."""

import struct
import zlib

import numpy as np
import pytest

import filly
from test_features import pack, unpack
from test_material_limits import textured_asset
from test_mesh import glb

pytestmark = pytest.mark.gpu

SPHERE = filly.shapes.uv_sphere(0.9, segments=96, rings=48)


def sphere_asset(extensions=None, metallic=0.3, roughness=0.35, base_color=(0.6, 0.5, 0.4, 1)):
    material = {"name": "surface", "pbrMetallicRoughness": {
        "baseColorFactor": list(base_color), "metallicFactor": metallic, "roughnessFactor": roughness}}
    if extensions:
        material["extensions"] = extensions
    doc, binary = unpack(glb(SPHERE, material))
    if extensions:
        doc["extensionsUsed"] = sorted(extensions)
    return pack(doc, binary)


def render_sphere(asset, size=96):
    """Sun, IBL, and a curved surface: every lobe contributes to some pixel."""
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(-0.5, -0.6, -1), intensity=80000)
        panorama = np.linspace(0.2, 1.5, 16, dtype=np.float32)[:, None, None] * np.ones((16, 32, 3), np.float32)
        panorama[3:7, 4:12] = (3, 2.5, 2)
        scene.set_environment(panorama, intensity=20000)
        model = scene.load(asset, strict=True)
        target = renderer.create_render_target(width=size, height=size)
        renderer.render(scene, target)
        return target.read().astype(int), model.material("surface")._shader


def test_iridescence_without_strength_matches_the_plain_material():
    """An iridescence-only material has no anisotropic lobe, which differs at zero strength."""
    # Measured with the anisotropic lobe: 89 of 9216 pixels differed by 1 in this scene.
    rough_metal = {"metallic": 1, "roughness": 0.7}
    plain, _ = render_sphere(sphere_asset(**rough_metal))
    film, _ = render_sphere(sphere_asset({"KHR_materials_iridescence": {"iridescenceFactor": 0}}, **rough_metal))
    assert plain[48, 48, :3].max() > 40
    np.testing.assert_array_equal(film, plain)


def test_specular_only_material_is_not_refractive():
    """Filament's own archive drew specular-only materials with its transmission entry."""
    extensions = {"KHR_materials_specular": {"specularFactor": 0.4, "specularColorFactor": [1, 0.8, 0.6]}}
    image, (name, refractive) = render_sphere(sphere_asset(extensions))
    assert not refractive, name
    assert image[48, 48, :3].max() > 40


def render_rim(asset, size=64):
    """A point light behind the sphere: specular highlights at grazing angles, where F90 matters."""
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_point_light(position=(1.6, 1.2, -0.6), intensity=400000, range=20)
        scene.load(asset, strict=True)
        target = renderer.create_render_target(width=size, height=size)
        renderer.render(scene, target)
        return target.read().astype(int)


# Filament derives F90 from F0 unless a material has KHR_materials_specular inputs. The derived
# value is below 1 when F0 is small: dark metals, and dielectrics at low IOR.
LOW_F0 = {
    "dark metal": ({}, {"metallic": 1, "roughness": 0.3, "base_color": (0.01, 0.01, 0.012, 1)}),
    "low ior": ({"KHR_materials_ior": {"ior": 1.1}}, {"metallic": 0, "roughness": 0.3}),
}


@pytest.mark.parametrize("surface", sorted(LOW_F0))
@pytest.mark.parametrize("lobe", ["KHR_materials_clearcoat", "KHR_materials_iridescence"])
def test_lobes_without_specular_extension_keep_derived_f90(surface, lobe):
    """Clearcoat or iridescence at zero strength must not change the Fresnel term of the base."""
    # Measured on the archive path when its lobe entries had specular inputs: up to 53 (dark
    # metal) and 115 (IOR 1.1) of 255 at the rim.
    extensions, factors = LOW_F0[surface]
    zero = {"KHR_materials_clearcoat": {"clearcoatFactor": 0}, "KHR_materials_iridescence": {"iridescenceFactor": 0}}
    plain = render_rim(sphere_asset(extensions or None, **factors))
    lobed = render_rim(sphere_asset({**extensions, lobe: zero[lobe]}, **factors))
    assert plain[..., :3].max() > 40
    np.testing.assert_allclose(lobed, plain, atol=1)


@pytest.mark.parametrize("lobe", [None, "KHR_materials_clearcoat"])
def test_specular_extension_uses_its_f90(lobe):
    """With KHR_materials_specular, F90 is the extension's specularFactor on every material."""
    _, factors = LOW_F0["dark metal"]
    plain = render_rim(sphere_asset(**factors))
    extensions = {"KHR_materials_specular": {"specularFactor": 1}}
    if lobe:
        extensions[lobe] = {"clearcoatFactor": 0}
    specular = render_rim(sphere_asset(extensions, **factors))
    assert np.abs(specular - plain).max() > 20
    alone = render_rim(sphere_asset({"KHR_materials_specular": {"specularFactor": 1}}, **factors))
    np.testing.assert_allclose(specular, alone, atol=1)


def emissive_triangle(extensions):
    """A black triangle whose glTF emission is its only light."""
    doc, binary = unpack(textured_asset(0, extensions=extensions))
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [0, 0, 0, 1]
    doc["materials"][0]["emissiveFactor"] = [0.1, 0.25, 0.05]
    doc["materials"][0]["extensions"]["KHR_materials_emissive_strength"] = {"emissiveStrength": 2}
    doc["extensionsUsed"] = sorted(doc["materials"][0]["extensions"])
    return pack(doc, binary)


def render_center(asset):
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        scene.add_directional_light(direction=(0, 0, -1), intensity=80000)
        scene.load(asset, strict=True)
        target = renderer.create_render_target(width=16, height=16)
        renderer.render(scene, target)
        return target.read()[8, 8].astype(int)


def test_diffuse_transmission_emission_matches_other_lit_materials():
    """The emission of a diffuse-transmission material is not scaled by the camera exposure."""
    # Before the fix: (92, 92, 92), the specular reflection of the light without the emission.
    plain = render_center(emissive_triangle({}))
    diffuse = render_center(emissive_triangle({"KHR_materials_diffuse_transmission": {"diffuseTransmissionFactor": 0.5}}))
    assert plain[1] > plain[0] + 40 and plain[1] > plain[2] + 40
    np.testing.assert_allclose(diffuse, plain, atol=1)


def emissive_only(factor, strength=None):
    """A black lit triangle whose only light output is its glTF emission."""
    doc, binary = unpack(textured_asset(0, extensions={}))
    material = doc["materials"][0]
    material["pbrMetallicRoughness"]["baseColorFactor"] = [0, 0, 0, 1]
    material["emissiveFactor"] = list(factor)
    material["extensions"] = {}
    if strength is not None:
        material["extensions"]["KHR_materials_emissive_strength"] = {"emissiveStrength": strength}
    doc["extensionsUsed"] = sorted(material["extensions"])
    return pack(doc, binary)


def render_emission(asset, clone=False):
    with filly.Renderer() as renderer:
        scene = renderer.create_scene()
        camera = scene.create_camera()
        camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
        camera.position = (0, 0, 3)
        camera.look_at((0, 0, 0))
        scene.camera = camera
        model = scene.load(asset, strict=True, clonable=clone)
        if clone:
            model.visible = False
            model.clone()
        target = renderer.create_render_target(width=16, height=16)
        renderer.render(scene, target)
        return target.read()[8, 8].astype(int)


@pytest.mark.parametrize("clone", [False, True])
def test_emissive_strength_applies_once(clone):
    """KHR_materials_emissive_strength scales emissiveFactor once, as the extension specifies.

    gltfio 1.77.1 applies it twice (factor 0.05 with strength 4 rendered like 0.8); gltf_viewer
    shows the same squared result, so this is an intended divergence from it.
    """
    scaled = render_emission(emissive_only((0.05, 0.1, 0.02), strength=4), clone=clone)
    plain = render_emission(emissive_only((0.2, 0.4, 0.08)), clone=clone)
    np.testing.assert_allclose(scaled, plain, atol=1)
    assert scaled[1] > scaled[2] + 20


def test_material_emissive_is_factor_times_strength(renderer, scene):
    """`Material.emissive` reads and writes the effective emission, with the strength included."""
    model = scene.load(emissive_only((0.05, 0.1, 0.02), strength=4), strict=True)
    material = model.material("many")
    assert material.emissive == pytest.approx((0.2, 0.4, 0.08))
    material.emissive = (0.3, 0.1, 0.0)
    assert material.emissive == pytest.approx((0.3, 0.1, 0.0))


def solid_png(rgba):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, *rgba]))) + chunk(b"IEND", b""))


def specular_glossiness(texel=None, specular=(1, 1, 1), glossiness=1.0):
    """A triangle with KHR_materials_pbrSpecularGlossiness and a 1 x 1 specular-glossiness texture."""
    extension = {"KHR_materials_pbrSpecularGlossiness": {
        "diffuseFactor": [0.5, 0.5, 0.5, 1], "specularFactor": list(specular), "glossinessFactor": glossiness}}
    if texel is None:
        return textured_asset(0, extensions=extension)
    doc, binary = unpack(textured_asset(
        1, extensions=extension, slots=[("KHR_materials_pbrSpecularGlossiness", "specularGlossinessTexture")]))
    image = solid_png(texel)
    doc["bufferViews"][3].update(byteOffset=len(binary), byteLength=len(image))
    binary += image
    return pack(doc, binary)


@pytest.mark.parametrize("texel", [(0, 0, 0, 0), (128, 128, 128, 0), (64, 128, 200, 128)])
def test_specular_glossiness_texture_is_sampled(texel):
    """The texture scales the factors: sRGB specular color in RGB, linear glossiness in A."""
    def linear(value):
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    textured = render_center(specular_glossiness(texel))
    factors = render_center(specular_glossiness(specular=[linear(c) for c in texel[:3]], glossiness=texel[3] / 255))
    untextured = render_center(specular_glossiness())
    np.testing.assert_allclose(textured, factors, atol=1)
    assert np.abs(textured - untextured).max() > 20
