"""Recognise an uploaded file's format from its bytes and store it by content hash."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# (magic test, mime, extension). HEIC/HEIF: an ISO-BMFF 'ftyp' box with a HEIF brand.
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    mime: str
    ext: str
    bytes: int
    path: Path


def sniff(data: bytes) -> tuple[str, str]:
    """(mime, extension) for the formats we accept; raises ValueError otherwise."""
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff", "tiff"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "image/heic", "heic"
    raise ValueError("unsupported file type (accepted: PDF, JPEG, PNG, TIFF, WebP, HEIC/HEIF)")


def store(data: bytes, storage_dir: Path) -> StoredFile:
    """Write the file once under storage_dir/files/<sha256>.<ext> (idempotent)."""
    mime, ext = sniff(data)
    digest = hashlib.sha256(data).hexdigest()
    path = storage_dir / "files" / f"{digest}.{ext}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return StoredFile(digest, mime, ext, len(data), path)
