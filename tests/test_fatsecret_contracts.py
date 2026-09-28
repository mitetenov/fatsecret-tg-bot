"""Contract examples from the FatSecret API documentation, without network calls."""

from datetime import date

import httpx
import pytest

from fsbot.domain.daybounds import Meal
from fsbot.fatsecret.client import FatSecretClient, FatSecretUnknownOutcome


@pytest.mark.asyncio
async def test_create_entry_reads_documented_nested_id():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"food_entries": {"food_entry": [{"food_entry_id": "1111111"}]}},
        )

    fs = FatSecretClient("key", "secret")
    await fs._client.aclose()
    fs._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        entry_id = await fs.create_entry(
            "token", "secret", food_id="42", serving_id="99", units=1.5,
            entry_name="oats", meal=Meal.BREAKFAST, day=date(2026, 9, 28),
        )
    finally:
        await fs.close()
    assert entry_id == "1111111"
    assert seen[0].url.params["number_of_units"] == "1.5000"


@pytest.mark.asyncio
async def test_missing_create_id_is_uncertain_not_success():
    fs = FatSecretClient("key", "secret")
    await fs._client.aclose()
    fs._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"food_entries": {}}))
    )
    try:
        with pytest.raises(FatSecretUnknownOutcome):
            await fs.create_entry(
                "token", "secret", food_id="42", serving_id="99", units=1,
                entry_name="oats", meal=Meal.BREAKFAST, day=date(2026, 9, 28),
            )
    finally:
        await fs.close()


@pytest.mark.asyncio
async def test_create_food_uses_documented_serving_parameters():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"food_id": {"value": "77"}})

    fs = FatSecretClient("key", "secret")
    await fs._client.aclose()
    fs._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        assert await fs.create_food(
            "token", "secret", name="oats", brand="fsbot", kcal=100,
            protein=2, fat=3, carbs=16,
        ) == "77"
    finally:
        await fs.close()
    params = seen[0].url.params
    assert params["serving_amount"] == "100"
    assert params["serving_amount_unit"] == "g"
    assert "metric_serving_amount" not in params


@pytest.mark.asyncio
async def test_access_token_uses_get_and_delete_requires_success():
    seen = []

    def respond(request):
        seen.append(request)
        if "access_token" in str(request.url):
            return httpx.Response(200, text="oauth_token=access&oauth_token_secret=secret")
        return httpx.Response(200, json={"success": {"value": "0"}})

    fs = FatSecretClient("key", "secret")
    await fs._client.aclose()
    fs._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        assert await fs.access_token("request", "secret", "1234") == ("access", "secret")
        with pytest.raises(FatSecretUnknownOutcome):
            await fs.delete_entry("token", "secret", "42")
    finally:
        await fs.close()
    assert seen[0].method == "GET"
    assert seen[0].url.params["oauth_verifier"] == "1234"
