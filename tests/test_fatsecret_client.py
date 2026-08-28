"""Контракты клиента FatSecret, не требующие живого API."""

import asyncio
from datetime import date

import httpx
import pytest

from fsbot.domain.daybounds import Meal
from fsbot.fatsecret.client import FatSecretClient, FatSecretError


class RecordingClient(FatSecretClient):
    """Оставляет реальную boundary-логику, заменяя только внешний HTTP-вызов."""

    def __init__(self):
        self.calls = []

    async def _call(self, api_method, token=None, token_secret="", **params):
        self.calls.append((api_method, params))
        return {"food": {"food_id": "4384"}}


def test_barcode_lookup_sends_only_normalized_gtin13():
    client = RecordingClient()

    food_id = asyncio.run(client.food_id_by_barcode("036000291452"))

    assert food_id == "4384"
    assert client.calls == [
        ("food.find_id_for_barcode.v2", {"barcode": "0036000291452", "flag_default_serving": True})
    ]


def test_invalid_or_gtin14_barcode_skips_http():
    client = RecordingClient()

    invalid = asyncio.run(client.food_id_by_barcode("4006381333932"))
    gtin14 = asyncio.run(client.food_id_by_barcode("10012345678902"))

    assert invalid is None
    assert gtin14 is None
    assert client.calls == []


class PayloadClient(FatSecretClient):
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def _call(self, api_method, token=None, token_secret="", **params):
        self.calls.append((api_method, params))
        return self.payload


def test_search_uses_v5_and_parses_new_envelope_with_embedded_food():
    food = {
        "food_id": "9",
        "food_name": "Milk",
        "brand_name": "Sante",
        "servings": {"serving": []},
    }
    client = PayloadClient({"foods_search": {"results": {"food": [food]}}})

    found = asyncio.run(client.search_foods("milk", max_results=3))

    assert found[0].title == "Sante Milk"
    assert found[0].details == food
    assert client.calls == [
        ("foods.search.v5", {"search_expression": "milk", "max_results": 3,
                             "flag_default_serving": True})
    ]


def test_get_and_autocomplete_use_latest_versions():
    food_client = PayloadClient({"food": {"food_id": "9"}})
    suggestions_client = PayloadClient({"suggestions": {"suggestion": ["milk", "milk tea"]}})

    assert asyncio.run(food_client.get_food("9")) == {"food_id": "9"}
    assert asyncio.run(suggestions_client.autocomplete("mil")) == ["milk", "milk tea"]
    assert food_client.calls == [("food.get.v5", {"food_id": "9", "flag_default_serving": True})]
    assert suggestions_client.calls == [
        ("foods.autocomplete.v2", {"expression": "mil", "max_results": 4})
    ]


def test_create_liquid_food_uses_documented_serving_parameters():
    client = PayloadClient({"food_id": {"value": "77"}})

    food_id = asyncio.run(
        client.create_food(
            "token", "secret", name="Milk", brand="Sante", kcal=60,
            protein=3, fat=3.2, carbs=4.7, basis_unit="ml"
        )
    )

    assert food_id == "77"
    method, params = client.calls[0]
    assert method == "food.create.v2"
    assert params["serving_size"] == "100 ml"
    assert params["serving_amount"] == 100
    assert params["serving_amount_unit"] == "ml"
    assert "metric_serving_unit" not in params


def test_recently_eaten_uses_v2():
    client = PayloadClient({"foods": {"food": []}})

    assert asyncio.run(client.recently_eaten("token", "secret")) == []
    assert client.calls == [("foods.get_recently_eaten.v2", {})]


class BarcodeV2Client(FatSecretClient):
    def __init__(self):
        self.calls = []
        self._food_cache = {}

    async def _call(self, api_method, token=None, token_secret="", **params):
        self.calls.append(api_method)
        if api_method == "food.find_id_for_barcode.v2":
            return {
                "food": {
                    "food_id": "4384",
                    "servings": {"serving": {"serving_id": "10"}},
                }
            }
        raise AssertionError("food.get.v5 не должен понадобиться после barcode v2")


def test_barcode_v2_food_is_reused_by_following_get():
    client = BarcodeV2Client()

    food_id = asyncio.run(client.food_id_by_barcode("036000291452"))
    food = asyncio.run(client.get_food(food_id))

    assert food["food_id"] == "4384"
    assert client.calls == ["food.find_id_for_barcode.v2"]


class ErrorClient(FatSecretClient):
    def __init__(self, code):
        self.code = code

    async def _call(self, api_method, token=None, token_secret="", **params):
        raise FatSecretError(self.code, "not found")


def test_barcode_v2_code_211_means_absent_product_not_failed_lookup():
    assert asyncio.run(ErrorClient(211).food_id_by_barcode("036000291452")) is None


def test_barcode_v2_propagates_real_api_errors():
    with pytest.raises(FatSecretError) as raised:
        asyncio.run(ErrorClient(9).food_id_by_barcode("036000291452"))
    assert raised.value.token_invalid


def test_create_entry_sends_exact_legacy_diary_contract():
    client = PayloadClient({"food_entry_id": {"value": "entry-1"}})

    entry_id = asyncio.run(
        client.create_entry(
            "token",
            "secret",
            food_id="9",
            serving_id="91",
            units=2.34567,
            entry_name="Very long soup name",
            meal=Meal.LUNCH,
            day=date(2026, 8, 28),
        )
    )

    method, params = client.calls[0]
    assert entry_id == "entry-1"
    assert method == "food_entry.create"
    assert params["food_id"] == "9"
    assert params["serving_id"] == "91"
    assert params["number_of_units"] == "2.3457"
    assert params["meal"] == "lunch"
    assert isinstance(params["date"], int)


async def call_with_transport(handler):
    client = FatSecretClient("consumer", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await client._call("foods.search.v5", search_expression="milk")
    finally:
        await client.close()


def test_low_level_call_maps_fatsecret_json_error_to_domain_error():
    def handler(request):
        assert request.url.params["method"] == "foods.search.v5"
        assert request.url.params["search_expression"] == "milk"
        assert request.url.params["oauth_signature"]
        return httpx.Response(200, json={"error": {"code": 21, "message": "denied"}})

    with pytest.raises(FatSecretError) as raised:
        asyncio.run(call_with_transport(handler))

    assert raised.value.code == 21
    assert raised.value.not_available_on_tier
