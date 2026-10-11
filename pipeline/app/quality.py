"""Is a photograph good enough to read? Measured the same way on the phone and on the laptop.

The phone checks first, so the manager can retake at once. The server repeats the checks and adds how
many words were recognised. Server-rendered PDF pages use an ink contrast check: scanner paper is
normally white, so whole-page brightness and blown-out pixel counts cannot distinguish paper from glare.

Every measure is taken on a grayscale copy downscaled to 800 px on the long side, so a threshold means
the same thing whatever resolution the phone photographed at. The thresholds are starting values in one
object, to be calibrated on real photographs (docs/phone-api.md section 6 holds the phone's copy).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class QualityThresholds:
    long_side: int = 800                  # measures are taken at this size
    min_sharpness: float = 60.0           # variance of the 3x3 Laplacian: below this, blurred
    min_mean_luma: float = 70.0           # too dark
    max_mean_luma: float = 215.0          # washed out
    min_contrast_spread: float = 80.0     # 95th minus 5th luminance percentile: below this, faint print
    glare_level: int = 250                # a pixel this bright is blown out
    glare_block: int = 16                 # pixels per side of a block
    glare_block_share: float = 0.25       # a block is glare when this share of its pixels are blown out
    max_glare_fraction: float = 0.02      # share of the page's pixels in glare blocks
    min_words: int = 25                   # fewer recognised words: cropped, dark, or not an invoice


THRESHOLDS = QualityThresholds()
MEASURES = ("sharpness", "mean_luma", "contrast_spread", "glare_fraction")


def gray_downscaled(path: Path, long_side: int = THRESHOLDS.long_side) -> np.ndarray | None:
    import cv2
    data = np.fromfile(str(path), dtype=np.uint8)     # cv2.imread cannot open every Windows path
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape
    scale = long_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    return img


def glare_fraction(gray: np.ndarray, t: QualityThresholds = THRESHOLDS) -> float:
    b = t.glare_block
    h, w = (gray.shape[0] // b) * b, (gray.shape[1] // b) * b
    if not h or not w:
        return 0.0
    bright = (gray[:h, :w] >= t.glare_level).reshape(h // b, b, w // b, b)
    per_block = bright.sum(axis=(1, 3))
    glare_blocks = per_block >= t.glare_block_share * b * b
    return float(per_block[glare_blocks].sum()) / (h * w)


def measure(path: Path, t: QualityThresholds = THRESHOLDS, *, scanned_pdf: bool = False) -> dict | None:
    """Keep camera measures; additionally measure sparse ink on server-rendered PDF pages."""
    import cv2
    gray = gray_downscaled(path, t.long_side)
    if gray is None:
        return None
    p5, p95 = np.percentile(gray, [5, 95])
    measured = {
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F, ksize=1).var()), 1),
        "mean_luma": round(float(gray.mean()), 1),
        "contrast_spread": round(float(p95 - p5), 1),
        "glare_fraction": round(glare_fraction(gray, t), 4),
    }
    if scanned_pdf:
        # Five percent can contain only paper on sparse invoices; one percent still rejects isolated specks.
        p1, p99 = np.percentile(gray, [1, 99])
        measured.update(profile="scanned_pdf", ink_contrast_spread=round(float(p99 - p1), 1))
    return measured


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if np.isfinite(value) else None


def problems(measures: dict | None, t: QualityThresholds = THRESHOLDS) -> dict[str, str]:
    """Problems by kind, in plain words, from whichever measures are present."""
    m = measures or {}
    scanned_pdf = m.get("profile") == "scanned_pdf"
    out: dict[str, str] = {}
    s = _number(m.get("sharpness"))
    if s is not None and s < t.min_sharpness:
        out["blur"] = f"photograph looks blurred (sharpness {s:.0f}, needs {t.min_sharpness:.0f})"
    luma = _number(m.get("mean_luma"))
    if luma is not None and luma < t.min_mean_luma:
        out["dark"] = f"photograph is too dark (brightness {luma:.0f}, needs at least {t.min_mean_luma:.0f})"
    elif not scanned_pdf and luma is not None and luma > t.max_mean_luma:
        out["washed_out"] = f"photograph is washed out (brightness {luma:.0f}, needs at most {t.max_mean_luma:.0f})"
    spread = _number(m.get("ink_contrast_spread" if scanned_pdf else "contrast_spread"))
    if spread is not None and spread < t.min_contrast_spread:
        out["faint"] = f"print is too faint (contrast {spread:.0f}, needs {t.min_contrast_spread:.0f})"
    glare = _number(m.get("glare_fraction"))
    if not scanned_pdf and glare is not None and glare > t.max_glare_fraction:
        out["glare"] = f"glare hides part of the page ({glare:.1%} of it, allowed {t.max_glare_fraction:.0%})"
    if m.get("cut_off") is True:
        out["cut_off"] = "the page is cut off"
    return out


def assess(server: dict | None, phone: dict | None, words: int | None = None,
           t: QualityThresholds = THRESHOLDS) -> dict:
    """{"ok", "problems"} for a page: the server's measures first, then anything only the phone saw."""
    found = problems(server, t)
    # A phone cannot select the PDF profile by submitting a quality field.
    camera = {key: value for key, value in (phone or {}).items() if key != "profile"}
    for kind, text in problems(camera, t).items():
        found.setdefault(kind, f"{text} on the phone's check")
    if words is not None and words < t.min_words:
        found["words"] = f"only {words} words recognised (needs {t.min_words})"
    return {"ok": not found, "problems": list(found.values())}
