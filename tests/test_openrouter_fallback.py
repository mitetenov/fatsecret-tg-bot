"""Fallback OpenRouter считается успешным только после разбора Recognition."""

import asyncio
import json

import httpx
import pytest

from fsbot.llm.openrouter import LLMError, OpenRouter, _normalize_lookup_product


VALID = {
    "choices": [
        {
            "message": {
                "content": (
                    '{"kind":"text","items":[{"query_en":"oats",'
                    '"name_ru":"овсянка","amount":60,"unit":"g"}]}'
                )
            }
        }
    ]
}


async def recognize_with(handler):
    client = OpenRouter("key", ["bad", "good"], ["bad", "good"])
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await client.recognize_text("овсянка 60 г")
    finally:
        await client.close()


def test_invalid_primary_response_falls_back_to_second_model():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        payload = (
            VALID
            if body["model"] == "good"
            else {"choices": [{"message": {"content": "not json"}}]}
        )
        return httpx.Response(200, json=payload)

    result = asyncio.run(recognize_with(handler))

    assert result.items[0].query_en == "oats"
    assert [body["model"] for body in seen] == ["bad", "bad", "good"]


def test_repair_can_save_current_model_without_using_fallback():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        payload = (
            VALID
            if len(seen) == 2
            else {"choices": [{"message": {"content": "not json"}}]}
        )
        return httpx.Response(200, json=payload)

    result = asyncio.run(recognize_with(handler))

    assert result.items[0].query_en == "oats"
    assert [body["model"] for body in seen] == ["bad", "bad"]
    assert seen[0]["provider"] == {"require_parameters": True}
    assert "provider" not in seen[1]


@pytest.mark.parametrize(
    "broken",
    [
        "not an OpenRouter json envelope",
        {"choices": []},
        {"choices": [{"message": {"content": None}}]},
    ],
)
def test_malformed_primary_envelope_falls_back_instead_of_leaking(broken):
    def handler(request):
        body = json.loads(request.content)
        if body["model"] == "good":
            return httpx.Response(200, json=VALID)
        if isinstance(broken, str):
            return httpx.Response(200, text=broken)
        return httpx.Response(200, json=broken)

    result = asyncio.run(recognize_with(handler))

    assert result.items[0].query_en == "oats"


def test_barcode_web_lookup_retries_not_found_and_normalizes_liquid():
    client = object.__new__(OpenRouter)
    client._text_models = ["model"]
    replies = [
        '{"found":false}',
        '{"found":false}',
        json.dumps(
            {
                "found": True,
                "name": "Milk",
                "brand": "Sante",
                "source": "example.org",
                "nutrition_basis": "100ml",
                "kcal_per_100": 60,
                "protein_per_100": 3,
                "fat_per_100": 3.2,
                "carbs_per_100": 4.7,
                "confidence": 0.99,
            }
        ),
    ]
    calls = []

    async def complete(models, messages, schema):
        calls.append((models, schema))
        return replies.pop(0)

    client._complete = complete

    product = asyncio.run(client.lookup_barcode("0036000291452"))

    assert len(calls) == 3
    assert calls[0] == (["model:online"], False)
    assert product["nutrition_basis"] == "ml"
    assert product["kcal_per_100"] == 60
    assert product["confidence"] == 0.75


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Milk", "source": ""},
        {
            "name": "Milk",
            "source": "example.org",
            "kcal_per_100": 50,
            "protein_per_100": 25,
            "fat_per_100": 25,
            "carbs_per_100": 25,
        },
        {
            "name": "Milk",
            "source": "example.org",
            "kcal_per_100": "unknown",
            "protein_per_100": 3,
            "fat_per_100": 3,
            "carbs_per_100": 5,
        },
    ],
)
def test_barcode_web_product_without_source_or_plausible_nutrition_is_rejected(payload):
    assert _normalize_lookup_product(payload) is None


def test_barcode_lookup_requires_a_model_with_online_grounding():
    client = object.__new__(OpenRouter)
    client._text_models = ["already:online"]

    assert asyncio.run(client.lookup_barcode("0036000291452")) is None


def test_all_429_responses_are_preserved_as_rate_limit_failure():
    def handler(request):
        return httpx.Response(429, text="provider busy")

    with pytest.raises(LLMError) as raised:
        asyncio.run(recognize_with(handler))

    assert raised.value.rate_limited
    assert raised.value.statuses == [429, 429, 429, 429]
