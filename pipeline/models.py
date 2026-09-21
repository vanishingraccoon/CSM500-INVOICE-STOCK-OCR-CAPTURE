"""The objects each pipeline stage hands to the next.

Shared records define the fields exchanged between stages.
Flow: ingest -> Page, ocr -> OCRResult, parser -> InvoiceRecord (of Line),
normaliser -> NormalisedLine, checks -> Flag, app -> ExportRow.

Every object round-trips through plain dicts (to_dict / from_dict) so each stage's
output can be cached to disk as JSON and the next stage developed against the cache.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any


class _Serialisable:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


# --- ingest ----------------------------------------------------------------

@dataclass
class Page(_Serialisable):
    page_no: int                       # 1-based position in the source PDF
    image_path: str                    # ingest stores the filename under config.PAGES
    source_pdf: str
    supplier: str | None = None     # canonical supplier key, None if unreadable
    supplier_score: float = 0.0        # 0-100 fuzzy match score of the header
    document_no: str | None = None
    date: str | None = None         # ISO yyyy-mm-dd
    invoice_id: str | None = None   # which grouped invoice this page belongs to
    page_of: str | None = None      # printed "page x of y" if found
    kind: str = "invoice"              # invoice | not_invoice | unreadable
    notes: list[str] = field(default_factory=list)


# --- ocr -------------------------------------------------------------------

@dataclass
class Word(_Serialisable):
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float          # engine confidence 0-100, -1 where the engine gives none
    block: int = 0
    line: int = 0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def cx(self) -> float:
        return self.left + self.width / 2

    @property
    def cy(self) -> float:
        return self.top + self.height / 2


@dataclass
class OCRResult(_Serialisable):
    image_path: str
    engine: str
    engine_version: str
    seconds: float
    width: int
    height: int
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def mean_conf(self) -> float:
        c = [w.conf for w in self.words if w.conf >= 0]
        return sum(c) / len(c) if c else 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OCRResult:
        d = dict(d)
        d["words"] = [Word.from_dict(w) for w in d.get("words", [])]
        return super().from_dict(d)


# --- parser ----------------------------------------------------------------

@dataclass
class Line(_Serialisable):
    """One invoice line exactly as printed. No correction, no conversion."""
    description: str
    quantity: float | None = None
    unit: str | None = None           # unit column as printed (KG, EA, CS ...)
    pack: str | None = None           # pack notation found in the text (24x330ml, 12.5kg ...)
    unit_price: float | None = None   # excl. VAT where the invoice distinguishes
    line_total: float | None = None   # excl. VAT
    vat: float | None = None
    stock_code: str | None = None     # supplier's own code, not Pilot's
    raw: str = ""                        # the OCR text the line was built from
    conf: float = 0.0


@dataclass
class InvoiceRecord(_Serialisable):
    invoice_id: str
    supplier: str | None
    layout: str | None
    document_no: str | None
    date: str | None
    pages: list[int] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    stated_total_excl: float | None = None
    stated_vat: float | None = None
    stated_total_incl: float | None = None
    lines_sum_excl: float | None = None
    adds_up: bool | None = None       # recorded, never "fixed"
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InvoiceRecord:
        d = dict(d)
        d["lines"] = [Line.from_dict(x) for x in d.get("lines", [])]
        return super().from_dict(d)


# --- normaliser ------------------------------------------------------------

class Refusal(str, Enum):
    """Why a line was not converted. A refusal is an answer, not a failure."""
    NO_ITEM_MATCH = "no_item_match"                 # nothing in stock resembles the line
    MULTIPLE_ITEM_MATCH = "multiple_item_match"     # two or more items equally plausible
    BASE_UNIT_ABSENT = "base_unit_absent"           # matched item has no parseable base unit
    PACK_NOTATION_ABSENT = "pack_notation_absent"   # count of packs, contents unknown (1 box)
    UNIT_CONFLICT = "unit_conflict"                 # mass vs volume etc., needs a density
    QUANTITY_MISSING = "quantity_missing"           # parser could not read a quantity
    NON_STOCK_CHARGE = "non_stock_charge"           # delivery fee, transport: charged, but not a stock item


@dataclass
class NormalisedLine(_Serialisable):
    line: Line
    item_code: str | None = None
    item_name: str | None = None
    base_unit: str | None = None
    match_score: float = 0.0
    match_method: str = ""                  # history | fuzzy | code
    factor: float | None = None          # base units per invoiced unit
    base_quantity: float | None = None
    conversion_basis: str = ""              # fixed_pack | variable_weight | same_unit | history
    refusal: str | None = None           # a Refusal value
    reason: str = ""                        # plain words
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    @property
    def converted(self) -> bool:
        return self.refusal is None and self.base_quantity is not None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalisedLine:
        d = dict(d)
        d["line"] = Line.from_dict(d["line"])
        return super().from_dict(d)


# --- checks ----------------------------------------------------------------

@dataclass
class Flag(_Serialisable):
    kind: str                 # flag identifier; each check defines its own kind
    invoice_id: str
    line_index: int | None
    severity: str             # high | medium | low
    confidence: float         # 0-1
    reason: str               # plain English, actionable
    evidence: dict[str, Any] = field(default_factory=dict)


# --- app -------------------------------------------------------------------

@dataclass
class ExportRow(_Serialisable):
    invoice_id: str
    supplier: str
    reference: str
    date: str | None
    item_code: str
    item_name: str
    quantity_base: float
    base_unit: str
    cost_excl: float
    vat: float
