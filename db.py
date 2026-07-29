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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS food_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                food_id             TEXT NOT NULL,
                food_name           TEXT NOT NULL,
                serving_description TEXT NOT NULL DEFAULT '',
                amount_raw          TEXT NOT NULL DEFAULT '',
                amount_grams        REAL NOT NULL DEFAULT 0,
                servings_multiplier REAL NOT NULL DEFAULT 1.0,
                calories            REAL NOT NULL DEFAULT 0,
                protein             REAL NOT NULL DEFAULT 0,
                fat                 REAL NOT NULL DEFAULT 0,
                carbs               REAL NOT NULL DEFAULT 0,
                logged_at           REAL NOT NULL
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

    # -- food log -------------------------------------------------------------

    def log_food(
        self,
        user_id: int,
        food_id: str,
        food_name: str,
        *,
        serving_description: str = "",
        amount_raw: str = "",
        amount_grams: float = 0.0,
        servings_multiplier: float = 1.0,
        calories: float = 0.0,
        protein: float = 0.0,
        fat: float = 0.0,
        carbs: float = 0.0,
    ) -> int:
        """Insert a food log entry and return the row ``id``."""
        cur = self._conn.execute(
            """
            INSERT INTO food_log (user_id, food_id, food_name, serving_description,
                                  amount_raw, amount_grams, servings_multiplier,
                                  calories, protein, fat, carbs, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                food_id,
                food_name,
                serving_description,
                amount_raw,
                amount_grams,
                servings_multiplier,
                calories,
                protein,
                fat,
                carbs,
                time.time(),
            ),
        )
        self._conn.commit()
        lid = cur.lastrowid
        assert lid is not None, "lastrowid must not be None after INSERT"
        return lid

    def get_user_log(
        self, user_id: int, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return recent food log entries for ``user_id``, newest first."""
        rows = self._conn.execute(
            """
            SELECT id, user_id, food_id, food_name, serving_description,
                   amount_raw, amount_grams, servings_multiplier,
                   calories, protein, fat, carbs, logged_at
            FROM food_log
            WHERE user_id = ?
            ORDER BY logged_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "food_id": r[2],
                "food_name": r[3],
                "serving_description": r[4],
                "amount_raw": r[5],
                "amount_grams": r[6],
                "servings_multiplier": r[7],
                "calories": r[8],
                "protein": r[9],
                "fat": r[10],
                "carbs": r[11],
                "logged_at": r[12],
            }
            for r in rows
        ]

    def get_daily_totals(self, user_id: int) -> dict[str, float]:
        """Return total KBJU for ``user_id`` today (UTC)."""
        day_start = time.time() - (time.time() % 86400)
        row = self._conn.execute(
            """
            SELECT COALESCE(SUM(calories), 0),
                   COALESCE(SUM(protein), 0),
                   COALESCE(SUM(fat), 0),
                   COALESCE(SUM(carbs), 0)
            FROM food_log
            WHERE user_id = ? AND logged_at >= ?
            """,
            (user_id, day_start),
        ).fetchone()
        if row is None:
            return {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
        return {
            "calories": row[0],
            "protein": row[1],
            "fat": row[2],
            "carbs": row[3],
        }
