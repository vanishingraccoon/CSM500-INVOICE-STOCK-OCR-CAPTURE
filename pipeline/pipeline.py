"""Regenerate every output from the binder PDF and the Pilot exports, one step after another.

    python pipeline.py                  # ingest -> parser -> normalise -> checks -> ground truth -> report
    python pipeline.py --ocr            # first extract pages and recognise any not yet in the OCR cache (slow)
    python pipeline.py --hwr            # also run the handwriting trial (needs .venv-hwr)
    python pipeline.py --from checks    # start part-way through

Each step is a module that can also be run on its own. Recognition results are cached, so a run
without --ocr repeats nothing slow. The run stops at the first step that fails.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_PDF = ROOT.parent.parent / "08 Invoice and Pilot data" / "NEWSCAFE EASTGATE AUG INVOICES.pdf"
HWR_PYTHON = ROOT / ".venv-hwr" / "Scripts" / "python.exe"


def steps(args) -> list[tuple[str, list[str], Path]]:
    py = Path(sys.executable)
    out = []
    if args.ocr:
        out += [("extract", ["-m", "ingest.extract", str(args.pdf), "--prefix", "aug"], py),
                ("ocr", ["-m", "ocr.run_all", "--engine", "paddle", "--workers", str(args.workers)], py)]
    out += [("ingest", ["-m", "ingest.run"], py),
            ("parser", ["-m", "parser.run"], py),
            ("normalise", ["-m", "normaliser.normalise"], py),
            ("checks", ["-m", "checks.run"], py),
            ("groundtruth", ["-m", "tools.groundtruth"], py)]
    if args.hwr:
        out += [("hwr-regions", ["-m", "hwr.regions", "--every", "6"], py),
                ("hwr-trocr", ["-m", "hwr.trocr"], HWR_PYTHON),
                ("hwr-trial", ["-m", "hwr.trial"], py)]
    out += [("report", ["-m", "eval.report"], py)]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ocr", action="store_true", help="extract pages and run recognition first")
    ap.add_argument("--hwr", action="store_true", help="run the handwriting trial")
    ap.add_argument("--from", dest="start", help="name of the first step to run")
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)

    plan = steps(args)
    names = [n for n, _, _ in plan]
    if args.start:
        if args.start not in names:
            ap.error(f"--from must be one of: {', '.join(names)}")
        plan = plan[names.index(args.start):]

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True")
    for name, cmd, python in plan:
        if not python.exists():
            print(f"[{name}] interpreter not found: {python}", file=sys.stderr)
            return 1
        t0 = time.perf_counter()
        print(f"[{name}] {' '.join(cmd)}", flush=True)
        result = subprocess.run([str(python), *cmd], cwd=ROOT, env=env, check=False)   # exit code reported below
        print(f"[{name}] finished in {time.perf_counter() - t0:.0f}s with exit code {result.returncode}", flush=True)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
