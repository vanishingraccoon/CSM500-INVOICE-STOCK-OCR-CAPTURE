"""The Flask app: the laptop portal and the phone API, plus the binder review pages.

- The portal (app.portal) is at /, and the phone API (app.api) at /api/v1, as docs/phone-api.md describes.
- The single-invoice review pages for the August binder moved under /binder. They are unchanged otherwise:
  a person accepts or corrects each line and exports rows to key into Pilot. Nothing is written to Pilot:
  it is the live stock system of a trading business, and the person keying the rows is the check the whole
  design depends on.
- /adjudicate, the research page settling disagreements with Pilot, is unchanged.

    python -m app.serve           # the laptop server: this app plus the background readers (see app/serve.py)
    python -m app.server          # this app alone, without readers: http://127.0.0.1:5000, this machine only

There are no logins yet (future work). Serve it on the office network only.
"""
from __future__ import annotations

import csv
import io
import math
import time
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import config
from app import data
from normaliser.stock import default_stock_file, load_stock

# templates and static files live beside this module, however it is started (python -m, runpy, a test client)
app = Flask(__name__, root_path=str(Path(__file__).resolve().parent))
# above the API's own 25 MB limit, so an oversized photograph gets the contract's 415 rather than a bare 413
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
_stock = None


def stock():
    global _stock
    if _stock is None:
        _stock = load_stock(default_stock_file())
    return _stock


ACTIONS = ("accept", "correct", "skip")


def _float(text: str | None) -> float | None:
    try:
        value = float(str(text).replace(",", "").strip()) if text not in (None, "") else None
    except ValueError:
        return None
    return value if value is None or math.isfinite(value) else None   # "nan" and "inf" parse as floats


def _differs(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) > 1e-6
    return (a or None) != (b or None)


# --- binder review pages ----------------------------------------------------------------------------

@app.get("/binder")
def index():
    records, normalised, flags, _ = data.load_all()
    reviewed = set(data.reviewed_ids())
    rows = []
    for r in records.values():
        ns = normalised.get(r.invoice_id, {}).values()
        rows.append({
            "id": r.invoice_id, "supplier": r.supplier or "unknown", "document_no": r.document_no or "",
            "date": r.date or "", "lines": len(r.lines),
            "converted": sum(1 for n in ns if not n.get("refusal") and n.get("base_quantity") is not None),
            "refused": sum(1 for n in ns if n.get("refusal")),
            "flags": len(flags.get(r.invoice_id, [])), "adds_up": r.adds_up, "reviewed": r.invoice_id in reviewed,
        })
    rows.sort(key=lambda x: (x["reviewed"], x["supplier"], x["date"]))
    return render_template("index.html", rows=rows, reviewed=len(reviewed))


@app.get("/binder/page/<path:name>")
def page_image(name: str):
    return send_from_directory(config.PAGES, name)


@app.get("/binder/invoice/<invoice_id>")
def invoice(invoice_id: str):
    records, normalised, flags, images = data.load_all()
    r = records.get(invoice_id) or abort(404)
    decided = (data.decision(invoice_id) or {}).get("rows", {})
    by_code = stock().by_code()
    lines = []
    for i, line in enumerate(r.lines):
        n = normalised.get(invoice_id, {}).get(i, {})
        d = decided.get(str(i), {})
        code = d.get("item_code", n.get("item_code"))
        lines.append({
            "i": i, "line": line, "n": n, "decided": d,
            "item_value": f"{code} {by_code[code].name}" if code in by_code else (code or ""),
            "qty_value": d.get("base_quantity", n.get("base_quantity")),
            "cost_value": d.get("cost", line.line_total),
            "action": d.get("action", "accept" if n.get("base_quantity") is not None and not n.get("refusal") else "correct"),
            "flags": [f for f in flags.get(invoice_id, []) if f.get("line_index") == i],
        })
    invoice_flags = [f for f in flags.get(invoice_id, []) if f.get("line_index") is None]
    return render_template("invoice.html", r=r, lines=lines, images=images.get(invoice_id, []),
                           invoice_flags=invoice_flags, stock_items=stock().items, opened=time.time(),
                           saved=request.args.get("saved"), decision=data.decision(invoice_id))


@app.post("/binder/invoice/<invoice_id>")
def save(invoice_id: str):
    records, normalised, flags, _ = data.load_all()
    r = records.get(invoice_id) or abort(404)
    by_code = stock().by_code()
    flagged = {f.get("line_index") for f in flags.get(invoice_id, [])}
    rows, corrections = {}, []
    for i, line in enumerate(r.lines):
        n = normalised.get(invoice_id, {}).get(i, {})
        action = request.form.get(f"action_{i}", "accept")
        if action not in ACTIONS:
            abort(400)
        checked = request.form.get(f"checked_{i}") == "1"
        # the review screen's guard, enforced here too: a refused or flagged line only saves as "accept" once
        # the reviewer ticks that they checked the photograph; recording the tick makes that measurable
        if action == "accept" and (n.get("refusal") or i in flagged) and not checked:
            action = "correct"
        code = (request.form.get(f"item_{i}", "").strip().split(" ") or [""])[0] or None
        final = {"item_code": code, "base_quantity": _float(request.form.get(f"qty_{i}")),
                 "cost": _float(request.form.get(f"cost_{i}")), "action": action,
                 "photo_checked": checked, "valid": False}
        # valid means the export will write it: a known item, a quantity and a cost
        final["valid"] = bool(code in by_code and action != "skip"
                              and final["base_quantity"] is not None and final["cost"] is not None)
        proposed = {"item_code": n.get("item_code"), "base_quantity": n.get("base_quantity"),
                    "cost": line.line_total, "refusal": n.get("refusal")}
        changed = ["skipped"] if action == "skip" else [k for k in ("item_code", "base_quantity", "cost")
                                                         if _differs(proposed[k], final[k])]
        rows[str(i)] = final
        corrections.append({"line_index": i, "description": line.description, "proposed": proposed,
                            "final": final, "changed": changed, "was_refused": bool(n.get("refusal"))})
    opened = _float(request.form.get("opened")) or time.time()
    data.save_review(invoice_id, rows, corrections, time.time() - opened)
    return redirect(url_for("invoice", invoice_id=invoice_id, saved=1))


def _export(ids: list[str]) -> Response:
    records, _, _, _ = data.load_all()
    by_code = stock().by_code()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["supplier", "reference", "date", "item_code", "item_name", "quantity_base", "base_unit", "cost_excl_vat", "vat"])
    for invoice_id in ids:
        r, d = records.get(invoice_id), data.decision(invoice_id)
        if not r or not d:
            continue
        for i, line in enumerate(r.lines):
            row = d["rows"].get(str(i))
            if not row or not row.get("valid") or row.get("base_quantity") is None or row.get("cost") is None:
                continue
            item = by_code[row["item_code"]]
            w.writerow([r.supplier or "", r.document_no or "", r.date or "", item.code, item.name,
                        f"{row['base_quantity']:g}", item.base.unit or item.base.raw, f"{row['cost']:.2f}",
                        "" if line.vat is None else f"{line.vat:.2f}"])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=pilot_rows_{time.strftime('%Y%m%d_%H%M')}.csv"})


@app.get("/binder/invoice/<invoice_id>/export.csv")
def export_one(invoice_id: str):
    if invoice_id not in data.load_all()[0]:
        abort(404)
    return _export([invoice_id])


@app.get("/binder/export.csv")
def export_all():
    return _export([i for i in data.reviewed_ids() if i])


@app.post("/binder/upload")
def upload():
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        return redirect(url_for("index"))
    from PIL import UnidentifiedImageError

    from app.pipeline import process_upload  # loads the recognition models on first use
    try:
        invoice_id = process_upload(photo)
    except UnidentifiedImageError:
        return redirect(url_for("index", upload_error="not_image"))
    return redirect(url_for("invoice", invoice_id=invoice_id))


# --- adjudication (research page, unchanged) ----------------------------------------------------------

def _find_disagreement(items: list[dict], invoice_id: str, line_index: int) -> dict:
    return next((d for d in items if d["invoice_id"] == invoice_id and d["line_index"] == line_index), None) or abort(404)


@app.get("/adjudicate")
def adjudicate_list():
    from app import adjudication
    items = adjudication.disagreements()
    decided = adjudication.verdicts()
    kind = request.args.get("kind")
    return render_template("adjudicate_list.html", items=[d for d in items if not kind or d["kind"] == kind],
                           decided=decided, summary=adjudication.summary(items, decided), total=len(items))


@app.get("/adjudicate/<invoice_id>/<int:line_index>")
def adjudicate_line(invoice_id: str, line_index: int):
    from app import adjudication
    items = adjudication.disagreements()
    item = _find_disagreement(items, invoice_id, line_index)
    decided = adjudication.verdicts()
    return render_template("adjudicate_line.html", d=item, verdict=decided.get(item["key"]),
                           remaining=sum(1 for d in items if d["key"] not in decided))


@app.post("/adjudicate/<invoice_id>/<int:line_index>")
def adjudicate_save(invoice_id: str, line_index: int):
    from app import adjudication
    items = adjudication.disagreements()
    item = _find_disagreement(items, invoice_id, line_index)
    verdict = request.form.get("verdict", "")
    if verdict not in adjudication.VERDICTS:
        abort(400)
    adjudication.record(item, verdict, request.form.get("note", ""))
    decided = adjudication.verdicts()
    following = next((d for d in items if d["key"] not in decided), None)
    if following is None:
        return redirect(url_for("adjudicate_list"))
    return redirect(url_for("adjudicate_line", invoice_id=following["invoice_id"], line_index=following["line_index"]))


# --- the portal and the phone API ------------------------------------------------------------------------

from app.api import (
    api,
)
from app.portal import portal

app.register_blueprint(api)
app.register_blueprint(portal)


def _api_error(status: int, code: str, message: str):
    return jsonify({"error": code, "message": message}), status


@app.errorhandler(404)
def _not_found(e):
    if request.path.startswith("/api/"):
        return _api_error(404, "not_found", "There is nothing at that address.")
    return e


@app.errorhandler(405)
def _wrong_method(e):
    if request.path.startswith("/api/"):
        return _api_error(405, "method_not_allowed", "That address does not take this method.")
    return e


@app.errorhandler(413)
def _too_large(e):
    if request.path.startswith("/api/"):
        return _api_error(415, "unsupported_image", "The photograph is larger than 25 MB.")
    return e


if __name__ == "__main__":
    import os
    # This machine only by default. For the phones, set INVOICE_HOST=0.0.0.0 so a phone on the same network
    # can reach http://<laptop-ip>:5000. Anyone who can reach it sees invoice photographs, so only on a trusted network.
    app.run(host=os.environ.get("INVOICE_HOST", "127.0.0.1"), port=int(os.environ.get("INVOICE_PORT", "5000")),
            debug=False)
