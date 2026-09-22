"""Step 2 of ingest: read the top of each page and decide supplier, document number and date.

The hard part is the document number. An invoice header is a pile of long numbers
(telephone, fax, VAT, company registration, account, liquor licence) and the wanted one
looks like all the others. Prefer numbers beside a document-number label and reject
phone, VAT and registration labels. If no labelled number is found, try the identified
supplier's numbering pattern.

"Next to" is decided by position on the page, not by the engine's line grouping: on
Cafe Albrecht's layout the value INV479253 is printed a few pixels higher than its
"Inv No." label, so any row-based grouping puts them on different lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from ingest.suppliers import SUPPLIERS, VENUE_STOPWORDS, Supplier
from models import OCRResult, Word

DOC_LABELS = ("document no", "document number", "invoice no", "invoice number", "inv no", "inv. no",
              "tax invoice no", "invoice #", "doc no", "docno", "document", "invoice")
REJECT_LABELS = ("tel", "fax", "vat", "reg", "registration", "account", "acc", "licence", "license",
                 "order", "phone", "cell", "branch", "code", "customer", "tax registration", "bay")
DATE_LABELS = ("date", "inv date", "invoice date", "date & time")

NUM_RE = re.compile(r"^[A-Z]{0,4}[-/]?\d[\d-]{3,}$", re.IGNORECASE)
DATE_RES = (
    re.compile(r"(20\d\d)[/.-](\d{1,2})[/.-](\d{1,2})"),
    re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](20\d\d|\d\d)"),
)
PAGE_OF_RE = re.compile(r"page\s*(\d+)\s*(?:of|/)\s*(\d+)", re.IGNORECASE)

_STRIP = ".,:;|()[]#"
LABEL_ROWS_ABOVE = 3       # tabular headers put the label up to a few rows above its value
LABEL_MAX_GAP = 5.0        # ... but no further than this many word-heights away
SAME_LINE_TOLERANCE = 0.8  # centres within this many word-heights count as one printed line
LABEL_MAX_REACH = 25.0     # a label further left than this many word-heights belongs to something else

SUPPLIER_MIN_SCORE = 80
TOKEN_MIN_RATIO = 75       # every word of a multi-word alias must match a header word this well


@dataclass
class Identification:
    supplier: Supplier | None
    supplier_score: float
    document_no: str | None
    date: str | None
    page_of: tuple[int, int] | None
    header_text: str


def identify(header: OCRResult) -> Identification:
    text = " ".join(w.text for w in header.words)
    supplier, score = match_supplier(text)
    document_no = find_document_no(header.words)
    if document_no is None and supplier is not None and supplier.doc_pattern:
        document_no = find_by_pattern(header.words, supplier.doc_pattern)
    return Identification(
        supplier=supplier,
        supplier_score=score,
        document_no=document_no,
        date=find_date(header.words),
        page_of=find_page_of(text),
        header_text=text,
    )


def find_by_pattern(words: list[Word], pattern: str) -> str | None:
    """Return the first token matching the supplier pattern in top-to-bottom reading order."""
    rx = re.compile(rf"(?<!\d)({pattern})(?!\d)")
    for w in sorted(words, key=lambda w: (w.top, w.left)):
        m = rx.search(w.text)
        if m:
            return m.group(1)
    return None


# --- supplier --------------------------------------------------------------

def _clean(text: str) -> str:
    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    for stop in VENUE_STOPWORDS:
        t = t.replace(stop, " ")
    return re.sub(r"\s+", " ", t).strip()


def _alias_score(alias: str, clean: str, tokens: list[str]) -> float:
    if len(alias) <= 5:
        # short aliases must appear as whole words; fuzzy matching them is noise
        return 100.0 if re.search(rf"\b{re.escape(alias)}\b", clean) else 0.0
    phrase = fuzz.partial_ratio(alias, clean)
    parts = alias.split()
    if len(parts) < 2 or not tokens:
        return phrase
    # word by word: survives the words of a name being printed on separate lines and
    # misread ("fournews" ... "Logisfics"), while a name sharing only one word scores low
    worst = min(max(fuzz.ratio(p, t) for t in tokens) for p in parts)
    wordwise = worst if worst >= TOKEN_MIN_RATIO else 0.0
    return max(phrase, wordwise)


def match_supplier(text: str) -> tuple[Supplier | None, float]:
    """Best supplier by alias, scored 0-100."""
    clean = _clean(text)
    tokens = clean.split()
    best, best_score, best_len = None, 0.0, 0
    for s in SUPPLIERS:
        for alias in s.aliases:
            sc = _alias_score(alias, clean, tokens)
            # longer aliases are more specific, so they win ties
            if sc > best_score or (sc == best_score and sc > 0 and len(alias) > best_len):
                best, best_score, best_len = s, sc, len(alias)
    if best_score < SUPPLIER_MIN_SCORE:
        return None, best_score
    return best, best_score


# --- layout helpers --------------------------------------------------------

def _lines(words: list[Word]) -> list[list[Word]]:
    """Group words by vertical centre distance scaled by word height, ignoring engine line IDs."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.cy, w.left)):
        for row in rows:
            ref = row[0]
            if abs(w.cy - ref.cy) < max(ref.height, w.height) * 0.6:
                row.append(w)
                break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w.left)
    return rows


LABEL_VERTICAL_REACH = 1.6    # a label may sit up to this many word-heights above or below its value
VERTICAL_WEIGHT = 3.0         # vertical offset counts this much more than horizontal gap


def _label_left_of(w: Word, words: list[Word], span: int = 4) -> str:
    """The label printed to the left of w, by geometry.

    The nearest word to the left is found first, with vertical offset weighted heavily, so
    a label one line out of step with its value (Fournews: FNLI30727 printed above "DOC NO")
    beats a level but distant unrelated field ("Co. Reg No 1995/013839/07"). The label is then
    that word and the words level with it, up to the value.
    """
    h = max(w.height, 1)
    left = [o for o in words
            if o is not w and o.right <= w.left + h * 0.5
            and abs(o.cy - w.cy) < h * LABEL_VERTICAL_REACH
            and w.left - o.right < h * LABEL_MAX_REACH]
    if not left:
        return ""
    nearest = min(left, key=lambda o: ((w.left - o.right) ** 2 + (VERTICAL_WEIGHT * (o.cy - w.cy)) ** 2))
    line = [o for o in left if abs(o.cy - nearest.cy) < max(o.height, nearest.height) * SAME_LINE_TOLERANCE]
    line.sort(key=lambda o: o.left)
    return " ".join(o.text for o in line[-span:]).lower()


def _token(text: str) -> str:
    return text.strip(_STRIP)


def _looks_like_docno(tok: str) -> bool:
    tok = _token(tok)
    return bool(NUM_RE.match(tok)) and not DATE_RES[0].search(tok) and not DATE_RES[1].search(tok)


def _has_label(text: str, labels) -> bool:
    return any(re.search(rf"(^|\s){re.escape(l)}\.?($|\s)", text) for l in labels)


# --- document number -------------------------------------------------------

def find_document_no(words: list[Word]) -> str | None:
    rows = _lines(words)
    candidates: list[tuple[int, float, str]] = []  # (top on page, distance to label, token)
    for ri, row in enumerate(rows):
        for w in row:
            tok = _token(w.text)
            if not _looks_like_docno(tok):
                continue
            before = _label_left_of(w, words)
            last_two = " ".join(before.split()[-2:])
            if _has_label(last_two, REJECT_LABELS):
                continue
            if _has_label(before, DOC_LABELS):
                candidates.append((w.top, 0.0, tok))
                continue
            # value printed in a box beneath its label: search a few rows up, same column.
            # Other columns' values (a date, a page number) can sit on rows in between.
            for up in range(1, LABEL_ROWS_ABOVE + 1):
                if ri - up < 0:
                    break
                label_row = rows[ri - up]
                gap = (w.top - label_row[0].bottom) / max(w.height, 1)
                if gap > LABEL_MAX_GAP:
                    break
                above = [a for a in label_row if a.left - w.width < w.cx < a.right + w.width]
                label = " ".join(a.text for a in above).lower()
                if any(r in label for r in ("order", "account", "vat", "date", "page")):
                    break  # the column belongs to another field
                if _has_label(label, DOC_LABELS):
                    candidates.append((w.top, gap, tok))
                    break
    if not candidates:
        return None
    # Binder pages are photographed overlapping: a strip of the sheet behind shows at the top of the
    # photo, number and all. The photographed sheet's own number is the lowest labelled one.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    return candidates[0][2]


# --- date ------------------------------------------------------------------

def find_date(words: list[Word]) -> str | None:
    labelled, loose = [], []
    for w in words:
        for d in _dates_in(w.text):
            context = _label_left_of(w, words)
            (labelled if _has_label(context, DATE_LABELS) else loose).append((w.top, d))
    if not labelled:
        # dates printed as separate tokens ("11/08/26" split by the engine) or under a label
        rows = _lines(words)
        for ri, row in enumerate(rows):
            joined = " ".join(w.text for w in row)
            above = " ".join(w.text for w in rows[ri - 1]).lower() if ri else ""
            for d in _dates_in(joined):
                target = labelled if (_has_label(joined.lower(), DATE_LABELS) or _has_label(above, DATE_LABELS)) else loose
                target.append((row[0].top, d))
    # topmost first; the sort is on position only, so within one token the order _dates_in
    # returned (year-first before day-first) is kept
    found = sorted(labelled, key=lambda t: t[0]) or sorted(loose, key=lambda t: t[0])
    return found[0][1] if found else None


def _dates_in(text: str) -> list[str]:
    out = []
    taken: list[tuple[int, int]] = []
    for m in DATE_RES[0].finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append(f"{y:04d}-{mo:02d}-{d:02d}")
            taken.append(m.span())
    for m in DATE_RES[1].finditer(text):
        # "2026/08/24" also contains "26/08/24"; a day-first reading inside a year-first date is not a date
        if any(a < m.end() and m.start() < b for a, b in taken):
            continue
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y) + (2000 if len(y) == 2 else 0)
        if 1 <= mo <= 12 and 1 <= d <= 31 and 2000 <= y <= 2099:
            out.append(f"{y:04d}-{mo:02d}-{d:02d}")
    return out


# the digit pairs a recognition engine most often swaps, used only to repair a year
DIGIT_CONFUSIONS = {"6": "85", "8": "630", "5": "6", "0": "86", "3": "8", "1": "7", "7": "1"}


def repair_year(date: str, expected_year: int) -> str | None:
    """If date's year differs from expected_year by one confusable digit, return it repaired."""
    y = date[:4]
    target = str(expected_year)
    if y == target:
        return date
    diffs = [i for i in range(4) if y[i] != target[i]]
    if len(diffs) == 1 and target[diffs[0]] in DIGIT_CONFUSIONS.get(y[diffs[0]], ""):
        return target + date[4:]
    return None


def find_page_of(text: str) -> tuple[int, int] | None:
    m = PAGE_OF_RE.search(text)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if 1 <= a <= b <= 20 else None
