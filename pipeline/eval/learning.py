"""Does item matching improve as invoices are reviewed? A replay of August in date order.

For every scanned invoice that was tied to what a manager keyed in Pilot, taken in invoice-date order:
  1. normalise its lines with whatever the system has learned so far,
  2. score each line against the stock item the manager chose (lines paired by equal cost),
  3. then learn from that invoice: its supplier wording becomes an alias for the chosen item,
     as it would once a person had reviewed it.

The same replay without step 3 is the baseline. Supplier history and price medians come only from
Pilot lines dated before the binder month, so nothing the replay scores has leaked into what it
matches with.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from normaliser.match import (
    Matcher,
    align_lines,
    build_aliases,
    history_from_purchases,
    price_medians,
)
from normaliser.normalise import normalise_line

HISTORY_BEFORE = "2026-08-01"


@dataclass
class Tally:
    aligned: int = 0          # lines paired with a keyed line whose item is a stock item
    agree: int = 0            # system chose the manager's item
    disagree: int = 0         # system chose a different item
    refused_match: int = 0    # system declined to choose (no match / multiple match)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["agree_rate"] = round(self.agree / self.aligned, 3) if self.aligned else None
        d["wrong_rate"] = round(self.disagree / self.aligned, 3) if self.aligned else None
        d["refusal_rate"] = round(self.refused_match / self.aligned, 3) if self.aligned else None
        return d


@dataclass
class Replay:
    baseline: Tally = field(default_factory=Tally)
    learning: Tally = field(default_factory=Tally)
    by_week: dict = field(default_factory=lambda: defaultdict(lambda: {"baseline": Tally(), "learning": Tally()}))
    aliases_learned: int = 0


def keyed_lines(purchase_lines) -> dict[tuple, list]:
    out = defaultdict(list)
    for l in purchase_lines:
        out[(l.supplier_name, l.reference, l.date)].append(l)
    return out


def _score(tally: Tally, n, codes: set[str]) -> None:
    tally.aligned += 1
    if n.item_code is None:
        tally.refused_match += 1
    elif n.item_code in codes:
        tally.agree += 1
    else:
        tally.disagree += 1


def replay(records: dict, matches: list[dict], purchase_lines, stock) -> Replay:
    before = [l for l in purchase_lines if l.date < HISTORY_BEFORE]
    history = history_from_purchases(before, stock)
    medians = price_medians(before, stock)
    by_desc = defaultdict(set)
    for item in stock.items:
        by_desc[item.description.lower()].add(item.code)
    keyed = keyed_lines(purchase_lines)

    plain = Matcher(stock, history=history)
    learner = Matcher(stock, history=history)
    pairs_seen: list[tuple[str, str, str]] = []
    result = Replay()

    ordered = sorted((m for m in matches if m["status"] in ("matched", "matched_on_total") and m["invoice_id"] in records),
                     key=lambda m: (records[m["invoice_id"]].date or m.get("pilot_date") or "", m["invoice_id"]))
    for m in ordered:
        rec = records[m["invoice_id"]]
        pl = keyed.get((m["pilot_supplier"], m["pilot_reference"], m["pilot_date"]), [])
        if not pl:
            continue
        week = _week(rec.date or m["pilot_date"])
        new_pairs = []
        for i, j in align_lines(rec.lines, pl):
            codes = by_desc.get(pl[j].item.lower(), set())
            if not codes:
                continue
            line = rec.lines[i]
            n_plain = normalise_line(line, rec.supplier, plain, medians)
            n_learn = normalise_line(line, rec.supplier, learner, medians)
            for tally, n in ((result.baseline, n_plain), (result.by_week[week]["baseline"], n_plain),
                             (result.learning, n_learn), (result.by_week[week]["learning"], n_learn)):
                _score(tally, n, codes)
            if len(codes) == 1 and line.description:
                new_pairs.append((rec.supplier or "", line.description, next(iter(codes))))
        # the invoice has now been reviewed: its wording is known from here on
        pairs_seen.extend(new_pairs)
        learner.aliases = build_aliases(pairs_seen)
    result.aliases_learned = len(learner.aliases)
    return result


def _week(date: str | None) -> str:
    if not date or len(date) < 10:
        return "unknown"
    day = int(date[8:10])
    return f"{date[:7]} week {min(4, (day - 1) // 7 + 1)}"


def summary(r: Replay) -> dict:
    return {
        "baseline": r.baseline.as_dict(),
        "learning": r.learning.as_dict(),
        "aliases_learned": r.aliases_learned,
        "by_week": {w: {k: t.as_dict() for k, t in v.items()} for w, v in sorted(r.by_week.items())},
    }
