"""Reading numbers as printed on invoices and as mangled by recognition.

Observed on the August binder:
  R345.00, R2 990.00       currency prefix; a thousands space splits one amount into two words
  495 00, R645 75          the decimal point lost, leaving two words
  7875, 13941              the decimal point lost inside one word (78.75, 139.41)
  6.00V, 1.50√, 3.00V      a handwritten delivery tick fused to the quantity
  15%, 0%, 3%              rates and discounts
Handle currency prefixes, trailing tick glyphs and neighbouring number fragments here.
A lost decimal point inside one word is not guessed here: the line's arithmetic
decides it later (see parser.engine.reconcile).
"""
from __future__ import annotations

import re

from models import Word

TICK_GLYPHS = "Vv√✓/\\"
MERGE_GAP = 1.2          # word-heights; further apart than this, two words are two values


def clean_token(text: str) -> str:
    t = text.strip().strip("|:;'\"")
    t = re.sub(r"^[Rr](?=[\d(\-])", "", t)
    t = re.sub(rf"(?<=\d)[{re.escape(TICK_GLYPHS)}]+$", "", t)
    return t


def parse_amount(text: str | None) -> float | None:
    if text is None:
        return None
    t = clean_token(text)
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    if t.startswith("-"):
        negative, t = True, t[1:]
    if t.endswith("-"):
        negative, t = True, t[:-1]
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "")
    elif re.fullmatch(r"\d+,\d{1,2}", t):
        t = t.replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if negative else v


def parse_percent(text: str | None) -> float | None:
    if not text or "%" not in text:
        return None
    v = parse_amount(text.replace("%", ""))
    return None if v is None else v / 100.0


def merge_cell_tokens(words: list[Word], height: float) -> list[tuple[str, float]]:
    """Join words in one table cell that are one printed value; returns (text, mean confidence)."""
    items = [[clean_token(w.text), w.left, w.right, [w.conf]] for w in sorted(words, key=lambda w: w.left)]
    i = 0
    while i < len(items) - 1:
        a, b = items[i], items[i + 1]
        joined = None
        if b[1] - a[2] <= MERGE_GAP * height:
            if re.fullmatch(r"-?\d{1,3}", a[0]) and re.fullmatch(r"\d{3}([.,]\d{1,2})?", b[0]):
                joined = a[0] + b[0]                    # thousands space: 2 | 990.00
            elif re.fullmatch(r"-?\d+", a[0]) and re.fullmatch(r"\d{2}", b[0]):
                joined = a[0] + "." + b[0]              # lost decimal point: 495 | 00
            elif re.fullmatch(r"-?\d+[.,]", a[0]) and re.fullmatch(r"\d{1,2}", b[0]):
                joined = a[0][:-1] + "." + b[0]         # point kept on the left: 495. | 00
        if joined is not None:
            items[i] = [joined, a[1], b[2], a[3] + b[3]]
            del items[i + 1]
            continue
        i += 1
    return [(t, sum(c) / len(c)) for t, _, _, c in items]


def cell_percent(words: list[Word]) -> float | None:
    for w in words:
        p = parse_percent(w.text)
        if p is not None:
            return p
    return None
