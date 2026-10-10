"""Step 3 of ingest: identify every page and group pages into invoices, writing a manifest.

Pages are grouped only on positive evidence: the same document number, or a printed
"page 2 of 2" following "page 1 of 2" from the same supplier. Two adjacent pages from
the same supplier are never merged just for being adjacent. An earlier attempt did that
and turned 94 Vassco pages into invoices that did not exist.

    python -m ingest.run            # uses cached OCR; run ocr.run_all first
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz

import config
from ingest.identify import identify
from models import OCRResult, Page
from ocr.engines import DEFAULT_ENGINE, get_ocr
from refs import primary_key, same_invoice

HEADER_FRACTION = 0.50     # supplier, number and date sit in the top half, even below a strip of the sheet behind
MIN_WORDS_FOR_INVOICE = 25


def header_of(result: OCRResult, fraction: float = HEADER_FRACTION) -> OCRResult:
    cut = result.height * fraction
    return OCRResult(result.image_path, result.engine, result.engine_version, result.seconds,
                     result.width, result.height, [w for w in result.words if w.top < cut])


DUPLICATE_TEXT_SIMILARITY = 90   # token-set ratio above which two same-numbered pages are one page shot twice


def identify_pages(engine: str = DEFAULT_ENGINE, pattern: str = "*.jpg") -> list[Page]:
    ocr = get_ocr(engine)
    pages = []
    prev_text, prev_page = "", None
    for i, path in enumerate(sorted(config.PAGES.glob(pattern)), start=1):
        if not ocr.cached(path):
            pages.append(Page(page_no=i, image_path=path.name, source_pdf="", kind="unreadable",
                              notes=["no OCR result cached"]))
            continue
        full = ocr.recognise(path)
        ident = identify(header_of(full))
        page = Page(
            page_no=i, image_path=path.name, source_pdf="",
            supplier=ident.supplier.key if ident.supplier else None,
            supplier_score=round(ident.supplier_score, 1),
            document_no=ident.document_no, date=ident.date,
            page_of=f"{ident.page_of[0]}/{ident.page_of[1]}" if ident.page_of else None,
        )
        if len(full.words) < MIN_WORDS_FOR_INVOICE:
            page.kind = "not_invoice"
            page.notes.append(f"only {len(full.words)} words recognised")
        elif page.supplier is None:
            page.kind = "unreadable"
            page.notes.append("supplier not identified from header")
        if page.supplier and not page.document_no:
            page.notes.append("document number not found")
        # a second, different document number lower on the page suggests overlapping invoices
        lower = identify(OCRResult(full.image_path, full.engine, full.engine_version, 0, full.width, full.height,
                                   [w for w in full.words if w.top >= full.height * HEADER_FRACTION]))
        if lower.document_no and page.document_no and not same_invoice(lower.document_no, page.document_no) \
                and lower.supplier and page.supplier == lower.supplier.key:
            page.notes.append(f"possible second invoice on page: {lower.document_no}")
        # the same sheet photographed twice: same number, near-identical text.
        # Not a second page of a multi-page invoice, which would carry different lines.
        text = full.text
        if (page.kind == "invoice" and prev_page is not None and prev_page.supplier == page.supplier
                and page.document_no and same_invoice(page.document_no, prev_page.document_no)):
            similarity = fuzz.token_set_ratio(prev_text, text)
            if similarity >= DUPLICATE_TEXT_SIMILARITY:
                page.kind = "duplicate_scan"
                page.notes.append(f"repeat photograph of page {prev_page.page_no} (text similarity {similarity:.0f})")
        if page.kind == "invoice":
            prev_text, prev_page = text, page
        pages.append(page)
    return pages


def repair_dates(pages: list[Page]) -> list[Page]:
    """A binder covers one period, so the most common year is taken as expected. A date whose
    year differs by one confusable digit (2028 for 2026) is repaired and the repair noted;
    any other outlier is left as read and noted, never silently changed."""
    from ingest.identify import repair_year
    years = Counter(p.date[:4] for p in pages if p.date)
    if not years:
        return pages
    expected = int(years.most_common(1)[0][0])
    for p in pages:
        if not p.date or int(p.date[:4]) == expected:
            continue
        fixed = repair_year(p.date, expected)
        if fixed:
            p.notes.append(f"date year repaired: read {p.date}, recorded {fixed}")
            p.date = fixed
        else:
            p.notes.append(f"date {p.date} outside the binder's year {expected}")
    return pages


def group(pages: list[Page]) -> list[Page]:
    prev: Page | None = None
    for p in pages:
        if p.kind != "invoice":
            p.invoice_id = None
            prev = None
            continue
        joins = False
        if prev is not None and prev.supplier == p.supplier:
            if p.document_no and prev.document_no and same_invoice(p.document_no, prev.document_no):
                joins = True
                p.notes.append("grouped: same document number as previous page")
            elif p.page_of and prev.page_of:
                a, n = map(int, p.page_of.split("/"))
                pa, pn = map(int, prev.page_of.split("/"))
                if n == pn and a == pa + 1 and not (p.document_no and prev.document_no):
                    joins = True
                    p.notes.append("grouped: page numbering continues previous page")
        if joins:
            p.invoice_id = prev.invoice_id
            p.document_no = p.document_no or prev.document_no
        else:
            ref = primary_key(p.document_no) if p.document_no else f"p{p.page_no:03d}"
            p.invoice_id = f"{p.supplier}_{ref}"
        prev = p
    # the same invoice photographed twice lands on non-adjacent pages: keep ids unique but say so
    seen: Counter = Counter()
    for p in pages:
        if p.invoice_id is None:
            continue
        first_page = next(q for q in pages if q.invoice_id == p.invoice_id)
        if first_page is not p and not _contiguous(pages, first_page, p):
            seen[p.invoice_id] += 1
            p.notes.append(f"same reference as page {first_page.page_no} but not adjacent: possible duplicate scan")
            p.invoice_id = f"{p.invoice_id}_dup{seen[p.invoice_id]}"
    return pages


def _contiguous(pages: list[Page], a: Page, b: Page) -> bool:
    ia, ib = pages.index(a), pages.index(b)
    return all(pages[k].invoice_id == a.invoice_id for k in range(ia, ib))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", default=DEFAULT_ENGINE)
    ap.add_argument("--out", default=str(config.OUT / "manifest.json"))
    # One batch at a time: data/pages holds every page ever extracted, so a new month's pages
    # would otherwise be identified and grouped together with the ones already there.
    ap.add_argument("--pattern", default="*.jpg", help="which pages to read, e.g. sep_*.jpg")
    a = ap.parse_args(argv)
    pages = group(identify_pages(a.engine, a.pattern))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps([p.to_dict() for p in pages], indent=1), encoding="utf-8")

    kinds = Counter(p.kind for p in pages)
    invoices = {p.invoice_id for p in pages if p.invoice_id}
    by_sup = Counter(p.supplier for p in pages if p.invoice_id and p.invoice_id in invoices)
    print(f"pages {len(pages)}  kinds {dict(kinds)}  invoices {len(invoices)}")
    print(f"multi-page invoices: {sum(1 for i in invoices if sum(p.invoice_id == i for p in pages) > 1)}")
    print(f"no document number: {sum(1 for p in pages if p.kind == 'invoice' and not p.document_no)}")
    for s, n in by_sup.most_common():
        print(f"  {s:25} pages {n:3}")
    print(f"manifest -> {a.out}")


if __name__ == "__main__":
    main()
