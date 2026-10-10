"""Run TrOCR (a handwriting recognition model) over the cropped ink regions.

Runs in its own environment (.venv-hwr), so PyTorch and Transformers cannot disturb the PaddleOCR
environment the rest of the pipeline uses:

    .venv-hwr/Scripts/python -m hwr.trocr            # reads data/hwr/regions.json, writes data/hwr/trocr.json

The model is microsoft/trocr-base-handwritten, trained on single lines of English handwriting (IAM).
Invoice handwriting is ticks, figures, dates and signatures, often not a line of words at all; the
trial measures how far that mismatch matters rather than assuming it.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HWR = ROOT / "data" / "hwr"
MODEL = "microsoft/trocr-base-handwritten"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)

    import torch
    from PIL import Image
    from transformers import (
        RobertaTokenizer,
        VisionEncoderDecoderModel,
        ViTImageProcessor,
    )

    # Image processor and tokenizer are loaded separately: transformers 5.17 cannot build this model's
    # fast tokenizer here ("Couldn't instantiate the backend tokenizer"), while the slow RoBERTa
    # tokenizer the model was trained with loads directly.
    image_processor = ViTImageProcessor.from_pretrained(a.model)
    tokenizer = RobertaTokenizer.from_pretrained(a.model)

    class _Processor:
        def __call__(self, images, return_tensors):
            return image_processor(images=images, return_tensors=return_tensors)

        def batch_decode(self, ids, skip_special_tokens):
            return tokenizer.batch_decode(ids, skip_special_tokens=skip_special_tokens)

    processor = _Processor()
    model = VisionEncoderDecoderModel.from_pretrained(a.model).eval()

    regions = json.loads((HWR / "regions.json").read_text(encoding="utf-8"))
    out_path = HWR / "trocr.json"
    done = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    todo = [r for r in regions if r["region_id"] not in done][: a.limit]
    print(f"{len(regions)} regions, {len(done)} already read, {len(todo)} to read with {a.model}", flush=True)
    for k, r in enumerate(todo, 1):
        image = Image.open(HWR / "crops" / r["crop"]).convert("RGB")
        t0 = time.perf_counter()
        with torch.no_grad():
            pixels = processor(images=image, return_tensors="pt").pixel_values
            ids = model.generate(pixels, max_new_tokens=32)
        text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        done[r["region_id"]] = {"text": text, "seconds": round(time.perf_counter() - t0, 2), "model": a.model}
        if k % 10 == 0 or k == len(todo):
            out_path.write_text(json.dumps(done, indent=1), encoding="utf-8")
            print(f"[{k}/{len(todo)}] {r['region_id']}: {text!r}", flush=True)
    out_path.write_text(json.dumps(done, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
