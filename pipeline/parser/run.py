"""Parse every invoice in the ingest manifest and report extraction per layout family.

"Done" for the parser is not good rates but known rates: this runs over every page without
stopping on a bad one and says, per family, how often a table was found, how many lines came
out, how many reconciled, and how many invoices' lines add up to their stated total.

    python -m parser.run
"""
from __future__ import annotations

import argparse
import json
import traceback
from collections import OrderedDict, defaultdict
from pathlib import Path

import config
from models import InvoiceRecord, Page
from ocr.engines import DEFAULT_ENGINE, get_ocr
from parser.engine import parse_invoice


def load_manifest(path: Path | None = None) -> list[Page]:
    path = path or config.OUT / "manifest.json"
    return [Page.from_dict(d) for d in json.loads(Path(path).read_text(encoding="utf-8"))]


def invoices_from_manifest(pages: list[Page]) -> OrderedDict[str, list[Page]]:
    groups: OrderedDict[str, list[Page]] = OrderedDict()
    for p in pages:
        if p.invoice_id:
            groups.setdefault(p.invoice_id, []).append(p)
    return groups


def summarise(records: list[InvoiceRecord]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for r in records:
        s = out[r.layout or "generic"]
        s["invoices"] += 1
        s["with_lines"] += bool(r.lines)
        s["lines"] += len(r.lines)
        s["lines_reconciled"] += sum(1 for i in range(len(r.lines))
                                     if not any(w.startswith(f"line {i + 1}: line does not reconcile") for w in r.warnings))
        s["quantity_derived"] += sum("quantity not printed" in w for w in r.warnings)
        s["decimal_repairs"] += sum("decimal point missing" in w for w in r.warnings)
        s["stated_total_found"] += r.stated_total_excl is not None
        s["adds_up"] += r.adds_up is True
        s["does_not_add_up"] += r.adds_up is False
    return {k: dict(v) for k, v in out.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", default=DEFAULT_ENGINE)
    ap.add_argument("--out", default=str(config.OUT / "invoices.json"))
    a = ap.parse_args(argv)

    ocr = get_ocr(a.engine)
    records, failures = [], []
    for inv_id, pages in invoices_from_manifest(load_manifest()).items():
        paths = [config.PAGES / p.image_path for p in pages]
        results = [ocr.recognise(path) if ocr.cached(path) else None for path in paths]
        try:
            records.append(parse_invoice(list(zip(pages, results))))
        except Exception as e:  # noqa: BLE001 - one bad invoice must not stop the run; it is reported instead
            failures.append({"invoice_id": inv_id, "error": f"{type(e).__name__}: {e}",
                             "trace": traceback.format_exc(limit=3)})

    Path(a.out).write_text(json.dumps([r.to_dict() for r in records], indent=1), encoding="utf-8")
    summary = summarise(records)
    (config.OUT / "parser_summary.json").write_text(json.dumps({"families": summary, "failures": failures}, indent=1),
                                                    encoding="utf-8")
    print(f"{len(records)} invoices parsed, {len(failures)} failed")
    for fam, s in sorted(summary.items(), key=lambda kv: -kv[1]["invoices"]):
        print(f"  {fam:24} " + "  ".join(f"{k}={v}" for k, v in s.items()))
    for f in failures[:10]:
        print("  FAILED", f["invoice_id"], f["error"])
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
