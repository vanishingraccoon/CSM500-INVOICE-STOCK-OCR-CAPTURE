"""Default data paths and the Tesseract executable location."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("INVOICE_DATA", ROOT / "data"))
PAGES = DATA / "pages"
OCR_CACHE = DATA / "ocr_cache"
PILOT = DATA / "pilot"
GT = DATA / "gt"
OUT = DATA / "out"
TESSERACT = os.environ.get("TESSERACT", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
