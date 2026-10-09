"""PaddleOCR (PP-OCR) wrapper returning the same OCRResult as every other engine.

PaddleOCR recognises text *lines*. The parser needs words, so word boxes are taken
from PaddleOCR's own word-level output where it provides it, and otherwise the line
box is divided between its words in proportion to their character counts.

Document unwarping and orientation correction are switched off on purpose: both
transform the image, and word boxes must stay in the original page's coordinates.
"""
from __future__ import annotations

import hashlib
import json
import time
from functools import cached_property
from pathlib import Path

import numpy as np
from PIL import Image

from models import OCRResult, Word
from ocr.base import OCREngine

# Measured on page 1 of the August binder (24-thread CPU):
#   PP-OCRv6 medium det + rec   ~50 s/page, mean conf 99.6
#   PP-OCRv5 mobile det + rec   ~19 s/page, mean conf 96.0, drops spaces ("4Rutland")
#   PP-OCRv5 mobile det + v6 medium rec  ~22 s/page, mean conf 99.6, same text as medium
# The last is used. oneDNN is disabled because paddlepaddle 3.3.1 on Windows fails
# with "ConvertPirAttribute2RuntimeAttribute not support" when it is on.
DEFAULT_OPTIONS = {
    "use_doc_orientation_classify": False,
    "use_doc_unwarping": False,
    "use_textline_orientation": False,
    "text_detection_model_name": "PP-OCRv5_mobile_det",
    "text_recognition_model_name": "PP-OCRv6_medium_rec",
    "enable_mkldnn": False,
}
# settings that change how fast recognition runs, not what it reads: they must not split the cache
RUNTIME_OPTIONS = {"cpu_threads"}


class PaddleEngine(OCREngine):
    name = "paddle"

    def __init__(self, lang: str = "en", **options):
        self.lang = lang
        self.options = {**DEFAULT_OPTIONS, **options}

    def cache_tag(self) -> str:
        changed = {k: v for k, v in self.options.items()
                   if k not in RUNTIME_OPTIONS and DEFAULT_OPTIONS.get(k, object()) != v}
        if self.lang != "en":
            changed["lang"] = self.lang
        if not changed:
            return ""
        return "o" + hashlib.sha1(json.dumps(changed, sort_keys=True, default=str).encode()).hexdigest()[:8]

    @cached_property
    def _ocr(self):
        from paddleocr import PaddleOCR  # imported lazily: loading models takes seconds
        return PaddleOCR(lang=self.lang, **self.options)

    def version(self) -> str:
        import paddleocr
        return f"paddleocr {paddleocr.__version__}"

    def recognise(self, image_path: Path, crop: tuple[int, int, int, int] | None = None,
                  psm: int = 3) -> OCRResult:
        # psm is a Tesseract setting; accepted and ignored so engines are interchangeable
        image_path = Path(image_path)
        with Image.open(image_path) as im:
            width, height = im.size
            region = im.crop(crop) if crop else im
            arr = np.asarray(region.convert("RGB"))[:, :, ::-1].copy()  # PaddleOCR expects BGR
        dx, dy = (crop[0], crop[1]) if crop else (0, 0)

        t0 = time.perf_counter()
        results = list(self._ocr.predict(arr, return_word_box=True))
        seconds = time.perf_counter() - t0

        words: list[Word] = []
        for res in results:
            words.extend(words_from_result(res, dx, dy))
        return OCRResult(image_path=str(image_path), engine=self.name, engine_version=self.version(),
                         seconds=round(seconds, 3), width=width, height=height, words=words)


def _get(res, key):
    try:
        return res[key]
    except (KeyError, TypeError):
        return getattr(res, key, None)


def words_from_result(res, dx: int = 0, dy: int = 0) -> list[Word]:
    texts = _get(res, "rec_texts") or []
    scores = _get(res, "rec_scores") or []
    boxes = _get(res, "rec_boxes")
    polys = _get(res, "rec_polys")
    word_texts = _get(res, "text_word")
    word_boxes = _get(res, "text_word_region")

    out: list[Word] = []
    for i, text in enumerate(texts):
        if not str(text).strip():
            continue
        conf = float(scores[i]) * 100 if i < len(scores) else -1.0
        box = _line_box(boxes, polys, i)
        if box is None:
            continue
        line_id = i + 1
        pieces = None
        if word_texts is not None and word_boxes is not None and i < len(word_texts):
            pieces = _merge_word_tokens(word_texts[i], word_boxes[i])
        if not pieces:
            pieces = _split_line(str(text), box)
        for wtext, (l, t, r, b) in pieces:
            out.append(Word(text=wtext, left=int(l) + dx, top=int(t) + dy,
                            width=max(1, int(r - l)), height=max(1, int(b - t)),
                            conf=round(conf, 2), block=0, line=line_id))
    return out


def _line_box(boxes, polys, i):
    if boxes is not None and i < len(boxes):
        l, t, r, b = [float(v) for v in boxes[i]]
        return l, t, r, b
    if polys is not None and i < len(polys):
        p = np.asarray(polys[i], dtype=float)
        return p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()
    return None


def _merge_word_tokens(tokens, regions):
    """PaddleOCR's word output can be per character, per word, or a run of words with
    spaces inside one token; join tokens between spaces, then split any run that still
    holds a space in proportion to character counts."""
    merged, cur_text, cur_box = [], "", None

    def flush():
        nonlocal cur_text, cur_box
        if cur_text.strip():
            merged.append((cur_text.strip(), cur_box))
        cur_text, cur_box = "", None

    for tok, reg in zip(tokens, regions):
        tok = str(tok)
        p = np.asarray(reg, dtype=float).reshape(-1, 2)
        box = (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max())
        if not tok.strip():
            flush()
            continue
        if tok[0].isspace():
            flush()
        if cur_box is None:
            cur_text, cur_box = tok, box
        else:
            cur_text += tok
            cur_box = (min(cur_box[0], box[0]), min(cur_box[1], box[1]),
                       max(cur_box[2], box[2]), max(cur_box[3], box[3]))
        if tok[-1].isspace():
            flush()
    flush()

    words = []
    for text, box in merged:
        words.extend(_split_line(text, box) if " " in text else [(text, box)])
    return words


def _split_line(text: str, box) -> list[tuple[str, tuple[float, float, float, float]]]:
    l, t, r, b = box
    parts = text.split()
    total = sum(len(p) for p in parts) + max(0, len(parts) - 1)
    if not parts or total == 0:
        return []
    per_char = (r - l) / total
    out, x = [], l
    for p in parts:
        w = len(p) * per_char
        out.append((p, (x, t, x + w, b)))
        x += w + per_char
    return out
