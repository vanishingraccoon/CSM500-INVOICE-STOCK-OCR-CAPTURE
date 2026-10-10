"""The reading queue: photographs are read in background processes while the manager carries on scanning.

Each reader process loads PaddleOCR once, then loops: claim a job, read the photograph, record what was read,
rebuild the batch's invoices. A claim is a lease, not a lock: the job is the reader's until lease_until, and a
helper thread renews it while recognition runs. A reader that dies stops renewing; its lease expires and the
job goes back in the queue. A job that fails is tried again after a growing pause, a bounded number of times;
then the photograph is marked failed and its invoice lands in Retake, so the manager photographs it again.
The attempt count also stops a photograph that crashes the reader outright from crashing it for ever.

No transaction is held during recognition. A claim, a renewal and each record are separate short transactions.

The recognition engine is passed in, so tests use a fake engine and never load PaddleOCR.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app import db, quality, reading
from app.settings import Settings, configure

LEASE_SECONDS = 90.0
RENEW_SECONDS = 20.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (5.0, 30.0, 120.0)      # before the second, third, ... attempt
POLL_SECONDS = 0.5

log = logging.getLogger("invoice.worker")


@dataclass
class Job:
    job_id: int
    photo_id: str
    batch_id: str
    shop_id: str
    storage_path: str
    attempts: int


def _requeue(conn: sqlite3.Connection, row: dict, why: str) -> None:
    if row["attempts"] >= MAX_ATTEMPTS:
        conn.execute("UPDATE jobs SET status = 'failed', lease_owner = NULL, lease_until = NULL, finished_at = ?,"
                     " last_error = COALESCE(last_error, ?) WHERE job_id = ?", (db.iso(), why, row["job_id"]))
        conn.execute("UPDATE photos SET state = 'failed' WHERE photo_id = ? AND state IN ('stored', 'reading')",
                     (row["photo_id"],))
        db.event(conn, "job_gave_up", batch_id=row["batch_id"], photo_id=row["photo_id"], attempts=row["attempts"],
                 error=why)
    else:
        conn.execute("UPDATE jobs SET status = 'queued', available = ?, lease_owner = NULL, lease_until = NULL"
                     " WHERE job_id = ?", (db.now(), row["job_id"]))
        conn.execute("UPDATE photos SET state = 'stored' WHERE photo_id = ? AND state = 'reading'", (row["photo_id"],))
        db.event(conn, "lease_expired", batch_id=row["batch_id"], photo_id=row["photo_id"], owner=row["lease_owner"],
                 attempts=row["attempts"], reason=why)
    db.bump_revision(conn, row["batch_id"])


def requeue_stale(conn: sqlite3.Connection, run_id: str | None = None) -> int:
    """Put back running jobs whose lease expired, and (at startup) those held by readers of an earlier run."""
    with db.transaction(conn):
        rows = db.all_rows(conn, "SELECT j.*, p.batch_id FROM jobs j JOIN photos p ON p.photo_id = j.photo_id"
                                 " WHERE j.status = 'running' AND (j.lease_until < ? OR (? IS NOT NULL AND"
                                 " j.lease_owner NOT LIKE ? || ':%'))", db.now(), run_id, run_id)
        for row in rows:
            expired = row["lease_until"] is not None and row["lease_until"] < db.now()
            _requeue(conn, row, "lease expired" if expired else "reader of an earlier run")
    return len(rows)


def claim(conn: sqlite3.Connection, owner: str, lease: float = LEASE_SECONDS) -> Job | None:
    requeue_stale(conn)
    with db.transaction(conn):
        while True:
            row = db.one(conn, "SELECT j.job_id, j.photo_id, j.attempts, p.batch_id, p.storage_path, p.state, b.shop_id"
                               " FROM jobs j JOIN photos p ON p.photo_id = j.photo_id"
                               " JOIN batches b ON b.batch_id = p.batch_id"
                               " WHERE j.status = 'queued' AND j.available <= ?"
                               " ORDER BY b.created, p.ordinal, j.job_id LIMIT 1", db.now())
            if row is None:
                return None
            if row["state"] == "replaced":
                conn.execute("UPDATE jobs SET status = 'done', finished_at = ?, last_error = ? WHERE job_id = ?",
                             (db.iso(), "replaced by a retake before it was read", row["job_id"]))
                continue
            conn.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, lease_owner = ?, lease_until = ?"
                         " WHERE job_id = ?", (owner, db.now() + lease, row["job_id"]))
            conn.execute("UPDATE photos SET state = 'reading' WHERE photo_id = ? AND state = 'stored'", (row["photo_id"],))
            db.event(conn, "job_claimed", batch_id=row["batch_id"], photo_id=row["photo_id"], owner=owner,
                     attempt=row["attempts"] + 1)
            db.bump_revision(conn, row["batch_id"])
            return Job(row["job_id"], row["photo_id"], row["batch_id"], row["shop_id"], row["storage_path"],
                       row["attempts"] + 1)


def renew(conn: sqlite3.Connection, job: Job, owner: str, lease: float = LEASE_SECONDS) -> bool:
    cur = conn.execute("UPDATE jobs SET lease_until = ? WHERE job_id = ? AND lease_owner = ? AND status = 'running'",
                       (db.now() + lease, job.job_id, owner))
    return cur.rowcount == 1


class LeaseKeeper:
    """Renews a lease from a helper thread (with its own connection) while the main thread reads."""

    def __init__(self, db_path: Path, job: Job, owner: str, every: float = RENEW_SECONDS):
        self.db_path, self.job, self.owner, self.every = db_path, job, owner, every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lease-{job.job_id}", daemon=True)

    def _run(self) -> None:
        conn = None       # opened only when a read outlasts the first interval
        try:
            while not self._stop.wait(self.every):
                try:
                    conn = conn or db.connection(self.db_path)
                    if not renew(conn, self.job, self.owner):
                        return
                except sqlite3.Error:
                    log.exception("lease renewal failed; trying again")
        finally:
            if conn is not None:
                db.close_thread()

    def __enter__(self) -> LeaseKeeper:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def identify_page(result) -> dict:
    from ingest.identify import identify
    from ingest.run import header_of
    ident = identify(header_of(result))
    return {"supplier": ident.supplier.key if ident.supplier else None, "supplier_score": round(ident.supplier_score, 1),
            "document_no": ident.document_no, "date": ident.date,
            "page_of": f"{ident.page_of[0]}/{ident.page_of[1]}" if ident.page_of else None}


def record_read(conn: sqlite3.Connection, job: Job, owner: str, result, cache_file: Path, measures: dict,
                ident: dict) -> bool:
    with db.transaction(conn):
        done = conn.execute("UPDATE jobs SET status = 'done', finished_at = ?, lease_owner = NULL, lease_until = NULL,"
                            " last_error = NULL WHERE job_id = ? AND lease_owner = ? AND status = 'running'",
                            (db.iso(), job.job_id, owner)).rowcount
        if not done:
            return False        # the lease was lost and the job is someone else's now
        conn.execute("INSERT OR REPLACE INTO ocr_results (photo_id, engine, engine_version, cache_file, words, mean_conf,"
                     " seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (job.photo_id, result.engine, result.engine_version, str(cache_file), len(result.words),
                      round(result.mean_conf, 2), result.seconds, db.iso()))
        ts = db.now()
        conn.execute("UPDATE photos SET state = 'read', quality = ?, words = ?, supplier = ?, supplier_score = ?,"
                     " document_no = ?, date = ?, page_of = ?, read_at = ?, read = ? WHERE photo_id = ? AND state = 'reading'",
                     (db.dumps({"server": measures}), len(result.words), ident["supplier"], ident["supplier_score"],
                      ident["document_no"], ident["date"], ident["page_of"], db.iso(ts), ts, job.photo_id))
        db.event(conn, "page_read", shop_id=job.shop_id, batch_id=job.batch_id, photo_id=job.photo_id,
                 words=len(result.words), mean_conf=round(result.mean_conf, 1), seconds=result.seconds,
                 attempt=job.attempts, quality=measures, **ident)
        db.bump_revision(conn, job.batch_id)
    return True


def record_failure(conn: sqlite3.Connection, job: Job, owner: str, error: str) -> str:
    """'retry', 'failed', or 'lost' when the lease had already gone."""
    with db.transaction(conn):
        row = db.one(conn, "SELECT attempts FROM jobs WHERE job_id = ? AND lease_owner = ? AND status = 'running'",
                     job.job_id, owner)
        if row is None:
            return "lost"
        common = {"shop_id": job.shop_id, "batch_id": job.batch_id, "photo_id": job.photo_id,
                  "attempt": row["attempts"], "error": error}
        if row["attempts"] >= MAX_ATTEMPTS:
            conn.execute("UPDATE jobs SET status = 'failed', last_error = ?, finished_at = ?, lease_owner = NULL,"
                         " lease_until = NULL WHERE job_id = ?", (error, db.iso(), job.job_id))
            conn.execute("UPDATE photos SET state = 'failed' WHERE photo_id = ? AND state = 'reading'", (job.photo_id,))
            db.event(conn, "job_gave_up", **common)
            outcome = "failed"
        else:
            delay = BACKOFF_SECONDS[min(row["attempts"] - 1, len(BACKOFF_SECONDS) - 1)]
            conn.execute("UPDATE jobs SET status = 'queued', available = ?, last_error = ?, lease_owner = NULL,"
                         " lease_until = NULL WHERE job_id = ?", (db.now() + delay, error, job.job_id))
            conn.execute("UPDATE photos SET state = 'stored' WHERE photo_id = ? AND state = 'reading'", (job.photo_id,))
            db.event(conn, "job_failed", retry_in_seconds=delay, **common)
            outcome = "retry"
        db.bump_revision(conn, job.batch_id)
    return outcome


def release(conn: sqlite3.Connection, job: Job, owner: str) -> None:
    """Shutting down mid-read: hand the job back without counting the interrupted attempt."""
    with db.transaction(conn):
        released = conn.execute("UPDATE jobs SET status = 'queued', attempts = MAX(attempts - 1, 0), available = ?,"
                                " lease_owner = NULL, lease_until = NULL WHERE job_id = ? AND lease_owner = ?"
                                " AND status = 'running'", (db.now(), job.job_id, owner)).rowcount
        if released:
            conn.execute("UPDATE photos SET state = 'stored' WHERE photo_id = ? AND state = 'reading'", (job.photo_id,))
            db.event(conn, "job_released", batch_id=job.batch_id, photo_id=job.photo_id, owner=owner)
            db.bump_revision(conn, job.batch_id)


def rebuild_quietly(conn: sqlite3.Connection, keys, batch_id: str) -> None:
    try:
        reading.update_batch(conn, batch_id, keys)
    except Exception as e:
        log.exception("could not rebuild batch %s", batch_id)
        with db.transaction(conn):
            db.event(conn, "rebuild_failed", batch_id=batch_id, error=f"{type(e).__name__}: {e}")


def process(conn: sqlite3.Connection, settings: Settings, keys, ocr, job: Job, owner: str) -> str:
    """Read one claimed photograph. Returns 'read', 'retry', 'failed' or 'lost'."""
    path = settings.photos / job.storage_path
    try:
        with LeaseKeeper(settings.db_path, job, owner):
            measures = quality.measure(path)
            if measures is None:
                raise ValueError("the stored photograph could not be decoded")
            result = ocr.recognise(path)
            cache_file = ocr.cache_file(path)
            ident = identify_page(result)
    except KeyboardInterrupt:
        release(conn, job, owner)
        raise
    except Exception as e:  # noqa: BLE001 - one photo must never stop a reader; the failure is recorded
        log.warning("reading %s failed: %s", job.photo_id, e)
        outcome = record_failure(conn, job, owner, f"{type(e).__name__}: {e}"[:500])
        if outcome == "failed":
            rebuild_quietly(conn, keys, job.batch_id)      # the page now belongs in Retake
        return outcome
    if not record_read(conn, job, owner, result, cache_file, measures, ident):
        return "lost"
    rebuild_quietly(conn, keys, job.batch_id)
    return "read"


def run_once(conn: sqlite3.Connection, settings: Settings, keys, ocr, owner: str) -> str | None:
    job = claim(conn, owner)
    return None if job is None else process(conn, settings, keys, ocr, job, owner)


def make_ocr(settings: Settings):
    """PaddleOCR (or OCR_ENGINE), through the disk cache, loaded now rather than on the first photograph.

    OCR_ENGINE may also name an engine class as module:Class, built with no arguments. That is how a test puts a
    fake engine into real reader processes, which cannot be handed an object across the process boundary."""
    import config
    from ocr.cache import CachedOCR
    from ocr.engines import get_ocr
    if ":" in settings.ocr_engine:
        import importlib
        module, _, name = settings.ocr_engine.partition(":")
        return CachedOCR(getattr(importlib.import_module(module), name)(), settings.ocr_cache)
    ocr = get_ocr(settings.ocr_engine)
    if Path(settings.ocr_cache) != Path(config.OCR_CACHE):
        ocr = CachedOCR(ocr.engine, settings.ocr_cache)
    getattr(ocr.engine, "_ocr", None)          # PaddleEngine loads its models on first touch of this property
    return ocr


def worker_main(settings: Settings, index: int, run_id: str, stop, threads: int) -> None:
    """Entry point of one reader process."""
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ["OMP_NUM_THREADS"] = str(threads)
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s reader-{index} %(levelname)s %(message)s")
    configure(settings)
    from app.shops import Keys
    conn = db.connection(settings.db_path)
    owner = f"{run_id}:{socket.gethostname()}:{os.getpid()}:r{index}"
    log.info("loading the recognition engine")
    ocr = make_ocr(settings)
    keys = Keys(settings)
    parent = multiprocessing.parent_process()
    log.info("ready")
    try:
        while not stop.is_set():
            if parent is not None and not parent.is_alive():
                break          # the server has gone; leave rather than read for nobody
            if run_once(conn, settings, keys, ocr, owner) is None:
                stop.wait(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        db.close_all()


class WorkerPool:
    """The reader processes of one server run, restarted if one dies."""

    def __init__(self, settings: Settings, count: int, run_id: str):
        self.settings, self.count, self.run_id = settings, count, run_id
        self.ctx = multiprocessing.get_context("spawn")
        self.stop = self.ctx.Event()
        self.procs: list = []
        self._watcher: threading.Thread | None = None

    def _spawn(self, index: int):
        threads = max(1, (os.cpu_count() or 4) // self.count)
        p = self.ctx.Process(target=worker_main, args=(self.settings, index, self.run_id, self.stop, threads),
                             name=f"reader-{index}")
        p.start()
        return p

    def start(self) -> None:
        self.procs = [self._spawn(i) for i in range(self.count)]
        self._watcher = threading.Thread(target=self._watch, name="reader-watch", daemon=True)
        self._watcher.start()

    def _watch(self) -> None:
        while not self.stop.wait(5.0):
            for i, p in enumerate(self.procs):
                if not p.is_alive() and not self.stop.is_set():
                    log.warning("reader-%d stopped (exit code %s); starting it again", i, p.exitcode)
                    self.procs[i] = self._spawn(i)

    def shutdown(self, timeout: float = 30.0) -> None:
        self.stop.set()
        deadline = time.monotonic() + timeout
        for p in self.procs:
            p.join(max(0.1, deadline - time.monotonic()))
        for p in self.procs:
            if p.is_alive():
                p.terminate()        # its lease expires, and the next start puts the job back in the queue
                p.join(5)
