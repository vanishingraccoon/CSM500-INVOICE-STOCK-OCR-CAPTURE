"""What a web request needs: the settings, this thread's database connection, and this process's shop keys.

The first connection to a database also creates its folders, migrates the schema and mirrors the configured
shops into it, so the portal, the API and the tests all start from the same state.
"""
from __future__ import annotations

import sqlite3
import threading

from app import db
from app.settings import Settings, current
from app.shops import Keys, sync_shops

_lock = threading.Lock()
_ready: set[str] = set()
_keys: dict[str, Keys] = {}


def settings() -> Settings:
    return current()


def connection() -> sqlite3.Connection:
    s = settings()
    path = str(s.db_path)
    if path not in _ready:
        with _lock:
            if path not in _ready:
                s.ensure_dirs()
                sync_shops(db.connection(s.db_path), s)
                _ready.add(path)
    return db.connection(s.db_path)


def keys() -> Keys:
    s = settings()
    with _lock:
        k = _keys.get(str(s.app_data))
        if k is None or k.settings != s:
            k = _keys[str(s.app_data)] = Keys(s)
        return k


def reset() -> None:
    """Forget cached keys and initialised databases (tests switch settings between cases)."""
    with _lock:
        _ready.clear()
        _keys.clear()
