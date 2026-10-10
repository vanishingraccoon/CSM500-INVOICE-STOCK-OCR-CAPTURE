"""Laptop server, phone side: pairing, tokens, batches and uploads (docs/phone-api.md sections 1 and 2), and
section 3's ways of reading one thing on its own: an invoice and a photograph."""
import hashlib
import io
import json
import time

from PIL import Image
from serverkit import OTHER_SHOP, SHOP, Phone, invoice_words, photo_bytes, sha

from app import context, db, pairing


def _count(sql, *params):
    return context.connection().execute(sql, params).fetchone()[0]


# --- pairing and tokens ----------------------------------------------------------------------------

def test_a_pair_code_works_once_and_only_the_tokens_hash_is_stored(api_client):
    conn = context.connection()
    code = pairing.issue_code(conn, SHOP)["code"]
    r = api_client.post("/api/v1/pair", json={"pair_code": code.lower(), "device_name": "Galaxy (store 1)"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["shop"] == SHOP and body["shop_name"] == "News Cafe Testshop"
    assert body["device_id"].startswith("dev_") and len(body["token"]) >= 48
    row = db.one(conn, "SELECT * FROM devices WHERE device_id = ?", body["device_id"])
    assert row["token_hash"] == hashlib.sha256(body["token"].encode()).hexdigest()
    assert body["token"] not in json.dumps(db.all_rows(conn, "SELECT * FROM devices"))
    again = api_client.post("/api/v1/pair", json={"pair_code": code, "device_name": "Another phone"})
    assert again.status_code == 400 and again.get_json()["error"] == "pair_code_invalid"


def test_an_expired_pair_code_is_refused(api_client, monkeypatch):
    code = pairing.issue_code(context.connection(), SHOP)["code"]
    later = time.time() + pairing.CODE_SECONDS + 5
    monkeypatch.setattr(db, "now", lambda: later)
    r = api_client.post("/api/v1/pair", json={"pair_code": code, "device_name": "Late phone"})
    assert r.status_code == 400 and r.get_json()["error"] == "pair_code_invalid"


def test_pair_codes_are_six_unambiguous_characters(shop):
    codes = [pairing.issue_code(context.connection(), SHOP)["code"] for _ in range(25)]
    assert all(len(c) == 6 and set(c) <= set(pairing.CODE_ALPHABET) for c in codes)
    assert not set("".join(codes)) & set("01IO")


def test_calls_without_a_valid_token_are_unauthorised(api_client, phone):
    assert api_client.get("/api/v1/ping").get_json()["error"] == "unauthorised"
    r = api_client.get("/api/v1/batches", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401 and r.get_json()["error"] == "unauthorised"
    ping = phone.get("/api/v1/ping").get_json()
    assert ping["ok"] is True and ping["shop"] == SHOP and ping["queue"] == {"waiting": 0, "reading": 0}


def test_a_revoked_device_is_forbidden(phone):
    pairing.revoke(context.connection(), phone.device["device_id"])
    r = phone.get("/api/v1/batches")
    assert r.status_code == 403 and r.get_json()["error"] == "device_revoked"


# --- batches ------------------------------------------------------------------------------------------

def test_creating_a_batch_again_with_the_same_key_returns_the_same_batch(phone):
    first = phone.post("/api/v1/batches", json={"client_batch_key": "0f8e2c1a-batch", "page_count": 3})
    second = phone.post("/api/v1/batches", json={"client_batch_key": "0f8e2c1a-batch", "page_count": 3})
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.get_json() == second.get_json()
    assert first.get_json()["status"] == "uploading" and first.get_json()["batch_id"].startswith("b_")
    assert _count("SELECT COUNT(*) FROM batches") == 1


def test_a_batch_without_its_key_is_refused(phone):
    r = phone.post("/api/v1/batches", json={"page_count": 3})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_body"


def test_another_shops_phone_cannot_see_a_batch(api_client, phone):
    batch_id = phone.new_batch()
    stranger = Phone(api_client, shop=OTHER_SHOP, name="Other shop's phone")
    assert stranger.get(f"/api/v1/batches/{batch_id}").status_code == 404
    assert stranger.get("/api/v1/batches").get_json() == {"batches": []}
    assert [b["batch_id"] for b in phone.get("/api/v1/batches").get_json()["batches"]] == [batch_id]


# --- photograph uploads ------------------------------------------------------------------------------

def test_an_upload_is_stored_whole_with_its_reading_job_and_answered_with_a_matching_receipt(phone, shop):
    batch_id = phone.new_batch()
    data = photo_bytes(1)
    r = phone.upload(batch_id, "photo-0001", data, ordinal=1)
    assert r.status_code == 201
    receipt = r.get_json()
    assert receipt == {"photo_id": receipt["photo_id"], "batch_id": batch_id, "client_photo_key": "photo-0001",
                       "sha256": sha(data), "bytes": len(data), "state": "stored"}
    row = db.one(context.connection(), "SELECT * FROM photos WHERE photo_id = ?", receipt["photo_id"])
    assert (shop.photos / row["storage_path"]).read_bytes() == data and row["ordinal"] == 1
    assert db.one(context.connection(), "SELECT status FROM jobs WHERE photo_id = ?", receipt["photo_id"])["status"] == "queued"
    assert not list(shop.tmp.iterdir())
    kinds = [e["kind"] for e in db.all_rows(context.connection(), "SELECT kind FROM events ORDER BY event_id")]
    assert kinds.index("upload_received") < kinds.index("receipt_issued")


def test_an_exact_retry_gets_the_same_receipt_and_stores_nothing_twice(phone, shop):
    batch_id = phone.new_batch()
    data = photo_bytes(2)
    first = phone.upload(batch_id, "photo-0001", data, ordinal=1)
    retry = phone.upload(batch_id, "photo-0001", data, ordinal=1)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.get_json() == first.get_json()
    assert _count("SELECT COUNT(*) FROM photos") == 1 and _count("SELECT COUNT(*) FROM jobs") == 1
    assert len(list(shop.photos.rglob("*.jpg"))) == 1


def test_the_same_key_with_different_content_is_refused(phone):
    batch_id = phone.new_batch()
    phone.upload(batch_id, "photo-0001", photo_bytes(3), ordinal=1)
    r = phone.upload(batch_id, "photo-0001", photo_bytes(4), ordinal=1)
    assert r.status_code == 409 and r.get_json()["error"] == "key_reused"


def test_a_body_that_does_not_match_its_hash_is_refused_and_nothing_is_kept(phone, shop):
    batch_id = phone.new_batch()
    r = phone.upload(batch_id, "photo-0001", photo_bytes(5), ordinal=1, digest=sha(b"the phone's real file"))
    assert r.status_code == 422 and r.get_json()["error"] == "hash_mismatch"
    assert _count("SELECT COUNT(*) FROM photos") == 0
    assert not list(shop.photos.rglob("*.*")) and not list(shop.tmp.iterdir())


def _png(width, height) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (width, height), 200).save(buf, "PNG")
    return buf.getvalue()


def test_files_that_are_not_one_jpeg_or_png_within_the_limits_are_unsupported(phone, shop):
    batch_id = phone.new_batch()
    gif = io.BytesIO()
    Image.new("L", (40, 40)).save(gif, "GIF", save_all=True, append_images=[Image.new("L", (40, 40), 255)])
    for n, data in enumerate((b"%PDF-1.4 a document, not a photograph", gif.getvalue(), _png(12_001, 20))):
        r = phone.upload(batch_id, f"bad-{n}", data, ordinal=1)
        assert r.status_code == 415 and r.get_json()["error"] == "unsupported_image"
    assert _count("SELECT COUNT(*) FROM photos") == 0 and not list(shop.tmp.iterdir())
    assert phone.upload(batch_id, "a-png", _png(1200, 1600), ordinal=1).status_code == 201


def test_uploads_need_an_ordinal_and_a_known_batch(phone):
    batch_id = phone.new_batch()
    data = photo_bytes(6)
    assert phone.upload("b_nope", "photo-0001", data, ordinal=1).status_code == 404
    r = phone.upload(batch_id, "photo-0001", data)
    assert r.status_code == 400 and "X-Photo-Ordinal" in r.get_json()["message"]


def test_complete_says_processing_while_photographs_wait_to_be_read(phone):
    batch_id = phone.new_batch()
    phone.upload(batch_id, "photo-0001", photo_bytes(7), ordinal=1)
    assert phone.post(f"/api/v1/batches/{batch_id}/complete").get_json() == {"status": "processing"}
    batch = phone.batch(batch_id)
    assert batch["status"] == "processing" and batch["progress"] == {"photos": 1, "waiting": 1, "reading": 0, "read": 0,
                                                                     "failed": 0}


def test_the_batch_answers_not_modified_until_something_changes(phone, reader):
    batch_id = phone.new_batch()
    phone.upload(batch_id, "photo-0001", photo_bytes(8), ordinal=1)
    first = phone.get(f"/api/v1/batches/{batch_id}")
    tag = first.headers["ETag"]
    assert phone.get(f"/api/v1/batches/{batch_id}", headers={"If-None-Match": tag}).status_code == 304
    reader.run()
    changed = phone.get(f"/api/v1/batches/{batch_id}", headers={"If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["ETag"] != tag


# --- one invoice on its own ---------------------------------------------------------------------------

def test_the_invoice_a_duplicate_repeats_is_fetched_on_its_own_from_a_batch_the_phone_never_loaded(phone, reader):
    first = phone.scan(reader, [(photo_bytes(20), invoice_words("102595"))], key="week-1")
    assert phone.post(f"/api/v1/batches/{first['batch_id']}/submit").status_code == 200
    again = phone.scan(reader, [(photo_bytes(21), invoice_words("102595"))], key="week-2")
    repeat = again["invoices"][0]
    assert repeat["tab"] == "duplicates" and repeat["duplicate_of"] == first["invoices"][0]["invoice_id"]
    r = phone.get(f"/api/v1/invoices/{repeat['duplicate_of']}")
    assert r.status_code == 200
    assert r.get_json() == phone.batch(first["batch_id"])["invoices"][0]
    assert r.get_json()["title"] == "INV 102595 (page 1)"


def test_another_shops_invoice_an_unknown_one_and_a_call_without_a_token_are_all_refused(api_client, phone, reader):
    batch = phone.scan(reader, [(photo_bytes(22), invoice_words("102595"))])
    invoice_id = batch["invoices"][0]["invoice_id"]
    stranger = Phone(api_client, shop=OTHER_SHOP, name="Other shop's phone")
    assert stranger.get(f"/api/v1/invoices/{invoice_id}").status_code == 404
    assert phone.get("/api/v1/invoices/i_nosuchthing").status_code == 404
    assert api_client.get(f"/api/v1/invoices/{invoice_id}").status_code == 401


def test_an_invoice_whose_pages_joined_another_says_to_fetch_the_batch_again(phone, reader):
    batch = phone.scan(reader, [(photo_bytes(23), invoice_words("102595", lines=[("Tequila Mix (750ml)", 2, 100.00)])),
                                (photo_bytes(24), invoice_words("102600", lines=[("Honey 5L", 1, 50.00)]))])
    second = batch["invoices"][1]
    same_invoice = reader.page(photo_bytes(25), invoice_words("102595", lines=[("Honey 5L", 1, 50.00)]))
    assert phone.upload(batch["batch_id"], "retake-1", same_invoice,
                        replaces=second["pages"][0]["photo_id"]).status_code == 201
    reader.run()
    assert [i["title"] for i in phone.batch(batch["batch_id"])["invoices"]] == ["INV 102595 (pages 1, 2)"]
    r = phone.get(f"/api/v1/invoices/{second['invoice_id']}")
    assert r.status_code == 409 and r.get_json()["error"] == "invoice_regrouped"


def test_the_stored_photograph_is_served_whole_or_downscaled(phone):
    batch_id = phone.new_batch()
    data = photo_bytes(9)
    photo_id = phone.upload(batch_id, "photo-0001", data, ordinal=1).get_json()["photo_id"]
    assert phone.get(f"/api/v1/photos/{photo_id}/image").data == data
    small = phone.get(f"/api/v1/photos/{photo_id}/image?w=300")
    assert small.status_code == 200 and Image.open(io.BytesIO(small.data)).size == (300, 400)
    assert phone.get(f"/api/v1/photos/{photo_id}/image?w=lots").status_code == 400
