"""Кнопки подтверждения: сомнительный черновик нельзя записать одним тапом."""

import asyncio
import copy
from datetime import timedelta
from types import SimpleNamespace

from fsbot.bot import ui
from fsbot.bot.handlers import callbacks
from fsbot.domain.daybounds import resolve


def pending_draft() -> dict:
    return {
        "day": "2026-08-28",
        "meal": "lunch",
        "confidence": 0.55,
        "needs_review": True,
        "items": [
            {
                "name_ru": "Суп",
                "title": "Soup",
                "food_id": "9",
                "serving_id": "91",
                "units": 3,
                "portion": "300 g",
                "kcal": 240,
                "protein": 12,
                "fat": 6,
                "carbohydrate": 30,
                "status": "pending",
            }
        ],
    }


class FakeStorage:
    def __init__(self, draft):
        self.draft = draft
        self.updated = []
        self.deleted = []
        self.batches = []
        self.bindings = []
        self.user = SimpleNamespace(
            user_id=42,
            token="token",
            token_secret="secret",
            is_linked=True,
            tz="UTC",
        )

    async def get_draft(self, draft_id):
        return self.draft

    async def update_draft(self, draft_id, payload):
        self.draft = payload
        self.updated.append(copy.deepcopy(payload))

    async def delete_draft(self, draft_id):
        self.deleted.append(draft_id)
        self.draft = None

    async def get_user(self, user_id):
        return self.user

    async def save_batch(self, user_id, entry_ids):
        self.batches.append((user_id, entry_ids))

    async def invalidate_link(self, user_id):
        raise AssertionError("валидный токен не должен инвалидироваться")

    async def bind_barcode(self, user_id, barcode, food_id):
        self.bindings.append((user_id, barcode, food_id))


class FakeMessage:
    def __init__(self):
        self.markups = []
        self.texts = []
        self.answers = []

    async def edit_reply_markup(self, reply_markup):
        self.markups.append(reply_markup)

    async def edit_text(self, text, reply_markup=None):
        self.texts.append((text, reply_markup))

    async def answer(self, text):
        self.answers.append(text)


class FakeCall:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=42)
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class FakeState:
    def __init__(self):
        self.state = None
        self.data = {}

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **values):
        self.data.update(values)


class EntryFatSecret:
    def __init__(self):
        self.entries = []

    async def create_entry(self, token, secret, **params):
        self.entries.append(params)
        return "entry-9"


LIQUID_FOOD = {
    "food_id": "77",
    "food_name": "Milk",
    "servings": {
        "serving": {
            "serving_id": "771",
            "serving_description": "100 ml",
            "metric_serving_amount": "100",
            "metric_serving_unit": "ml",
            "number_of_units": "100",
            "calories": "60",
            "protein": "3",
            "fat": "3.2",
            "carbohydrate": "4.7",
        }
    },
}


class OwnFoodFatSecret(EntryFatSecret):
    def __init__(self):
        super().__init__()
        self.created = []

    async def create_food(self, token, secret, **params):
        self.created.append(params)
        return "77"

    async def get_food(self, food_id):
        return LIQUID_FOOD


def invoke(call, storage, fs, state=None):
    cfg = SimpleNamespace(default_tz="UTC")
    return asyncio.run(callbacks(call, state or FakeState(), storage, fs, cfg))


def test_review_then_write_is_a_real_two_step_confirmation():
    storage = FakeStorage(pending_draft())
    fs = EntryFatSecret()

    review = FakeCall(ui.cb(1, ui.REVIEW))
    invoke(review, storage, fs)

    assert fs.entries == []
    assert storage.draft["review_prompted"] is True
    texts = [button["text"] for row in review.message.markups[0]["inline_keyboard"] for button in row]
    assert "⚠️ Записать всё равно" in texts
    assert review.answers[-1][1] is True

    confirm = FakeCall(ui.cb(1, ui.WRITE))
    invoke(confirm, storage, fs)

    assert [entry["food_id"] for entry in fs.entries] == ["9"]
    assert storage.batches == [(42, ["entry-9"])]
    assert storage.deleted == [1]
    assert storage.draft is None
    assert "✅ Soup" in confirm.message.texts[-1][0]


def test_stale_write_button_cannot_bypass_low_confidence_review():
    storage = FakeStorage(pending_draft())
    fs = EntryFatSecret()

    stale_write = FakeCall(ui.cb(1, ui.WRITE))
    invoke(stale_write, storage, fs)

    assert fs.entries == []
    assert storage.draft["review_prompted"] is True
    assert stale_write.answers == [("Нужна дополнительная проверка", True)]


def test_create_liquid_callback_binds_barcode_to_new_food():
    payload = {
        "day": "2026-08-28",
        "meal": "lunch",
        "barcode": "0036000291452",
        "confidence": 0.9,
        "needs_review": False,
        "items": [
            {
                "name_ru": "Молоко",
                "amount": 450,
                "unit": "ml",
                "confidence": 0.9,
                "candidates": [],
                "creatable": {
                    "name": "Milk",
                    "brand": "Sante",
                    "kcal": 60,
                    "protein": 3,
                    "fat": 3.2,
                    "carbs": 4.7,
                    "basis_unit": "ml",
                },
            }
        ],
    }
    storage = FakeStorage(payload)
    fs = OwnFoodFatSecret()

    invoke(FakeCall(ui.cb(1, ui.CREATE_FOOD, 0)), storage, fs)

    assert fs.created[0]["basis_unit"] == "ml"
    assert storage.bindings == [(42, "0036000291452", "77")]
    assert storage.draft["items"][0]["food_id"] == "77"
    assert "creatable" not in storage.draft["items"][0]


def test_expired_and_cancelled_drafts_never_reach_fatsecret():
    fs = EntryFatSecret()
    expired_storage = FakeStorage(None)
    expired = FakeCall(ui.cb(99, ui.WRITE))

    invoke(expired, expired_storage, fs)

    assert expired.answers == [("Черновик уже неактуален", True)]
    assert fs.entries == []

    cancel_storage = FakeStorage(pending_draft())
    cancel = FakeCall(ui.cb(1, ui.CANCEL))
    invoke(cancel, cancel_storage, fs)

    assert cancel_storage.deleted == [1]
    assert "в дневник ничего не пошло" in cancel.message.texts[-1][0]
    assert fs.entries == []


def test_unlinked_user_cannot_write_even_a_ready_draft():
    payload = pending_draft()
    payload["needs_review"] = False
    storage = FakeStorage(payload)
    storage.user.is_linked = False
    fs = EntryFatSecret()
    call = FakeCall(ui.cb(1, ui.WRITE))

    invoke(call, storage, fs)

    assert call.answers == [("Сначала /link", True)]
    assert fs.entries == []
    assert storage.deleted == []


def test_candidate_change_recalculates_item_and_resets_review_prompt():
    food = {
        "servings": {
            "serving": {
                "serving_id": "22",
                "serving_description": "100 g",
                "metric_serving_amount": "100",
                "metric_serving_unit": "g",
                "number_of_units": "100",
                "calories": "120",
                "protein": "5",
                "fat": "4",
                "carbohydrate": "18",
            }
        }
    }
    payload = pending_draft()
    payload["review_prompted"] = True
    payload["items"][0].update(
        amount=150,
        unit="g",
        confidence=0.8,
        candidates=[{"food_id": "22", "title": "Other Soup", "food": food}],
    )
    storage = FakeStorage(payload)

    invoke(FakeCall(ui.cb(1, ui.PICK_CANDIDATE, "0.0")), storage, EntryFatSecret())

    changed = storage.draft["items"][0]
    assert changed["food_id"] == "22"
    assert changed["portion"] == "150 g"
    assert changed["kcal"] == 180
    assert "review_prompted" not in storage.draft


def test_amount_and_date_callbacks_keep_edit_context():
    storage = FakeStorage(pending_draft())
    state = FakeState()

    invoke(FakeCall(ui.cb(1, ui.ASK_GRAMS, 0)), storage, EntryFatSecret(), state)

    assert state.data == {"draft_id": 1, "index": 0}
    assert state.state is not None

    expected, _ = resolve("UTC")
    invoke(FakeCall(ui.cb(1, ui.PICK_DATE, "yesterday")), storage, EntryFatSecret())

    assert storage.draft["day"] == (expected - timedelta(days=1)).isoformat()
