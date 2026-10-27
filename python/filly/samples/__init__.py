"""Sample models installed with filly. See ATTRIBUTION.md in this folder for their sources.

Both are in the public domain (CC0 1.0), so images rendered from them need no attribution.

`SUZANNE` is Blender's monkey head in blue metal: one mesh, one material, no textures, 73 KB.

`HORSE` is Quaternius's low-poly horse: one vertex-colored mesh with a 1.5-second gallop clip
that animates morph targets, 178 KB.

Programs that must not import filly's native module can find Suzanne without importing filly:
it is `samples/suzanne.glb` next to `importlib.util.find_spec("filly").origin`.
"""

from importlib import resources
from pathlib import Path

__all__ = ["HORSE", "SUZANNE"]

SUZANNE = Path(__file__).with_name("suzanne.glb")
# The build installs the horse from the repository's samples/ folder, which the MATLAB and Octave
# packages share. In an editable install the file is in the build's install tree, not next to
# this module; importlib.resources finds it in both cases.
HORSE = Path(str(resources.files(__name__) / "horse.glb"))
