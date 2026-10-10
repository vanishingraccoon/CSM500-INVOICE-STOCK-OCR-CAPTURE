"""Receiving a photograph safely: nothing is acknowledged until it is on disk, whole, and recorded.

The order is the contract's (docs/phone-api.md section 2): stream the body to a temporary file while hashing
it; refuse it if the hash differs from the one the phone sent; check it is a single JPEG or PNG within the
limits; flush it to disk; rename it into place. The rename is atomic, so a crash leaves either no photograph
or the whole photograph, never half of one. A temporary file left by a crash is removed at the next start.
Only after the database row is committed does the phone get its receipt and delete its own copy.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_BYTES = 25 * 1024 * 1024
MAX_SIDE = 12_000
CHUNK = 1 << 20
FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png"}    # Pillow names some camera JPEGs MPO
MIME = {"jpg": "image/jpeg", "png": "image/png"}


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def unsupported(message: str = "The photograph must be one JPEG or PNG image, under 25 MB and 12,000 pixels a side.") -> UploadError:
    return UploadError(415, "unsupported_image", message)


@dataclass
class Received:
    temp: Path
    sha256: str
    bytes: int
    extension: str = "jpg"
    width: int = 0
    height: int = 0

    @property
    def content_type(self) -> str:
        return MIME[self.extension]


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def receive(stream: BinaryIO, tmp_dir: Path, expected_sha256: str, content_length: int | None = None) -> Received:
    """Stream, hash, check. Returns the temporary file; the caller renames it into place or discards it."""
    if content_length is not None and content_length > MAX_BYTES:
        drain(stream)
        raise unsupported("The photograph is larger than 25 MB.")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="upload_", suffix=".part", dir=tmp_dir)
    temp = Path(name)
    digest, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    drain(stream)
                    raise unsupported("The photograph is larger than 25 MB.")
                digest.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        if digest.hexdigest() != expected_sha256.lower():
            raise UploadError(422, "hash_mismatch", "The photograph arrived different from what the phone sent."
                                                     " Keep it and send it again.")
        extension, width, height = validate_image(temp)
    except BaseException:
        _remove(temp)
        raise
    return Received(temp, digest.hexdigest(), size, extension, width, height)


def validate_image(path: Path) -> tuple[str, int, int]:
    from PIL import Image
    try:
        with Image.open(path) as im:
            extension = FORMATS.get(im.format or "")
            if extension is None:
                raise unsupported()
            if getattr(im, "n_frames", 1) != 1:
                raise unsupported("The photograph holds more than one frame.")
            width, height = im.size
            if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
                raise unsupported("The photograph is larger than 12,000 pixels a side.")
            im.verify()
    except UploadError:
        raise
    except Exception as e:     # not an image, truncated, or a decompression bomb: all refused the same way
        raise unsupported() from e
    return extension, width, height


def store(received: Received, final: Path) -> Path:
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(received.temp, final)          # atomic on one volume; tmp/ sits beside photos/ for that reason
    return final


def discard(received: Received) -> None:
    _remove(received.temp)


def drain(stream: BinaryIO, limit: int = MAX_BYTES * 2) -> None:
    """Read and drop what is left of a request body, so a refused upload does not break the connection."""
    read = 0
    while read < limit:
        chunk = stream.read(CHUNK)
        if not chunk:
            return
        read += len(chunk)


def downscaled(source: Path, width: int, cache_dir: Path, name: str) -> Path:
    """A copy of the photograph at most width pixels wide, made once and kept."""
    from PIL import Image, ImageOps
    out = cache_dir / f"{name}_w{width}.jpg"
    if out.exists():
        return out
    with Image.open(source) as im:
        if im.width <= width:
            return source
        im = ImageOps.exif_transpose(im)
        small = im.convert("RGB").resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, name_tmp = tempfile.mkstemp(prefix="thumb_", suffix=".part", dir=cache_dir)
    with os.fdopen(fd, "wb") as f:
        small.save(f, "JPEG", quality=85)
    os.replace(name_tmp, out)
    return out


def remove_temp_files(*folders: Path) -> int:
    """Startup: nothing is being uploaded yet, so every temporary file is an orphan of a crash."""
    removed = 0
    for folder in folders:
        if folder.exists():
            for f in folder.rglob("*.part"):
                _remove(f)
                removed += 1
    return removed
