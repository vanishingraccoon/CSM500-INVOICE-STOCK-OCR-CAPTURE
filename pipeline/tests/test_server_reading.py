"""Laptop server, reading side: the queue with leases and retries, grouping pages into invoices, and the tabs
(docs/phone-api.md sections 3, 4 and 6). A fake engine stands in for PaddleOCR; figures are substituted."""
import time

import numpy as np
import pytest
from serverkit import OWNER, invoice_words, photo_bytes, sha

from app import context, db, quality, worker


def _tabs(batch):
    return [(inv["tab"], [p["ordinal"] for p in inv["pages"]]) for inv in batch["invoices"]]


# --- the queue ------------------------------------------------------------------------------------------

def test_a_claimed_job_is_leased_and_goes_back_in_the_queue_when_its_lease_expires(phone, monkeypatch):
    batch_id = phone.new_batch()
    phone.upload(batch_id, "photo-0001", photo_bytes(1), ordinal=1)
    conn = context.connection()
    job = worker.claim(conn, OWNER, lease=30)
    assert job is not None and job.attempts == 1
    assert db.one(conn, "SELECT state FROM photos WHERE photo_id = ?", job.photo_id)["state"] == "reading"
    assert worker.claim(conn, "run1:test:2:r1") is None
    later = time.time() + 31
    monkeypatch.setattr(db, "now", lambda: later)
    again = worker.claim(conn, "run1:test:2:r1")
    assert again.job_id == job.job_id and again.attempts == 2
    assert db.one(conn, "SELECT COUNT(*) AS n FROM events WHERE kind = 'lease_expired'")["n"] == 1
    assert worker.record_failure(conn, job, OWNER, "too late") == "lost"      # the first reader no longer holds it


def test_a_failing_photograph_is_retried_after_a_pause_then_failed_into_retake(phone, reader, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr(db, "now", lambda: clock[0])
    data = photo_bytes(2)
    reader.engine.fail.add(sha(data))
    batch_id = phone.new_batch()
    phone.upload(batch_id, "photo-0001", data, ordinal=1)
    assert reader.run() == ["retry"]
    assert reader.run() == []                         # still waiting out its pause
    clock[0] += worker.BACKOFF_SECONDS[0] + 1
    assert reader.run() == ["retry"]
    clock[0] += worker.BACKOFF_SECONDS[1] + 1
    assert reader.run() == ["failed"]
    batch = phone.batch(batch_id)
    [invoice] = batch["invoices"]
    assert invoice["tab"] == "retake" and invoice["tab_reasons"][0].startswith("page 1: recognition failed")
    assert batch["progress"]["failed"] == 1


def test_startup_puts_back_jobs_held_by_readers_of_an_earlier_run(phone):
    batch_id = phone.new_batch()
    phone.upload(batch_id, "photo-0001", photo_bytes(3), ordinal=1)
    conn = context.connection()
    worker.claim(conn, "oldrun:laptop:99:r0", lease=600)
    assert worker.requeue_stale(conn, run_id="newrun") == 1
    assert db.one(conn, "SELECT status FROM jobs")["status"] == "queued"


@pytest.mark.parametrize("recovery", ["lease", "startup"])
def test_exhausted_crash_recovery_rebuilds_retake_without_another_job(phone, monkeypatch, recovery):
    batch_id = phone.new_batch(pages=1)
    phone.upload(batch_id, "crashing-photo", photo_bytes(93), ordinal=1)
    phone.post(f"/api/v1/batches/{batch_id}/complete")
    conn = context.connection()
    clock = [time.time()]
    monkeypatch.setattr(db, "now", lambda: clock[0])
    for attempt in range(worker.MAX_ATTEMPTS):
        assert worker.claim(conn, OWNER, lease=1).attempts == attempt + 1
        clock[0] += 2
    if recovery == "startup":
        assert worker.requeue_stale(conn, run_id="newrun") == 1
    assert worker.claim(conn, OWNER) is None
    batch = phone.batch(batch_id)
    assert batch["progress"]["failed"] == 1
    assert [inv["tab"] for inv in batch["invoices"]] == ["retake"]
    assert worker.requeue_stale(conn) == 0


def test_a_photograph_replaced_before_it_was_read_is_never_read(phone, reader):
    batch_id = phone.new_batch()
    old = phone.upload(batch_id, "photo-0001", photo_bytes(4), ordinal=1).get_json()["photo_id"]
    reader.page(photo_bytes(5), invoice_words())
    phone.upload(batch_id, "photo-0001-retake", photo_bytes(5), replaces=old)
    assert reader.run() == ["read"] and reader.engine.calls == 1


# --- grouping pages into invoices ---------------------------------------------------------------------------

def test_pages_with_the_same_supplier_and_number_are_one_invoice_and_a_new_number_starts_another(phone, reader):
    batch = phone.scan(reader, [
        (photo_bytes(10), invoice_words("102595", lines=[("Tequila Mix (750ml)", 2, 100.00)], show_totals=False)),
        (photo_bytes(11), invoice_words("I02595", lines=[("Honey 5L", 1, 50.00)], totals=(250.00, 37.50))),
        (photo_bytes(12), invoice_words("102600", lines=[("Honey 5L", 2, 50.00)])),
    ])
    assert _tabs(batch) == [("good", [1, 2]), ("good", [3])]
    first = batch["invoices"][0]
    assert [l["index"] for l in first["lines"]] == [0, 1] and first["adds_up"] is True
    assert first["title"] == "INV 102595 (pages 1, 2)" and first["lines_sum_excl"] == 250.00


def test_a_page_without_a_number_joins_only_when_its_printed_page_numbering_continues(phone, reader):
    batch = phone.scan(reader, [
        (photo_bytes(20), invoice_words("102595", page_of=(1, 2), show_totals=False)),
        (photo_bytes(21), invoice_words(None, page_of=(2, 2), lines=[("Honey 5L", 1, 50.00)], totals=(250.00, 37.50))),
        (photo_bytes(22), invoice_words(None, lines=[("Honey 5L", 1, 50.00)])),
    ])
    assert [[p["ordinal"] for p in i["pages"]] for i in batch["invoices"]] == [[1, 2], [3]]
    assert "no invoice number" in batch["invoices"][1]["tab_reasons"]


def test_pages_read_out_of_order_still_group_by_scanner_position(phone, reader):
    batch_id = phone.new_batch()
    second, first = photo_bytes(31), photo_bytes(30)
    reader.page(first, invoice_words("102595", show_totals=False))
    reader.page(second, invoice_words("102595", lines=[("Honey 5L", 1, 50.00)], totals=(250.00, 37.50)))
    phone.upload(batch_id, "p2", second, ordinal=2)
    assert reader.run() == ["read"]
    phone.upload(batch_id, "p1", first, ordinal=1)
    reader.run()
    batch = phone.batch(batch_id)
    assert _tabs(batch) == [("good", [1, 2])]
    assert [l["printed"]["description"] for l in batch["invoices"][0]["lines"]] == ["Tequila Mix (750ml)", "Honey 5L"]


# --- tabs ----------------------------------------------------------------------------------------------------

def test_an_invoice_read_clearly_is_good_with_every_line_converted(phone, reader):
    batch = phone.scan(reader, [(photo_bytes(40), invoice_words())])
    [invoice] = batch["invoices"]
    assert invoice["tab"] == "good" and invoice["tab_reasons"] == []
    [line] = invoice["lines"]
    assert line["item"] == {"code": "5001", "name": "tequila mix (750ML)", "base_unit": "each"}
    assert line["base_quantity"] == 2.0 and line["star"] is False and line["entered_by"] == "system"
    assert batch["counts"] == {"good": 1, "corrections": 0, "retake": 0, "duplicates": 0} and batch["status"] == "ready"


def test_an_unmatched_line_is_starred_and_named_in_the_corrections_reasons(phone, reader):
    words = invoice_words(lines=[("Tequila Mix (750ml)", 2, 100.00), ("Mystery Widget 5L", 1, 10.00)])
    [invoice] = phone.scan(reader, [(photo_bytes(41), words)])["invoices"]
    assert invoice["tab"] == "corrections"
    assert invoice["tab_reasons"] == ["line 2: no stock item matched"]
    assert [l["star"] for l in invoice["lines"]] == [False, True] and invoice["lines"][1]["refusal"] == "no_item_match"


def test_missing_supplier_number_and_date_are_each_a_reason(phone, reader):
    words = invoice_words(None, None, supplier=("Unheard", "Of", "Traders"))
    [invoice] = phone.scan(reader, [(photo_bytes(42), words)])["invoices"]
    assert invoice["tab"] == "corrections" and invoice["supplier"] is None
    assert {"supplier not recognised", "no invoice number", "no date"} <= set(invoice["tab_reasons"])


def test_lines_that_do_not_add_up_to_the_stated_total_are_a_correction(phone, reader):
    [invoice] = phone.scan(reader, [(photo_bytes(43), invoice_words(totals=(300.00, 45.00)))])["invoices"]
    assert invoice["tab"] == "corrections" and invoice["adds_up"] is False
    assert invoice["tab_reasons"] == ["does not add up: the lines sum to R200.00 but the invoice states R300.00 excl. VAT"]


def test_a_blurred_photograph_is_a_retake_even_when_words_were_read(phone, reader):
    words = invoice_words(lines=[("Mystery Widget 5L", 1, 10.00)])
    [invoice] = phone.scan(reader, [(photo_bytes(44, blurred=True), words)])["invoices"]
    assert invoice["tab"] == "retake"                  # retake outranks the unmatched line's correction
    assert any(r.startswith("page 1: photograph looks blurred") for r in invoice["tab_reasons"])
    assert invoice["pages"][0]["quality"]["ok"] is False


def test_too_few_words_or_no_line_items_is_a_retake(phone, reader):
    few = invoice_words()[:8]
    batch = phone.scan(reader, [(photo_bytes(45), few)])
    [invoice] = batch["invoices"]
    assert invoice["tab"] == "retake"
    assert "page 1: only 8 words recognised (needs 25)" in invoice["tab_reasons"]
    assert "no line items found" in invoice["tab_reasons"]


def test_the_phones_own_quality_check_can_send_a_page_to_retake(phone, reader):
    batch_id = phone.new_batch()
    reader.page(photo_bytes(46), invoice_words())
    phone.upload(batch_id, "p1", photo_bytes(46), ordinal=1, quality={"sharpness": 12.5, "mean_luma": 150})
    reader.run()
    [invoice] = phone.batch(batch_id)["invoices"]
    assert invoice["tab"] == "retake"
    assert invoice["tab_reasons"] == ["page 1: photograph looks blurred (sharpness 12, needs 60) on the phone's check"]


def test_a_repeated_invoice_number_further_on_is_a_duplicate_and_outranks_retake(phone, reader):
    batch = phone.scan(reader, [
        (photo_bytes(50), invoice_words("102595")),
        (photo_bytes(51), invoice_words("102600")),
        (photo_bytes(52, blurred=True), invoice_words("102595")),
    ])
    assert [i["tab"] for i in batch["invoices"]] == ["good", "good", "duplicates"]
    original, _, repeat = batch["invoices"]
    assert repeat["duplicate_of"] == original["invoice_id"]
    assert repeat["tab_reasons"] == ["the same invoice as INV 102595 (page 1) in this batch: same supplier, number and total"]


def test_the_same_photograph_uploaded_twice_is_a_duplicate(phone, reader):
    same = photo_bytes(60)
    batch = phone.scan(reader, [(same, invoice_words("102595")), (photo_bytes(61), invoice_words("102600")),
                                (same, None)])
    assert [i["tab"] for i in batch["invoices"]] == ["good", "good", "duplicates"]
    assert batch["invoices"][2]["tab_reasons"] == ["page 3 is the same photograph as page 1 in this batch"]


# --- quality measures -----------------------------------------------------------------------------------------

def test_quality_measures_do_not_depend_on_the_photographs_resolution(tmp_path):
    import cv2
    small, large = tmp_path / "small.png", tmp_path / "large.png"
    rng = np.random.default_rng(7)
    page = np.full((1600, 1200), 205, np.uint8)
    for _ in range(400):
        x, y = int(rng.integers(0, 1140)), int(rng.integers(0, 1590))
        page[y:y + 10, x:x + 40] = 25
    cv2.imwrite(str(small), page)
    cv2.imwrite(str(large), cv2.resize(page, (2400, 3200), interpolation=cv2.INTER_NEAREST))
    a, b = quality.measure(small), quality.measure(large)
    assert 0.7 < a["sharpness"] / b["sharpness"] < 1.3
    assert abs(a["mean_luma"] - b["mean_luma"]) < 3 and abs(a["contrast_spread"] - b["contrast_spread"]) < 5


def test_glare_counts_blown_out_blocks_not_scattered_bright_pixels():
    gray = np.full((800, 800), 180, np.uint8)
    gray[::7, ::7] = 255                      # scattered: about 2% of each block
    assert quality.glare_fraction(gray) == 0.0
    gray[0:160, 0:160] = 255                  # a blown-out patch: 4% of the page
    assert 0.035 < quality.glare_fraction(gray) < 0.045
    assert "glare" in quality.problems({"glare_fraction": quality.glare_fraction(gray)})


def test_a_reused_number_with_another_total_is_a_duplicate_but_identical_lines_under_a_new_number_only_a_note():
    from collections import defaultdict

    from app.reading import _record_duplicates
    from checks.duplicates import InvoiceSummary, find_duplicates
    lines = (("bread", 2.0),)
    first = InvoiceSummary("a", "vassco", "1001", 100.0, "2025-08-01", "scan", lines)
    hard, soft = {}, defaultdict(list)
    reused = InvoiceSummary("b", "vassco", "1001", 250.0, "2025-08-01", "scan")
    _record_duplicates(find_duplicates([first, reused]), "b", "a", "INV 1001 in this batch", 0, hard, soft)
    assert hard["b"].other == "a" and "the totals differ" in hard["b"].reason
    renumbered = InvoiceSummary("c", "vassco", "2002", 100.0, "2025-08-03", "scan", lines)
    _record_duplicates(find_duplicates([first, renumbered]), "c", "a", "INV 1001 in this batch", 2, hard, soft)
    assert "c" not in hard
    assert soft["c"] == ["identical lines and total to INV 1001 in this batch under a different number, 2 days apart"]
