"""Page geometry shared by the parser: skew, visual rows and word heights."""
from __future__ import annotations

from statistics import median

from models import Word

ROW_TOLERANCE = 0.6    # levels closer than this many word-heights share a row
NEIGHBOUR_GAP = 10.0   # word-heights: how far right to look for the next word on a line
MIN_SKEW_PAIRS = 5
SKEW_LIMIT = 0.06      # search bound for dy/dx (about 3.4 degrees); not a rescan decision
SKEW_STEP = 0.003
MIN_ROW_GAIN = 2       # a tilt is only applied if it removes at least this many rows ...
MIN_ROW_GAIN_SHARE = 0.05  # ... or this share of them, whichever is larger


def estimate_skew(words: list[Word]) -> float:
    """Page tilt as dy/dx from the median slope between neighbouring words on different engine lines.

    Words the engine split from one text line share one box and so always have slope zero, whatever
    the page's tilt; only pairs across fragments carry the tilt. Used as one candidate in best_skew.
    """
    ws = sorted(words, key=lambda w: w.left)
    slopes = []
    for i, w in enumerate(ws):
        h = max(w.height, 1)
        best = None
        for o in ws[i + 1:]:
            if o.left - w.right > NEIGHBOUR_GAP * h:
                break
            if o.line == w.line and o.block == w.block:
                continue
            dx, dy = o.cx - w.cx, o.cy - w.cy
            if dx <= h or abs(dy) >= max(h, o.height):
                continue
            if best is None or dx < best[0]:
                best = (dx, dy)
        if best:
            slopes.append(best[1] / best[0])
    return float(median(slopes)) if len(slopes) >= MIN_SKEW_PAIRS else 0.0


def _group(words: list[Word], skew: float, tolerance: float) -> list[list[Word]]:
    def level(w: Word) -> float:
        return w.cy - skew * w.cx

    rows: list[list[Word]] = []
    levels: list[float] = []
    for w in sorted(words, key=lambda w: (level(w), w.left)):
        lw = level(w)
        placed = False
        for k in range(len(rows) - 1, -1, -1):
            ref = rows[k][0]
            h = max(ref.height, w.height)
            if lw - levels[k] > 2 * h:
                break          # rows further back are further away still
            if abs(lw - levels[k]) < h * tolerance:
                rows[k].append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
            levels.append(lw)
    for row in rows:
        row.sort(key=lambda w: w.left)
    return rows


def best_skew(words: list[Word], tolerance: float = ROW_TOLERANCE) -> float:
    """The tilt that lines the words up into the fewest rows.

    A single slope estimate can over-correct on a page with stamps and handwriting and split real
    lines apart. The right tilt is the one under which printed lines are tightest, so candidates are
    tried and the one giving the fewest rows is kept, and only if it clearly beats no tilt at all.
    """
    if len(words) < 20:
        return 0.0
    base = len(_group(words, 0.0, tolerance))
    steps = round(2 * SKEW_LIMIT / SKEW_STEP)
    candidates = [round(-SKEW_LIMIT + k * SKEW_STEP, 4) for k in range(steps + 1)]
    est = estimate_skew(words)
    if abs(est) <= SKEW_LIMIT:
        candidates.append(round(est, 4))
    best_s, best_n = 0.0, base
    for s in candidates:
        n = len(_group(words, s, tolerance))
        if n < best_n or (n == best_n and abs(s) < abs(best_s)):
            best_s, best_n = s, n
    return best_s if base - best_n >= max(MIN_ROW_GAIN, MIN_ROW_GAIN_SHARE * base) else 0.0


def group_rows(words: list[Word], tolerance: float = ROW_TOLERANCE, skew: float | None = None) -> list[list[Word]]:
    """Words grouped into visual rows along the page's tilt, each row sorted left to right.

    Rows are built from position alone, not from the engine's own line ids, because engines
    split one printed line into several fragments and occasionally join two.
    """
    if skew is None:
        skew = best_skew(words, tolerance)
    return _group(words, skew, tolerance)


def row_top(row: list[Word]) -> int:
    return min(w.top for w in row)


def row_bottom(row: list[Word]) -> int:
    return max(w.bottom for w in row)


def row_text(row: list[Word]) -> str:
    return " ".join(w.text for w in row)


def typical_height(words: list[Word], default: float = 30.0) -> float:
    hs = [w.height for w in words if w.height > 0]
    return float(median(hs)) if hs else default
