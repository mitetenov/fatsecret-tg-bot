"""SQLite-хранилище: Приглашения, Привязки, черновики пачек и записанные пачки.

Черновик живёт в БД, а не в памяти процесса, — иначе перезапуск контейнера посреди
записи оставляет пользователя в неизвестности (решение 17).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import asyncio
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
    created_at INTEGER NOT NULL,
    writing    INTEGER NOT NULL DEFAULT 0,
    writing_since INTEGER
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    entry_ids  TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    draft_id   INTEGER,
    undoing    INTEGER NOT NULL DEFAULT 0,
    undoing_since INTEGER
);

-- Связка «штрих-код → Свой продукт»: второе сканирование обходится без фото.
CREATE TABLE IF NOT EXISTS barcode_bindings (
    user_id INTEGER NOT NULL,
    barcode TEXT NOT NULL,
    food_id TEXT NOT NULL,
    PRIMARY KEY (user_id, barcode)
);
"""

CLAIM_TTL_SECONDS = 3600


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
        self._write_lock = asyncio.Lock()

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
        async with self._db.execute("PRAGMA table_info(drafts)") as cursor:
            draft_columns = {row["name"] for row in await cursor.fetchall()}
        if "writing" not in draft_columns:
            await self._db.execute("ALTER TABLE drafts ADD COLUMN writing INTEGER NOT NULL DEFAULT 0")
        if "writing_since" not in draft_columns:
            await self._db.execute("ALTER TABLE drafts ADD COLUMN writing_since INTEGER")
        async with self._db.execute("PRAGMA table_info(batches)") as cursor:
            batch_columns = {row["name"] for row in await cursor.fetchall()}
        if "draft_id" not in batch_columns:
            await self._db.execute("ALTER TABLE batches ADD COLUMN draft_id INTEGER")
        if "undoing" not in batch_columns:
            await self._db.execute("ALTER TABLE batches ADD COLUMN undoing INTEGER NOT NULL DEFAULT 0")
        if "undoing_since" not in batch_columns:
            await self._db.execute("ALTER TABLE batches ADD COLUMN undoing_since INTEGER")
        await self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS batches_draft_id ON batches(draft_id)"
        )
        # Another process may still be working. Only expired claims are released;
        # the item-level 'writing' status still prevents an uncertain retry.
        expiry = int(time.time()) - CLAIM_TTL_SECONDS
        await self._db.execute(
            "UPDATE drafts SET writing = 0, writing_since = NULL WHERE writing = 1 "
            "AND (writing_since IS NULL OR writing_since < ?)", (expiry,),
        )
        await self._db.execute(
            "UPDATE batches SET undoing = 0, undoing_since = NULL WHERE undoing = 1 "
            "AND (undoing_since IS NULL OR undoing_since < ?)", (expiry,),
        )
        await self._db.execute("DELETE FROM batches WHERE entry_ids = '[]'")
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

    async def update_draft(self, draft_id: int, payload: dict, user_id: int) -> None:
        await self.db.execute(
            "UPDATE drafts SET payload = ? WHERE draft_id = ? AND user_id = ?",
            (json.dumps(payload, ensure_ascii=False), draft_id, user_id),
        )
        await self.db.commit()

    async def get_draft(self, draft_id: int, user_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT payload FROM drafts WHERE draft_id = ? AND user_id = ?",
            (draft_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return json.loads(row["payload"]) if row else None

    async def claim_draft(self, draft_id: int, user_id: int) -> dict | None:
        """Claim a draft for one callback, including across concurrent bot processes."""
        async with self._write_lock:
            now = int(time.time())
            async with self.db.execute(
                "UPDATE drafts SET writing = 1, writing_since = ? "
                "WHERE draft_id = ? AND user_id = ? "
                "AND (writing = 0 OR writing_since IS NULL OR writing_since < ?) "
                "AND NOT EXISTS "
                "(SELECT 1 FROM batches WHERE batches.draft_id = drafts.draft_id "
                "AND batches.undoing = 1) RETURNING payload",
                (now, draft_id, user_id, now - CLAIM_TTL_SECONDS),
            ) as cursor:
                row = await cursor.fetchone()
            await self.db.commit()
        return json.loads(row["payload"]) if row else None

    async def release_draft(self, draft_id: int, user_id: int) -> None:
        await self.db.execute(
            "UPDATE drafts SET writing = 0, writing_since = NULL "
            "WHERE draft_id = ? AND user_id = ?",
            (draft_id, user_id),
        )
        await self.db.commit()

    async def last_draft(self, user_id: int) -> tuple[int, dict] | None:
        """Последний показанный черновик — чтобы голое число можно было понять как
        количество, даже если человек не нажимал «Указать количество»."""
        async with self.db.execute(
            "SELECT draft_id, payload FROM drafts WHERE user_id = ? "
            "ORDER BY draft_id DESC LIMIT 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return (row["draft_id"], json.loads(row["payload"])) if row else None

    async def delete_draft(self, draft_id: int, user_id: int) -> None:
        await self.db.execute(
            "DELETE FROM drafts WHERE draft_id = ? AND user_id = ?", (draft_id, user_id)
        )
        await self.db.commit()

    # --- Связки штрих-кодов -----------------------------------------------

    async def bound_food(self, user_id: int, barcode: str) -> str | None:
        async with self.db.execute(
            "SELECT food_id FROM barcode_bindings WHERE user_id = ? AND barcode = ?",
            (user_id, barcode),
        ) as cursor:
            row = await cursor.fetchone()
        return row["food_id"] if row else None

    async def bind_barcode(self, user_id: int, barcode: str, food_id: str) -> None:
        await self.db.execute(
            "INSERT INTO barcode_bindings (user_id, barcode, food_id) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, barcode) DO UPDATE SET food_id = excluded.food_id",
            (user_id, barcode, food_id),
        )
        await self.db.commit()

    # --- записанные пачки (для /undo) -------------------------------------

    async def save_batch(self, user_id: int, entry_ids: list[str]) -> None:
        await self.db.execute(
            "INSERT INTO batches (user_id, entry_ids, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(entry_ids), int(time.time())),
        )
        await self.db.commit()

    async def record_write_success(
        self, draft_id: int, user_id: int, payload: dict, entry_id: str
    ) -> None:
        """Persist the item result and its undo ID in one SQLite transaction."""
        async with self._write_lock:
            # A separate connection keeps unrelated writes on self.db from
            # accidentally joining and committing this transaction.
            async with aiosqlite.connect(self._path) as tx:
                await tx.execute("BEGIN IMMEDIATE")
                try:
                    async with tx.execute(
                        "SELECT entry_ids FROM batches WHERE draft_id = ? AND user_id = ?",
                        (draft_id, user_id),
                    ) as cursor:
                        row = await cursor.fetchone()
                    ids = json.loads(row[0]) if row else []
                    if entry_id not in ids:
                        ids.append(entry_id)
                    await tx.execute(
                        "INSERT INTO batches (user_id, entry_ids, created_at, draft_id) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(draft_id) DO UPDATE SET "
                        "entry_ids = excluded.entry_ids",
                        (user_id, json.dumps(ids), int(time.time()), draft_id),
                    )
                    await tx.execute(
                        "UPDATE drafts SET payload = ? WHERE draft_id = ? AND user_id = ?",
                        (json.dumps(payload, ensure_ascii=False), draft_id, user_id),
                    )
                    await tx.commit()
                except Exception:
                    await tx.rollback()
                    raise

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

    async def claim_last_batch(self, user_id: int) -> tuple[int, list[str]] | None:
        """Allow one undo at a time and wait until its draft stops writing."""
        async with self._write_lock:
            now = int(time.time())
            async with self.db.execute(
                "UPDATE batches SET undoing = 1, undoing_since = ? WHERE batch_id = "
                "(SELECT batch_id FROM batches WHERE user_id = ? "
                "ORDER BY batch_id DESC LIMIT 1) "
                "AND (undoing = 0 OR undoing_since IS NULL OR undoing_since < ?) "
                "AND NOT EXISTS (SELECT 1 FROM drafts WHERE drafts.draft_id = batches.draft_id "
                "AND drafts.writing = 1) RETURNING batch_id, entry_ids",
                (now, user_id, now - CLAIM_TTL_SECONDS),
            ) as cursor:
                row = await cursor.fetchone()
            await self.db.commit()
        return (row["batch_id"], json.loads(row["entry_ids"])) if row else None

    async def release_batch(self, batch_id: int, user_id: int) -> None:
        await self.db.execute(
            "DELETE FROM batches WHERE batch_id = ? AND user_id = ? AND entry_ids = '[]'",
            (batch_id, user_id),
        )
        await self.db.execute(
            "UPDATE batches SET undoing = 0, undoing_since = NULL "
            "WHERE batch_id = ? AND user_id = ?",
            (batch_id, user_id),
        )
        await self.db.commit()

    async def delete_batch(self, batch_id: int) -> None:
        await self.db.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))
        await self.db.commit()

    async def update_batch_remaining(self, batch_id: int, entry_ids: list[str]) -> None:
        if entry_ids:
            await self.db.execute(
                "UPDATE batches SET entry_ids = ?, undoing = 0, undoing_since = NULL "
                "WHERE batch_id = ?",
                (json.dumps(entry_ids), batch_id),
            )
        else:
            await self.db.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))
        await self.db.commit()

    async def record_undo_success(self, batch_id: int, user_id: int, entry_id: str) -> None:
        """Persist each confirmed deletion before making another API call."""
        async with self._write_lock:
            async with aiosqlite.connect(self._path) as tx:
                await tx.execute("BEGIN IMMEDIATE")
                try:
                    async with tx.execute(
                        "SELECT draft_id, entry_ids FROM batches WHERE batch_id = ? AND user_id = ? "
                        "AND undoing = 1", (batch_id, user_id),
                    ) as cursor:
                        row = await cursor.fetchone()
                    if row is None:
                        raise RuntimeError("Пачка для отмены уже неактуальна")
                    draft_id = row[0]
                    ids = json.loads(row[1])
                    if entry_id not in ids:
                        raise RuntimeError("Запись уже удалена из пачки")
                    if draft_id is not None:
                        async with tx.execute(
                            "SELECT payload FROM drafts WHERE draft_id = ? AND user_id = ?",
                            (draft_id, user_id),
                        ) as cursor:
                            draft_row = await cursor.fetchone()
                        if draft_row:
                            payload = json.loads(draft_row[0])
                            for item in payload.get("items", []):
                                if item.get("entry_id") == entry_id:
                                    item["status"] = "undone"
                            await tx.execute(
                                "UPDATE drafts SET payload = ? WHERE draft_id = ? AND user_id = ?",
                                (json.dumps(payload, ensure_ascii=False), draft_id, user_id),
                            )
                    ids.remove(entry_id)
                    await tx.execute(
                        "UPDATE batches SET entry_ids = ? WHERE batch_id = ?",
                        (json.dumps(ids), batch_id),
                    )
                    await tx.commit()
                except Exception:
                    await tx.rollback()
                    raise
