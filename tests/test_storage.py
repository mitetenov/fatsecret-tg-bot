"""SQLite хранит состояние между апдейтами и перезапусками процесса."""

import asyncio

from fsbot.storage import Storage


def test_user_link_lifecycle_preserves_timezone_when_token_is_revoked(tmp_path):
    async def scenario():
        storage = Storage(tmp_path / "state.sqlite3")
        await storage.open()
        try:
            created = await storage.ensure_user(42)
            assert not created.allowed
            assert not created.is_linked

            await storage.allow(42)
            await storage.save_link(42, "token", "secret")
            await storage.set_tz(42, "Europe/Berlin")
            linked = await storage.get_user(42)
            assert linked.allowed
            assert linked.is_linked
            assert linked.tz == "Europe/Berlin"

            await storage.invalidate_link(42)
            revoked = await storage.get_user(42)
            assert not revoked.is_linked
            assert revoked.token == "token"
            assert revoked.tz == "Europe/Berlin"
        finally:
            await storage.close()

    asyncio.run(scenario())


def test_draft_roundtrip_keeps_confidence_and_100ml_basis(tmp_path):
    async def scenario():
        storage = Storage(tmp_path / "state.sqlite3")
        await storage.open()
        try:
            await storage.ensure_user(42)
            first = {
                "confidence": 0.55,
                "needs_review": True,
                "items": [{"nutrition_basis": "ml", "amount": 450}],
            }
            draft_id = await storage.save_draft(42, first)
            assert await storage.get_draft(draft_id) == first

            updated = {**first, "review_prompted": True}
            await storage.update_draft(draft_id, updated)
            assert await storage.last_draft(42) == (draft_id, updated)

            await storage.delete_draft(draft_id)
            assert await storage.get_draft(draft_id) is None
        finally:
            await storage.close()

    asyncio.run(scenario())


def test_barcode_binding_upsert_and_last_batch_lifecycle(tmp_path):
    async def scenario():
        storage = Storage(tmp_path / "state.sqlite3")
        await storage.open()
        try:
            await storage.ensure_user(42)
            await storage.bind_barcode(42, "0036000291452", "food-1")
            await storage.bind_barcode(42, "0036000291452", "food-2")
            assert await storage.bound_food(42, "0036000291452") == "food-2"

            await storage.save_batch(42, ["entry-1", "entry-2"])
            batch_id, entries = await storage.last_batch(42)
            assert entries == ["entry-1", "entry-2"]

            await storage.delete_batch(batch_id)
            assert await storage.last_batch(42) is None
        finally:
            await storage.close()

    asyncio.run(scenario())
