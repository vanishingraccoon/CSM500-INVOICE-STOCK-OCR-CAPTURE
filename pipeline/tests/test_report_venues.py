"""A second venue: how much of one venue's key carries over. Items are substituted."""
from eval.report import portability
from normaliser.stock import StockFile, StockItem
from normaliser.units import parse_base_unit, split_item_name


def item(code, name):
    desc, suffix = split_item_name(name)
    return StockItem(code, name, desc, "dg - dry goods", parse_base_unit(suffix), None, None)


HOME = StockFile("Venue A", "2026-09-13", [item("1", "honey (LITRE)"), item("2", "flour cake (KILOGRAM)"),
                                           item("3", "napkin bar (EACH)")])
OTHER = StockFile("Venue B", "2026-09-13", [item("1", "honey (LITRE)"), item("2", "flour cake (GRAM)"),
                                            item("9", "napkin bar (EACH)")])


def test_items_kept_the_same_way_carry_over_and_a_changed_unit_does_not():
    p = portability({"1", "2", "3", "unknown"}, HOME, OTHER)
    assert p == {"items_used": 3, "same_code_same_name": 1, "same_name_any_code": 2}
