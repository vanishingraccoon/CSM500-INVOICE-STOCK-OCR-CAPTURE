"""Tie each scanned invoice to what a manager keyed into Pilot for it.

Pilot source: the Purchasing Report, which carries each invoice's date and keyed totals.
Matching, strongest first:

  matched            same supplier, references share a key (refs.ref_keys)
  matched_on_total   same supplier, no reference match, keyed cost equals the scan's total excl. VAT
                     to the cent and the dates are within a few days. Weaker evidence, reported
                     separately. Needed because references are sometimes keyed with a digit dropped
                     (Rand Dairy: 600032506 beside 6000325539)
  scan_only          scanned but no Pilot entry found: never captured, or keyed beyond recognition
  pilot_only         keyed into Pilot but no scan found: missing from the binder, or unreadable

A Pilot invoice is matched at most once. These matches measure agreement with
manager-entered records; they are not independent, blind ground truth.

    python -m tools.groundtruth
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from pathlib import Path

import config
from models import InvoiceRecord, Page
from refs import primary_key, ref_keys
from tools.pilot import PurchaseLine, load_purchasing_report

TOTAL_TOLERANCE = 0.02
MAX_DAYS_APART = 3


@dataclass
class PilotInvoice:
    supplier_name: str
    supplier_key: str | None
    reference: str
    date: str
    lines: list[PurchaseLine] = field(default_factory=list)

    @property
    def keys(self) -> frozenset[str]:
        return ref_keys(self.reference)

    @property
    def cost(self) -> float:
        return round(sum(l.cost for l in self.lines), 2)


@dataclass
class Scan:
    invoice_id: str
    supplier: str | None
    document_no: str | None
    date: str | None
    pages: list[int]
    total_excl: float | None = None     # stated on the invoice, from the parser
    lines_sum: float | None = None


@dataclass
class Match:
    status: str                            # matched | matched_on_total | scan_only | pilot_only
    invoice_id: str | None = None
    supplier: str | None = None
    scan_reference: str | None = None
    scan_date: str | None = None
    pages: list[int] = field(default_factory=list)
    scan_total_excl: float | None = None
    pilot_supplier: str | None = None
    pilot_reference: str | None = None
    pilot_date: str | None = None
    pilot_lines: int = 0
    pilot_cost: float | None = None
    notes: list[str] = field(default_factory=list)


def pilot_invoices(lines: list[PurchaseLine], start: str, end: str, goods_only: bool = True) -> list[PilotInvoice]:
    from ingest.suppliers import BY_KEY
    groups: OrderedDict[tuple, PilotInvoice] = OrderedDict()
    for l in lines:
        if not (start <= l.date <= end):
            continue
        if goods_only and (l.supplier_name.lower().startswith("cashup")
                           or (l.supplier_key and not BY_KEY[l.supplier_key].goods)):
            continue
        k = (l.supplier_name, primary_key(l.reference), l.date)
        groups.setdefault(k, PilotInvoice(l.supplier_name, l.supplier_key, l.reference, l.date)).lines.append(l)
    return list(groups.values())


def scans_from(pages: list[Page], records: list[InvoiceRecord] | None = None) -> list[Scan]:
    by_id = {r.invoice_id: r for r in records or []}
    grouped: OrderedDict[str, list[Page]] = OrderedDict()
    for p in pages:
        if p.invoice_id:
            grouped.setdefault(p.invoice_id, []).append(p)
    out = []
    for inv_id, ps in grouped.items():
        r = by_id.get(inv_id)
        out.append(Scan(inv_id, ps[0].supplier, ps[0].document_no, ps[0].date, [p.page_no for p in ps],
                        r.stated_total_excl if r else None, r.lines_sum_excl if r else None))
    return out


def _days(a: str | None, b: str | None) -> int | None:
    try:
        return abs((_date.fromisoformat(a) - _date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def match(scans: list[Scan], pilot: list[PilotInvoice]) -> list[Match]:
    index: dict[tuple, list[int]] = defaultdict(list)
    for i, inv in enumerate(pilot):
        for k in inv.keys:
            index[(inv.supplier_key, k)].append(i)

    used: set[int] = set()
    results: list[Match] = []
    pending: list[tuple[Scan, Match]] = []
    for s in scans:
        m = Match("scan_only", s.invoice_id, s.supplier, s.document_no, s.date, s.pages, s.total_excl)
        cand = sorted({i for k in ref_keys(s.document_no) for i in index.get((s.supplier, k), [])})
        fresh = [i for i in cand if i not in used]
        if len(fresh) > 1 and s.date:
            # the same reference on several Pilot invoices: prefer the one keyed for the scan's date
            dated = [i for i in fresh if _days(pilot[i].date, s.date) == 0]
            fresh = dated or fresh
        if len(fresh) == 1:
            used.add(fresh[0])
            m.status = "matched"
            _fill(m, pilot[fresh[0]])
        elif len(fresh) > 1:
            m.notes.append(f"ambiguous: {len(fresh)} Pilot invoices share this reference")
        elif cand:
            m.notes.append("the Pilot invoice with this reference is already matched to another scan: possible duplicate scan")
        else:
            pending.append((s, m))
        results.append(m)

    # second pass: reference did not match; try the keyed cost and the date
    for s, m in pending:
        total = s.total_excl if s.total_excl is not None else s.lines_sum
        if total is None:
            continue
        options = [i for i, inv in enumerate(pilot)
                   if i not in used and inv.supplier_key == s.supplier
                   and abs(inv.cost - total) <= TOTAL_TOLERANCE
                   and (_days(inv.date, s.date) is None or _days(inv.date, s.date) <= MAX_DAYS_APART)]
        if len(options) == 1:
            used.add(options[0])
            m.status = "matched_on_total"
            _fill(m, pilot[options[0]])
            m.notes.append(f"references differ ({s.document_no} scanned, {pilot[options[0]].reference} keyed)")

    for i, inv in enumerate(pilot):
        if i not in used:
            m = Match("pilot_only", supplier=inv.supplier_key)
            _fill(m, inv)
            results.append(m)
    return results


def _fill(m: Match, inv: PilotInvoice) -> None:
    m.pilot_supplier = inv.supplier_name
    m.pilot_reference = inv.reference
    m.pilot_date = inv.date
    m.pilot_lines = len(inv.lines)
    m.pilot_cost = inv.cost


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=str(config.OUT / "manifest.json"))
    ap.add_argument("--invoices", default=str(config.OUT / "invoices.json"))
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="2026-08-31")
    ap.add_argument("--out", default=str(config.GT / "pilot_match.json"))
    a = ap.parse_args(argv)

    pages = [Page.from_dict(d) for d in json.loads(Path(a.manifest).read_text(encoding="utf-8"))]
    records = None
    if Path(a.invoices).exists():
        records = [InvoiceRecord.from_dict(d) for d in json.loads(Path(a.invoices).read_text(encoding="utf-8"))]
    results = match(scans_from(pages, records), pilot_invoices(load_purchasing_report(), a.start, a.end))

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps([asdict(r) for r in results], indent=1), encoding="utf-8")
    print(dict(Counter(r.status for r in results)))
    by_sup: dict = defaultdict(Counter)
    for r in results:
        by_sup[r.supplier or r.pilot_supplier][r.status] += 1
    for s, c in sorted(by_sup.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {s!s:25} {dict(c)}")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
