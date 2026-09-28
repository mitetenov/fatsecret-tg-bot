"""Реплика → Кандидаты → черновик → Позиции.

Один Кандидат на пункт выбирается автоматически, остальные остаются под «Изменить»
(решение 9). Запись — best-effort с точным отчётом: у FatSecret нет идемпотентности,
поэтому повтор возможен только по упавшим пунктам (решение 17, ADR отсутствует
намеренно — правило описано в резюме и в тексте отчёта).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape

from fsbot.domain import matching, servings as srv
from fsbot.domain.daybounds import Meal, resolve
from fsbot.domain.nutrition import validated_nutrition
from fsbot.fatsecret.client import (
    FatSecretClient,
    FatSecretError,
    FatSecretUnknownOutcome,
    FoodSummary,
)
from fsbot.llm.parsing import Recognition, RecognizedItem

log = logging.getLogger(__name__)

MAX_CANDIDATES = 5
REVIEW_THRESHOLD = 0.65


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
    barcode: str | None = None,
) -> dict:
    # Вся реплика — один приём пищи. Модель может поставить подсказку только на
    # второй продукт, поэтому берём первое явно указанное значение из реплики.
    meal_hint = next((item.meal for item in recognition.items if item.meal), None)
    date_hint = next((item.date_hint for item in recognition.items if item.date_hint), None)
    day, meal = resolve(tz, meal_hint=meal_hint, date_hint=date_hint)

    items = [await _resolve_item(fs, item, recent or []) for item in recognition.items]
    draft = {
        "day": day.isoformat(),
        "meal": meal.value,
        "items": items,
        "kind": recognition.kind,
        "barcode": barcode or recognition.barcode,
    }
    refresh_confidence(draft)
    return draft


async def draft_from_food(
    fs: FatSecretClient, food_id: str, tz: str, amount: float = 100, unit: str = "g"
) -> dict:
    """Черновик по известному food_id — путь штрих-кода: продукт уже определён точно,
    гадать нечего, остаётся уточнить количество."""
    day, meal = resolve(tz)
    food = await fs.get_food(food_id)
    title = " ".join(filter(None, (food.get("brand_name"), food.get("food_name"))))
    item = {
        "name_ru": title,
        "query": title,
        "amount": amount,
        "unit": unit,
        "status": "pending",
        "entry_id": None,
        "error": None,
        "candidates": [
            {"food_id": food_id, "title": title, "description": "", "food": food}
        ],
        "chosen": 0,
        "food_id": None,
        "confidence": 1.0,
    }
    await apply_candidate(fs, item, chosen=0)
    draft = {
        "day": day.isoformat(),
        "meal": meal.value,
        "items": [item],
        "kind": "barcode",
        "barcode": None,
    }
    refresh_confidence(draft)
    return draft


async def draft_from_web(
    fs: FatSecretClient, product: dict, tz: str, barcode: str
) -> dict:
    """Товар опознан по штрих-коду в вебе, но его нет в базе FatSecret.

    Barcode indexes may be incomplete. Search by name before offering a new
    private food, and compare the result against the label nutrients.
    """
    day, meal = resolve(tz)
    basis_unit = "ml" if product.get("nutrition_basis") in {"ml", "100ml"} else "g"
    checked = validated_nutrition(
        {
            key: _product_nutrient(product, key, basis_unit)
            for key in ("kcal", "protein", "fat", "carbs")
        }
    )
    name = product.get("name") or "Продукт"
    item = {
        "name_ru": name,
        "query": name,
        "amount": 100,
        "unit": basis_unit,
        "status": "pending",
        "entry_id": None,
        "error": None,
        "candidates": [],
        "chosen": 0,
        "food_id": None,
        "creatable": {
            "name": name,
            "brand": product.get("brand") or "fsbot",
            **checked,
            "basis_unit": basis_unit,
        } if checked else None,
        "source": product.get("source"),
        "confidence": float(product.get("confidence", 0.6)),
    }
    draft = {
        "day": day.isoformat(),
        "meal": meal.value,
        "items": [item],
        "kind": "web",
        "barcode": barcode,
    }
    if checked:
        terms = [" ".join(filter(None, (product.get("brand"), name))), name]
        found: list[FoodSummary] = []
        for term in dict.fromkeys(terms):
            try:
                found = await fs.search_foods(term, max_results=MAX_CANDIDATES)
            except FatSecretError:
                break
            if found:
                break
        if found:
            item["candidates"] = [
                {"food_id": food.food_id, "title": food.title,
                 "description": food.description, "food": food.details}
                for food in found
            ]
            item["label_kcal"] = checked["kcal"]
            item["label_macros"] = checked
            item["label_basis_unit"] = basis_unit
            await pick_best_candidate(fs, item)
    refresh_confidence(draft)
    return draft


def _product_nutrient(product: dict, name: str, basis_unit: str) -> object:
    generic = product.get(f"{name}_per_100")
    return generic if generic is not None else product.get(f"{name}_100{basis_unit}")


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
        "confidence": item.confidence,
    }

    try:
        found = await fs.search_foods(item.query_en, max_results=MAX_CANDIDATES)
        if not found:
            # Автокомплит знает, как продукт называется в базе: LLM переводит «творог»
            # то в «cottage cheese», то в «curd», и второе не находится.
            for suggestion in (await fs.autocomplete(item.query_en))[:2]:
                found = await fs.search_foods(suggestion, max_results=MAX_CANDIDATES)
                if found:
                    log.info("автокомплит помог: %r → %r", item.query_en, suggestion)
                    break
    except FatSecretError as exc:
        base["error"] = exc.message
        return base

    if not found:
        # Продукта в базе нет. Если с этикетки считаны КБЖУ — из них можно создать
        # Свой продукт, но только по явной кнопке: удалить его через API нельзя.
        if item.nutrition:
            base["creatable"] = {
                "name": item.name_ru,
                "brand": item.brand or "fsbot",
                "kcal": item.nutrition.kcal,
                "protein": item.nutrition.protein,
                "fat": item.nutrition.fat,
                "carbs": item.nutrition.carbs,
                "basis_unit": item.nutrition.basis_unit,
            }
        return base

    ranked = _rank(found, recent)
    base["candidates"] = [
        {
            "food_id": c.food_id,
            "title": c.title,
            "description": c.description,
            "food": c.details,
        }
        for c in ranked
    ]
    if item.nutrition:
        # С этикетки известна калорийность — выбираем Кандидата, который в неё
        # укладывается, а не первого попавшегося: на салат из тунца поиск однажды
        # вернул шоколад, и бот принял это молча.
        base["label_kcal"] = item.nutrition.kcal
        base["label_macros"] = {
            "kcal": item.nutrition.kcal,
            "protein": item.nutrition.protein,
            "fat": item.nutrition.fat,
            "carbs": item.nutrition.carbs,
        }
        base["label_basis_unit"] = item.nutrition.basis_unit
        base["creatable"] = {
            "name": item.name_ru,
            "brand": item.brand or "fsbot",
            "kcal": item.nutrition.kcal,
            "protein": item.nutrition.protein,
            "fat": item.nutrition.fat,
            "carbs": item.nutrition.carbs,
            "basis_unit": item.nutrition.basis_unit,
        }
    await pick_best_candidate(fs, base)
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
        food = candidate.get("food") or await fs.get_food(candidate["food_id"])
    except FatSecretError as exc:
        item["food_id"] = None
        item["error"] = exc.message
        return

    portions = srv.parse_servings(food)
    manual_id = item.get("manual_serving_id")
    portion = (
        srv.by_units(portions, manual_id, item["amount"])
        if manual_id
        else srv.default_portion(portions, item["amount"], item["unit"])
    )
    if portion is None:
        item.update(
            chosen=chosen,
            title=candidate["title"],
            food_id=candidate["food_id"] if portions else None,
            serving_id=None,
            units=None,
            needs_portion=bool(portions),
            available_servings=[
                {"serving_id": p.serving_id, "description": p.description}
                for p in portions[:15]
            ],
            error="выбери порцию и её количество" if portions else "у продукта нет ни одной порции",
        )
        return

    mismatch = None
    if item.get("label_kcal"):
        ok, gap = matching.matches_label(
            item.get("label_macros") or item["label_kcal"],
            portions,
            basis_unit=item.get("label_basis_unit", "g"),
        )
        if not ok:
            mismatch = gap

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
        mismatch=mismatch,
        needs_portion=False,
        available_servings=[],
    )
    if mismatch is not None:
        item["confidence"] = min(float(item.get("confidence", 0.5)), 0.35)
    elif item.get("label_kcal"):
        item["confidence"] = max(float(item.get("confidence", 0.5)), 0.85)


async def apply_portion_choice(
    fs: FatSecretClient, item: dict, serving_id: str, count: float
) -> None:
    if count <= 0:
        raise ValueError("Количество порций должно быть положительным")
    item["manual_serving_id"] = serving_id
    item["amount"] = count
    item["unit"] = "piece"
    await apply_candidate(fs, item, item.get("chosen", 0))


async def pick_best_candidate(fs: FatSecretClient, item: dict) -> None:
    """Взять первого Кандидата, чья калорийность сходится с этикеткой.

    Если не сошёлся ни один — оставляем первого, но с пометкой расхождения: молча
    записывать в Дневник продукт, который в два-три раза калорийнее съеденного,
    нельзя, а решать за человека, что именно он ел, — не наше дело.
    """
    if not item.get("label_kcal"):
        await apply_candidate(fs, item, chosen=0)
        return

    for position in range(len(item.get("candidates") or [])):
        await apply_candidate(fs, item, chosen=position)
        if item.get("food_id") and not item.get("mismatch"):
            return
    await apply_candidate(fs, item, chosen=0)


async def create_own_food(
    fs: FatSecretClient,
    item: dict,
    token: str,
    token_secret: str,
    on_created: Callable[[], Awaitable[None]] | None = None,
) -> str | None:
    """Создать Свой продукт из считанных с этикетки КБЖУ и подставить его в пункт."""
    spec = item.get("creatable")
    if not spec:
        return None

    food_id = await fs.create_food(
        token,
        token_secret,
        name=spec["name"],
        brand=spec["brand"],
        kcal=spec["kcal"],
        protein=spec["protein"],
        fat=spec["fat"],
        carbs=spec["carbs"],
        basis_unit=spec.get("basis_unit", "g"),
    )
    item["candidates"] = [{"food_id": food_id, "title": spec["name"], "description": "свой"}]
    item.pop("creatable", None)
    item["status"] = "pending"
    item["own_food_id"] = food_id
    if on_created:
        await on_created()
    await apply_candidate(fs, item, chosen=0)
    return food_id


async def set_amount(fs: FatSecretClient, item: dict, amount: float) -> None:
    item["amount"] = amount
    if item.get("creatable") and not item.get("candidates"):
        # Продукта ещё нет — пересчитывать нечего, показ считается из спецификации.
        return
    await apply_candidate(fs, item, item.get("chosen", 0))


def shift_day(
    draft: dict,
    hint: str,
    tz: str,
    now_utc: datetime | None = None,
) -> None:
    if hint not in {"today", "yesterday"}:
        return
    today, _ = resolve(tz, now_utc)
    target = today if hint == "today" else today - timedelta(days=1)
    draft["day"] = target.isoformat()


def refresh_confidence(draft: dict) -> None:
    """Сводная уверенность черновика и необходимость дополнительной проверки."""
    scores = [
        max(0.0, min(1.0, float(item.get("confidence", 0.5))))
        for item in draft.get("items", [])
    ]
    if not scores:
        draft.pop("confidence", None)
        draft.pop("needs_review", None)
        return
    score = round(min(scores), 2)
    draft["confidence"] = score
    draft["needs_review"] = score < REVIEW_THRESHOLD or any(
        bool(item.get("mismatch")) for item in draft.get("items", [])
    )


async def write_draft(
    fs: FatSecretClient,
    draft: dict,
    token: str,
    token_secret: str,
    persist: Callable[[], Awaitable[None]] | None = None,
    record_success: Callable[[str], Awaitable[None]] | None = None,
) -> WriteReport:
    report = WriteReport(written=[], failed=[], entry_ids=[])
    day = date.fromisoformat(draft["day"])
    meal = Meal(draft["meal"])

    for item in draft["items"]:
        if item.get("status") in {"written", "undone"}:
            continue  # Уже записанное или отменённое не повторяем.
        if item.get("status") in {"writing", "unknown", "creating", "create_unknown"}:
            report.failed.append(
                (item["name_ru"], "исход предыдущего запроса неизвестен; проверь дневник FatSecret")
            )
            continue
        if item.get("needs_portion"):
            report.failed.append((item["name_ru"], "сначала выбери порцию"))
            continue
        if not item.get("food_id"):
            report.failed.append((item["name_ru"], "не найден в базе"))
            continue
        if not item.get("serving_id") or not item.get("units"):
            report.failed.append((item["name_ru"], "не удалось определить порцию"))
            continue

        item["status"] = "writing"
        if persist:
            await persist()
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
            if persist:
                await persist()
            if exc.token_invalid:
                report.token_invalid = True
                break
            continue
        except FatSecretUnknownOutcome as exc:
            item["status"] = "unknown"
            item["error"] = str(exc)
            report.failed.append(
                (item.get("title") or item["name_ru"], "неизвестно, записал ли FatSecret позицию")
            )
            if persist:
                await persist()
            break

        item["status"] = "written"
        item["entry_id"] = entry_id
        item["error"] = None
        if record_success:
            await record_success(entry_id)
        elif persist:
            await persist()
        report.written.append(item.get("title") or item["name_ru"])
        report.entry_ids.append(entry_id)

    return report


def render_report(report: WriteReport) -> str:
    lines = [f"✅ {escape(str(name))}" for name in report.written]
    lines += [f"❌ {escape(str(name))} — {escape(str(reason))}" for name, reason in report.failed]
    if report.failed and report.written:
        lines.append("\nЗаписалось не всё. Повтор коснётся только безопасных для повтора пунктов.")
    if report.token_invalid:
        lines.append("\nДоступ к твоему аккаунту FatSecret отозван — набери /link заново.")
    return "\n".join(lines) or "Нечего записывать."
