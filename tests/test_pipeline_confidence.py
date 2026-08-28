import asyncio

from fsbot.bot.pipeline import build_draft, draft_from_web
from fsbot.fatsecret.client import FoodSummary
from fsbot.llm.parsing import Nutrition, Recognition, RecognizedItem


FOOD = {
    "food_id": "9",
    "food_name": "Soup",
    "servings": {
        "serving": {
            "serving_id": "91",
            "serving_description": "100 g",
            "metric_serving_amount": "100",
            "metric_serving_unit": "g",
            "number_of_units": "100",
            "calories": "80",
            "protein": "4",
            "fat": "2",
            "carbohydrate": "10",
        }
    },
}


class SearchOnlyFatSecret:
    def __init__(self):
        self.get_calls = 0

    async def search_foods(self, query, max_results=5):
        return [FoodSummary("9", "Soup", None, "", details=FOOD)]

    async def autocomplete(self, expression):
        return []

    async def get_food(self, food_id):
        self.get_calls += 1
        raise AssertionError("food из foods.search.v5 должен использоваться без N+1")


def test_low_confidence_plate_requires_review_and_reuses_v5_food():
    fs = SearchOnlyFatSecret()
    recognition = Recognition(
        kind="plate",
        items=[RecognizedItem("soup", "суп", 300, "g", confidence=0.55)],
    )

    draft = asyncio.run(build_draft(fs, recognition, "UTC"))

    assert draft["confidence"] == 0.55
    assert draft["needs_review"] is True
    assert fs.get_calls == 0


def test_matching_label_evidence_raises_confidence():
    fs = SearchOnlyFatSecret()
    recognition = Recognition(
        kind="label",
        items=[
            RecognizedItem(
                "soup",
                "суп",
                100,
                "g",
                nutrition=Nutrition(80, 4, 2, 10, "g"),
                confidence=0.5,
            )
        ],
    )

    draft = asyncio.run(build_draft(fs, recognition, "UTC"))

    assert draft["confidence"] == 0.85
    assert draft["needs_review"] is False


def test_web_liquid_draft_preserves_ml_basis_and_confidence():
    draft = draft_from_web(
        {
            "name": "Milk",
            "brand": "Sante",
            "source": "openfoodfacts.org",
            "nutrition_basis": "ml",
            "confidence": 0.9,
            "kcal_100ml": 60,
            "protein_100ml": 3,
            "fat_100ml": 3.2,
            "carbs_100ml": 4.7,
        },
        "UTC",
        "0036000291452",
    )

    item = draft["items"][0]
    assert item["unit"] == "ml"
    assert item["creatable"]["basis_unit"] == "ml"
    assert draft["confidence"] == 0.9
    assert draft["needs_review"] is False
