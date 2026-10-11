"""Stage scanned PDF pages, then publish a complete batch to the existing reader queue."""
from __future__ import annotations

import hashlib
import math
import os
import secrets
import tempfile
from pathlib import Path

import pymupdf as fitz

from app import db, quality, reading, storage
from app.batches import ApiError

MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_PAGES = 100
MAX_RENDERED_BYTES = 250 * 1024 * 1024
MAX_RENDER_SIDE = 4500


def is_pdf_page(conn, photo_id: str) -> bool:
    """Use the server's import event and generated path, never a phone's quality claim."""
    return db.one(conn, "SELECT 1 FROM photos p WHERE p.photo_id = ? AND p.replaces IS NULL"
                  " AND p.storage_path = p.batch_id || '/page-' || p.ordinal || '.jpg'"
                  " AND EXISTS (SELECT 1 FROM events e WHERE e.batch_id = p.batch_id"
                  " AND e.kind = 'pdf_imported')", photo_id) is not None


def recheck_quality(conn, settings, keys, batch_id: str):
    batch = db.one(conn, "SELECT * FROM batches WHERE batch_id = ?", batch_id)
    if batch is None:
        raise ApiError(404, "not_found", "That batch does not exist.")
    if batch["status"] != "ready":
        raise ApiError(409, "not_ready", "Wait for reading to finish. Submitted reports are preserved; use a new batch for those PDFs.")
    pages = db.all_rows(conn, "SELECT * FROM photos WHERE batch_id = ? AND state <> 'replaced'", batch_id)
    measured = {}
    for page in pages:
        if page["state"] == "read" and is_pdf_page(conn, page["photo_id"]):
            value = quality.measure(settings.photos / page["storage_path"], scanned_pdf=True)
            if value is None:
                raise ApiError(409, "missing_image", "A stored PDF page could not be read. Keep this batch and upload the original PDF again.")
            measured[page["photo_id"]] = value
    if not measured:
        raise ApiError(409, "not_pdf", "This batch has no completed PDF pages to recheck.")
    with db.transaction(conn):
        current = db.one(conn, "SELECT status, revision FROM batches WHERE batch_id = ?", batch_id)
        if current["status"] != "ready" or current["revision"] != batch["revision"]:
            raise ApiError(409, "batch_changed", "The batch changed during the quality check. Refresh and try again.")
        for page in pages:
            if page["photo_id"] in measured:
                previous = db.loads(page["quality"], {})
                updated = {**previous, "server": measured[page["photo_id"]]}
                conn.execute("UPDATE photos SET quality = ? WHERE photo_id = ?", (db.dumps(updated), page["photo_id"]))
                db.event(conn, "pdf_quality_rechecked", shop_id=batch["shop_id"], batch_id=batch_id,
                         photo_id=page["photo_id"], before=previous, after=updated)
        db.bump_revision(conn, batch_id)
    return reading.update_batch(conn, batch_id, keys)


def _stage(stream, folder: Path):
    source = folder / "source.pdf"
    digest, size = hashlib.sha256(), 0
    with source.open("wb") as out:
        while chunk := stream.read(storage.CHUNK):
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                raise ApiError(413, "pdf_too_large", "Choose a PDF smaller than 30 MB.")
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    pages, rendered_bytes = [], 0
    try:
        # A failed native open can retain a Windows file handle through its traceback.
        with fitz.open(stream=source.read_bytes(), filetype="pdf") as document:
            if not document.is_pdf or document.needs_pass:
                raise ApiError(400, "invalid_pdf", "Choose a PDF without password protection.")
            if not 1 <= len(document) <= MAX_PAGES:
                raise ApiError(400, "pdf_page_limit", "Choose a PDF with 1 to 100 pages.")
            for ordinal, page in enumerate(document, start=1):
                side = max(page.rect.width, page.rect.height)
                if not math.isfinite(side) or side <= 0:
                    raise ApiError(400, "invalid_pdf", "A PDF page has invalid dimensions.")
                scale = min(300 / 72, MAX_RENDER_SIDE / side)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                path = folder / f"page-{ordinal}.jpg"
                pixmap.save(path, jpg_quality=95)
                length = path.stat().st_size
                rendered_bytes += length
                if length > storage.MAX_BYTES or rendered_bytes > MAX_RENDERED_BYTES:
                    raise ApiError(413, "pdf_render_limit", "The rendered PDF is too large. Split it into smaller PDFs.")
                extension, width, height = storage.validate_image(path)
                with path.open("rb") as page_file:
                    sha = hashlib.file_digest(page_file, "sha256").hexdigest()
                pages.append(storage.Received(path, sha, length, extension, width, height))
    except ApiError:
        raise
    except storage.UploadError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    except Exception as exc:
        raise ApiError(400, "invalid_pdf", "This PDF could not be read. Export it again as an unprotected PDF.") from exc
    return source, digest.hexdigest(), pages


def import_pdf(conn, settings, shop_id: str, stream, filename: str) -> str:
    """Render before creating records; readers see all page jobs together after commit."""
    settings.ensure_dirs()
    with tempfile.TemporaryDirectory(prefix="pdf-upload-", dir=settings.tmp) as temporary:
        source, source_sha, pages = _stage(stream, Path(temporary))
        batch_id, device_id, ts = db.new_id("b"), db.new_id("d"), db.now()
        destination = settings.photos / batch_id
        destination.mkdir()
        try:
            os.replace(source, destination / "source.pdf")
            for ordinal, page in enumerate(pages, start=1):
                storage.store(page, destination / f"page-{ordinal}.jpg")
            with db.transaction(conn):
                conn.execute("INSERT INTO devices (device_id, shop_id, name, token_hash, paired_at) VALUES (?, ?, ?, ?, ?)",
                             (device_id, shop_id, "PDF upload", hashlib.sha256(secrets.token_bytes(32)).hexdigest(), db.iso(ts)))
                conn.execute("INSERT INTO batches (batch_id, shop_id, device_id, client_batch_key, page_count,"
                             " status, created_at, created, completed_at) VALUES (?, ?, ?, ?, ?, 'processing', ?, ?, ?)",
                             (batch_id, shop_id, device_id, batch_id, len(pages), db.iso(ts), ts, db.iso(ts)))
                common = {"shop_id": shop_id, "device_id": device_id, "batch_id": batch_id}
                db.event(conn, "batch_created", page_count=len(pages), source="pdf", **common)
                for ordinal, page in enumerate(pages, start=1):
                    photo_id = db.new_id("p")
                    relative = f"{batch_id}/page-{ordinal}.jpg"
                    conn.execute("INSERT INTO photos (photo_id, batch_id, client_photo_key, ordinal, sha256, bytes,"
                                 " content_type, width, height, storage_path, received_at, received)"
                                 " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (photo_id, batch_id, f"pdf-page-{ordinal}", ordinal, page.sha256, page.bytes,
                                  page.content_type, page.width, page.height, relative, db.iso(ts), ts))
                    conn.execute("INSERT INTO jobs (photo_id, available, created_at) VALUES (?, ?, ?)",
                                 (photo_id, ts, db.iso(ts)))
                    db.event(conn, "upload_received", photo_id=photo_id, ordinal=ordinal, bytes=page.bytes,
                             source="pdf", **common)
                name = filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
                db.event(conn, "pdf_imported", filename=name, sha256=source_sha, pages=len(pages),
                         render_dpi=300, max_side=MAX_RENDER_SIDE, **common)
                db.event(conn, "batch_completed", photos=len(pages), page_count=len(pages),
                         still_reading=len(pages), **common)
        except BaseException:
            # This generated directory contains only this attempt's flat staged files.
            for path in destination.iterdir():
                path.unlink()
            destination.rmdir()
            raise
    return batch_id
