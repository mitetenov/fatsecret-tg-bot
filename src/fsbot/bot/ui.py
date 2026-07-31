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


def cb(draft_id: int, action: str, arg: str | int = "") -> str:
    return f"{draft_id}:{action}:{arg}"


def parse_cb(data: str) -> tuple[int, str, str]:
    draft_id, action, arg = data.split(":", 2)
    return int(draft_id), action, arg


def render_draft(draft: dict) -> str:
    # Названия продуктов приходят из чужой базы: «Ben & Jerry's», «<brand>» и прочее
    # ломают разметку HTML, а Telegram на такое отвечает ошибкой, а не показом текста.
    lines: list[str] = []
    total = 0.0
    for index, item in enumerate(draft["items"], start=1):
        if not item.get("food_id"):
            name = escape(str(item["name_ru"]))
            if item.get("creatable"):
                spec = item["creatable"]
                origin = (
                    f"источник: {escape(str(item['source']))}"
                    if item.get("source")
                    else "с этикетки"
                )
                lines.append(
                    f"{index}. <b>{name}</b> — в базе FatSecret нет, {origin}:\n"
                    f"    {spec['kcal']:g} ккал · Б {spec['protein']:g} · "
                    f"Ж {spec['fat']:g} · У {spec['carbs']:g} на 100 г"
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
                f"\n    ⚠️ на этикетке {item['label_kcal']:g} ккал/100 г — "
                f"расхождение {item['mismatch'] * 100:.0f}%, похоже, это другой продукт"
            )
        lines.append(line)

    meal = MEAL_RU[Meal(draft["meal"])]
    lines.append(f"\n<b>Итого: {total:g} ккал</b> · {meal} · {draft['day']}")
    return "\n".join(lines)


def draft_keyboard(draft_id: int, draft: dict | None = None) -> dict:
    rows = [
        [
            {"text": "✅ Записать", "callback_data": cb(draft_id, WRITE)},
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
