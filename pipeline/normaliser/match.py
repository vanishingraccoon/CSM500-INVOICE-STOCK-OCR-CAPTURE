"""Which Pilot stock item does an invoice line belong to? Scored, never assumed.

Supplier wording and venue wording differ ("Bread - Super Slice White" against "bread superloaf
white"), and some supplier products map onto two stock items the venue keeps apart ("staff bread
white" and "bread superloaf white" for the same loaf). So each candidate gets a score, and the
decision to accept, or to refuse as no_item_match / multiple_item_match, is taken on the scores.

Evidence used, strongest first:
  alias     this supplier's wording already tied to a stock item on an earlier invoice, learned by
            lining up scanned lines with the lines a manager keyed for the same invoice (build_aliases).
            When evaluating, aliases must come only from invoices other than the one being scored.
  history   stock items this supplier has been invoiced for in Pilot before. A strong prior: of 1,145
            Eastgate items, Aero Power has only ever supplied four.
  text      fuzzy similarity of the normalised wording.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from normaliser.stock import StockFile, StockItem
from normaliser.units import MULTI_RE, SINGLE_RE, parse_pack

HISTORY_BONUS = 12.0
SIZE_BONUS = 8.0
ALIAS_SCORE = 200.0     # above any history-boosted score, so a known wording always ranks first
TOP_N = 5

STOPWORDS = {"nc", "the", "and", "of", "x", "pack", "pkt", "each", "ea", "unit", "units", "per", "box", "case",
             "tin", "tins", "bag", "bags", "tub", "bottle", "btl", "jar", "roll", "pcs", "pc", "s", "a", "for", "with"}


def normalise_text(text: str) -> str:
    t = text.lower()
    t = MULTI_RE.sub(" ", t)
    t = SINGLE_RE.sub(" ", t)
    t = t.replace("&", " and ")
    t = re.sub(r"\(\s*\d+\s*'?s\s*\)", " ", t)         # (12's)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = []
    for w in t.split():
        if w in STOPWORDS or w.isdigit():
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def text_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 0.6 * fuzz.token_set_ratio(a, b) + 0.4 * fuzz.token_sort_ratio(a, b)


@dataclass
class Candidate:
    code: str
    name: str
    score: float
    method: str                    # alias | history | text
    text_score: float = 0.0


@dataclass
class Matcher:
    stock: StockFile
    history: dict[str, set[str]] = field(default_factory=dict)      # supplier key -> stock codes invoiced before
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)  # (supplier key, normalised wording) -> code

    def __post_init__(self):
        self._items = self.stock.items
        self._norm = {i.code: normalise_text(i.description) for i in self._items}
        self._packs = {i.code: parse_pack(i.description) for i in self._items}
        self._by_code = self.stock.by_code()

    def candidates(self, supplier: str | None, description: str) -> list[Candidate]:
        nd = normalise_text(description)
        alias_code = self.aliases.get((supplier or "", nd)) if nd else None
        known = self.history.get(supplier or "", set())
        pack = parse_pack(description)
        scored = []
        for item in self._items:
            ts = text_score(nd, self._norm[item.code])
            if item.code == alias_code:
                scored.append(Candidate(item.code, item.name, ALIAS_SCORE, "alias", ts))
                continue
            if ts <= 0:
                continue
            # The history bonus is not capped: capping it at 99 tied every pair of similar items a
            # supplier delivers (sauce bbq and sauce smokey bbq both scored 99), hiding which reads closer.
            score = ts + self._size_fit(pack, item)
            method = "text"
            if item.code in known:
                score, method = score + HISTORY_BONUS, "history"
            scored.append(Candidate(item.code, item.name, round(score, 2), method, ts))
        scored.sort(key=lambda c: (-c.score, -c.text_score))
        return scored[:TOP_N]

    def _size_fit(self, pack, item: StockItem) -> float:
        """Separate same-name items by size: 'Clr Water (12 x 1L)' is the 1 l water, not the 500 ml one.

        Only sizes in the same dimension are compared. A mass-versus-volume mismatch is deliberately not
        penalised here: steering 'Jalapeno Mayo (1L)' away from the kg-held mayonnaise onto some litre-held
        lookalike would be a silent wrong guess; the conversion step refuses the conflict instead.
        """
        if pack is None or pack.amount is None:
            return 0.0
        own = self._packs.get(item.code)
        if own is not None and own.amount is not None and own.dimension == pack.dimension:
            return SIZE_BONUS if abs(own.amount - pack.amount) <= 0.02 * pack.amount else -SIZE_BONUS
        base = item.base
        if base.content is not None and base.content_dimension == pack.dimension:
            return SIZE_BONUS if abs(base.content - pack.amount) <= 0.02 * pack.amount else -SIZE_BONUS
        return 0.0


def history_from_purchases(lines, stock: StockFile) -> dict[str, set[str]]:
    """Supplier key -> stock codes, from Pilot purchase lines (item names carry no unit suffix)."""
    by_desc = defaultdict(list)
    for item in stock.items:
        by_desc[item.description.lower()].append(item.code)
    out: dict[str, set[str]] = defaultdict(set)
    for l in lines:
        if l.supplier_key:
            for code in by_desc.get(l.item.lower(), []):
                out[l.supplier_key].add(code)
    return dict(out)


def price_medians(lines, stock: StockFile) -> dict[str, float]:
    """Stock code -> median unit cost per base unit keyed in Pilot. The median shrugs off keying errors."""
    from statistics import median
    by_desc = defaultdict(list)
    for item in stock.items:
        by_desc[item.description.lower()].append(item.code)
    prices: dict[str, list[float]] = defaultdict(list)
    for l in lines:
        if l.quantity and l.quantity > 0 and l.cost > 0:
            for code in by_desc.get(l.item.lower(), []):
                prices[code].append(l.cost / l.quantity)
    return {c: median(v) for c, v in prices.items() if len(v) >= 2}


def build_aliases(pairs: Iterable[tuple[str, str, str]]) -> dict[tuple[str, str], str]:
    """(supplier key, invoice wording, stock code) triples -> alias table.

    A wording is only an alias if it consistently meant one stock item: wording tied to two
    different items (the Super Slice White loaf posted as staff bread one week and superloaf the
    next) is left out, because learning it would hide a genuine ambiguity.
    """
    seen: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for supplier, wording, code in pairs:
        nd = normalise_text(wording)
        if nd:      # wording with nothing left after normalising ("12 x 1kg") would match every uninformative line
            seen[(supplier, nd)][code] += 1
    return {k: c.most_common(1)[0][0] for k, c in seen.items() if len(c) == 1}


def align_lines(scan_lines, pilot_lines, tolerance: float = 0.02) -> list[tuple[int, int]]:
    """Pair scanned lines with keyed lines of the same invoice by equal cost, each used once.

    Cost is the one field both readings share exactly: descriptions differ by design and quantities
    differ whenever a conversion happened. Lines whose cost is not unique on the invoice are only
    paired when their text agrees best.
    """
    pairs, used = [], set()
    for i, s in enumerate(scan_lines):
        if s.line_total is None:
            continue
        options = [j for j, p in enumerate(pilot_lines) if j not in used and abs(p.cost - s.line_total) <= tolerance]
        if len(options) > 1:
            ns = normalise_text(s.description)
            options.sort(key=lambda j: -text_score(ns, normalise_text(pilot_lines[j].item)))
        if options:
            used.add(options[0])
            pairs.append((i, options[0]))
    return pairs


# Charges printed as invoice lines. The Eastgate Stock Valuation export holds no delivery or transport item:
# managers key these against charge accounts ("delivery vat", "## delivery ##", "transport"), not stock.
CHARGE_RE = re.compile(r"\b(deliver(y|ies)|transport|courier|freight|fuel levy|handling fee)\b", re.IGNORECASE)


def is_charge(description: str) -> bool:
    return bool(description and CHARGE_RE.search(description))
