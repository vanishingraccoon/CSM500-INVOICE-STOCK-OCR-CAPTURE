"""Batches and invoices as the phone and the portal see them, and every change a person makes to them.

Every change goes through reading.update_batch: the manager's action is applied, the batch is rebuilt (tabs,
totals, duplicates), and the result is written in one short transaction. So the tab the phone gets back after
an edit is always the one the server would decide from scratch. Each action is also an event, with what the
line or invoice was before and after: how often a person had to intervene, and on what, is a result in itself.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date as _date

from app import db, reading, storage
from app.reading import BatchState, InvoiceState, LineState, cents, invoice_title, rands
from ingest.suppliers import BY_KEY
from models import Line
from normaliser.convert import convert

MAX_MONEY = 10_000_000.0
MAX_QUANTITY = 1_000_000.0
TABS = ("good", "corrections", "retake", "duplicates")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def not_found(what: str = "That") -> ApiError:
    return ApiError(404, "not_found", f"{what} does not exist for this shop.")


def regrouped() -> ApiError:
    return ApiError(409, "invoice_regrouped", "That invoice was just regrouped with another; fetch the batch again.")


# --- batches --------------------------------------------------------------------------------------

def create_batch(conn: sqlite3.Connection, device: dict, client_batch_key: str, page_count: int | None) -> tuple[dict, bool]:
    """(batch, created). The same client_batch_key from the same device returns the batch it made before."""
    with db.transaction(conn):
        existing = db.one(conn, "SELECT * FROM batches WHERE device_id = ? AND client_batch_key = ?",
                          device["device_id"], client_batch_key)
        if existing:
            return existing, False
        batch_id, ts = db.new_id("b"), db.now()
        conn.execute("INSERT INTO batches (batch_id, shop_id, device_id, client_batch_key, page_count, created_at, created)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (batch_id, device["shop_id"], device["device_id"], client_batch_key, page_count, db.iso(ts), ts))
        db.event(conn, "batch_created", shop_id=device["shop_id"], device_id=device["device_id"], batch_id=batch_id,
                 page_count=page_count)
    return db.one(conn, "SELECT * FROM batches WHERE batch_id = ?", batch_id), True


def get_batch(conn: sqlite3.Connection, batch_id: str, shop_id: str) -> dict:
    batch = db.one(conn, "SELECT * FROM batches WHERE batch_id = ? AND shop_id = ?", batch_id, shop_id)
    if batch is None:
        raise not_found("That batch")
    return batch


def batch_created_json(batch: dict) -> dict:
    return {"batch_id": batch["batch_id"], "status": batch["status"], "created_at": batch["created_at"]}


def complete(conn: sqlite3.Connection, batch: dict, device: dict) -> str:
    with db.transaction(conn):
        b = db.one(conn, "SELECT * FROM batches WHERE batch_id = ?", batch["batch_id"])
        outstanding = conn.execute("SELECT COUNT(*) FROM photos WHERE batch_id = ? AND state IN ('stored', 'reading')",
                                   (b["batch_id"],)).fetchone()[0]
        status = b["status"]
        if status == "uploading":
            status = "processing" if outstanding else "ready"
            conn.execute("UPDATE batches SET status = ?, completed_at = ?, revision = revision + 1 WHERE batch_id = ?",
                         (status, db.iso(), b["batch_id"]))
            photos = conn.execute("SELECT COUNT(*) FROM photos WHERE batch_id = ? AND state <> 'replaced'",
                                  (b["batch_id"],)).fetchone()[0]
            db.event(conn, "batch_completed", shop_id=b["shop_id"], device_id=device["device_id"],
                     batch_id=b["batch_id"], photos=photos, page_count=b["page_count"], still_reading=outstanding)
    return "processing" if status in ("uploading", "processing") else status


def progress_of(state: BatchState) -> dict:
    active = state.active_photos()
    count = lambda s: sum(1 for p in active if p.state == s)
    return {"photos": len(active), "waiting": count("stored"), "reading": count("reading"), "read": count("read"),
            "failed": count("failed")}


def counts_of(state: BatchState) -> dict:
    live = [i for i in state.invoices.values() if i.status == "open"]
    return {tab: sum(1 for i in live if i.tab == tab) for tab in TABS}


def etag(state: BatchState) -> str:
    """Unquoted; Flask quotes it. Every change to what the batch returns moves the revision."""
    return f"{state.batch_id}-r{state.batch['revision']}"


def summary_json(state: BatchState) -> dict:
    b = state.batch
    return {"batch_id": b["batch_id"], "status": b["status"], "created_at": b["created_at"],
            "submitted_at": b["submitted_at"], "progress": progress_of(state), "counts": counts_of(state)}


def ordered_invoices(state: BatchState, include_deleted: bool = True) -> list[InvoiceState]:
    invoices = [i for i in state.invoices.values() if include_deleted or i.status == "open"]
    return sorted(invoices, key=lambda i: (min(state.ordinals(i), default=10 ** 6), i.invoice_id))


def batch_json(state: BatchState) -> dict:
    return {**summary_json(state), "invoices": [invoice_json(state, i) for i in ordered_invoices(state)]}


def summaries(conn: sqlite3.Connection, shop_id: str, limit: int = 100) -> list[dict]:
    """Batch summaries, newest first, counted in SQL so a long history does not load every line."""
    rows = db.all_rows(conn, "SELECT b.*, d.name AS device_name FROM batches b LEFT JOIN devices d ON d.device_id = b.device_id"
                             " WHERE b.shop_id = ? ORDER BY b.created DESC LIMIT ?", shop_id, limit)
    if not rows:
        return []
    ids = [r["batch_id"] for r in rows]
    marks = ",".join("?" * len(ids))
    photos: dict[str, dict[str, int]] = {i: {} for i in ids}
    for batch_id, state, n in conn.execute(f"SELECT batch_id, state, COUNT(*) FROM photos WHERE batch_id IN ({marks})"
                                           " AND state <> 'replaced' GROUP BY batch_id, state", ids):
        photos[batch_id][state] = n
    tabs: dict[str, dict[str, int]] = {i: {} for i in ids}
    for batch_id, tab, n in conn.execute(f"SELECT batch_id, tab, COUNT(*) FROM invoices WHERE batch_id IN ({marks})"
                                         " AND status = 'open' GROUP BY batch_id, tab", ids):
        tabs[batch_id][tab] = n
    out = []
    for b in rows:
        p = photos[b["batch_id"]]
        out.append({"batch_id": b["batch_id"], "status": b["status"], "created_at": b["created_at"],
                    "submitted_at": b["submitted_at"],
                    "progress": {"photos": sum(p.values()), "waiting": p.get("stored", 0), "reading": p.get("reading", 0),
                                 "read": p.get("read", 0), "failed": p.get("failed", 0)},
                    "counts": {t: tabs[b["batch_id"]].get(t, 0) for t in TABS},
                    "device_name": b["device_name"]})
    return out


# --- invoices and lines as JSON ------------------------------------------------------------------

def _money(value) -> float | None:
    return None if value is None else round(float(value), 2)


def line_json(l: LineState) -> dict:
    p = l.printed
    return {
        "index": l.idx, "star": l.star, "entered_by": l.entered_by,
        "printed": None if p is None else {
            "description": p.get("description"), "quantity": p.get("quantity"), "unit": p.get("unit"),
            "pack": p.get("pack"), "unit_price": _money(p.get("unit_price")), "line_total": _money(p.get("line_total"))},
        "item": {"code": l.item_code, "name": l.item_name, "base_unit": l.base_unit} if l.item_code else None,
        "base_quantity": l.base_quantity, "refusal": l.refusal, "reason": l.reason,
        "flags": [{"kind": f.get("kind"), "reason": f.get("reason")} for f in l.flags],
        "candidates": l.candidates,
        # beyond the contract's example: what the report will use for this line
        "free_text": l.free_text, "quantity": l.quantity, "unit": l.unit,
        "cost": rands(l.cost_cents), "vat": rands(l.vat_cents),
    }


def invoice_json(state: BatchState, inv: InvoiceState) -> dict:
    supplier = inv.eff_supplier
    pages = []
    for page_no, pid in enumerate(inv.pages, start=1):
        p = state.photos[pid]
        pages.append({"photo_id": pid, "ordinal": p.ordinal, "page_no": page_no, "state": p.state,
                      "quality": p.assessment()})
    return {
        "invoice_id": inv.invoice_id, "tab": inv.tab, "tab_reasons": inv.tab_reasons,
        "supplier": supplier, "supplier_name": BY_KEY[supplier].pilot_name if supplier in BY_KEY else None,
        "document_no": inv.eff_document_no, "date": inv.eff_date,
        "title": invoice_title(inv.eff_document_no, state.ordinals(inv)),
        "pages": pages,
        "stated_total_excl": rands(inv.stated_total_excl), "stated_vat": rands(inv.stated_vat),
        "stated_total_incl": rands(inv.stated_total_incl),
        "lines_sum_excl": rands(inv.lines_sum_excl), "adds_up": inv.adds_up,
        "duplicate_of": inv.duplicate_of,
        "lines": [line_json(l) for l in inv.live_lines()],
        "updated_at": inv.updated_at,
        # beyond the contract: notes that do not decide the tab (a possible duplicate under another number)
        "notes": reading.notes_of(inv),
    }


# --- manager actions ------------------------------------------------------------------------------

@dataclass
class LineEntry:
    item_code: str | None
    free_text: str | None
    quantity: float
    unit: str | None
    cost: float


def _number(body: dict, name: str, limit: float) -> float:
    value = body.get(name)
    if value is None:
        raise ApiError(400, "invalid_body", f"'{name}' is required.")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ApiError(400, "invalid_body", f"'{name}' must be a finite number.")
    if abs(value) > limit:
        raise ApiError(400, "invalid_body", f"'{name}' is out of range.")
    return float(value)


def _text(body: dict, name: str, max_length: int) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ApiError(400, "invalid_body", f"'{name}' must be text of 1 to {max_length} characters.")
    return value.strip()


def parse_line_entry(body) -> LineEntry:
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_body", "Send a JSON object.")
    item_code, free_text = _text(body, "item_code", 40), _text(body, "free_text", 200)
    if (item_code is None) == (free_text is None):
        raise ApiError(400, "invalid_body", "Send either item_code or free_text, not both.")
    unit = _text(body, "unit", 30)
    return LineEntry(item_code, free_text, _number(body, "quantity", MAX_QUANTITY), unit, _number(body, "cost", MAX_MONEY))


def _vat_for(printed: dict | None, cost_cents: int) -> int | None:
    """VAT the manager did not type: the printed line's, scaled if the manager changed the cost."""
    if not printed or printed.get("vat") is None or printed.get("line_total") in (None, 0):
        return None
    printed_total, printed_vat = cents(printed["line_total"]), cents(printed["vat"])
    return printed_vat if printed_total == cost_cents else round(cost_cents * printed_vat / printed_total)


def manager_line(key, entry: LineEntry, idx: int, printed: dict | None) -> LineState:
    cost = cents(entry.cost)
    if entry.free_text is not None:
        return LineState(idx=idx, entered_by="manager", printed=printed, free_text=entry.free_text,
                         quantity=entry.quantity, unit=entry.unit, base_quantity=entry.quantity, cost_cents=cost,
                         vat_cents=_vat_for(printed, cost), match_method="manager",
                         reason="typed by the manager: not in the key, so the quantity is taken in Pilot's base unit")
    item = key.by_code.get(entry.item_code)
    if item is None:
        raise ApiError(422, "unknown_item", "That stock item is not in this shop's key.")
    unit = entry.unit if entry.unit is not None else (printed or {}).get("unit")
    line = Line(description=(printed or {}).get("description") or "", quantity=entry.quantity, unit=unit,
                unit_price=entry.cost / entry.quantity if entry.quantity else None, line_total=entry.cost)
    conv = convert(line, item, key.medians.get(item.code))
    return LineState(
        idx=idx, entered_by="manager", printed=printed, item_code=item.code, item_name=item.name,
        base_unit=item.base.unit, quantity=entry.quantity, unit=unit,
        base_quantity=round(entry.quantity * conv.factor, 4) if conv.ok else None, cost_cents=cost,
        vat_cents=_vat_for(printed, cost), factor=conv.factor if conv.ok else None,
        conversion_basis=conv.basis or None, match_method="manager", refusal=conv.refusal,
        reason=f"chosen by the manager; {conv.reason}", star=not conv.ok)


def was_regrouped(conn: sqlite3.Connection, invoice_id: str, shop_id: str) -> bool:
    """Did this id disappear because its pages joined another invoice? The events say so, and they are never
    deleted, so an id the phone still holds can be told apart from one that never existed."""
    return db.one(conn, "SELECT 1 FROM events WHERE kind = 'invoice_merged' AND shop_id = ?"
                        " AND json_extract(detail, '$.absorbed') = ?", shop_id, invoice_id) is not None


def locate_invoice(conn: sqlite3.Connection, invoice_id: str, shop_id: str) -> str:
    row = db.one(conn, "SELECT i.batch_id FROM invoices i JOIN batches b ON b.batch_id = i.batch_id"
                       " WHERE i.invoice_id = ? AND b.shop_id = ?", invoice_id, shop_id)
    if row is None:
        raise regrouped() if was_regrouped(conn, invoice_id, shop_id) else not_found("That invoice")
    return row["batch_id"]


def invoice_for_shop(conn: sqlite3.Connection, invoice_id: str, shop_id: str) -> dict:
    """One invoice on its own, in the shape a batch already gives it. The phone follows a duplicate to the
    invoice it repeats, and that one may sit in a batch the phone never loaded."""
    state = reading.load_batch(conn, locate_invoice(conn, invoice_id, shop_id))
    return result_invoice(state, invoice_id)


def _invoice(state: BatchState, invoice_id: str, editable: bool = True) -> InvoiceState:
    inv = state.invoices.get(invoice_id)
    if inv is None:
        raise regrouped()
    if editable and inv.status == "deleted":
        raise ApiError(409, "invoice_deleted", "That invoice was deleted.")
    return inv


def _touched(state: BatchState, inv: InvoiceState) -> None:
    if state.submitted:
        inv.needs_report = True        # changed after its report: it joins the next one


def _line(inv: InvoiceState, index: int) -> LineState:
    line = next((l for l in inv.lines if l.idx == index and not l.removed), None)
    if line is None:
        raise not_found("That line")
    return line


def result_invoice(state: BatchState, invoice_id: str) -> dict:
    return invoice_json(state, _invoice(state, invoice_id, editable=False))


def edit_line(conn, keys, device: dict, invoice_id: str, index: int, body) -> dict:
    entry = parse_line_entry(body)
    batch_id = locate_invoice(conn, invoice_id, device["shop_id"])
    key = keys.get(conn, device["shop_id"])

    def change(state: BatchState) -> None:
        inv = _invoice(state, invoice_id)
        old = _line(inv, index)
        new = manager_line(key, entry, index, old.printed)
        inv.lines[inv.lines.index(old)] = new
        _touched(state, inv)
        state.event("line_edited", invoice_id=invoice_id, line_idx=index, device_id=device["device_id"],
                    before=line_json(old), after=line_json(new))

    return result_invoice(reading.update_batch(conn, batch_id, keys, change), invoice_id)


def add_line(conn, keys, device: dict, invoice_id: str, body) -> dict:
    entry = parse_line_entry(body)
    batch_id = locate_invoice(conn, invoice_id, device["shop_id"])
    key = keys.get(conn, device["shop_id"])

    def change(state: BatchState) -> None:
        inv = _invoice(state, invoice_id)
        new = manager_line(key, entry, max((l.idx for l in inv.lines), default=-1) + 1, None)
        inv.lines.append(new)
        _touched(state, inv)
        state.event("line_added", invoice_id=invoice_id, line_idx=new.idx, device_id=device["device_id"],
                    after=line_json(new))

    return result_invoice(reading.update_batch(conn, batch_id, keys, change), invoice_id)


def remove_line(conn, keys, device: dict, invoice_id: str, index: int) -> dict:
    batch_id = locate_invoice(conn, invoice_id, device["shop_id"])

    def change(state: BatchState) -> None:
        inv = _invoice(state, invoice_id)
        old = _line(inv, index)
        old.removed = True        # kept, so a fresh reading of the same page does not bring the line back
        _touched(state, inv)
        state.event("line_removed", invoice_id=invoice_id, line_idx=index, device_id=device["device_id"],
                    before=line_json(old))

    return result_invoice(reading.update_batch(conn, batch_id, keys, change), invoice_id)


def _patch_values(body) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_body", "Send a JSON object.")
    values = {}
    if "supplier" in body:
        supplier = body["supplier"]
        if supplier is not None and (not isinstance(supplier, str) or supplier not in BY_KEY):
            raise ApiError(422, "unknown_supplier", "That supplier is not one the system knows.")
        values["supplier"] = supplier
    if "document_no" in body:
        values["document_no"] = _text(body, "document_no", 40)
    if "date" in body:
        value = body["date"]
        if value is not None:
            try:
                if not isinstance(value, str) or len(value) != 10:
                    raise ValueError
                _date.fromisoformat(value)
            except ValueError:
                raise ApiError(400, "invalid_body", "'date' must be a date written yyyy-mm-dd.")
        values["date"] = value
    if not values:
        raise ApiError(400, "invalid_body", "Send supplier, document_no or date.")
    return values


def patch_invoice(conn, keys, device: dict, invoice_id: str, body) -> dict:
    values = _patch_values(body)
    batch_id = locate_invoice(conn, invoice_id, device["shop_id"])

    def change(state: BatchState) -> None:
        inv = _invoice(state, invoice_id)
        before = {"supplier": inv.eff_supplier, "document_no": inv.eff_document_no, "date": inv.eff_date}
        for name, value in values.items():
            setattr(inv, f"{name}_override", value)
        after = {"supplier": inv.eff_supplier, "document_no": inv.eff_document_no, "date": inv.eff_date}
        if after["supplier"] != before["supplier"]:
            state.reparse.add(invoice_id)     # another supplier means another layout and supplier history
        _touched(state, inv)
        state.event("invoice_edited", invoice_id=invoice_id, device_id=device["device_id"], before=before, after=after)

    return result_invoice(reading.update_batch(conn, batch_id, keys, change), invoice_id)


def delete_invoice(conn, keys, device: dict, invoice_id: str) -> dict:
    batch_id = locate_invoice(conn, invoice_id, device["shop_id"])

    def change(state: BatchState) -> None:
        inv = _invoice(state, invoice_id, editable=False)
        if inv.status == "deleted":
            return
        state.event("duplicate_deleted", invoice_id=invoice_id, device_id=device["device_id"], tab_before=inv.tab,
                    duplicate_of=inv.duplicate_of, reasons=inv.tab_reasons)
        inv.status = "deleted"
        _touched(state, inv)

    return result_invoice(reading.update_batch(conn, batch_id, keys, change), invoice_id)


def keep_invoice(conn, keys, device: dict, invoice_id: str) -> dict:
    batch_id = locate_invoice(conn, invoice_id, device["shop_id"])

    def change(state: BatchState) -> None:
        inv = _invoice(state, invoice_id)
        if not inv.not_duplicate:
            inv.not_duplicate = True
            _touched(state, inv)
            state.event("not_duplicate", invoice_id=invoice_id, device_id=device["device_id"],
                        duplicate_of=inv.duplicate_of, reasons=inv.tab_reasons)

    return result_invoice(reading.update_batch(conn, batch_id, keys, change), invoice_id)


# --- photographs -----------------------------------------------------------------------------------

def receipt(photo: dict) -> dict:
    """What the phone checks before deleting its copy: batch, key, hash and size must all match its file."""
    return {"photo_id": photo["photo_id"], "batch_id": photo["batch_id"], "client_photo_key": photo["client_photo_key"],
            "sha256": photo["sha256"], "bytes": photo["bytes"], "state": "stored"}


def find_photo(conn: sqlite3.Connection, batch_id: str, client_photo_key: str) -> dict | None:
    return db.one(conn, "SELECT * FROM photos WHERE batch_id = ? AND client_photo_key = ?", batch_id, client_photo_key)


def record_photo(conn: sqlite3.Connection, settings, device: dict, batch: dict, client_photo_key: str,
                 received: storage.Received, ordinal: int | None, phone_quality: dict | None,
                 replaces: str | None) -> tuple[dict, bool]:
    """Rename the checked file into place, then in ONE transaction the photo row, its reading job, and for a
    retake the old photo marked replaced. Returns (photo, created)."""
    photo_id = db.new_id("p")
    relative = f"{batch['batch_id']}/{photo_id}.{received.extension}"
    final = storage.store(received, settings.photos / relative)
    try:
        with db.transaction(conn):
            existing = find_photo(conn, batch["batch_id"], client_photo_key)
            if existing is not None:
                final.unlink()          # a retry that raced this one got there first; its row stands
                return existing, False
            old = None
            if replaces:
                old = db.one(conn, "SELECT * FROM photos WHERE photo_id = ? AND batch_id = ?", replaces, batch["batch_id"])
                if old is None:
                    raise not_found("The photograph to replace")
                if old["state"] == "replaced":
                    raise ApiError(409, "already_replaced", "That photograph was already replaced by a retake.")
                ordinal = old["ordinal"]
            ts = db.now()
            conn.execute(
                "INSERT INTO photos (photo_id, batch_id, client_photo_key, ordinal, sha256, bytes, content_type, width,"
                " height, storage_path, phone_quality, replaces, received_at, received)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (photo_id, batch["batch_id"], client_photo_key, ordinal, received.sha256, received.bytes,
                 received.content_type, received.width, received.height, relative,
                 db.dumps(phone_quality) if phone_quality is not None else None, replaces, db.iso(ts), ts))
            conn.execute("INSERT INTO jobs (photo_id, available, created_at) VALUES (?, ?, ?)", (photo_id, ts, db.iso(ts)))
            common = {"shop_id": batch["shop_id"], "device_id": device["device_id"], "batch_id": batch["batch_id"],
                      "photo_id": photo_id}
            if old is not None:
                conn.execute("UPDATE photos SET state = 'replaced', replaced_by = ? WHERE photo_id = ?", (photo_id, replaces))
                conn.execute("UPDATE invoice_pages SET photo_id = ? WHERE photo_id = ?", (photo_id, replaces))
                conn.execute("UPDATE batches SET status = 'processing' WHERE batch_id = ? AND status = 'ready'",
                             (batch["batch_id"],))
                db.event(conn, "retake_received", replaces=replaces, ordinal=ordinal, bytes=received.bytes,
                         after_submit=batch["status"] == "submitted", **common)
            else:
                db.event(conn, "upload_received", ordinal=ordinal, bytes=received.bytes, **common)
            db.event(conn, "receipt_issued", sha256=received.sha256, bytes=received.bytes, **common)
            db.bump_revision(conn, batch["batch_id"])
    except BaseException:
        if final.exists() and not find_photo(conn, batch["batch_id"], client_photo_key):
            final.unlink()
        raise
    return db.one(conn, "SELECT * FROM photos WHERE photo_id = ?", photo_id), True


def photo_for_shop(conn: sqlite3.Connection, photo_id: str, shop_id: str | None) -> dict:
    sql = "SELECT p.* FROM photos p JOIN batches b ON b.batch_id = p.batch_id WHERE p.photo_id = ?"
    row = db.one(conn, sql + (" AND b.shop_id = ?" if shop_id else ""), *([photo_id, shop_id] if shop_id else [photo_id]))
    if row is None:
        raise not_found("That photograph")
    return row
