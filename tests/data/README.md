# Test fixtures

## Textures

The texture fixtures are generated images.

`green.ktx2` contains a 4 by 4 solid green image. It was encoded with the Filament 1.77.1 SDK's
Basis Universal 2.10 encoder:

```powershell
.deps/bin/basisu.exe -ktx2 -uastc -file green.png -output_file tests/data/green.ktx2
```

`green.webp` contains a 4 by 4 solid green image. `quadrants.webp` contains a 2 by 2 image: red
and green in the top row, blue and white in the bottom row. Both are lossless WebP files, written
with Pillow 11.3 (libwebp 1.5.0):

```python
from PIL import Image
Image.new("RGBA", (4, 4), (0, 255, 0, 255)).save("green.webp", format="WEBP", lossless=True)
image = Image.new("RGBA", (2, 2))
image.putdata([(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 255, 255)])
image.save("quadrants.webp", format="WEBP", lossless=True, exact=True)
```

## Box (`box/`, `draco-box/`)

`tests/test_draco.py` and the browser test `test_draco_matches_desktop` compare a
Draco-compressed (`KHR_draco_mesh_compression`) box with the same box without compression.

- Title: Box
- Author: Cesium, 2017
- Source: Khronos glTF Sample Assets, `Models/Box/glTF` and `Models/Box/glTF-Draco`
  (<https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/Box>)
- License: [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/legalcode)
- Changes: none. The files are copied without changes, with their original names.
