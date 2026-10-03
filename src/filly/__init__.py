"""A small 3D renderer for psychophysics stimuli, built on Google Filament."""

import sys

from ._native import (
    AnimationInfo,
    AssetCompatibilityWarning,
    AssetError,
    BackendError,
    Camera,
    FillyError,
    HostTexture,
    ImportedTarget,
    InteropError,
    Light,
    Material,
    Model,
    Node,
    OffscreenTarget,
    Preparation,
    Renderer,
    Scene,
    Stats,
    Texture,
    current_gl_context,
    set_log_level,
    shapes,
)

# The shape functions are native; this keeps `import filly.shapes` working.
sys.modules[__name__ + ".shapes"] = shapes

__version__ = "0.1.0.dev0"
__all__ = [
    "AnimationInfo", "AssetCompatibilityWarning", "AssetError", "BackendError", "Camera",
    "FillyError", "HostTexture", "ImportedTarget", "InteropError", "Light", "Material", "Model",
    "Node", "OffscreenTarget", "Preparation", "Renderer", "Scene", "Stats", "Texture", "current_gl_context",
    "set_log_level", "shapes",
]
