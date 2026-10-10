"""The portal: the laptop's pages for the office. Batches with their invoices by tab, input reports, unresolved
invoices, each shop's key, pairing a phone, and the device list.

NO LOGINS YET (future work). Anyone who can reach the laptop can open these pages, pair a phone, revoke one,
or remove a learned alias. The server must only listen on the dedicated office network; see the README,
"Laptop server and portal".
"""
from __future__ import annotations

from flask import (
    Blueprint,
    Response,
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app import batches, context, db, pairing, reading, report, shops
from app.api import send_photo
from app.batches import ApiError
from app.reading import invoice_title
from ingest.suppliers import BY_KEY
from normaliser.stock import conversion_table, summarise

portal = Blueprint("portal", __name__)

TAB_ORDER = ("corrections", "retake", "duplicates", "good", "deleted")
TAB_TITLES = {"corrections": "Corrections", "retake": "Retake", "duplicates": "Duplicates", "good": "Good",
              "deleted": "Deleted"}


@portal.app_template_filter("rands")
def _rands(value) -> str:
    return "not read" if value is None else f"R{value:,.2f}"


@portal.app_template_filter("cost")
def _cost(value) -> str:
    return "" if value is None else f"{value:,.2f}"


@portal.app_template_filter("qty")
def _qty(value) -> str:
    if value is None:
        return ""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@portal.app_template_filter("when")
def _when(value) -> str:
    return "" if not value else str(value)[:16].replace("T", " ")


def _shop_list():
    return context.keys().shops()


def _chosen_shop(shop_list):
    shop_id = request.args.get("shop") or next(iter(shop_list), None)
    if shop_id not in shop_list:
        abort(404)
    return shop_list[shop_id]


@portal.get("/")
def index():
    conn = context.connection()
    groups = [{"shop": s, "batches": batches.summaries(conn, s.shop_id)} for s in _shop_list().values()]
    return render_template("portal_batches.html", groups=groups, active="batches")


@portal.get("/batches/<batch_id>")
def batch(batch_id: str):
    conn = context.connection()
    state = reading.load_batch(conn, batch_id)
    if state is None:
        abort(404)
    data = batches.batch_json(state)
    assigned = {pid for inv in state.invoices.values() for pid in inv.pages}
    loose = [p for p in state.active_photos() if p.photo_id not in assigned]
    device = db.one(conn, "SELECT name FROM devices WHERE device_id = ?", state.batch["device_id"])
    shop = db.one(conn, "SELECT name FROM shops WHERE shop_id = ?", state.shop_id)
    reports = report.versions(conn, batch_id)
    return render_template(
        "portal_batch.html", b=data, by_tab={t: [i for i in data["invoices"] if i["tab"] == t] for t in TAB_ORDER},
        tabs=TAB_ORDER, titles=TAB_TITLES, loose=loose, device_name=device["name"] if device else "",
        shop_name=shop["name"] if shop else state.shop_id, report=reports[0] if reports else None, older=reports[1:],
        retaken=[i for i in state.invoices.values() if i.needs_report], active="batches")


@portal.post("/batches/<batch_id>/rebuild-report")
def rebuild_report(batch_id: str):
    """The office's button for a batch whose invoices changed after its report: issue the next version now."""
    try:
        report.rebuild(context.settings(), context.connection(), batch_id)
    except ApiError as e:
        abort(e.status, e.message)
    return redirect(url_for("portal.batch", batch_id=batch_id) + "#report")


def _report_file(batch_id: str, kind: str):
    conn, settings = context.connection(), context.settings()
    if db.one(conn, "SELECT 1 FROM batches WHERE batch_id = ?", batch_id) is None:
        abort(404)
    row = report.ensure_built(settings, conn, batch_id)
    if row is None:
        abort(404, "This batch has not been submitted yet, so it has no input report.")
    return send_file(settings.reports / row[f"{kind}_path"], mimetype="application/pdf" if kind == "pdf" else "text/csv",
                     as_attachment=kind == "csv", download_name=f"input_report_{batch_id}_v{row['version']}.{kind}")


@portal.get("/portal/batches/<batch_id>/report.pdf")      # the path the submit response gives the phone
@portal.get("/batches/<batch_id>/report.pdf")
def report_pdf(batch_id: str):
    return _report_file(batch_id, "pdf")


@portal.get("/portal/batches/<batch_id>/report.csv")
@portal.get("/batches/<batch_id>/report.csv")
def report_csv(batch_id: str):
    return _report_file(batch_id, "csv")


@portal.get("/portal/photos/<photo_id>/image")
def photo_image(photo_id: str):
    try:
        photo = batches.photo_for_shop(context.connection(), photo_id, None)
        return send_photo(photo, request.args.get("w"))
    except ApiError as e:
        abort(e.status)


@portal.get("/unresolved")
def unresolved():
    conn = context.connection()
    rows = db.all_rows(
        conn, "SELECT i.invoice_id, i.batch_id, i.tab, i.tab_reasons, i.stated_total_excl, i.updated_at,"
              " COALESCE(i.supplier_override, i.supplier) AS supplier, COALESCE(i.document_no_override, i.document_no)"
              " AS document_no, COALESCE(i.date_override, i.date) AS date, b.status AS batch_status, b.created_at"
              " AS batch_created, s.name AS shop_name FROM invoices i JOIN batches b ON b.batch_id = i.batch_id"
              " JOIN shops s ON s.shop_id = b.shop_id WHERE i.status = 'open' AND i.tab IN ('retake', 'duplicates')"
              " ORDER BY b.created DESC, i.created_at")
    for r in rows:
        ordinals = [o for (o,) in conn.execute("SELECT p.ordinal FROM invoice_pages ip JOIN photos p ON p.photo_id = ip.photo_id"
                                               " WHERE ip.invoice_id = ? ORDER BY p.ordinal", (r["invoice_id"],))]
        r["title"] = invoice_title(r["document_no"], ordinals)
        r["supplier_name"] = BY_KEY[r["supplier"]].pilot_name if r["supplier"] in BY_KEY else None
        r["reasons"] = db.loads(r["tab_reasons"], [])
        r["stated"] = None if r["stated_total_excl"] is None else r["stated_total_excl"] / 100
    return render_template("portal_unresolved.html", rows=rows, active="unresolved")


@portal.get("/key")
def key():
    conn = context.connection()
    shop_list = _shop_list()
    shop = _chosen_shop(shop_list)
    q = request.args.get("q", "").strip()
    shop_key, summary, problems, found, problem = None, None, [], [], None
    try:
        shop_key = context.keys().get(conn, shop.shop_id)
        table = conversion_table(shop_key.stock)
        summary = summarise(table)
        problems = unconvertible(table)
        found = shop_key.search(q, 60) if q else []
    except (FileNotFoundError, LookupError) as e:
        problem = str(e)
    return render_template("portal_key.html", shops=shop_list, shop=shop, key=shop_key, summary=summary,
                           problems=problems, found=found, q=q, aliases=shops.alias_rows(conn, shop.shop_id),
                           problem=problem, active="key")


# "pack_size_as_unit" items (a 750ML bottle counted each) do convert when the invoice prints the same size, so
# only items whose names carry no usable base unit are listed as ones the key cannot convert
UNCONVERTIBLE = ("base_unit_absent", "base_unit_unrecognised")


def unconvertible(table) -> list:
    return [e for e in table if e.status in UNCONVERTIBLE]


@portal.post("/key/<shop_id>/aliases/remove")
def remove_alias(shop_id: str):
    if shop_id not in _shop_list():
        abort(404)
    form = request.form
    shops.remove_alias(context.connection(), shop_id, form.get("supplier", ""), form.get("normalised", ""),
                       form.get("item_code", ""))
    return redirect(url_for("portal.key", shop=shop_id) + "#aliases")


@portal.get("/pair")
def pair():
    conn, settings = context.connection(), context.settings()
    shop_list = _shop_list()
    shop = _chosen_shop(shop_list)
    code = pairing.current_code(conn, shop.shop_id, pairing.qr_reads_back(settings, shop.shop_id))
    seconds_left = max(0, int(code["expires"] - db.now()))
    recent = db.all_rows(conn, "SELECT name, paired_at FROM devices WHERE shop_id = ? ORDER BY paired_at DESC LIMIT 3",
                         shop.shop_id)
    return render_template(
        "portal_pair.html", shops=shop_list, shop=shop, code=code, payload=pairing.qr_payload(settings, code),
        server=pairing.server_url(settings), expires_at=db.iso(code["expires"]), seconds_left=seconds_left,
        refresh_in=max(5, seconds_left - pairing.REUSE_IF_SECONDS_LEFT + 1), recent=recent,
        local_only=settings.host in ("127.0.0.1", "localhost", "::1"), active="pair")


@portal.post("/pair/new")
def pair_new():
    shop_id = request.form.get("shop", "")
    if shop_id not in _shop_list():
        abort(400)
    pairing.issue_code(context.connection(), shop_id, pairing.qr_reads_back(context.settings(), shop_id))
    return redirect(url_for("portal.pair", shop=shop_id))


@portal.get("/pair/qr/<code>.png")
def pair_qr(code: str):
    row = db.one(context.connection(), "SELECT * FROM pair_codes WHERE code = ?", pairing.normalise_code(code))
    if row is None:
        abort(404)
    png = pairing.qr_png(pairing.qr_payload(context.settings(), row))
    return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})


@portal.get("/devices")
def devices():
    rows = db.all_rows(context.connection(),
                       "SELECT d.*, s.name AS shop_name, (SELECT COUNT(*) FROM batches b WHERE b.device_id = d.device_id)"
                       " AS batches FROM devices d JOIN shops s ON s.shop_id = d.shop_id"
                       " ORDER BY d.revoked_at IS NOT NULL, d.paired_at DESC")
    for r in rows:
        r["last_seen_at"] = db.iso(r["last_seen"]) if r["last_seen"] else None
    return render_template("portal_devices.html", devices=rows, active="devices")


@portal.post("/devices/<device_id>/revoke")
def revoke(device_id: str):
    pairing.revoke(context.connection(), device_id)
    return redirect(url_for("portal.devices"))
