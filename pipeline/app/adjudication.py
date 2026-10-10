"""Settle disagreements between the system and the manager's keyed entry, against the paper.

Pilot holds the manager's reading of each invoice, not the truth: June held 48 unit-entry errors. Where a
scanned line and the keyed line for the same invoice (paired by equal cost) disagree, only the paper can say
who was right. This lists those lines beside the invoice image and records a verdict for each:

  system    the system's reading matches the paper
  pilot     the manager's keyed entry matches the paper
  neither   both are wrong
  unclear   the paper does not settle it

Kinds of disagreement:
  item       the system chose a different stock item
  quantity   same stock item, different base-unit quantity
  refused    the system declined to choose while the manager keyed a stock item

Verdicts are appended to data/out/review/adjudications.jsonl; the latest verdict per line counts.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import config
from app import data

VERDICTS = ("system", "pilot", "neither", "unclear")
QTY_TOLERANCE = 0.01
KIND_ORDER = {"quantity": 0, "item": 1, "refused": 2}


def log_path() -> Path:
    return data.REVIEW / "adjudications.jsonl"


def classify(n: dict, keyed_quantity: float, keyed_codes: set[str]) -> str | None:
    """What kind of disagreement a paired line is, or None when the readings agree or cannot be compared."""
    if not keyed_codes:
        return None      # keyed against something that is not a stock item (a charge, a placeholder)
    if not n.get("item_code"):
        return "refused"
    if n["item_code"] not in keyed_codes:
        return "item"
    q = n.get("base_quantity")
    if q is not None and abs(q - keyed_quantity) > max(QTY_TOLERANCE, QTY_TOLERANCE * abs(keyed_quantity)):
        return "quantity"
    return None


def _stamp() -> tuple:
    """Everything the disagreements are built from: pipeline outputs, page images, uploads and the Pilot exports."""
    files = [config.OUT / "invoices.json", config.OUT / "normalised.json", config.OUT / "manifest.json",
             config.GT / "pilot_match.json"]
    files += sorted(config.PILOT.glob("*.csv")) if config.PILOT.exists() else []
    uploads = data.UPLOADS
    files += sorted(uploads.glob("*.json")) if uploads.exists() else []
    return tuple((str(f), f.stat().st_mtime if f.exists() else 0.0) for f in files)


@lru_cache(maxsize=2)
def _disagreements(_outputs_stamp: tuple) -> tuple[dict, ...]:
    from eval.learning import keyed_lines
    from normaliser.match import align_lines
    from normaliser.stock import default_stock_file, load_stock
    from tools.pilot import load_purchasing_report

    records, normalised, _, images = data.load_all()
    match_file = config.GT / "pilot_match.json"
    matches = json.loads(match_file.read_text(encoding="utf-8")) if match_file.exists() else []
    by_desc: dict[str, set[str]] = defaultdict(set)
    for item in load_stock(default_stock_file()).items:
        by_desc[item.description.lower()].add(item.code)
    keyed = keyed_lines(load_purchasing_report())

    out = []
    for m in matches:
        r = records.get(m.get("invoice_id") or "")
        pl = keyed.get((m.get("pilot_supplier"), m.get("pilot_reference"), m.get("pilot_date")), [])
        if r is None or not pl:
            continue
        for i, j in align_lines(r.lines, pl):
            n = normalised.get(r.invoice_id, {}).get(i)
            if n is None:
                continue
            p = pl[j]
            kind = classify(n, p.quantity, by_desc.get(p.item.lower(), set()))
            if kind is None:
                continue
            line = r.lines[i]
            out.append({
                "key": f"{r.invoice_id}:{i}", "invoice_id": r.invoice_id, "line_index": i, "kind": kind,
                "supplier": r.supplier, "document_no": r.document_no, "date": r.date,
                "images": images.get(r.invoice_id, []),
                "printed": {"description": line.description, "quantity": line.quantity, "unit": line.unit,
                            "pack": line.pack, "unit_price": line.unit_price, "line_total": line.line_total},
                "system": {k: n.get(k) for k in ("item_code", "item_name", "base_quantity", "base_unit", "factor",
                                                  "conversion_basis", "refusal", "reason")},
                "pilot": {"item": p.item, "quantity": p.quantity, "cost": p.cost, "reference": p.reference, "date": p.date},
            })
    out.sort(key=lambda d: (KIND_ORDER[d["kind"]], d["supplier"] or "", d["invoice_id"], d["line_index"]))
    return tuple(out)


def disagreements() -> list[dict]:
    """Recomputed only when the pipeline outputs change."""
    return list(_disagreements(_stamp()))


def verdicts() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    path = log_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            v = json.loads(line)
            latest[v["key"]] = v
    return latest


def record(item: dict, verdict: str, note: str = "") -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"key": item["key"], "kind": item["kind"], "verdict": verdict, "note": note.strip(),
                            "supplier": item["supplier"], "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")


def summary(items: list[dict], decided: dict[str, dict]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for d in items:
        v = decided.get(d["key"])
        counts[d["kind"]][v["verdict"] if v else "undecided"] += 1
    return {k: dict(v) for k, v in counts.items()}
