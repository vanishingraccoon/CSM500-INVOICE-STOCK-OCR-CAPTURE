from checks.duplicates import InvoiceSummary, find_duplicates
from checks.prices import PricePoint, find_price_anomalies

# figures are substituted; the patterns are the ones seen in the venue's data


# --- duplicates ------------------------------------------------------------

def test_same_invoice_keyed_with_and_without_prefix_is_a_duplicate():
    flags = find_duplicates([
        InvoiceSummary("a", "green_grocer", "053241", 100.00),
        InvoiceSummary("b", "green_grocer", "INV053241", 100.00),
    ])
    assert [f.kind for f in flags] == ["duplicate_invoice"]
    assert "entered twice" in flags[0].reason


def test_same_reference_different_total_is_reported_but_not_called_a_duplicate():
    flags = find_duplicates([
        InvoiceSummary("a", "vassco", "1844378", 100.00),
        InvoiceSummary("b", "vassco", "1844378", 250.00),
    ])
    assert [f.kind for f in flags] == ["duplicate_reference"]
    assert flags[0].severity == "medium"


def test_same_reference_at_different_suppliers_is_not_a_duplicate():
    assert find_duplicates([
        InvoiceSummary("a", "rand_dairy", "3826", 100.00),
        InvoiceSummary("b", "vervoor", "3826", 100.00),
    ]) == []


def test_unrecognised_suppliers_are_not_compared_with_each_other():
    assert find_duplicates([
        InvoiceSummary("a", None, "3826", 100.00),
        InvoiceSummary("b", None, "3826", 100.00),
    ]) == []


def test_identical_lines_and_total_days_apart_is_possible_duplicate():
    sig = (("egg", 360.0),)
    flags = find_duplicates([
        InvoiceSummary("a", "vervoor", "170826", 660.00, "2026-08-17", line_signature=sig),
        InvoiceSummary("b", "vervoor", "170862", 660.00, "2026-08-18", line_signature=sig),
    ])
    assert [f.kind for f in flags] == ["possible_duplicate"]
    assert flags[0].evidence["days_apart"] == 1


def test_weekly_standing_order_is_not_a_duplicate():
    sig = (("la machina pina coco", 1.0), ("la machina watermelon", 2.0))
    assert find_duplicates([
        InvoiceSummary("a", "aero_power", "I02444", 567.00, "2026-06-11", line_signature=sig),
        InvoiceSummary("b", "aero_power", "I02451", 567.00, "2026-06-15", line_signature=sig),
        InvoiceSummary("c", "aero_power", "I02523", 567.00, "2026-07-06", line_signature=sig),
    ]) == []


def test_identical_content_without_dates_is_not_flagged():
    sig = (("egg", 360.0),)
    assert find_duplicates([
        InvoiceSummary("a", "vervoor", "170826", 660.00, line_signature=sig),
        InvoiceSummary("b", "vervoor", "240826", 660.00, line_signature=sig),
    ]) == []


def test_misread_leading_one_still_matches():
    flags = find_duplicates([
        InvoiceSummary("a", "aero_power", "I02604", 423.38),
        InvoiceSummary("b", "aero_power", "102604", 423.38),
    ])
    assert flags and flags[0].kind == "duplicate_invoice"


# --- prices ----------------------------------------------------------------

def test_entry_and_its_reversal_is_a_reversal_not_a_duplicate():
    flags = find_duplicates([
        InvoiceSummary("a", "cafe_albrecht", "473511", 100.00, "2026-06-26"),
        InvoiceSummary("b", "cafe_albrecht", "0473511", -100.00, "2026-06-26"),
    ])
    assert [f.kind for f in flags] == ["reversed_entry"] and flags[0].severity == "low"


def test_placeholder_item_is_flagged_as_such():
    flags = find_price_anomalies([PricePoint("## bread ##", 5, 60.0, "x")])
    assert [f.kind for f in flags] == ["placeholder_item"]


def test_deposits_are_not_price_checked():
    assert find_price_anomalies([PricePoint("deposit keg 30l", 1, 6.5, "x")], history("deposit keg 30l", 300.0)) == []


def history(item, unit_price, n=6):
    return [PricePoint(item, 10, unit_price * 10, f"h{i}") for i in range(n)]


def test_normal_price_is_not_flagged():
    assert find_price_anomalies([PricePoint("flour", 10, 112.0, "x")], history("flour", 11.0)) == []


def test_grams_keyed_as_kilograms_is_a_unit_error():
    # 10 kg keyed as 10000: unit price 1000x too low
    flags = find_price_anomalies([PricePoint("flour", 10000, 110.0, "x")], history("flour", 11.0))
    assert [f.kind for f in flags] == ["price_unit_error"]
    assert flags[0].evidence["factor"] == 1000.0 and "low" in flags[0].reason


def test_case_keyed_as_units_matches_a_pack_factor():
    # 2 cases of 24 keyed as 2: unit price 24x too high
    flags = find_price_anomalies([PricePoint("hunters dry", 2, 2 * 24 * 12.9, "x")], history("hunters dry", 12.9))
    assert flags[0].kind == "price_unit_error" and flags[0].evidence["factor"] == 24.0


def test_quantity_equal_to_cost_is_transposed():
    flags = find_price_anomalies([PricePoint("toothpicks", 78.0, 78.0, "x")], history("toothpicks", 0.108))
    assert flags[0].kind == "qty_cost_transposed"


def test_pack_with_uneven_contents_keyed_as_one_unit():
    # toothpicks held per stick; a box keyed as 1 each makes the unit price ~700x the median
    flags = find_price_anomalies([PricePoint("toothpicks", 1, 78.0, "x")], history("toothpicks", 0.108))
    assert flags[0].kind == "price_unit_error" and flags[0].severity == "high"
    assert 650 <= flags[0].evidence["factor"] <= 800 and "per pack" in flags[0].reason


def test_unexplained_outlier_says_it_needs_a_person():
    flags = find_price_anomalies([PricePoint("basil", 1, 57.0, "x")], history("basil", 11.0))
    assert flags[0].kind == "price_outlier" and "person" in flags[0].reason


def test_negative_price_on_positive_quantity():
    flags = find_price_anomalies([PricePoint("avocado", 7, -238.0, "x")], history("avocado", 34.0))
    assert flags[0].kind == "price_sign_error"


def test_zero_quantity_with_cost():
    flags = find_price_anomalies([PricePoint("lemons", 0, 45.0, "x")])
    assert flags[0].kind == "qty_zero_with_cost"


def test_median_is_robust_to_the_anomaly_itself():
    # with a mean, one 1000x entry among few would drag the baseline towards itself
    pts = history("sugar", 20.0, n=4) + [PricePoint("sugar", 1, 20000.0, "bad")]
    flags = find_price_anomalies(pts)
    assert [f.invoice_id for f in flags] == ["bad"]


def test_no_baseline_no_flag():
    assert find_price_anomalies([PricePoint("new item", 1, 999.0, "x")], history("new item", 1.0, n=2)) == []
