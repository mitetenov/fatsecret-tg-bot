import json
from datetime import date, datetime, timezone

import httpx
import pytest

from fsbot.bot import pipeline
from fsbot.bot.pipeline import WriteReport, apply_candidate, apply_portion_choice, build_draft, draft_from_web, render_report, shift_day
from fsbot.domain.barcodes import expand_upce, fatsecret_gtin13, plausible, valid_gtin
from fsbot.domain.nutrition import validated_nutrition
from fsbot.fatsecret.client import FoodSummary
from fsbot.llm.openrouter import OpenRouter
from fsbot.llm.parsing import Recognition, RecognizedItem, parse_recognition


def test_gtin_checksum_and_conversion_before_fatsecret():
    assert valid_gtin("96385074")
    assert not valid_gtin("96385075")
    assert fatsecret_gtin13("96385074") == "0000096385074"
    assert fatsecret_gtin13("036000291452") == "0036000291452"
    assert fatsecret_gtin13("4006381333931") == "4006381333931"
    assert fatsecret_gtin13("00012345600012") is None
    assert expand_upce("01234565") == "012345000065"
    assert plausible([("01234565", "UPCE")]) == "012345000065"
    assert plausible([("96385075", "EAN8")]) is None
    assert plausible([("9780306406157", "ISBN13")]) is None


@pytest.mark.parametrize("values", [
    {"kcal": float("nan"), "protein": 1, "fat": 1, "carbs": 1},
    {"kcal": float("inf"), "protein": 1, "fat": 1, "carbs": 1},
    {"kcal": 100, "protein": -1, "fat": 1, "carbs": 1},
    {"kcal": 100, "protein": 60, "fat": 60, "carbs": 60},
])
def test_bad_nutrition_is_refused(values):
    assert validated_nutrition(values) is None


def test_zero_calorie_food_is_valid():
    assert validated_nutrition({"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}) is not None


def test_write_report_escapes_food_names_from_api():
    report = WriteReport(["<food>"], [("A&B", "invalid <token>")], ["1"])
    rendered = render_report(report)
    assert "&lt;food&gt;" in rendered
    assert "A&amp;B" in rendered
    assert "invalid &lt;token&gt;" in rendered


def test_label_nutrition_uses_same_validator():
    raw = json.dumps({
        "kind": "label",
        "items": [{"query_en": "food", "name_ru": "еда", "amount": 100, "unit": "g",
                   "kcal_100g": float("nan"), "protein_100g": -1,
                   "fat_100g": 0, "carbs_100g": 0}],
    })
    assert parse_recognition(raw).items[0].nutrition is None


@pytest.mark.asyncio
async def test_bad_json_on_first_model_reaches_second_model():
    used = []

    def respond(request):
        body = json.loads(request.content)
        used.append(body)
        content = "bad json" if body["model"] == "first" else json.dumps({
            "kind": "text", "items": [
                {"query_en": "apple", "name_ru": "яблоко", "amount": 100, "unit": "g"}
            ],
        })
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    llm = OpenRouter("key", ["first", "second"], ["first"])
    await llm._client.aclose()
    llm._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        result = await llm.recognize_text("яблоко")
    finally:
        await llm.close()
    assert result.items[0].name_ru == "яблоко"
    assert [body["model"] for body in used] == ["first", "first", "second"]
    assert used[0]["provider"] == {"require_parameters": True}
    assert "provider" not in used[1]


def test_date_buttons_use_users_diary_day():
    draft = {"day": "2026-09-01"}
    now = datetime(2026, 9, 27, 22, 30, tzinfo=timezone.utc)
    shift_day(draft, "today", "Asia/Tbilisi", now)
    assert draft["day"] == "2026-09-27"  # 02:30 locally, before diary day starts
    shift_day(draft, "yesterday", "Asia/Tbilisi", now)
    assert draft["day"] == "2026-09-26"
    shift_day(draft, "unknown", "Asia/Tbilisi", now)
    assert draft["day"] == "2026-09-26"


@pytest.mark.asyncio
async def test_one_message_uses_meal_hint_from_any_item(monkeypatch):
    hints = []

    def resolve(_tz, **kwargs):
        hints.append(kwargs)
        return date(2026, 9, 28), pipeline.Meal.DINNER

    async def resolve_item(_fs, item, _recent):
        return {"name_ru": item.name_ru}

    monkeypatch.setattr(pipeline, "resolve", resolve)
    monkeypatch.setattr(pipeline, "_resolve_item", resolve_item)
    recognized = Recognition("text", [
        RecognizedItem("oats", "овсянка", 100, "g"),
        RecognizedItem("apple", "яблоко", 100, "g", "dinner", "yesterday"),
    ])
    draft = await build_draft(None, recognized, "Asia/Tbilisi")
    assert hints == [{"meal_hint": "dinner", "date_hint": "yesterday"}]
    assert draft["meal"] == "dinner"
    assert len(draft["items"]) == 2


@pytest.mark.asyncio
async def test_metric_request_without_metric_serving_requires_choice():
    class FS:
        async def get_food(self, _):
            return {"servings": {"serving": {
                "serving_id": "1", "serving_description": "1 cup", "calories": "100",
                "protein": "1", "fat": "1", "carbohydrate": "1",
            }}}

    item = {"name_ru": "еда", "amount": 200, "unit": "g",
            "candidates": [{"food_id": "42", "title": "Food"}]}
    await apply_candidate(FS(), item, 0)
    assert item["needs_portion"] is True
    assert item["serving_id"] is None
    assert item["available_servings"][0]["description"] == "1 cup"
    await apply_portion_choice(FS(), item, "1", 1.5)
    assert item["needs_portion"] is False
    assert item["units"] == 1.5
    assert item["kcal"] == 150


@pytest.mark.asyncio
async def test_web_product_checks_fatsecret_name_before_offering_creation():
    class FS:
        def __init__(self):
            self.queries = []

        async def search_foods(self, query, max_results):
            self.queries.append(query)
            return [FoodSummary("42", "Tuna Salad", "Trata", "")]

        async def get_food(self, _):
            return {"servings": {"serving": {
                "serving_id": "1", "serving_description": "100 g",
                "metric_serving_amount": "100", "metric_serving_unit": "g",
                "number_of_units": "100", "calories": "186",
                "protein": "12", "fat": "10", "carbohydrate": "12",
            }}}

    fs = FS()
    product = {
        "name": "Tuna Salad", "brand": "Trata", "source": "openfoodfacts.org",
        "kcal_100g": 186, "protein_100g": 12,
        "fat_100g": 10, "carbs_100g": 12,
    }
    result = await draft_from_web(fs, product, "Asia/Tbilisi", "4006381333931")
    assert fs.queries == ["Trata Tuna Salad"]
    assert result["items"][0]["food_id"] == "42"
