"""Recognise every page image into the cache, several pages at a time.

Each worker process loads its own engine once and then takes pages from a shared list,
so model loading is paid per worker, not per page. Pages already cached are skipped,
which makes the run resumable after an interruption.

    python -m ocr.run_all --engine paddle --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import config

_ocr = None


def _init(engine_name: str, threads: int):
    global _ocr
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ["OMP_NUM_THREADS"] = str(threads)
    from ocr.engines import get_ocr
    _ocr = get_ocr(engine_name)
    if engine_name == "paddle":
        _ocr.engine.options["cpu_threads"] = threads


def _one(path: str) -> dict:
    t0 = time.perf_counter()
    r = _ocr.recognise(Path(path))
    return {"page": Path(path).name, "words": len(r.words), "mean_conf": round(r.mean_conf, 1),
            "engine_seconds": r.seconds, "wall": round(time.perf_counter() - t0, 1)}


def pages_to_do(pages: list[Path], ocr) -> list[Path]:
    """Pages with no cached result under the settings the pipeline reads with."""
    return [p for p in pages if not ocr.cached(p)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", default="paddle")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--pattern", default="*.jpg")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)

    from ocr.engines import get_ocr
    probe = get_ocr(a.engine)   # builds the engine without loading its models: Paddle loads them on first use

    pages = sorted(config.PAGES.glob(a.pattern))
    todo = pages_to_do(pages, probe)
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(pages)} pages, {len(pages) - len(todo)} cached, {len(todo)} to do", flush=True)
    if not todo:
        return

    threads = max(1, (os.cpu_count() or 4) // a.workers)
    log = config.OCR_CACHE / f"run_{a.engine}.jsonl"
    t0 = time.perf_counter()
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(a.engine, threads)) as ex, \
            open(log, "a", encoding="utf-8") as f:
        futures = {ex.submit(_one, str(p)): p for p in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            p = futures[fut]
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001 - one bad page must not stop the batch
                rec = {"page": p.name, "error": f"{type(e).__name__}: {e}"}
            f.write(json.dumps(rec) + "\n")
            f.flush()
            elapsed = time.perf_counter() - t0
            eta = elapsed / i * (len(todo) - i)
            print(f"[{i}/{len(todo)}] {rec} eta {eta/60:.0f} min", flush=True)


if __name__ == "__main__":
    sys.exit(main())
