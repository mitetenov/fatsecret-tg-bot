"""Реплика → Кандидаты → черновик → Позиции.

Один Кандидат на пункт выбирается автоматически, остальные остаются под «Изменить»
(решение 9). Запись — best-effort с точным отчётом: у FatSecret нет идемпотентности,
поэтому повтор возможен только по упавшим пунктам (решение 17, ADR отсутствует
намеренно — правило описано в резюме и в тексте отчёта).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fsbot.domain import matching, servings as srv
from fsbot.domain.daybounds import Meal, resolve
from fsbot.fatsecret.client import FatSecretClient, FatSecretError, FoodSummary
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
    first = recognition.items[0]
    day, meal = resolve(tz, meal_hint=first.meal, date_hint=first.date_hint)

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


def draft_from_web(product: dict, tz: str, barcode: str) -> dict:
    """Товар опознан по штрих-коду в вебе, но его нет в базе FatSecret.

    Искать его там же по названию бессмысленно — если бы он там был, нашёлся бы по
    коду. Поэтому сразу предлагаем создать Свой продукт из найденных данных.
    """
    day, meal = resolve(tz)
    name = product.get("name") or "Продукт"
    basis_unit = "ml" if product.get("nutrition_basis") == "ml" else "g"
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
            "kcal": _product_nutrient(product, "kcal", basis_unit),
            "protein": _product_nutrient(product, "protein", basis_unit),
            "fat": _product_nutrient(product, "fat", basis_unit),
            "carbs": _product_nutrient(product, "carbs", basis_unit),
            "basis_unit": basis_unit,
        },
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
        item["error"] = exc.message
        return

    portions = srv.parse_servings(food)
    portion = srv.default_portion(portions, item["amount"], item["unit"])
    if portion is None:
        item["error"] = "у продукта нет ни одной порции"
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
    )
    if mismatch is not None:
        item["confidence"] = min(float(item.get("confidence", 0.5)), 0.35)
    elif item.get("label_kcal"):
        item["confidence"] = max(float(item.get("confidence", 0.5)), 0.85)


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
    fs: FatSecretClient, item: dict, token: str, token_secret: str
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
