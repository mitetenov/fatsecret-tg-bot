"""Кнопка создания Своего продукта появляется только там, где это безопасно."""

from fsbot.bot import ui


def draft(**item):
    base = {"name_ru": "Молоко", "amount": 100, "unit": "g"}
    return {"day": "2026-07-31", "meal": "breakfast", "items": [{**base, **item}]}


def buttons(markup):
    return [b["text"] for row in markup["inline_keyboard"] for b in row]


def test_create_button_appears_for_items_with_label_nutrition():
    d = draft(creatable={"name": "Молоко", "brand": "Sante", "kcal": 60,
                         "protein": 3, "fat": 3.2, "carbs": 4.7})
    assert any("Создать" in text for text in buttons(ui.draft_keyboard(1, d)))


def test_no_create_button_when_product_was_found():
    d = draft(food_id="123", title="Milk", portion="100 g", kcal=60,
              protein=3, fat=3.2, carbohydrate=4.7)
    assert not any("Создать" in text for text in buttons(ui.draft_keyboard(1, d)))


def test_no_create_button_without_nutrition():
    # Продукта нет и КБЖУ не считаны — создавать не из чего.
    assert not any("Создать" in text for text in buttons(ui.draft_keyboard(1, draft())))


def test_draft_without_argument_still_renders_main_buttons():
    assert buttons(ui.draft_keyboard(1)) == ["✅ Записать", "✏️ Изменить", "❌ Отмена"]
