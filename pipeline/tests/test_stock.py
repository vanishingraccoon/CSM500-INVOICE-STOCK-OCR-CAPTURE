import pytest

from normaliser.stock import compare_venues, conversion_table, load_stock, summarise

# substituted figures; the layout is Pilot's Stock Valuation CSV export
CSV = (
    "﻿Textbox1,Textbox2\r\n"
    "News Cafe - Testvenue,Stock Valuation for 13-09-2026\r\n"
    "\r\n"
    "Cat,Total_label,Total__label,Total__Value_Y,Total1,prod,StockItem,BackStock,FrontStock,StockOnHand,UnitCost,Total,Textbox40\r\n"
    "b1 - beer-local,Total,,100.00,100.00,2,amstel 330ml (EACH),10.00,2.00,12.00,1.00,12.00,999.00\r\n"
    "dg - dry goods,Total,,50.00,50.00,1189,honey (LITRE),1.00,0.00,1.00,2.00,2.00,999.00\r\n"
    "wr - wine red,Total,,50.00,50.00,5001,thelema merlot (750ML),1.00,0.00,1.00,3.00,3.00,999.00\r\n"
    "cs - consumables,Total,,5.00,5.00,7,cashup variance (),0.00,0.00,0.00,0.00,0.00,999.00\r\n"
    "cs - consumables,Total,,5.00,5.00,8,napkins (BOX),1.00,0.00,1.00,4.00,4.00,999.00\r\n"
)


@pytest.fixture
def stock_file(tmp_path):
    p = tmp_path / "stock.csv"
    p.write_text(CSV, encoding="utf-8")
    return p


def test_load_reads_store_date_and_items(stock_file):
    s = load_stock(stock_file)
    assert s.store == "News Cafe - Testvenue" and s.as_of == "2026-09-13"
    assert [i.code for i in s.items] == ["2", "1189", "5001", "7", "8"]
    honey = s.by_code()["1189"]
    assert honey.description == "honey" and honey.base.unit == "l" and honey.unit_cost == 2.0
    assert honey.category_code == "dg"


def test_conversion_table_statuses(stock_file):
    table = {e.code: e for e in conversion_table(load_stock(stock_file))}
    assert table["2"].status == "ok" and table["2"].name_pack == "330ml"
    assert table["5001"].status == "pack_size_as_unit" and table["5001"].content == pytest.approx(0.75)
    assert table["7"].status == "base_unit_absent"
    assert table["8"].status == "base_unit_unrecognised"
    s = summarise(list(table.values()))
    assert s["unrecognised"] == ["BOX"]


def test_compare_venues_flags_unit_drift(stock_file, tmp_path):
    other = tmp_path / "other.csv"
    other.write_text(CSV.replace("honey (LITRE)", "honey (KILOGRAM)").replace("Testvenue", "Other"), encoding="utf-8")
    c = compare_venues(load_stock(stock_file), load_stock(other))
    assert c["shared_codes"] == 5 and c["same_unit"] == 4
    assert c["unit_differs"] == [("1189", "honey (LITRE)", "honey (KILOGRAM)")]
