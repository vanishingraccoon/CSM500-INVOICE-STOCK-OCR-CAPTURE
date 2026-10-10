"""Submitting a batch, and the input report the office keys into Pilot from.

Submitting freezes the batch: a snapshot of every invoice as it stands is stored with the report, and the PDF
and CSV are built from that snapshot, never from the live tables, so a report can be rebuilt byte for byte
later and a retake after submit cannot quietly change a report already printed. Such a retake joins the next
report the shop submits, or, once its page has been read, the next version of this batch's own report, which
the office asks for with Rebuild report on the portal: every version keeps its files and its SHA-256.

Everything goes in (option b, docs/phone-api.md section 7). Good and Corrections invoices are reported as
they stand. Lines without a stock item and base quantity print under "not in the key". Retake and duplicate
invoices are listed at the end as unresolved. Deleted duplicates never appear.

Submitting also teaches the key: every line whose stock item the manager chose, or accepted by submitting it
unstarred, becomes a learned alias (supplier wording -> stock item) for this shop.

Files are written to a temporary name, flushed, and renamed into place; their SHA-256 is recorded, so the
printed copy can be matched to the file it came from.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import os
import sqlite3
import tempfile
from pathlib import Path

from app import db, reading
from app.batches import ApiError, not_found
from app.reading import BatchState, InvoiceState, invoice_title, line_problem, rands
from ingest.suppliers import BY_KEY
from normaliser.match import normalise_text
from refs import primary_key

REPORTABLE_TABS = ("good", "corrections")
UNRESOLVED_TABS = ("retake", "duplicates")
CSV_COLUMNS = ("section", "shop", "batch_id", "supplier", "invoice", "date", "pages", "item_code", "item",
               "quantity_base", "base_unit", "cost_excl_vat", "vat", "typed_by_manager", "note")


# --- the frozen snapshot ----------------------------------------------------------------------------

def _supplier_name(key: str | None) -> str | None:
    return BY_KEY[key].pilot_name if key in BY_KEY else None


def _invoice_entry(state: BatchState, inv: InvoiceState, carried: bool) -> dict:
    rows, not_in_key = [], []
    for l in inv.live_lines():
        typed = l.entered_by == "manager"
        if l.item_code and l.base_quantity is not None and l.refusal is None:
            rows.append({"item_code": l.item_code, "item": l.item_name, "quantity_base": l.base_quantity,
                         "base_unit": l.base_unit, "cost_excl": rands(l.cost_cents), "vat": rands(l.vat_cents),
                         "typed_by_manager": typed,
                         "check": line_problem(l) if l.flags else None})
        else:
            not_in_key.append({
                "description": l.free_text or (l.printed or {}).get("description") or l.item_name or "",
                "item": l.item_name, "quantity": l.base_quantity if l.free_text else l.quantity,
                "base_unit": l.free_text is not None, "cost_excl": rands(l.cost_cents), "vat": rands(l.vat_cents),
                "typed_by_manager": typed,
                "why": "typed by the manager: not in the key" if l.free_text else line_problem(l)})
    return {
        "batch_id": state.batch_id, "invoice_id": inv.invoice_id, "carried": carried,
        "batch_submitted_at": state.batch.get("submitted_at"),
        "title": invoice_title(inv.eff_document_no, state.ordinals(inv)), "supplier": inv.eff_supplier,
        "supplier_name": _supplier_name(inv.eff_supplier), "document_no": inv.eff_document_no, "date": inv.eff_date,
        "pages": sorted(state.ordinals(inv)), "tab": inv.tab, "reasons": inv.tab_reasons,
        "stated_total_excl": rands(inv.stated_total_excl), "stated_vat": rands(inv.stated_vat),
        "stated_total_incl": rands(inv.stated_total_incl), "lines_sum_excl": rands(inv.lines_sum_excl),
        "adds_up": inv.adds_up, "rows": rows, "not_in_key": not_in_key,
    }


def _order(entry: dict) -> tuple:
    number = primary_key(entry["document_no"])
    return (entry["supplier_name"] is None, (entry["supplier_name"] or "").lower(),
            (0, int(number), "") if number.isdigit() else (1, 0, number), entry["pages"])


def snapshot(conn: sqlite3.Connection, state: BatchState, carried: list[tuple[BatchState, InvoiceState]],
             version: int, submitted_at: str) -> dict:
    shop = db.one(conn, "SELECT name FROM shops WHERE shop_id = ?", state.shop_id)
    device = db.one(conn, "SELECT name FROM devices WHERE device_id = ?", state.batch["device_id"])
    entries = [_invoice_entry(state, inv, False) for inv in state.invoices.values() if inv.status == "open"]
    entries += [_invoice_entry(s, inv, True) for s, inv in carried if inv.status == "open"]
    entries.sort(key=_order)
    return {
        "shop_id": state.shop_id, "shop_name": shop["name"] if shop else state.shop_id,
        "batch_id": state.batch_id, "created_at": state.batch["created_at"], "submitted_at": submitted_at,
        "device_name": device["name"] if device else "", "version": version,
        "invoices": [e for e in entries if e["tab"] in REPORTABLE_TABS],
        "unresolved": [e for e in entries if e["tab"] in UNRESOLVED_TABS],
    }


# --- rendering ----------------------------------------------------------------------------------------

def _qty(q: float | None) -> str:
    if q is None:
        return ""
    text = f"{q:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _cost(v: float | None) -> str:
    return "" if v is None else f"{v:.2f}"


def _rand(v: float | None) -> str:
    return "not read" if v is None else f"R{v:,.2f}"


def _e(text) -> str:
    return html.escape("" if text is None else str(text))


CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #111; }
h1 { font-size: 15pt; margin: 0 0 3pt 0; }
h2 { font-size: 12pt; margin: 14pt 0 3pt 0; }
h3 { font-size: 10pt; margin: 10pt 0 1pt 0; }
p { margin: 0 0 3pt 0; }
.meta { font-size: 8.5pt; }
.note { font-size: 7.5pt; color: #444; }
.bad { color: #a00000; }
table { width: 100%; margin: 2pt 0 2pt 0; }
th { text-align: left; font-size: 7.5pt; border-bottom: 0.8pt solid #000; padding: 1pt 3pt; }
td { border-bottom: 0.4pt solid #999; padding: 2pt 3pt; vertical-align: top; }
.num { text-align: right; }
.tick { text-align: center; }
td.tick { font-size: 12pt; }
"""


def render_html(snap: dict) -> str:
    out = [f"<h1>Input report: {_e(snap['shop_name'])}</h1>",
           (f"<p class='meta'>Batch {_e(snap['batch_id'])} &#183; photographed {_e(snap['created_at'][:16].replace('T', ' '))}"
            f" &#183; submitted {_e(snap['submitted_at'][:16].replace('T', ' '))} &#183; phone {_e(snap['device_name'])}"
            f" &#183; report version {snap['version']}</p>"),
           ("<p class='note'>Key each row into Pilot exactly as printed, then tick its box. &#x270E; marks a line the"
            " manager typed on the phone. Quantities are in the stock item's base unit; costs exclude VAT.</p>")]
    for inv in snap["invoices"]:
        out.append(f"<h3>{_e(inv['supplier_name'] or 'Supplier not recognised')}: {_e(inv['title'])}</h3>")
        adds = ("adds up" if inv["adds_up"] else "<span class='bad'>does not add up</span>") if inv["adds_up"] is not None \
            else "total not read"
        carried = (f" &#183; retaken after its batch was submitted ({_e(inv['batch_id'])})" if inv["carried"] else "")
        out.append(f"<p class='meta'>Date {_e(inv['date'] or 'not read')} &#183; invoice states {_rand(inv['stated_total_excl'])}"
                   f" excl. VAT &#183; lines sum {_rand(inv['lines_sum_excl'])} &#183; {adds}{carried}</p>")
        if not inv["rows"]:
            out.append("<p class='note'>No lines with a stock item; see 'Not in the key' below.</p>")
            continue
        out.append("<table><tr><th>ITEM (UNIT)</th><th class='num'>QTY (BASE UNIT)</th><th class='num'>COST EXCL. VAT</th>"
                   "<th class='num'>VAT</th><th class='tick'>KEYED</th></tr>")
        for r in inv["rows"]:
            mark = "&#x270E; " if r["typed_by_manager"] else ""
            check = f"<br/><span class='note'>check: {_e(r['check'])}</span>" if r.get("check") else ""
            out.append(f"<tr><td>{mark}{_e(r['item'])}{check}</td><td class='num'>{_qty(r['quantity_base'])}</td>"
                       f"<td class='num'>{_cost(r['cost_excl'])}</td><td class='num'>{_cost(r['vat'])}</td>"
                       "<td class='tick'>&#x2610;</td></tr>")
        out.append("</table>")

    # the same five columns as an invoice's section, grouped under the invoice, so the wording has room on paper
    loose = [inv for inv in snap["invoices"] if inv["not_in_key"]]
    out.append("<h2>Not in the key</h2>")
    if loose:
        out.append("<p class='note'>Lines without a stock item from the shop's key: choose the item in Pilot, then tick."
                   " A quantity typed by the manager is in Pilot's base unit; any other is as printed.</p>")
    else:
        out.append("<p class='note'>Every reported line has a stock item.</p>")
    for inv in loose:
        out.append(f"<h3>{_e(inv['supplier_name'] or 'Supplier not recognised')}: {_e(inv['title'])}</h3>")
        out.append("<table><tr><th>AS WORDED</th><th class='num'>QTY</th><th class='num'>COST EXCL. VAT</th>"
                   "<th class='num'>VAT</th><th class='tick'>KEYED</th></tr>")
        for l in inv["not_in_key"]:
            mark = "&#x270E; " if l["typed_by_manager"] else ""
            unit = "quantity in Pilot's base unit" if l["base_unit"] else "quantity as printed"
            out.append(f"<tr><td>{mark}{_e(l['description'])}<br/><span class='note'>{_e(l['why'])} &#183; {unit}</span></td>"
                       f"<td class='num'>{_qty(l['quantity'])}</td><td class='num'>{_cost(l['cost_excl'])}</td>"
                       f"<td class='num'>{_cost(l['vat'])}</td><td class='tick'>&#x2610;</td></tr>")
        out.append("</table>")

    out.append("<h2>Unresolved invoices</h2>")
    if snap["unresolved"]:
        out.append("<p class='note'>Not keyable as read: retake the photograph, or decide on the duplicate, then key"
                   " from the paper.</p>")
        for inv in snap["unresolved"]:
            reasons = "; ".join(inv["reasons"]) or inv["tab"]
            out.append(f"<p><b>{_e(inv['supplier_name'] or 'Supplier not recognised')}: {_e(inv['title'])}</b>"
                       f" ({_e(inv['tab'])}) &#183; date {_e(inv['date'] or 'not read')} &#183; states"
                       f" {_rand(inv['stated_total_excl'])} excl. VAT<br/><span class='note'>{_e(reasons)}</span></p>")
    else:
        out.append("<p class='note'>None.</p>")
    return "".join(out)


def render_pdf(snap: dict) -> bytes:
    import pymupdf
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    story = pymupdf.Story(html=render_html(snap), user_css=CSS)
    page = pymupdf.paper_rect("a4")
    where = page + (40, 40, -40, -48)
    more = 1
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    doc = pymupdf.open("pdf", buffer.getvalue())
    for n, p in enumerate(doc, start=1):
        p.insert_text((40, page.height - 26), f"{snap['shop_name']} | batch {snap['batch_id']} | report version"
                                              f" {snap['version']} | page {n} of {doc.page_count}", fontsize=7)
    doc.set_metadata({"title": f"Input report {snap['batch_id']}", "subject": snap["shop_name"],
                      "creator": "invoice-pipeline laptop server"})
    return doc.tobytes(garbage=3, deflate=True)


def render_csv(snap: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    common = lambda inv: [snap["shop_id"], inv["batch_id"], inv["supplier_name"] or "", inv["document_no"] or "",
                          inv["date"] or "", " ".join(str(p) for p in inv["pages"])]
    for inv in snap["invoices"]:
        for r in inv["rows"]:
            w.writerow(["invoice", *common(inv), r["item_code"], r["item"], _qty(r["quantity_base"]), r["base_unit"] or "",
                        _cost(r["cost_excl"]), _cost(r["vat"]), "yes" if r["typed_by_manager"] else "no",
                        f"check: {r['check']}" if r.get("check") else ""])
    for inv in snap["invoices"]:
        for l in inv["not_in_key"]:
            w.writerow(["not_in_key", *common(inv), "", l["description"], _qty(l["quantity"]),
                        "base unit" if l["base_unit"] else "", _cost(l["cost_excl"]), _cost(l["vat"]),
                        "yes" if l["typed_by_manager"] else "no", l["why"]])
    for inv in snap["unresolved"]:
        w.writerow(["unresolved", *common(inv), "", "", "", "", "", "", "no",
                    f"{inv['tab']}: " + "; ".join(inv["reasons"])
                    + f"; the invoice states {_rand(inv['stated_total_excl'])} excl. VAT"])
    return out.getvalue()


# --- building the files --------------------------------------------------------------------------

def write_atomic(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.stem + "_", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return hashlib.sha256(data).hexdigest()


def build(settings, conn: sqlite3.Connection, report_id: int) -> dict:
    row = db.one(conn, "SELECT r.*, b.shop_id FROM reports r JOIN batches b ON b.batch_id = r.batch_id"
                       " WHERE r.report_id = ?", report_id)
    snap = db.loads(row["snapshot"])
    base = f"{row['batch_id']}/report_v{row['version']}"
    try:
        pdf_sha = write_atomic(settings.reports / f"{base}.pdf", render_pdf(snap))
        csv_sha = write_atomic(settings.reports / f"{base}.csv", render_csv(snap).encode("utf-8-sig"))
    except Exception as e:
        with db.transaction(conn):
            conn.execute("UPDATE reports SET status = 'failed', last_error = ? WHERE report_id = ?",
                         (f"{type(e).__name__}: {e}", report_id))
            db.event(conn, "report_failed", shop_id=row["shop_id"], batch_id=row["batch_id"], version=row["version"],
                     error=f"{type(e).__name__}: {e}")
        raise
    with db.transaction(conn):
        conn.execute("UPDATE reports SET status = 'ready', pdf_path = ?, pdf_sha256 = ?, csv_path = ?, csv_sha256 = ?,"
                     " last_error = NULL, built_at = ? WHERE report_id = ?",
                     (f"{base}.pdf", pdf_sha, f"{base}.csv", csv_sha, db.iso(), report_id))
        db.event(conn, "report_generated", shop_id=row["shop_id"], batch_id=row["batch_id"], version=row["version"],
                 pdf_sha256=pdf_sha, csv_sha256=csv_sha, invoices=len(snap["invoices"]),
                 unresolved=len(snap["unresolved"]))
    return db.one(conn, "SELECT * FROM reports WHERE report_id = ?", report_id)


def latest(conn: sqlite3.Connection, batch_id: str) -> dict | None:
    return db.one(conn, "SELECT * FROM reports WHERE batch_id = ? ORDER BY version DESC LIMIT 1", batch_id)


def versions(conn: sqlite3.Connection, batch_id: str) -> list[dict]:
    """Every version this batch has had, newest first: the office keeps the printed copies of the older ones."""
    return db.all_rows(conn, "SELECT report_id, version, status, created_at, built_at, pdf_sha256 FROM reports"
                             " WHERE batch_id = ? ORDER BY version DESC", batch_id)


def ensure_built(settings, conn: sqlite3.Connection, batch_id: str) -> dict | None:
    """The batch's latest report with its files on disk, rebuilt from the snapshot if they are missing."""
    row = latest(conn, batch_id)
    if row is None:
        return None
    files_there = row["status"] == "ready" and all((settings.reports / row[k]).exists() for k in ("pdf_path", "csv_path"))
    return row if files_there else build(settings, conn, row["report_id"])


# --- submitting -------------------------------------------------------------------------------------

def report_links(batch_id: str) -> dict:
    return {"pdf": f"/portal/batches/{batch_id}/report.pdf", "csv": f"/portal/batches/{batch_id}/report.csv"}


def learn_aliases(conn: sqlite3.Connection, shop_id: str, batch_id: str, invoices: list[InvoiceState]) -> int:
    removed = {(r["supplier"], r["normalised"], r["item_code"]) for r in db.all_rows(
        conn, "SELECT supplier, normalised, item_code FROM learned_aliases WHERE shop_id = ? AND removed_at IS NOT NULL",
        shop_id)}
    learned = 0
    for inv in invoices:
        if inv.status != "open" or inv.tab not in REPORTABLE_TABS or not inv.eff_supplier:
            continue
        for l in inv.live_lines():
            if not l.item_code or not l.printed or (l.entered_by == "system" and l.star):
                continue     # no wording to learn, or the system's guess that nobody accepted
            wording = (l.printed.get("description") or "").strip()
            normalised = normalise_text(wording)
            if not normalised or (inv.eff_supplier, normalised, l.item_code) in removed:
                continue     # nothing distinctive left to match on, or a person already removed this alias
            cur = conn.execute(
                "INSERT OR IGNORE INTO learned_aliases (shop_id, supplier, wording, normalised, item_code, item_name,"
                " source_batch, source_invoice, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (shop_id, inv.eff_supplier, wording, normalised, l.item_code, l.item_name or "", batch_id,
                 inv.invoice_id, db.iso()))
            learned += cur.rowcount
    return learned


def _freeze(conn: sqlite3.Connection, device: dict, batch_id: str) -> int | None:
    with db.transaction(conn):
        batch = db.one(conn, "SELECT * FROM batches WHERE batch_id = ?", batch_id)
        if batch["status"] == "submitted":
            return None
        if conn.execute("SELECT COUNT(*) FROM photos WHERE batch_id = ? AND state IN ('stored', 'reading')",
                        (batch_id,)).fetchone()[0]:
            raise ApiError(409, "still_reading", "Photographs are still being read. Submit once they are all read.")
        state = reading.load_batch(conn, batch_id)
        carried: list[tuple[BatchState, InvoiceState]] = []
        for row in db.all_rows(conn, "SELECT DISTINCT i.batch_id FROM invoices i JOIN batches b ON b.batch_id = i.batch_id"
                                     " WHERE b.shop_id = ? AND b.status = 'submitted' AND i.needs_report = 1"
                                     " ORDER BY i.batch_id", batch["shop_id"]):
            other = reading.load_batch(conn, row["batch_id"])
            carried += [(other, inv) for inv in other.invoices.values() if inv.needs_report]
        version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM reports WHERE batch_id = ?",
                               (batch_id,)).fetchone()[0]
        ts = db.now()
        submitted_at = db.iso(ts)
        snap = snapshot(conn, state, carried, version, submitted_at)
        report_id = conn.execute("INSERT INTO reports (batch_id, version, snapshot, created_at) VALUES (?, ?, ?, ?)",
                                 (batch_id, version, db.dumps(snap), submitted_at)).lastrowid
        conn.execute("UPDATE batches SET status = 'submitted', submitted_at = ?, revision = revision + 1"
                     " WHERE batch_id = ?", (submitted_at, batch_id))
        conn.execute("UPDATE invoices SET reported_in = ? WHERE batch_id = ? AND status = 'open'", (report_id, batch_id))
        for other, inv in carried:
            conn.execute("UPDATE invoices SET reported_in = ?, needs_report = 0 WHERE invoice_id = ?",
                         (report_id, inv.invoice_id))
            db.bump_revision(conn, other.batch_id)
        learned = learn_aliases(conn, batch["shop_id"], batch_id,
                                list(state.invoices.values()) + [inv for _, inv in carried])
        first = conn.execute("SELECT MIN(received) FROM photos WHERE batch_id = ?", (batch_id,)).fetchone()[0]
        tabs = {}
        for inv in state.invoices.values():
            tabs[inv.tab] = tabs.get(inv.tab, 0) + 1
        db.event(conn, "batch_submitted", shop_id=batch["shop_id"], device_id=device["device_id"], batch_id=batch_id,
                 tabs=tabs, carried=len(carried), report_version=version,
                 seconds_from_first_photo=round(ts - first, 1) if first else None)
        db.event(conn, "aliases_learned", shop_id=batch["shop_id"], batch_id=batch_id, count=learned)
    return report_id


def submit(conn: sqlite3.Connection, keys, settings, device: dict, batch_id: str) -> dict:
    batch = db.one(conn, "SELECT * FROM batches WHERE batch_id = ? AND shop_id = ?", batch_id, device["shop_id"])
    if batch is None:
        raise not_found("That batch")
    report_id = _freeze(conn, device, batch_id)
    if report_id is None:
        _quietly(lambda: ensure_built(settings, conn, batch_id))       # a retried submit
        return {"status": "submitted", "report": report_links(batch_id)}
    _quietly(lambda: build(settings, conn, report_id))                  # a failure is recorded on the report row
    # invoices still open elsewhere may now duplicate what was just submitted
    for row in db.all_rows(conn, "SELECT batch_id FROM batches WHERE shop_id = ? AND status <> 'submitted'",
                           batch["shop_id"]):
        _quietly(lambda b=row["batch_id"]: reading.update_batch(conn, b, keys))
    return {"status": "submitted", "report": report_links(batch_id)}


# --- rebuilding a submitted batch's report ------------------------------------------------------------

def _carried_again(conn: sqlite3.Connection, previous: dict) -> list[tuple[BatchState, InvoiceState]]:
    """The invoices from other batches that the last version carried. They stay in the rebuilt one, as they
    now stand, so a rebuild never quietly drops an invoice the office was still waiting to key."""
    snap = db.loads(previous["snapshot"])
    wanted: dict[str, list[str]] = {}
    for entry in snap["invoices"] + snap["unresolved"]:
        if entry["carried"]:
            wanted.setdefault(entry["batch_id"], []).append(entry["invoice_id"])
    out = []
    for batch_id, invoice_ids in wanted.items():
        other = reading.load_batch(conn, batch_id)
        if other is not None:
            out += [(other, other.invoices[i]) for i in invoice_ids if i in other.invoices]
    return out


def rebuild(settings, conn: sqlite3.Connection, batch_id: str) -> dict:
    """The next version of a submitted batch's report, from a fresh snapshot of the batch as it stands.

    A retake accepted after Submit (section 5) is read later; this is how the office gets the corrected report
    without waiting for the shop to submit another batch. The versions before it keep their files and their
    SHA-256, so a printed copy can still be matched to what it came from. The phone is not told: it keeps the
    links its own Submit gave it, which always serve the newest version.
    """
    with db.transaction(conn):
        batch = db.one(conn, "SELECT * FROM batches WHERE batch_id = ?", batch_id)
        if batch is None:
            raise not_found("That batch")
        if batch["status"] != "submitted":
            raise ApiError(409, "not_submitted", "This batch has not been submitted yet, so it has no report to"
                                                 " rebuild.")
        if conn.execute("SELECT COUNT(*) FROM photos WHERE batch_id = ? AND state IN ('stored', 'reading')",
                        (batch_id,)).fetchone()[0]:
            raise ApiError(409, "still_reading", "Photographs are still being read. Rebuild the report once they are"
                                                 " all read.")
        state = reading.load_batch(conn, batch_id)
        previous = latest(conn, batch_id)
        carried = _carried_again(conn, previous) if previous else []
        corrected = [inv.invoice_id for inv in state.invoices.values() if inv.needs_report]
        version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM reports WHERE batch_id = ?",
                               (batch_id,)).fetchone()[0]
        snap = snapshot(conn, state, carried, version, batch["submitted_at"])
        report_id = conn.execute("INSERT INTO reports (batch_id, version, snapshot, created_at) VALUES (?, ?, ?, ?)",
                                 (batch_id, version, db.dumps(snap), db.iso())).lastrowid
        conn.execute("UPDATE invoices SET reported_in = ?, needs_report = 0 WHERE batch_id = ? AND status = 'open'",
                     (report_id, batch_id))
        for other, inv in carried:
            conn.execute("UPDATE invoices SET reported_in = ?, needs_report = 0 WHERE invoice_id = ?",
                         (report_id, inv.invoice_id))
            db.bump_revision(conn, other.batch_id)
        db.bump_revision(conn, batch_id)
        db.event(conn, "report_rebuilt", shop_id=batch["shop_id"], batch_id=batch_id, version=version,
                 corrected=corrected, carried=len(carried), invoices=len(snap["invoices"]),
                 unresolved=len(snap["unresolved"]))
    return build(settings, conn, report_id)


def _quietly(action) -> None:
    try:
        action()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("after submit")
