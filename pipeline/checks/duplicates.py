"""Duplicate invoice detection.

The same invoice entered twice is the cheapest error to make and one of the costliest to miss:
stock and cost of sales both double. Three traps shape the check.

References are keyed inconsistently. At Eastgate one produce supplier's lines carry 053241 and
INV053241; for Aero Power the manager typed I02582, 02595 and 102604. So references are compared
through refs.ref_keys, never as strings.

A matching reference alone is not proof. Suppliers reuse short numbers and some accounts print
a daily docket number. So a reference match is confirmed by the amount or the lines.

Identical lines and total are not proof either. Aero Power delivers the same three tequilas at
the same price every week: a standing order, not a duplicate. Run over June to September this
rule alone raised 67 flags, nearly all standing orders. So identical content under a different
reference is only flagged when the invoice dates are within a few days of each other, which is
what one delivery keyed twice looks like.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date as _date
from itertools import combinations

from models import Flag
from refs import ref_keys

AMOUNT_TOLERANCE = 0.01      # rand; totals are compared to the cent
MAX_DAYS_APART = 3           # identical content further apart than this is a repeat order


@dataclass
class InvoiceSummary:
    """The minimum the check needs, from either a parsed scan or a Pilot entry."""
    invoice_id: str
    supplier: str | None
    reference: str | None
    total: float | None
    date: str | None = None               # ISO yyyy-mm-dd
    source: str = ""                         # scan | pilot
    line_signature: tuple = field(default_factory=tuple)   # sorted (item, qty) pairs, when known


def _same_amount(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= AMOUNT_TOLERANCE


def days_apart(a: str | None, b: str | None) -> int | None:
    try:
        return abs((_date.fromisoformat(a) - _date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def find_duplicates(invoices: Iterable[InvoiceSummary]) -> list[Flag]:
    by_supplier: dict[str | None, list[InvoiceSummary]] = {}
    for inv in invoices:
        by_supplier.setdefault(inv.supplier, []).append(inv)

    flags: list[Flag] = []
    for supplier, group in by_supplier.items():
        if supplier is None:
            continue    # unrecognised suppliers are not one supplier: a shared reference or total proves nothing
        for a, b in combinations(group, 2):
            same_ref = bool(ref_keys(a.reference) & ref_keys(b.reference))
            same_amount = _same_amount(a.total, b.total)
            same_lines = bool(a.line_signature) and a.line_signature == b.line_signature
            gap = days_apart(a.date, b.date)
            evidence = {"other": a.invoice_id, "references": [a.reference, b.reference],
                        "totals": [a.total, b.total], "dates": [a.date, b.date], "days_apart": gap}
            reversal = (a.total is not None and b.total is not None and a.total != 0
                        and abs(a.total + b.total) <= AMOUNT_TOLERANCE)
            if same_ref and reversal:
                # entered, then cancelled by an equal negative entry: a correction, not a duplicate
                flags.append(Flag(
                    kind="reversed_entry", invoice_id=b.invoice_id, line_index=None,
                    severity="low", confidence=0.8,
                    reason=(f"{supplier} reference {b.reference} was entered and then reversed"
                            f" (R{a.total:,.2f} and R{b.total:,.2f}). The pair nets to nil; if the delivery"
                            " was kept, check it was entered again correctly."),
                    evidence=evidence))
            elif same_ref and (same_amount or same_lines):
                flags.append(Flag(
                    kind="duplicate_invoice", invoice_id=b.invoice_id, line_index=None,
                    severity="high", confidence=0.95,
                    reason=(f"Same invoice entered twice: {supplier} reference {a.reference} / {b.reference}"
                            f" with the same {'total' if same_amount else 'lines'}"
                            f"{f' (R{a.total:,.2f})' if same_amount else ''}."),
                    evidence=evidence))
            elif same_ref:
                flags.append(Flag(
                    kind="duplicate_reference", invoice_id=b.invoice_id, line_index=None,
                    severity="medium", confidence=0.6,
                    reason=(f"{supplier} reference {b.reference} was already used by {a.invoice_id}, but the totals differ"
                            f" (R{(a.total or 0):,.2f} vs R{(b.total or 0):,.2f}). Either a mistyped reference"
                            " or a supplier reusing a number: check the paper."),
                    evidence=evidence))
            elif same_amount and same_lines and gap is not None and gap <= MAX_DAYS_APART:
                flags.append(Flag(
                    kind="possible_duplicate", invoice_id=b.invoice_id, line_index=None,
                    severity="medium", confidence=0.5,
                    reason=(f"{supplier}: two invoices {gap} day{'s' if gap != 1 else ''} apart with different references"
                            f" ({a.reference}, {b.reference}) but identical lines and total. Possibly one delivery"
                            " keyed twice under a mistyped reference."),
                    evidence=evidence))
    return flags
