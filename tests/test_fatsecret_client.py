"""Контракты клиента FatSecret, не требующие живого API."""

import asyncio

from fsbot.fatsecret.client import FatSecretClient


class RecordingClient(FatSecretClient):
    """Оставляет реальную boundary-логику, заменяя только внешний HTTP-вызов."""

    def __init__(self):
        self.calls = []

    async def _call(self, api_method, token=None, token_secret="", **params):
        self.calls.append((api_method, params))
        return {"food_id": {"value": "4384"}}


def test_barcode_lookup_sends_only_normalized_gtin13():
    client = RecordingClient()

    food_id = asyncio.run(client.food_id_by_barcode("036000291452"))

    assert food_id == "4384"
    assert client.calls == [
        ("food.find_id_for_barcode", {"barcode": "0036000291452"})
    ]


def test_invalid_or_gtin14_barcode_skips_http():
    client = RecordingClient()

    invalid = asyncio.run(client.food_id_by_barcode("4006381333932"))
    gtin14 = asyncio.run(client.food_id_by_barcode("10012345678902"))

    assert invalid is None
    assert gtin14 is None
    assert client.calls == []
