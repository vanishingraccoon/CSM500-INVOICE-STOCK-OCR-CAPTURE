"""Shared pieces for the laptop server tests: a fake recognition engine behind the real OCR cache, photographs
made of noise, a small stock export, invoice word layouts, and a paired phone. Every figure is substituted;
the layout copies an Aero Power page (see test_parser.py)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from app import context, pairing, worker
from app.settings import Settings
from models import OCRResult, Word
from ocr.base import OCREngine
from ocr.cache import CachedOCR

SHOP, OTHER_SHOP = "testshop", "othershop"
OWNER = "run1:test:1:r0"

STOCK_CSV = (
    "Textbox1,Textbox2\r\n"
    "News Cafe - Testshop,Stock Valuation for 13-09-2026\r\n"
    "\r\n"
    "Cat,Total_label,Total__label,Total__Value_Y,Total1,prod,StockItem,BackStock,FrontStock,StockOnHand,UnitCost,Total,Textbox40\r\n"
    "dg - dry goods,Total,,50.00,50.00,1189,honey (LITRE),1.00,0.00,1.00,2.00,2.00,999.00\r\n"
    "sp - spirits,Total,,50.00,50.00,5001,tequila mix (750ML),1.00,0.00,1.00,3.00,3.00,999.00\r\n"
    "dg - dry goods,Total,,50.00,50.00,3001,flour cake (KILOGRAM),1.00,0.00,1.00,4.00,4.00,999.00\r\n"
    "dg - dry goods,Total,,50.00,50.00,4001,sauce bbq (LITRE),1.00,0.00,1.00,5.00,5.00,999.00\r\n"
)


def install_shops(s: Settings) -> Settings:
    s.ensure_dirs()
    s.pilot_dir.mkdir(parents=True, exist_ok=True)
    for shop in (SHOP, OTHER_SHOP):
        (s.pilot_dir / f"stock_valuation_{shop}_2026-09-13.csv").write_text(STOCK_CSV, encoding="utf-8")
    s.shops_file.write_text(json.dumps([
        {"shop_id": SHOP, "name": "News Cafe Testshop", "stock_glob": f"stock_valuation_{SHOP}_*.csv"},
        {"shop_id": OTHER_SHOP, "name": "News Cafe Othershop", "stock_glob": f"stock_valuation_{OTHER_SHOP}_*.csv"},
    ]), encoding="utf-8")
    context.reset()
    return s


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def photo_bytes(seed: int, blurred: bool = False, size: tuple[int, int] = (1200, 1600)) -> bytes:
    """A grey page with dark text-like bars (passes every quality check), or a flat blur (fails them)."""
    import cv2
    width, height = size
    img = np.full((height, width), 128 if blurred else 205, np.uint8)
    if not blurred:
        rng = np.random.default_rng(seed)
        for _ in range(500):
            x, y = int(rng.integers(0, width - 60)), int(rng.integers(0, height - 12))
            img[y:y + 10, x:x + int(rng.integers(20, 60))] = 25
    else:
        img[0, 0] = seed % 256          # a different file for each seed
    _ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes()


def _w(text: str, left: int, top: int) -> Word:
    return Word(text, left, top, max(10, len(text) * 14), 30, 99.0)


def invoice_words(number: str | None = "102595", date: str | None = "2026/08/06",
                  lines=(("Tequila Mix (750ml)", 2, 100.00),), supplier=("Aero", "Power", "Trading"),
                  totals: tuple[float, float] | None = None, show_totals: bool = True,
                  page_of: tuple[int, int] | None = None) -> list[Word]:
    """One invoice page: letterhead, a Document No / Date block, an Aero Power style table and a TOTALS row.
    totals=(excl, vat) overrides the printed totals; by default they are the lines' sums."""
    words = [_w(t, 100 + i * 150, 100) for i, t in enumerate(supplier)]
    if page_of:
        words += [_w("Page", 1500, 160), _w(str(page_of[0]), 1580, 160), _w("of", 1610, 160), _w(str(page_of[1]), 1650, 160)]
    words += [Word("Document", 210, 300, 144, 30, 99.0), Word("No", 377, 300, 36, 30, 99.0), Word("Date", 1216, 300, 72, 30, 99.0)]
    if date:
        words.append(Word(date, 1170, 323, 180, 30, 99.0))
    if number:
        words.append(Word(number, 256, 365, max(10, len(number) * 18), 30, 99.0))
    top = 1443
    for text, left, right in (("Stock", 99, 147), ("Code", 165, 213), ("Description", 366, 480), ("Qty", 868, 894),
                              ("Unit", 922, 962), ("Price", 975, 1022), ("Nett", 1162, 1203), ("Price", 1209, 1256),
                              ("VAT", 1333, 1368), ("Incl.", 1462, 1515), ("Price", 1521, 1567)):
        words.append(Word(text, left, top, right - left, 30, 99.0))
    excl_sum = vat_sum = 0.0
    for k, (description, quantity, price) in enumerate(lines):
        y = top + 65 + k * 60
        total = round(quantity * price, 2)
        vat = round(total * 0.15, 2)
        excl_sum, vat_sum = excl_sum + total, vat_sum + vat
        words.append(Word(f"10000{k + 1}", 95, y, 66, 30, 99.0))
        x = 365
        for part in description.split():
            words.append(Word(part, x, y, len(part) * 11, 30, 99.0))
            x += len(part) * 11 + 10
        words += [Word(f"{quantity:g}", 884, y, 9 * len(f"{quantity:g}"), 30, 99.0),
                  Word(f"{price:.2f}", 959, y, 67, 30, 99.0), Word(f"{total:.2f}", 1201, y, 62, 30, 99.0),
                  Word(f"{vat:.2f}", 1324, y, 47, 30, 99.0), Word(f"{total + vat:.2f}", 1511, y, 60, 30, 99.0)]
    if show_totals:
        excl, vat = totals or (round(excl_sum, 2), round(vat_sum, 2))
        y = top + 65 + len(lines) * 60 + 200
        words += [Word("TOTALS", 740, y, 90, 30, 99.0), Word(f"{excl:.2f}", 1150, y, 80, 30, 99.0),
                  Word(f"{vat:.2f}", 1300, y, 70, 30, 99.0), Word(f"{excl + vat:.2f}", 1500, y, 70, 30, 99.0)]
    return words


class FakeEngine(OCREngine):
    """Returns prepared words for a photograph, looked up by the photograph's SHA-256."""
    name = "fake"

    def __init__(self):
        self.pages: dict[str, list[Word]] = {}
        self.fail: set[str] = set()
        self.calls = 0

    def version(self) -> str:
        return "fake 1"

    def recognise(self, image_path: Path, crop=None, psm: int = 3) -> OCRResult:
        self.calls += 1
        digest = sha(Path(image_path).read_bytes())
        if digest in self.fail:
            raise RuntimeError("the engine stopped on this photograph")
        return OCRResult(str(image_path), self.name, self.version(), 0.01, 1760, 2720, list(self.pages.get(digest, [])))


FILE_ENGINE_ENV = "INVOICE_TEST_OCR_WORDS"


class FileEngine(OCREngine):
    """A fake engine for real reader processes (OCR_ENGINE=serverkit:FileEngine): a photograph's words are read from
    <folder>/<sha256>.json, the folder named by INVOICE_TEST_OCR_WORDS, which a spawned process inherits."""
    name = "fakefile"

    def version(self) -> str:
        return "fake file 1"

    def recognise(self, image_path: Path, crop=None, psm: int = 3) -> OCRResult:
        path = Path(os.environ[FILE_ENGINE_ENV]) / f"{sha(Path(image_path).read_bytes())}.json"
        words = [Word.from_dict(w) for w in json.loads(path.read_text(encoding="utf-8"))] if path.exists() else []
        return OCRResult(str(image_path), self.name, self.version(), 0.01, 1760, 2720, words)


def write_words(folder: Path, data: bytes, words: list[Word]) -> bytes:
    """Prepare what FileEngine reads on this photograph."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sha(data)}.json").write_text(json.dumps([w.to_dict() for w in words]), encoding="utf-8")
    return data


class Reader:
    """The background queue, run by hand in the test's own thread."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = FakeEngine()
        self.ocr = CachedOCR(self.engine, settings.ocr_cache)

    def page(self, data: bytes, words: list[Word]) -> bytes:
        self.engine.pages[sha(data)] = words
        return data

    def run(self, limit: int = 100) -> list[str]:
        outcomes = []
        while len(outcomes) < limit:
            outcome = worker.run_once(context.connection(), self.settings, context.keys(), self.ocr, OWNER)
            if outcome is None:
                break
            outcomes.append(outcome)
        return outcomes


class Phone:
    """A paired phone talking to the test client."""

    def __init__(self, client, shop: str = SHOP, name: str = "Test phone"):
        code = pairing.issue_code(context.connection(), shop)["code"]
        self.client = client
        self.device = client.post("/api/v1/pair", json={"pair_code": code, "device_name": name}).get_json()
        self.headers = {"Authorization": f"Bearer {self.device['token']}"}

    def _h(self, extra=None) -> dict:
        return {**self.headers, **(extra or {})}

    def get(self, path, headers=None):
        return self.client.get(path, headers=self._h(headers))

    def post(self, path, json=None):
        return self.client.post(path, json=json, headers=self.headers)

    def put(self, path, json=None):
        return self.client.put(path, json=json, headers=self.headers)

    def patch(self, path, json=None):
        return self.client.patch(path, json=json, headers=self.headers)

    def delete(self, path):
        return self.client.delete(path, headers=self.headers)

    def new_batch(self, key: str = "batch-1", pages: int | None = None) -> str:
        return self.post("/api/v1/batches", json={"client_batch_key": key, "page_count": pages}).get_json()["batch_id"]

    def upload(self, batch_id: str, key: str, data: bytes, ordinal: int | None = None, replaces: str | None = None,
               digest: str | None = None, quality: dict | None = None):
        headers = self._h({"Content-Type": "image/jpeg", "X-Content-SHA256": digest or sha(data)})
        if ordinal is not None:
            headers["X-Photo-Ordinal"] = str(ordinal)
        if replaces:
            headers["X-Replaces-Photo"] = replaces
        if quality is not None:
            headers["X-Quality"] = json.dumps(quality)
        return self.client.put(f"/api/v1/batches/{batch_id}/photos/{key}", data=data, headers=headers)

    def batch(self, batch_id: str) -> dict:
        return self.get(f"/api/v1/batches/{batch_id}").get_json()

    def scan(self, reader: Reader, pages: list[tuple[bytes, list[Word]]], key: str = "batch-1") -> dict:
        """A whole scanner session: upload every page in order, complete, read everything, return the batch."""
        batch_id = self.new_batch(key, len(pages))
        for ordinal, (data, words) in enumerate(pages, start=1):
            if words is not None:
                reader.page(data, words)
            assert self.upload(batch_id, f"{key}-p{ordinal}", data, ordinal).status_code == 201
        self.post(f"/api/v1/batches/{batch_id}/complete")
        reader.run()
        return self.batch(batch_id)
