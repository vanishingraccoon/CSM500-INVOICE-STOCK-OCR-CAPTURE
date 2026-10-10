"""Run the error checks over parsed and normalised invoices.

Duplicates are looked for among the scanned invoices themselves: the same invoice photographed
twice, or two scans of one delivery under different references.

Prices are checked per stock item and per base unit, so a converted line is compared like for
like with what Pilot has recorded for that item. History comes from Pilot's purchase lines; the
median is robust to the keying errors that history contains. Lines the normaliser refused have no
base unit and are not price-checked: they already carry a refusal a person will see.

    python -m checks.run            # reads data/out/invoices.json and normalised.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import config
from checks.duplicates import InvoiceSummary, find_duplicates
from checks.prices import PricePoint, find_price_anomalies
from models import Flag, InvoiceRecord


def scan_summaries(records: list[InvoiceRecord]) -> list[InvoiceSummary]:
    out = []
    for r in records:
        total = r.stated_total_excl if r.stated_total_excl is not None else r.lines_sum_excl
        signature = tuple(sorted(((l.description.lower(), l.quantity) for l in r.lines),
                                 key=lambda x: (x[0], -1.0 if x[1] is None else x[1])))
        out.append(InvoiceSummary(r.invoice_id, r.supplier, r.document_no, total, r.date, "scan", signature))
    return out


def price_points(normalised: list[dict]) -> list[PricePoint]:
    points = []
    for n in normalised:
        if n.get("refusal") or not n.get("item_code") or n.get("base_quantity") is None:
            continue
        cost = n["line"].get("line_total")
        if cost is None:
            continue
        points.append(PricePoint(n["item_code"], n["base_quantity"], cost, n["invoice_id"], n["line_index"]))
    return points


def history_points(purchase_lines, stock) -> list[PricePoint]:
    by_desc = defaultdict(list)
    for item in stock.items:
        by_desc[item.description.lower()].append(item.code)
    points = []
    for l in purchase_lines:
        for code in by_desc.get(l.item.lower(), []):
            points.append(PricePoint(code, l.quantity, l.cost, f"pilot:{l.supplier_name}|{l.reference}|{l.date}"))
    return points


def run(records: list[InvoiceRecord], normalised: list[dict], purchase_lines, stock) -> list[Flag]:
    flags = find_duplicates(scan_summaries(records))
    flags += find_price_anomalies(price_points(normalised), history_points(purchase_lines, stock))
    for r in records:
        if r.adds_up is False:
            flags.append(Flag("total_mismatch", r.invoice_id, None, "medium", 0.9,
                              f"The invoice's own lines sum to R{r.lines_sum_excl:,.2f} but it states "
                              f"R{r.stated_total_excl:,.2f} excl. VAT. Check the paper before keying.",
                              {"lines_sum": r.lines_sum_excl, "stated": r.stated_total_excl}))
    return flags


def main(argv=None):
    from normaliser.stock import default_stock_file, load_stock
    from tools.pilot import load_purchasing_report

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--invoices", default=str(config.OUT / "invoices.json"))
    ap.add_argument("--normalised", default=str(config.OUT / "normalised.json"))
    ap.add_argument("--out", default=str(config.OUT / "flags.json"))
    a = ap.parse_args(argv)
    records = [InvoiceRecord.from_dict(d) for d in json.loads(Path(a.invoices).read_text(encoding="utf-8"))]
    normalised = json.loads(Path(a.normalised).read_text(encoding="utf-8")) if Path(a.normalised).exists() else []
    flags = run(records, normalised, load_purchasing_report(), load_stock(default_stock_file()))
    Path(a.out).write_text(json.dumps([f.to_dict() for f in flags], indent=1), encoding="utf-8")
    print(dict(Counter(f.kind for f in flags)))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
