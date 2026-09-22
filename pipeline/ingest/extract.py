"""Step 1 of ingest: pull each page's embedded scan out of the binder PDF.

The binder PDF wraps one JPEG per page. The JPEG stream is written out as-is, so no
recompression loss is introduced before recognition. Pages that are not a single
embedded image are rendered at 300 dpi instead.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf

import config


def extract(pdf: Path, out_dir: Path = config.PAGES, prefix: str | None = None) -> list[Path]:
    pdf = Path(pdf)
    prefix = prefix or _slug(pdf.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            images = page.get_images()
            if len(images) == 1:
                img = doc.extract_image(images[0][0])
                ext = "jpg" if img["ext"] in ("jpeg", "jpg") else img["ext"]
                path = out_dir / f"{prefix}_p{i:03d}.{ext}"
                if not path.exists():
                    path.write_bytes(img["image"])
            else:
                path = out_dir / f"{prefix}_p{i:03d}.png"
                if not path.exists():
                    page.get_pixmap(dpi=300).save(path)
            written.append(path)
    return written


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in s).strip("_")[:24]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--prefix")
    a = ap.parse_args()
    paths = extract(Path(a.pdf), prefix=a.prefix)
    print(f"{len(paths)} pages -> {config.PAGES}")
