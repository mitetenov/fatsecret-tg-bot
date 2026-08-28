"""Fallback OpenRouter считается успешным только после разбора Recognition."""

import asyncio
import json

import httpx
import pytest

from fsbot.llm.openrouter import OpenRouter


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
