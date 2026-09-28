import asyncio
import json
import sqlite3

import pytest

from fsbot.bot.pipeline import write_draft
from fsbot.fatsecret.client import FatSecretUnknownOutcome
from fsbot.storage import Storage


def draft():
    return {
        "day": "2026-09-28", "meal": "lunch",
        "items": [
            {"name_ru": "A", "food_id": "a", "serving_id": "1", "units": 1, "status": "pending"},
            {"name_ru": "B", "food_id": "b", "serving_id": "2", "units": 1, "status": "pending"},
        ],
    }


class FakeFS:
    def __init__(self):
        self.calls = []
        self.fail_b = True

    async def create_entry(self, *_args, **kwargs):
        self.calls.append(kwargs["food_id"])
        if kwargs["food_id"] == "b" and self.fail_b:
            raise FatSecretUnknownOutcome("timeout")
        return "entry-" + kwargs["food_id"]


@pytest.mark.asyncio
async def test_uncertain_write_is_not_retried_and_known_ids_share_one_undo_batch(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    await storage.open()
    try:
        await storage.ensure_user(1)
        data = draft()
        draft_id = await storage.save_draft(1, data)
        fs = FakeFS()

        async def persist():
            await storage.update_draft(draft_id, data, 1)

        async def record(entry_id):
            await storage.record_write_success(draft_id, 1, data, entry_id)

        first = await write_draft(fs, data, "t", "s", persist, record)
        assert first.entry_ids == ["entry-a"]
        assert data["items"][1]["status"] == "unknown"
        assert (await storage.last_batch(1))[1] == ["entry-a"]

        fs.fail_b = False
        second = await write_draft(fs, data, "t", "s", persist, record)
        assert second.entry_ids == []
        assert fs.calls == ["a", "b"]

        # A later successful retry of a known failure appends to the same batch.
        data["items"][1]["status"] = "failed"
        third = await write_draft(fs, data, "t", "s", persist, record)
        assert third.entry_ids == ["entry-b"]
        assert (await storage.last_batch(1))[1] == ["entry-a", "entry-b"]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_claim_is_owned_and_only_one_callback_can_write(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    await storage.open()
    try:
        await storage.ensure_user(1)
        await storage.ensure_user(2)
        draft_id = await storage.save_draft(1, draft())
        assert await storage.get_draft(draft_id, 2) is None
        claims = await asyncio.gather(
            storage.claim_draft(draft_id, 1), storage.claim_draft(draft_id, 1)
        )
        assert sum(claim is not None for claim in claims) == 1
        assert await storage.claim_draft(draft_id, 2) is None
        await storage.release_draft(draft_id, 1)
        assert await storage.claim_draft(draft_id, 1) is not None
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_existing_database_is_migrated_without_losing_data(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE drafts (draft_id INTEGER PRIMARY KEY, user_id INTEGER, payload TEXT, created_at INTEGER)")
        db.execute("CREATE TABLE batches (batch_id INTEGER PRIMARY KEY, user_id INTEGER, entry_ids TEXT, created_at INTEGER)")
        db.execute("INSERT INTO drafts VALUES (1, 1, ?, 0)", (json.dumps(draft()),))
        db.execute("INSERT INTO batches VALUES (1, 1, '[\"old-entry\"]', 0)")
    storage = Storage(path)
    await storage.open()
    try:
        assert await storage.get_draft(1, 1) == draft()
        assert (await storage.last_batch(1))[1] == ["old-entry"]
        assert await storage.claim_draft(1, 1) is not None
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_undo_claim_blocks_second_undo_and_draft_write(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    await storage.open()
    try:
        await storage.ensure_user(1)
        data = draft()
        data["items"][0].update(status="written", entry_id="entry-a")
        draft_id = await storage.save_draft(1, data)
        await storage.record_write_success(draft_id, 1, data, "entry-a")
        batch_id, ids = await storage.claim_last_batch(1)
        assert ids == ["entry-a"]
        assert await storage.claim_last_batch(1) is None
        assert await storage.claim_draft(draft_id, 1) is None

        await storage.record_undo_success(batch_id, 1, "entry-a")
        assert (await storage.get_draft(draft_id, 1))["items"][0]["status"] == "undone"
        assert (await storage.last_batch(1))[1] == []
        await storage.release_batch(batch_id, 1)
        assert await storage.last_batch(1) is None
        assert await storage.claim_draft(draft_id, 1) is not None
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_undo_waits_for_active_draft_write_and_keeps_failed_ids(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    await storage.open()
    try:
        await storage.ensure_user(1)
        data = draft()
        draft_id = await storage.save_draft(1, data)
        await storage.record_write_success(draft_id, 1, data, "entry-a")
        assert await storage.claim_draft(draft_id, 1) is not None
        assert await storage.claim_last_batch(1) is None
        await storage.release_draft(draft_id, 1)

        batch_id, _ = await storage.claim_last_batch(1)
        await storage.release_batch(batch_id, 1)
        assert (await storage.last_batch(1))[1] == ["entry-a"]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_opening_second_process_preserves_fresh_claims(tmp_path):
    path = tmp_path / "shared.sqlite3"
    first, second = Storage(path), Storage(path)
    await first.open()
    try:
        await first.ensure_user(1)
        draft_id = await first.save_draft(1, draft())
        assert await first.claim_draft(draft_id, 1) is not None
        await second.open()
        try:
            assert await second.claim_draft(draft_id, 1) is None
            await first.release_draft(draft_id, 1)
            await first.record_write_success(draft_id, 1, draft(), "entry-a")
            batch_id, _ = await first.claim_last_batch(1)
            await second.close()
            await second.open()
            assert await second.claim_last_batch(1) is None
            await first.release_batch(batch_id, 1)
        finally:
            await second.close()
    finally:
        await first.close()
