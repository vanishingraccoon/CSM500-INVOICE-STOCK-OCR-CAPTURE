"""Disk cache for recognition results.

Store results as JSON under the engine name. Keys include the image stem,
content hash, crop, page-segmentation mode and engine settings tag. Changed
image bytes produce a new key; previous cache files remain on disk.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import config
from models import OCRResult
from ocr.base import OCREngine


def _file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class CachedOCR:
    def __init__(self, engine: OCREngine, cache_dir: Path = config.OCR_CACHE):
        self.engine = engine
        self.dir = Path(cache_dir) / engine.name
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, image_path: Path, crop, psm) -> Path:
        crop_part = "full" if crop is None else "c" + "-".join(map(str, crop))
        tag = self.engine.cache_tag()
        # non-default engine settings get their own entry, or a changed setting would silently reuse old results
        tag_part = f"_{tag}" if tag else ""
        return self.dir / f"{Path(image_path).stem}_{_file_hash(image_path)}_{crop_part}_psm{psm}{tag_part}.json"

    def recognise(self, image_path: Path, crop: tuple[int, int, int, int] | None = None,
                  psm: int = 3, refresh: bool = False) -> OCRResult:
        key = self._key(Path(image_path), crop, psm)
        if key.exists() and not refresh:
            return OCRResult.from_dict(json.loads(key.read_text(encoding="utf-8")))
        result = self.engine.recognise(Path(image_path), crop=crop, psm=psm)
        key.write_text(json.dumps(result.to_dict()), encoding="utf-8")
        return result

    def cached(self, image_path: Path, crop=None, psm: int = 3) -> bool:
        return self._key(Path(image_path), crop, psm).exists()

    def cache_file(self, image_path: Path, crop=None, psm: int = 3) -> Path:
        """Where the result for this image is stored, so a record can point at it instead of copying it."""
        return self._key(Path(image_path), crop, psm)
