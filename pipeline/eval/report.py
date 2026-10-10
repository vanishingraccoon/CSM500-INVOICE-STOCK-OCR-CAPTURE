"""Every figure in the results chapter, regenerated from the pipeline outputs in one command.

    python -m eval.report          # after ingest.run, parser.run, normaliser.normalise, checks.run, tools.groundtruth

Writes data/out/results/metrics.json (all figures) and results.md (the tables). Reads only pipeline
outputs and Pilot exports; changes nothing. Money appears only as counts and rates, never as prices.

The reference is what managers keyed into Pilot, which is itself fallible (June held 48 unit-entry
errors). So agreement with Pilot is reported as agreement, not accuracy, and disagreements are
classified by what they look like rather than scored as system errors.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

import config
from checks.prices import find_price_anomalies
from checks.run import history_points
from eval import learning
from models import InvoiceRecord
from normaliser.match import align_lines
from normaliser.stock import (
    StockFile,
    compare_venues,
    conversion_table,
    default_stock_file,
    load_stock,
    summarise,
)
from tools.pilot import load_purchasing_report

RESULTS = config.OUT / "results"
SUM_TOLERANCE = 0.10


def _read(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _rate(n: int, d: int):
    return round(n / d, 3) if d else None


RATE_COLUMNS = ("rate", "precision", "share", "cer")


def table(rows: list[dict], columns: list[str]) -> str:
    def cell(v, column: str):
        if isinstance(v, float):
            # percentages only where the column is a rate; a confidence of 0.0 is not 0%
            return f"{v:.1%}" if any(s in column for s in RATE_COLUMNS) else f"{v:,.2f}"
        return "" if v is None else str(v)
    out = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    out += ["| " + " | ".join(cell(r.get(c), c) for c in columns) + " |" for r in rows]
    return "\n".join(out)


# --- sections ------------------------------------------------------------------------------

def dataset(manifest: list[dict], records: list[InvoiceRecord]) -> dict:
    kinds = Counter(p["kind"] for p in manifest)
    pages_per_invoice = Counter(len(r.pages) for r in records)
    by_supplier = Counter(r.supplier for r in records)
    return {
        "pages": len(manifest), "page_kinds": dict(kinds), "invoices": len(records),
        "multi_page_invoices": sum(v for k, v in pages_per_invoice.items() if k > 1),
        "document_number_found": sum(1 for r in records if r.document_no),
        "suppliers": len(by_supplier),
        "invoices_by_supplier": dict(by_supplier.most_common()),
        "lines": sum(len(r.lines) for r in records),
    }


def ocr(manifest: list[dict]) -> dict:
    supplier_of = {p["image_path"]: p.get("supplier") or "unidentified" for p in manifest}
    runs = []
    log = config.OCR_CACHE / "run_paddle.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if "error" not in rec:
                runs.append(rec)
    by_sup = defaultdict(list)
    for r in runs:
        by_sup[supplier_of.get(r["page"], "unidentified")].append(r)
    return {
        "pages_recognised": len(runs),
        "seconds_per_page_median": round(median(r["engine_seconds"] for r in runs), 1) if runs else None,
        "mean_confidence": round(mean(r["mean_conf"] for r in runs), 1) if runs else None,
        "by_supplier": {s: {"pages": len(v), "mean_confidence": round(mean(x["mean_conf"] for x in v), 1),
                            "lowest_page_confidence": round(min(x["mean_conf"] for x in v), 1)}
                        for s, v in sorted(by_sup.items(), key=lambda kv: -len(kv[1]))},
    }


def matching(matches: list[dict]) -> dict:
    status = Counter(m["status"] for m in matches)
    by_sup = defaultdict(Counter)
    for m in matches:
        by_sup[m.get("supplier") or m.get("pilot_supplier")][m["status"]] += 1
    scans = status["matched"] + status["matched_on_total"] + status["scan_only"]
    return {"status": dict(status), "scans_tied_to_pilot": _rate(status["matched"] + status["matched_on_total"], scans),
            "by_supplier": {k: dict(v) for k, v in by_sup.items()}}


def parser(records: dict[str, InvoiceRecord], matches: list[dict], keyed: dict) -> dict:
    fam = defaultdict(Counter)
    for m in matches:
        r = records.get(m.get("invoice_id") or "")
        if r is None:
            continue
        f = fam[r.layout]
        f["invoices"] += 1
        f["with_lines"] += bool(r.lines)
        f["stated_total_found"] += r.stated_total_excl is not None
        f["adds_up"] += r.adds_up is True
        f["does_not_add_up"] += r.adds_up is False
        f["lines"] += len(r.lines)
        f["lines_reconciled"] += len(r.lines) - sum("does not reconcile" in w for w in r.warnings)
        pl = keyed.get((m.get("pilot_supplier"), m.get("pilot_reference"), m.get("pilot_date")), [])
        if pl:
            f["tied_to_pilot"] += 1
            f["line_count_equals_pilot"] += len(r.lines) == len(pl)
            cost = round(sum(l.cost for l in pl), 2)
            f["lines_sum_equals_pilot"] += r.lines_sum_excl is not None and abs(r.lines_sum_excl - cost) <= SUM_TOLERANCE
            f["stated_equals_pilot"] += r.stated_total_excl is not None and abs(r.stated_total_excl - cost) <= SUM_TOLERANCE
    rows = []
    for name, f in sorted(fam.items(), key=lambda kv: -kv[1]["invoices"]):
        rows.append({"layout": name, **f,
                     "adds_up_rate": _rate(f["adds_up"], f["stated_total_found"]),
                     "reconciled_rate": _rate(f["lines_reconciled"], f["lines"]),
                     "sum_equals_pilot_rate": _rate(f["lines_sum_equals_pilot"], f["tied_to_pilot"])})
    total = Counter()
    for f in fam.values():
        total.update(f)
    return {"by_layout": rows, "total": dict(total),
            "adds_up_rate": _rate(total["adds_up"], total["stated_total_found"]),
            "sum_equals_pilot_rate": _rate(total["lines_sum_equals_pilot"], total["tied_to_pilot"])}


def normaliser(records: dict[str, InvoiceRecord], normalised: list[dict], matches: list[dict], keyed: dict, stock) -> dict:
    by_desc = defaultdict(set)
    for item in stock.items:
        by_desc[item.description.lower()].add(item.code)
    norm = {(n["invoice_id"], n["line_index"]): n for n in normalised}
    outcome = Counter("converted" if not n.get("refusal") and n.get("base_quantity") is not None else n.get("refusal")
                      for n in normalised)
    basis = Counter(n.get("conversion_basis") for n in normalised if not n.get("refusal") and n.get("base_quantity") is not None)

    against = Counter()
    disagreement_kinds = Counter()
    examples = defaultdict(list)
    for m in matches:
        r = records.get(m.get("invoice_id") or "")
        pl = keyed.get((m.get("pilot_supplier"), m.get("pilot_reference"), m.get("pilot_date")), [])
        if r is None or not pl:
            continue
        for i, j in align_lines(r.lines, pl):
            n = norm.get((r.invoice_id, i))
            codes = by_desc.get(pl[j].item.lower(), set())
            if n is None:
                continue
            against["aligned"] += 1
            if not codes:
                against["keyed_to_non_stock_item"] += 1
                continue
            if not n.get("item_code"):
                against[f"refused_{n.get('refusal')}"] += 1
                continue
            if n["item_code"] not in codes:
                against["item_differs"] += 1
                continue
            against["item_agrees"] += 1
            if n.get("refusal"):
                against[f"item_agrees_but_refused_{n['refusal']}"] += 1
                continue
            sq, pq = n["base_quantity"], pl[j].quantity
            if abs(sq - pq) <= max(0.01, 0.01 * abs(pq)):
                against["quantity_agrees"] += 1
                continue
            against["quantity_differs"] += 1
            kind = classify_quantity_difference(n, pq)
            disagreement_kinds[kind] += 1
            if len(examples[kind]) < 5:
                examples[kind].append({"supplier": r.supplier, "printed": n["line"]["description"],
                                       "printed_quantity": n["line"]["quantity"], "factor": n.get("factor"),
                                       "system_base_quantity": sq, "pilot_quantity": pq, "item": n.get("item_name")})
    compared = against["item_agrees"] + against["item_differs"]
    qty_compared = against["quantity_agrees"] + against["quantity_differs"]
    return {
        "outcomes": dict(outcome), "conversion_basis": dict(basis),
        "converted_rate": _rate(outcome["converted"], len(normalised)),
        "against_pilot": dict(against),
        "item_agreement_rate": _rate(against["item_agrees"], compared),
        "quantity_agreement_rate": _rate(against["quantity_agrees"], qty_compared),
        "quantity_difference_kinds": dict(disagreement_kinds),
        "quantity_difference_examples": dict(examples),
    }


def classify_quantity_difference(n: dict, pilot_qty: float) -> str:
    """What a quantity disagreement looks like. Not a verdict: adjudication against the paper decides."""
    sq, printed, factor = n["base_quantity"], n["line"].get("quantity"), n.get("factor") or 1.0
    if sq and pilot_qty:
        ratio = pilot_qty / sq
        for scale in (1000.0, 0.001):
            if abs(ratio / scale - 1) <= 0.05:
                return "scale of 1000 (grams or millilitres keyed as kg or litres, or the reverse)"
    if factor != 1.0 and printed is not None and abs(pilot_qty - printed) <= max(0.01, 0.01 * abs(printed)):
        return "keyed the invoiced count, not the base-unit quantity"
    if printed is not None and factor != 1.0 and abs(pilot_qty - printed * factor) > 0.01 and sq and \
            abs(pilot_qty / sq - round(pilot_qty / sq)) < 0.02 and round(pilot_qty / sq) > 1:
        return "keyed a whole multiple of the base quantity (pack contents counted)"
    if printed is not None and abs(round(pilot_qty) - pilot_qty) < 0.01 and abs(printed - round(printed)) > 0.01:
        return "weighed quantity rounded when keyed"
    return "other"


def pilot_keying(purchase_lines, stock) -> dict:
    """What the checks find in what managers keyed, month by month: the baseline error rate."""
    points = history_points(purchase_lines, stock)
    dates = {}
    for l in purchase_lines:
        dates[f"pilot:{l.supplier_name}|{l.reference}|{l.date}"] = l.date
    flags = find_price_anomalies(points)
    by_month = defaultdict(Counter)
    for f in flags:
        month = (dates.get(f.invoice_id) or "unknown")[:7]
        by_month[month][f.kind] += 1
    lines_by_month = Counter(l.date[:7] for l in purchase_lines)
    return {"lines_by_month": dict(sorted(lines_by_month.items())),
            "flags_by_month": {k: dict(v) for k, v in sorted(by_month.items())},
            "flags_total": dict(Counter(f.kind for f in flags))}


def checks(flags: list[dict]) -> dict:
    return {"flags_by_kind": dict(Counter(f["kind"] for f in flags)),
            "invoices_flagged": len({f["invoice_id"] for f in flags})}


def portability(codes: set[str], home: StockFile, other: StockFile) -> dict:
    """Of the stock items the normaliser chose at one venue, how many the other venue keeps the same way.

    The unit lives inside the item name, so the same name means the same base unit: an item kept under the same
    code and name converts identically at the other venue, which is what makes a rollout configuration."""
    ca, cb = home.by_code(), other.by_code()
    used = [c for c in codes if c in ca]
    names_b = {i.name.lower() for i in other.items}
    return {"items_used": len(used),
            "same_code_same_name": sum(1 for c in used if c in cb and cb[c].name.lower() == ca[c].name.lower()),
            "same_name_any_code": sum(1 for c in used if ca[c].name.lower() in names_b)}


def venues(normalised: list[dict]) -> dict:
    """The franchise premise: each venue's conversion table regenerated from its own export, no code changes."""
    stocks = [load_stock(p) for p in sorted(config.PILOT.glob("stock_valuation_*.csv"))]
    home = load_stock(default_stock_file())
    used = {n["item_code"] for n in normalised if n.get("item_code") and not n.get("refusal")}
    tables = []
    for s in stocks:
        summary = summarise(conversion_table(s))
        status = {k: summary["status"].get(k, 0)
                  for k in ("ok", "pack_size_as_unit", "base_unit_absent", "base_unit_unrecognised")}
        tables.append({"venue": s.store, "as_of": s.as_of, "items": summary["items"], **status,
                       "unrecognised_suffixes": len(summary["unrecognised"])})
    comparisons = []
    for s in stocks:
        if s.store == home.store:
            continue
        c = compare_venues(home, s)
        comparisons.append({**{k: v for k, v in c.items() if k != "unit_differs"},
                            "unit_differs": len(c["unit_differs"]), **portability(used, home, s)})
    return {"tables": tables, "comparisons": comparisons}


# --- report --------------------------------------------------------------------------------

def build() -> dict:
    manifest = _read(config.OUT / "manifest.json", [])
    records = {d["invoice_id"]: InvoiceRecord.from_dict(d) for d in _read(config.OUT / "invoices.json", [])}
    normalised = _read(config.OUT / "normalised.json", [])
    flags = _read(config.OUT / "flags.json", [])
    matches = _read(config.GT / "pilot_match.json", [])
    purchase_lines = load_purchasing_report()
    stock = load_stock(default_stock_file())
    keyed = learning.keyed_lines(purchase_lines)
    return {
        "dataset": dataset(manifest, list(records.values())),
        "ocr": ocr(manifest),
        "matching_to_pilot": matching(matches),
        "parser": parser(records, matches, keyed),
        "normaliser": normaliser(records, normalised, matches, keyed, stock),
        "learning_replay": learning.summary(learning.replay(records, matches, purchase_lines, stock)),
        "checks": checks(flags),
        "pilot_keying_baseline": pilot_keying(purchase_lines, stock),
        "venues": venues(normalised),
    }


def _dataset_section(d: dict) -> list[str]:
    return [
        "## Dataset",
        table([{"pages": d["pages"], "invoices": d["invoices"], "multi-page": d["multi_page_invoices"],
                "document number found": d["document_number_found"], "suppliers": d["suppliers"], "lines": d["lines"]}],
              ["pages", "invoices", "multi-page", "document number found", "suppliers", "lines"]),
    ]


def _ocr_section(o: dict) -> list[str]:
    return [
        "## Recognition (PaddleOCR)",
        f"Median {o['seconds_per_page_median']} s per page; mean word confidence {o['mean_confidence']}.",
        table([{"supplier": s, **v} for s, v in o["by_supplier"].items()], ["supplier", "pages", "mean_confidence", "lowest_page_confidence"]),
    ]


def _matching_section(mt: dict) -> list[str]:
    return [
        "## Scanned invoices tied to Pilot entries",
        table([{"status": k, "count": v} for k, v in mt["status"].items()], ["status", "count"]),
    ]


def _parser_section(p: dict) -> list[str]:
    return [
        "## Parser by layout family",
        table(p["by_layout"], ["layout", "invoices", "with_lines", "lines", "reconciled_rate", "stated_total_found", "adds_up",
                               "adds_up_rate", "tied_to_pilot", "line_count_equals_pilot", "lines_sum_equals_pilot", "sum_equals_pilot_rate"]),
    ]


def _normaliser_section(n: dict) -> list[str]:
    return [
        "## Normaliser",
        table([{"outcome": k, "lines": v} for k, v in sorted(n["outcomes"].items(), key=lambda kv: -kv[1])], ["outcome", "lines"]),
        "### Against the manager's keyed entry (lines paired by cost)",
        table([{"measure": k, "lines": v} for k, v in sorted(n["against_pilot"].items(), key=lambda kv: -kv[1])], ["measure", "lines"]),
        f"Item agreement {n['item_agreement_rate']}; quantity agreement where the item agrees and the line converted {n['quantity_agreement_rate']}.",
        "### What quantity disagreements look like",
        table([{"kind": k, "lines": v} for k, v in sorted(n["quantity_difference_kinds"].items(), key=lambda kv: -kv[1])], ["kind", "lines"]),
    ]


def _learning_section(lr: dict) -> list[str]:
    return [
        "## Matching as invoices are reviewed (August replayed in date order)",
        table([{"variant": "no learning", **lr["baseline"]}, {"variant": "learning from reviewed invoices", **lr["learning"]}],
              ["variant", "aligned", "agree", "disagree", "refused_match", "agree_rate", "wrong_rate", "refusal_rate"]),
        f"Aliases learned: {lr['aliases_learned']}.",
    ]


def _checks_section(checks: dict, baseline: dict) -> list[str]:
    return [
        "## Checks",
        table([{"flag": k, "count": v} for k, v in checks["flags_by_kind"].items()], ["flag", "count"]),
        "### Price checks over what managers keyed, by month",
        table([{"month": k, "lines": baseline["lines_by_month"].get(k), **v}
               for k, v in baseline["flags_by_month"].items()],
              ["month", "lines", "price_unit_error", "price_outlier", "qty_zero_with_cost", "price_sign_error",
               "qty_cost_transposed", "placeholder_item"]),
    ]


def _venues_section(v: dict | None) -> list[str]:
    if not (v and v["comparisons"]):
        return []
    parts = [
        "## A second venue: the conversion table regenerated from its own export",
        "The same loader and table builder read each venue's Stock Valuation export; no code was changed.",
        table(v["tables"], ["venue", "as_of", "items", "ok", "pack_size_as_unit", "base_unit_absent",
                            "base_unit_unrecognised", "unrecognised_suffixes"]),
    ]
    for c in v["comparisons"]:
        parts.append(
            f"{c['venue_a']} against {c['venue_b']}: {c['shared_codes']} stock codes appear at both "
            f"({c['only_a']} only at the first, {c['only_b']} only at the second); of the shared codes, "
            f"{c['same_name']} carry the same name and {c['same_unit']} the same base unit ({c['unit_differs']} differ). "
            f"Of the {c['items_used']} stock items the normaliser chose for the August invoices, "
            f"{c['same_code_same_name']} are kept at the second venue under the same code and name, and "
            f"{c['same_name_any_code']} under the same name.")
    return parts


def _handwriting_section() -> list[str]:
    hw = _read(RESULTS / "hwr_trial.json")
    if not hw:
        return []
    rows = [{"engine": engine, "kind": kind, **v}
            for engine, kinds in hw["engines"].items() for kind, v in sorted(kinds.items())]
    return [
        "## Handwriting trial (reported separately from printed text)",
        (f"{hw['ink_regions']} ink regions on {hw['sampled_pages']} sampled pages; detection precision "
         f"{hw['detection_precision']:.0%}; {hw['handwriting']} handwritten, of which "
         f"{hw['legible_text_regions_scored']} legible text regions were scored. PaddleOCR read something in "
         f"{hw['paddle_read_anything_share']:.0%} of handwritten regions."),
        table(rows, ["engine", "kind", "regions", "mean_cer", "exact_match_rate"]),
    ]


def _library_trials_section() -> list[str]:
    dewarp = _read(config.DATA / "trials" / "dewarp" / "results.json")
    tables_trial = _read(config.DATA / "trials" / "tables" / "results.json")
    parts = []
    if dewarp or tables_trial:
        parts.append("## Library trials (evaluated against the binder, not adopted)")
    if dewarp:
        parts += [
            "page-dewarp (MIT, github.com/lmmx/page-dewarp): each page read with Tesseract before and after flattening.",
            table([{"page": r["page"], "group": r["group"], "paddle_conf": r["paddle_conf"],
                    "tesseract_conf_before": r["before"]["mean_conf"], "tesseract_conf_after": r.get("after", {}).get("mean_conf"),
                    "words_before": r["before"]["words"], "words_after": r.get("after", {}).get("words")} for r in dewarp],
                  ["page", "group", "paddle_conf", "tesseract_conf_before", "tesseract_conf_after", "words_before", "words_after"]),
        ]
    if tables_trial:
        closer = sum(1 for r in tables_trial
                     if abs(r["largest_table_rows"] - 1 - r["pilot_lines"]) < abs(r["our_lines"] - r["pilot_lines"]))
        parts += [
            (f"img2table (MIT, github.com/xavctn/img2table): on {len(tables_trial)} invoices whose parsed line count differs "
             f"from Pilot's, its largest detected table was closer to Pilot's line count on {closer}."),
            table(tables_trial, ["invoice_id", "pages", "our_lines", "pilot_lines", "tables_found", "largest_table_rows"]),
        ]
    return parts


def _speed_section() -> list[str]:
    speed = _read(config.DATA / "trials" / "speed" / "results.json")
    if not speed:
        return []
    return [
        "## Speed trial: recognition settings against agreement with Pilot",
        *(s for s in (speed.get("sample_rule"), speed.get("adoption_rule")) if isinstance(s, str)),
        speed.get("recommendation", ""),
        "Full tables: data/trials/speed/results.md (python -m eval.trials.speed).",
    ]


def _adjudication_section() -> list[str]:
    verdict_log = config.OUT / "review" / "adjudications.jsonl"
    if not verdict_log.exists():
        return []
    latest = {}
    for line in verdict_log.read_text(encoding="utf-8").splitlines():
        v = json.loads(line)
        latest[v["key"]] = v
    counts = Counter((v["kind"], v["verdict"]) for v in latest.values())
    return [
        "## Adjudication against the paper",
        f"{len(latest)} disagreements settled by looking at the invoice.",
        table([{"kind": k, "verdict": verdict, "lines": c} for (k, verdict), c in sorted(counts.items())],
              ["kind", "verdict", "lines"]),
    ]


def markdown(m: dict) -> str:
    parts = [
        "# Results (generated by eval.report)",
        *_dataset_section(m["dataset"]),
        *_ocr_section(m["ocr"]),
        *_matching_section(m["matching_to_pilot"]),
        *_parser_section(m["parser"]),
        *_normaliser_section(m["normaliser"]),
        *_learning_section(m["learning_replay"]),
        *_checks_section(m["checks"], m["pilot_keying_baseline"]),
        *_venues_section(m.get("venues")),
        *_handwriting_section(),
        *_library_trials_section(),
        *_speed_section(),
        *_adjudication_section(),
    ]
    return "\n\n".join(parts) + "\n"


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)
    m = build()
    (RESULTS / "metrics.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    (RESULTS / "results.md").write_text(markdown(m), encoding="utf-8")
    print(markdown(m))
    print(f"-> {RESULTS}")


if __name__ == "__main__":
    main()
