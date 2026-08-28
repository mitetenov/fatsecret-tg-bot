"""Запись в FatSecret: частичные ошибки не должны создавать дубли."""

import asyncio

from fsbot.bot.pipeline import create_own_food, draft_from_food, write_draft
from fsbot.fatsecret.client import FatSecretError


FOOD = {
    "food_id": "77",
    "food_name": "Milk",
    "brand_name": "Sante",
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


def item(food_id: str, name: str) -> dict:
    return {
        "name_ru": name,
        "title": name,
        "food_id": food_id,
        "serving_id": f"s-{food_id}",
        "units": 1,
        "status": "pending",
    }


def draft(*items: dict) -> dict:
    return {"day": "2026-08-28", "meal": "lunch", "items": list(items)}


class EntryFatSecret:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def create_entry(self, token, secret, **params):
        self.calls.append(params)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retry_writes_only_failed_items_and_never_duplicates_successes():
    payload = draft(item("1", "Овсянка"), item("2", "Молоко"))
    first = EntryFatSecret(["entry-1", FatSecretError(2, "временная ошибка")])

    report1 = asyncio.run(write_draft(first, payload, "token", "secret"))

    assert report1.entry_ids == ["entry-1"]
    assert report1.failed == [("Молоко", "временная ошибка")]
    assert payload["items"][0]["status"] == "written"
    assert payload["items"][1]["status"] == "failed"

    retry = EntryFatSecret(["entry-2"])
    report2 = asyncio.run(write_draft(retry, payload, "token", "secret"))

    assert report2.entry_ids == ["entry-2"]
    assert [call["food_id"] for call in retry.calls] == ["2"]
    assert payload["items"][0]["entry_id"] == "entry-1"
    assert payload["items"][1]["entry_id"] == "entry-2"


def test_invalid_token_stops_batch_before_later_items():
    payload = draft(item("1", "Первый"), item("2", "Второй"))
    fs = EntryFatSecret([FatSecretError(9, "token revoked"), "must-not-be-used"])

    report = asyncio.run(write_draft(fs, payload, "token", "secret"))

    assert report.token_invalid is True
    assert report.failed == [("Первый", "token revoked")]
    assert [call["food_id"] for call in fs.calls] == ["1"]
    assert payload["items"][1]["status"] == "pending"


def test_item_without_food_is_reported_without_external_write():
    missing = {"name_ru": "Неизвестное", "food_id": None, "status": "pending"}
    fs = EntryFatSecret([])

    report = asyncio.run(write_draft(fs, draft(missing), "token", "secret"))

    assert report.failed == [("Неизвестное", "не найден в базе")]
    assert fs.calls == []


class OwnFoodFatSecret:
    def __init__(self):
        self.created = []
        self.get_calls = 0

    async def create_food(self, token, secret, **params):
        self.created.append(params)
        return "77"

    async def get_food(self, food_id):
        self.get_calls += 1
        return FOOD


def test_create_own_liquid_preserves_100ml_basis_and_amount():
    fs = OwnFoodFatSecret()
    liquid = {
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

    food_id = asyncio.run(create_own_food(fs, liquid, "token", "secret"))

    assert food_id == "77"
    assert fs.created[0]["basis_unit"] == "ml"
    assert liquid["food_id"] == "77"
    assert liquid["portion"] == "450 ml"
    assert liquid["units"] == 450
    assert "creatable" not in liquid


def test_exact_barcode_draft_fetches_food_only_once():
    fs = OwnFoodFatSecret()

    result = asyncio.run(draft_from_food(fs, "77", "UTC", amount=250, unit="ml"))

    assert fs.get_calls == 1
    assert result["confidence"] == 1.0
    assert result["needs_review"] is False
    assert result["items"][0]["portion"] == "250 ml"
