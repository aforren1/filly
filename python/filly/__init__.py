"""A small 3D renderer for psychophysics stimuli, built on Google Filament."""

import sys
from importlib import metadata as _metadata

from ._native import (
    AnimationInfo,
    Asset,
    AssetCompatibilityWarning,
    AssetError,
    AssetMemory,
    BackendError,
    Camera,
    Environment,
    FillyError,
    ImportedTarget,
    ImportedTexture,
    InteropError,
    Light,
    Material,
    Model,
    Node,
    PickQuery,
    PickResult,
    Preparation,
    Readback,
    Renderer,
    RenderTarget,
    Scene,
    Stats,
    Texture,
    current_gl_context,
    set_log_level,
    set_material_parameters,
    set_morph_weights,
    set_transforms,
    shapes,
)

# The shape functions are native; this keeps `import filly.shapes` working.
sys.modules[__name__ + ".shapes"] = shapes

from . import samples  # noqa: E402

__version__ = _metadata.version("filly")  # one source: pyproject.toml
__all__ = [
    "AnimationInfo", "Asset", "AssetCompatibilityWarning", "AssetError", "AssetMemory", "BackendError",
    "Camera", "Environment", "FillyError", "ImportedTarget", "ImportedTexture", "InteropError", "Light",
    "Material", "Model", "Node", "PickQuery", "PickResult", "Preparation", "Readback", "Renderer", "RenderTarget", "Scene", "Stats", "Texture",
    "current_gl_context", "samples", "set_log_level", "set_material_parameters", "set_morph_weights",
    "set_transforms", "shapes",
]
