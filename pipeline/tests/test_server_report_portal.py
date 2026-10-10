"""The input report built from a submitted batch's frozen snapshot, the laptop portal's pages, and the database's
own guarantees. Figures are substituted."""
import csv
import io
import json

import cv2
import numpy as np
import pymupdf
import pytest
from serverkit import SHOP, invoice_words, photo_bytes, sha

from app import context, db, pairing, report, settings


def _submitted(phone, reader) -> str:
    batch = phone.scan(reader, [
        (photo_bytes(1), invoice_words("102595", lines=[("Tequila Mix (750ml)", 2, 100.00)], show_totals=False)),
        (photo_bytes(2), invoice_words("102595", lines=[("Mystery Widget 5L", 2, 10.00)], totals=(220.00, 33.00))),
        (photo_bytes(3, blurred=True), invoice_words("102600")),
    ])
    first = batch["invoices"][0]
    phone.put(f"/api/v1/invoices/{first['invoice_id']}/lines/1",
              json={"free_text": "widget tub 5 litre", "quantity": 10, "cost": 20.00})
    assert phone.post(f"/api/v1/batches/{batch['batch_id']}/submit").status_code == 200
    return batch["batch_id"]


def test_the_report_pdf_opens_with_each_invoice_titled_and_a_tick_box_per_row(api_client, phone, reader):
    batch_id = _submitted(phone, reader)
    r = api_client.get(f"/portal/batches/{batch_id}/report.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    text = "".join(page.get_text() for page in pymupdf.open("pdf", r.data))
    assert "INV 102595 (pages 1, 2)" in text and "tequila mix (750ML)" in text and "☐" in text
    assert "Not in the key" in text and "widget tub 5 litre" in text and "✎" in text
    assert "Unresolved invoices" in text and "INV 102600 (page 3)" in text
    row = report.latest(context.connection(), batch_id)
    assert row["status"] == "ready" and row["pdf_sha256"] == sha(r.data)
    assert api_client.get(f"/batches/{batch_id}/report.pdf").data == r.data


def test_the_report_csv_has_the_same_rows_and_says_which_the_manager_typed(api_client, phone, reader):
    batch_id = _submitted(phone, reader)
    body = api_client.get(f"/batches/{batch_id}/report.csv").data
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    assert [(r["section"], r["item"], r["quantity_base"], r["cost_excl_vat"], r["typed_by_manager"]) for r in rows] == [
        ("invoice", "tequila mix (750ML)", "2", "200.00", "no"),
        ("not_in_key", "widget tub 5 litre", "10", "20.00", "yes"),
        ("unresolved", "", "", "", "no")]
    assert (rows[0]["invoice"], rows[0]["pages"], rows[0]["vat"], rows[0]["base_unit"]) == ("102595", "1 2", "30.00", "each")
    assert rows[2]["note"].startswith("retake: page 3: photograph looks blurred")
    assert report.latest(context.connection(), batch_id)["csv_sha256"] == sha(body)


def test_an_unsubmitted_batch_has_no_report(api_client, phone):
    batch_id = phone.new_batch()
    assert api_client.get(f"/batches/{batch_id}/report.pdf").status_code == 404


def test_a_missing_report_file_is_rebuilt_byte_for_byte_from_the_snapshot(api_client, phone, reader, shop):
    batch_id = _submitted(phone, reader)
    row = report.latest(context.connection(), batch_id)
    (shop.reports / row["csv_path"]).unlink()
    rebuilt = api_client.get(f"/batches/{batch_id}/report.csv").data
    assert sha(rebuilt) == row["csv_sha256"]


def test_invoices_are_reported_by_supplier_then_number():
    entries = [{"supplier_name": "Vassco Distributors", "document_no": "20", "pages": [3]},
               {"supplier_name": None, "document_no": None, "pages": [4]},
               {"supplier_name": "Aero Power Trading", "document_no": "I00100", "pages": [2]},
               {"supplier_name": "Vassco Distributors", "document_no": "3", "pages": [1]}]
    ordered = sorted(entries, key=report._order)
    assert [(e["supplier_name"], e["document_no"]) for e in ordered] == [
        ("Aero Power Trading", "I00100"), ("Vassco Distributors", "3"), ("Vassco Distributors", "20"), (None, None)]


def test_a_deleted_duplicate_never_reaches_the_report(api_client, phone, reader):
    batch = phone.scan(reader, [(photo_bytes(5), invoice_words("102595")), (photo_bytes(6), invoice_words("102600")),
                                (photo_bytes(7), invoice_words("102595"))])
    phone.post(f"/api/v1/invoices/{batch['invoices'][2]['invoice_id']}/delete")
    phone.post(f"/api/v1/batches/{batch['batch_id']}/submit")
    rows = list(csv.DictReader(io.StringIO(api_client.get(f"/batches/{batch['batch_id']}/report.csv").data.decode("utf-8-sig"))))
    assert [(r["invoice"], r["pages"]) for r in rows] == [("102595", "1"), ("102600", "2")]


# --- rebuilding a submitted batch's report ----------------------------------------------------------------------

def test_rebuilding_is_refused_before_the_batch_is_submitted_and_while_a_retake_is_still_being_read(api_client, phone,
                                                                                                   reader):
    batch_id = phone.new_batch()
    photo_id = phone.upload(batch_id, "p1", photo_bytes(20), ordinal=1).get_json()["photo_id"]
    too_early = api_client.post(f"/batches/{batch_id}/rebuild-report")
    assert too_early.status_code == 409 and "has not been submitted" in too_early.get_data(as_text=True)
    reader.page(photo_bytes(20), invoice_words("102595"))
    reader.run()
    assert phone.post(f"/api/v1/batches/{batch_id}/submit").status_code == 200
    phone.upload(batch_id, "retake-1", photo_bytes(21), replaces=photo_id)
    too_soon = api_client.post(f"/batches/{batch_id}/rebuild-report")
    assert too_soon.status_code == 409 and "still being read" in too_soon.get_data(as_text=True)
    assert db.one(context.connection(), "SELECT COUNT(*) AS n FROM reports")["n"] == 1


def test_a_retake_read_after_submit_is_rebuilt_into_version_2_and_version_1_keeps_its_file(api_client, phone, reader,
                                                                                           shop):
    batch = phone.scan(reader, [(photo_bytes(22), invoice_words("102595", lines=[("Tequila Mix (750ml)", 2, 100.00)])),
                                (photo_bytes(23, blurred=True), invoice_words("102600"))], key="week-1")
    batch_id = batch["batch_id"]
    assert [i["tab"] for i in batch["invoices"]] == ["good", "retake"]
    phone.post(f"/api/v1/batches/{batch_id}/submit")
    first = report.latest(context.connection(), batch_id)
    sharp = reader.page(photo_bytes(24), invoice_words("102600", lines=[("Honey 5L", 3, 50.00)]))
    phone.upload(batch_id, "retake-1", sharp, replaces=batch["invoices"][1]["pages"][0]["photo_id"])
    reader.run()

    r = api_client.post(f"/batches/{batch_id}/rebuild-report")
    assert r.status_code == 302
    second = report.latest(context.connection(), batch_id)
    assert (second["version"], second["status"]) == (2, "ready")
    pdf = api_client.get(f"/batches/{batch_id}/report.pdf")
    text = "".join(page.get_text() for page in pymupdf.open("pdf", pdf.data))
    assert "report version 2" in text and second["pdf_sha256"] == sha(pdf.data)
    assert "INV 102600 (page 2)" in text and "honey (LITRE)" in text and "150.00" in text
    assert (shop.reports / first["pdf_path"]).exists() and first["pdf_path"] != second["pdf_path"]
    was = "".join(p.get_text() for p in pymupdf.open(shop.reports / first["pdf_path"]))
    assert "honey (LITRE)" not in was and "page 2: photograph looks blurred" in was
    detail = json.loads(db.one(context.connection(), "SELECT detail FROM events WHERE kind = 'report_rebuilt'")["detail"])
    assert detail["version"] == 2 and detail["corrected"] == [batch["invoices"][1]["invoice_id"]]
    assert db.one(context.connection(), "SELECT needs_report FROM invoices WHERE invoice_id = ?",
                  batch["invoices"][1]["invoice_id"])["needs_report"] == 0
    page = api_client.get(f"/batches/{batch_id}").get_data(as_text=True)
    assert "Input report version 2" in page and "1 earlier version kept" in page and "Rebuild report" in page


# --- portal pages ---------------------------------------------------------------------------------------------

def test_portal_pages_show_batches_invoices_by_tab_and_the_key(api_client, phone, reader):
    batch = phone.scan(reader, [(photo_bytes(10), invoice_words(lines=[("Tequila Mix (750ml)", 2, 100.00),
                                                                        ("Mystery Widget 5L", 2, 10.00)])),
                                (photo_bytes(11, blurred=True), invoice_words("102600"))])
    assert "News Cafe Testshop" in api_client.get("/").get_data(as_text=True)
    page = api_client.get(f"/batches/{batch['batch_id']}").get_data(as_text=True)
    assert 'class="tab-section tab-corrections"' in page and "line 2: no stock item matched" in page
    assert "Mystery Widget 5L" in page and 'class="tab-section tab-retake"' in page
    photo_id = batch["invoices"][0]["pages"][0]["photo_id"]
    assert api_client.get(f"/portal/photos/{photo_id}/image?w=200").status_code == 200
    assert "INV 102600 (page 2)" in api_client.get("/unresolved").get_data(as_text=True)
    key_page = api_client.get(f"/key?shop={SHOP}&q=honey").get_data(as_text=True)
    assert "honey (LITRE)" in key_page and "Learned aliases" in key_page
    assert "Test phone" in api_client.get("/devices").get_data(as_text=True)
    assert api_client.get("/batches/b_nope").status_code == 404


def test_the_pair_page_shows_a_qr_code_holding_the_contract_json(api_client):
    html = api_client.get(f"/pair?shop={SHOP}").get_data(as_text=True)
    code = db.one(context.connection(), "SELECT code FROM pair_codes WHERE shop_id = ?", SHOP)["code"]
    assert code in html and "Valid until" in html
    png = api_client.get(f"/pair/qr/{code}.png")
    assert png.mimetype == "image/png"
    image = cv2.imdecode(np.frombuffer(png.data, np.uint8), cv2.IMREAD_GRAYSCALE)
    payload = json.loads(cv2.QRCodeDetector().detectAndDecode(image)[0])
    assert set(payload) == {"v", "server", "pair_code", "shop"}
    assert payload["v"] == 1 and payload["pair_code"] == code and payload["shop"] == SHOP
    assert payload["server"].startswith("http://") and payload["server"].endswith(":5000")


def test_the_address_given_to_phones_can_be_set_by_hand(shop, monkeypatch):
    monkeypatch.setattr(settings, "_current", shop.with_(public_url="http://192.168.1.20:5000/"))
    code = pairing.issue_code(context.connection(), SHOP)
    assert json.loads(pairing.qr_payload(settings.current(), code))["server"] == "http://192.168.1.20:5000"


def test_revoking_a_phone_on_the_portal_locks_it_out(api_client, phone):
    r = api_client.post(f"/devices/{phone.device['device_id']}/revoke")
    assert r.status_code == 302 and phone.get("/api/v1/ping").status_code == 403
    assert "revoked" in api_client.get("/devices").get_data(as_text=True)


# --- the database ---------------------------------------------------------------------------------------------

def test_the_schema_is_versioned_and_migrating_again_changes_nothing(tmp_path):
    conn = db.connection(tmp_path / "invoices.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.migrate(conn) == len(db.MIGRATIONS)


def test_events_can_be_added_but_never_changed_or_deleted(tmp_path):
    import sqlite3
    conn = db.connection(tmp_path / "invoices.db")
    with db.transaction(conn):
        db.event(conn, "upload_received", batch_id="b_1", bytes=812)
    for statement in ("UPDATE events SET kind = 'nothing'", "DELETE FROM events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with db.transaction(conn):
                conn.execute(statement)
    assert json.loads(db.one(conn, "SELECT detail FROM events")["detail"]) == {"bytes": 812}
