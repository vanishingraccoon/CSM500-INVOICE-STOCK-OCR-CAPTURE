"""Trial: does flattening the page photograph before recognition help?

Library under trial: page-dewarp (MIT), https://github.com/lmmx/page-dewarp. It fits a cubic sheet model
to the text lines of a photographed page and remaps the image flat, then thresholds it to black and white.

Runs in .venv-trial (kept apart from the PaddleOCR environment):

    .venv-trial/Scripts/python -m eval.trials.dewarp

Pages: the eight with the lowest PaddleOCR confidence in the August run, plus the three highest as a
control (a method that damages good pages is no good). Each page is read with Tesseract before and after
dewarping, same engine and settings, and mean word confidence and words recognised are compared.
PaddleOCR is not rerun here (20-60 s a page); a clear Tesseract gain would justify rereading those pages.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ocr.tesseract import TesseractEngine

OUT = ROOT / "data" / "trials" / "dewarp"


def read(engine: TesseractEngine, path: Path) -> dict:
    r = engine.recognise(path)
    confident = [w for w in r.words if w.conf >= 0]
    return {"words": len(r.words), "mean_conf": round(sum(w.conf for w in confident) / len(confident), 1) if confident else 0.0,
            "seconds": r.seconds}


def main():
    runs = [json.loads(line) for line in (ROOT / "data" / "ocr_cache" / "run_paddle.jsonl").read_text(encoding="utf-8").splitlines()]
    runs = [r for r in runs if "error" not in r]
    worst = sorted(runs, key=lambda r: r["mean_conf"])[:8]
    control = sorted(runs, key=lambda r: -r["mean_conf"])[:3]
    exe = Path(sys.executable).parent / "page-dewarp.exe"
    engine = TesseractEngine()
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for group, pages in (("worst", worst), ("control", control)):
        for run in pages:
            src = ROOT / "data" / "pages" / run["page"]
            work = OUT / src.stem
            work.mkdir(exist_ok=True)
            t0 = time.perf_counter()
            proc = subprocess.run([str(exe), str(src), "-o", str(work)], capture_output=True, text=True, timeout=900,
                                  check=False)
            seconds = round(time.perf_counter() - t0, 1)
            produced = sorted(p for p in work.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
            row = {"group": group, "page": run["page"], "paddle_conf": run["mean_conf"], "dewarp_seconds": seconds,
                   "before": read(engine, src)}
            if proc.returncode != 0 or not produced:
                row["error"] = (proc.stderr or proc.stdout)[-300:]
            else:
                row["after"] = read(engine, produced[-1])
                row["output"] = str(produced[-1].relative_to(ROOT))
            rows.append(row)
            print(json.dumps(row), flush=True)

    (OUT / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print("\npage | group | Paddle conf | Tesseract conf before -> after | words before -> after")
    for r in rows:
        after = r.get("after", {})
        print(f"{r['page']} | {r['group']} | {r['paddle_conf']} | {r['before']['mean_conf']} -> {after.get('mean_conf', 'failed')}"
              f" | {r['before']['words']} -> {after.get('words', '-')}")


if __name__ == "__main__":
    main()
