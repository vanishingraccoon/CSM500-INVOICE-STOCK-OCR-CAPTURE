"""White scanner paper is not camera glare; unreadable PDFs still need a retake."""
import io

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFilter

from app import context, db, quality, reading
from serverkit import SHOP, invoice_words


def scan_image(path, *, ink=20, blur=0, blank=False):
    image = Image.new("L", (600, 800), 255)
    draw = ImageDraw.Draw(image)
    if not blank:
        for y in range(120, 640, 20):
            for x in range(60, 500, 30):
                draw.rectangle((x, y, x + 7, y + 3), fill=ink)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    image.save(path)
    return image


def test_clear_white_scan_survives_real_pdf_worker(api_client, reader, tmp_path):
    path = tmp_path / "white-scan.png"
    scan_image(path)
    with pymupdf.open() as document:
        page = document.new_page(width=432, height=576)
        page.insert_image(page.rect, stream=path.read_bytes())
        source = document.tobytes()
    response = api_client.post("/upload-pdf", data={"shop_id": SHOP, "pdf": (io.BytesIO(source), "scan.pdf")},
                               content_type="multipart/form-data")
    assert response.status_code == 303
    conn = context.connection()
    photo = db.one(conn, "SELECT * FROM photos")
    reader.page((context.settings().photos / photo["storage_path"]).read_bytes(), invoice_words("905001"))
    assert reader.run() == ["read"]
    state = reading.load_batch(conn, photo["batch_id"])
    assessment = next(iter(state.photos.values())).assessment()
    assert assessment["ok"], assessment
    # Simulate this PDF having been processed by v11's camera-only check.
    stored = context.settings().photos / photo["storage_path"]
    original_image = stored.read_bytes()
    camera_quality = quality.measure(stored)
    conn.execute("UPDATE photos SET quality = ? WHERE photo_id = ?",
                 (db.dumps({"server": camera_quality}), photo["photo_id"]))
    reading.update_batch(conn, photo["batch_id"], context.keys())
    calls = reader.engine.calls
    recheck = api_client.post(response.location + "/recheck-pdf-quality")
    assert recheck.status_code == 303
    state = reading.load_batch(conn, photo["batch_id"])
    assert next(iter(state.photos.values())).assessment()["ok"]
    assert reader.engine.calls == calls
    assert stored.read_bytes() == original_image
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'pdf_quality_rechecked'").fetchone()[0] == 1
    assert api_client.post(response.location + "/submit").status_code == 303
    snapshot = db.one(conn, "SELECT snapshot FROM reports")["snapshot"]
    assert api_client.post(response.location + "/recheck-pdf-quality").status_code == 409
    assert db.one(conn, "SELECT snapshot FROM reports")["snapshot"] == snapshot


@pytest.mark.parametrize("kind", ["faint", "blur", "blank"])
def test_unreadable_scan_still_fails_pdf_quality(tmp_path, kind):
    path = tmp_path / "unreadable.png"
    scan_image(path, ink=240 if kind == "faint" else 20, blur=8 if kind == "blur" else 0,
               blank=kind == "blank")
    assessment = quality.assess(quality.measure(path, scanned_pdf=True), None, words=100)
    assert not assessment["ok"]
    assert any(("faint" if kind == "faint" else "blurred") in reason for reason in assessment["problems"])


def test_white_scan_keeps_camera_exposure_checks(tmp_path):
    path = tmp_path / "phone.png"
    scan_image(path)
    problems = quality.problems(quality.measure(path))
    assert "washed_out" in problems and "glare" in problems


def test_phone_cannot_claim_pdf_profile():
    assessment = quality.assess(None, {"profile": "scanned_pdf", "mean_luma": 250, "glare_fraction": 0.5}, words=100)
    assert not assessment["ok"]
    assert any("washed out" in reason for reason in assessment["problems"])


def test_pdf_word_count_still_rejects_blank_recognition(tmp_path):
    path = tmp_path / "scan.png"
    scan_image(path)
    assessment = quality.assess(quality.measure(path, scanned_pdf=True), None, words=2)
    assert not assessment["ok"]
    assert any("only 2 words" in reason for reason in assessment["problems"])
