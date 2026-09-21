"""Invoice reference normalisation, shared by ingest, checks and ground-truth matching.

The same invoice is keyed differently by different people: 053241 and INV053241 at one
venue, and for Aero Power's printed I02657 the manager has typed I02582, 02595 and
102604 on successive invoices, reading the letter I as a one or dropping it. So a
reference is not compared as a string. It is reduced to a set of plausible keys, and
two references are the same invoice if their key sets intersect.
"""
from __future__ import annotations

import re

_PREFIX = re.compile(r"^(?:INV|IN|NO|DOC|I|#)+(?=\d)")
_LETTERS = re.compile(r"^[A-Z]+(?=\d)")


def ref_keys(ref: str | None) -> frozenset[str]:
    if not ref:
        return frozenset()
    base = re.sub(r"[^A-Z0-9]", "", str(ref).upper())
    if not base:
        return frozenset()
    variants = {base}
    stripped = _PREFIX.sub("", base)
    variants.add(stripped)
    # a supplier's own letter prefix (FNLI30776) is often left off when keyed: 30776
    variants.add(_LETTERS.sub("", base))
    # a leading 1 in front of a zero-padded number is usually an I read or typed as a one
    if re.fullmatch(r"10\d{3,}", stripped):
        variants.add(stripped[1:])
    keys = set()
    for v in variants:
        if not v:
            continue
        keys.add(v)
        if v.isdigit():
            keys.add(v.lstrip("0") or "0")
    return frozenset(keys)


def primary_key(ref: str | None) -> str:
    """One stable display key: prefix removed, leading zeros removed."""
    if not ref:
        return ""
    base = _PREFIX.sub("", re.sub(r"[^A-Z0-9]", "", str(ref).upper()))
    return base.lstrip("0") if base.isdigit() else base


def same_invoice(a: str | None, b: str | None) -> bool:
    return bool(ref_keys(a) & ref_keys(b))
