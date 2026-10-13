"""Loads thumbnails and prepares photos for sending (the PC counterpart of ImageEncoder.kt)."""

import io
import os
from enum import Enum
from typing import NamedTuple, Optional

from PIL import Image, ImageOps

from .protocol import MAX_FILE_BYTES

try:  # HEIC/HEIF support is optional (pip install pillow-heif)
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff")


class Quality(Enum):
    ORIGINAL = 0
    LARGE = 1
    MEDIUM = 2
    SMALL = 3


QUALITIES = [
    (Quality.ORIGINAL, "Original file (best quality)", 0),
    (Quality.LARGE, "Large – 2048 px", 2048),
    (Quality.MEDIUM, "Medium – 1280 px", 1280),
    (Quality.SMALL, "Small – 640 px (fastest)", 640),
]


class Encoded(NamedTuple):
    data: bytes
    name: str


def is_photo(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in EXTENSIONS


def load(data: bytes, max_dimension: int) -> Image.Image:
    """Decodes an image no larger than max_dimension px, upright per its EXIF orientation."""
    image = Image.open(io.BytesIO(data))
    if max_dimension > 0 and image.format == "JPEG":
        image.draft("RGB", (max_dimension, max_dimension))
    image = ImageOps.exif_transpose(image)
    if max_dimension > 0 and max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
    else:
        image.load()
    return image


def try_load(data: bytes, max_dimension: int) -> Optional[Image.Image]:
    try:
        return load(data, max_dimension)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def try_load_thumbnail(path: str, size: int) -> Optional[Image.Image]:
    """Small thumbnail for the picker grid, or None if the file cannot be decoded."""
    try:
        with open(path, "rb") as f:
            return load(f.read(), size)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def encode(path: str, quality: Quality, max_bytes: int = MAX_FILE_BYTES) -> Encoded:
    """Reads or re-encodes a photo. Runs on any thread."""
    name = os.path.basename(path)
    max_dim = next(d for q, _, d in QUALITIES if q == quality)
    with open(path, "rb") as f:
        raw = f.read()
    if max_dim == 0:
        if not raw:
            raise IOError("The file is empty")
        if len(raw) > max_bytes:
            raise IOError(f"Photo is {len(raw) // 1024} KB; the maximum is {max_bytes // 1024} KB")
        return Encoded(raw, name)

    try:
        image = load(raw, max_dim)
    except (ValueError, SyntaxError, Image.DecompressionBombError) as e:
        raise IOError(f"Cannot read image: {e}") from e
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    elif image.mode != "RGB":
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    stem = os.path.splitext(name)[0]
    return Encoded(out.getvalue(), f"{stem}_ls{max_dim}.jpg")


def cover(image: Image.Image, size: int) -> Image.Image:
    """Scales and centre-crops to a square (like WPF's UniformToFill)."""
    return ImageOps.fit(image, (size, size), Image.BILINEAR)
