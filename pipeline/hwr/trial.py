"""Score the handwriting trial: how well is handwriting found, and how well is it read?

Inputs
  data/hwr/regions.json        ink regions found on the sampled pages, with PaddleOCR's reading
  data/hwr/trocr.json          TrOCR's reading of each crop
  data/gt/handwriting.json     the transcription of each crop:
        {region_id: {"is_handwriting": bool, "kind": "tick|quantity|figure|date|word|note|signature|mixed",
                     "legible": bool, "text": "what is written, exactly"}}

Reported separately from printed text, as the objective requires:
  detection precision     share of ink regions that really are handwriting (stamps, coloured print excluded)
  per engine, per kind    character error rate (edit distance / length of the transcription) and
                          exact-match rate, over legible regions whose kind carries text

Ticks and signatures carry no text to read and are counted, not scored. A poor figure is a
finding: it says what a person must still check by eye.

    python -m hwr.trial
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz.distance import Levenshtein

import config

TEXT_KINDS = {"quantity", "figure", "date", "word", "note", "mixed"}


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def cer(hypothesis: str, reference: str) -> float:
    ref = normalise(reference)
    return Levenshtein.distance(normalise(hypothesis), ref) / max(1, len(ref))


def score(regions: list[dict], trocr: dict, labels: dict) -> dict:
    labelled = [r for r in regions if r["region_id"] in labels]
    handwriting = [r for r in labelled if labels[r["region_id"]]["is_handwriting"]]
    kinds = Counter(labels[r["region_id"]]["kind"] for r in handwriting)
    scorable = [r for r in handwriting
                if labels[r["region_id"]].get("legible") and labels[r["region_id"]]["kind"] in TEXT_KINDS]

    engines: dict[str, dict] = {}
    for engine in ("paddle", "trocr"):
        per_kind: dict[str, list] = defaultdict(list)
        for r in scorable:
            gt = labels[r["region_id"]]
            hyp = r["paddle_text"] if engine == "paddle" else trocr.get(r["region_id"], {}).get("text")
            if hyp is None:
                continue
            per_kind[gt["kind"]].append((cer(hyp, gt["text"]), normalise(hyp) == normalise(gt["text"])))
            per_kind["all"].append(per_kind[gt["kind"]][-1])
        engines[engine] = {
            kind: {"regions": len(v), "mean_cer": round(sum(c for c, _ in v) / len(v), 3),
                   "exact_match_rate": round(sum(e for _, e in v) / len(v), 3)}
            for kind, v in per_kind.items() if v
        }
    paddle_read_something = sum(1 for r in handwriting if r["paddle_text"].strip())
    return {
        "sampled_pages": len({r["page"] for r in regions}),
        "ink_regions": len(regions),
        "labelled": len(labelled),
        "handwriting": len(handwriting),
        "detection_precision": round(len(handwriting) / len(labelled), 3) if labelled else None,
        "handwriting_kinds": dict(kinds),
        "legible_text_regions_scored": len(scorable),
        "paddle_read_anything_share": round(paddle_read_something / len(handwriting), 3) if handwriting else None,
        "engines": engines,
    }


def main():
    hwr = config.DATA / "hwr"
    result = score(_read(hwr / "regions.json", []), _read(hwr / "trocr.json", {}),
                   _read(config.GT / "handwriting.json", {}))
    out = config.OUT / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hwr_trial.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
