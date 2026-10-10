"""Run one uploaded invoice photograph through the whole pipeline, for the review page.

photograph -> OCR -> supplier, number, date -> lines -> stock items and base units -> flags
The result is stored under data/out/uploads/ and opens in the same review page as the binder.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps

import config
from app.data import UPLOADS, load_all
from checks.duplicates import find_duplicates
from checks.prices import find_price_anomalies
from checks.run import history_points, price_points, scan_summaries
from ingest.identify import identify
from ingest.run import header_of
from models import Flag, Page
from normaliser.normalise import build_matcher, normalise_line
from ocr.engines import get_ocr
from parser.engine import parse_invoice

MIN_MEAN_CONFIDENCE = 80.0
MIN_WORDS = 25
MIN_SHARPNESS = 60.0      # variance of the Laplacian; below this the photograph is probably blurred

_cache: dict = {}


def _shared():
    if "ocr" not in _cache:
        _cache["ocr"] = get_ocr()
        from tools.pilot import load_purchasing_report
        lines = load_purchasing_report()
        matcher, medians = build_matcher(lines)
        _cache.update(lines=lines, matcher=matcher, medians=medians)
    return _cache


def sharpness(path: Path) -> float | None:
    try:
        import cv2
    except ImportError:
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return None if img is None else float(cv2.Laplacian(img, cv2.CV_64F).var())


def quality_notes(path: Path, words: int, mean_conf: float) -> list[str]:
    notes = []
    s = sharpness(path)
    if s is not None and s < MIN_SHARPNESS:
        notes.append(f"photograph looks blurred (sharpness {s:.0f}); consider retaking it")
    if words < MIN_WORDS:
        notes.append(f"only {words} words recognised; the photograph may be cropped, dark or not an invoice")
    elif mean_conf < MIN_MEAN_CONFIDENCE:
        notes.append(f"recognition confidence is low ({mean_conf:.0f}); check figures against the paper")
    return notes


def process_upload(file_storage) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    invoice_id = f"upload_{stamp}_{uuid.uuid4().hex[:6]}"   # two photos in the same second must not collide
    path = config.PAGES / f"{invoice_id}.jpg"
    config.PAGES.mkdir(parents=True, exist_ok=True)
    with Image.open(file_storage.stream) as im:
        ImageOps.exif_transpose(im).convert("RGB").save(path, "JPEG", quality=92)

    shared = _shared()
    result = shared["ocr"].recognise(path)
    ident = identify(header_of(result))
    page = Page(page_no=1, image_path=path.name, source_pdf="upload",
                supplier=ident.supplier.key if ident.supplier else None, supplier_score=round(ident.supplier_score, 1),
                document_no=ident.document_no, date=ident.date, invoice_id=invoice_id)
    record = parse_invoice([(page, result)])
    record.warnings = quality_notes(path, len(result.words), result.mean_conf) + record.warnings
    if page.supplier is None:
        record.warnings.insert(0, "supplier not recognised from the letterhead; generic layout used")

    normalised = []
    for i, line in enumerate(record.lines):
        n = normalise_line(line, record.supplier, shared["matcher"], shared["medians"])
        normalised.append({"invoice_id": invoice_id, "line_index": i, **n.to_dict()})

    existing, _, _, _ = load_all()
    others = [r for r in existing.values() if r.invoice_id != invoice_id]
    flags: list[Flag] = [f for f in find_duplicates(scan_summaries(others + [record])) if f.invoice_id == invoice_id]
    flags += find_price_anomalies(price_points(normalised), history_points(shared["lines"], shared["matcher"].stock))
    if record.adds_up is False:
        flags.append(Flag("total_mismatch", invoice_id, None, "medium", 0.9,
                          f"The invoice's own lines sum to R{record.lines_sum_excl:,.2f} but it states "
                          f"R{record.stated_total_excl:,.2f} excl. VAT. Check the paper before keying.", {}))

    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / f"{invoice_id}.json").write_text(json.dumps({
        "record": record.to_dict(), "normalised": normalised, "flags": [f.to_dict() for f in flags],
        "images": [path.name], "ocr_seconds": result.seconds,
    }, indent=1), encoding="utf-8")
    return invoice_id
