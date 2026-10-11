"""Layout families as data.

One generic engine (parser.engine) reads a line-item table by finding its header row, placing
column boundaries between the header words, and assigning every word below to a column by
position. What differs between suppliers is described here, not coded: which header words
name which field, and a few conventions (where a wrapped description sits, whether quantity
may be absent). Adding a supplier is a new entry, not a new parser.

Header vocabulary is shared across families because suppliers overwhelmingly use the same
words. A family entry overrides only what its supplier does differently.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FIELDS = ("code", "description", "quantity", "unit", "unit_price", "discount_pct", "vat",
          "line_total", "line_total_excl", "line_total_incl")
MONEY_FIELDS = frozenset({"unit_price", "line_total", "line_total_excl", "line_total_incl"})

# header phrase (normalised words) -> field. Longest phrases are tried first, so "unit price"
# wins over "unit" and "price ex vat" over "vat". "total" and "amount" alone are deliberately
# ambiguous (line_total): whether they include VAT is decided per line by arithmetic.
HEADER_PHRASES: dict[tuple[str, ...], str] = {
    ("stock", "code"): "code", ("item", "code"): "code", ("product", "code"): "code",
    ("code",): "code", ("bin",): "code", ("sku",): "code",
    ("product", "description"): "description", ("item", "description"): "description",
    ("stock", "item"): "description", ("description",): "description", ("item",): "description",
    ("product",): "description", ("details",): "description",
    ("no", "of", "units"): "quantity", ("quantity",): "quantity", ("qty",): "quantity",
    ("units", "of", "measure"): "unit", ("unit", "of", "measure"): "unit", ("uom",): "unit", ("unit",): "unit",
    ("net", "unit", "price"): "unit_price", ("unit", "price"): "unit_price", ("price", "ex", "vat"): "unit_price",
    ("price", "excl"): "unit_price", ("unit", "cost"): "unit_price", ("price",): "unit_price", ("rate",): "unit_price",
    ("disc", "%"): "discount_pct", ("discount", "%"): "discount_pct", ("disc",): "discount_pct",
    ("discount",): "discount_pct",
    ("vat", "amount"): "vat", ("tax", "amount"): "vat", ("vat",): "vat", ("tax",): "vat",
    ("nett", "price"): "line_total_excl", ("net", "price"): "line_total_excl",
    ("amount", "excl", "vat"): "line_total_excl", ("total", "excl", "vat"): "line_total_excl",
    ("amount", "excl"): "line_total_excl", ("total", "excl"): "line_total_excl", ("nett",): "line_total_excl",
    ("incl", "price"): "line_total_incl", ("total", "incl"): "line_total_incl",
    ("amount", "incl"): "line_total_incl", ("incl",): "line_total_incl",
    ("line", "total"): "line_total", ("amount",): "line_total", ("total",): "line_total",
}

# labels that end the table and carry the invoice's stated totals; "row" means a TOTALS row
# holding excl, VAT and incl side by side
TOTAL_LABELS: dict[str, str] = {
    "total excl vat": "excl", "total excl": "excl", "total exclusive": "excl", "sub total": "excl",
    "subtotal": "excl", "nett total": "excl", "total nett": "excl", "amount excl vat": "excl",
    "total vat": "vat", "vat total": "vat", "vat": "vat", "tax": "vat",
    "total incl vat": "incl", "total incl": "incl", "total inc": "incl", "invoice total": "incl",
    "total due": "incl", "amount due": "incl", "balance due": "incl", "grand total": "incl",
    "totals": "row", "total": "incl",
    "total net": "excl", "net total": "excl",
    "amount excl tax": "excl",
    # rows that end the table but carry no total of their own
    "total discount": "ignore", "discount": "ignore", "amount paid": "ignore",
}

FOOTER_WORDS = ("banking details", "bank details", "details bank", "account holder", "goods received",
                "goods receive", "received in good", "e&oe", "please note", "terms and conditions", "our banking",
                "corporate business account", "branch code")


@dataclass(frozen=True)
class Layout:
    key: str
    header_overrides: dict[tuple[str, ...], str] = field(default_factory=dict)
    min_header_fields: int = 3
    description_offset: str = "below"   # below | above | auto: where a description-only row belongs
    derive_quantity: bool = True        # quantity = total / (price x (1 - discount)) when unprinted
    notes: str = ""

    def phrases(self) -> dict[tuple[str, ...], str]:
        merged = dict(HEADER_PHRASES)
        merged.update(self.header_overrides)
        return dict(sorted(merged.items(), key=lambda kv: -len(kv[0])))


GENERIC = Layout("generic", notes="shared vocabulary only")

LAYOUTS: dict[str, Layout] = {
    "aero_power": Layout(
        "aero_power",
        notes="Omni Accounts: Stock Code | Description | Qty | Unit Price | Nett Price | VAT | Incl. Price; "
              "a TOTALS row with excl, VAT and incl side by side"),
    "avo_avenue": Layout(
        "avo_avenue", description_offset="below",
        notes="Item | Qty | Rate | Tax | Amount; the description is printed on the line below its figures; "
              "Tax holds a rate (0%), Amount the line total"),
    "cafe_albrecht": Layout(
        "cafe_albrecht",
        notes="Code | Stock Item | UOM | Quantity | Price Ex VAT | VAT | Total (Incl); VAT blank on "
              "zero-rated lines; delivery ticks fused to quantities"),
    "fournews_logistics": Layout(
        "fournews_logistics",
        notes="Code | Description | Qty | Unit | Unit Price | Disc % | Tax | Nett Price; header words run "
              "together (UnitUnit, %TaxNett); quantity often unprinted; thousands separated by a space"),
    "sir_fruit": Layout(
        "sir_fruit",
        header_overrides={("no", "of", "units"): "quantity", ("units", "of", "measure"): "unit"},
        notes="Stock Code | Description | Units of Measure | No. of Units | Net Unit Price | VAT | Total (Excl)"),
    "vassco": Layout(
        "vassco",
        notes="Bin | Description | Qty | Price | VAT | Total; Total includes VAT; watermark behind the table"),
    "international_seafoods": Layout(
        "international_seafoods",
        header_overrides={("price",): "unit_price"},
        notes="Quantity | Product Description | UOM | Price (Excl) | Amount Excl. VAT"),
}


def for_supplier(supplier_key: str | None) -> Layout:
    return LAYOUTS.get(supplier_key or "", GENERIC)
