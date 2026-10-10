"""Load uploaded images upright, and encode pages for the vision model within the tested size budget."""

from __future__ import annotations

import io
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()

# Decision log (2026-10-10): full-resolution JPEGs up to this size were accepted and read correctly in the spike
MODEL_IMAGE_MAX_BYTES = 170_000
PREPROCESS_VERSION = "img-1"  # bump when anything below changes how images reach the model


def load_pages(path: Path) -> list[Image.Image]:
    """All frames (multi-page TIFF) as upright RGB images; EXIF orientation applied."""
    pages = []
    with Image.open(path) as im:
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            pages.append(ImageOps.exif_transpose(im.copy()).convert("RGB"))
    return pages


def encode_for_model(image: Image.Image, source: bytes | None = None,
                     max_bytes: int = MODEL_IMAGE_MAX_BYTES) -> tuple[bytes, dict]:
    """JPEG bytes for the model: the original JPEG if it already fits, else the highest quality that fits,
    shrinking the image only if even quality 30 is too big."""
    if source is not None and source.startswith(b"\xff\xd8\xff") and len(source) <= max_bytes:
        return source, {"bytes": len(source), "px": list(image.size), "quality": "original"}
    scale = 1.0
    while scale > 0.15:
        w, h = round(image.width * scale), round(image.height * scale)
        resized = image if scale == 1.0 else image.resize((w, h), Image.LANCZOS)
        lo, hi, best = 30, 95, None
        while lo <= hi:
            q = (lo + hi) // 2
            buf = io.BytesIO()
            resized.save(buf, "JPEG", quality=q, optimize=True)
            if buf.tell() <= max_bytes:
                best, lo = (buf.getvalue(), q), q + 1
            else:
                hi = q - 1
        if best:
            return best[0], {"bytes": len(best[0]), "px": [w, h], "quality": best[1]}
        scale *= 0.8
    raise ValueError(f"cannot encode image under {max_bytes} bytes")
