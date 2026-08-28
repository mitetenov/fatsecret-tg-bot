"""Невалидные коды не должны уходить ни в один внешний fallback."""

import asyncio

from fsbot.bot.handlers import _lookup_product


class RecordingOFF:
    def __init__(self):
        self.codes = []

    async def lookup(self, code):
        self.codes.append(code)
        return None


class RecordingLLM:
    def __init__(self):
        self.codes = []

    async def lookup_barcode(self, code):
        self.codes.append(code)
        return None


def test_invalid_checksum_skips_all_external_barcode_lookups():
    off, llm = RecordingOFF(), RecordingLLM()

    product = asyncio.run(_lookup_product("4006381333932", off, llm))

    assert product is None
    assert off.codes == []
    assert llm.codes == []


def test_typed_upce_is_canonicalized_before_external_lookup():
    off, llm = RecordingOFF(), RecordingLLM()

    asyncio.run(_lookup_product("04252614", off, llm))

    assert off.codes == ["042100005264"]
    assert llm.codes == ["042100005264"]
