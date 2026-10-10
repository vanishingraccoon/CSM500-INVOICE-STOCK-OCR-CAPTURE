"""End to end, the way the laptop runs it: startup recovery, a reader in its own process (started by the WorkerPool
app.serve uses, with a fake engine in place of PaddleOCR), and a phone going from pairing through a batch, a
correction and submit to the downloaded input report (docs/phone-api.md). Figures are substituted."""
import csv
import io
import json
import time

import pymupdf
from serverkit import FILE_ENGINE_ENV, Phone, invoice_words, photo_bytes, write_words

from app import context, db, serve, settings, worker

MYSTERY = [("Tequila Mix (750ml)", 2, 100.00), ("Mystery Widget 5L", 2, 10.00)]


def _ready(phone, batch_id: str, pool, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        batch = phone.batch(batch_id)
        if batch["status"] == "ready":
            return batch
        assert all(p.exitcode is None for p in pool.procs), "a reader process stopped"
        assert time.monotonic() < deadline, f"the reader process did not finish: {batch['progress']}"
        time.sleep(0.25)


def test_a_batch_goes_from_pairing_to_the_downloaded_report_through_a_reader_process(shop, tmp_path, monkeypatch):
    words = tmp_path / "words"
    monkeypatch.setenv(FILE_ENGINE_ENV, str(words))
    s = shop.with_(ocr_engine="serverkit:FileEngine", workers=1)
    monkeypatch.setattr(settings, "_current", s)
    from app.server import app
    app.config["TESTING"] = True
    client = app.test_client()
    phone = Phone(client)

    batch_id = phone.new_batch("session-1", 2)
    first = write_words(words, photo_bytes(1), invoice_words("102595"))
    assert phone.upload(batch_id, "page-1", first, ordinal=1).status_code == 201
    # the last run crashed: its reader still held the first page, and an upload was cut off half-way
    assert worker.claim(context.connection(), "oldrun:laptop:99:r0", lease=600) is not None
    (s.tmp / "upload_cut_off.part").write_bytes(b"half a photograph")
    assert serve.recover(s, "newrun") == {"requeued": 1, "temp_files_removed": 1}
    second = write_words(words, photo_bytes(2), invoice_words("102600", lines=MYSTERY))
    assert phone.upload(batch_id, "page-2", second, ordinal=2).status_code == 201
    assert phone.post(f"/api/v1/batches/{batch_id}/complete").get_json() == {"status": "processing"}

    pool = worker.WorkerPool(s, s.workers, "newrun")
    pool.start()
    try:
        batch = _ready(phone, batch_id, pool)
    finally:
        pool.shutdown(timeout=60)
    assert not any(p.is_alive() for p in pool.procs)
    assert batch["progress"] == {"photos": 2, "waiting": 0, "reading": 0, "read": 2, "failed": 0}
    assert [(i["title"], i["tab"]) for i in batch["invoices"]] == [("INV 102595 (page 1)", "good"),
                                                                   ("INV 102600 (page 2)", "corrections")]
    assert batch["invoices"][1]["tab_reasons"] == ["line 2: no stock item matched"]

    fixed = phone.put(f"/api/v1/invoices/{batch['invoices'][1]['invoice_id']}/lines/1",
                      json={"item_code": "1189", "quantity": 2, "unit": None, "cost": 20.00}).get_json()
    assert fixed["tab"] == "good" and fixed["lines"][1]["base_quantity"] == 10.0
    assert phone.batch(batch_id)["counts"] == {"good": 2, "corrections": 0, "retake": 0, "duplicates": 0}

    submitted = phone.post(f"/api/v1/batches/{batch_id}/submit")
    assert submitted.status_code == 200
    links = submitted.get_json()["report"]
    pdf = client.get(links["pdf"])
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf"
    text = "".join(page.get_text() for page in pymupdf.open("pdf", pdf.data))
    assert "INV 102595 (page 1)" in text and "INV 102600 (page 2)" in text and "honey (LITRE)" in text
    rows = list(csv.DictReader(io.StringIO(client.get(links["csv"]).data.decode("utf-8-sig"))))
    assert [(r["invoice"], r["item"], r["quantity_base"], r["cost_excl_vat"], r["typed_by_manager"]) for r in rows] == [
        ("102595", "tequila mix (750ML)", "2", "200.00", "no"),
        ("102600", "tequila mix (750ML)", "2", "200.00", "no"),
        ("102600", "honey (LITRE)", "10", "20.00", "yes")]

    conn = context.connection()
    owners = [json.loads(e["detail"])["owner"] for e in
              db.all_rows(conn, "SELECT detail FROM events WHERE kind = 'job_claimed' ORDER BY event_id")]
    assert len(owners) == 3 and owners[0].startswith("oldrun:") and all(o.startswith("newrun:") for o in owners[1:])
    kinds = {e["kind"] for e in db.all_rows(conn, "SELECT kind FROM events")}
    assert {"server_started", "lease_expired", "page_read", "tab_changed", "line_edited", "batch_submitted",
            "aliases_learned", "report_generated"} <= kinds
    assert list((s.ocr_cache / "fakefile").glob("*.json"))      # recognised words stay in the server's own folder
