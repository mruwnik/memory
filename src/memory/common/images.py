"""Opening untrusted image files with PIL."""

import pathlib
from typing import IO

from PIL import Image

# The raster formats the app ingests ("JPEG" also opens MPO camera files).
# PIL picks its plugin from the content, not the declared type or extension,
# and some plugins hand the data to another program (EPS runs Ghostscript on
# it), so every other format is refused at open.
RASTER_FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "BMP", "TIFF", "ICO", "AVIF")


def open_image(source: str | pathlib.Path | IO[bytes]) -> Image.Image:
    """Open an image of one of RASTER_FORMATS, raising
    PIL.UnidentifiedImageError (an OSError) for anything else."""
    return Image.open(source, formats=RASTER_FORMATS)
