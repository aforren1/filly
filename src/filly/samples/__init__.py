"""Sample models installed with filly. See ATTRIBUTION.md in this folder for their sources.

`SUZANNE` is Blender's monkey head in blue metal: one mesh, one material, no textures, 73 KB. It
is in the public domain (CC0 1.0), so images rendered from it need no attribution.

Programs that must not import filly's native module can find the file without importing filly:
it is `samples/suzanne.glb` next to `importlib.util.find_spec("filly").origin`.
"""

from pathlib import Path

__all__ = ["SUZANNE"]

SUZANNE = Path(__file__).with_name("suzanne.glb")
