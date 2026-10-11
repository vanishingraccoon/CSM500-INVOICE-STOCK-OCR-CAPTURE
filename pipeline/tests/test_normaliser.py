"""Normaliser tests: matching, conversion and refusal. Items and prices are substituted."""
from collections import namedtuple

import pytest

from models import Line, Refusal
from normaliser.convert import convert
from normaliser.match import Matcher, align_lines, build_aliases, normalise_text
from normaliser.normalise import decide, normalise_line
from normaliser.stock import StockFile, StockItem
from normaliser.units import parse_base_unit, split_item_name


def item(code, name, category="dg - dry goods"):
    desc, suffix = split_item_name(name)
    return StockItem(code, name, desc, category, parse_base_unit(suffix), None, None)


STOCK = StockFile("News Cafe - Test", "2026-09-13", [
    item("1", "la machina watermelon (750ML)"), item("2", "la machina pina coco (750ML)"),
    item("3", "flour cake (KILOGRAM)"), item("4", "honey (LITRE)"), item("5", "meringue kisses (EACH)"),
    item("6", "avocado fresh (KILOGRAM)"), item("7", "bread superloaf white (EACH)"), item("8", "staff bread white (EACH)"),
    item("9", "napkin bar (EACH)"), item("10", "cashup variance ()"), item("11", "sauce bbq (LITRE)"),
    item("12", "coke can 330ml (EACH)"), item("13", "cling wrap (METER)"),
])
BY = STOCK.by_code()


def test_numeric_product_qualifiers_survive_pack_removal():
    assert normalise_text("Heineken 0.0 24x330ml") != normalise_text("Heineken 24x330ml")
    assert normalise_text("Whisky 12 year 750ml") != normalise_text("Whisky 18 year 750ml")


def test_unreadable_multipack_size_cannot_fall_back_to_one_each():
    c = convert(Line("Beer 24X33OML NRB", quantity=1), item("beer", "beer silver 330ml (EACH)"))
    assert c.refusal == Refusal.PACK_NOTATION_ABSENT.value


def L(desc, q, unit=None, price=None, total=None):
    return Line(description=desc, quantity=q, unit=unit, unit_price=price, line_total=total)


# --- conversion ------------------------------------------------------------------------------

def test_weighed_goods_invoiced_in_kg_are_variable_weight():
    c = convert(L("Peppers yellow box", 4.78, "KG", 78.0, 372.84), BY["6"])
    assert (c.factor, c.basis) == (1.0, "variable_weight")


def test_fixed_pack_by_mass():
    c = convert(L("Flour cake 2 x 12.5kg bags", 3), BY["3"])
    assert (c.factor, c.basis) == (25.0, "fixed_pack")


def test_volume_pack_into_litres():
    c = convert(L("Sauce BBQ (5Lt)", 2), BY["11"])
    assert (c.factor, c.basis) == (5.0, "fixed_pack")


def test_mass_invoiced_for_an_item_held_in_litres_is_refused():
    c = convert(L("Honey 5 kg tub", 1), BY["4"])
    assert c.refusal == Refusal.UNIT_CONFLICT.value and "density" in c.reason


def test_pack_in_another_dimension_beats_a_matching_unit_column():
    # the column says litres, the description says 5 kg: contradictory, so refused rather than taken as 1 l
    c = convert(L("Honey 5 kg tub", 1, "Litre"), BY["4"])
    assert c.refusal == Refusal.UNIT_CONFLICT.value


def test_box_of_meringues_is_refused():
    c = convert(L("1 box meringues", 1), BY["5"])
    assert c.refusal == Refusal.PACK_NOTATION_ABSENT.value


def test_box_of_avocados_held_in_kg_must_be_weighed():
    c = convert(L("Avocado Hass", 2, None, 119.0, 238.0), BY["6"])
    assert c.refusal == Refusal.PACK_NOTATION_ABSENT.value and "weighed" in c.reason


def test_bottle_matching_the_items_own_size():
    c = convert(L("Tequila La Machina Watermelon (750ml)", 2), BY["1"])
    assert (c.factor, c.basis) == (1.0, "fixed_pack")


def test_case_of_bottles():
    assert convert(L("La Machina Watermelon 6x750ml", 2), BY["1"]).factor == 6.0


def test_a_different_bottle_size_is_not_the_same_product():
    assert convert(L("La Machina Watermelon 1L", 1), BY["1"]).refusal == Refusal.UNIT_CONFLICT.value


@pytest.mark.parametrize("description", ["La Machina Watermelon", "La Machina Watermelon 750ml"])
def test_bottle_rejects_a_conflicting_mass_column(description):
    assert convert(L(description, 2, "KG"), BY["1"]).refusal == Refusal.UNIT_CONFLICT.value


def test_bottle_converts_explicit_volume_and_count_units():
    assert convert(L("La Machina Watermelon", 1.5, "Litre"), BY["1"]).factor == pytest.approx(1 / 0.75)
    assert convert(L("La Machina Watermelon", 2, "dozen"), BY["1"]).factor == 12
    assert convert(L("La Machina Watermelon 6x750ml", 2, "dozen"), BY["1"]).refusal == Refusal.UNIT_CONFLICT.value
    assert convert(L("La Machina Watermelon", 2, "PKT"), BY["1"]).refusal == Refusal.PACK_NOTATION_ABSENT.value


@pytest.mark.parametrize("name,description,quantity,unit,factor,target", [
    ("rump (GRAM)", "Rump", 10, "KG", 1000, "g"),
    ("rump (GRAM)", "Rump 500g", 2, None, 500, "g"),
    ("syrup (ML)", "Syrup", 2, "Litre", 1000, "ml"),
    ("eggs (DOZEN)", "Eggs", 24, "EACH", 1 / 12, "dozen"),
])
def test_conversion_preserves_the_actual_pilot_unit(name, description, quantity, unit, factor, target):
    stock_item = item("99", name)
    assert stock_item.base.unit == target
    c = convert(L(description, quantity, unit), stock_item)
    assert c.ok and c.factor == pytest.approx(factor)
    matcher = Matcher(StockFile("test", None, [stock_item]))
    result = normalise_line(L(description, quantity, unit), None, matcher, {})
    assert result.converted and result.base_unit == target
    assert result.base_quantity == pytest.approx(round(quantity * factor, 4))


def test_pack_count_resolved_by_price_history():
    c = convert(L("Napkin NC Bar (2000's)", 1, None, 900.0), BY["9"], median_price=0.45)
    assert (c.factor, c.basis) == (2000.0, "history")


def test_pack_count_without_history_is_refused():
    c = convert(L("Napkin NC Bar (2000's)", 1, None, 900.0), BY["9"])
    assert c.refusal == Refusal.PACK_NOTATION_ABSENT.value and "no price history" in c.reason


def test_case_of_cans_resolved_by_history():
    assert convert(L("Coke Can 24x330ml", 2, None, 240.0), BY["12"], median_price=10.0).factor == 24.0


def test_length_pack():
    assert convert(L("Cling wrap 300m roll", 1), BY["13"]).factor == 300.0 or \
        convert(L("Cling wrap 300m roll", 1), BY["13"]).refusal == Refusal.PACK_NOTATION_ABSENT.value


def test_missing_quantity_and_missing_base_unit():
    assert convert(L("Honey", None), BY["4"]).refusal == Refusal.QUANTITY_MISSING.value
    assert convert(L("Cashup", 1), BY["10"]).refusal == Refusal.BASE_UNIT_ABSENT.value


# --- matching --------------------------------------------------------------------------------

def test_normalise_text_drops_pack_notation_and_filler():
    assert normalise_text("Tequila La Machina Watermelon (750ml)") == "tequila la machina watermelon"
    assert normalise_text("Napkin NC Bar (2000's)") == "napkin bar"


def test_supplier_history_decides_between_similar_items():
    m = Matcher(STOCK, history={"aero_power": {"1", "2"}})
    top = m.candidates("aero_power", "Tequila La Machina Pine Coco")[0]
    assert top.code == "2" and top.method == "history"


def test_two_equally_plausible_items_are_refused_not_guessed():
    m = Matcher(STOCK, history={"cafe_albrecht": {"7", "8"}})
    _, refusal, reason = decide(m.candidates("cafe_albrecht", "Bread - Super Slice White"))
    assert refusal == Refusal.MULTIPLE_ITEM_MATCH.value, reason


def test_alias_learned_from_earlier_invoices_is_used():
    aliases = build_aliases([("cafe_albrecht", "Bread - Super Slice White", "7")] * 2)
    m = Matcher(STOCK, history={"cafe_albrecht": {"7", "8"}}, aliases=aliases)
    chosen, refusal, _ = decide(m.candidates("cafe_albrecht", "Bread - Super Slice White"))
    assert refusal is None and chosen.code == "7" and chosen.method == "alias"


def test_wording_with_nothing_left_after_normalising_is_never_learned_or_matched():
    assert build_aliases([("c", "12 x 1kg", "7")] * 2) == {}
    m = Matcher(STOCK, aliases={("c", ""): "7"})
    assert all(cand.method != "alias" for cand in m.candidates("c", "2 x 5"))


def test_wording_that_meant_two_items_is_not_learned():
    assert build_aliases([("c", "Super Slice White", "7"), ("c", "Super Slice White", "8")]) == {}


def test_nothing_similar_is_no_item_match():
    _, refusal, _ = decide(Matcher(STOCK).candidates(None, "Printer toner cartridge"))
    assert refusal == Refusal.NO_ITEM_MATCH.value


def test_align_scanned_and_keyed_lines_by_cost():
    P = namedtuple("P", "item cost")
    scan = [L("Super Slice White", 6, total=106.5), L("Super Slice Brown", 6, total=107.7), L("Large Brown", 1, total=33.99)]
    keyed = [P("bread brown", 33.99), P("bread superloaf white", 106.5), P("bread superloaf brown", 107.7)]
    assert align_lines(scan, keyed) == [(0, 1), (1, 2), (2, 0)]


def test_history_candidate_beats_a_lookalike_the_supplier_never_delivered():
    stock = StockFile("t", None, [item("1", "sauce peri peri (LITRE)"), item("2", "sauce peri peri hot (LITRE)")])
    m = Matcher(stock, history={"fournews_logistics": {"1"}})
    chosen, refusal, _ = decide(m.candidates("fournews_logistics", "Sauce Peri Peri (5Lt)"))
    assert refusal is None and chosen.code == "1"


def test_pack_on_the_description_wins_over_a_unit_column_for_whole_packs():
    c = convert(L("Sauce Pork Bao Bun (2L)", 3, "Litre"), BY["11"])
    assert (c.factor, c.basis) == (2.0, "fixed_pack")


def test_weighed_line_keeps_the_unit_column_despite_a_nominal_pack_size():
    c = convert(L("Beef Bone Marrow (1 KG)", 1.254, "Kilogram"), BY["6"])
    assert (c.factor, c.basis) == (1.0, "variable_weight")


def test_slices_are_a_count():
    from normaliser.units import parse_pack
    p = parse_pack("Dehydrated Orange - 125g (40 Slices)")
    assert p.count == 40 and p.amount == pytest.approx(0.125)


def test_delivery_charges_are_recognised_as_charges():
    from normaliser.match import is_charge
    assert is_charge("DELIVERY FEE JOHANNESBURG") and is_charge("Transport Received") and is_charge("Del002-Delivery Fee")
    assert not is_charge("Sauce Pork Bao Bun (2L)") and not is_charge("")


# --- end to end ------------------------------------------------------------------------------


def test_delivery_fee_is_a_non_stock_charge_not_a_failed_match():
    n = normalise_line(L("DELIVERY FEE JOHANNESBURG", 1, None, 30.0, 30.0), "vassco", Matcher(STOCK), {})
    assert n.refusal == Refusal.NON_STOCK_CHARGE.value and "not a stock item" in n.reason and n.item_code is None

def test_normalise_line_converted():
    m = Matcher(STOCK, history={"aero_power": {"1", "2"}})
    n = normalise_line(L("Tequila La Machina Watermelon (750ml)", 2, None, 211.69, 423.38), "aero_power", m, {})
    assert n.converted and n.item_code == "1" and n.base_quantity == 2.0


def test_normalise_line_refused_carries_a_reason():
    m = Matcher(STOCK, history={"avo_avenue": {"6"}})
    n = normalise_line(L("Avocado Hass", 2, None, 119.0, 238.0), "avo_avenue", m, {})
    assert not n.converted and n.item_code == "6" and n.refusal == Refusal.PACK_NOTATION_ABSENT.value
    assert n.reason
