"""Price anomaly detection, classified rather than merely flagged.

Each line's unit price is compared with the median unit price of the same stock item. The
median, not the mean: the anomalies are exactly the values that drag a mean towards
themselves, so a mean-based threshold hides the errors it exists to find.

"Anomaly" is not actionable, so each flag says what the number most likely is:

  price_unit_error      out by about 1000 or 100 (grams keyed as kilograms, ml as litres), or
                        by a case size (6, 10, 12, 24): quantity keyed in the wrong unit
  qty_cost_transposed   quantity equals the cost: the cost was typed into the quantity field
  qty_zero_with_cost    a cost against a quantity of zero
  price_sign_error      quantity and cost with opposite signs
  price_outlier         far from the median with no recognisable cause: needs a person

Pack factors are deliberately few. Factors of 2 or 5 fit too many ordinary price movements by
coincidence (a basil price 5.2x its median is not evidence of a pack of five), so only real
case sizes are used, with a tighter tolerance than the unit factors.

June 2026 at Eastgate held 48 lines whose unit price was 3x or more from the item's median.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from models import Flag

OUTLIER_RATIO = 3.0          # 3x from the median either way
MIN_HISTORY = 3              # fewer prior prices than this and there is no baseline
UNIT_FACTORS = (1000.0, 100.0)
UNIT_TOLERANCE = 0.15        # within 15% of 1000 counts as 1000
PACK_FACTORS = (24.0, 12.0, 10.0, 6.0)
PACK_TOLERANCE = 0.05        # case sizes must match closely
LARGE_RATIO = 50.0           # beyond this no real price change is plausible: a pack/unit keying error


@dataclass
class PricePoint:
    item: str                 # stock item code or name, whatever identifies the item consistently
    quantity: float
    cost: float               # excl. VAT
    invoice_id: str
    line_index: int | None = None
    date: str | None = None

    @property
    def unit_price(self) -> float | None:
        return self.cost / self.quantity if self.quantity else None


def _near_factor(ratio: float, factors, tolerance: float) -> float | None:
    for f in factors:
        if abs(ratio / f - 1) <= tolerance:
            return f
    return None


def classify(point: PricePoint, item_median: float) -> Flag | None:
    up = point.unit_price
    base = {"item": point.item, "quantity": point.quantity, "cost": point.cost,
            "unit_price": None if up is None else round(up, 4), "item_median": round(item_median, 4)}
    if up is None:
        if point.cost:
            return Flag("qty_zero_with_cost", point.invoice_id, point.line_index, "high", 0.9,
                        f"{point.item}: a cost of R{point.cost:,.2f} was entered against a quantity of zero.", base)
        return None
    if up < 0:
        return Flag("price_sign_error", point.invoice_id, point.line_index, "high", 0.9,
                    f"{point.item}: quantity and cost have opposite signs (qty {point.quantity}, cost {point.cost}).",
                    base)
    if item_median <= 0:
        return None
    ratio = up / item_median
    if 1 / OUTLIER_RATIO < ratio < OUTLIER_RATIO:
        return None
    big = ratio if ratio >= 1 else 1 / ratio
    direction = "high" if ratio >= 1 else "low"
    evidence = {**base, "ratio": round(ratio, 4)}

    if abs(point.quantity - point.cost) < 0.005:
        return Flag("qty_cost_transposed", point.invoice_id, point.line_index, "high", 0.8,
                    f"{point.item}: quantity and cost are the same number ({point.quantity:g}), probably the cost "
                    "was typed into the quantity field.", evidence)
    unit = _near_factor(big, UNIT_FACTORS, UNIT_TOLERANCE)
    if unit:
        return Flag("price_unit_error", point.invoice_id, point.line_index, "high", 0.85,
                    f"{point.item}: unit price is about {unit:.0f}x too {direction} against its usual price, "
                    "probably the quantity was keyed in the wrong unit (e.g. grams as kilograms).",
                    {**evidence, "factor": unit})
    pack = _near_factor(big, PACK_FACTORS, PACK_TOLERANCE)
    if pack:
        return Flag("price_unit_error", point.invoice_id, point.line_index, "medium", 0.65,
                    f"{point.item}: unit price is about {pack:.0f}x too {direction}, which matches a case size: "
                    "probably cases were keyed as units or units as cases.",
                    {**evidence, "factor": pack})
    if big >= LARGE_RATIO:
        # no supplier moves a price fifty-fold; a pack whose contents are not a round number
        # (a box of toothpicks, a 300 m roll of cling wrap) has been keyed as a single unit
        keyed = "a whole pack was keyed as one unit" if ratio >= 1 else "pack contents were keyed as packs"
        return Flag("price_unit_error", point.invoice_id, point.line_index, "high", 0.75,
                    f"{point.item}: unit price is about {big:.0f}x too {direction}. No price moves that far, so "
                    f"probably {keyed} (about {big:.0f} per pack).",
                    {**evidence, "factor": round(big)})
    return Flag("price_outlier", point.invoice_id, point.line_index, "medium", 0.5,
                f"{point.item}: unit price R{up:,.2f} is {big:.1f}x {'higher' if ratio >= 1 else 'lower'} than its "
                f"median R{item_median:,.2f} with no recognisable cause. Needs a person to check.", evidence)


def find_price_anomalies(points: Iterable[PricePoint], history: Iterable[PricePoint] = ()) -> list[Flag]:
    """Flag each point against the median of history plus the other points (leave-one-out)."""
    points = list(points)
    prices: dict[str, list[float]] = defaultdict(list)
    for p in list(history) + points:
        up = p.unit_price
        if up is not None and up > 0:
            prices[p.item].append(up)

    flags = []
    for p in points:
        name = p.item.strip()
        if name.lower().startswith("deposit"):
            # keg and gas deposits are signed adjustments against a returnable container, not purchases
            continue
        if len(name) > 2 and name.startswith("#") and name.endswith("#"):
            # "## bread ##": a catch-all item used when the right stock item could not be found. The
            # cost reaches the accounts but the stock movement lands nowhere, so say that, not "price".
            flags.append(Flag("placeholder_item", p.invoice_id, p.line_index, "medium", 0.9,
                              f"Posted to the catch-all item '{name}' instead of a real stock item: the cost is "
                              "recorded but no stock item's quantity moves.",
                              {"item": name, "quantity": p.quantity, "cost": p.cost}))
            continue
        up = p.unit_price
        if up is None or up < 0:
            # zero quantity and sign errors need no baseline
            f = classify(p, 0.0)
            if f:
                flags.append(f)
            continue
        others = list(prices[p.item])
        others.remove(up)   # leave the point itself out of its own baseline
        if len(others) < MIN_HISTORY:
            continue
        f = classify(p, median(others))
        if f:
            flags.append(f)
    return flags
