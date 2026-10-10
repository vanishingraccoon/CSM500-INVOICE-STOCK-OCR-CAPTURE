"""Review page: listing, showing refusals and flags, recording corrections, exporting rows."""
import json

import pytest

import config
from models import InvoiceRecord, Line
from normaliser.stock import StockFile, StockItem
from normaliser.units import parse_base_unit, split_item_name


@pytest.fixture
def client(tmp_path, monkeypatch):
    out, pages = tmp_path / "out", tmp_path / "pages"
    out.mkdir()
    pages.mkdir()
    monkeypatch.setattr(config, "OUT", out)
    monkeypatch.setattr(config, "PAGES", pages)
    from app import data, server
    monkeypatch.setattr(data, "REVIEW", out / "review")
    monkeypatch.setattr(data, "DECISIONS", out / "review" / "decisions")
    monkeypatch.setattr(data, "CORRECTIONS", out / "review" / "corrections.jsonl")
    monkeypatch.setattr(data, "UPLOADS", out / "uploads")
    desc, suffix = split_item_name("honey (LITRE)")
    monkeypatch.setattr(server, "_stock", StockFile("t", None, [
        StockItem("1189", "honey (LITRE)", desc, "dg - dry goods", parse_base_unit(suffix), None, None)]))

    # substituted figures
    rec = InvoiceRecord("inv1", "vassco", "generic", "123", "2026-08-01", [1],
                        [Line("Honey 5L", 2, None, "5L", 100.0, 200.0, 30.0), Line("Mystery", 1, None, None, 5.0, 5.0, 0.75)],
                        200.0, None, None, 205.0, False, [])
    (out / "invoices.json").write_text(json.dumps([rec.to_dict()]), encoding="utf-8")
    (out / "normalised.json").write_text(json.dumps([
        {"invoice_id": "inv1", "line_index": 0, "item_code": "1189", "item_name": "honey (LITRE)", "base_quantity": 10.0,
         "refusal": None, "reason": "matched", "line": rec.lines[0].to_dict()},
        {"invoice_id": "inv1", "line_index": 1, "item_code": None, "base_quantity": None,
         "refusal": "no_item_match", "reason": "nothing resembles it", "line": rec.lines[1].to_dict()},
    ]), encoding="utf-8")
    (out / "flags.json").write_text(json.dumps([
        {"kind": "total_mismatch", "invoice_id": "inv1", "line_index": None, "severity": "medium",
         "confidence": 0.9, "reason": "lines do not add up", "evidence": {}}]), encoding="utf-8")
    server.app.config["TESTING"] = True
    return server.app.test_client(), out


def test_index_lists_invoices(client):
    c, _ = client
    html = c.get("/binder").get_data(as_text=True)
    assert "vassco" in html and "to review" in html


def test_invoice_page_shows_lines_refusals_and_flags(client):
    c, _ = client
    html = c.get("/binder/invoice/inv1").get_data(as_text=True)
    assert "Honey 5L" in html and "no item match" in html and "total mismatch" in html


def test_unknown_invoice_is_404(client):
    c, _ = client
    assert c.get("/binder/invoice/nope").status_code == 404


def test_review_records_what_changed_and_exports_only_valid_rows(client):
    c, out = client
    r = c.post("/binder/invoice/inv1", data={
        "opened": "0",
        "item_0": "1189 honey (LITRE)", "qty_0": "10", "cost_0": "200.00", "action_0": "accept",
        "item_1": "", "qty_1": "", "cost_1": "5.00", "action_1": "skip",
    })
    assert r.status_code == 302
    log = [json.loads(line) for line in (out / "review" / "corrections.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["changed"] for x in log] == [[], ["skipped"]]
    assert log[1]["was_refused"] is True
    csv_text = c.get("/binder/invoice/inv1/export.csv").get_data(as_text=True)
    assert "vassco,123,2026-08-01,1189,honey (LITRE),10,l,200.00,30.00" in csv_text
    assert "Mystery" not in csv_text


def test_a_correction_is_recorded_as_a_change(client):
    c, out = client
    c.post("/binder/invoice/inv1", data={"opened": "0", "item_0": "1189 honey (LITRE)", "qty_0": "12", "cost_0": "200.00",
                                  "action_0": "correct", "action_1": "skip"})
    first = json.loads((out / "review" / "corrections.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["changed"] == ["base_quantity"]


def test_phone_stylesheets_are_served_locally(client):
    c, _ = client
    assert c.get("/static/app.css").status_code == 200
    assert c.get("/static/industry.css").status_code == 200
    assert "fonts.googleapis" not in c.get("/binder").get_data(as_text=True)


def test_refused_line_is_guarded_on_the_review_page(client):
    c, _ = client
    html = c.get("/binder/invoice/inv1").get_data(as_text=True)
    assert 'data-line="1"' in html and 'data-guard="1"' in html
    assert 'name="checked_1"' in html and 'name="action_1"' in html
    assert 'name="checked_0"' not in html


def test_list_links_to_adjudication(client):
    c, _ = client
    assert "/adjudicate" in c.get("/binder").get_data(as_text=True)


def test_photo_tick_is_recorded(client):
    c, out = client
    c.post("/binder/invoice/inv1", data={"opened": "0", "item_0": "1189 honey (LITRE)", "qty_0": "10", "cost_0": "200.00",
                                  "action_0": "accept", "action_1": "correct", "checked_1": "1"})
    log = [json.loads(line) for line in (out / "review" / "corrections.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["final"]["photo_checked"] is False and log[1]["final"]["photo_checked"] is True


def test_reopened_invoice_keeps_the_photo_tick(client):
    c, _ = client
    c.post("/binder/invoice/inv1", data={"opened": "0", "item_0": "1189 honey (LITRE)", "qty_0": "10", "cost_0": "200.00",
                                  "action_0": "accept", "item_1": "1189 honey (LITRE)", "qty_1": "1", "cost_1": "5.00",
                                  "action_1": "accept", "checked_1": "1"})
    html = c.get("/binder/invoice/inv1").get_data(as_text=True)
    assert 'name="checked_1" value="1" checked' in html


def test_accepted_line_without_quantity_is_not_valid(client):
    c, _ = client
    c.post("/binder/invoice/inv1", data={"opened": "0", "item_0": "1189 honey (LITRE)", "qty_0": "", "cost_0": "200.00",
                                  "action_0": "accept", "action_1": "skip"})
    from app import data
    assert data.decision("inv1")["rows"]["0"]["valid"] is False


def test_upload_that_is_not_an_image_is_refused_politely(client):
    import io
    c, _ = client
    r = c.post("/binder/upload", data={"photo": (io.BytesIO(b"not an image"), "notes.txt")}, content_type="multipart/form-data")
    assert r.status_code == 302 and "upload_error" in r.headers["Location"]
    assert "not a photograph" in c.get(r.headers["Location"]).get_data(as_text=True)


def test_refused_line_cannot_be_accepted_without_the_tick_even_if_the_page_is_bypassed(client):
    c, _ = client
    c.post("/binder/invoice/inv1", data={"opened": "0", "item_0": "1189 honey (LITRE)", "qty_0": "10", "cost_0": "200.00",
                                  "action_0": "accept", "item_1": "1189 honey (LITRE)", "qty_1": "1", "cost_1": "5.00",
                                  "action_1": "accept"})
    from app import data
    assert data.decision("inv1")["rows"]["1"]["action"] == "correct"


def test_unknown_action_is_rejected(client):
    c, _ = client
    assert c.post("/binder/invoice/inv1", data={"opened": "0", "action_0": "approved", "action_1": "skip"}).status_code == 400


def test_nan_and_infinity_are_not_numbers(client):
    c, _ = client
    c.post("/binder/invoice/inv1", data={"opened": "0", "item_0": "1189 honey (LITRE)", "qty_0": "nan", "cost_0": "inf",
                                  "action_0": "accept", "action_1": "skip"})
    from app import data
    row = data.decision("inv1")["rows"]["0"]
    assert row["base_quantity"] is None and row["cost"] is None and row["valid"] is False


def test_export_of_unknown_invoice_is_404(client):
    c, _ = client
    assert c.get("/binder/invoice/nope/export.csv").status_code == 404


def test_adjudication_pages_render_in_the_phone_shell(client, monkeypatch):
    c, out = client
    from app import adjudication
    item = {"key": "inv1:0", "invoice_id": "inv1", "line_index": 0, "kind": "quantity", "supplier": "vassco",
            "document_no": "123", "date": "2026-08-01", "images": [],
            "printed": {"description": "Honey 5L", "quantity": 2, "unit": None, "pack": "5L", "unit_price": 100.0,
                        "line_total": 200.0},
            "system": {"item_code": "1189", "item_name": "honey (LITRE)", "base_quantity": 10.0, "base_unit": "l",
                       "factor": 5.0, "conversion_basis": "fixed_pack", "refusal": None, "reason": "matched"},
            "pilot": {"item": "honey", "quantity": 2.0, "cost": 200.0, "reference": "123", "date": "2026-08-01"}}
    monkeypatch.setattr(adjudication, "disagreements", lambda: [item])
    assert "Honey 5L" in c.get("/adjudicate").get_data(as_text=True)
    page = c.get("/adjudicate/inv1/0").get_data(as_text=True)
    assert 'value="system"' in page and 'value="pilot"' in page and "app.css" in page
    r = c.post("/adjudicate/inv1/0", data={"verdict": "pilot", "note": "paper says 2"})
    assert r.status_code == 302
    assert '"verdict": "pilot"' in (out / "review" / "adjudications.jsonl").read_text(encoding="utf-8")
