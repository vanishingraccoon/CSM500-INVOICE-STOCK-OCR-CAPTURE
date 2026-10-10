"""Load Pilot's "Purchases by Supplier" export: what the managers actually keyed in.

This is the second reading of every invoice. It is not treated as the truth: June held
48 unit-entry errors. It is one of two independent readings from which ground truth is
built (see tools/groundtruth.py).

Export columns: Storename1, QTY2, Cost2, Vat2, Total2 (report grand totals, ignored),
Supplier1, Sref (invoice reference as typed), CATDESC, Item, QTY, Cost, Vat, Total.
The export carries no date and no item code, so invoices are identified by supplier and
reference alone.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import config
from ingest.suppliers import BY_PILOT_NAME


@dataclass
class PilotLine:
    supplier_name: str
    supplier_key: str | None
    reference: str
    category: str
    item: str
    quantity: float
    cost: float
    vat: float
    total: float

    @property
    def unit_cost(self) -> float | None:
        return self.cost / self.quantity if self.quantity else None


def _num(s: str) -> float:
    s = (s or "").replace(",", "").strip()
    return float(s) if s else 0.0


def load_lines(path: Path | None = None) -> list[PilotLine]:
    path = path or next(config.PILOT.glob("purchases_by_supplier*.csv"))
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            sup = BY_PILOT_NAME.get(r["Supplier1"].strip().lower())
            out.append(PilotLine(
                supplier_name=r["Supplier1"].strip(),
                supplier_key=sup.key if sup else None,
                reference=r["Sref"].strip(),
                category=r["CATDESC"].strip(),
                item=r["Item"].strip(),
                quantity=_num(r["QTY"]), cost=_num(r["Cost"]), vat=_num(r["Vat"]), total=_num(r["Total"]),
            ))
    return out


@dataclass
class PurchaseLine(PilotLine):
    """A line from the Purchasing Report, which adds what Purchases by Supplier lacks."""
    date: str = ""                      # ISO yyyy-mm-dd
    captured_by: str = ""               # Pilot login that keyed the invoice
    invoice_cost: float = 0.0           # the invoice's totals as keyed, repeated on every line
    invoice_vat: float = 0.0
    invoice_total: float = 0.0


def load_purchasing_report(path: Path | None = None) -> list[PurchaseLine]:
    """Stock Management > Purchasing Report, CSV export.

    Columns: Supplier, Textbox75-77 (supplier totals), Date (dd/mm/yyyy), Sref (reference),
    Pref1 (who captured), Textbox81-83 (invoice cost, VAT, total), Cat2, Item1, Qty1, Cost, Vat,
    Total, Textbox68-70 (report totals). Despite the names, Cat2 holds the item name and Item1
    the category code.
    """
    path = path or sorted(config.PILOT.glob("purchasing_report_*.csv"))[-1]
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    header_at = next(i for i, r in enumerate(rows) if r and r[0] == "Supplier" and "Sref" in r)
    cols = rows[header_at]
    out = []
    for r in rows[header_at + 1:]:
        if len(r) != len(cols):
            continue
        d = dict(zip(cols, r))
        sup = BY_PILOT_NAME.get(d["Supplier"].strip().lower())
        day, month, year = d["Date"].split("/")
        out.append(PurchaseLine(
            supplier_name=d["Supplier"].strip(), supplier_key=sup.key if sup else None,
            reference=d["Sref"].strip(), category=d["Item1"].strip(), item=d["Cat2"].strip(),
            quantity=_num(d["Qty1"]), cost=_num(d["Cost"]), vat=_num(d["Vat"]), total=_num(d["Total"]),
            date=f"{year}-{month}-{day}", captured_by=d["Pref1"].strip(),
            invoice_cost=_num(d["Textbox81"]), invoice_vat=_num(d["Textbox82"]), invoice_total=_num(d["Textbox83"]),
        ))
    return out
