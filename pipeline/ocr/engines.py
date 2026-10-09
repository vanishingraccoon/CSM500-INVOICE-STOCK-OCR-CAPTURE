"""Choose a recognition engine by name. PaddleOCR is the primary engine; Tesseract is kept
as the comparison engine."""
from __future__ import annotations

import os

from ocr.base import OCREngine
from ocr.cache import CachedOCR

DEFAULT_ENGINE = os.environ.get("OCR_ENGINE", "paddle")


def get_engine(name: str = DEFAULT_ENGINE) -> OCREngine:
    if name == "paddle":
        from ocr.paddle import PaddleEngine
        return PaddleEngine()
    if name == "tesseract":
        from ocr.tesseract import TesseractEngine
        return TesseractEngine()
    raise ValueError(f"unknown OCR engine: {name}")


def get_ocr(name: str = DEFAULT_ENGINE) -> CachedOCR:
    return CachedOCR(get_engine(name))
