"""SQLite-хранилище: Приглашения, Привязки, черновики пачек и записанные пачки.

Черновик живёт в БД, а не в памяти процесса, — иначе перезапуск контейнера посреди
записи оставляет пользователя в неизвестности (решение 17).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    allowed      INTEGER NOT NULL DEFAULT 0,
    tz           TEXT,
    token        TEXT,
    token_secret TEXT,
    link_valid   INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    payload    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    entry_ids  TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- Пока не используется: создание Своих продуктов недоступно на Basic.
CREATE TABLE IF NOT EXISTS barcode_bindings (
    user_id INTEGER NOT NULL,
    barcode TEXT NOT NULL,
    food_id TEXT NOT NULL,
    PRIMARY KEY (user_id, barcode)
);
"""


@dataclass(slots=True)
class UserRow:
    user_id: int
    allowed: bool
    tz: str | None
    token: str | None
    token_secret: str | None
    link_valid: bool

    @property
    def is_linked(self) -> bool:
        return bool(self.token and self.token_secret and self.link_valid)


class Storage:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self._path)
        except (OSError, sqlite3.OperationalError) as exc:
            # Иначе наружу выпадает стек из недр aiosqlite вперемешку с «Event loop is
            # closed» из рабочего потока, и причина — права на каталог — теряется.
            raise SystemExit(
                f"Не удалось открыть базу {self._path}: {exc}\n"
                f"Каталог состояния должен быть доступен на запись пользователю "
                f"uid={os.getuid()}. Проверь монтирование /data."
            ) from exc
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage.open() не вызван")
        return self._db

    # --- пользователи -----------------------------------------------------

    async def ensure_user(self, user_id: int) -> UserRow:
        await self.db.execute(
            "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
            (user_id, int(time.time())),
        )
        await self.db.commit()
        user = await self.get_user(user_id)
        assert user
        return user

    async def get_user(self, user_id: int) -> UserRow | None:
        async with self.db.execute(
            "SELECT user_id, allowed, tz, token, token_secret, link_valid "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return UserRow(
            user_id=row["user_id"],
            allowed=bool(row["allowed"]),
            tz=row["tz"],
            token=row["token"],
            token_secret=row["token_secret"],
            link_valid=bool(row["link_valid"]),
        )

    async def allow(self, user_id: int) -> None:
        await self.db.execute(
            "INSERT INTO users (user_id, allowed, created_at) VALUES (?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET allowed = 1",
            (user_id, int(time.time())),
        )
        await self.db.commit()

    async def save_link(self, user_id: int, token: str, token_secret: str) -> None:
        await self.db.execute(
            "UPDATE users SET token = ?, token_secret = ?, link_valid = 1 WHERE user_id = ?",
            (token, token_secret, user_id),
        )
        await self.db.commit()

    async def invalidate_link(self, user_id: int) -> None:
        """Токен отозван: Привязку помечаем, но не удаляем — часовой пояс переживёт."""
        await self.db.execute(
            "UPDATE users SET link_valid = 0 WHERE user_id = ?", (user_id,)
        )
        await self.db.commit()

    async def set_tz(self, user_id: int, tz: str) -> None:
        await self.db.execute("UPDATE users SET tz = ? WHERE user_id = ?", (tz, user_id))
        await self.db.commit()

    # --- черновики --------------------------------------------------------

    async def save_draft(self, user_id: int, payload: dict) -> int:
        cursor = await self.db.execute(
            "INSERT INTO drafts (user_id, payload, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(payload, ensure_ascii=False), int(time.time())),
        )
        await self.db.commit()
        return int(cursor.lastrowid or 0)

    async def update_draft(self, draft_id: int, payload: dict) -> None:
        await self.db.execute(
            "UPDATE drafts SET payload = ? WHERE draft_id = ?",
            (json.dumps(payload, ensure_ascii=False), draft_id),
        )
        await self.db.commit()

    async def get_draft(self, draft_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT payload FROM drafts WHERE draft_id = ?", (draft_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return json.loads(row["payload"]) if row else None

    async def delete_draft(self, draft_id: int) -> None:
        await self.db.execute("DELETE FROM drafts WHERE draft_id = ?", (draft_id,))
        await self.db.commit()

    # --- записанные пачки (для /undo) -------------------------------------

    async def save_batch(self, user_id: int, entry_ids: list[str]) -> None:
        await self.db.execute(
            "INSERT INTO batches (user_id, entry_ids, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(entry_ids), int(time.time())),
        )
        await self.db.commit()

    async def last_batch(self, user_id: int) -> tuple[int, list[str]] | None:
        async with self.db.execute(
            "SELECT batch_id, entry_ids FROM batches WHERE user_id = ? "
            "ORDER BY batch_id DESC LIMIT 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return row["batch_id"], json.loads(row["entry_ids"])

    async def delete_batch(self, batch_id: int) -> None:
        await self.db.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))
        await self.db.commit()
