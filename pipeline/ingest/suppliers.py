"""Supplier registry for the case-study venue.

Each supplier has one canonical key (used for folders and layout families), the name
Pilot files it under, and the phrases that identify its letterhead. Aliases are chosen
to be distinctive: Aero Power's letterhead reads "T/A Fournews Liquor Distribution",
so Fournews Logistics may only be matched on "Fournews Logistics", never on "Fournews".

The customer block of every invoice names the venue ("News Cafe Eastgate", "Matnews"),
so those words are stripped before matching; otherwise every page would score against
any supplier whose name shares them.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Supplier:
    key: str
    pilot_name: str
    aliases: tuple[str, ...]
    goods: bool = True            # False for services and accounting constructs
    vat_numbers: tuple[str, ...] = field(default_factory=tuple)
    # The supplier's own numbering scheme, used only when no labelled document number is found:
    # binder photographs sometimes crop the "Invoice No" label off the edge of the page.
    doc_pattern: str | None = None


SUPPLIERS: tuple[Supplier, ...] = (
    Supplier("vassco", "Vassco Distributors", ("vassco distributors", "vassco", "complete basket of goods")),
    Supplier("fournews_logistics", "Fournews Logistics", ("fournews logistics", "4news logistics")),
    Supplier("green_grocer", "Green Grocer Express", ("green grocer express", "green grocer")),
    Supplier("international_seafoods", "International Seafoods", ("international seafoods", "internationalseafoods")),
    Supplier("rand_dairy", "Rand Dairy", ("rand dairy",), doc_pattern=r"6000\d{5,6}"),
    Supplier("aero_power", "Aero Power Trading", ("aero power trading", "aero power", "fournews liquor distribution"),
             vat_numbers=("4830308500",)),
    Supplier("sir_fruit", "Sir juice", ("sir fruit", "luckybird", "sirfruit")),
    Supplier("v4_butchery", "V4 Butchery", ("v4 butchery", "v4 meat")),
    Supplier("cafe_albrecht", "Cafe Albrecht", ("cafe albrecht", "artisanal bakery")),
    Supplier("avo_avenue", "avo avenue", ("avo avenue",)),
    Supplier("checkers", "Checkers", ("checkers", "shoprite checkers")),
    Supplier("ice_chain", "The Ice Chain", ("the ice chain", "ice chain")),
    Supplier("belayet", "Belayet Tbacco", ("belayet",)),
    Supplier("ocean_breeze", "Ocean Breeze", ("ocean breeze",)),
    Supplier("spoton", "SpotON Supplies", ("spoton supplies", "spot on supplies")),
    Supplier("vervoor", "Vervoor Boerdery BK", ("vervoor boerdery", "vervoor")),
    Supplier("akhalwayas", "AKHALWAYAS", ("akhalwayas", "akhalwaya")),
    Supplier("johnnys_liquor", "JOHNNYS LIQUOR", ("johnnys liquor", "johnny's liquor")),
    Supplier("georges_catering", "Georges Catering", ("georges catering", "george's catering")),
    Supplier("kya", "Kya Enterprise", ("kya enterprise",)),
    Supplier("skandara", "Skandara", ("skandara",)),
    Supplier("pna", "PNA Eastgate", ("pna", "postnet")),
    # services and non-goods accounts: identified so they can be excluded explicitly
    Supplier("shama", "SHAMA FACILITIES MANAGEMENT", ("shama facilities", "shama"), goods=False),
    Supplier("kingfisher", "Kingfisher Food Safety", ("kingfisher food safety", "kingfisher"), goods=False),
    Supplier("master_locksmith", "MASTER LOCKSMITH", ("master locksmith",), goods=False),
    Supplier("shell", "Shell", ("shell",), goods=False),
)

BY_KEY = {s.key: s for s in SUPPLIERS}
BY_PILOT_NAME = {s.pilot_name.lower(): s for s in SUPPLIERS}

# words from the venue's own customer block, removed before supplier matching
VENUE_STOPWORDS = ("news cafe eastgate", "newscafe eastgate", "news cafe", "matnews eastgate",
                   "matnews east", "matnews", "eastgate shopping centre", "eastgate")
