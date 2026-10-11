import json

import pytest

from ingest.suppliers import SUPPLIERS, load_additional_suppliers


def test_supplier_identity_can_be_manual_only(tmp_path):
    path = tmp_path / "suppliers.json"
    path.write_text(json.dumps([{"key": "new_supplier", "pilot_name": "New Supplier", "aliases": []}]))
    suppliers = load_additional_suppliers(path, SUPPLIERS)
    assert suppliers[-1].key == "new_supplier" and suppliers[-1].aliases == ()
    assert suppliers[:-1] == SUPPLIERS


@pytest.mark.parametrize("row", [
    {"key": "aero_power", "pilot_name": "Other Name"},
    {"key": "other", "pilot_name": "Aero Power Trading"},
    {"key": "other", "pilot_name": "Other Name", "aliases": [""]},
])
def test_duplicate_or_empty_supplier_configuration_is_rejected(tmp_path, row):
    path = tmp_path / "suppliers.json"
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError):
        load_additional_suppliers(path, SUPPLIERS)


def test_price_window_retains_older_supplier_product_history(shop, monkeypatch):
    from app import context
    from app.shops import Keys, Shop
    from tools import pilot
    from tools.pilot import PurchaseLine

    def line(day, item, cost):
        return PurchaseLine("Aero Power Trading", "aero_power", day, "dg", item, 1, cost, 0, cost, date=day)

    purchases = [line("2026-01-01", "honey", 1), line("2026-01-02", "honey", 1),
                 line("2026-09-26", "honey", 10), line("2026-09-27", "honey", 10),
                 line("2026-01-01", "flour cake", 4)]
    monkeypatch.setattr(pilot, "load_purchasing_report", lambda path: purchases)
    key = Keys(shop)._build(context.connection(), Shop("testshop", "Test", "unused", history_days=90),
                            shop.pilot_dir / "stock_valuation_testshop_2026-09-13.csv", shop.pilot_dir / "purchases.csv")
    assert key.medians["1189"] == 10
    assert "3001" in key.matcher.history["aero_power"]
    assert len(key.history) == 2
