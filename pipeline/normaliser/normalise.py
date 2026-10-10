"""Normalise parsed invoice lines: match each to a stock item, convert to its base unit, or refuse.

    python -m normaliser.normalise            # reads data/out/invoices.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import config
from models import InvoiceRecord, Line, NormalisedLine, Refusal
from normaliser.convert import convert
from normaliser.match import Matcher, history_from_purchases, is_charge, price_medians
from normaliser.stock import default_stock_file, load_stock

ACCEPT = 75.0          # a candidate must score at least this to be used
MARGIN = 8.0           # ... and beat the runner-up by this much, unless the runner-up is weak
REFUSE_BELOW = 55.0    # nothing this close is a match at all
NEAR_EXACT = 97.0      # text score of an all-but-identical wording ...
NEAR_EXACT_LEAD = 3.0  # ... accepted when it reads closer than the runner-up by at least this


def decide(candidates) -> tuple[object | None, str | None, str]:
    if not candidates or candidates[0].score < REFUSE_BELOW:
        best = f" (closest: '{candidates[0].name}', score {candidates[0].score:.0f})" if candidates else ""
        return None, Refusal.NO_ITEM_MATCH.value, "no stock item resembles this line" + best
    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    if top.method == "alias":
        return top, None, f"known wording for '{top.name}' from earlier invoices"
    if top.score >= ACCEPT and (second is None or top.score - second.score >= MARGIN or second.score < REFUSE_BELOW):
        return top, None, f"matched '{top.name}' by {top.method} (score {top.score:.0f})"
    if (top.score >= ACCEPT and second is not None and top.method == second.method
            and top.text_score >= NEAR_EXACT and top.text_score - second.text_score >= NEAR_EXACT_LEAD):
        # 'Sauce Sweet Chilli Soya (2L)' reads as 'sauce sweet chilli soya' exactly; 'sauce sweet chilli' is a subset
        return top, None, (f"matched '{top.name}': the wording matches it almost exactly, "
                           f"and '{second.name}' less closely")
    if (top.score >= ACCEPT and top.method == "history" and second is not None and second.method != "history"
            and top.text_score >= second.text_score):
        # an item this supplier has actually delivered outranks a lookalike it has never delivered
        return top, None, (f"matched '{top.name}' by history (score {top.score:.0f}); the close alternative "
                           f"'{second.name}' has never been supplied by this supplier")
    if second is not None and top.score - second.score < MARGIN:
        return None, Refusal.MULTIPLE_ITEM_MATCH.value, (
            f"'{top.name}' ({top.score:.0f}) and '{second.name}' ({second.score:.0f}) are equally plausible")
    return None, Refusal.NO_ITEM_MATCH.value, f"best candidate '{top.name}' scores only {top.score:.0f}"


def normalise_line(line: Line, supplier: str | None, matcher: Matcher, medians: dict[str, float]) -> NormalisedLine:
    if is_charge(line.description):
        return NormalisedLine(line=line, refusal=Refusal.NON_STOCK_CHARGE.value,
                              reason="a delivery or transport charge, not a stock item: it belongs on a charge "
                                     "account in Pilot (such as delivery vat), with no stock quantity")
    candidates = matcher.candidates(supplier, line.description)
    alternatives = [{"code": c.code, "name": c.name, "score": round(c.score, 1), "method": c.method} for c in candidates]
    chosen, refusal, reason = decide(candidates)
    out = NormalisedLine(line=line, alternatives=alternatives)
    if chosen is None:
        out.refusal, out.reason = refusal, reason
        return out
    item = matcher._by_code[chosen.code]
    out.item_code, out.item_name, out.base_unit = item.code, item.name, item.base.unit
    out.match_score, out.match_method = round(chosen.score, 1), chosen.method
    conv = convert(line, item, medians.get(item.code))
    if not conv.ok:
        out.refusal, out.reason = conv.refusal, f"{reason}; {conv.reason}"
        return out
    out.factor, out.conversion_basis = conv.factor, conv.basis
    out.base_quantity = round(line.quantity * conv.factor, 4)
    out.reason = f"{reason}; {conv.reason}"
    return out


def build_matcher(purchase_lines=None, stock=None) -> tuple[Matcher, dict[str, float]]:
    from tools.pilot import load_purchasing_report
    stock = stock or load_stock(default_stock_file())
    purchase_lines = purchase_lines if purchase_lines is not None else load_purchasing_report()
    return Matcher(stock, history_from_purchases(purchase_lines, stock)), price_medians(purchase_lines, stock)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--invoices", default=str(config.OUT / "invoices.json"))
    ap.add_argument("--out", default=str(config.OUT / "normalised.json"))
    a = ap.parse_args(argv)
    records = [InvoiceRecord.from_dict(d) for d in json.loads(Path(a.invoices).read_text(encoding="utf-8"))]
    matcher, medians = build_matcher()
    out, outcome = [], Counter()
    for r in records:
        for idx, line in enumerate(r.lines):
            n = normalise_line(line, r.supplier, matcher, medians)
            outcome["converted" if n.converted else n.refusal] += 1
            out.append({"invoice_id": r.invoice_id, "line_index": idx, **n.to_dict()})
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(dict(outcome))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
