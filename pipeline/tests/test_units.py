import pytest

from normaliser.units import (
    COUNT,
    LENGTH,
    MASS,
    VOLUME,
    parse_base_unit,
    parse_pack,
    split_item_name,
    unit_from_column,
)

# --- Pilot item names ------------------------------------------------------

def test_split_item_name_takes_only_a_trailing_bracket():
    assert split_item_name("honey (LITRE)") == ("honey", "LITRE")
    assert split_item_name("cloth mutton 400g (METER)") == ("cloth mutton 400g", "METER")
    assert split_item_name("tequila (la machina) watermelon") == ("tequila (la machina) watermelon", "")
    assert split_item_name("cashup variance ()") == ("cashup variance", "")


@pytest.mark.parametrize("suffix,dim,unit", [
    ("KILOGRAM", MASS, "kg"), ("EACH", COUNT, "each"), ("LITRE", VOLUME, "l"), ("METER", LENGTH, "m"),
    ("kg", MASS, "kg"), (" Litre ", VOLUME, "l"),
])
def test_real_base_units(suffix, dim, unit):
    b = parse_base_unit(suffix)
    assert (b.dimension, b.unit, b.content) == (dim, unit, None)


def test_pack_size_used_as_a_unit_is_a_count_with_content():
    b = parse_base_unit("750ML")
    assert b.dimension == COUNT and b.unit == "each"
    assert b.content == pytest.approx(0.75) and b.content_dimension == VOLUME
    assert parse_base_unit("750ml").content == pytest.approx(0.75)
    assert parse_base_unit("200ML").content == pytest.approx(0.2)


def test_empty_or_unknown_suffix_is_unknown_not_each():
    assert not parse_base_unit("").known
    assert not parse_base_unit("BOX").known
    assert not parse_base_unit("PORTION").known


# --- invoice pack notation -------------------------------------------------

def test_multi_pack():
    p = parse_pack("Coke Can 24x330ml")
    assert (p.count, p.dimension) == (24, VOLUME)
    assert p.amount == pytest.approx(0.33) and p.total == pytest.approx(7.92)


def test_multi_pack_with_spaces_and_decimal():
    p = parse_pack("Flour cake 2 x 12.5kg bags")
    assert p.count == 2 and p.amount == pytest.approx(12.5) and p.dimension == MASS


def test_single_amount():
    p = parse_pack("Tequila La Machina Watermelon (750ml)")
    assert p.count == 1 and p.amount == pytest.approx(0.75) and p.dimension == VOLUME
    assert parse_pack("Honey 5 kg tub").amount == pytest.approx(5.0)
    assert parse_pack("Sauce tomato 5L").dimension == VOLUME


def test_decimal_comma():
    assert parse_pack("Cheese 2,5kg").amount == pytest.approx(2.5)


def test_grams_convert_to_kg():
    assert parse_pack("Crumbed chicken 500g").amount == pytest.approx(0.5)


def test_container_without_contents_is_not_given_contents():
    p = parse_pack("1 box meringues")
    assert p.amount is None and p.container == "box"
    assert parse_pack("Avocado Hass").__class__ is type(None)


def test_count_with_unknown_contents():
    p = parse_pack("Jalapeno sliced 6 x A10 tins")
    assert p.count == 6 and p.amount is None and p.container == "tins" or p.container is None


def test_bare_m_is_not_metres():
    assert parse_pack("Bag TA 4m") is None or parse_pack("Bag TA 4m").dimension != LENGTH


def test_unit_column():
    assert unit_from_column("KG") == (MASS, 1.0)
    assert unit_from_column("Dozen") == (COUNT, 12.0)
    assert unit_from_column("PKT") is None
    assert unit_from_column(None) is None
@pytest.mark.parametrize("text,count,amount", [
    ("Drink (24 \u00d7 250ml)", 24, 0.25),
    ("FRUIT RUBYAPPLE24X275ML", 24, 0.275),
    ("HEINEKEN24X330MLNRR", 24, 0.33),
    ("SAVANNA DRY24X330MLNRB5", 24, 0.33),
    ("MINT SYRUP1L", 1, 1),
])
def test_pack_notation_with_unicode_multiplier_and_glued_words(text, count, amount):
    pack = parse_pack(text)
    assert pack is not None
    assert pack.count == count and pack.amount == pytest.approx(amount)

