"""Units: reading Pilot's base unit out of an item name, and pack notation out of an invoice line.

Pilot has no unit column. The base unit is typed by staff as a bracketed suffix on the item
name: "honey (LITRE)". Observed across 473 June items: KILOGRAM 155, EACH 134, 750ML 104,
LITRE 73, METER 4, plus "750ml", "200ML" and one empty "()". So the vocabulary is open,
and a suffix like 750ML is a pack size standing in for a unit: the item is counted in
bottles, each holding 750 ml.

Amounts are parsed in canonical units (kg, l, each, m); a stock unit also retains its
scale so exported quantities use Pilot's actual unit. Conversion stays within a dimension. Mass to volume
needs a density nobody recorded, so it is refused upstream, never guessed here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# canonical unit per dimension
MASS, VOLUME, COUNT, LENGTH = "mass", "volume", "count", "length"
CANONICAL = {MASS: "kg", VOLUME: "l", COUNT: "each", LENGTH: "m"}

# spelling -> (dimension, factor to the canonical unit)
UNIT_WORDS: dict[str, tuple[str, float]] = {
    "kilogram": (MASS, 1.0), "kilograms": (MASS, 1.0), "kg": (MASS, 1.0), "kgs": (MASS, 1.0), "kilo": (MASS, 1.0),
    "gram": (MASS, 0.001), "grams": (MASS, 0.001), "g": (MASS, 0.001), "gr": (MASS, 0.001), "grm": (MASS, 0.001),
    "litre": (VOLUME, 1.0), "litres": (VOLUME, 1.0), "liter": (VOLUME, 1.0), "liters": (VOLUME, 1.0),
    "l": (VOLUME, 1.0), "lt": (VOLUME, 1.0), "ltr": (VOLUME, 1.0), "lit": (VOLUME, 1.0),
    "ml": (VOLUME, 0.001), "millilitre": (VOLUME, 0.001), "cl": (VOLUME, 0.01),
    "each": (COUNT, 1.0), "ea": (COUNT, 1.0), "unit": (COUNT, 1.0), "units": (COUNT, 1.0), "un": (COUNT, 1.0),
    "pc": (COUNT, 1.0), "pcs": (COUNT, 1.0), "piece": (COUNT, 1.0), "no": (COUNT, 1.0), "nr": (COUNT, 1.0),
    "dozen": (COUNT, 12.0), "doz": (COUNT, 12.0), "dz": (COUNT, 12.0),
    "meter": (LENGTH, 1.0), "metre": (LENGTH, 1.0), "meters": (LENGTH, 1.0), "m": (LENGTH, 1.0),
}

# words that name a container whose contents are not stated by the word itself
CONTAINER_WORDS = {"case", "cs", "box", "bx", "ctn", "carton", "pack", "pk", "pkt", "packet", "bag", "tub",
                   "tray", "crate", "bucket", "tin", "can", "bottle", "btl", "jar", "roll", "sleeve",
                   "punnet", "bunch", "block", "drum", "keg", "sack", "shrink", "outer", "inner"}


@dataclass(frozen=True)
class BaseUnit:
    """A Pilot item's unit of stock, parsed from its name."""
    raw: str                        # suffix as typed, "" if absent
    dimension: str | None        # None when the suffix could not be understood
    unit: str | None             # unit in which Pilot holds this item
    content: float | None = None  # for pack-size suffixes (750ML): canonical amount per counted unit
    content_dimension: str | None = None
    scale: float = 1.0           # canonical amount per stock unit

    @property
    def known(self) -> bool:
        return self.dimension is not None


SUFFIX_RE = re.compile(r"\(([^()]*)\)\s*$")
AMOUNT_UNIT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-z]+)\s*$", re.IGNORECASE)


def split_item_name(name: str) -> tuple[str, str]:
    """('honey', 'LITRE') from 'honey (LITRE)'. Only a trailing bracketed group is a unit."""
    m = SUFFIX_RE.search(name)
    if not m:
        return name.strip(), ""
    return name[:m.start()].strip(), m.group(1).strip()


def parse_base_unit(suffix: str) -> BaseUnit:
    s = suffix.strip().lower()
    if not s:
        return BaseUnit(suffix, None, None)
    if s in UNIT_WORDS:
        dim, factor = UNIT_WORDS[s]
        if factor == 1.0:
            return BaseUnit(suffix, dim, CANONICAL[dim])
        unit = {MASS: "g", VOLUME: "cl" if factor == 0.01 else "ml", COUNT: "dozen"}[dim]
        return BaseUnit(suffix, dim, unit, scale=factor)
    m = AMOUNT_UNIT_RE.match(s)
    if m and m.group(2).lower() in UNIT_WORDS and float(m.group(1).replace(",", ".")) > 0:
        amount = float(m.group(1).replace(",", "."))
        dim, factor = UNIT_WORDS[m.group(2).lower()]
        # a pack size used as a unit: the item is counted, each holding this much
        return BaseUnit(suffix, COUNT, "each", content=amount * factor, content_dimension=dim)
    return BaseUnit(suffix, None, None)


# --- pack notation on invoice lines ----------------------------------------

@dataclass(frozen=True)
class Pack:
    """What one invoiced unit contains, as far as the text says."""
    raw: str
    count: float = 1.0                  # inner units per invoiced unit (24 in 24x330ml)
    amount: float | None = None      # canonical amount per inner unit (0.33 l)
    dimension: str | None = None
    container: str | None = None     # "box", "case" when named without contents

    @property
    def total(self) -> float | None:
        return None if self.amount is None else self.count * self.amount


_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(kg|kgs|kilo|g|gr|grm|gram|grams|ml|cl|l|lt|ltr|litre|liter|litres|m|meter|metre)"
MULTI_RE = re.compile(rf"(?<![\w.]){_NUM}\s*[xÃ—\*]\s*{_NUM}\s*{_UNIT}\b", re.IGNORECASE)            # 24x330ml, 2 x 12.5kg
SINGLE_RE = re.compile(rf"(?<![\w.]){_NUM}\s*{_UNIT}\b", re.IGNORECASE)                            # 750ml, 5 kg
COUNT_RE = re.compile(r"(?<![\w.])(\d+)\s*(?:'s|s\b|\s*(?:pcs|pc|pieces|units|each|ea|slices|sheets|portions|sachets"
                      r"|sticks|capsules|pods|per\s+(?:case|pack|box)))", re.IGNORECASE)  # 12's, 24 pcs, 40 Slices
X_COUNT_RE = re.compile(r"(?<![\w.])(\d+)\s*[xÃ—]\s*(?=[a-z])", re.IGNORECASE)                    # 6 x A10 (count known, contents not)


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_pack(text: str) -> Pack | None:
    """Pack notation found in a description, or None. Never infers contents from a container word."""
    if not text:
        return None
    t = text.lower()   # matched in lower case; raw text is sliced from the original so it reads as printed
    m = MULTI_RE.search(t)
    if m:
        dim, factor = UNIT_WORDS[_norm_unit(m.group(3))]
        return Pack(text[m.start():m.end()], count=_f(m.group(1)), amount=_f(m.group(2)) * factor, dimension=dim)
    m = SINGLE_RE.search(t)
    if m:
        unit = _norm_unit(m.group(2))
        # a bare "m" is too ambiguous in food descriptions (sizes, "m" for medium) unless explicit
        if unit != "m":
            dim, factor = UNIT_WORDS[unit]
            count = 1.0
            c = COUNT_RE.search(t)
            if c and c.start() != m.start():
                count = float(c.group(1))
            return Pack(text[m.start():m.end()], count=count, amount=_f(m.group(1)) * factor, dimension=dim)
    c = COUNT_RE.search(t) or X_COUNT_RE.search(t)
    container = next((w for w in re.findall(r"[a-z]+", t) if w in CONTAINER_WORDS), None)
    if c:
        return Pack(text[c.start():c.end()], count=float(c.group(1)), container=container)
    if container:
        return Pack(container, container=container)
    return None


def _norm_unit(u: str) -> str:
    u = u.lower()
    return u if u in UNIT_WORDS else {"kgs": "kg"}.get(u, u)


def unit_from_column(text: str | None) -> tuple[str, float] | None:
    """An invoice's unit column ('KG', 'EA', 'Each', 'Dozen') as (dimension, factor), or None."""
    if not text:
        return None
    return UNIT_WORDS.get(text.strip().lower().rstrip("."))
