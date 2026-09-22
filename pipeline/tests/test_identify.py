from ingest.identify import (
    find_date,
    find_document_no,
    find_page_of,
    match_supplier,
    repair_year,
)
from models import Word


def row(y, *items, h=30):
    """items are (text, left) pairs on one visual row."""
    return [Word(t, x, y, max(10, len(t) * 18), h, 99.0) for t, x in items]


def test_document_number_beside_its_label():
    words = row(100, ("Invoice", 100), ("No:", 250), ("INV478071", 330))
    assert find_document_no(words) == "INV478071"


def test_phone_vat_and_registration_numbers_are_rejected():
    words = (row(100, ("Tel:", 100), ("0118038535", 180))
             + row(150, ("VAT", 100), ("Reg", 170), ("No:", 240), ("4830308500", 320))
             + row(200, ("Reg.", 100), ("No.", 180), ("2016/405558/07", 260)))
    assert find_document_no(words) is None


def test_label_above_value_with_another_column_between():
    # Aero Power: "Document No" label, a date from the next column on the row between, then the value
    words = (row(988, ("Document", 210), ("No", 377), ("Date", 1216))
             + row(1011, ("2026/08/06", 1170))
             + row(1053, ("102595", 256)))
    assert find_document_no(words) == "102595"


def test_the_front_sheet_number_wins_when_a_sheet_behind_shows_through():
    # a strip of the invoice behind shows its number above the photographed invoice's own header
    words = (row(100, ("Tax", 1597), ("invoice", 1670)) + row(150, ("1014751", 1596))
             + row(700, ("DOCUMENT", 609), ("NO.", 785)) + row(750, ("1844378", 570)))
    assert find_document_no(words) == "1844378"


def test_value_under_account_column_is_not_a_document_number():
    words = row(100, ("Account", 900), ("No", 1040)) + row(150, ("NCEAST01", 910), ("12345678", 1100))
    assert find_document_no(words) is None


def test_hash_prefixed_reference_under_invoice_heading():
    words = row(101, ("INVOICE", 1563)) + row(148, ("#INV-058704", 1606))
    assert find_document_no(words) == "INV-058704"


def test_dates_in_both_orders():
    assert find_date(row(10, ("Date:", 10), ("2026/08/31", 90))) == "2026-08-31"
    assert find_date(row(10, ("Inv", 10), ("Date", 60), ("11/08/26", 140))) == "2026-08-11"


def test_year_first_date_is_not_reread_day_first():
    # "2026/08/24" contains "26/08/24", which must not become 26 August 2024
    assert find_date(row(10, ("Date", 10), ("2026/08/24", 90))) == "2026-08-24"
    assert find_date(row(10, ("2028/08/22", 90))) == "2028-08-22"


def test_page_of():
    assert find_page_of("Page 2 of 3") == (2, 3)
    assert find_page_of("Page 1 of 0") is None


def test_supplier_ignores_the_venue_name_in_the_customer_block():
    sup, _score = match_supplier("TAX INVOICE Customer News Cafe Eastgate Matnews East Pty Ltd")
    assert sup is None


def test_aero_power_letterhead_is_not_fournews_logistics():
    sup, _ = match_supplier("Aero Power Trading (Pty)Ltd T/A Fournews Liquor Distribution 4 Rutland Mews")
    assert sup.key == "aero_power"


def test_supplier_name_split_across_lines_and_misread():
    # page 24: faint dot-matrix Fournews Logistics header
    sup, _score = match_supplier("TAXINTOICE fournews DATE 2251B Logisfics PAGE rEn Page Na 2")
    assert sup is not None and sup.key == "fournews_logistics"


def test_value_printed_slightly_above_its_label():
    # Cafe Albrecht: INV479253 sits 6 px higher than "Inv No." and is taller
    words = [Word("INV479253", 1710, 478, 170, 42, 100.0),
             Word("Inv", 1282, 484, 50, 30, 100.0), Word("No.", 1348, 484, 45, 30, 100.0),
             Word("Customer", 63, 484, 150, 30, 100.0)]
    assert find_document_no(words) == "INV479253"


def test_value_one_line_above_its_label_beats_a_level_unrelated_field():
    # Fournews p026: FNLI30727 at y387 is level with "Co. Reg No 1995/013839/07" but belongs to "DOC NO" at y422
    words = [Word("Co.", 673, 387, 45, 30, 100.0), Word("Reg", 728, 387, 50, 30, 100.0),
             Word("No", 789, 387, 35, 30, 100.0), Word("1995/013839/07", 833, 387, 220, 30, 100.0),
             Word("FNLI30727", 1474, 387, 170, 30, 99.0),
             Word("VAT", 668, 422, 50, 30, 100.0), Word("No", 730, 422, 35, 30, 100.0),
             Word("4190161671", 775, 422, 170, 30, 100.0),
             Word("DOC", 1100, 422, 60, 30, 100.0), Word("NO", 1184, 422, 45, 30, 100.0)]
    assert find_document_no(words) == "FNLI30727"


def test_supplier_numbering_pattern_when_the_label_is_cropped_off():
    from ingest.identify import identify
    from models import OCRResult
    words = (row(40, ("RAND", 800), ("DAIRY", 1030))
             + row(100, ("Bantjes", 7), ("600032828", 213), ("Invoice", 1498), ("Date", 1673), ("2026-08-31", 1802)))
    ident = identify(OCRResult("x", "t", "0", 0, 2000, 2800, words))
    assert ident.supplier.key == "rand_dairy" and ident.document_no == "600032828"


def test_repair_year_only_for_a_confusable_digit():
    assert repair_year("2028-08-22", 2026) == "2026-08-22"
    assert repair_year("2026-08-22", 2026) == "2026-08-22"
    assert repair_year("2024-08-22", 2026) is None   # 4 for 6 is not a known confusion
    assert repair_year("2019-08-22", 2026) is None   # more than one digit differs
