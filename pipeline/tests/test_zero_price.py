"""A zero-cost converted line must not interrupt processing of the remaining batch."""
from checks.prices import PricePoint, find_price_anomalies
from serverkit import invoice_words, photo_bytes


def test_free_line_does_not_enter_positive_price_history():
    points = [PricePoint("juice", 1, 0, "free"), PricePoint("juice", 1, 100, "paid")]
    history = [PricePoint("juice", 1, 10, "prior") for _ in range(3)]
    flags = find_price_anomalies(points, history)
    assert [(f.invoice_id, f.kind) for f in flags] == [("paid", "price_unit_error")]


def test_zero_cost_and_zero_quantity_need_no_baseline():
    assert find_price_anomalies([PricePoint("juice", 0, 0, "free")]) == []


def test_free_invoice_does_not_hide_later_pages(phone, reader):
    batch = phone.scan(reader, [
        (photo_bytes(271), invoice_words("271001", lines=(("Tequila Mix (750ml)", 2, 0),))),
        (photo_bytes(272), invoice_words("271002")),
        (photo_bytes(273), invoice_words("271003")),
    ])
    assert len(batch["invoices"]) == 3
    assert sorted(p["ordinal"] for inv in batch["invoices"] for p in inv["pages"]) == [1, 2, 3]
