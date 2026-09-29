"""A small 3D renderer for psychophysics stimuli, built on Google Filament."""

from ._native import (
    AnimationInfo,
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
    Renderer,
    Scene,
    Stats,
    Texture,
    current_gl_context,
    set_log_level,
)
from ._assets import AssetCompatibilityWarning
from . import shapes

__version__ = "0.1.0.dev0"
__all__ = [
    "AnimationInfo", "AssetCompatibilityWarning", "AssetError", "BackendError", "Camera",
    "FillyError", "HostTexture", "ImportedTarget", "InteropError", "Light", "Material", "Model",
    "Node", "OffscreenTarget", "Renderer", "Scene", "Stats", "Texture", "current_gl_context",
    "set_log_level", "shapes",
]
