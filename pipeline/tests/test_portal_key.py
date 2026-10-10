"""The key page lists only stock items the key truly cannot convert. Items are substituted."""
from app.portal import unconvertible
from normaliser.stock import StockFile, StockItem, conversion_table
from normaliser.units import parse_base_unit, split_item_name


def item(code, name):
    desc, suffix = split_item_name(name)
    return StockItem(code, name, desc, "dg - dry goods", parse_base_unit(suffix), None, None)


def test_items_held_by_pack_size_are_not_listed_as_unconvertible():
    table = conversion_table(StockFile("Shop", None, [
        item("1", "honey (LITRE)"), item("2", "tequila mix (750ML)"), item("3", "cashup variance ()")]))
    assert [e.code for e in unconvertible(table)] == ["3"]
