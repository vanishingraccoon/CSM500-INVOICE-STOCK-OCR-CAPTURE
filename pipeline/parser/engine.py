"""The generic table engine: words with positions in, an InvoiceRecord out.

1. Find the header: the band of one to three adjacent rows whose words name the most fields.
   Header words the engine ran together ("UnitUnit", "%TaxNett") are split back into words.
2. Place column boundaries midway between neighbouring header phrases.
3. Walk the rows below. A row with a figure in a price or total column is a line; a row with
   only description text continues the line above. The table ends at a totals label or footer,
   and the stated totals are read from those rows.
4. Reconcile each line: quantity x price x (1 - discount) should equal its total. Where it does
   not, try the repairs recognition is known to need, each accepted only if the line then adds up:
   a decimal point lost from a figure printed without one, a delivery tick read as a digit after
   the quantity, a price column that holds the line amount. Where quantity is unprinted, derive it.
   Every repair and derivation is written into the record's warnings, so the evaluation can count them.
5. Compare the lines' sum with the stated total. A mismatch is recorded, never corrected:
   suppliers do issue invoices whose lines do not add up.

Two things observed on the binder are handled at invoice level: figures below the table (bank
details, the invoice total itself) read as lines, and the same page photographed twice inside a
multi-page invoice.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

from models import InvoiceRecord, Line, OCRResult, Page, Word
from normaliser.units import parse_pack
from parser.geometry import group_rows, row_bottom, row_text, row_top, typical_height
from parser.layouts import (
    FOOTER_WORDS,
    MONEY_FIELDS,
    TOTAL_LABELS,
    Layout,
    for_supplier,
)
from parser.numbers import cell_percent, merge_cell_tokens, parse_amount

HEADER_BAND_GAP = 1.5        # word-heights between rows that still form one header band
PHRASE_MAX_GAP = 1.5         # word-heights between words of one header phrase: a space, not a column gap
TABLE_GAP_STOP = 12.0        # word-heights of empty space after the last line that ends the table
ADDS_UP_TOLERANCE = 0.10     # rand
VAT_RATE = 0.15              # South Africa
LABEL_REACH = 4              # a totals label's figure follows within this many words
DUPLICATE_PAGE_OVERLAP = 0.6 # share of the smaller page's lines that must recur for a page to be a repeat
MIN_CODES_FOR_REPEAT = 3     # below this many stock codes, pages are compared by line totals instead

MONEY_TOKEN = re.compile(r"\d[.,]\d{2}\b")
UNIT_WITH_NUMBER = re.compile(r"^(\d+(?:[.,]\d+)?)([A-Za-z]{2,})$")   # "2.205Kilogram"


# --- header --------------------------------------------------------------------------------

@dataclass
class ColumnSpan:
    field: str
    left: float
    right: float

    @property
    def centre(self) -> float:
        return (self.left + self.right) / 2


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9%]+", "", text.lower())


def _vocabulary(layout: Layout) -> list[str]:
    words = {w for phrase in layout.phrases() for w in phrase}
    return sorted(words, key=len, reverse=True)


def header_tokens(words: list[Word], layout: Layout) -> list[tuple[str, float, float]]:
    """Normalised header words with x-spans; tokens that are several vocabulary words run together are split."""
    vocab = _vocabulary(layout)
    pattern = re.compile("|".join(re.escape(v) for v in vocab))
    out = []
    for w in sorted(words, key=lambda w: w.left):
        t = _norm(w.text)
        if not t:
            continue
        pieces = pattern.findall(t) if t not in vocab else [t]
        if t not in vocab and "".join(pieces) == t and len(pieces) > 1:
            per_char = w.width / len(t)
            x = w.left
            for p in pieces:
                out.append((p, x, x + per_char * len(p)))
                x += per_char * len(p)
        else:
            out.append((_near_vocabulary(t, vocab) or t, w.left, w.right))
    return out


def _near_vocabulary(t: str, vocab: list[str]) -> str | None:
    """A header word with one misread letter ("FRICE" for PRICE), when exactly one longer vocabulary word fits."""
    if t in vocab or len(t) < 5:
        return None
    near = [v for v in vocab if len(v) >= 5 and Levenshtein.distance(t, v) <= 1]
    return near[0] if len(near) == 1 else None


def match_header(tokens: list[tuple[str, float, float]], layout: Layout, height: float) -> list[ColumnSpan]:
    phrases = layout.phrases()
    spans: list[ColumnSpan] = []
    seen: set[str] = set()
    i = 0
    while i < len(tokens):
        for phrase, fld in phrases.items():
            n = len(phrase)
            window = tokens[i:i + n]
            if len(window) < n or tuple(t for t, _, _ in window) != phrase:
                continue
            if any(window[k + 1][1] - window[k][2] > PHRASE_MAX_GAP * height for k in range(n - 1)):
                continue
            if fld not in seen:
                spans.append(ColumnSpan(fld, window[0][1], window[-1][2]))
                seen.add(fld)
            i += n
            break
        else:
            i += 1
    return spans


def find_header(rows: list[list[Word]], layout: Layout, height: float) -> tuple[list[ColumnSpan], int] | None:
    """Best header band: (column spans, index of the last row in the band)."""
    best: tuple[int, int, int, list[ColumnSpan]] | None = None   # (fields, -rows, -index, spans)
    for i in range(len(rows)):
        band: list[Word] = []
        for k in range(3):
            j = i + k
            if j >= len(rows):
                break
            if k and row_top(rows[j]) - row_bottom(rows[j - 1]) > HEADER_BAND_GAP * height:
                break
            if k and any(MONEY_TOKEN.search(w.text) for w in rows[j]):
                # a row with money in it is the first line of the table, not more header. Letting it
                # join would interleave its figures between header words ("Unit 211.69 Price") and
                # break the phrases apart, which can raise the field count and swallow the line.
                break
            band.extend(rows[j])
            spans = match_header(header_tokens(band, layout), layout, height)
            fields = {s.field for s in spans}
            if len(fields) < layout.min_header_fields or not fields & MONEY_FIELDS:
                continue
            key = (len(fields), -(k + 1), -i)
            if best is None or key > best[:3]:
                best = (*key, spans)
    if best is None:
        return None
    _, neg_rows, neg_i, spans = best
    return spans, -neg_i - neg_rows - 1


def column_bounds(spans: list[ColumnSpan]) -> list[tuple[str, float, float]]:
    ordered = sorted(spans, key=lambda s: s.centre)
    bounds = []
    for k, s in enumerate(ordered):
        lo = float("-inf") if k == 0 else (ordered[k - 1].right + s.left) / 2
        hi = float("inf") if k == len(ordered) - 1 else (s.right + ordered[k + 1].left) / 2
        bounds.append((s.field, lo, hi))
    return bounds


def assign(row: list[Word], bounds) -> dict[str, list[Word]]:
    cells: dict[str, list[Word]] = {}
    for w in row:
        for fld, lo, hi in bounds:
            if lo <= w.cx < hi:
                cells.setdefault(fld, []).append(w)
                break
    return cells


# --- totals and table end ------------------------------------------------------------------

_LABELS = sorted(((tuple(k.split()), v) for k, v in TOTAL_LABELS.items()), key=lambda kv: (-len(kv[0]), -len("".join(kv[0]))))


def _label_word(text: str) -> str:
    return re.sub(r"[^a-z%&]+", "", text.lower())


def _word_is(word: str, target: str, fuzzy: bool) -> bool:
    if word == target:
        return True
    # one misread letter in a longer label word ("Tofal" for "Total"); short words must be exact
    return fuzzy and len(target) >= 4 and len(word) >= 3 and Levenshtein.distance(word, target) <= 1


def find_totals_label(row: list[Word]) -> tuple[str, int] | None:
    """(kind, index of the first word after the label) for a totals label followed by a figure.

    The label need not start the row: Aero Power prints "Credit Terms: 14 Days" to the left of its
    TOTALS box on the same line. It must be followed within a few words by a money figure, and no
    money figure may come before it, since a line item's figures sit to the right of its description.
    Exact labels are tried before a one-letter misread is allowed.
    """
    words = [_label_word(w.text) for w in row]
    for fuzzy in (False, True):
        for i in range(len(row)):
            if MONEY_TOKEN.search(row[i].text):
                break
            for label, kind in _LABELS:
                n = len(label)
                if i + n > len(row):
                    continue
                if all(_word_is(words[i + k], label[k], fuzzy) for k in range(n)) \
                        and any(MONEY_TOKEN.search(w.text) for w in row[i + n:i + n + LABEL_REACH]):
                    return kind, i + n
    return None


def totals_label(row: list[Word]) -> str | None:
    found = find_totals_label(row)
    return found[0] if found else None


def is_footer(row: list[Word]) -> bool:
    text = row_text(row).lower()
    return any(f in text for f in FOOTER_WORDS)


def split_totals_row(values: list[float]) -> dict[str, float]:
    """Excl, VAT and incl from a TOTALS row, decided by arithmetic rather than position.

    Position is not reliable: Vassco's till software prints the quantity total first
    ("Totals 28.00 | 3382.55 | 507.40"), so the last three figures are not excl, VAT, incl.
    The pair where one figure is 15% of another is excl and VAT.
    """
    for i, excl in enumerate(values):
        for j, vat in enumerate(values):
            if i != j and excl > 0 and abs(vat - VAT_RATE * excl) <= max(0.02, 0.01 * vat):
                incl = next((v for v in values if abs(v - (excl + vat)) <= 0.05), round(excl + vat, 2))
                return {"excl": excl, "vat": vat, "incl": incl}
    if len(values) >= 3:
        return dict(zip(("excl", "vat", "incl"), values[-3:]))
    return {"incl": values[-1]}


def read_totals(rows: list[list[Word]], height: float) -> dict[str, float]:
    found: dict[str, float] = {}
    for row in rows:
        hit = find_totals_label(row)
        if hit is None:
            continue
        kind, start = hit
        if kind == "ignore":      # "Total Discount: R0.00" ends the table but is not a total
            continue
        values = [v for v in (parse_amount(t) for t, _ in merge_cell_tokens(row[start:], height)) if v is not None]
        if not values:
            continue
        if kind == "row":
            for k, v in split_totals_row(values).items():
                found.setdefault(k, v)
        else:
            found.setdefault(kind, values[-1])
    return found


# --- lines ---------------------------------------------------------------------------------

@dataclass
class RawLine:
    cells: dict[str, list[Word]]
    top: int
    bottom: int
    extra_description: list[Word]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.02, 0.005 * abs(b))


def _cell(words: list[Word], height: float, last: bool = True) -> tuple[float | None, bool]:
    """A cell's figure, and whether it was printed without a decimal point (so one may have been lost)."""
    vals = [(parse_amount(t), t) for t, _ in merge_cell_tokens(words, height)]
    vals = [(v, t) for v, t in vals if v is not None]
    if not vals:
        return None, False
    v, t = vals[-1] if last else vals[0]
    return v, not re.search(r"[.,]\d", t)


TotalReading = tuple[float, str, bool]   # (value, basis, a lost decimal point is possible)

# the order in which a total's basis is trusted when nothing checks it
BASIS_PREFERENCE = ["excl", "incl less VAT", "incl less VAT rate", "total less VAT", "total",
                    "incl, no VAT shown", "incl at 15% VAT"]


def _total_readings(ex: float | None, inc: float | None, t: float | None, v: float | None, r: float | None,
                    pointless: set[str]) -> list[TotalReading]:
    """Every reading of the line's excl. VAT total that its printed columns allow."""
    totals: list[TotalReading] = []

    def add(value: float, basis: str, source: str):
        totals.append((value, basis, source in pointless))

    if ex is not None:
        add(ex, "excl", "line_total_excl")
    if inc is not None and v is not None:
        add(round(inc - v, 2), "incl less VAT", "line_total_incl")
    if inc is not None and r is not None:
        add(round(inc / (1 + r), 2), "incl less VAT rate", "line_total_incl")
    if t is not None:
        add(t, "total", "line_total")
        if v is not None:
            add(round(t - v, 2), "total less VAT", "line_total")
    if inc is not None and v is None and r is None:
        add(inc, "incl, no VAT shown", "line_total_incl")
        add(round(inc / (1 + VAT_RATE), 2), "incl at 15% VAT", "line_total_incl")
    return totals


def _price_total_pairs(p: float | None, totals: list[TotalReading], pointless: set[str]):
    """(price, note, total, note) for every price and total, as printed and with a lost decimal point restored."""
    prices = []
    if p is not None:
        prices.append((p, ""))
        if "unit_price" in pointless:
            prices.append((round(p / 100, 4), "decimal point missing from unit price"))
    for price, pnote in prices:
        for total, _basis, repairable in totals:
            options = [(total, "")]
            if repairable:
                options.append((round(total / 100, 2), "decimal point missing from line total"))
            for tot, tnote in options:
                yield price, pnote, tot, tnote


def _quantity_readings(q: float | None):
    yield q, ""
    if q is not None and q >= 10 and float(q).is_integer():
        # a handwritten delivery tick after the figure read as one more digit: 1 becomes 11, 2 becomes 21
        yield float(int(q) // 10), "delivery tick read as a digit after the quantity"


def _reading_that_adds_up(q: float, p: float, d: float, totals: list[TotalReading], pointless: set[str],
                          notes: list[str]) -> tuple[float, float, float] | None:
    """(quantity, price, total) of the first reading, as printed or repaired, whose figures multiply out."""
    for qv, qnote in _quantity_readings(q):
        for price, pnote, tot, tnote in _price_total_pairs(p, totals, pointless):
            if _close(qv * price * (1 - d), tot):
                notes += [n for n in (qnote, pnote, tnote) if n]
                return qv, price, tot
    return None


def _price_is_line_amount(q: float, p: float, d: float, t: float | None, inc: float | None, v: float | None,
                          notes: list[str]) -> tuple[float, float, float | None] | None:
    """(total, price, VAT) when the "price" column turns out to hold the line's amount."""
    # the "price" column holds the line's amount excl. VAT, beside a total incl. VAT (The Ice Chain)
    for tot in (x for x in (t, inc) if x is not None):
        if _close(p * (1 + VAT_RATE), tot):
            ex, p = p, round(p / (q * (1 - d)), 4)
            if v is None:
                v = round(tot - ex, 2)
            notes.append("price column holds the line amount excl. VAT; unit price derived")
            return ex, p, v
    return None


def _derived_quantity(p: float, d: float, totals: list[TotalReading], pointless: set[str],
                      notes: list[str]) -> tuple[float, float, float] | None:
    """(quantity, price, total) when total / price comes out whole, or failing that in halves."""
    for step in (1.0, 0.5):
        for price, pnote, tot, tnote in _price_total_pairs(p, totals, pointless):
            if price <= 0:
                continue
            qq = tot / (price * (1 - d))
            if qq >= step and abs(qq / step - round(qq / step)) < 0.01:
                notes += [n for n in (pnote, tnote) if n] + ["quantity not printed: derived from total / price"]
                return round(qq, 3), price, tot
    return None


def _preferred_total(totals: list[TotalReading]) -> TotalReading:
    return min(totals, key=lambda x: BASIS_PREFERENCE.index(x[1]))


def _derived_price(q: float, d: float, totals: list[TotalReading], notes: list[str]) -> tuple[float, float]:
    """(price, total) for a line whose price is not printed."""
    # Nothing to check the total against, so choose its basis by what the columns say. An unlabelled
    # "Total" beside a VAT amount column includes that VAT (Vassco): taking it as excl. overstated
    # every Vassco line by exactly 15%.
    ex, basis, _ = _preferred_total(totals)
    p = round(ex / (q * (1 - d)), 4)
    notes.append("unit price not printed: derived from total / quantity"
                 + ("" if basis in ("excl", "total") else f" ({basis})"))
    return p, ex


def _derived_vat(inc: float | None, r: float | None, t: float | None, ex: float | None) -> float | None:
    if inc is not None and ex is not None:
        return round(inc - ex, 2)
    if r is not None and ex is not None:
        return round(ex * r, 2)
    if t is not None and ex is not None and abs(t - ex) > 0.005:
        return round(t - ex, 2)
    return None


def reconcile(c: dict, notes: list[str]) -> dict:
    """Make quantity x price x (1 - discount) agree with the line total, or say it does not."""
    q, p = c.get("quantity"), c.get("unit_price")
    d = c.get("discount_pct") or 0.0
    ex, inc, t = c.get("line_total_excl"), c.get("line_total_incl"), c.get("line_total")
    v, r = c.get("vat"), c.get("vat_rate")
    pointless: set[str] = c.get("pointless", set())
    totals = _total_readings(ex, inc, t, v, r, pointless)

    verified = False
    if q is not None and p is not None and totals:
        found = _reading_that_adds_up(q, p, d, totals, pointless, notes)
        if found is not None:
            (q, p, ex), verified = found, True
        elif q:
            amount = _price_is_line_amount(q, p, d, t, inc, v, notes)
            if amount is not None:
                (ex, p, v), verified = amount, True
    elif q is None and p is not None and totals and c.get("derive_quantity", True):
        found = _derived_quantity(p, d, totals, pointless, notes)
        if found is not None:
            (q, p, ex), verified = found, True
    elif p is None and q and totals:
        p, ex = _derived_price(q, d, totals, notes)
        verified = True

    if not verified:
        if totals and ex is None:
            # no reading reconciles: fall back on what the columns say the total is, as for a missing price
            ex = _preferred_total(totals)[0]
        if q is not None or p is not None:
            notes.append("line does not reconcile: quantity x price does not equal the line total")

    if v is None:
        v = _derived_vat(inc, r, t, ex)
    return {"quantity": q, "unit_price": p, "line_total_excl": ex, "vat": v, "verified": verified}


def build_line(raw: RawLine, height: float, layout: Layout) -> tuple[Line, list[str]]:
    cells = raw.cells
    pointless: set[str] = set()

    def money(name: str) -> float | None:
        v, no_point = _cell(cells.get(name, []), height)
        if v is not None and no_point:
            pointless.add(name)
        return v

    quantity, _ = _cell(cells.get("quantity", []), height, last=False)
    unit_parts = []
    for w in cells.get("unit", []):
        m = UNIT_WITH_NUMBER.match(w.text.strip())
        if m:
            # quantity fused to its unit by the engine: "2.205Kilogram"
            if quantity is None:
                quantity = parse_amount(m.group(1))
            unit_parts.append(m.group(2))
        elif parse_amount(w.text) is None:
            unit_parts.append(w.text)

    vat_rate = cell_percent(cells.get("vat", []))
    fields = {
        "quantity": quantity,
        "unit_price": money("unit_price"),
        "discount_pct": cell_percent(cells.get("discount_pct", [])),
        "line_total_excl": money("line_total_excl"),
        "line_total_incl": money("line_total_incl"),
        "line_total": money("line_total"),
        "vat": None if vat_rate is not None else money("vat"),
        "vat_rate": vat_rate,
        "derive_quantity": layout.derive_quantity,
        "pointless": pointless,
    }
    notes: list[str] = []
    r = reconcile(fields, notes)

    desc_words = cells.get("description", []) + raw.extra_description
    description = " ".join(w.text for w in desc_words).strip()
    used = sorted([w for ws in cells.values() for w in ws] + raw.extra_description, key=lambda w: (w.top, w.left))
    pack = parse_pack(description)
    code = " ".join(w.text for w in cells.get("code", [])) or None
    line = Line(
        description=description,
        quantity=r["quantity"], unit=" ".join(unit_parts) or None, pack=pack.raw if pack else None,
        unit_price=r["unit_price"], line_total=r["line_total_excl"], vat=r["vat"],
        stock_code=code, raw=row_text(used),
        conf=round(sum(w.conf for w in used) / len(used), 1) if used else 0.0,
    )
    return line, notes


def _walk_table(body: list[list[Word]], bounds, height: float) -> tuple[list[RawLine], list[tuple[int, list[Word]]], int]:
    """(lines, description-only rows with their tops, index of the row that ends the table)."""
    raws: list[RawLine] = []
    loose: list[tuple[int, list[Word]]] = []
    end = len(body)
    for k, row in enumerate(body):
        if totals_label(row) or is_footer(row):
            end = k
            break
        if raws and row_top(row) - raws[-1].bottom > TABLE_GAP_STOP * height:
            end = k
            break
        cells = assign(row, bounds)
        has_money = any(_cell(cells.get(f, []), height)[0] is not None for f in MONEY_FIELDS)
        if has_money:
            raws.append(RawLine(cells, row_top(row), row_bottom(row), []))
        elif cells.get("description"):
            loose.append((row_top(row), cells["description"]))
    return raws, loose, end


def _attach_descriptions(raws: list[RawLine], loose: list[tuple[int, list[Word]]], layout: Layout) -> None:
    for top, desc in loose:
        if not raws:
            continue
        if layout.description_offset == "above":
            after = [l for l in raws if l.top >= top]
            target = after[0] if after else raws[-1]
        elif layout.description_offset == "auto":
            target = min(raws, key=lambda l: abs(l.top - top))
        else:   # below: a wrapped description continues the line above it
            before = [l for l in raws if l.top <= top]
            target = before[-1] if before else raws[0]
        target.extra_description.extend(desc)


def _drop_stray_rows(built: list[tuple[Line, list[str]]]) -> tuple[list[tuple[Line, list[str]]], int]:
    """(the lines kept, how many rows were dropped as not being invoice lines)."""
    dropped = 0
    # figures below the table (the invoice total, bank details) read as lines: no quantity, no price, no code
    while built and built[-1][0].quantity is None and built[-1][0].unit_price is None and not built[-1][0].stock_code:
        built.pop()
        dropped += 1
    kept = []
    for line, notes in built:
        if any(f in line.description.lower() for f in FOOTER_WORDS):
            dropped += 1
            continue
        if not line.description and line.line_total is None:
            # a stray figure with neither wording nor a total is a misread column (a stock code read into
            # the quantity column when the code header was missed), not an invoice line
            dropped += 1
            continue
        kept.append((line, notes))
    return kept, dropped


def parse_page(ocr: OCRResult, layout: Layout) -> tuple[list[tuple[Line, list[str]]], dict[str, float], list[str]]:
    words = ocr.words
    height = typical_height(words)
    rows = group_rows(words)
    warnings: list[str] = []
    header = find_header(rows, layout, height)
    if header is None:
        return [], read_totals(rows, height), ["no line-item table header found"]
    spans, last_header_row = header
    bounds = column_bounds(spans)
    body = rows[last_header_row + 1:]

    raws, loose, end = _walk_table(body, bounds, height)
    _attach_descriptions(raws, loose, layout)
    kept, dropped = _drop_stray_rows([build_line(raw, height, layout) for raw in raws])
    if dropped:
        warnings.append(f"{dropped} row(s) outside the table read as figures and ignored")
    totals = read_totals(body[end:], height) or read_totals(body, height)
    return kept, totals, warnings


def repeats(a: list[tuple[Line, list[str]]], b: list[tuple[Line, list[str]]]) -> bool:
    """Do two pages of one invoice carry the same lines (one sheet photographed twice)?

    Stock codes are compared where there are enough of them: two photographs of one sheet are read
    slightly differently, so their figures can disagree while the printed codes agree.
    """
    ca = Counter(l.stock_code for l, _ in a if l.stock_code)
    cb = Counter(l.stock_code for l, _ in b if l.stock_code)
    if sum(ca.values()) >= MIN_CODES_FOR_REPEAT and sum(cb.values()) >= MIN_CODES_FOR_REPEAT:
        smaller = min(sum(ca.values()), sum(cb.values()))
        return sum((ca & cb).values()) >= DUPLICATE_PAGE_OVERLAP * smaller
    ta = Counter(round(l.line_total, 2) for l, _ in a if l.line_total is not None)
    tb = Counter(round(l.line_total, 2) for l, _ in b if l.line_total is not None)
    smaller = min(sum(ta.values()), sum(tb.values()))
    return smaller >= 2 and sum((ta & tb).values()) >= DUPLICATE_PAGE_OVERLAP * smaller


def invoice_totals(page_totals: list[dict[str, float]]) -> dict[str, float]:
    """The invoice's totals, taken whole from one page and never mixed across pages.

    Prefer the last page whose totals add up (excl. + VAT = incl.), then the last page stating a total
    excl. VAT, then the last page with any totals. Vassco's second page often carries a partial totals
    row (VAT 211.18 against 1,618.95) while page 1 prints a complete, consistent set; mixing the two
    builds totals no page printed, and "last page wins" alone would discard the consistent set.
    """
    printed = [t for t in page_totals if t]
    consistent = [t for t in printed if all(k in t for k in ("excl", "vat", "incl"))
                  and abs(t["excl"] + t["vat"] - t["incl"]) <= max(ADDS_UP_TOLERANCE, 0.001 * abs(t["incl"]))]
    with_excl = [t for t in printed if "excl" in t]
    for group in (consistent, with_excl, printed):
        if group:
            return dict(group[-1])
    return {}


def parse_invoice(pages: list[tuple[Page, OCRResult | None]], layout: Layout | None = None) -> InvoiceRecord:
    first = pages[0][0]
    layout = layout or for_supplier(first.supplier)
    warnings: list[str] = []

    parsed: list[tuple[Page, list, dict, list[str]]] = []
    for page, ocr in pages:
        if ocr is None:
            warnings.append(f"page {page.page_no}: no OCR result")
            continue
        entries, page_totals, page_warnings = parse_page(ocr, layout)
        parsed.append((page, entries, page_totals, page_warnings))

    keep = [True] * len(parsed)
    for j in range(len(parsed)):
        for k in range(j):
            if keep[k] and keep[j] and parsed[j][1] and parsed[k][1] and repeats(parsed[j][1], parsed[k][1]):
                pj, pk = parsed[j][0].page_no, parsed[k][0].page_no
                if len(parsed[j][1]) > len(parsed[k][1]):
                    keep[k] = False
                    warnings.append(f"page {pk}: repeated by page {pj}, which read more lines; page {pk} skipped")
                else:
                    keep[j] = False
                    warnings.append(f"page {pj}: repeats the lines of page {pk}; skipped as a second photograph")

    lines: list[Line] = []
    kept_totals: list[dict[str, float]] = []
    for (page, entries, page_totals, page_warnings), kept in zip(parsed, keep):
        if not kept:
            continue
        warnings.extend(f"page {page.page_no}: {w}" for w in page_warnings)
        for line, notes in entries:
            lines.append(line)
            warnings.extend(f"line {len(lines)}: {n}" for n in notes)
        kept_totals.append(page_totals)

    totals = invoice_totals(kept_totals)
    excl, vat, incl = totals.get("excl"), totals.get("vat"), totals.get("incl")
    if excl is None and incl is not None and vat is not None:
        excl = round(incl - vat, 2)
        warnings.append("total excl. VAT not printed: taken as total incl. less VAT")
    known = [l.line_total for l in lines if l.line_total is not None]
    lines_sum = round(sum(known), 2) if known else None
    adds_up = None
    if lines_sum is not None and excl is not None:
        adds_up = abs(lines_sum - excl) <= max(ADDS_UP_TOLERANCE, 0.001 * abs(excl))
        if not adds_up:
            warnings.append(f"lines sum to R{lines_sum:,.2f} but the invoice states R{excl:,.2f} excl. VAT "
                            f"(difference R{lines_sum - excl:,.2f}); recorded, not corrected")
    return InvoiceRecord(
        invoice_id=first.invoice_id or f"page{first.page_no}", supplier=first.supplier, layout=layout.key,
        document_no=first.document_no, date=first.date, pages=[p.page_no for p, _ in pages], lines=lines,
        stated_total_excl=excl, stated_vat=vat, stated_total_incl=incl, lines_sum_excl=lines_sum,
        adds_up=adds_up, warnings=warnings,
    )
