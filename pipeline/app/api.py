"""The phone API, /api/v1, exactly as docs/phone-api.md describes it. The Android app is built against that
document, so a change starts there.

Every call except POST /pair carries the device's bearer token. The views are thin: they check what came in,
call app.batches, app.report or app.pairing, and turn the answer or the refusal into JSON.
"""
from __future__ import annotations

import json
import re

from flask import Blueprint, g, jsonify, make_response, request, send_file

from app import batches, context, db, pairing, reading, report, storage
from app.batches import ApiError
from app.storage import UploadError
from ingest.suppliers import SUPPLIERS

api = Blueprint("api", __name__, url_prefix="/api/v1")

CLIENT_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_ORDINAL = 10_000
IMAGE_WIDTHS = (64, 4096)


def error(status: int, code: str, message: str):
    return jsonify({"error": code, "message": message}), status


@api.errorhandler(ApiError)
@api.errorhandler(UploadError)
def _refused(e):
    return error(e.status, e.code, e.message)


@api.errorhandler(LookupError)
def _gone(e):
    return error(404, "not_found", "That does not exist for this shop.")


@api.errorhandler(FileNotFoundError)
def _no_key(e):
    return error(503, "key_unavailable", f"The shop's stock export could not be loaded: {e}")


@api.before_request
def _authenticate():
    if request.endpoint == "api.pair":
        return None
    device, problem = pairing.authenticate(context.connection(), request.headers.get("Authorization"))
    if problem == "device_revoked":
        return error(403, "device_revoked", "This phone was removed on the laptop. Pair it again to carry on.")
    if device is None:
        return error(401, "unauthorised", "This phone is not paired with the laptop. Pair it from the laptop's /pair page.")
    g.device = device
    return None


def _body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_body", "Send a JSON object.")
    return body


# --- 1. pairing ---------------------------------------------------------------------------------

@api.post("/pair")
def pair():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    name = body.get("device_name")
    name = name.strip()[:80] if isinstance(name, str) and name.strip() else "Phone"
    paired = pairing.pair(context.connection(), body.get("pair_code"), name)
    if paired is None:
        return error(400, "pair_code_invalid", "That pairing code is wrong, expired or already used."
                                               " Show a new code on the laptop.")
    return jsonify(paired), 201


@api.get("/ping")
def ping():
    conn = context.connection()
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM jobs WHERE status IN ('queued', 'running') GROUP BY status")
                  .fetchall())
    return jsonify({"ok": True, "server_time": db.iso(), "shop": g.device["shop_id"],
                    "queue": {"waiting": counts.get("queued", 0), "reading": counts.get("running", 0)}})


# --- 2. batches and uploads -----------------------------------------------------------------------

@api.post("/batches")
def create_batch():
    body = _body()
    key = body.get("client_batch_key")
    if not isinstance(key, str) or not CLIENT_KEY.match(key):
        raise ApiError(400, "invalid_body", "'client_batch_key' must be the phone's UUID for this scanner session.")
    page_count = body.get("page_count")
    if page_count is not None and (isinstance(page_count, bool) or not isinstance(page_count, int)
                                   or not 0 <= page_count <= MAX_ORDINAL):
        raise ApiError(400, "invalid_body", "'page_count' must be a whole number of pages.")
    batch, created = batches.create_batch(context.connection(), g.device, key, page_count)
    return jsonify(batches.batch_created_json(batch)), 201 if created else 200


def _header_ordinal(required: bool):
    raw = request.headers.get("X-Photo-Ordinal")
    if raw is None:
        if required:
            raise ApiError(400, "invalid_header", "X-Photo-Ordinal is required: the page's position in the scanner result.")
        return None
    if not raw.strip().isdigit() or not 1 <= int(raw) <= MAX_ORDINAL:
        raise ApiError(400, "invalid_header", "X-Photo-Ordinal must be a whole number from 1.")
    return int(raw)


def _header_quality():
    raw = request.headers.get("X-Quality")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None          # the phone's own checks are advisory; the server measures the photograph itself
    return value if isinstance(value, dict) else None


@api.put("/batches/<batch_id>/photos/<client_photo_key>")
def put_photo(batch_id: str, client_photo_key: str):
    conn, settings, device = context.connection(), context.settings(), g.device
    stream = request.stream
    try:
        batch = db.one(conn, "SELECT * FROM batches WHERE batch_id = ? AND shop_id = ?", batch_id, device["shop_id"])
        if batch is None:
            raise batches.not_found("That batch")
        if not CLIENT_KEY.match(client_photo_key):
            raise ApiError(400, "invalid_key", "The client photo key must be the phone's UUID for the photograph.")
        sha = (request.headers.get("X-Content-SHA256") or "").strip()
        if not SHA256_HEX.match(sha):
            raise ApiError(400, "invalid_header", "X-Content-SHA256 must be the hex SHA-256 of the body.")
        existing = batches.find_photo(conn, batch_id, client_photo_key)
        if existing is not None and existing["sha256"] != sha.lower():
            raise ApiError(409, "key_reused", "That photo key was already used for a different photograph.")
        replaces = (request.headers.get("X-Replaces-Photo") or "").strip() or None
        if existing is None:
            if batch["status"] == "submitted" and not replaces:
                raise ApiError(423, "batch_submitted", "This batch was submitted. Only retakes can still be sent.")
            if replaces and db.one(conn, "SELECT 1 FROM photos WHERE photo_id = ? AND batch_id = ?", replaces, batch_id) is None:
                raise batches.not_found("The photograph to replace")
        ordinal = _header_ordinal(required=existing is None and not replaces)
        quality = _header_quality()
    except ApiError:
        storage.drain(stream)
        raise

    received = storage.receive(stream, settings.tmp, sha, request.content_length)
    if existing is not None:
        storage.discard(received)
        return jsonify(batches.receipt(existing)), 200
    photo, created = batches.record_photo(conn, settings, device, batch, client_photo_key, received, ordinal, quality,
                                          replaces)
    if not created and photo["sha256"] != received.sha256:
        raise ApiError(409, "key_reused", "That photo key was already used for a different photograph.")
    return jsonify(batches.receipt(photo)), 201 if created else 200


@api.post("/batches/<batch_id>/complete")
def complete(batch_id: str):
    conn = context.connection()
    batch = batches.get_batch(conn, batch_id, g.device["shop_id"])
    return jsonify({"status": batches.complete(conn, batch, g.device)})


# --- 3. reading the results --------------------------------------------------------------------------

@api.get("/batches")
def list_batches():
    return jsonify({"batches": batches.summaries(context.connection(), g.device["shop_id"])})


@api.get("/batches/<batch_id>")
def get_batch(batch_id: str):
    conn = context.connection()
    batches.get_batch(conn, batch_id, g.device["shop_id"])
    state = reading.load_batch(conn, batch_id)
    tag = batches.etag(state)
    if request.if_none_match.contains(tag):
        response = make_response("", 304)
    else:
        response = jsonify(batches.batch_json(state))
    response.set_etag(tag)
    response.headers["Cache-Control"] = "no-cache"
    return response


def send_photo(photo: dict, width_arg):
    settings = context.settings()
    path = settings.photos / photo["storage_path"]
    if width_arg is not None:
        if not width_arg.isdigit() or not IMAGE_WIDTHS[0] <= int(width_arg) <= IMAGE_WIDTHS[1]:
            raise ApiError(400, "invalid_query", f"w must be a width from {IMAGE_WIDTHS[0]} to {IMAGE_WIDTHS[1]} pixels.")
        path = storage.downscaled(path, int(width_arg), settings.thumbs / photo["batch_id"], photo["photo_id"])
    mimetype = photo["content_type"] if path.suffix.lower() != ".jpg" or photo["content_type"] == "image/jpeg" else "image/jpeg"
    return send_file(path, mimetype=mimetype, conditional=True, max_age=3600)


@api.get("/invoices/<invoice_id>")
def get_invoice(invoice_id: str):
    """One invoice, whichever batch of this shop it is in: the phone shows the invoice a duplicate repeats."""
    return jsonify(batches.invoice_for_shop(context.connection(), invoice_id, g.device["shop_id"]))


@api.get("/photos/<photo_id>/image")
def photo_image(photo_id: str):
    photo = batches.photo_for_shop(context.connection(), photo_id, g.device["shop_id"])
    return send_photo(photo, request.args.get("w"))


# --- 5. manager actions ------------------------------------------------------------------------------

@api.get("/stock/search")
def stock_search():
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        raise ApiError(400, "invalid_query", "limit must be a whole number.")
    conn = context.connection()
    key = context.keys().get(conn, g.device["shop_id"])
    items = key.search(request.args.get("q", ""), max(1, min(limit, 100)))
    return jsonify({"items": [{"code": i.code, "name": i.name, "base_unit": i.base.unit, "category": i.category}
                              for i in items]})


@api.get("/suppliers")
def suppliers():
    """Beyond the contract: the supplier keys PATCH /invoices accepts, with the names Pilot files them under."""
    return jsonify({"suppliers": [{"key": s.key, "name": s.pilot_name} for s in SUPPLIERS]})


@api.put("/invoices/<invoice_id>/lines/<int:index>")
def edit_line(invoice_id: str, index: int):
    body = request.get_json(silent=True)
    return jsonify(batches.edit_line(context.connection(), context.keys(), g.device, invoice_id, index, body))


@api.post("/invoices/<invoice_id>/lines")
def add_line(invoice_id: str):
    body = request.get_json(silent=True)
    return jsonify(batches.add_line(context.connection(), context.keys(), g.device, invoice_id, body)), 201


@api.delete("/invoices/<invoice_id>/lines/<int:index>")
def remove_line(invoice_id: str, index: int):
    return jsonify(batches.remove_line(context.connection(), context.keys(), g.device, invoice_id, index))


@api.patch("/invoices/<invoice_id>")
def patch_invoice(invoice_id: str):
    body = request.get_json(silent=True)
    return jsonify(batches.patch_invoice(context.connection(), context.keys(), g.device, invoice_id, body))


@api.post("/invoices/<invoice_id>/delete")
def delete_invoice(invoice_id: str):
    return jsonify(batches.delete_invoice(context.connection(), context.keys(), g.device, invoice_id))


@api.post("/invoices/<invoice_id>/not_duplicate")
def not_duplicate(invoice_id: str):
    return jsonify(batches.keep_invoice(context.connection(), context.keys(), g.device, invoice_id))


# --- 7. submit ---------------------------------------------------------------------------------------

@api.post("/batches/<batch_id>/submit")
def submit(batch_id: str):
    return jsonify(report.submit(context.connection(), context.keys(), context.settings(), g.device, batch_id))
