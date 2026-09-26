"""The one interface every recognition engine implements.

Wrappers return OCRResult so the parser can consume the same word records
regardless of which engine produced them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from models import OCRResult


class OCREngine(ABC):
    name: str = "base"

    @abstractmethod
    def version(self) -> str: ...

    def cache_tag(self) -> str:
        """Short tag naming settings that change the output; empty for the engine's default settings,
        so results cached before tags existed stay valid."""
        return ""

    @abstractmethod
    def recognise(self, image_path: Path, crop: tuple[int, int, int, int] | None = None,
                  psm: int = 3) -> OCRResult:
        """Word-level result for the image, or for crop=(left, top, right, bottom) of it.

        Word boxes are always in full-image coordinates, even when a crop was read.
        """
