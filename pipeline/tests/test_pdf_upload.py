"""PDF uploads use the real queue and refuse partial imports and incomplete reports."""
import io
import pymupdf as fitz
import pytest

from app import context, db, pdf_upload
from serverkit import SHOP, invoice_words, photo_bytes


def scanned_pdf(count=3, encrypted=False):
    with fitz.open() as document:
        for n in range(count):
            page = document.new_page(width=420, height=560)
            page.insert_image(page.rect, stream=photo_bytes(900 + n, size=(900, 1200)))
        if encrypted:
            return document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
        return document.tobytes()


def upload(client, content, shop_id=SHOP):
    return client.post("/upload-pdf", data={"shop_id": shop_id, "pdf": (io.BytesIO(content), "invoices.pdf")},
                       content_type="multipart/form-data")


def test_pdf_pages_reach_existing_grouping_and_report(api_client, reader, server_settings):
    source = scanned_pdf()
    response = upload(api_client, source)
    assert response.status_code == 303
    conn = context.connection()
    batch = db.one(conn, "SELECT * FROM batches")
    photos = db.all_rows(conn, "SELECT * FROM photos ORDER BY ordinal")
    assert [p["ordinal"] for p in photos] == [1, 2, 3]
    assert batch["page_count"] == 3
    assert (server_settings.photos / batch["batch_id"] / "source.pdf").read_bytes() == source
    for n, photo in enumerate(photos):
        words = invoice_words("901001" if n < 2 else "901002", page_of=(n + 1, 2) if n < 2 else None)
        reader.page((server_settings.photos / photo["storage_path"]).read_bytes(), words)
    submit_path = f"/batches/{batch['batch_id']}/submit"
    assert api_client.post(submit_path).status_code == 409
    assert reader.run() == ["read"] * 3
    assert conn.execute("SELECT COUNT(*) FROM invoice_pages").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 2
    result = api_client.post(submit_path)
    assert result.status_code == 303
    page = api_client.get(result.location)
    assert b"PDF to print" in page.data
    assert api_client.get(f"/batches/{batch['batch_id']}/report.pdf").data.startswith(b"%PDF")


@pytest.mark.parametrize("kind", ["invalid", "encrypted", "too_many", "oversized", "unknown_shop"])
def test_refused_pdf_creates_no_jobs_or_batch(api_client, server_settings, monkeypatch, kind):
    content, shop_id = scanned_pdf(1), SHOP
    if kind == "invalid":
        content = b"this is not a PDF"
    elif kind == "encrypted":
        content = scanned_pdf(1, encrypted=True)
    elif kind == "too_many":
        monkeypatch.setattr(pdf_upload, "MAX_PAGES", 0)
    elif kind == "oversized":
        monkeypatch.setattr(pdf_upload, "MAX_PDF_BYTES", 20)
    else:
        shop_id = "missing-shop"
    response = upload(api_client, content, shop_id)
    assert response.status_code in (400, 413)
    assert b"Upload scanned PDF" in response.data
    conn = context.connection()
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert list(server_settings.photos.iterdir()) == []
    assert list(server_settings.tmp.iterdir()) == []


def test_database_failure_rolls_back_every_page(api_client, server_settings, monkeypatch):
    conn = context.connection()
    original = db.event

    def fail_on_second_page(connection, kind, **kwargs):
        if kind == "upload_received" and kwargs.get("ordinal") == 2:
            raise RuntimeError("simulated database failure")
        return original(connection, kind, **kwargs)

    monkeypatch.setattr(db, "event", fail_on_second_page)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        upload(api_client, scanned_pdf(2))
    for table in ("batches", "photos", "jobs", "devices", "events"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert list(server_settings.photos.iterdir()) == []
    assert list(server_settings.tmp.iterdir()) == []


def test_render_failure_does_not_publish_first_page(api_client, server_settings, monkeypatch):
    original = fitz.Page.get_pixmap

    def fail_on_second_page(page, *args, **kwargs):
        if page.number == 1:
            raise RuntimeError("simulated renderer failure")
        return original(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", fail_on_second_page)
    assert upload(api_client, scanned_pdf(2)).status_code == 400
    assert context.connection().execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert list(server_settings.photos.iterdir()) == []
    assert list(server_settings.tmp.iterdir()) == []


def test_web_report_refuses_read_but_unattached_page(api_client, reader):
    response = upload(api_client, scanned_pdf(1))
    conn = context.connection()
    conn.execute("UPDATE photos SET state = 'read'")
    assert api_client.post(response.location + "/submit").status_code == 409
    assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_portal_has_pdf_form_without_phone_pairing(api_client):
    response = api_client.get("/")
    assert response.status_code == 200
    assert b'enctype="multipart/form-data"' in response.data
    assert b'name="pdf"' in response.data
    assert b'News Cafe Testshop' in response.data
