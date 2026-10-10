"""Find handwriting on printed invoice pages, by ink colour, and crop it for the trial.

The invoices are printed in black. What people add by hand is in pen: delivery ticks beside
quantities, corrected quantities, "EFT", the delivery date, a signature, running notes such as
"SAB x3 = R1089.48" written into the description column. On the binder that pen is blue or red.
So a region of blue or red ink is a handwriting candidate.

Two stamps are coloured too ("POSTED" in red, "PAID" in blue). Their words are recognised as print,
so any ink region overlapping a recognised stamp word is dropped. What remains is not guaranteed to
be handwriting; the trial reports how often it was (detection precision) as well as how well each
engine read it.

    python -m hwr.regions --every 6        # every 6th invoice page -> data/hwr/regions.json + crops
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

import config
from models import OCRResult
from ocr.engines import get_ocr

HWR = config.DATA / "hwr"
STAMP_WORDS = {"posted", "paid", "unpaid"}
MIN_AREA_SHARE = 0.00015     # smaller ink specks than this share of the page are noise
MAX_WIDTH_SHARE = 0.6        # a coloured band wider than this is a border or divider, not writing
PAD = 12                     # pixels of margin around each crop


@dataclass
class Region:
    region_id: str
    page: str
    box: tuple[int, int, int, int]          # left, top, right, bottom in page pixels
    ink: str                                # blue | red
    ink_pixels: int
    paddle_text: str = ""                   # what the page's OCR read inside the region
    paddle_conf: float | None = None
    crop: str = ""
    notes: list[str] = field(default_factory=list)


def ink_masks(bgr: np.ndarray) -> dict[str, np.ndarray]:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    ink = (v > 35) & (v < 235)
    blue = ink & (h >= 95) & (h <= 135) & (s > 70)
    red = ink & ((h <= 8) | (h >= 165)) & (s > 80)
    return {"blue": blue.astype(np.uint8) * 255, "red": red.astype(np.uint8) * 255}


def _join_strokes(mask: np.ndarray, page_height: int) -> np.ndarray:
    k = max(5, page_height // 140)            # about half a handwritten letter's height
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k * 2, k))
    return cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel), kernel)


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def find_regions(image_path: Path, ocr: OCRResult) -> list[Region]:
    bgr = cv2.imread(str(image_path))
    if bgr is None:
        return []
    height, width = bgr.shape[:2]
    # stamp words however decorated: the PAID stamp's logo is read as part of the word ("愛PAID")
    stamps = [(w.left - PAD * 3, w.top - PAD * 3, w.right + PAD * 3, w.bottom + PAD * 3)
              for w in ocr.words if any(s in w.text.lower() for s in STAMP_WORDS)]
    edge = 0.03 * width        # the binder's coloured index tabs run down the page edge
    regions: list[Region] = []
    for ink, mask in ink_masks(bgr).items():
        joined = _join_strokes(mask, height)
        count, _, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
        for i in range(1, count):
            x, y, w, h, _ = stats[i]
            box = (int(x), int(y), int(x + w), int(y + h))
            pixels = int(np.count_nonzero(mask[y:y + h, x:x + w]))
            if pixels < MIN_AREA_SHARE * width * height or w > MAX_WIDTH_SHARE * width or h > 0.25 * height:
                continue
            if x < edge or x + w > width - edge:
                continue
            if any(_overlaps(box, s) for s in stamps):
                continue
            inside = [wd for wd in ocr.words if _overlaps(box, (wd.left, wd.top, wd.right, wd.bottom))
                      and wd.cx >= box[0] and wd.cx <= box[2] and wd.cy >= box[1] and wd.cy <= box[3]]
            inside.sort(key=lambda wd: (wd.top // max(wd.height, 1), wd.left))
            ink_in_print = sum(np.count_nonzero(mask[wd.top:wd.bottom, wd.left:wd.right]) for wd in inside
                               if wd.conf >= 95 and sum(ch.isalpha() for ch in wd.text) >= 3)
            if ink_in_print >= 0.5 * pixels:
                # coloured print, not pen: most of the ink lies inside words the engine read confidently as
                # words (a red letterhead, a blue "TAX INVOICE", a single-word logo). Handwritten figures, dates
                # and scrawls are not read that way, and black print beside a red tick holds little of its ink.
                continue
            regions.append(Region(
                region_id=f"{Path(image_path).stem}_{ink}_{len(regions):02d}", page=Path(image_path).name, box=box,
                ink=ink, ink_pixels=pixels,
                paddle_text=" ".join(wd.text for wd in inside),
                paddle_conf=round(sum(wd.conf for wd in inside) / len(inside), 1) if inside else None,
            ))
    return regions


def save_crop(image_path: Path, region: Region, out_dir: Path) -> str:
    bgr = cv2.imread(str(image_path))
    h, w = bgr.shape[:2]
    x0, y0, x1, y1 = region.box
    crop = bgr[max(0, y0 - PAD):min(h, y1 + PAD), max(0, x0 - PAD):min(w, x1 + PAD)]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{region.region_id}.png"
    cv2.imwrite(str(path), crop)
    return path.name


def sample_pages(manifest: list[dict], every: int) -> list[str]:
    """Every n-th invoice page in binder order: a rule, written down, rather than pages picked by eye."""
    pages = [p["image_path"] for p in manifest if p["kind"] == "invoice"]
    return pages[::every]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--every", type=int, default=6)
    a = ap.parse_args(argv)
    manifest = json.loads((config.OUT / "manifest.json").read_text(encoding="utf-8"))
    ocr = get_ocr("paddle")
    crops = HWR / "crops"
    all_regions = []
    for name in sample_pages(manifest, a.every):
        path = config.PAGES / name
        if not ocr.cached(path):
            continue
        for r in find_regions(path, ocr.recognise(path)):
            r.crop = save_crop(path, r, crops)
            all_regions.append(r)
    HWR.mkdir(parents=True, exist_ok=True)
    (HWR / "regions.json").write_text(json.dumps([asdict(r) for r in all_regions], indent=1), encoding="utf-8")
    pages = len({r.page for r in all_regions})
    print(f"{len(all_regions)} ink regions on {pages} sampled pages "
          f"({sum(r.ink == 'blue' for r in all_regions)} blue, {sum(r.ink == 'red' for r in all_regions)} red) -> {HWR}")


if __name__ == "__main__":
    main()
