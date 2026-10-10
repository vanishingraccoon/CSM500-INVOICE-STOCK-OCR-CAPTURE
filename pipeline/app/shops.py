"""Shops, and each shop's key: the stock items its managers key into Pilot, plus what the system has learned.

A shop is data, not code. It has an id, the name printed on its reports, and the Pilot exports in
data/pilot that are its own. The latest export matching each pattern is used, so dropping in a newer
export updates the key without a restart. Eastgate has a Purchasing Report (supplier history and the
price medians the checks use); Emperors has only its Stock Valuation, so it matches on wording alone.

To add a shop, write data/app/shops.json listing every shop (it replaces the built-in list):

    [{"shop_id": "eastgate", "name": "News Cafe Eastgate",
      "stock_glob": "stock_valuation_eastgate_*.csv", "purchases_glob": "purchasing_report_eastgate_*.csv"},
     {"shop_id": "rosebank", "name": "News Cafe Rosebank", "stock_glob": "stock_valuation_rosebank_*.csv"}]

Learned aliases (supplier wording tied to a stock item when a batch is submitted) are rows in the database,
fed through normaliser.match.build_aliases, so the same rules apply as in the evaluation: wording that meant
two different items is not learned, and wording with nothing left after normalising never is.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app import db
from app.settings import Settings
from checks.prices import PricePoint
from checks.run import history_points
from normaliser.match import (
    Matcher,
    build_aliases,
    history_from_purchases,
    normalise_text,
    price_medians,
    text_score,
)
from normaliser.stock import StockFile, StockItem, load_stock


@dataclass(frozen=True)
class Shop:
    shop_id: str
    name: str
    stock_glob: str                          # Pilot Stock Valuation export; the latest file wins
    purchases_glob: str | None = None     # Pilot Purchasing Report, when the shop has one


DEFAULT_SHOPS: tuple[Shop, ...] = (
    Shop("eastgate", "News Cafe Eastgate", "stock_valuation_eastgate_*.csv", "purchasing_report_eastgate_*.csv"),
    Shop("emperors", "News Cafe Emperors", "stock_valuation_emperors_*.csv"),
)


def load_shops(settings: Settings) -> dict[str, Shop]:
    if settings.shops_file.exists():
        rows = json.loads(settings.shops_file.read_text(encoding="utf-8"))
        return {r["shop_id"]: Shop(**r) for r in rows}
    return {s.shop_id: s for s in DEFAULT_SHOPS}


def sync_shops(conn: sqlite3.Connection, settings: Settings) -> dict[str, Shop]:
    """Mirror the configured shops into the database, so devices and batches can refer to them."""
    shops = load_shops(settings)
    with db.transaction(conn):
        for s in shops.values():
            conn.execute("INSERT INTO shops (shop_id, name, updated_at) VALUES (?, ?, ?)"
                         " ON CONFLICT (shop_id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at"
                         " WHERE shops.name <> excluded.name", (s.shop_id, s.name, db.iso()))
    return shops


def latest(folder: Path, pattern: str | None) -> Path | None:
    if not pattern:
        return None
    files = sorted(Path(folder).glob(pattern))
    return files[-1] if files else None


# --- the key -----------------------------------------------------------------------------------

@dataclass
class ShopKey:
    shop: Shop
    stock_file: Path
    purchases_file: Path | None
    stock: StockFile
    matcher: Matcher
    medians: dict[str, float]
    history: list[PricePoint]
    purchase_lines: int = 0
    _names: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.by_code = self.stock.by_code()
        self._names = {i.code: normalise_text(i.description) for i in self.stock.items}

    def search(self, query: str, limit: int = 20) -> list[StockItem]:
        """Stock items for the manager's search box: exact code, then name prefix, words, and fuzzy wording."""
        q = (query or "").strip().lower()
        if not q:
            return []
        words = q.split()
        nq = normalise_text(q)
        scored = []
        for item in self.stock.items:
            name = item.name.lower()
            if item.code.lower() == q:
                score = 1000.0
            elif name.startswith(q):
                score = 500.0
            elif q in name:
                score = 400.0
            elif all(w in name for w in words):
                score = 300.0
            else:
                score = text_score(nq, self._names[item.code]) if nq else 0.0
                if score < 60:
                    continue
            scored.append((score, item.name, item))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [item for _, _, item in scored[:limit]]


def learned_pairs(conn: sqlite3.Connection, shop_id: str) -> list[tuple[str, str, str]]:
    rows = conn.execute("SELECT supplier, wording, item_code FROM learned_aliases"
                        " WHERE shop_id = ? AND removed_at IS NULL ORDER BY alias_id", (shop_id,)).fetchall()
    return [(r["supplier"], r["wording"], r["item_code"]) for r in rows]


def submitted_price_points(conn: sqlite3.Connection, shop_id: str) -> list[PricePoint]:
    """Base-unit prices from lines this shop already submitted: price history for a shop with no Pilot report."""
    rows = conn.execute(
        "SELECT l.item_code, l.base_quantity, l.cost_cents, l.invoice_id, l.idx FROM lines l"
        " JOIN invoices i ON i.invoice_id = l.invoice_id JOIN batches b ON b.batch_id = i.batch_id"
        " WHERE b.shop_id = ? AND b.status = 'submitted' AND i.status = 'open' AND l.removed = 0"
        " AND l.item_code IS NOT NULL AND l.refusal IS NULL AND l.base_quantity > 0 AND l.cost_cents IS NOT NULL",
        (shop_id,)).fetchall()
    return [PricePoint(r["item_code"], r["base_quantity"], r["cost_cents"] / 100, f"submitted:{r['invoice_id']}",
                       r["idx"]) for r in rows]


class Keys:
    """Each process's cache of shop keys, rebuilt when an export file or the learned aliases change."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: dict[str, tuple[tuple, ShopKey]] = {}
        self._lock = threading.Lock()

    def shops(self) -> dict[str, Shop]:
        return load_shops(self.settings)

    def get(self, conn: sqlite3.Connection, shop_id: str) -> ShopKey:
        shop = self.shops().get(shop_id)
        if shop is None:
            raise LookupError(f"no shop '{shop_id}' is configured")
        stock_file = latest(self.settings.pilot_dir, shop.stock_glob)
        if stock_file is None:
            raise FileNotFoundError(f"no Stock Valuation export for {shop.name} in {self.settings.pilot_dir}"
                                    f" (looked for {shop.stock_glob})")
        purchases_file = latest(self.settings.pilot_dir, shop.purchases_glob)
        aliases = conn.execute("SELECT COUNT(*), COALESCE(MAX(alias_id), 0), COUNT(removed_at) FROM learned_aliases"
                               " WHERE shop_id = ?", (shop_id,)).fetchone()
        submitted = conn.execute("SELECT COUNT(*), COALESCE(MAX(r.report_id), 0) FROM reports r"
                                 " JOIN batches b ON b.batch_id = r.batch_id WHERE b.shop_id = ?", (shop_id,)).fetchone()
        stamp = (str(stock_file), stock_file.stat().st_mtime,
                 str(purchases_file), purchases_file.stat().st_mtime if purchases_file else 0.0,
                 tuple(aliases), tuple(submitted))
        with self._lock:
            hit = self._cache.get(shop_id)
            if hit and hit[0] == stamp:
                return hit[1]
        key = self._build(conn, shop, stock_file, purchases_file)
        with self._lock:
            self._cache[shop_id] = (stamp, key)
        return key

    def _build(self, conn, shop: Shop, stock_file: Path, purchases_file: Path | None) -> ShopKey:
        from tools.pilot import load_purchasing_report
        stock = load_stock(stock_file)
        purchases = load_purchasing_report(purchases_file) if purchases_file else []
        matcher = Matcher(stock, history_from_purchases(purchases, stock),
                          aliases=build_aliases(learned_pairs(conn, shop.shop_id)))
        history = history_points(purchases, stock) + submitted_price_points(conn, shop.shop_id)
        return ShopKey(shop, stock_file, purchases_file, stock, matcher, price_medians(purchases, stock), history,
                       purchase_lines=len(purchases))

    def forget(self, shop_id: str | None = None) -> None:
        with self._lock:
            if shop_id is None:
                self._cache.clear()
            else:
                self._cache.pop(shop_id, None)


def alias_rows(conn: sqlite3.Connection, shop_id: str) -> list[dict]:
    """Learned aliases for the /key page, one row per wording and stock item, with how often each was learned.
    A wording learned for two different items is shown but not used (build_aliases leaves it out)."""
    rows = db.all_rows(
        conn, "SELECT supplier, normalised, item_code, item_name, MIN(wording) AS wording, COUNT(*) AS times,"
              " COUNT(DISTINCT source_batch) AS batches, MAX(created_at) AS last_learned,"
              " SUM(removed_at IS NULL) AS live FROM learned_aliases WHERE shop_id = ?"
              " GROUP BY supplier, normalised, item_code ORDER BY supplier, normalised, item_code", shop_id)
    codes: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        if r["live"]:
            codes.setdefault((r["supplier"], r["normalised"]), set()).add(r["item_code"])
    for r in rows:
        r["removed"] = not r["live"]
        r["conflict"] = len(codes.get((r["supplier"], r["normalised"]), ())) > 1
    return rows


def remove_alias(conn: sqlite3.Connection, shop_id: str, supplier: str, normalised: str, item_code: str) -> int:
    """A person removed a bad alias: it stops being used, stays on record, and is never learned again."""
    with db.transaction(conn):
        removed = conn.execute("UPDATE learned_aliases SET removed_at = ? WHERE shop_id = ? AND supplier = ?"
                               " AND normalised = ? AND item_code = ? AND removed_at IS NULL",
                               (db.iso(), shop_id, supplier, normalised, item_code)).rowcount
        if removed:
            db.event(conn, "alias_removed", shop_id=shop_id, supplier=supplier, normalised=normalised,
                     item_code=item_code, rows=removed)
    return removed
