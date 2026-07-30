"""Схема ответа LLM и её разбор.

Бесплатные модели OpenRouter не гарантируют соблюдение json_schema, поэтому разбор
обязан быть устойчивым: вытащить JSON из текста, проверить поля, отбросить мусор.
Это чистый код без сети — он и тестируется (решение 19).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

UNITS = {"g", "ml", "piece"}
MEALS = {"breakfast", "lunch", "dinner", "other"}

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

RECOGNITION_SCHEMA = {
    "name": "recognized_meal",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["text", "plate", "label"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "query_en": {"type": "string"},
                        "name_ru": {"type": "string"},
                        "amount": {"type": "number"},
                        "unit": {"type": "string", "enum": sorted(UNITS)},
                        "meal": {"type": "string"},
                        "date_hint": {"type": "string"},
                    },
                    "required": ["query_en", "name_ru", "amount", "unit"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["kind", "items"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True, slots=True)
class RecognizedItem:
    query_en: str
    name_ru: str
    amount: float
    unit: str
    meal: str | None = None
    date_hint: str | None = None


@dataclass(frozen=True, slots=True)
class Recognition:
    kind: str
    items: list[RecognizedItem] = field(default_factory=list)


class ParseError(Exception):
    pass


def extract_json(raw: str) -> dict:
    """Модель может обернуть JSON в текст или в ```json — достаём объект."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") :] if "{" in raw else raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(raw)
    if not match:
        raise ParseError("в ответе модели нет JSON")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ParseError(f"JSON в ответе модели битый: {exc}") from exc


def parse_recognition(raw: str) -> Recognition:
    payload = extract_json(raw)

    kind = str(payload.get("kind") or "text")
    if kind not in {"text", "plate", "label"}:
        kind = "text"

    items: list[RecognizedItem] = []
    for entry in payload.get("items") or []:
        if not isinstance(entry, dict):
            continue
        item = _parse_item(entry)
        if item:
            items.append(item)

    if not items:
        raise ParseError("модель не назвала ни одного продукта")
    return Recognition(kind=kind, items=items)


def _parse_item(entry: dict) -> RecognizedItem | None:
    query = str(entry.get("query_en") or entry.get("name_ru") or "").strip()
    if not query:
        return None

    try:
        amount = float(entry.get("amount"))
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None

    unit = str(entry.get("unit") or "g").lower()
    if unit in {"gram", "grams", "г", "гр"}:
        unit = "g"
    elif unit in {"мл", "milliliter", "millilitre"}:
        unit = "ml"
    elif unit in {"pcs", "pc", "шт", "штука"}:
        unit = "piece"
    if unit not in UNITS:
        unit = "g"

    meal = str(entry.get("meal") or "").lower() or None
    if meal not in MEALS:
        meal = None

    hint = str(entry.get("date_hint") or "").lower() or None
    if hint not in {"today", "yesterday"}:
        hint = None

    return RecognizedItem(
        query_en=query,
        name_ru=str(entry.get("name_ru") or query).strip(),
        amount=round(amount, 2),
        unit=unit,
        meal=meal,
        date_hint=hint,
    )
