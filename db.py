"""SQLite caching layer for product lookups and search results."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class CacheDB:
    """SQLite-based cache with TTL for product lookups and searches."""

    def __init__(self, db_path: str, *, search_ttl: int = 300) -> None:
        """Open (or create) the cache database at ``db_path``.

        ``search_ttl`` is the time-to-live in seconds for cached search results.
        """
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._search_ttl = search_ttl
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                key        TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                cached_at  REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS searches (
                query      TEXT PRIMARY KEY,
                results    TEXT NOT NULL,
                cached_at  REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- product cache --------------------------------------------------------

    def get_product(self, key: str) -> dict[str, Any] | None:
        """Return cached product data for ``key`` (barcode or 'food:<id>'), or None."""
        row = self._conn.execute(
            "SELECT data FROM products WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set_product(self, key: str, data: dict[str, Any]) -> None:
        """Store product ``data`` under ``key``, overwriting if it exists."""
        self._conn.execute(
            "INSERT OR REPLACE INTO products (key, data, cached_at) VALUES (?, ?, ?)",
            (key, json.dumps(data), time.time()),
        )
        self._conn.commit()

    # -- search cache ---------------------------------------------------------

    def get_search(self, query: str) -> list[dict[str, Any]] | None:
        """Return cached search results if not expired, or None."""
        row = self._conn.execute(
            "SELECT results, cached_at FROM searches WHERE query = ?", (query,)
        ).fetchone()
        if row is None:
            return None
        results_json, cached_at = row
        if time.time() - cached_at > self._search_ttl:
            return None
        return json.loads(results_json)

    def set_search(self, query: str, results: list[dict[str, Any]]) -> None:
        """Cache ``results`` for ``query``."""
        self._conn.execute(
            "INSERT OR REPLACE INTO searches (query, results, cached_at) VALUES (?, ?, ?)",
            (query, json.dumps(results), time.time()),
        )
        self._conn.commit()
