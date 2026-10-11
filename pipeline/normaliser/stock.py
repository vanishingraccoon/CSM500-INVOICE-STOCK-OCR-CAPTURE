"""Pilot stock items and the conversion table derived from them.

Source: Stock Management > Stock Valuation, exported as CSV with zero-stock items included.
Layout: a title line (store name, "Stock Valuation for DD-MM-YYYY"), a blank line, then
    Cat, Total_label, Total__label, Total__Value_Y, Total1, prod, StockItem,
    BackStock, FrontStock, StockOnHand, UnitCost, Total, Textbox40
where prod is the stock item code and StockItem the free-text name carrying the base unit.

The table is regenerated from the supplied venue export. Eastgate is the current
case study; earlier cross-venue comparisons remain historical evaluation evidence.

    python -m normaliser.stock data/pilot/stock_valuation_eastgate_2026-09-13.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import config
from normaliser.units import BaseUnit, parse_base_unit, parse_pack, split_item_name


@dataclass
class StockItem:
    code: str
    name: str                 # full name as typed, suffix included
    description: str          # name without the unit suffix: the text invoice lines are matched to
    category: str
    base: BaseUnit
    unit_cost: float | None
    on_hand: float | None

    @property
    def category_code(self) -> str:
        return self.category.split(" - ", 1)[0].strip()

    def to_row(self) -> dict:
        d = asdict(self)
        d["base"] = asdict(self.base)
        return d


@dataclass
class StockFile:
    store: str
    as_of: str | None      # ISO date
    items: list[StockItem]

    def by_code(self) -> dict[str, StockItem]:
        return {i.code: i for i in self.items}


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def load_stock(path: Path) -> StockFile:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    store, as_of = "", None
    header_at = None
    for i, r in enumerate(rows):
        if r and r[0] == "Cat" and "StockItem" in r:
            header_at = i
            break
        if len(r) >= 2 and r[1].startswith("Stock Valuation for"):
            store = r[0].strip()
            m = re.search(r"(\d{2})-(\d{2})-(\d{4})", r[1])
            if m:
                as_of = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    if header_at is None:
        raise ValueError(f"{path}: no Stock Valuation header row found")
    cols = rows[header_at]
    idx = {c: cols.index(c) for c in ("Cat", "prod", "StockItem", "StockOnHand", "UnitCost")}
    items = []
    for r in rows[header_at + 1:]:
        if len(r) <= idx["StockItem"] or not r[idx["StockItem"]].strip():
            continue
        name = r[idx["StockItem"]].strip()
        desc, suffix = split_item_name(name)
        items.append(StockItem(
            code=r[idx["prod"]].strip(), name=name, description=desc, category=r[idx["Cat"]].strip(),
            base=parse_base_unit(suffix), unit_cost=_num(r[idx["UnitCost"]]), on_hand=_num(r[idx["StockOnHand"]]),
        ))
    return StockFile(store, as_of, items)


def default_stock_file() -> Path:
    files = sorted(config.PILOT.glob("stock_valuation_eastgate_*.csv"))
    if not files:
        raise FileNotFoundError("no Eastgate stock valuation export in data/pilot")
    return files[-1]


# --- conversion table -------------------------------------------------------

@dataclass
class TableEntry:
    code: str
    description: str
    category: str
    base_raw: str
    dimension: str | None
    unit: str | None
    content: float | None           # for pack-size units: amount per counted unit
    content_dimension: str | None
    name_pack: str | None           # pack notation found inside the name itself ("amstel 330ml")
    status: str                        # ok | pack_size_as_unit | base_unit_absent | base_unit_unrecognised
    scale: float = 1.0                 # canonical amount per stock unit


def conversion_table(stock: StockFile) -> list[TableEntry]:
    out = []
    for it in stock.items:
        b = it.base
        if not b.raw:
            status = "base_unit_absent"
        elif not b.known:
            status = "base_unit_unrecognised"
        elif b.content is not None and b.dimension == "count":
            status = "pack_size_as_unit"
        else:
            status = "ok"
        pack = parse_pack(it.description)
        out.append(TableEntry(it.code, it.description, it.category, b.raw, b.dimension, b.unit,
                              b.content, b.content_dimension, pack.raw if pack and pack.amount else None, status, b.scale))
    return out


def summarise(table: list[TableEntry]) -> dict:
    return {
        "items": len(table),
        "status": dict(Counter(e.status for e in table)),
        "suffixes": dict(Counter(e.base_raw for e in table).most_common()),
        "unrecognised": sorted({e.base_raw for e in table if e.status == "base_unit_unrecognised"}),
    }


def compare_venues(a: StockFile, b: StockFile) -> dict:
    """How standardised are stock items across two venues? Same code: same name and unit?"""
    ca, cb = a.by_code(), b.by_code()
    shared = set(ca) & set(cb)
    same_name = sum(1 for c in shared if ca[c].name.lower() == cb[c].name.lower())
    same_unit = sum(1 for c in shared if ca[c].base.raw.lower() == cb[c].base.raw.lower())
    differing = [(c, ca[c].name, cb[c].name) for c in sorted(shared, key=lambda x: int(x) if x.isdigit() else 0)
                 if ca[c].base.raw.lower() != cb[c].base.raw.lower()]
    return {"venue_a": a.store, "venue_b": b.store, "items_a": len(ca), "items_b": len(cb),
            "shared_codes": len(shared), "only_a": len(set(ca) - set(cb)), "only_b": len(set(cb) - set(ca)),
            "same_name": same_name, "same_unit": same_unit, "unit_differs": differing[:50]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("export", nargs="?")
    ap.add_argument("--compare")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    stock = load_stock(Path(a.export) if a.export else default_stock_file())
    table = conversion_table(stock)
    out = Path(a.out) if a.out else config.OUT / f"conversion_table_{re.sub(r'[^a-z]+', '_', stock.store.lower()).strip('_')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([asdict(e) for e in table], indent=1), encoding="utf-8")
    print(stock.store, stock.as_of, json.dumps(summarise(table), indent=1))
    if a.compare:
        print(json.dumps(compare_venues(stock, load_stock(Path(a.compare))), indent=1))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
