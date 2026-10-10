"""Where the laptop server keeps its data and how it listens. Everything read from the environment is here.

    INVOICE_APP_DATA    database, photographs, recognised words and reports (default data/app); the server
                        writes nowhere else under data/
    INVOICE_HOST        127.0.0.1, this laptop only (default); 0.0.0.0 to reach it from the office Wi-Fi
    INVOICE_PORT        5000
    INVOICE_WORKERS     reading processes, each loading its own PaddleOCR (default 2)
    INVOICE_PUBLIC_URL  the address phones are told to use, when the detected LAN address is wrong
    OCR_ENGINE          paddle (default) or tesseract; module:Class names a test's fake engine

Tests configure a Settings pointing at a temporary folder, so they never touch the real data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

import config


@dataclass(frozen=True)
class Settings:
    app_data: Path
    pilot_dir: Path
    ocr_cache: Path
    host: str = "127.0.0.1"
    port: int = 5000
    workers: int = 2
    public_url: str | None = None
    ocr_engine: str = "paddle"

    @property
    def db_path(self) -> Path:
        return self.app_data / "invoices.db"

    @property
    def photos(self) -> Path:
        return self.app_data / "photos"

    @property
    def reports(self) -> Path:
        return self.app_data / "reports"

    @property
    def thumbs(self) -> Path:
        return self.app_data / "thumbs"

    @property
    def tmp(self) -> Path:
        # on the same volume as photos and reports, so a finished file can be renamed into place atomically
        return self.app_data / "tmp"

    @property
    def shops_file(self) -> Path:
        return self.app_data / "shops.json"

    def ensure_dirs(self) -> Settings:
        for d in (self.app_data, self.photos, self.reports, self.thumbs, self.tmp):
            d.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        app_data = Path(env.get("INVOICE_APP_DATA", config.DATA / "app"))
        return cls(
            app_data=app_data,
            pilot_dir=config.PILOT,             # read only: the shops' Pilot exports
            # the phones' recognised words stay with the server's own data, apart from the binder's OCR cache
            ocr_cache=app_data / "ocr_cache",
            host=env.get("INVOICE_HOST", "127.0.0.1"),
            port=int(env.get("INVOICE_PORT", "5000")),
            workers=max(1, int(env.get("INVOICE_WORKERS", "2"))),
            public_url=env.get("INVOICE_PUBLIC_URL") or None,
            ocr_engine=env.get("OCR_ENGINE", "paddle"),
        )

    def with_(self, **changes) -> Settings:
        return replace(self, **changes)


_current: Settings | None = None


def current() -> Settings:
    global _current
    if _current is None:
        _current = Settings.from_env()
    return _current


def configure(settings: Settings | None) -> Settings | None:
    """Set the settings every request and worker uses; None goes back to the environment's."""
    global _current
    _current = settings
    return settings
