"""Реплика → Кандидаты → черновик → Позиции.

Один Кандидат на пункт выбирается автоматически, остальные остаются под «Изменить»
(решение 9). Запись — best-effort с точным отчётом: у FatSecret нет идемпотентности,
поэтому повтор возможен только по упавшим пунктам (решение 17, ADR отсутствует
намеренно — правило описано в резюме и в тексте отчёта).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from fsbot.domain import servings as srv
from fsbot.domain.daybounds import Meal, resolve
from fsbot.fatsecret.client import FatSecretClient, FatSecretError, FoodSummary
from fsbot.llm.parsing import Recognition, RecognizedItem

log = logging.getLogger(__name__)

MAX_CANDIDATES = 5


@dataclass(slots=True)
class WriteReport:
    written: list[str]
    failed: list[tuple[str, str]]
    entry_ids: list[str]
    token_invalid: bool = False


async def build_draft(
    fs: FatSecretClient,
    recognition: Recognition,
    tz: str,
    recent: list[FoodSummary] | None = None,
) -> dict:
    first = recognition.items[0]
    day, meal = resolve(tz, meal_hint=first.meal, date_hint=first.date_hint)

    items = [await _resolve_item(fs, item, recent or []) for item in recognition.items]
    return {"day": day.isoformat(), "meal": meal.value, "items": items, "kind": recognition.kind}


async def _resolve_item(
    fs: FatSecretClient, item: RecognizedItem, recent: list[FoodSummary]
) -> dict:
    base = {
        "name_ru": item.name_ru,
        "query": item.query_en,
        "amount": item.amount,
        "unit": item.unit,
        "status": "pending",
        "entry_id": None,
        "error": None,
        "candidates": [],
        "chosen": 0,
        "food_id": None,
    }

    try:
        found = await fs.search_foods(item.query_en, max_results=MAX_CANDIDATES)
    except FatSecretError as exc:
        base["error"] = exc.message
        return base

    if not found:
        return base

    ranked = _rank(found, recent)
    base["candidates"] = [
        {"food_id": c.food_id, "title": c.title, "description": c.description} for c in ranked
    ]
    await apply_candidate(fs, base, chosen=0)
    return base


def _rank(found: list[FoodSummary], recent: list[FoodSummary]) -> list[FoodSummary]:
    """Недавно съеденное поднимается наверх: если человек ест это регулярно,
    в следующий раз именно оно и должно победить (решение 9)."""
    recent_ids = {food.food_id for food in recent}
    return sorted(found, key=lambda food: food.food_id not in recent_ids)


async def apply_candidate(fs: FatSecretClient, item: dict, chosen: int) -> None:
    """Пересчитать Порцию и нутриенты под выбранного Кандидата и текущее количество."""
    candidates = item.get("candidates") or []
    if not candidates:
        return
    chosen = max(0, min(chosen, len(candidates) - 1))
    candidate = candidates[chosen]

    try:
        food = await fs.get_food(candidate["food_id"])
    except FatSecretError as exc:
        item["error"] = exc.message
        return

    portions = srv.parse_servings(food)
    portion = srv.default_portion(portions, item["amount"], item["unit"])
    if portion is None:
        item["error"] = "у продукта нет ни одной порции"
        return

    item.update(
        chosen=chosen,
        food_id=candidate["food_id"],
        title=candidate["title"],
        serving_id=portion.serving.serving_id,
        # В API уходят единицы measurement_description, а не множитель Порции.
        units=portion.api_units,
        portion=portion.describe(),
        kcal=portion.calories,
        protein=portion.nutrient("protein"),
        fat=portion.nutrient("fat"),
        carbohydrate=portion.nutrient("carbohydrate"),
        error=None,
    )


async def set_amount(fs: FatSecretClient, item: dict, amount: float) -> None:
    item["amount"] = amount
    await apply_candidate(fs, item, item.get("chosen", 0))


def shift_day(draft: dict, hint: str) -> None:
    day = date.fromisoformat(draft["day"])
    today = date.today()
    draft["day"] = (today if hint == "today" else today - timedelta(days=1)).isoformat()
    if hint not in {"today", "yesterday"}:
        draft["day"] = day.isoformat()


async def write_draft(
    fs: FatSecretClient, draft: dict, token: str, token_secret: str
) -> WriteReport:
    report = WriteReport(written=[], failed=[], entry_ids=[])
    day = date.fromisoformat(draft["day"])
    meal = Meal(draft["meal"])

    for item in draft["items"]:
        if item.get("status") == "written":
            continue  # повторяем только упавшее — иначе получим дубли
        if not item.get("food_id"):
            report.failed.append((item["name_ru"], "не найден в базе"))
            continue

        try:
            entry_id = await fs.create_entry(
                token,
                token_secret,
                food_id=item["food_id"],
                serving_id=item["serving_id"],
                units=item["units"],
                entry_name=item.get("title") or item["name_ru"],
                meal=meal,
                day=day,
            )
        except FatSecretError as exc:
            item["status"] = "failed"
            item["error"] = exc.message
            report.failed.append((item.get("title") or item["name_ru"], exc.message))
            if exc.token_invalid:
                report.token_invalid = True
                break
            continue

        item["status"] = "written"
        item["entry_id"] = entry_id
        report.written.append(item.get("title") or item["name_ru"])
        report.entry_ids.append(entry_id)

    return report


def render_report(report: WriteReport) -> str:
    lines = [f"✅ {name}" for name in report.written]
    lines += [f"❌ {name} — {reason}" for name, reason in report.failed]
    if report.failed and report.written:
        lines.append("\nЗаписалось не всё. Повтор коснётся только неудавшихся пунктов.")
    if report.token_invalid:
        lines.append("\nДоступ к твоему аккаунту FatSecret отозван — набери /link заново.")
    return "\n".join(lines) or "Нечего записывать."
