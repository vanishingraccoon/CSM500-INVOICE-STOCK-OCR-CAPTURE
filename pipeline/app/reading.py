"""From a batch's photographs to invoices, lines and tabs. Run after every photograph is read and after
every manager action, by whichever process made the change.

1. Group pages into invoices on positive evidence only, as ingest.run.group does for the binder: a page
   joins the page before it (by scanner position, never upload order) when both come from the same
   supplier and carry the same invoice number (refs.same_invoice), or when the printed "page x of y"
   continues. Anything else is its own invoice. The same number further away is a second invoice, which
   the duplicate check then catches, rather than a merge that hides it.
2. Keep invoice ids stable while pages arrive in any order: a new grouping takes over the existing
   invoice it shares most pages with, and an invoice absorbed into another hands over the manager's work.
3. Parse an invoice whose pages changed, in page order, with parser.engine.parse_invoice; normalise every
   line with the shop's key; price-check the lines. Lines the manager already entered or removed are
   carried over by what was printed on them: the manager's entry wins over a fresh reading.
4. Decide every invoice's tab, first match wins (docs/phone-api.md section 4): duplicates, retake,
   corrections, good. Reasons are always listed, in plain words. Only the contract's reasons decide a tab;
   a possible duplicate under another invoice number is a note beside the invoice, not a reason.
5. Write back only what changed, in one short transaction, and only if nobody else changed the batch
   meanwhile (its revision); otherwise start again from the new state.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from app import db
from app.quality import assess
from checks.duplicates import InvoiceSummary, days_apart, find_duplicates
from checks.prices import PricePoint, find_price_anomalies
from ingest.identify import repair_year
from models import InvoiceRecord, NormalisedLine, OCRResult, Page
from normaliser.match import normalise_text, text_score
from normaliser.normalise import normalise_line
from parser.engine import ADDS_UP_TOLERANCE, parse_invoice
from refs import ref_keys, same_invoice

UNREAD = ("stored", "reading")
CARRY_MIN_TEXT_SCORE = 85.0     # a retake reads a line's wording slightly differently; the total must still agree


def cents(value: float | None) -> int | None:
    return None if value is None else round(value * 100)


def rands(value: int | None) -> float | None:
    return None if value is None else round(value / 100, 2)


# --- state ---------------------------------------------------------------------------------------

@dataclass
class PhotoRow:
    photo_id: str
    batch_id: str
    ordinal: int
    sha256: str
    state: str
    storage_path: str
    received: float
    replaces: str | None = None
    replaced_by: str | None = None
    supplier: str | None = None
    document_no: str | None = None
    date: str | None = None
    page_of: str | None = None
    words: int | None = None
    quality: dict | None = None          # the server's measures, once read
    phone_quality: dict | None = None
    read: float | None = None
    cache_file: str | None = None
    last_error: str | None = None

    def assessment(self) -> dict:
        server = (self.quality or {}).get("server")
        return assess(server, self.phone_quality, self.words if self.state == "read" else None)


@dataclass
class LineState:
    idx: int
    entered_by: str = "system"
    printed: dict | None = None          # the parsed line exactly as read; None for a line the manager added
    removed: bool = False
    item_code: str | None = None
    item_name: str | None = None
    base_unit: str | None = None
    free_text: str | None = None
    quantity: float | None = None        # as invoiced
    unit: str | None = None
    base_quantity: float | None = None
    cost_cents: int | None = None        # excl. VAT
    vat_cents: int | None = None
    factor: float | None = None
    conversion_basis: str | None = None
    match_method: str | None = None
    match_score: float | None = None
    refusal: str | None = None
    reason: str = ""
    flags: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    star: bool = False
    updated_at: str = ""

    def row(self) -> dict:
        d = asdict(self)
        d.pop("updated_at")
        return d


@dataclass
class InvoiceState:
    invoice_id: str
    batch_id: str
    created_at: str
    updated_at: str
    status: str = "open"
    supplier: str | None = None
    document_no: str | None = None
    date: str | None = None
    supplier_override: str | None = None
    document_no_override: str | None = None
    date_override: str | None = None
    layout: str | None = None
    stated_total_excl: int | None = None
    stated_vat: int | None = None
    stated_total_incl: int | None = None
    lines_sum_excl: int | None = None
    adds_up: bool | None = None
    warnings: list[str] = field(default_factory=list)
    tab: str = "retake"
    tab_reasons: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    not_duplicate: bool = False
    parsed: float | None = None
    needs_report: bool = False
    reported_in: int | None = None
    pages: list[str] = field(default_factory=list)     # photo ids in scanner order
    lines: list[LineState] = field(default_factory=list)

    @property
    def eff_supplier(self) -> str | None:
        return self.supplier_override or self.supplier

    @property
    def eff_document_no(self) -> str | None:
        return self.document_no_override or self.document_no

    @property
    def eff_date(self) -> str | None:
        return self.date_override or self.date

    def live_lines(self) -> list[LineState]:
        return [l for l in self.lines if not l.removed]

    def row(self) -> dict:
        d = asdict(self)
        for k in ("pages", "lines", "updated_at", "created_at"):
            d.pop(k)
        return d


@dataclass
class BatchState:
    batch: dict
    photos: dict[str, PhotoRow]
    invoices: dict[str, InvoiceState]
    events: list[dict] = field(default_factory=list)
    reparse: set[str] = field(default_factory=set)
    carried: dict[str, list[LineState]] = field(default_factory=dict)   # manager lines moving between invoices

    @property
    def batch_id(self) -> str:
        return self.batch["batch_id"]

    @property
    def shop_id(self) -> str:
        return self.batch["shop_id"]

    @property
    def submitted(self) -> bool:
        return self.batch["status"] == "submitted"

    def active_photos(self) -> list[PhotoRow]:
        return sorted((p for p in self.photos.values() if p.state != "replaced"), key=lambda p: (p.ordinal, p.received))

    def invoice_of(self, photo_id: str) -> InvoiceState | None:
        return next((i for i in self.invoices.values() if photo_id in i.pages), None)

    def ordinals(self, inv: InvoiceState) -> list[int]:
        return [self.photos[p].ordinal for p in inv.pages if p in self.photos]

    def event(self, kind: str, **fields) -> None:
        self.events.append({"kind": kind, **fields})


def load_batch(conn: sqlite3.Connection, batch_id: str) -> BatchState | None:
    with db.snapshot(conn):
        batch = db.one(conn, "SELECT * FROM batches WHERE batch_id = ?", batch_id)
        if batch is None:
            return None
        photo_rows = db.all_rows(conn, "SELECT p.*, o.cache_file, j.last_error FROM photos p"
                                       " LEFT JOIN ocr_results o ON o.photo_id = p.photo_id"
                                       " LEFT JOIN jobs j ON j.photo_id = p.photo_id AND j.kind = 'read'"
                                       " WHERE p.batch_id = ? ORDER BY p.ordinal, p.received", batch_id)
        invoice_rows = db.all_rows(conn, "SELECT * FROM invoices WHERE batch_id = ? ORDER BY created_at, invoice_id",
                                   batch_id)
        page_rows = db.all_rows(conn, "SELECT ip.* FROM invoice_pages ip JOIN invoices i ON i.invoice_id = ip.invoice_id"
                                      " WHERE i.batch_id = ? ORDER BY ip.invoice_id, ip.page_no", batch_id)
        line_rows = db.all_rows(conn, "SELECT l.* FROM lines l JOIN invoices i ON i.invoice_id = l.invoice_id"
                                      " WHERE i.batch_id = ? ORDER BY l.invoice_id, l.idx", batch_id)
    photos = {}
    for r in photo_rows:
        photos[r["photo_id"]] = PhotoRow(
            photo_id=r["photo_id"], batch_id=r["batch_id"], ordinal=r["ordinal"], sha256=r["sha256"], state=r["state"],
            storage_path=r["storage_path"], received=r["received"], replaces=r["replaces"],
            replaced_by=r["replaced_by"], supplier=r["supplier"], document_no=r["document_no"], date=r["date"],
            page_of=r["page_of"], words=r["words"], quality=db.loads(r["quality"]),
            phone_quality=db.loads(r["phone_quality"]), read=r["read"], cache_file=r["cache_file"],
            last_error=r["last_error"])
    invoices = {}
    for r in invoice_rows:
        invoices[r["invoice_id"]] = InvoiceState(
            invoice_id=r["invoice_id"], batch_id=r["batch_id"], created_at=r["created_at"], updated_at=r["updated_at"],
            status=r["status"], supplier=r["supplier"], document_no=r["document_no"], date=r["date"],
            supplier_override=r["supplier_override"], document_no_override=r["document_no_override"],
            date_override=r["date_override"], layout=r["layout"], stated_total_excl=r["stated_total_excl"],
            stated_vat=r["stated_vat"], stated_total_incl=r["stated_total_incl"], lines_sum_excl=r["lines_sum_excl"],
            adds_up=None if r["adds_up"] is None else bool(r["adds_up"]), warnings=db.loads(r["warnings"], []),
            tab=r["tab"], tab_reasons=db.loads(r["tab_reasons"], []), duplicate_of=r["duplicate_of"],
            not_duplicate=bool(r["not_duplicate"]), parsed=r["parsed"], needs_report=bool(r["needs_report"]),
            reported_in=r["reported_in"])
    for r in page_rows:
        invoices[r["invoice_id"]].pages.append(r["photo_id"])
    for inv in invoices.values():
        inv.pages.sort(key=lambda pid: (photos[pid].ordinal, photos[pid].received))
    for r in line_rows:
        invoices[r["invoice_id"]].lines.append(LineState(
            idx=r["idx"], entered_by=r["entered_by"], printed=db.loads(r["printed"]), removed=bool(r["removed"]),
            item_code=r["item_code"], item_name=r["item_name"], base_unit=r["base_unit"], free_text=r["free_text"],
            quantity=r["quantity"], unit=r["unit"], base_quantity=r["base_quantity"], cost_cents=r["cost_cents"],
            vat_cents=r["vat_cents"], factor=r["factor"], conversion_basis=r["conversion_basis"],
            match_method=r["match_method"], match_score=r["match_score"], refusal=r["refusal"], reason=r["reason"],
            flags=db.loads(r["flags"], []), candidates=db.loads(r["candidates"], []), star=bool(r["star"]),
            updated_at=r["updated_at"]))
    return BatchState(batch, photos, invoices)


# --- 1 and 2: grouping ---------------------------------------------------------------------------

def continues(prev: PhotoRow, page: PhotoRow) -> bool:
    """Does page belong to the same invoice as the page before it? Positive evidence only."""
    if not page.supplier or page.supplier != prev.supplier:
        return False
    if page.document_no and prev.document_no:
        return same_invoice(page.document_no, prev.document_no)
    if page.page_of and prev.page_of:
        a, n = map(int, page.page_of.split("/"))
        pa, pn = map(int, prev.page_of.split("/"))
        return n == pn and a == pa + 1
    return False


def group_pages(pages: list[PhotoRow], breaks: list[PhotoRow] = ()) -> list[list[PhotoRow]]:
    """Runs of pages that continue one another. A break (a page of a deleted invoice) keeps its place in the
    scanner order, so the pages either side of it never join up."""
    groups: list[list[PhotoRow]] = []
    prev = None
    marked = [(p, False) for p in pages] + [(p, True) for p in breaks]
    for page, is_break in sorted(marked, key=lambda m: (m[0].ordinal, m[0].received)):
        if is_break:
            prev = None
            continue
        if prev is not None and continues(prev, page):
            groups[-1].append(page)
        else:
            groups.append([page])
        prev = page
    return groups


def deleted_pages(state: BatchState) -> list[PhotoRow]:
    """Pages of invoices the manager deleted: they stay with that invoice, for the record."""
    frozen = {pid for inv in state.invoices.values() if inv.status == "deleted" for pid in inv.pages}
    return [p for p in state.active_photos() if p.photo_id in frozen]


def participants(state: BatchState) -> list[PhotoRow]:
    """Pages that can be grouped: read or failed. A retake still being read keeps the place of the page it
    replaces, identified as that page was; any other unread page waits outside every invoice."""
    frozen = {p.photo_id for p in deleted_pages(state)}
    out = []
    for p in state.active_photos():
        if p.photo_id in frozen:
            continue
        if p.state in ("read", "failed"):
            out.append(p)
        elif p.replaces and state.invoice_of(p.photo_id) is not None:
            old = state.photos.get(p.replaces)
            out.append(replace(p, supplier=old.supplier, document_no=old.document_no, page_of=old.page_of)
                       if old else p)
    return out


def assign(state: BatchState, groups: list[list[PhotoRow]]) -> None:
    existing = {i.invoice_id: set(i.pages) for i in state.invoices.values() if i.status == "open"}
    claimed: dict[str, list[str]] = {}
    unmatched: list[list[str]] = []
    for g in groups:
        ids = [p.photo_id for p in g]
        options = [(len(set(ids) & pages), ids[0] in pages, inv_id)
                   for inv_id, pages in existing.items() if set(ids) & pages and inv_id not in claimed]
        if options:
            claimed[max(options)[2]] = ids
        else:
            unmatched.append(ids)

    for inv_id, old_pages in existing.items():
        if inv_id in claimed:
            continue
        into = max(((len(old_pages & set(ids)), target) for target, ids in claimed.items() if old_pages & set(ids)),
                   default=None)
        if into is None:
            continue     # its pages are not groupable any more; leave it as it is
        absorbed, target = state.invoices.pop(inv_id), state.invoices[into[1]]
        state.carried.setdefault(target.invoice_id, []).extend(
            l for l in absorbed.lines if l.entered_by == "manager" or l.removed)
        for f in ("supplier_override", "document_no_override", "date_override"):
            if getattr(target, f) is None:
                setattr(target, f, getattr(absorbed, f))
        target.not_duplicate = target.not_duplicate or absorbed.not_duplicate
        state.event("invoice_merged", invoice_id=target.invoice_id, absorbed=inv_id)
        state.reparse.add(target.invoice_id)

    now = db.iso()
    for ids in unmatched:
        inv = InvoiceState(db.new_id("i"), state.batch_id, now, now, pages=ids)
        state.invoices[inv.invoice_id] = inv
        state.reparse.add(inv.invoice_id)
        state.event("invoice_found", invoice_id=inv.invoice_id, pages=[state.photos[p].ordinal for p in ids])
    for inv_id, ids in claimed.items():
        inv = state.invoices[inv_id]
        if inv.pages != ids:
            state.event("invoice_pages_changed", invoice_id=inv_id, before=state.ordinals(inv),
                        after=[state.photos[p].ordinal for p in ids])
            inv.pages = ids
            state.reparse.add(inv_id)


# --- 3: parsing, normalising, carrying the manager's work over ------------------------------------

LINE_NOTE = re.compile(r"^line (\d+): (.*)$")
OcrLoader = Callable[[PhotoRow], OCRResult | None]


def load_ocr_file(photo: PhotoRow) -> OCRResult | None:
    """The recognised words for a read page, from the OCR disk cache the reader wrote them to."""
    if not photo.cache_file:
        return None
    path = Path(photo.cache_file)
    if not path.exists():
        return None
    return OCRResult.from_dict(json.loads(path.read_text(encoding="utf-8")))


def read_header(state: BatchState, inv: InvoiceState) -> tuple[str | None, str | None, str | None, list[str]]:
    """Supplier, number and date as read: the first page that shows each. A date whose year is one misread
    digit away from the batch's year is repaired, and the repair noted (ingest.identify.repair_year)."""
    pages = [state.photos[p] for p in inv.pages]
    supplier = next((p.supplier for p in pages if p.supplier), None)
    document_no = next((p.document_no for p in pages if p.document_no), None)
    date = next((p.date for p in pages if p.date), None)
    notes = []
    if date:
        fixed = repair_year(date, int(state.batch["created_at"][:4]))
        if fixed and fixed != date:
            notes.append(f"date year repaired: read {date}, recorded {fixed}")
            date = fixed
    return supplier, document_no, date, notes


def system_line(idx: int, n: NormalisedLine, flags: list[dict], notes: list[str]) -> LineState:
    line = n.line
    repairs = [f"read with a repair: {x}" for x in notes if not x.startswith("line does not reconcile")]
    return LineState(
        idx=idx, entered_by="system", printed=line.to_dict(), item_code=n.item_code, item_name=n.item_name,
        base_unit=n.base_unit, quantity=line.quantity, unit=line.unit,
        base_quantity=n.base_quantity if n.converted else None,
        cost_cents=cents(line.line_total), vat_cents=cents(line.vat), factor=n.factor,
        conversion_basis=n.conversion_basis or None, match_method=n.match_method or None,
        match_score=n.match_score or None, refusal=n.refusal, reason="; ".join(([n.reason] if n.reason else []) + repairs),
        flags=flags, candidates=[{"code": c["code"], "name": c["name"], "score": c["score"]} for c in n.alternatives],
        star=bool(n.refusal or flags or line.quantity is None))


def reparse(state: BatchState, inv: InvoiceState, key, load_ocr: OcrLoader = load_ocr_file) -> bool:
    """Read an invoice's lines again from its pages. False when a page is still being read."""
    pages = [state.photos[p] for p in inv.pages]
    carried = state.carried.pop(inv.invoice_id, [])
    if any(p.state in UNREAD for p in pages):
        inv.lines = inv.lines + carried
        for k, l in enumerate(inv.lines):
            l.idx = k
        return False
    inv.supplier, inv.document_no, inv.date, warnings = read_header(state, inv)
    supplier = inv.eff_supplier
    readable = []
    for p in pages:
        result = load_ocr(p) if p.state == "read" else None
        if result is None:
            warnings.append(f"page {p.ordinal}: could not be read" if p.state == "failed"
                            else f"page {p.ordinal}: its recognised words are missing from the OCR cache")
            continue
        readable.append((Page(page_no=p.ordinal, image_path=Path(p.storage_path).name, source_pdf="phone",
                              supplier=supplier, document_no=inv.eff_document_no, date=inv.eff_date,
                              invoice_id=inv.invoice_id, page_of=p.page_of), result))
    record = parse_invoice(readable) if readable else InvoiceRecord(inv.invoice_id, supplier, None,
                                                                    inv.eff_document_no, inv.eff_date)
    inv.layout = record.layout
    inv.stated_total_excl, inv.stated_vat = cents(record.stated_total_excl), cents(record.stated_vat)
    inv.stated_total_incl = cents(record.stated_total_incl)
    inv.warnings = warnings + record.warnings

    normalised = [normalise_line(line, supplier, key.matcher, key.medians) for line in record.lines]
    notes: dict[int, list[str]] = defaultdict(list)
    for w in record.warnings:
        m = LINE_NOTE.match(w)
        if m:
            notes[int(m.group(1)) - 1].append(m.group(2))
    flags: dict[int, list[dict]] = defaultdict(list)
    points = [PricePoint(n.item_code, n.base_quantity, n.line.line_total, inv.invoice_id, i)
              for i, n in enumerate(normalised) if n.converted and n.line.line_total is not None]
    for f in find_price_anomalies(points, key.history):
        flags[f.line_index].append({"kind": f.kind, "reason": f.reason})
    for i, found in notes.items():
        if any(x.startswith("line does not reconcile") for x in found):
            flags[i].append({"kind": "does_not_reconcile",
                             "reason": "quantity x price does not equal the line total: check the figures on the paper"})
    fresh = [system_line(i, n, flags[i], notes[i]) for i, n in enumerate(normalised)]
    inv.lines = carry_over(inv.lines + carried, fresh)
    inv.parsed = db.now()
    return True


def _same_printed_line(printed: dict, lines: list[LineState], taken: set[int]) -> int | None:
    wording = normalise_text(printed.get("description") or "")
    total, quantity = cents(printed.get("line_total")), printed.get("quantity")
    for j, l in enumerate(lines):
        p = l.printed
        if j not in taken and p is not None and cents(p.get("line_total")) == total \
                and p.get("quantity") == quantity and normalise_text(p.get("description") or "") == wording:
            return j
    if total is None:
        return None
    best = None
    for j, l in enumerate(lines):
        p = l.printed
        if j in taken or p is None or cents(p.get("line_total")) != total:
            continue
        score = text_score(wording, normalise_text(p.get("description") or ""))
        if score >= CARRY_MIN_TEXT_SCORE and (best is None or score > best[0]):
            best = (score, j)
    return best[1] if best else None


def carry_over(old: list[LineState], fresh: list[LineState]) -> list[LineState]:
    """The fresh reading, with the manager's entries and removals laid back over the lines they were made on."""
    lines = list(fresh)
    taken: set[int] = set()
    typed: list[LineState] = []
    for o in old:
        if o.entered_by != "manager" and not o.removed:
            continue
        j = _same_printed_line(o.printed, lines, taken) if o.printed is not None else None
        if j is None:
            if o.entered_by == "manager" and not o.removed:
                typed.append(o)          # nothing printed matches any more, but the manager's entry stands
            continue
        taken.add(j)
        if o.entered_by == "manager":
            lines[j] = replace(o, printed=lines[j].printed)
        if o.removed:
            lines[j] = replace(lines[j], removed=True)
    merged = lines + typed
    for k, l in enumerate(merged):
        l.idx = k
    return merged


def settle_totals(inv: InvoiceState) -> None:
    """Lines sum and 'adds up' from the lines as they now stand, the manager's entries included.
    Same tolerance as the parser: ten cents, or 0.1% of the stated total."""
    costs = [l.cost_cents for l in inv.live_lines() if l.cost_cents is not None]
    inv.lines_sum_excl = sum(costs) if costs else None
    if inv.lines_sum_excl is None or inv.stated_total_excl is None:
        inv.adds_up = None
    else:
        tolerance = max(ADDS_UP_TOLERANCE * 100, 0.001 * abs(inv.stated_total_excl))
        inv.adds_up = abs(inv.lines_sum_excl - inv.stated_total_excl) <= tolerance


# --- 4: tabs -------------------------------------------------------------------------------------

REFUSAL_WORDS = {
    "no_item_match": "no stock item matched",
    "multiple_item_match": "two stock items are equally likely",
    "base_unit_absent": "the stock item has no base unit in its name",
    "pack_notation_absent": "the pack size is not printed, so the base quantity is not known",
    "unit_conflict": "the invoice's unit does not fit the stock item's unit",
    "quantity_missing": "no quantity was read",
    "non_stock_charge": "a charge, not a stock item",
}
FLAG_WORDS = {
    "price_unit_error": "the price suggests the wrong unit",
    "price_outlier": "the price is far from its usual",
    "qty_cost_transposed": "quantity and cost look swapped",
    "qty_zero_with_cost": "a cost against a quantity of zero",
    "price_sign_error": "quantity and cost have opposite signs",
    "placeholder_item": "a catch-all stock item",
    "does_not_reconcile": "quantity x price does not equal the line total",
}


def line_problem(l: LineState) -> str:
    if l.refusal:
        return REFUSAL_WORDS.get(l.refusal, l.refusal.replace("_", " "))
    if l.flags:
        kind = l.flags[0].get("kind", "")
        return FLAG_WORDS.get(kind, kind.replace("_", " "))
    return "no quantity was read"


def invoice_title(document_no: str | None, ordinals: list[int]) -> str:
    """'INV 1844378 (pages 1, 2)': the number as printed, and where the sheets sit in the scanner session."""
    if document_no:
        number = document_no if document_no.upper().startswith("INV") else f"INV {document_no}"
    else:
        number = "Invoice without a number"
    if not ordinals:
        return number
    return f"{number} (page{'' if len(ordinals) == 1 else 's'} {', '.join(str(o) for o in sorted(ordinals))})"


@dataclass
class Duplicate:
    other: str | None      # the invoice this one repeats, when it is known
    reason: str


def _signature(printed_lines) -> tuple:
    pairs = [((p.get("description") or "").lower(), p.get("quantity")) for p in printed_lines]
    return tuple(sorted(pairs, key=lambda x: (x[0], -1.0 if x[1] is None else x[1])))


def _total(stated: int | None, lines_sum: int | None) -> float | None:
    return rands(stated if stated is not None else lines_sum)


def _same_photographs(conn, state: BatchState, live: list[InvoiceState], hard: dict[str, Duplicate]) -> None:
    mine = [(pid, inv) for inv in live for pid in inv.pages]
    shas = sorted({state.photos[pid].sha256 for pid, _ in mine})
    if not shas:
        return
    others = db.all_rows(
        conn, "SELECT p.sha256, p.ordinal, p.batch_id, b.created, b.created_at, ip.invoice_id, i.status AS invoice_status"
              " FROM photos p JOIN batches b ON b.batch_id = p.batch_id"
              " LEFT JOIN invoice_pages ip ON ip.photo_id = p.photo_id LEFT JOIN invoices i ON i.invoice_id = ip.invoice_id"
              f" WHERE p.state <> 'replaced' AND p.batch_id <> ? AND p.sha256 IN ({','.join('?' * len(shas))})"
              " ORDER BY b.created, p.ordinal", state.batch_id, *shas)
    for pid, inv in mine:
        if inv.invoice_id in hard:
            continue
        photo = state.photos[pid]
        for other_pid, other in mine:
            o = state.photos[other_pid]
            if other is not inv and o.sha256 == photo.sha256 and (o.ordinal, o.received) < (photo.ordinal, photo.received):
                hard[inv.invoice_id] = Duplicate(other.invoice_id, f"page {photo.ordinal} is the same photograph as page"
                                                                   f" {o.ordinal} in this batch")
                break
        else:
            for r in others:
                if r["sha256"] != photo.sha256 or (r["created"], r["batch_id"]) > (state.batch["created"], state.batch_id):
                    continue
                if r["invoice_id"] and r["invoice_status"] != "open":
                    continue
                hard[inv.invoice_id] = Duplicate(r["invoice_id"], f"page {photo.ordinal} is the same photograph as page"
                                                                  f" {r['ordinal']} of the batch from {r['created_at'][:10]}")
                break


def _submitted_invoices(conn, state: BatchState, suppliers: list[str]) -> list[dict]:
    """Open invoices from these suppliers in the shop's batches already submitted."""
    return db.all_rows(
        conn, "SELECT i.invoice_id, b.submitted_at, COALESCE(i.supplier_override, i.supplier) AS supplier,"
              " COALESCE(i.document_no_override, i.document_no) AS document_no, COALESCE(i.date_override, i.date) AS date,"
              " i.stated_total_excl, i.lines_sum_excl FROM invoices i JOIN batches b ON b.batch_id = i.batch_id"
              " WHERE b.shop_id = ? AND b.status = 'submitted' AND b.batch_id <> ? AND i.status = 'open'"
              f" AND COALESCE(i.supplier_override, i.supplier) IN ({','.join('?' * len(suppliers))})",
        state.shop_id, state.batch_id, *suppliers)


def _printed_of_row(conn, invoice_id: str) -> list[dict]:
    return [db.loads(r["printed"]) for r in conn.execute(
        "SELECT printed FROM lines WHERE invoice_id = ? AND removed = 0 AND printed IS NOT NULL", (invoice_id,))]


def _number_candidates(conn, state: BatchState, inv: InvoiceState, earlier: list[InvoiceState],
                       submitted: list[dict]) -> list[tuple]:
    """(invoice id, number, total, date, where, printed lines) for every earlier invoice from the same supplier:
    those before it in this batch, then those in batches already submitted. Printed lines are fetched only if asked."""
    candidates = [(o.invoice_id, o.eff_document_no, _total(o.stated_total_excl, o.lines_sum_excl), o.eff_date,
                   f"{invoice_title(o.eff_document_no, state.ordinals(o))} in this batch",
                   lambda o=o: [l.printed for l in o.live_lines() if l.printed])
                  for o in earlier if o.eff_supplier == inv.eff_supplier]
    candidates += [(r["invoice_id"], r["document_no"], _total(r["stated_total_excl"], r["lines_sum_excl"]),
                    r["date"], (f"{invoice_title(r['document_no'], [])} in the batch submitted on"
                                f" {(r['submitted_at'] or '')[:10]}"),
                    lambda r=r: _printed_of_row(conn, r["invoice_id"]))
                   for r in submitted if r["supplier"] == inv.eff_supplier]
    return candidates


def _record_duplicates(found, invoice_id: str, other_id: str, where: str, gap: int | None,
                       hard: dict[str, Duplicate], soft: dict[str, list[str]]) -> None:
    for f in found:
        if f.kind == "duplicate_invoice" and invoice_id not in hard:
            hard[invoice_id] = Duplicate(other_id, f"the same invoice as {where}: same supplier,"
                                                   " number and total")
        elif f.kind == "duplicate_reference" and invoice_id not in hard:
            hard[invoice_id] = Duplicate(other_id, f"same invoice number as {where}, but the totals"
                                                   " differ: a misread number, or the supplier reused it")
        elif f.kind == "possible_duplicate":
            soft[invoice_id].append(f"identical lines and total to {where} under a different number,"
                                    f" {gap} day{'' if gap == 1 else 's'} apart")


def _same_numbers(conn, state: BatchState, live: list[InvoiceState], hard: dict[str, Duplicate],
                  soft: dict[str, list[str]]) -> None:
    suppliers = sorted({i.eff_supplier for i in live if i.eff_supplier})
    if not suppliers:
        return
    submitted = _submitted_invoices(conn, state, suppliers)

    earlier: list[InvoiceState] = []
    for inv in live:
        if inv.eff_supplier:
            this = InvoiceSummary(inv.invoice_id, inv.eff_supplier, inv.eff_document_no,
                                  _total(inv.stated_total_excl, inv.lines_sum_excl), inv.eff_date, "scan")
            for other_id, number, total, date, where, printed in _number_candidates(conn, state, inv, earlier, submitted):
                same_ref = bool(ref_keys(number) & ref_keys(inv.eff_document_no))
                gap = days_apart(date, inv.eff_date)
                close = total is not None and this.total is not None and abs(total - this.total) <= 0.01
                if not same_ref and not (close and gap is not None and gap <= 3):
                    continue
                this.line_signature = _signature(l.printed for l in inv.live_lines() if l.printed)
                other = InvoiceSummary(other_id, inv.eff_supplier, number, total, date, "scan", _signature(printed()))
                _record_duplicates(find_duplicates([other, this]), inv.invoice_id, other_id, where, gap, hard, soft)
        earlier.append(inv)


def find_batch_duplicates(conn, state: BatchState) -> tuple[dict[str, Duplicate], dict[str, list[str]]]:
    """(duplicates by invoice, 'possible duplicate' notes by invoice). Only the later of two is the duplicate:
    later in this batch by scanner position, or anything matching a batch already submitted."""
    live = sorted((i for i in state.invoices.values() if i.status == "open" and i.pages),
                  key=lambda i: min(state.ordinals(i)))
    hard: dict[str, Duplicate] = {}
    soft: dict[str, list[str]] = defaultdict(list)
    _same_photographs(conn, state, live, hard)
    _same_numbers(conn, state, live, hard, soft)
    return hard, soft


def _short(error: str | None) -> str:
    text = (error or "").strip().splitlines()[0] if error and error.strip() else ""
    return text[:80]


POSSIBLE_DUPLICATE = "possible duplicate: "


def notes_of(inv: InvoiceState) -> list[str]:
    """Notes that do not decide the tab. Section 4 of the contract lists what does; identical lines and total
    under another invoice number a few days apart (checks.duplicates' possible duplicate) is not on that list,
    so it is shown beside the invoice for the manager to judge instead."""
    return [w for w in inv.warnings if w.startswith(POSSIBLE_DUPLICATE)]


def set_notes(inv: InvoiceState, possible_duplicates: list[str]) -> None:
    inv.warnings = [w for w in inv.warnings if not w.startswith(POSSIBLE_DUPLICATE)] + \
        [POSSIBLE_DUPLICATE + note for note in possible_duplicates]


def decide_tab(state: BatchState, inv: InvoiceState, dup: Duplicate | None) -> tuple[str, list[str], str | None]:
    if inv.status == "deleted":
        return "deleted", ["deleted by the manager"], inv.duplicate_of
    pages = [state.photos[p] for p in inv.pages]
    waiting = sorted(p.ordinal for p in pages if p.state in UNREAD)
    if waiting and inv.parsed is not None:
        # a retaken page: the invoice keeps its tab until the new photograph is read
        kept = [r for r in inv.tab_reasons if not any(r.startswith(f"page {o}:") for o in waiting)]
        return inv.tab, kept + [f"page {o}: the retake is still being read" for o in waiting], inv.duplicate_of
    if dup is not None and not inv.not_duplicate:
        return "duplicates", [dup.reason], dup.other

    retake = []
    for p in pages:
        if p.state == "failed":
            retake.append(f"page {p.ordinal}: recognition failed" + (f" ({_short(p.last_error)})" if _short(p.last_error) else ""))
        else:
            retake += [f"page {p.ordinal}: {x}" for x in p.assessment()["problems"]]
    live = inv.live_lines()
    if not live and any(p.state == "read" for p in pages):
        retake.append("no line items found")
    if retake:
        return "retake", retake, None

    corrections = [f"line {l.idx + 1}: {line_problem(l)}" for l in live if l.star]
    if not inv.eff_supplier:
        corrections.append("supplier not recognised")
    if not inv.eff_document_no:
        corrections.append("no invoice number")
    if not inv.eff_date:
        corrections.append("no date")
    if inv.adds_up is False:
        corrections.append(f"does not add up: the lines sum to R{inv.lines_sum_excl / 100:,.2f} but the invoice"
                           f" states R{inv.stated_total_excl / 100:,.2f} excl. VAT")
    elif inv.adds_up is None:
        corrections.append("no total excl. VAT was read, so the lines cannot be checked against it")
    if corrections:
        return "corrections", corrections, None
    return "good", [], None


# --- 5: the whole update, written back only if nobody changed the batch meanwhile ----------------

def rebuild(conn: sqlite3.Connection, state: BatchState, keys, load_ocr: OcrLoader = load_ocr_file) -> None:
    if not state.submitted:
        # a submitted batch keeps the invoices it was reported with
        assign(state, group_pages(participants(state), deleted_pages(state)))
    due = {i for i in state.reparse if i in state.invoices}
    for inv in state.invoices.values():
        if inv.parsed is None or any((state.photos[p].read or 0) > inv.parsed for p in inv.pages):
            due.add(inv.invoice_id)
    due = {i for i in due if state.invoices[i].status == "open"}
    if due:
        key = keys.get(conn, state.shop_id)
        for invoice_id in sorted(due):
            inv = state.invoices[invoice_id]
            if reparse(state, inv, key, load_ocr) and state.submitted:
                inv.needs_report = True          # read again after its report: it joins the next one
    for inv in state.invoices.values():
        settle_totals(inv)
    hard, soft = find_batch_duplicates(conn, state)
    found = {e["invoice_id"] for e in state.events if e["kind"] == "invoice_found"}
    for inv in state.invoices.values():
        tab, reasons, duplicate_of = decide_tab(state, inv, hard.get(inv.invoice_id))
        before = None if inv.invoice_id in found else inv.tab
        if tab != before:
            state.event("tab_changed", invoice_id=inv.invoice_id, before=before, after=tab, reasons=reasons)
        inv.tab, inv.tab_reasons, inv.duplicate_of = tab, reasons, duplicate_of
        set_notes(inv, [] if inv.not_duplicate or inv.status != "open" else soft.get(inv.invoice_id, []))


INVOICE_COLUMNS = ("status", "supplier", "document_no", "date", "supplier_override", "document_no_override",
                   "date_override", "layout", "stated_total_excl", "stated_vat", "stated_total_incl", "lines_sum_excl",
                   "adds_up", "warnings", "tab", "tab_reasons", "duplicate_of", "not_duplicate", "parsed",
                   "needs_report", "reported_in")
LINE_COLUMNS = ("idx", "printed", "entered_by", "removed", "item_code", "item_name", "base_unit", "free_text",
                "quantity", "unit", "base_quantity", "cost_cents", "vat_cents", "factor", "conversion_basis",
                "match_method", "match_score", "refusal", "reason", "flags", "candidates", "star")
_JSON = {"warnings", "tab_reasons", "printed", "flags", "candidates"}
_BOOL = {"adds_up", "not_duplicate", "needs_report", "removed", "star"}


def _sql_value(column: str, value):
    if column in _JSON:
        return None if value is None else db.dumps(value)
    if column in _BOOL:
        return None if value is None else int(value)
    return value


def _write_invoice(conn: sqlite3.Connection, inv: InvoiceState, before: InvoiceState | None, now: str) -> None:
    inv.updated_at = now
    row = inv.row()
    values = [_sql_value(c, row[c]) for c in INVOICE_COLUMNS]
    if before is None:
        conn.execute(f"INSERT INTO invoices (invoice_id, batch_id, created_at, updated_at, {', '.join(INVOICE_COLUMNS)})"
                     f" VALUES (?, ?, ?, ?, {', '.join('?' * len(INVOICE_COLUMNS))})",
                     (inv.invoice_id, inv.batch_id, inv.created_at, now, *values))
    else:
        conn.execute(f"UPDATE invoices SET updated_at = ?, {', '.join(c + ' = ?' for c in INVOICE_COLUMNS)}"
                     " WHERE invoice_id = ?", (now, *values, inv.invoice_id))


def _write_lines(conn: sqlite3.Connection, inv: InvoiceState, before: InvoiceState | None, now: str) -> None:
    """Replace the invoice's lines; a line that did not change keeps its time of last change."""
    old_rows = {l.idx: l for l in (before.lines if before else [])}
    conn.execute("DELETE FROM lines WHERE invoice_id = ?", (inv.invoice_id,))
    for l in inv.lines:
        prior = old_rows.get(l.idx)
        l.updated_at = prior.updated_at if prior is not None and prior.row() == l.row() and prior.updated_at else now
        line = l.row()
        conn.execute(f"INSERT INTO lines (invoice_id, updated_at, {', '.join(LINE_COLUMNS)})"
                     f" VALUES (?, ?, {', '.join('?' * len(LINE_COLUMNS))})",
                     (inv.invoice_id, l.updated_at, *[_sql_value(c, line[c]) for c in LINE_COLUMNS]))


def _write_pages(conn: sqlite3.Connection, repaged: list[InvoiceState]) -> None:
    for inv in repaged:
        conn.execute("DELETE FROM invoice_pages WHERE invoice_id = ?", (inv.invoice_id,))
    for inv in repaged:
        for page_no, photo_id in enumerate(inv.pages, start=1):
            conn.execute("INSERT INTO invoice_pages (invoice_id, photo_id, page_no) VALUES (?, ?, ?)",
                         (inv.invoice_id, photo_id, page_no))


def _batch_status(state: BatchState) -> str:
    outstanding = sum(1 for p in state.active_photos() if p.state in UNREAD)
    status = state.batch["status"]
    if status == "processing" and outstanding == 0:
        status = "ready"
    elif status == "ready" and outstanding:
        status = "processing"
    return status


def write_batch(conn: sqlite3.Connection, state: BatchState, original: BatchState) -> bool:
    now = db.iso()
    changed = False
    for invoice_id in original.invoices.keys() - state.invoices.keys():
        conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
        changed = True
    repaged = []
    for inv in state.invoices.values():
        before = original.invoices.get(inv.invoice_id)
        lines_changed = before is None or [l.row() for l in before.lines] != [l.row() for l in inv.lines]
        pages_changed = before is None or before.pages != inv.pages
        if before is not None and not lines_changed and not pages_changed and before.row() == inv.row():
            continue
        changed = True
        _write_invoice(conn, inv, before, now)
        if pages_changed:
            repaged.append(inv)
        if lines_changed:
            _write_lines(conn, inv, before, now)
    _write_pages(conn, repaged)
    for e in state.events:
        fields = dict(e)
        db.event(conn, fields.pop("kind"), shop_id=state.shop_id, batch_id=state.batch_id, **fields)

    status = _batch_status(state)
    if changed or state.events or status != original.batch["status"]:
        conn.execute("UPDATE batches SET status = ?, revision = revision + 1 WHERE batch_id = ?",
                     (status, state.batch_id))
        state.batch["status"] = status
        state.batch["revision"] = original.batch["revision"] + 1
    return changed


def update_batch(conn: sqlite3.Connection, batch_id: str, keys, change: Callable[[BatchState], None] | None = None,
                 reparse_ids=(), load_ocr: OcrLoader = load_ocr_file, attempts: int = 4) -> BatchState:
    """Apply change (a manager action, or nothing after a page is read), rebuild, and write back.

    The work happens outside any transaction; the write only goes in if the batch's revision is still the one
    it started from. The last attempt holds the write lock throughout, so a busy batch still gets its update."""
    for attempt in range(attempts):
        locked = attempt == attempts - 1
        if locked:
            conn.execute("BEGIN IMMEDIATE")
        try:
            original = load_batch(conn, batch_id)
            if original is None:
                raise LookupError(f"no batch {batch_id}")
            state = copy.deepcopy(original)
            state.reparse |= set(reparse_ids)
            if change is not None:
                change(state)
            rebuild(conn, state, keys, load_ocr)
            if not locked:
                conn.execute("BEGIN IMMEDIATE")
                revision = conn.execute("SELECT revision FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()[0]
                if revision != original.batch["revision"]:
                    conn.execute("ROLLBACK")
                    continue
            write_batch(conn, state, original)
            conn.execute("COMMIT")
            return state
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    raise RuntimeError("unreachable")
