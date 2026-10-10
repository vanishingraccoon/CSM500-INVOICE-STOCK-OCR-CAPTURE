"""What the review page reads (pipeline outputs) and writes (the reviewer's decisions).

Every decision is kept in two forms: the latest accepted rows per invoice, which the export uses,
and an append-only log of what the person changed against what the system proposed. How often a
person had to intervene, and on what, is a result in itself.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

import config
from models import InvoiceRecord

REVIEW = config.OUT / "review"
DECISIONS = REVIEW / "decisions"
CORRECTIONS = REVIEW / "corrections.jsonl"
UPLOADS = config.OUT / "uploads"


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def safe_id(invoice_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", invoice_id)


def load_all() -> tuple[dict[str, InvoiceRecord], dict[str, dict[int, dict]], dict[str, list[dict]], dict[str, list[str]]]:
    """(records, normalised lines by invoice and line, flags by invoice, page images by invoice)."""
    records = {d["invoice_id"]: InvoiceRecord.from_dict(d) for d in _read(config.OUT / "invoices.json", [])}
    normalised: dict[str, dict[int, dict]] = defaultdict(dict)
    for n in _read(config.OUT / "normalised.json", []):
        normalised[n["invoice_id"]][n["line_index"]] = n
    flags: dict[str, list[dict]] = defaultdict(list)
    for f in _read(config.OUT / "flags.json", []):
        flags[f["invoice_id"]].append(f)

    images: dict[str, list[str]] = defaultdict(list)
    for p in _read(config.OUT / "manifest.json", []):
        if p.get("invoice_id"):
            images[p["invoice_id"]].append(p["image_path"])

    if UPLOADS.exists():
        for path in sorted(UPLOADS.glob("*.json")):
            u = _read(path, None)
            if not u:
                continue
            rec = InvoiceRecord.from_dict(u["record"])
            records[rec.invoice_id] = rec
            normalised[rec.invoice_id] = {n["line_index"]: n for n in u.get("normalised", [])}
            flags[rec.invoice_id] = u.get("flags", [])
            images[rec.invoice_id] = u.get("images", [])
    return records, normalised, flags, images


def decision(invoice_id: str) -> dict | None:
    return _read(DECISIONS / f"{safe_id(invoice_id)}.json", None)


def reviewed_ids() -> list[str]:
    if not DECISIONS.exists():
        return []
    return [_read(p, {}).get("invoice_id") for p in sorted(DECISIONS.glob("*.json"))]


def save_review(invoice_id: str, rows: dict[str, dict], corrections: list[dict], seconds: float) -> None:
    DECISIONS.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    (DECISIONS / f"{safe_id(invoice_id)}.json").write_text(
        json.dumps({"invoice_id": invoice_id, "saved_at": now, "review_seconds": round(seconds, 1), "rows": rows},
                   indent=1), encoding="utf-8")
    with open(CORRECTIONS, "a", encoding="utf-8") as f:
        f.writelines(json.dumps({"saved_at": now, "invoice_id": invoice_id, "review_seconds": round(seconds, 1), **c}) + "\n" for c in corrections)
