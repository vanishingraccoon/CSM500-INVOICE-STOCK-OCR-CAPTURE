"""The laptop server's SQLite database: one file, shared by the web server and the reading processes.

Several processes write to it at once (web requests, two or more readers), so:
- WAL journal, so readers never block the writer and the writer never blocks readers;
- every write is a short BEGIN IMMEDIATE transaction, taking the write lock up front instead of failing
  half-way on an upgrade, and nothing slow (recognition, parsing, building a PDF) happens inside one;
- synchronous=FULL, because a phone deletes its copy of a photograph once it has the receipt, so the
  commit behind a receipt must survive a power cut;
- a busy timeout, so a writer waits its turn instead of erroring;
- one connection per thread per process: sqlite3 connections must not be shared across threads.

The schema has an explicit version (PRAGMA user_version) and is changed only by appending a migration.

The events table is append-only, enforced by triggers. It is evaluation evidence: how often a person
intervened, retakes, duplicates caught, and the time from the first photograph to submit.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BUSY_TIMEOUT_MS = 15_000

MIGRATIONS: tuple[str, ...] = (
    # 1: first schema
    """
    CREATE TABLE shops (
        shop_id      TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );

    CREATE TABLE devices (
        device_id     TEXT PRIMARY KEY,
        shop_id       TEXT NOT NULL REFERENCES shops(shop_id),
        name          TEXT NOT NULL,
        token_hash    TEXT NOT NULL UNIQUE,
        paired_at     TEXT NOT NULL,
        last_seen     REAL,
        revoked_at    TEXT
    );

    CREATE TABLE pair_codes (
        code          TEXT PRIMARY KEY,
        shop_id       TEXT NOT NULL REFERENCES shops(shop_id),
        created       REAL NOT NULL,
        expires       REAL NOT NULL,
        used          REAL,
        device_id     TEXT REFERENCES devices(device_id)
    );

    CREATE TABLE batches (
        batch_id          TEXT PRIMARY KEY,
        shop_id           TEXT NOT NULL REFERENCES shops(shop_id),
        device_id         TEXT NOT NULL REFERENCES devices(device_id),
        client_batch_key  TEXT NOT NULL,
        page_count        INTEGER,
        status            TEXT NOT NULL DEFAULT 'uploading'
                          CHECK (status IN ('uploading', 'processing', 'ready', 'submitted')),
        revision          INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT NOT NULL,
        created           REAL NOT NULL,
        completed_at      TEXT,
        submitted_at      TEXT,
        UNIQUE (device_id, client_batch_key)
    );

    CREATE TABLE photos (
        photo_id          TEXT PRIMARY KEY,
        batch_id          TEXT NOT NULL REFERENCES batches(batch_id),
        client_photo_key  TEXT NOT NULL,
        ordinal           INTEGER NOT NULL,
        sha256            TEXT NOT NULL,
        bytes             INTEGER NOT NULL,
        content_type      TEXT NOT NULL,
        width             INTEGER NOT NULL,
        height            INTEGER NOT NULL,
        storage_path      TEXT NOT NULL,
        state             TEXT NOT NULL DEFAULT 'stored'
                          CHECK (state IN ('stored', 'reading', 'read', 'failed', 'replaced')),
        phone_quality     TEXT,
        quality           TEXT,
        replaces          TEXT REFERENCES photos(photo_id),
        replaced_by       TEXT REFERENCES photos(photo_id),
        supplier          TEXT,
        supplier_score    REAL,
        document_no       TEXT,
        date              TEXT,
        page_of           TEXT,
        words             INTEGER,
        received_at       TEXT NOT NULL,
        received          REAL NOT NULL,
        read_at           TEXT,
        read              REAL,
        UNIQUE (batch_id, client_photo_key)
    );
    CREATE INDEX photos_by_hash ON photos(sha256);
    CREATE INDEX photos_by_batch ON photos(batch_id, ordinal);

    CREATE TABLE jobs (
        job_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id      TEXT NOT NULL REFERENCES photos(photo_id),
        kind          TEXT NOT NULL DEFAULT 'read',
        status        TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
        attempts      INTEGER NOT NULL DEFAULT 0,
        available     REAL NOT NULL,
        lease_owner   TEXT,
        lease_until   REAL,
        last_error    TEXT,
        created_at    TEXT NOT NULL,
        finished_at   TEXT,
        UNIQUE (photo_id, kind)
    );
    CREATE INDEX jobs_ready ON jobs(status, available);

    -- the recognised words stay in the OCR disk cache (ocr/cache.py); this row says where
    CREATE TABLE ocr_results (
        photo_id        TEXT PRIMARY KEY REFERENCES photos(photo_id),
        engine          TEXT NOT NULL,
        engine_version  TEXT NOT NULL,
        cache_file      TEXT NOT NULL,
        words           INTEGER NOT NULL,
        mean_conf       REAL NOT NULL,
        seconds         REAL NOT NULL,
        created_at      TEXT NOT NULL
    );

    CREATE TABLE reports (
        report_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id      TEXT NOT NULL REFERENCES batches(batch_id),
        version       INTEGER NOT NULL,
        snapshot      TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'building' CHECK (status IN ('building', 'ready', 'failed')),
        pdf_path      TEXT,
        pdf_sha256    TEXT,
        csv_path      TEXT,
        csv_sha256    TEXT,
        last_error    TEXT,
        created_at    TEXT NOT NULL,
        built_at      TEXT,
        UNIQUE (batch_id, version)
    );

    CREATE TABLE invoices (
        invoice_id            TEXT PRIMARY KEY,
        batch_id              TEXT NOT NULL REFERENCES batches(batch_id),
        status                TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'deleted')),
        supplier              TEXT,
        document_no           TEXT,
        date                  TEXT,
        supplier_override     TEXT,
        document_no_override  TEXT,
        date_override         TEXT,
        layout                TEXT,
        stated_total_excl     INTEGER,
        stated_vat            INTEGER,
        stated_total_incl     INTEGER,
        lines_sum_excl        INTEGER,
        adds_up               INTEGER,
        warnings              TEXT NOT NULL DEFAULT '[]',
        tab                   TEXT NOT NULL DEFAULT 'retake'
                              CHECK (tab IN ('good', 'corrections', 'retake', 'duplicates', 'deleted')),
        tab_reasons           TEXT NOT NULL DEFAULT '[]',
        duplicate_of          TEXT,
        not_duplicate         INTEGER NOT NULL DEFAULT 0,
        parsed                REAL,
        needs_report          INTEGER NOT NULL DEFAULT 0,
        reported_in           INTEGER REFERENCES reports(report_id),
        created_at            TEXT NOT NULL,
        updated_at            TEXT NOT NULL
    );
    CREATE INDEX invoices_by_batch ON invoices(batch_id);

    CREATE TABLE invoice_pages (
        invoice_id  TEXT NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
        photo_id    TEXT NOT NULL UNIQUE REFERENCES photos(photo_id),
        page_no     INTEGER NOT NULL,
        PRIMARY KEY (invoice_id, photo_id)
    );

    CREATE TABLE lines (
        line_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id        TEXT NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
        idx               INTEGER NOT NULL,
        printed           TEXT,
        entered_by        TEXT NOT NULL CHECK (entered_by IN ('system', 'manager')),
        removed           INTEGER NOT NULL DEFAULT 0,
        item_code         TEXT,
        item_name         TEXT,
        base_unit         TEXT,
        free_text         TEXT,
        quantity          REAL,
        unit              TEXT,
        base_quantity     REAL,
        cost_cents        INTEGER,
        vat_cents         INTEGER,
        factor            REAL,
        conversion_basis  TEXT,
        match_method      TEXT,
        match_score       REAL,
        refusal           TEXT,
        reason            TEXT NOT NULL DEFAULT '',
        flags             TEXT NOT NULL DEFAULT '[]',
        candidates        TEXT NOT NULL DEFAULT '[]',
        star              INTEGER NOT NULL DEFAULT 0,
        updated_at        TEXT NOT NULL,
        UNIQUE (invoice_id, idx)
    );

    CREATE TABLE learned_aliases (
        alias_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id         TEXT NOT NULL REFERENCES shops(shop_id),
        supplier        TEXT NOT NULL,
        wording         TEXT NOT NULL,
        normalised      TEXT NOT NULL CHECK (normalised <> ''),
        item_code       TEXT NOT NULL,
        item_name       TEXT NOT NULL,
        source_batch    TEXT NOT NULL REFERENCES batches(batch_id),
        source_invoice  TEXT,
        created_at      TEXT NOT NULL,
        removed_at      TEXT,
        UNIQUE (shop_id, supplier, normalised, item_code, source_batch)
    );

    CREATE TABLE events (
        event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        at          TEXT NOT NULL,
        at_epoch    REAL NOT NULL,
        kind        TEXT NOT NULL,
        shop_id     TEXT,
        device_id   TEXT,
        batch_id    TEXT,
        photo_id    TEXT,
        invoice_id  TEXT,
        line_idx    INTEGER,
        detail      TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX events_by_batch ON events(batch_id, at_epoch);
    CREATE INDEX events_by_kind ON events(kind, at_epoch);
    CREATE TRIGGER events_are_never_updated BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    CREATE TRIGGER events_are_never_deleted BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    """,
)


# --- time and ids ----------------------------------------------------------------------------

def now() -> float:
    """Seconds since the epoch. Tests replace this to move the clock."""
    return time.time()


def iso(ts: float | None = None) -> str:
    """ISO 8601 local time, as the phone contract uses."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now() if ts is None else ts))


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if text in (None, ""):
        return default
    return json.loads(text)


# --- connections -----------------------------------------------------------------------------

_local = threading.local()
_open: dict[tuple[int, int, str], sqlite3.Connection] = {}
_open_lock = threading.Lock()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: no implicit transactions; every write says BEGIN IMMEDIATE itself
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def connection(path: Path) -> sqlite3.Connection:
    """This thread's connection to the database at path, opened and migrated on first use."""
    key = (os.getpid(), threading.get_ident(), str(Path(path).resolve()))
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    conn = conns.get(key)
    if conn is None:
        conn = conns[key] = _connect(Path(path))
        migrate(conn)
        with _open_lock:
            _open[key] = conn
    return conn


def close_all() -> None:
    """Close every connection this process opened (tests, and shutdown)."""
    with _open_lock:
        items = [(k, c) for k, c in _open.items() if k[0] == os.getpid()]
        for k, _ in items:
            _open.pop(k, None)
    for _, c in items:
        try:
            c.close()
        except sqlite3.ProgrammingError:
            pass   # opened by another thread, which closes it itself when it ends
    conns = getattr(_local, "conns", None)
    if conns is not None:
        conns.clear()


def close_thread() -> None:
    """Close the connections this thread opened (a helper thread that is about to end)."""
    conns = getattr(_local, "conns", None) or {}
    for key, conn in list(conns.items()):
        with _open_lock:
            _open.pop(key, None)
        conn.close()
    conns.clear()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A short write transaction that takes the write lock at once."""
    if conn.in_transaction:
        raise RuntimeError("transactions are not nested; finish the outer one first")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A read transaction: several queries see one consistent state of the database (WAL keeps writers moving)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN DEFERRED")
    try:
        yield conn
    finally:
        conn.execute("COMMIT")


def _statements(script: str) -> Iterator[str]:
    """Split a migration into statements; complete_statement knows a trigger body's semicolons."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf
            buf = ""
    if buf.strip() and not all(l.strip().startswith("--") or not l.strip() for l in buf.splitlines()):
        raise ValueError("a migration ends with an incomplete statement")


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the schema up to date. Safe when several processes start at once: the version is re-read
    after the write lock is taken."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= len(MIGRATIONS):
        return len(MIGRATIONS)
    with transaction(conn):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for n in range(version, len(MIGRATIONS)):
            for statement in _statements(MIGRATIONS[n]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {n + 1}")
    return len(MIGRATIONS)


# --- small helpers ---------------------------------------------------------------------------

def one(conn: sqlite3.Connection, sql: str, *params) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def all_rows(conn: sqlite3.Connection, sql: str, *params) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def event(conn: sqlite3.Connection, kind: str, *, shop_id: str | None = None, device_id: str | None = None,
          batch_id: str | None = None, photo_id: str | None = None, invoice_id: str | None = None,
          line_idx: int | None = None, **detail) -> None:
    ts = now()
    conn.execute(
        "INSERT INTO events (at, at_epoch, kind, shop_id, device_id, batch_id, photo_id, invoice_id, line_idx, detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (iso(ts), ts, kind, shop_id, device_id, batch_id, photo_id, invoice_id, line_idx, dumps(detail)))


def bump_revision(conn: sqlite3.Connection, batch_id: str) -> None:
    """Anything that changes what GET /batches/{id} returns moves the batch's revision, and so its ETag."""
    conn.execute("UPDATE batches SET revision = revision + 1 WHERE batch_id = ?", (batch_id,))
