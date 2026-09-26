"""Tesseract through its command line, reading TSV output for word boxes.

The CLI is called directly rather than through pytesseract: one fewer dependency, and
the exact invocation is visible for the methodology write-up.
"""
from __future__ import annotations

import csv
import io
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

import config
from models import OCRResult, Word
from ocr.base import OCREngine


class TesseractEngine(OCREngine):
    name = "tesseract"

    def __init__(self, exe: str = config.TESSERACT, lang: str = "eng"):
        self.exe = exe
        self.lang = lang
        self._version: str | None = None

    def version(self) -> str:
        # asked once per engine; an lru_cache on the method would keep every engine alive for ever
        if self._version is None:
            out = subprocess.run([self.exe, "--version"], capture_output=True, text=True, check=False).stdout
            self._version = out.splitlines()[0].strip() if out else "unknown"
        return self._version

    def recognise(self, image_path: Path, crop: tuple[int, int, int, int] | None = None,
                  psm: int = 3) -> OCRResult:
        image_path = Path(image_path)
        with Image.open(image_path) as im:
            width, height = im.size
            dx, dy = 0, 0
            with tempfile.TemporaryDirectory() as tmp:
                src = image_path
                if crop:
                    dx, dy = crop[0], crop[1]
                    src = Path(tmp) / "crop.png"
                    im.crop(crop).save(src)
                t0 = time.perf_counter()
                proc = subprocess.run(
                    [self.exe, str(src), "stdout", "-l", self.lang, "--psm", str(psm), "tsv"],
                    capture_output=True, timeout=180, check=False,     # the exit code is checked below
                )
                seconds = time.perf_counter() - t0
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract failed on {image_path}: {proc.stderr.decode(errors='replace')}")
        words = parse_tsv(proc.stdout.decode("utf-8", errors="replace"), dx, dy)
        return OCRResult(image_path=str(image_path), engine=self.name, engine_version=self.version(),
                         seconds=round(seconds, 3), width=width, height=height, words=words)


def parse_tsv(tsv: str, dx: int = 0, dy: int = 0) -> list[Word]:
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        words.append(Word(
            text=text,
            left=int(row["left"]) + dx, top=int(row["top"]) + dy,
            width=int(row["width"]), height=int(row["height"]),
            conf=float(row["conf"]),
            # encode block, paragraph and line numbers into the grouping ID
            block=int(row["block_num"]),
            line=int(row["block_num"]) * 1000 + int(row["par_num"]) * 100 + int(row["line_num"]),
        ))
    return words
