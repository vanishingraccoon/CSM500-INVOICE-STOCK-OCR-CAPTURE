"""Trial: can PaddleOCR read a page faster without losing agreement with what managers keyed in Pilot?

Runs in .venv, the PaddleOCR environment (unlike the library trials, which use .venv-trial):

    .venv/Scripts/python -m eval.trials.speed               # time what is not yet timed, then score and report
    .venv/Scripts/python -m eval.trials.speed --score-only  # score and report from cached results and logged timings
    .venv/Scripts/python -m eval.trials.speed --retime      # time every variant on every sample page again

Variants
  A  current defaults (ocr/paddle.py): PP-OCRv5_mobile_det, PP-OCRv6_medium_rec, detection at full resolution.
  B  A with text_det_limit_side_len=1280 and text_det_limit_type="max": detection on the page scaled to 1280 px.
  C  B's detection; every text line read by PP-OCRv5_mobile_rec, then read again by PP-OCRv6_medium_rec when it
     holds a digit, the mobile model scored it below 0.90, or it starts in the top half of the page (the region
     ingest reads for supplier, number and date).

Sample: chosen by rule, with no randomness (SAMPLE_RULE below).

Timing: one process, every model loaded and warmed first. Pages are taken in sample order and, for each page, the
variants run back to back, so another process using the CPU slows them alike. Wall time, this process's CPU time and
the load from other processes are logged per page. A is re-timed without touching its cache entry. C is timed on the
first PILOT_PAGES pages and continues only if its CPU time there is below B's: C shares B's detection and reads some
lines with a weaker model, so if it is no cheaper than B it cannot be preferred to B.

Scoring: each variant's text is taken through identify, parse_invoice and normalise_line as the pipeline does, keeping
the manifest's page grouping and the Pilot tie fixed, and agreement is counted by eval.report's own functions.
Adoption rule agreed with the user: faster than A, and neither item nor quantity agreement drops (rate and count).

Outputs go to data/trials/speed/. B's results are cached in the pipeline's OCR cache under their settings tag, so an
adopted change reuses them; C's cache is kept under data/trials/speed/ocr_cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import OrderedDict, defaultdict
from functools import cached_property
from pathlib import Path
from statistics import mean, median

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import config
from ingest.run import HEADER_FRACTION
from models import OCRResult
from ocr.paddle import PaddleEngine, words_from_result

OUT = config.DATA / "trials" / "speed"
TIMINGS = OUT / "timings.jsonl"
TARGET_PAGES = 30
PILOT_PAGES = 5
TIED = ("matched", "matched_on_total")
VARIANTS = ("A", "B", "C")
B_OPTIONS = {"text_det_limit_side_len": 1280, "text_det_limit_type": "max"}
FAST_REC = "PP-OCRv5_mobile_rec"
CASCADE_MIN_SCORE = 0.90

DESCRIPTIONS = {
    "A": "current defaults: PP-OCRv5_mobile_det at full resolution, PP-OCRv6_medium_rec",
    "B": "A with text_det_limit_side_len=1280, text_det_limit_type=max",
    "C": (f"B's detection; all lines read by {FAST_REC}, lines with a digit, a score below {CASCADE_MIN_SCORE}, "
          f"or starting in the top {HEADER_FRACTION:.0%} of the page read again by PP-OCRv6_medium_rec"),
}
SAMPLE_RULE = (
    "Every scanned invoice tied to a Pilot entry in data/gt/pilot_match.json (status matched or matched_on_total) "
    "whose keyed lines are in the Purchasing Report. Suppliers take turns in alphabetical order; each turn takes that "
    "supplier's next invoice in binder page order, with all its pages; selection stops as soon as {n} pages are taken. "
    "No randomness: the same inputs always give the same sample."
)


# --- sample --------------------------------------------------------------------------------

def choose_sample(matches: list[dict], manifest: list[dict], keyed: dict,
                  target_pages: int = TARGET_PAGES) -> list[tuple[dict, list[dict]]]:
    """(Pilot match, manifest pages of its invoice) in the order chosen by SAMPLE_RULE."""
    pages_of: OrderedDict[str, list[dict]] = OrderedDict()
    for p in manifest:
        if p.get("invoice_id"):
            pages_of.setdefault(p["invoice_id"], []).append(p)
    queues: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        if (m.get("status") in TIED and m.get("invoice_id") in pages_of
                and keyed.get((m.get("pilot_supplier"), m.get("pilot_reference"), m.get("pilot_date")))):
            queues[m.get("supplier") or ""].append(m)
    for q in queues.values():
        q.sort(key=lambda m: (pages_of[m["invoice_id"]][0]["page_no"], m["invoice_id"]))
    chosen, n_pages = [], 0
    while n_pages < target_pages and any(queues.values()):
        for supplier in sorted(queues):
            if n_pages >= target_pages:
                break
            if queues[supplier]:
                m = queues[supplier].pop(0)
                chosen.append((m, pages_of[m["invoice_id"]]))
                n_pages += len(pages_of[m["invoice_id"]])
    return chosen


# --- variant C: a mobile-first cascade ---------------------------------------------------------

def needs_accurate(text: str, score: float, top: float, page_height: float,
                   min_score: float = CASCADE_MIN_SCORE, header_fraction: float = HEADER_FRACTION) -> bool:
    """Whether a line the fast model read is read again by the accurate one: it holds a figure (money, quantity,
    number or date), the reading is doubtful, or it lies where ingest looks for supplier, number and date."""
    return any(ch.isdigit() for ch in text) or score < min_score or top < page_height * header_fraction


def read_lines(model, crops: list) -> list[tuple[str, float, list]]:
    """(text, score, word info) per crop, fed in order of aspect ratio as the PaddleOCR pipeline feeds them."""
    order = sorted(range(len(crops)), key=lambda i: crops[i].shape[1] / float(crops[i].shape[0]))
    out: list = [None] * len(crops)
    if crops:
        for k, rec in enumerate(model([crops[i] for i in order], return_word_box=True)):
            out[order[k]] = (rec["rec_text"][0], float(rec["rec_score"]), rec["rec_text"][1])
    return out


class CascadeEngine(PaddleEngine):
    """Detection once, every line read by a fast recogniser, the lines that matter read again by the accurate one.

    Reuses the PaddleOCR pipeline's own detector, box sorting, cropping and word-box code, so with the fast pass
    switched off (accurate_for_all) it reproduces PaddleOCR.predict; manual_check tests that."""
    name = "paddle_cascade"

    def __init__(self, lang: str = "en", fast_model: str = FAST_REC, min_score: float = CASCADE_MIN_SCORE,
                 header_fraction: float = HEADER_FRACTION, accurate_for_all: bool = False, **options):
        super().__init__(lang=lang, **{**B_OPTIONS, **options})
        self.fast_model, self.min_score, self.header_fraction = fast_model, min_score, header_fraction
        self.accurate_for_all = accurate_for_all
        self.last_stats: dict = {}

    def cache_tag(self) -> str:
        settings = {"base": super().cache_tag(), "fast": self.fast_model, "min_score": self.min_score,
                    "header_fraction": self.header_fraction, "accurate_for_all": self.accurate_for_all}
        return "c" + hashlib.sha1(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:8]

    @cached_property
    def _fast(self):
        from paddleocr import PaddleOCR
        return PaddleOCR(lang=self.lang, **{**self.options, "text_recognition_model_name": self.fast_model})

    def recognise(self, image_path: Path, crop: tuple[int, int, int, int] | None = None,
                  psm: int = 3) -> OCRResult:
        image_path = Path(image_path)
        with Image.open(image_path) as im:
            width, height = im.size
            region = im.crop(crop) if crop else im
            arr = np.asarray(region.convert("RGB"))[:, :, ::-1].copy()  # BGR, as PaddleEngine passes it
        dx, dy = (crop[0], crop[1]) if crop else (0, 0)
        t0 = time.perf_counter()
        res = self._read(arr, page_height=height, dy=dy)
        seconds = time.perf_counter() - t0
        return OCRResult(image_path=str(image_path), engine=self.name, engine_version=self.version(),
                         seconds=round(seconds, 3), width=width, height=height, words=words_from_result(res, dx, dy))

    def _read(self, arr, page_height: int, dy: int = 0) -> dict:
        from paddlex.inference.pipelines.components import (
            cal_ocr_word_box,
            convert_points_to_boxes,
        )
        accurate = self._ocr.paddlex_pipeline          # forwards to the pipeline object PaddleOCR.predict uses
        image = accurate.img_reader([arr])[0]
        det = next(iter(accurate.text_det_model([image], **accurate.get_text_det_params())))
        polys = accurate._sort_boxes(det["dt_polys"])
        crops, kept = [], []
        if len(polys):
            for sub, poly in zip(accurate._crop_by_polys(image, polys), polys):
                if sub.size > 0 and sub.shape[0] > 0 and sub.shape[1] > 0:
                    crops.append(sub)
                    kept.append(poly)
        if self.accurate_for_all:
            reads, again = read_lines(accurate.text_rec_model, crops), list(range(len(crops)))
        else:
            reads = read_lines(self._fast.paddlex_pipeline.text_rec_model, crops)
            again = [i for i, (text, score, _) in enumerate(reads)
                     if needs_accurate(text, score, float(np.asarray(kept[i])[:, 1].min()) + dy, page_height,
                                       self.min_score, self.header_fraction)]
            for i, r in zip(again, read_lines(accurate.text_rec_model, [crops[i] for i in again])):
                reads[i] = r
        self.last_stats = {"text_lines": len(crops), "read_again": len(again)}
        res = {"rec_texts": [], "rec_scores": [], "rec_polys": [], "text_word": [], "text_word_region": []}
        for (text, score, info), poly in zip(reads, kept):
            if score < accurate.text_rec_score_thresh:
                continue
            words, regions = cal_ocr_word_box(text, poly, info)
            res["text_word"].append(words)
            res["text_word_region"].append(regions)
            res["rec_texts"].append(text)
            res["rec_scores"].append(score)
            res["rec_polys"].append(poly)
        res["rec_boxes"] = convert_points_to_boxes(res["rec_polys"])
        return res


# --- timing --------------------------------------------------------------------------------

def read_log(path: Path = TIMINGS) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_timings(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """The last timing logged for each (variant, page); earlier rows stay in the log but are superseded."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.get("kind") == "page":
            out[(r["variant"], r["page"])] = r
    return out


def cascade_decision(timings: dict, pilot: list[str]) -> dict | None:
    """Whether C goes on past the pilot pages: only if its CPU time on them is below B's. None until both are timed."""
    if not pilot or not all(("B", p) in timings and ("C", p) in timings for p in pilot):
        return None
    total = lambda v, k: round(sum(timings[(v, p)][k] for p in pilot), 1)
    return {"pilot_pages": list(pilot), "B_cpu_s": total("B", "cpu_s"), "C_cpu_s": total("C", "cpu_s"),
            "B_wall_s": total("B", "wall_s"), "C_wall_s": total("C", "wall_s"),
            "continue": total("C", "cpu_s") < total("B", "cpu_s")}


def timed(fn, *args, **kwargs):
    """fn's result, with wall time, this process's CPU time, and the logical cores other processes kept busy."""
    import psutil
    psutil.cpu_percent(interval=None)
    w0, c0 = time.perf_counter(), time.process_time()
    result = fn(*args, **kwargs)
    wall, cpu = time.perf_counter() - w0, time.process_time() - c0
    system = psutil.cpu_percent(interval=None)
    busy = system / 100 * (psutil.cpu_count() or 1)
    return result, {"wall_s": round(wall, 2), "cpu_s": round(cpu, 2), "system_cpu_pct": system,
                    "other_processes_cores": round(max(0.0, busy - cpu / wall), 2) if wall > 0 else None}


def caches() -> dict:
    from ocr.cache import CachedOCR
    return {"A": CachedOCR(PaddleEngine()), "B": CachedOCR(PaddleEngine(**B_OPTIONS)),
            "C": CachedOCR(CascadeEngine(), cache_dir=OUT / "ocr_cache")}


def same_words(a: OCRResult, b: OCRResult) -> bool:
    def key(r):
        return [(w.text, w.left, w.top, w.width, w.height, w.conf) for w in r.words]
    return key(a) == key(b)


def run_timings(pages: list[str], pilot_pages: int = PILOT_PAGES, retime: bool = False) -> None:
    import paddleocr
    from rapidfuzz import fuzz
    OUT.mkdir(parents=True, exist_ok=True)
    timings = {} if retime else latest_timings(read_log())
    pilot = pages[:pilot_pages]

    def wanted(v: str, i: int, page: str) -> bool:
        if (v, page) in timings:
            return False
        if v == "C" and i >= pilot_pages:
            decision = cascade_decision(timings, pilot)
            return decision is None or decision["continue"]
        return True

    if not any(wanted(v, i, p) for i, p in enumerate(pages) for v in VARIANTS):
        print("every variant is already timed on the sample (use --retime to time again)", flush=True)
        return
    c = caches()
    engines = {v: c[v].engine for v in VARIANTS}
    first = config.PAGES / pages[0]
    with Image.open(first) as im:
        width = im.size[0]
    meta = {"kind": "run", "started": time.strftime("%Y-%m-%d %H:%M:%S"), "paddleocr": paddleocr.__version__,
            "logical_cpus": os.cpu_count(),
            "paddle_cpu_threads": os.environ.get("PADDLE_PDX_CPU_NUM_THREADS", "10 (paddlex default)"),
            "retime": retime}
    for v, engine in engines.items():     # load and warm every model before any page is timed
        t0 = time.perf_counter()
        engine.recognise(first, crop=(0, 0, width, 400))
        meta[f"load_and_warm_{v}_s"] = round(time.perf_counter() - t0, 1)
    print(json.dumps(meta), flush=True)
    with open(TIMINGS, "a", encoding="utf-8") as log:
        log.write(json.dumps(meta) + "\n")
        log.flush()
        for i, page in enumerate(pages):
            path = config.PAGES / page
            for v in VARIANTS:
                if not wanted(v, i, page):
                    continue
                if v == "A":      # the pipeline's own cache entry is compared with, never overwritten
                    result, t = timed(engines["A"].recognise, path)
                else:
                    result, t = timed(c[v].recognise, path, refresh=True)
                row = {"kind": "page", "variant": v, "page": page, **t, "engine_s": result.seconds,
                       "words": len(result.words), "mean_conf": round(result.mean_conf, 2)}
                if v == "A" and c["A"].cached(path):
                    before = c["A"].recognise(path)
                    row["same_words_as_cache"] = same_words(result, before)
                    row["text_similarity_to_cache"] = round(fuzz.ratio(result.text, before.text), 2)
                if v == "C":
                    row.update(engines["C"].last_stats)
                timings[(v, page)] = row
                log.write(json.dumps(row) + "\n")
                log.flush()
                print(f"[{i + 1}/{len(pages)}] " + json.dumps(row), flush=True)
            if i + 1 == pilot_pages:
                print("cascade decision after the pilot pages: " + json.dumps(cascade_decision(timings, pilot)),
                      flush=True)


def manual_check(page: str) -> dict:
    """With every line read by the accurate model, does the cascade's reuse of the pipeline's parts give exactly what
    PaddleOCR.predict gives for B? Run once; the answer is saved."""
    path = OUT / "manual_check.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    from rapidfuzz import fuzz
    b = caches()["B"].recognise(config.PAGES / page)
    manual = CascadeEngine(accurate_for_all=True).recognise(config.PAGES / page)
    out = {"page": page, "same_words": same_words(b, manual), "words_predict": len(b.words),
           "words_manual": len(manual.words), "text_similarity": round(fuzz.ratio(b.text, manual.text), 2)}
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


# --- scoring -------------------------------------------------------------------------------

def load_inputs():
    from eval import learning
    from normaliser.normalise import build_matcher
    from normaliser.stock import default_stock_file, load_stock
    from tools.pilot import load_purchasing_report
    manifest = json.loads((config.OUT / "manifest.json").read_text(encoding="utf-8"))
    matches = json.loads((config.GT / "pilot_match.json").read_text(encoding="utf-8"))
    purchase_lines = load_purchasing_report()
    stock = load_stock(default_stock_file())
    matcher, medians = build_matcher(purchase_lines, stock)      # as normaliser.normalise builds it
    return manifest, matches, learning.keyed_lines(purchase_lines), stock, matcher, medians


def cached_results(variant: str, pages: list[str]) -> dict[str, OCRResult]:
    cache = caches()[variant]
    return {p: cache.recognise(config.PAGES / p) for p in pages if cache.cached(config.PAGES / p)}


def agreement(n: dict) -> dict:
    """Counts behind eval.report's item and quantity agreement rates, from its against_pilot tally."""
    a = n["against_pilot"]
    item = a.get("item_agrees", 0) + a.get("item_differs", 0)
    qty = a.get("quantity_agrees", 0) + a.get("quantity_differs", 0)
    return {"aligned": a.get("aligned", 0),
            "item_agrees": a.get("item_agrees", 0), "item_compared": item,
            "item_rate": round(a.get("item_agrees", 0) / item, 4) if item else None,
            "quantity_agrees": a.get("quantity_agrees", 0), "quantity_compared": qty,
            "quantity_rate": round(a.get("quantity_agrees", 0) / qty, 4) if qty else None}


def score(results: dict[str, OCRResult], sample, keyed, stock, matcher, medians) -> dict:
    """Identify, parse and normalise the sample invoices from one variant's text; count agreement with Pilot.

    Each page is identified from the variant's own text (so a misread supplier changes the layout and matching,
    as it would in the pipeline); the manifest's page grouping and the Pilot tie are held fixed."""
    from eval import report
    from ingest.identify import identify
    from ingest.run import header_of
    from models import Page
    from normaliser.normalise import normalise_line
    from parser.engine import parse_invoice

    records, pages, used = {}, {}, []
    for m, manifest_pages in sample:
        if not all(p["image_path"] in results for p in manifest_pages):
            continue
        pairs = []
        for p in manifest_pages:
            r = results[p["image_path"]]
            ident = identify(header_of(r))
            page = Page(page_no=p["page_no"], image_path=p["image_path"], source_pdf="",
                        supplier=ident.supplier.key if ident.supplier else None,
                        supplier_score=round(ident.supplier_score, 1), document_no=ident.document_no,
                        date=ident.date, invoice_id=m["invoice_id"],
                        page_of=f"{ident.page_of[0]}/{ident.page_of[1]}" if ident.page_of else None)
            pairs.append((page, r))
            pages[p["image_path"]] = {"supplier": page.supplier, "document_no": page.document_no, "date": page.date,
                                      "words": len(r.words), "mean_conf": r.mean_conf, "text": r.text}
        records[m["invoice_id"]] = parse_invoice(pairs)
        used.append(m)
    normalised = [{"invoice_id": r.invoice_id, "line_index": i, **normalise_line(line, r.supplier, matcher, medians).to_dict()}
                  for r in records.values() for i, line in enumerate(r.lines)]
    parsed = report.parser(records, used, keyed)
    whole = report.normaliser(records, normalised, used, keyed, stock)
    per_invoice = {}
    for m in used:
        iid = m["invoice_id"]
        per_invoice[iid] = agreement(report.normaliser(
            {iid: records[iid]}, [x for x in normalised if x["invoice_id"] == iid], [m], keyed, stock))
    return {"invoices": len(used), "pages": pages, "per_invoice": per_invoice,
            "parser": {**parsed["total"], "adds_up_rate": parsed["adds_up_rate"]},
            "normaliser": {**agreement(whole), "against_pilot": whole["against_pilot"], "outcomes": whole["outcomes"]}}


def speed(timings: dict, variant: str, pages: list[str]) -> dict | None:
    rows = [timings[(variant, p)] for p in pages if (variant, p) in timings]
    if not rows:
        return None
    walls, cpus = [r["wall_s"] for r in rows], [r["cpu_s"] for r in rows]
    return {"pages_timed": len(rows), "wall_s_total": round(sum(walls), 1), "cpu_s_total": round(sum(cpus), 1),
            "wall_s_per_page": round(sum(walls) / len(rows), 1), "wall_s_median": round(median(walls), 1),
            "cpu_s_per_page": round(sum(cpus) / len(rows), 1),
            "other_processes_cores": round(mean(r.get("other_processes_cores") or 0.0 for r in rows), 1)}


def paired_ratio(timings: dict, variant: str, pages: list[str], key: str) -> float | None:
    """variant's total over A's total, on the pages both were timed on."""
    both = [p for p in pages if (variant, p) in timings and ("A", p) in timings]
    if not both:
        return None
    return round(sum(timings[(variant, p)][key] for p in both) / sum(timings[("A", p)][key] for p in both), 3)


def compare_pages(a_pages: dict, v_pages: dict) -> dict:
    """Recognition and identification per page, against A's."""
    from rapidfuzz import fuzz
    common = [p for p in v_pages if p in a_pages]
    out: dict = {"pages": len(common), "words": sum(v_pages[p]["words"] for p in common),
                 "mean_conf": round(mean(v_pages[p]["mean_conf"] for p in common), 2) if common else None}
    sims = [fuzz.ratio(a_pages[p]["text"], v_pages[p]["text"]) for p in common]
    out["text_similarity_to_A_mean"] = round(mean(sims), 1) if sims else None
    out["text_similarity_to_A_min"] = round(min(sims), 1) if sims else None
    for f in ("supplier", "document_no", "date"):
        out[f"{f}_found"] = sum(1 for p in common if v_pages[p][f])
        out[f"{f}_same_as_A"] = sum(1 for p in common if v_pages[p][f] == a_pages[p][f])
        out[f"{f}_differences"] = [{"page": p, "A": a_pages[p][f], "variant": v_pages[p][f]}
                                   for p in common if v_pages[p][f] != a_pages[p][f]]
    return out


def invoice_changes(a: dict, v: dict) -> dict:
    common = [i for i in v if i in a]
    return {f"{k}_{way}": sorted(i for i in common if (v[i][k] < a[i][k] if way == "fell" else v[i][k] > a[i][k]))
            for k in ("item_agrees", "quantity_agrees") for way in ("fell", "rose")}


def not_lower(v: dict, a: dict, agrees: str, compared: str) -> bool:
    """v's agreement is at least a's both as a count of agreeing lines and as a rate (compared exactly)."""
    return v[agrees] >= a[agrees] and v[agrees] * a[compared] >= a[agrees] * v[compared]


def decide(a: dict, v: dict, ratios: dict, whole_sample: bool) -> dict:
    """The user's rule: adopt only if faster than A and neither item nor quantity agreement drops, on the full sample."""
    faster = all(ratios.get(k) is not None and ratios[k] < 1 for k in ("wall", "cpu"))
    item = not_lower(v["normaliser"], a["normaliser"], "item_agrees", "item_compared")
    qty = not_lower(v["normaliser"], a["normaliser"], "quantity_agrees", "quantity_compared")
    return {"faster_than_A": faster, "item_agreement_not_lower": item, "quantity_agreement_not_lower": qty,
            "scored_on_full_sample": whole_sample, "adopt": faster and item and qty and whole_sample}


def pipeline_outputs_check(sample, keyed, stock, a_score: dict) -> dict:
    """Does variant A, re-run here, give the agreement the pipeline's own data/out files give for these invoices?"""
    from eval import report
    from models import InvoiceRecord
    ids = {m["invoice_id"] for m, _ in sample}
    try:
        invoices = json.loads((config.OUT / "invoices.json").read_text(encoding="utf-8"))
        normalised = json.loads((config.OUT / "normalised.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False}
    records = {d["invoice_id"]: InvoiceRecord.from_dict(d) for d in invoices if d["invoice_id"] in ids}
    got = agreement(report.normaliser(records, [n for n in normalised if n["invoice_id"] in ids],
                                      [m for m, _ in sample], keyed, stock))
    mine = {k: a_score["normaliser"][k] for k in got}
    return {"available": True, "data_out": got, "variant_A_rerun": mine, "same": got == mine}


# --- report --------------------------------------------------------------------------------

ADOPTION_RULE = (
    "Adopt a variant only if, on the full sample, it is faster than A in both wall and CPU time, and neither item "
    "agreement nor quantity agreement with the manager's keyed Pilot lines drops: the number of agreeing lines and the "
    "agreement rate must both be at least A's (eval.report's definitions: lines paired by cost; quantity compared where "
    "the item agrees and the line converted)."
)
ACCELERATOR_NOTE = (
    "Neither was installed or downloaded. What testing them would need on this machine (Windows, Python 3.12; wheel "
    "sizes from PyPI in September 2026):\n\n"
    "- **ONNX Runtime.** The installed paddlex 3.7.2 already has an `onnxruntime` engine that keeps PaddleOCR's own "
    "pre- and post-processing. It needs `onnxruntime` (1.30.0 wheel, 14.3 MB) and the models in ONNX format. PaddleX's "
    "Paddle2ONNX plugin converts the local models (`paddlex --install paddle2onnx`: `paddle2onnx` 2.1.0, 2.0 MB, with "
    "`onnx` 1.22.0, 11.4 MB, and two small helpers, onnxoptimizer and polygraphy). Converted models are about the size "
    "of the Paddle weights: PP-OCRv6_medium_rec 76 MB, PP-OCRv5_mobile_det 5 MB.\n"
    "- **OpenVINO.** paddlex reaches OpenVINO only through its high-performance inference plugin (`ultra-infer-python`), "
    "and paddlex's own list of plugin builds has Linux wheels only. On Windows it would be the `openvino` package "
    "itself (2026.3.1 wheel, 75.8 MB) reading the converted ONNX models, with code of our own in place of paddlex's runner.\n"
    "- **Why either might help.** Paddle's optimised CPU kernels (oneDNN) are switched off here because paddlepaddle "
    "3.3.1 crashes with them on Windows; both runtimes bring their own. Rerunning this trial with the engine as a "
    "fourth variant would judge it by the same rule."
)

SPEED_COLUMNS = ["variant", "scope", "pages timed", "wall s/page", "median wall s", "CPU s/page", "wall vs A",
                 "CPU vs A", "other processes, cores"]
OCR_COLUMNS = ["variant", "scope", "words", "mean conf", "text similarity to A", "supplier same as A",
               "document no. found", "document no. same as A", "date found", "date same as A"]
DOWN_COLUMNS = ["variant", "scope", "invoices", "lines parsed", "stated total found", "adds up", "aligned with Pilot",
                "item agreement", "quantity agreement", "invoices: item fell/rose", "invoices: quantity fell/rose"]


def _num(x, fmt: str = "{:.1f}") -> str:
    return "" if x is None else fmt.format(x)


def _pct(n, d) -> str:
    return "" if not d else f"{n / d:.1%}"


def table_rows(label: str, scope: str, s: dict, sp: dict | None, ratios: dict, o: dict, ch: dict):
    n, pr, sp = s["normaliser"], s["parser"], sp or {}

    def frac(k):
        return f"{o[k]}/{o['pages']}"
    return (
        {"variant": label, "scope": scope, "pages timed": str(sp.get("pages_timed", 0)),
         "wall s/page": _num(sp.get("wall_s_per_page")), "median wall s": _num(sp.get("wall_s_median")),
         "CPU s/page": _num(sp.get("cpu_s_per_page")), "wall vs A": _num(ratios.get("wall"), "{:.2f}"),
         "CPU vs A": _num(ratios.get("cpu"), "{:.2f}"), "other processes, cores": _num(sp.get("other_processes_cores"))},
        {"variant": label, "scope": scope, "words": str(o["words"]), "mean conf": _num(o["mean_conf"], "{:.2f}"),
         "text similarity to A": _num(o["text_similarity_to_A_mean"]), "supplier same as A": frac("supplier_same_as_A"),
         "document no. found": frac("document_no_found"), "document no. same as A": frac("document_no_same_as_A"),
         "date found": frac("date_found"), "date same as A": frac("date_same_as_A")},
        {"variant": label, "scope": scope, "invoices": str(s["invoices"]), "lines parsed": str(pr.get("lines", 0)),
         "stated total found": str(pr.get("stated_total_found", 0)),
         "adds up": f"{pr.get('adds_up', 0)} ({_pct(pr.get('adds_up', 0), pr.get('stated_total_found', 0))})",
         "aligned with Pilot": str(n["aligned"]),
         "item agreement": f"{n['item_agrees']}/{n['item_compared']} ({_pct(n['item_agrees'], n['item_compared'])})",
         "quantity agreement": (f"{n['quantity_agrees']}/{n['quantity_compared']} "
                                f"({_pct(n['quantity_agrees'], n['quantity_compared'])})"),
         "invoices: item fell/rose": f"{len(ch['item_agrees_fell'])}/{len(ch['item_agrees_rose'])}",
         "invoices: quantity fell/rose": f"{len(ch['quantity_agrees_fell'])}/{len(ch['quantity_agrees_rose'])}"},
    )


def recommendation(variants: dict, c_decision: dict | None) -> str:
    out = []
    for v in ("B", "C"):
        e = variants.get(v) or {}
        if not e.get("scored"):
            out.append(f"**{v}: not scored** (no recognition results).")
            continue
        d, n, an, r = e["decision"], e["normaliser"], e["A_on_same_invoices"]["normaliser"], e["speed_vs_A"]
        failed = [text for key, text in (("scored_on_full_sample", "not run on the full sample"),
                                         ("faster_than_A", "not faster than A"),
                                         ("item_agreement_not_lower", "item agreement drops"),
                                         ("quantity_agreement_not_lower", "quantity agreement drops")) if not d[key]]
        s = (f"**{v}: {'adopt' if d['adopt'] else 'do not adopt'}.** On {e['invoices']} invoices ({e['pages']} pages): "
             f"wall time {_num(r['wall'], '{:.2f}')}x and CPU time {_num(r['cpu'], '{:.2f}')}x A's; item agreement "
             f"{n['item_agrees']}/{n['item_compared']} ({_pct(n['item_agrees'], n['item_compared'])}) against A's "
             f"{an['item_agrees']}/{an['item_compared']} ({_pct(an['item_agrees'], an['item_compared'])}); quantity "
             f"agreement {n['quantity_agrees']}/{n['quantity_compared']} ({_pct(n['quantity_agrees'], n['quantity_compared'])}) "
             f"against A's {an['quantity_agrees']}/{an['quantity_compared']} "
             f"({_pct(an['quantity_agrees'], an['quantity_compared'])}).")
        if failed:
            s += " Fails the rule: " + "; ".join(failed) + "."
        if v == "C" and c_decision and not c_decision["continue"]:
            s += (f" C was stopped after the {len(c_decision['pilot_pages'])} pilot pages, where it used "
                  f"{c_decision['C_cpu_s']} CPU seconds against B's {c_decision['B_cpu_s']} "
                  f"({c_decision['C_wall_s']} s wall against {c_decision['B_wall_s']} s).")
        out.append(s)
    adopted = [v for v in ("B", "C") if (variants.get(v) or {}).get("decision", {}).get("adopt")]
    if adopted:
        best = min(adopted, key=lambda v: variants[v]["speed_vs_A"]["cpu"])
        out.append(f"**Overall: adopt {best}** ({DESCRIPTIONS[best]}).")
    else:
        out.append("**Overall: keep the current defaults (A).**")
    return "\n\n".join(out)


def build_results(sample, target_pages: int, pilot_pages: int, keyed, stock, matcher, medians,
                  manual: dict | None) -> dict:
    pages = [p["image_path"] for _, ps in sample for p in ps]
    pilot = pages[:pilot_pages]
    log = read_log()
    timings = latest_timings(log)
    results = {v: cached_results(v, pages) for v in VARIANTS}
    memo: dict = {}

    def scored(v, scope):
        key = (v, tuple(m["invoice_id"] for m, _ in scope))
        if key not in memo:
            memo[key] = score(results[v], scope, keyed, stock, matcher, medians)
        return memo[key]

    variants: dict = {}
    tables: dict = {"speed": [], "ocr": [], "downstream": []}
    for v in VARIANTS:
        scope = [(m, ps) for m, ps in sample if all(p["image_path"] in results[v] for p in ps)]
        if not scope:
            variants[v] = {"description": DESCRIPTIONS[v], "scored": False}
            continue
        whole = len(scope) == len(sample)
        label = "sample" if whole else f"first {len(scope)} invoices"
        scope_pages = [p["image_path"] for _, ps in scope for p in ps]
        a = scored("A", scope)
        entry: dict = {"description": DESCRIPTIONS[v], "scored": True, "scope": label, "invoices": len(scope),
                       "pages": len(scope_pages), "A_on_same_invoices": {"parser": a["parser"], "normaliser": a["normaliser"]}}
        for w in ([v] if whole else ["A", "B", v]):   # a partial run is shown beside A and B on the same invoices
            if not all(p in results[w] for p in scope_pages):
                continue
            sw = scored(w, scope)
            ratios = {"wall": paired_ratio(timings, w, scope_pages, "wall_s"),
                      "cpu": paired_ratio(timings, w, scope_pages, "cpu_s")}
            o, ch, sp = compare_pages(a["pages"], sw["pages"]), invoice_changes(a["per_invoice"], sw["per_invoice"]), \
                speed(timings, w, scope_pages)
            for name, row in zip(("speed", "ocr", "downstream"), table_rows(w, label, sw, sp, ratios, o, ch)):
                tables[name].append(row)
            if w == v:
                entry.update({"speed": sp, "speed_vs_A": ratios, "ocr": o, "parser": sw["parser"],
                              "normaliser": sw["normaliser"], "invoice_changes_vs_A": ch})
                if v != "A":
                    entry["decision"] = decide(a, sw, ratios, whole)
            elif w == "B":
                entry["B_on_same_invoices"] = {"speed": sp, "parser": sw["parser"], "normaliser": sw["normaliser"]}
        variants[v] = entry

    a_full = scored("A", sample)
    checks = []
    a_rows = [timings[("A", p)] for p in pages if "same_words_as_cache" in timings.get(("A", p), {})]
    if a_rows:
        checks.append(f"Re-reading A in this run gave exactly the words, boxes and confidences of its cache entry on "
                      f"{sum(r['same_words_as_cache'] for r in a_rows)} of {len(a_rows)} pages (mean text similarity "
                      f"{mean(r['text_similarity_to_cache'] for r in a_rows):.1f}). The cache was written by ocr.run_all "
                      "with several workers sharing the CPU; this run used paddlex's default thread count. A is scored "
                      "from its cache, as the pipeline is.")
    if manual:
        checks.append("With every line read by the medium model, C's reuse of the pipeline's detector, cropping and word "
                      "boxes " + ("reproduced PaddleOCR.predict exactly" if manual["same_words"] else
                                  "did NOT reproduce PaddleOCR.predict exactly") +
                      f" for B on {manual['page']} ({manual['words_manual']} words against {manual['words_predict']}; "
                      f"text similarity {manual['text_similarity']}).")
    c_rows = [timings[("C", p)] for p in pages if ("C", p) in timings]
    if c_rows:
        lines, again = sum(r.get("text_lines", 0) for r in c_rows), sum(r.get("read_again", 0) for r in c_rows)
        checks.append(f"C read {again} of {lines} text lines ({_pct(again, lines)}) a second time with the medium model.")
    po = pipeline_outputs_check(sample, keyed, stock, a_full)
    if po["available"]:
        d = po["data_out"]
        checks.append("A's scoring here " + ("matches" if po["same"] else "does NOT match") + " the pipeline's own "
                      f"data/out files for these invoices ({d['item_agrees']}/{d['item_compared']} items and "
                      f"{d['quantity_agrees']}/{d['quantity_compared']} quantities agree in data/out).")
    same = {f: sum(1 for _, ps in sample for p in ps if a_full["pages"][p["image_path"]][f] == p.get(f))
            for f in ("supplier", "document_no", "date")}
    checks.append(f"A's identification from the cache matches the manifest on supplier {same['supplier']}, document "
                  f"number {same['document_no']} and date {same['date']} of {len(pages)} pages (the manifest carries a "
                  "grouped page's number over from the page before it).")
    runs = [r for r in log if r.get("kind") == "run"]
    timing_note = ("One process; every model loaded and warmed before timing; on each page the variants ran back to "
                   "back. CPU seconds are this process's own and change little when other programs share the machine; "
                   f"wall seconds do. 'Other processes, cores' is the mean number of the {os.cpu_count()} logical cores "
                   "other programs kept busy meanwhile. Ratios are totals over the same pages. "
                   + (f"paddleocr {runs[-1]['paddleocr']}; Paddle CPU threads {runs[-1]['paddle_cpu_threads']}." if runs else ""))
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sample_rule": SAMPLE_RULE.format(n=target_pages),
        "sample_size": {"invoices": len(sample), "pages": len(pages),
                        "suppliers": len({m.get("supplier") for m, _ in sample})},
        "sample": [{"invoice_id": m["invoice_id"], "supplier": m.get("supplier"), "status": m["status"],
                    "pages": [p["image_path"] for p in ps], "pilot_lines": m.get("pilot_lines")} for m, ps in sample],
        "pilot_pages": pilot,
        "adoption_rule": ADOPTION_RULE,
        "variants": variants,
        "cascade_decision": cascade_decision(timings, pilot),
        "manual_check": manual,
        "pipeline_outputs_check": po,
        "checks": checks,
        "timing_note": timing_note,
        "tables": tables,
        "recommendation": recommendation(variants, cascade_decision(timings, pilot)),
        "accelerators": ACCELERATOR_NOTE,
    }


def markdown(res: dict) -> str:
    from eval.report import table
    size = res["sample_size"]
    parts = [
        "# Speed trial: PaddleOCR settings against agreement with Pilot (generated by eval.trials.speed)",
        "## Sample", res["sample_rule"],
        (f"{size['invoices']} invoices, {size['pages']} pages, {size['suppliers']} suppliers. "
         f"C's pilot pages: {', '.join(res['pilot_pages'])}."),
        "## Variants", "\n".join(f"- **{v}**: {d}" for v, d in DESCRIPTIONS.items()),
        "## Rule", res["adoption_rule"],
        "## Speed", table(res["tables"]["speed"], SPEED_COLUMNS), res["timing_note"],
        "## Recognition and identification, page by page against A", table(res["tables"]["ocr"], OCR_COLUMNS),
        "## Parsing, and agreement with the manager's keyed Pilot lines", table(res["tables"]["downstream"], DOWN_COLUMNS),
        "## Recommendation", res["recommendation"],
        "## Checks", "\n".join(f"- {c}" for c in res["checks"]),
        "## ONNX Runtime and OpenVINO (not tested)", res["accelerators"],
    ]
    return "\n\n".join(parts) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", type=int, default=TARGET_PAGES, help="pages in the sample")
    ap.add_argument("--pilot-pages", type=int, default=PILOT_PAGES,
                    help="pages on which C must use less CPU than B to go on")
    ap.add_argument("--score-only", action="store_true", help="recognise nothing; score cached results")
    ap.add_argument("--retime", action="store_true", help="time every variant again, ignoring logged timings")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, matches, keyed, stock, matcher, medians = load_inputs()
    sample = choose_sample(matches, manifest, keyed, a.pages)
    pages = [p["image_path"] for _, ps in sample for p in ps]
    print(f"sample: {len(sample)} invoices, {len(pages)} pages", flush=True)
    manual_path = OUT / "manual_check.json"
    if a.score_only:
        manual = json.loads(manual_path.read_text(encoding="utf-8")) if manual_path.exists() else None
    else:
        run_timings(pages, a.pilot_pages, a.retime)
        manual = manual_check(pages[0])
    res = build_results(sample, a.pages, a.pilot_pages, keyed, stock, matcher, medians, manual)
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    md = markdown(res)
    (OUT / "results.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
