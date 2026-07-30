"""Текст сообщения подтверждения и клавиатуры.

Принцип из решения 9: типичный случай — один тап «Записать», всё остальное спрятано
за «Изменить». Приём пищи и дата всегда видны в тексте, чтобы автоопределение не могло
ошибиться молча.
"""

from __future__ import annotations

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


def cb(draft_id: int, action: str, arg: str | int = "") -> str:
    return f"{draft_id}:{action}:{arg}"


def parse_cb(data: str) -> tuple[int, str, str]:
    draft_id, action, arg = data.split(":", 2)
    return int(draft_id), action, arg


def render_draft(draft: dict) -> str:
    lines: list[str] = []
    total = 0.0
    for index, item in enumerate(draft["items"], start=1):
        if not item.get("food_id"):
            lines.append(f"{index}. <b>{item['name_ru']}</b> — не нашёл в базе FatSecret")
            continue
        total += item["kcal"]
        lines.append(
            f"{index}. <b>{item['title']}</b> — {item['portion']}\n"
            f"    {item['kcal']:g} ккал · Б {item['protein']:g} · "
            f"Ж {item['fat']:g} · У {item['carbohydrate']:g}"
        )

    meal = MEAL_RU[Meal(draft["meal"])]
    lines.append(f"\n<b>Итого: {total:g} ккал</b> · {meal} · {draft['day']}")
    return "\n".join(lines)


def draft_keyboard(draft_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Записать", "callback_data": cb(draft_id, WRITE)},
                {"text": "✏️ Изменить", "callback_data": cb(draft_id, EDIT)},
                {"text": "❌ Отмена", "callback_data": cb(draft_id, CANCEL)},
            ]
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
