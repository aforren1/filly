"""A small 3D renderer for psychophysics stimuli, built on Google Filament."""

import sys
from importlib import metadata as _metadata

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
    ModelMemory,
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

from . import samples  # noqa: E402

__version__ = _metadata.version("filly")  # one source: pyproject.toml
__all__ = [
    "AnimationInfo", "AssetCompatibilityWarning", "AssetError", "BackendError", "Camera",
    "FillyError", "HostTexture", "ImportedTarget", "InteropError", "Light", "Material", "Model",
    "ModelMemory", "Node", "OffscreenTarget", "Preparation", "Renderer", "Scene", "Stats", "Texture", "current_gl_context",
    "samples", "set_log_level", "shapes",
]
