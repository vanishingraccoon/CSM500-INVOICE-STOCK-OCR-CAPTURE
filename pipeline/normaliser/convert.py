"""Convert an invoice quantity into a stock item's base unit, or refuse and say why.

The arithmetic is trivial. Establishing which conversion applies is not, and for some lines no
conversion is defensible at all:

  "1 box of meringues"           box contents appear in neither system       -> pack_notation_absent
  "Honey 5 kg tub" held in LITRE needs a density nobody recorded            -> unit_conflict
  "Avocado Hass 2" held in KG    a box weighs what it weighs this delivery   -> pack_notation_absent
  "Napkins (2000's)" held EACH   Pilot may count packs or napkins            -> resolved by price history, else refused

A surfaced ambiguity costs one operator decision. A silent wrong factor enters the stock system
undetected and corrupts a month of theoretical cost of sales. So every rule below either finds a
factor from evidence printed on the invoice (a unit column, pack notation), or from the item's
own price history, or refuses.
"""
from __future__ import annotations

from dataclasses import dataclass

from models import Line, Refusal
from normaliser.stock import StockItem
from normaliser.units import COUNT, Pack, parse_pack, unit_from_column

PRICE_BAND = 3.0          # an implied base-unit price within 3x of the item's median is plausible
CONTENT_TOLERANCE = 0.02  # 750 ml against 0.75 l


@dataclass
class Conversion:
    factor: float | None
    basis: str = ""                  # same_unit | variable_weight | fixed_pack | history
    refusal: str | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.refusal is None and self.factor is not None


def _refuse(kind: Refusal, reason: str) -> Conversion:
    return Conversion(None, "", kind.value, reason)


def _by_history(line: Line, options: list[float], median_price: float | None, what: str) -> Conversion:
    """Pick the one factor whose implied base-unit price is plausible against the item's history."""
    if median_price and line.unit_price:
        plausible = [f for f in options
                     if 1 / PRICE_BAND <= (line.unit_price / f) / median_price <= PRICE_BAND]
        if len(plausible) == 1:
            f = plausible[0]
            return Conversion(f, "history",
                              reason=f"{what}: a factor of {f:g} gives R{line.unit_price / f:,.2f} per base unit, "
                                     f"in line with the item's usual R{median_price:,.2f}")
    return _refuse(Refusal.PACK_NOTATION_ABSENT,
                   f"{what}: could be {' or '.join(f'{o:g}' for o in options)} per invoiced unit, and "
                   f"{'the price history does not settle it' if median_price else 'there is no price history to settle it'}")


def convert(line: Line, item: StockItem, median_price: float | None = None) -> Conversion:
    base = item.base
    if line.quantity is None:
        return _refuse(Refusal.QUANTITY_MISSING, "no quantity could be read for this line")
    if not base.known:
        return _refuse(Refusal.BASE_UNIT_ABSENT,
                       f"stock item '{item.name}' has no recognisable base unit in its name"
                       + (f" ('{base.raw}')" if base.raw else ""))

    pack: Pack | None = parse_pack(line.description)
    column = unit_from_column(line.unit)
    fractional = abs(line.quantity - round(line.quantity)) > 1e-9

    # --- stock held by mass, volume or length -------------------------------------------------
    if base.dimension != COUNT:
        pack_fits = pack is not None and pack.amount is not None and pack.dimension == base.dimension
        # A pack printed in another dimension ("Honey 5 kg tub" beside a "Litre" column, held in litres) is
        # contradictory evidence: the column must not override it. It falls through to the unit conflict below.
        pack_conflicts = pack is not None and pack.amount is not None and pack.dimension != base.dimension
        # A whole number of "Sauce Pork Bao Bun (2L)" beside a unit column saying "Litre" is a count of 2 L
        # jugs: the column names what the jug holds. A weighed quantity (1.254 of "Beef Bone Marrow (1 KG)")
        # is already in the column's unit and the pack size is only the nominal size.
        if column and column[0] == base.dimension and not pack_conflicts and (fractional or not pack_fits):
            return Conversion(column[1], "variable_weight" if fractional else "same_unit",
                              reason=f"invoiced in {line.unit}, held in {base.unit}")
        if pack and pack.amount is not None:
            if pack.dimension == base.dimension:
                return Conversion(pack.total, "fixed_pack",
                                  reason=f"'{pack.raw}' is {pack.total:g} {base.unit} per invoiced unit")
            return _refuse(Refusal.UNIT_CONFLICT,
                           f"invoiced as '{pack.raw}' ({pack.dimension}) but held in {base.unit} ({base.dimension}); "
                           "converting needs a density that is not recorded anywhere")
        if column and column[0] != base.dimension and column[0] != COUNT:
            return _refuse(Refusal.UNIT_CONFLICT, f"invoiced in {line.unit} but held in {base.unit}")
        return _refuse(Refusal.PACK_NOTATION_ABSENT,
                       f"held in {base.unit} but the invoice gives only a count"
                       + (f" of {pack.container}es" if pack and pack.container else "")
                       + "; the weight or volume per unit is not printed, so it must be weighed or measured")

    # --- stock counted, each unit holding a fixed amount (a 750ML bottle) ----------------------
    if base.content is not None:
        if pack and pack.amount is not None:
            if pack.dimension == base.content_dimension and abs(pack.amount - base.content) <= CONTENT_TOLERANCE * base.content:
                return Conversion(pack.count, "fixed_pack",
                                  reason=f"'{pack.raw}' is {pack.count:g} x {base.raw} per invoiced unit")
            return _refuse(Refusal.UNIT_CONFLICT,
                           f"invoiced as '{pack.raw}' but the stock item is counted in units of {base.raw}: "
                           "a different size, not the same product")
        if pack and pack.container:
            return _refuse(Refusal.PACK_NOTATION_ABSENT,
                           f"invoiced per {pack.container} without stating how many {base.raw} units it holds")
        return Conversion(1.0, "same_unit", reason=f"one invoiced unit is one {base.raw} unit")

    # --- stock counted, plain EACH -----------------------------------------------------------
    if column and column[0] != COUNT:
        return _refuse(Refusal.UNIT_CONFLICT, f"invoiced in {line.unit} but the stock item is counted (EACH)")
    if pack and pack.count > 1:
        return _by_history(line, [1.0, pack.count], median_price, f"'{pack.raw}' on an item counted EACH")
    if pack and pack.container and pack.amount is None:
        # A plausible price does not tell us the box holds one: history can only choose between factors the
        # invoice offers, and here it offers none, so the contents stay unknown (decided by the user, 2026-09-15)
        return _refuse(Refusal.PACK_NOTATION_ABSENT,
                       f"invoiced per {pack.container}; its contents are not printed and the stock item is counted EACH")
    if column and column[1] != 1.0:
        return Conversion(column[1], "same_unit", reason=f"{line.unit} is {column[1]:g} each")
    return Conversion(1.0, "same_unit", reason="counted in the same units as invoiced")
