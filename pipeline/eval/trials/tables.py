"""Trial: does image-based table structure detection find the line-item rows our parser misses?

Library under trial: img2table (MIT), https://github.com/xavctn/img2table. It detects tables from ruled lines
and text alignment with OpenCV, and fills cells with text from an OCR engine (Tesseract here).

Runs in .venv-trial:

    .venv-trial/Scripts/python -m eval.trials.tables

Invoices: those tied to a Pilot entry whose parsed line count differs from the number of lines the manager
keyed, in a fixed order (supplier, invoice id), up to a limit. For each, the largest table found on each page
is counted, and its row count compared with our line count and Pilot's. Rows include a header row, so a
table that matches Pilot has one more row than Pilot has lines.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["PATH"] = r"C:\Program Files\Tesseract-OCR" + os.pathsep + os.environ.get("PATH", "")
OUT = ROOT / "data" / "trials" / "tables"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args(argv)

    from img2table.document import Image
    from img2table.ocr import TesseractOCR

    invoices = {d["invoice_id"]: d for d in json.loads((ROOT / "data" / "out" / "invoices.json").read_text(encoding="utf-8"))}
    matches = json.loads((ROOT / "data" / "gt" / "pilot_match.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "data" / "out" / "manifest.json").read_text(encoding="utf-8"))
    images: dict[str, list[str]] = {}
    for p in manifest:
        if p.get("invoice_id"):
            images.setdefault(p["invoice_id"], []).append(p["image_path"])

    chosen = sorted((m for m in matches
                     if m.get("invoice_id") in invoices and m.get("pilot_lines")
                     and len(invoices[m["invoice_id"]]["lines"]) != m["pilot_lines"]),
                    key=lambda m: (m["supplier"] or "", m["invoice_id"]))[: a.limit]

    ocr = TesseractOCR(n_threads=4, lang="eng")
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in chosen:
        inv = invoices[m["invoice_id"]]
        t0 = time.perf_counter()
        table_rows, tables_found = 0, 0
        for name in images.get(m["invoice_id"], []):
            doc = Image(str(ROOT / "data" / "pages" / name), detect_rotation=False)
            tables = doc.extract_tables(ocr=ocr, implicit_rows=True, borderless_tables=True, min_confidence=50)
            tables_found += len(tables)
            if tables:
                # rows from the table's own cell map: its .df export would need pandas, which the trial does not
                table_rows += max(len(t.content) for t in tables)
        row = {"invoice_id": m["invoice_id"], "supplier": m["supplier"], "pages": len(images.get(m["invoice_id"], [])),
               "our_lines": len(inv["lines"]), "pilot_lines": m["pilot_lines"], "tables_found": tables_found,
               "largest_table_rows": table_rows, "seconds": round(time.perf_counter() - t0, 1)}
        rows.append(row)
        print(json.dumps(row), flush=True)

    (OUT / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    closer = sum(1 for r in rows if abs(r["largest_table_rows"] - 1 - r["pilot_lines"]) < abs(r["our_lines"] - r["pilot_lines"]))
    print(f"\n{len(rows)} invoices; img2table's largest-table row count (less a header) was closer to Pilot's line count "
          f"than our parser's on {closer}")


if __name__ == "__main__":
    main()
