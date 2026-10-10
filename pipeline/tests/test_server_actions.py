"""Laptop server, manager side: line edits through the key, typed lines, header fixes, duplicates, retakes, and
submitting with alias learning (docs/phone-api.md sections 5 and 7). Figures and items are substituted."""
import json

from serverkit import SHOP, invoice_words, photo_bytes

from app import context, db

MYSTERY = [("Tequila Mix (750ml)", 2, 100.00), ("Mystery Widget 5L", 2, 10.00)]


def _scan_one(phone, reader, words, seed=100, key="batch-1"):
    batch = phone.scan(reader, [(photo_bytes(seed), words)], key=key)
    return batch, batch["invoices"][0]


def _conn():
    return context.connection()


# --- lines -----------------------------------------------------------------------------------------------

def test_a_manager_edit_converts_the_printed_quantity_through_the_key_and_clears_the_flags(phone, reader):
    _, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY))
    assert invoice["tab"] == "corrections"
    r = phone.put(f"/api/v1/invoices/{invoice['invoice_id']}/lines/1",
                  json={"item_code": "1189", "quantity": 2, "unit": None, "cost": 20.00})
    assert r.status_code == 200
    edited = r.get_json()
    line = edited["lines"][1]
    assert line["entered_by"] == "manager" and line["item"]["name"] == "honey (LITRE)"
    assert line["base_quantity"] == 10.0 and line["refusal"] is None and line["flags"] == [] and line["star"] is False
    assert line["printed"]["description"] == "Mystery Widget 5L" and line["vat"] == 3.00
    assert edited["tab"] == "good" and edited["tab_reasons"] == []
    detail = json.loads(db.one(_conn(), "SELECT detail FROM events WHERE kind = 'line_edited'")["detail"])
    assert detail["before"]["refusal"] == "no_item_match" and detail["after"]["item"]["code"] == "1189"
    assert db.one(_conn(), "SELECT COUNT(*) AS n FROM events WHERE kind = 'tab_changed' AND invoice_id = ?",
                  invoice["invoice_id"])["n"] == 2


def test_an_item_the_key_cannot_convert_keeps_the_star_and_says_why(phone, reader):
    _, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY))
    edited = phone.put(f"/api/v1/invoices/{invoice['invoice_id']}/lines/1",
                       json={"item_code": "3001", "quantity": 2, "cost": 20.00}).get_json()
    line = edited["lines"][1]
    assert line["refusal"] == "unit_conflict" and line["base_quantity"] is None and line["star"] is True
    assert "density" in line["reason"]
    assert edited["tab_reasons"] == ["line 2: the invoice's unit does not fit the stock item's unit"]


def test_a_free_text_line_takes_its_quantity_in_the_base_unit(phone, reader):
    _, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY))
    edited = phone.put(f"/api/v1/invoices/{invoice['invoice_id']}/lines/1",
                       json={"free_text": "widget tub, 5 litre", "quantity": 10, "cost": 20.00}).get_json()
    line = edited["lines"][1]
    assert line["item"] is None and line["free_text"] == "widget tub, 5 litre"
    assert line["base_quantity"] == 10.0 and line["star"] is False and line["entered_by"] == "manager"
    assert edited["tab"] == "good"


def test_a_typed_line_can_be_added_and_a_line_the_system_invented_removed(phone, reader):
    _, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY))
    base = f"/api/v1/invoices/{invoice['invoice_id']}/lines"
    added = phone.post(base, json={"item_code": "4001", "quantity": 1, "unit": "Litre", "cost": 5.00})
    assert added.status_code == 201
    new = added.get_json()["lines"][-1]
    assert new["index"] == 2 and new["printed"] is None and new["base_quantity"] == 1.0 and new["entered_by"] == "manager"
    removed = phone.delete(f"{base}/1")
    assert removed.status_code == 200
    lines = removed.get_json()["lines"]
    assert [l["index"] for l in lines] == [0, 2]
    assert "Mystery Widget 5L" not in [l["printed"]["description"] for l in lines if l["printed"]]
    assert phone.delete(f"{base}/1").status_code == 404


def test_line_entries_are_checked_before_anything_changes(phone, reader):
    _, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY))
    url = f"/api/v1/invoices/{invoice['invoice_id']}/lines/1"
    assert phone.put(url, json={"item_code": "1189", "quantity": "2", "cost": 1}).status_code == 400
    assert phone.put(url, json={"item_code": "1189", "free_text": "honey", "quantity": 2, "cost": 1}).status_code == 400
    nan = phone.client.put(url, data='{"item_code": "1189", "quantity": NaN, "cost": 1}',
                           headers={**phone.headers, "Content-Type": "application/json"})
    assert nan.status_code == 400 and nan.get_json()["error"] == "invalid_body"
    unknown = phone.put(url, json={"item_code": "no-such-item", "quantity": 2, "cost": 1})
    assert unknown.status_code == 422 and unknown.get_json()["error"] == "unknown_item"
    assert phone.put(f"/api/v1/invoices/{invoice['invoice_id']}/lines/9",
                     json={"item_code": "1189", "quantity": 2, "cost": 1}).status_code == 404
    assert db.one(_conn(), "SELECT COUNT(*) AS n FROM events WHERE kind = 'line_edited'")["n"] == 0


def test_stock_search_finds_items_in_this_shops_key(phone):
    items = phone.get("/api/v1/stock/search?q=honey&limit=5").get_json()["items"]
    assert items[0] == {"code": "1189", "name": "honey (LITRE)", "base_unit": "l", "category": "dg - dry goods"}
    assert phone.get("/api/v1/stock/search?q=").get_json() == {"items": []}


# --- the invoice ------------------------------------------------------------------------------------------

def test_fixing_the_supplier_reads_the_invoice_again_and_keeps_the_managers_lines(phone, reader):
    _, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY, supplier=("Unheard", "Of", "Traders")))
    url = f"/api/v1/invoices/{invoice['invoice_id']}"
    assert "supplier not recognised" in invoice["tab_reasons"]
    phone.put(url + "/lines/1", json={"item_code": "1189", "quantity": 2, "cost": 20.00})
    patched = phone.patch(url, json={"supplier": "aero_power"})
    assert patched.status_code == 200
    body = patched.get_json()
    assert body["supplier"] == "aero_power" and body["supplier_name"] == "Aero Power Trading"
    assert body["lines"][1]["entered_by"] == "manager" and body["lines"][1]["item"]["code"] == "1189"
    assert body["tab"] == "good"
    assert db.one(_conn(), "SELECT layout FROM invoices WHERE invoice_id = ?", invoice["invoice_id"])["layout"] == "aero_power"
    assert phone.patch(url, json={"date": "31/08/2026"}).status_code == 400
    assert phone.patch(url, json={"supplier": "nobody"}).get_json()["error"] == "unknown_supplier"


def test_a_deleted_duplicate_leaves_the_tabs_and_a_kept_one_is_decided_afresh(phone, reader):
    batch = phone.scan(reader, [(photo_bytes(50), invoice_words("102595")), (photo_bytes(51), invoice_words("102600")),
                                (photo_bytes(52), invoice_words("102595")), (photo_bytes(53), invoice_words("102600"))])
    assert [i["tab"] for i in batch["invoices"]] == ["good", "good", "duplicates", "duplicates"]
    third, fourth = batch["invoices"][2], batch["invoices"][3]
    deleted = phone.post(f"/api/v1/invoices/{third['invoice_id']}/delete").get_json()
    assert deleted["tab"] == "deleted"
    kept = phone.post(f"/api/v1/invoices/{fourth['invoice_id']}/not_duplicate").get_json()
    assert kept["tab"] == "good" and kept["duplicate_of"] is None
    after = phone.batch(batch["batch_id"])
    assert after["counts"] == {"good": 3, "corrections": 0, "retake": 0, "duplicates": 0}
    assert [i["tab"] for i in after["invoices"]] == ["good", "good", "deleted", "good"]
    assert {e["kind"] for e in db.all_rows(_conn(), "SELECT kind FROM events")} >= {"duplicate_deleted", "not_duplicate"}


# --- submit, learning, retakes -------------------------------------------------------------------------------

def test_submit_waits_until_every_photograph_is_read_and_is_safe_to_retry(phone, reader):
    batch_id = phone.new_batch()
    phone.upload(batch_id, "p1", photo_bytes(70), ordinal=1)
    refused = phone.post(f"/api/v1/batches/{batch_id}/submit")
    assert refused.status_code == 409 and refused.get_json()["error"] == "still_reading"
    reader.page(photo_bytes(70), invoice_words())
    reader.run()
    done = phone.post(f"/api/v1/batches/{batch_id}/submit")
    assert done.status_code == 200
    assert done.get_json() == {"status": "submitted", "report": {"pdf": f"/portal/batches/{batch_id}/report.pdf",
                                                                 "csv": f"/portal/batches/{batch_id}/report.csv"}}
    assert phone.post(f"/api/v1/batches/{batch_id}/submit").get_json() == done.get_json()
    assert phone.batch(batch_id)["status"] == "submitted"
    assert db.one(_conn(), "SELECT COUNT(*) AS n FROM reports")["n"] == 1


def test_submitting_teaches_the_key_and_the_next_batch_matches_that_wording(phone, reader):
    batch, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY), key="week-1")
    phone.put(f"/api/v1/invoices/{invoice['invoice_id']}/lines/1", json={"item_code": "1189", "quantity": 2, "cost": 20.00})
    phone.post(f"/api/v1/batches/{batch['batch_id']}/submit")
    learned = db.all_rows(_conn(), "SELECT supplier, normalised, item_code FROM learned_aliases ORDER BY item_code")
    assert learned == [{"supplier": "aero_power", "normalised": "mystery widget", "item_code": "1189"},
                       {"supplier": "aero_power", "normalised": "tequila mix", "item_code": "5001"}]
    _, later = _scan_one(phone, reader, invoice_words("102700", lines=[("Mystery Widget 5L", 3, 10.00)]), seed=101,
                         key="week-2")
    [line] = later["lines"]
    assert line["item"]["code"] == "1189" and line["base_quantity"] == 15.0 and "known wording" in line["reason"]


def test_wording_with_nothing_left_after_normalising_is_never_learned(phone, reader):
    batch, invoice = _scan_one(phone, reader, invoice_words(lines=[("2 x 5L", 1, 10.00)]))
    phone.put(f"/api/v1/invoices/{invoice['invoice_id']}/lines/0", json={"item_code": "1189", "quantity": 1, "cost": 10.00})
    phone.post(f"/api/v1/batches/{batch['batch_id']}/submit")
    assert db.one(_conn(), "SELECT COUNT(*) AS n FROM learned_aliases")["n"] == 0


def test_a_removed_alias_is_neither_used_nor_learned_again(api_client, phone, reader):
    batch, invoice = _scan_one(phone, reader, invoice_words(lines=MYSTERY), key="week-1")
    phone.put(f"/api/v1/invoices/{invoice['invoice_id']}/lines/1", json={"item_code": "1189", "quantity": 2, "cost": 20.00})
    phone.post(f"/api/v1/batches/{batch['batch_id']}/submit")
    api_client.post(f"/key/{SHOP}/aliases/remove",
                    data={"supplier": "aero_power", "normalised": "mystery widget", "item_code": "1189"})
    again, repeat = _scan_one(phone, reader, invoice_words("102700", lines=MYSTERY), seed=101, key="week-2")
    assert repeat["lines"][1]["refusal"] == "no_item_match"
    phone.put(f"/api/v1/invoices/{repeat['invoice_id']}/lines/1", json={"item_code": "1189", "quantity": 2, "cost": 20.00})
    phone.post(f"/api/v1/batches/{again['batch_id']}/submit")
    rows = db.all_rows(_conn(), "SELECT removed_at FROM learned_aliases WHERE normalised = 'mystery widget'")
    assert len(rows) == 1 and rows[0]["removed_at"] is not None


def test_after_submit_only_retakes_are_taken_and_a_retaken_invoice_joins_the_next_report(phone, reader):
    batch = phone.scan(reader, [(photo_bytes(89), invoice_words("102595")),
                                (photo_bytes(90, blurred=True), invoice_words("102600"))], key="week-1")
    assert [i["tab"] for i in batch["invoices"]] == ["good", "retake"]
    batch_id = batch["batch_id"]
    phone.post(f"/api/v1/batches/{batch_id}/submit")
    late = phone.upload(batch_id, "late-page", photo_bytes(91), ordinal=3)
    assert late.status_code == 423 and late.get_json()["error"] == "batch_submitted"

    old_photo = batch["invoices"][1]["pages"][0]["photo_id"]
    sharp = reader.page(photo_bytes(92), invoice_words("102600"))
    retake = phone.upload(batch_id, "retake-1", sharp, replaces=old_photo)
    assert retake.status_code == 201
    reader.run()
    after = phone.batch(batch_id)
    retaken = after["invoices"][1]
    assert after["status"] == "submitted" and retaken["tab"] == "good"
    assert retaken["pages"][0]["photo_id"] == retake.get_json()["photo_id"] and retaken["pages"][0]["ordinal"] == 2
    assert db.one(_conn(), "SELECT state, replaced_by FROM photos WHERE photo_id = ?", old_photo) == \
        {"state": "replaced", "replaced_by": retake.get_json()["photo_id"]}

    following = phone.scan(reader, [(photo_bytes(93), invoice_words("102700"))], key="week-2")
    phone.post(f"/api/v1/batches/{following['batch_id']}/submit")
    snapshot = json.loads(db.one(_conn(), "SELECT snapshot FROM reports WHERE batch_id = ?",
                                 following["batch_id"])["snapshot"])
    assert [(i["title"], i["carried"]) for i in snapshot["invoices"]] == [("INV 102600 (page 2)", True),
                                                                          ("INV 102700 (page 1)", False)]
    assert db.one(_conn(), "SELECT needs_report FROM invoices WHERE invoice_id = ?", retaken["invoice_id"])["needs_report"] == 0
