# Texture fixtures

The fixtures are generated images. No external artwork is used.

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
