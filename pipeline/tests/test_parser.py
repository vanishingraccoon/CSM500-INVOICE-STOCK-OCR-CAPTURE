"""Parser tests on word layouts copied from real recognised pages, with every figure substituted."""
import pytest

from models import OCRResult, Page, Word
from parser.engine import header_tokens, parse_invoice, parse_page
from parser.layouts import GENERIC, for_supplier
from parser.numbers import merge_cell_tokens, parse_amount, parse_percent


def W(text, left, right, top, conf=99.0, h=30):
    return Word(text, left, top, right - left, h, conf)


def page(words, supplier, w=1760, h=2720):
    return (Page(page_no=1, image_path="x.jpg", source_pdf="", supplier=supplier, invoice_id=f"{supplier}_1"),
            OCRResult("x.jpg", "test", "0", 0.0, w, h, words))


# --- numbers ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("R345.00", 345.0), ("6.00V", 6.0), ("1.50√", 1.5), ("1,234.56", 1234.56), ("12,50", 12.5),
    ("(12.00)", -12.0), ("-238.00", -238.0), ("Each", None), ("15%", None), ("", None),
])
def test_parse_amount(text, value):
    assert parse_amount(text) == value


def test_parse_percent():
    assert parse_percent("15%") == pytest.approx(0.15) and parse_percent("0%") == 0.0
    assert parse_percent("15") is None


def test_merge_thousands_space_and_lost_decimal_point():
    cell = [W("1", 1134, 1140, 0), W("250", 1155, 1184, 0), W("00", 1198, 1217, 0)]
    assert [t for t, _ in merge_cell_tokens(cell, 30)] == ["1250.00"]
    assert [t for t, _ in merge_cell_tokens([W("R2", 1611, 1631, 0), W("500.00", 1646, 1711, 0)], 30)] == ["2500.00"]
    assert [t for t, _ in merge_cell_tokens([W("R645", 1632, 1674, 0), W("75", 1692, 1706, 0)], 30)] == ["645.75"]


def test_far_apart_numbers_are_not_merged():
    assert len(merge_cell_tokens([W("4", 100, 110, 0), W("100", 400, 440, 0)], 30)) == 2


def test_run_together_header_words_are_split():
    toks = [t for t, _, _ in header_tokens([W("UnitUnit", 987, 1149, 0), W("%TaxNett", 1354, 1600, 0)], GENERIC)]
    assert toks == ["unit", "unit", "%", "tax", "nett"]


# --- Aero Power style: TOTALS row, VAT and incl columns --------------------------------------

def test_aero_power_style():
    words = [
        W("Stock", 99, 147, 1043), W("Code", 165, 213, 1043), W("Description", 366, 480, 1043), W("Qty", 868, 894, 1043),
        W("Unit", 922, 962, 1043), W("Price", 975, 1022, 1043), W("Nett", 1162, 1203, 1043), W("Price", 1209, 1256, 1043),
        W("VAT", 1333, 1368, 1043), W("Incl.", 1462, 1515, 1043), W("Price", 1521, 1567, 1043),
        W("100003", 95, 161, 1108), W("Tequila", 365, 427, 1108), W("Mix", 440, 480, 1108), W("(750ml)", 490, 560, 1108),
        W("2", 888, 893, 1108), W("100.00", 959, 1026, 1108), W("200.00", 1201, 1263, 1108), W("30.00", 1324, 1371, 1108),
        W("230.00", 1511, 1571, 1108),
        W("TOTALS", 740, 830, 1600), W("200.00", 1150, 1230, 1600), W("30.00", 1300, 1370, 1600), W("230.00", 1500, 1570, 1600),
    ]
    rec = parse_invoice([page(words, "aero_power")])
    assert rec.layout == "aero_power"
    [line] = rec.lines
    assert line.description == "Tequila Mix (750ml)" and line.stock_code == "100003"
    assert (line.quantity, line.unit_price, line.line_total, line.vat) == (2, 100.0, 200.0, 30.0)
    assert line.pack == "750ml"
    assert (rec.stated_total_excl, rec.stated_vat, rec.stated_total_incl) == (200.0, 30.0, 230.0)
    assert rec.adds_up is True and not rec.warnings


# --- Cafe Albrecht style: ticks on quantities, blank VAT on zero-rated lines ------------------

def test_cafe_albrecht_style():
    words = [
        W("Code", 83, 155, 961), W("Stock", 219, 291, 961), W("Item", 307, 363, 961), W("UOM", 995, 1048, 961),
        W("Quantity", 1189, 1309, 961), W("Price", 1378, 1444, 961), W("Ex", 1480, 1510, 961), W("VAT", 1532, 1576, 961),
        W("VAT", 1682, 1731, 961), W("Total", 1815, 1891, 961), W("(Incl)", 1891, 1983, 961),
        W("900", 82, 126, 1023), W("Bread", 219, 286, 1023), W("White", 333, 406, 1023), W("Each", 988, 1048, 1023),
        W("6.00V", 1241, 1322, 1023), W("10.00", 1503, 1575, 1023), W("9.00", 1658, 1731, 1023), W("69.00", 1875, 1968, 1023),
        W("Bread", 211, 280, 1064), W("Brown", 300, 380, 1064), W("Each", 989, 1053, 1064),
        W("5.00", 1243, 1302, 1064), W("10.00", 1503, 1571, 1064), W("50.00", 1878, 1971, 1064),
        W("1708", 81, 137, 1148), W("Rolls", 214, 290, 1148), W("Dozen", 997, 1072, 1148),
        W("1.50√", 1249, 1321, 1148), W("20.00", 1505, 1580, 1148), W("4.50", 1684, 1733, 1148), W("34.50", 1904, 1977, 1148),
        W("Total", 1305, 1376, 1270), W("(Excl)", 1376, 1482, 1270), W("140.00", 1880, 1980, 1270),
        W("Tax", 1305, 1348, 1332), W("13.50", 1901, 1987, 1332),
        W("Total", 1313, 1379, 1377), W("Inc", 1404, 1446, 1377), W("153.50", 1884, 1984, 1377),
    ]
    rec = parse_invoice([page(words, "cafe_albrecht", w=2240, h=3040)])
    assert [l.quantity for l in rec.lines] == [6, 5, 1.5]
    assert [l.unit for l in rec.lines] == ["Each", "Each", "Dozen"]
    assert [l.line_total for l in rec.lines] == [60.0, 50.0, 30.0]
    assert [l.vat for l in rec.lines] == [9.0, 0.0, 4.5]
    assert (rec.stated_total_excl, rec.stated_vat, rec.stated_total_incl) == (140.0, 13.5, 153.5)
    assert rec.adds_up is True


# --- Fournews style: run-together headers, unprinted quantity, split and pointless figures ----

def test_fournews_style():
    words = [
        W("Code", 151, 208, 948), W("Description", 483, 637, 948), W("Qty", 891, 925, 948), W("UnitUnit", 987, 1149, 948),
        W("Price", 1164, 1223, 948), W("Disc", 1299, 1347, 948), W("%TaxNett", 1354, 1600, 948), W("Price", 1614, 1675, 948),
        # quantity printed
        W("100362", 111, 183, 1022), W("Sauce", 339, 399, 1022), W("BBQ", 416, 449, 1022), W("(5Lt)", 454, 509, 1022),
        W("1", 908, 928, 1022), W("Each", 1016, 1056, 1022), W("300.00", 1151, 1215, 1022), W("0%", 1320, 1343, 1022),
        W("15%", 1446, 1482, 1022), W("R300.00", 1623, 1703, 1022),
        # quantity unprinted, thousands split by spaces
        W("101262", 107, 173, 1070), W("Wings", 334, 409, 1070), W("(15KG)", 602, 682, 1070), W("Unit", 1030, 1067, 1070),
        W("1", 1134, 1140, 1070), W("250", 1155, 1184, 1070), W("00", 1198, 1217, 1070), W("0%", 1322, 1350, 1070),
        W("15%", 1449, 1485, 1070), W("R2", 1611, 1631, 1070), W("500.00", 1646, 1711, 1070),
        # decimal point lost inside the unit price
        W("101469", 104, 174, 1119), W("Bao", 335, 370, 1119), W("Buns", 382, 429, 1119), W("(12's)", 429, 499, 1119),
        W("Unit", 1026, 1067, 1119), W("4050", 1168, 1218, 1119), W("0%", 1323, 1346, 1119), W("15%", 1449, 1487, 1119),
        W("R81.00", 1630, 1704, 1119),
        # 3% discount
        W("100705", 109, 178, 1170), W("Puree", 405, 461, 1170), W("(1L)", 512, 557, 1170), W("Each", 1013, 1058, 1170),
        W("200.00", 1151, 1214, 1170), W("3%", 1318, 1345, 1170), W("15%", 1445, 1483, 1170), W("R194.00", 1620, 1700, 1170),
    ]
    rec = parse_invoice([page(words, "fournews_logistics")])
    assert [l.quantity for l in rec.lines] == [1, 2, 2, 1]
    assert [l.unit_price for l in rec.lines] == [300.0, 1250.0, 40.5, 200.0]
    assert [l.line_total for l in rec.lines] == [300.0, 2500.0, 81.0, 194.0]
    assert [l.vat for l in rec.lines] == pytest.approx([45.0, 375.0, 12.15, 29.1])
    assert rec.lines[0].pack == "5Lt" and rec.lines[1].unit == "Unit"
    joined = " / ".join(rec.warnings)
    assert "line 2: quantity not printed" in joined
    assert "line 3: decimal point missing from unit price" in joined
    assert rec.stated_total_excl is None and rec.adds_up is None


# --- avo avenue style: description on the row below, lines that do not add up ---------------

def test_description_below_and_mismatch_is_recorded_not_fixed():
    words = [
        W("Qty", 1033, 1063, 944), W("Rate", 1249, 1299, 944), W("Tax", 1502, 1544, 944), W("Amount", 1690, 1786, 944),
        W("Item", 253, 297, 972),
        W("4", 1061, 1065, 1025), W("100.00", 1225, 1299, 1025), W("0%", 1509, 1543, 1025), W("400.00", 1708, 1786, 1025),
        W("Avocado", 253, 364, 1046), W("Hass", 387, 440, 1046),
        W("Sub", 1435, 1480, 1227), W("Total", 1500, 1565, 1227), W("R420.00", 1729, 1827, 1227),
        W("Total", 1503, 1571, 1292), W("R420.00", 1728, 1828, 1292),
    ]
    rec = parse_invoice([page(words, "avo_avenue", w=2040, h=2888)])
    [line] = rec.lines
    assert line.description == "Avocado Hass"
    assert (line.quantity, line.unit_price, line.line_total, line.vat) == (4, 100.0, 400.0, 0.0)
    assert rec.stated_total_excl == 420.0 and rec.stated_total_incl == 420.0
    assert rec.adds_up is False
    assert any("recorded, not corrected" in w for w in rec.warnings)


def test_page_without_a_table_says_so():
    lines, _totals, warnings = parse_page(OCRResult("x", "t", "0", 0, 100, 100, [W("Hello", 0, 50, 0)]), GENERIC)
    assert lines == [] and warnings == ["no line-item table header found"]


def test_unknown_supplier_uses_generic_vocabulary():
    assert for_supplier("nobody").key == "generic" and for_supplier(None).key == "generic"


def test_totals_label_after_unrelated_text_on_the_same_line():
    from parser.engine import read_totals
    # Aero Power prints "Credit Terms: 14 Days" to the left of its TOTALS box
    row = [W("Credit", 106, 162, 2251), W("Terms:", 174, 247, 2251), W("14", 259, 275, 2251), W("Days", 292, 338, 2251),
           W("TOTALS", 947, 1047, 2241), W("200.00", 1218, 1282, 2243), W("30.00", 1373, 1429, 2245),
           W("230.00", 1556, 1622, 2253)]
    assert read_totals([row], 30) == {"excl": 200.0, "vat": 30.0, "incl": 230.0}


def test_delivery_tick_read_as_a_digit_is_repaired_only_when_the_line_then_adds_up():
    from parser.engine import reconcile
    notes = []
    r = reconcile({"quantity": 11.0, "unit_price": 200.0, "line_total_excl": 200.0}, notes)
    assert r["quantity"] == 1.0 and r["verified"] and "tick" in notes[0]
    notes = []
    r = reconcile({"quantity": 30.0, "unit_price": 10.0, "line_total_excl": 300.0}, notes)
    assert r["quantity"] == 30.0 and notes == []


def test_price_column_holding_the_line_amount_beside_a_total_incl_vat():
    from parser.engine import reconcile
    notes = []
    r = reconcile({"quantity": 30.0, "unit_price": 1000.0, "line_total": 1150.0}, notes)
    assert r["line_total_excl"] == 1000.0 and r["vat"] == 150.0
    assert r["unit_price"] == pytest.approx(33.3333, abs=1e-3) and "line amount" in notes[0]


def test_discount_row_is_not_the_invoice_total():
    from parser.engine import read_totals
    rows = [[W("Total", 1000, 1080, 0), W("Discount:", 1100, 1250, 0), W("R0.00", 1700, 1800, 0)],
            [W("Total", 1000, 1080, 50), W("Exclusive:", 1100, 1250, 50), W("R1,000.00", 1700, 1800, 50)],
            [W("Total:", 1000, 1080, 100), W("R1,150.00", 1700, 1800, 100)]]
    assert read_totals(rows, 30) == {"excl": 1000.0, "incl": 1150.0}


def test_the_same_sheet_photographed_twice_is_read_once():
    words = [W("Code", 100, 160, 100), W("Description", 300, 450, 100), W("Qty", 900, 940, 100),
             W("Price", 1100, 1170, 100), W("Total", 1500, 1570, 100)]
    for k in range(3):
        y = 160 + k * 50
        words += [W(f"10000{k + 1}", 95, 175, y), W(f"Item{k + 1}", 300, 400, y), W(str(k + 1), 915, 925, y),
                  W("10.00", 1110, 1170, y), W(f"{(k + 1) * 10}.00", 1500, 1570, y)]
    first = page(words, "vassco")
    second = (Page(page_no=2, image_path="y.jpg", source_pdf="", supplier="vassco", invoice_id="vassco_1"), first[1])
    rec = parse_invoice([first, second])
    assert len(rec.lines) == 3
    assert any("skipped as a second photograph" in w for w in rec.warnings)


def test_totals_are_taken_whole_from_one_page_never_mixed(monkeypatch):
    from models import Line
    from parser import engine
    per_page = {
        "p1": ([(Line(description="a", quantity=1, line_total=100.0), [])], {"excl": 100.0, "vat": 15.0}, []),
        "p2": ([(Line(description="b", quantity=1, line_total=50.0), [])], {"incl": 172.5}, []),
    }
    monkeypatch.setattr(engine, "parse_page", lambda ocr, layout: per_page[ocr.image_path])
    pages = [(Page(page_no=n, image_path=f"{k}.jpg", source_pdf="", supplier="vassco", invoice_id="vassco_1"),
              OCRResult(k, "test", "0", 0.0, 100, 100, [])) for n, k in ((1, "p1"), (2, "p2"))]
    rec = parse_invoice(pages)
    assert (rec.stated_total_excl, rec.stated_vat, rec.stated_total_incl) == (100.0, 15.0, None)


def test_a_consistent_totals_set_beats_a_partial_row_on_a_later_page():
    from parser.engine import invoice_totals
    # figures substituted; the shape is Vassco's: complete totals on page 1, a fragment on page 2
    page1 = {"excl": 3000.00, "vat": 450.00, "incl": 3450.00}
    page2 = {"vat": 211.18, "incl": 1618.95}
    assert invoice_totals([page1, page2]) == page1
    assert invoice_totals([{}, {"excl": 10.0}]) == {"excl": 10.0} and invoice_totals([{}, {}]) == {}


def test_rows_follow_the_tilt_of_a_rotated_photograph():
    from parser.geometry import estimate_skew, group_rows
    # eight words per printed line, each 3 px lower per 100 px to the right; rows 60 px apart
    words = []
    for k in range(6):
        for j in range(8):
            x = 100 + j * 200
            words.append(Word(f"w{k}{j}", x, int(1000 + k * 60 + 0.03 * x), 100, 30, 99.0, 0, k * 10 + j))
    assert abs(estimate_skew(words) - 0.03) < 0.005
    rows = group_rows(words)
    assert len(rows) == 6 and all(len(r) == 8 for r in rows)


def test_unlabelled_total_beside_a_vat_amount_includes_vat_when_price_is_missing():
    from parser.engine import reconcile
    notes = []
    r = reconcile({"quantity": 2.0, "line_total": 115.0, "vat": 15.0}, notes)
    assert r["line_total_excl"] == 100.0 and r["unit_price"] == 50.0 and r["vat"] == 15.0


def test_unreconciled_line_still_takes_a_vat_inclusive_total_less_its_vat():
    from parser.engine import reconcile
    notes = []
    # weighed item: printed quantity and price do not multiply out, the Total column includes VAT
    r = reconcile({"quantity": 4.0, "unit_price": 100.0, "line_total": 540.0, "vat": 70.0}, notes)
    assert r["line_total_excl"] == 470.0 and "does not reconcile" in notes[-1]


def test_decimal_point_lost_from_a_line_total_is_restored_only_when_printed_without_one():
    from parser.engine import reconcile
    notes = []
    r = reconcile({"quantity": 3.0, "unit_price": 12.5, "line_total_excl": 3750.0,
                   "pointless": {"line_total_excl"}}, notes)
    assert r["line_total_excl"] == 37.5 and r["verified"] and notes == ["decimal point missing from line total"]
    notes = []
    r = reconcile({"quantity": 3.0, "unit_price": 12.5, "line_total_excl": 3750.0}, notes)
    assert not r["verified"] and "does not reconcile" in notes[-1]


def test_unprinted_quantity_is_derived_in_halves_when_not_whole():
    from parser.engine import reconcile
    notes = []
    r = reconcile({"unit_price": 40.0, "line_total_excl": 100.0}, notes)
    assert r["quantity"] == 2.5 and r["verified"] and "quantity not printed" in notes[-1]
    notes = []
    r = reconcile({"unit_price": 40.0, "line_total_excl": 100.0, "derive_quantity": False}, notes)
    assert r["quantity"] is None and not r["verified"]


def test_header_word_with_one_misread_letter():
    toks = [t for t, _, _ in header_tokens([W("FRICE", 1400, 1480, 0), W("QTY", 1200, 1250, 0)], GENERIC)]
    assert toks == ["qty", "price"]


def test_totals_row_is_split_by_arithmetic_not_position():
    from parser.engine import split_totals_row
    # Vassco prints the quantity total first: Totals 28.00 | 1000.00 | 150.00
    assert split_totals_row([28.0, 1000.0, 150.0]) == {"excl": 1000.0, "vat": 150.0, "incl": 1150.0}
    assert split_totals_row([200.0, 30.0, 230.0]) == {"excl": 200.0, "vat": 30.0, "incl": 230.0}
    assert split_totals_row([100.0, 0.0, 100.0]) == {"excl": 100.0, "vat": 0.0, "incl": 100.0}


def test_stray_figure_without_wording_or_total_is_not_a_line():
    words = [W("Code", 100, 160, 100), W("Description", 300, 450, 100), W("Qty", 900, 940, 100),
             W("Price", 1100, 1170, 100), W("Total", 1500, 1570, 100),
             W("100001", 95, 175, 160), W("Sauce", 300, 380, 160), W("2", 915, 925, 160),
             W("10.00", 1110, 1170, 160), W("20.00", 1500, 1570, 160),
             # a stock code read into the quantity column beside a price, with no description and no total
             W("100371", 890, 960, 210), W("10.00", 1110, 1170, 210),
             W("Sauce", 300, 380, 260), W("3", 915, 925, 260), W("10.00", 1110, 1170, 260), W("30.00", 1500, 1570, 260)]
    rec = parse_invoice([page(words, "vassco")])
    assert [l.quantity for l in rec.lines] == [2, 3]
    assert any("read as figures and ignored" in w for w in rec.warnings)
