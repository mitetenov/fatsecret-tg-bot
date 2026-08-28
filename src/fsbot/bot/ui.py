"""Текст сообщения подтверждения и клавиатуры.

Принцип из решения 9: типичный случай — один тап «Записать», всё остальное спрятано
за «Изменить». Приём пищи и дата всегда видны в тексте, чтобы автоопределение не могло
ошибиться молча.
"""

from __future__ import annotations

from html import escape

from fsbot.domain.daybounds import MEAL_RU, Meal

WRITE = "w"
EDIT = "e"
CANCEL = "x"
PICK_ITEM = "i"
PICK_CANDIDATE = "c"
ASK_GRAMS = "g"
PICK_MEAL = "m"
PICK_DATE = "d"
BACK = "b"
CREATE_FOOD = "n"
REVIEW = "r"


def cb(draft_id: int, action: str, arg: str | int = "") -> str:
    return f"{draft_id}:{action}:{arg}"


def parse_cb(data: str) -> tuple[int, str, str]:
    draft_id, action, arg = data.split(":", 2)
    return int(draft_id), action, arg


def scale_creatable(item: dict) -> dict[str, float]:
    """КБЖУ будущего Своего продукта под указанное количество.

    В спецификации всё хранится на 100 г — это формат, который ждёт FatSecret при
    создании. Человеку же нужно видеть то, что попадёт в Дневник.
    """
    spec = item["creatable"]
    scale = float(item.get("amount") or 100) / 100
    return {key: round(float(spec[key] or 0) * scale, 1) for key in ("kcal", "protein", "fat", "carbs")}


def render_draft(draft: dict) -> str:
    # Названия продуктов приходят из чужой базы: «Ben & Jerry's», «<brand>» и прочее
    # ломают разметку HTML, а Telegram на такое отвечает ошибкой, а не показом текста.
    lines: list[str] = []
    total = 0.0
    for index, item in enumerate(draft["items"], start=1):
        if not item.get("food_id"):
            name = escape(str(item["name_ru"]))
            spec = item.get("creatable")
            if spec:
                # Показываем не «на 100 г», а то, что реально уйдёт в Дневник, — и
                # добавляем в сумму: иначе в строке 186 ккал, а в итоге ноль.
                scaled = scale_creatable(item)
                total += scaled["kcal"]
                origin = (
                    f"источник: {escape(str(item['source']))}"
                    if item.get("source")
                    else "с этикетки"
                )
                basis = "100 мл" if spec.get("basis_unit") == "ml" else "100 г"
                lines.append(
                    f"{index}. <b>{name}</b> — {item['amount']:g} {item['unit']}"
                    f" · в базе FatSecret нет, {origin}\n"
                    f"    {scaled['kcal']:g} ккал · Б {scaled['protein']:g} · "
                    f"Ж {scaled['fat']:g} · У {scaled['carbs']:g}\n"
                    f"    данные с этикетки на {basis}\n"
                    f"    ➕ создай продукт кнопкой ниже, иначе пункт не запишется"
                )
            else:
                lines.append(f"{index}. <b>{name}</b> — не нашёл в базе FatSecret")
            continue
        total += item["kcal"]
        line = (
            f"{index}. <b>{escape(str(item['title']))}</b> — {escape(str(item['portion']))}\n"
            f"    {item['kcal']:g} ккал · Б {item['protein']:g} · "
            f"Ж {item['fat']:g} · У {item['carbohydrate']:g}"
        )
        if item.get("mismatch"):
            # Молчать тут нельзя: расхождение с этикеткой в разы означает, что нашёлся
            # другой продукт, и записывать его — значит испортить Дневник.
            line += (
                f"\n    ⚠️ на этикетке {item['label_kcal']:g} ккал/100 "
                f"{'мл' if item.get('label_basis_unit') == 'ml' else 'г'} — "
                f"расхождение {item['mismatch'] * 100:.0f}%, похоже, это другой продукт"
            )
        lines.append(line)

    meal = MEAL_RU[Meal(draft["meal"])]
    lines.append(f"\n<b>Итого: {total:g} ккал</b> · {meal} · {draft['day']}")
    if "confidence" in draft:
        percent = round(float(draft["confidence"]) * 100)
        lines.append(f"Уверенность распознавания: {percent}%")
    if draft.get("needs_review"):
        lines.append("⚠️ Низкая уверенность: проверь продукт и количество перед записью.")
    return "\n".join(lines)


def draft_keyboard(draft_id: int, draft: dict | None = None) -> dict:
    write = (
        {"text": "⚠️ Проверить", "callback_data": cb(draft_id, REVIEW)}
        if (draft or {}).get("needs_review")
        else {"text": "✅ Записать", "callback_data": cb(draft_id, WRITE)}
    )
    rows = [
        [
            write,
            {"text": "✏️ Изменить", "callback_data": cb(draft_id, EDIT)},
            {"text": "❌ Отмена", "callback_data": cb(draft_id, CANCEL)},
        ]
    ]
    # Создание Свого продукта необратимо (в API нет удаления), поэтому только явной
    # кнопкой и только там, где с этикетки есть полные КБЖУ.
    for index, item in enumerate((draft or {}).get("items", [])):
        if item.get("creatable"):
            rows.append(
                [
                    {
                        "text": f"➕ Создать «{item['name_ru'][:24]}»",
                        "callback_data": cb(draft_id, CREATE_FOOD, index),
                    }
                ]
            )
    return {"inline_keyboard": rows}


def review_keyboard(draft_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "⚠️ Записать всё равно",
                    "callback_data": cb(draft_id, WRITE),
                }
            ],
            [
                {"text": "✏️ Изменить", "callback_data": cb(draft_id, EDIT)},
                {"text": "❌ Отмена", "callback_data": cb(draft_id, CANCEL)},
            ],
        ]
    }


def edit_keyboard(draft_id: int, draft: dict) -> dict:
    rows = [
        [
            {
                "text": f"{index}. {item['name_ru'][:28]}",
                "callback_data": cb(draft_id, PICK_ITEM, index - 1),
            }
        ]
        for index, item in enumerate(draft["items"], start=1)
    ]
    rows.append(
        [
            {"text": "🍽 Приём пищи", "callback_data": cb(draft_id, PICK_MEAL)},
            {"text": "📅 Дата", "callback_data": cb(draft_id, PICK_DATE)},
        ]
    )
    rows.append([{"text": "← Назад", "callback_data": cb(draft_id, BACK)}])
    return {"inline_keyboard": rows}


def item_keyboard(draft_id: int, index: int, item: dict) -> dict:
    rows = []
    for position, candidate in enumerate(item.get("candidates", [])[:5]):
        mark = "• " if position == item.get("chosen") else ""
        rows.append(
            [
                {
                    "text": f"{mark}{candidate['title'][:40]}",
                    "callback_data": cb(draft_id, PICK_CANDIDATE, f"{index}.{position}"),
                }
            ]
        )
    rows.append(
        [{"text": "⚖️ Указать количество", "callback_data": cb(draft_id, ASK_GRAMS, index)}]
    )
    rows.append([{"text": "← Назад", "callback_data": cb(draft_id, EDIT)}])
    return {"inline_keyboard": rows}


def meal_keyboard(draft_id: int) -> dict:
    rows = [
        [
            {
                "text": MEAL_RU[meal],
                "callback_data": cb(draft_id, PICK_MEAL, meal.value),
            }
        ]
        for meal in Meal
    ]
    rows.append([{"text": "← Назад", "callback_data": cb(draft_id, EDIT)}])
    return {"inline_keyboard": rows}


def date_keyboard(draft_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Сегодня", "callback_data": cb(draft_id, PICK_DATE, "today")}],
            [{"text": "Вчера", "callback_data": cb(draft_id, PICK_DATE, "yesterday")}],
            [{"text": "← Назад", "callback_data": cb(draft_id, EDIT)}],
        ]
    }
