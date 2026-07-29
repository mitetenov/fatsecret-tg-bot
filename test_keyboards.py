"""Tests for inline keyboards (keyboards.py)."""

from keyboards import (
    build_food_results_keyboard,
    build_confirm_keyboard,
    build_serving_keyboard,
)


class TestBuildFoodResultsKeyboard:
    """Inline keyboard for /search results."""

    def test_builds_keyboard_from_food_list(self):
        foods = [
            {"food_id": "1", "food_name": "Apple", "brand_name": "Generic"},
            {"food_id": "2", "food_name": "Banana", "brand_name": ""},
            {"food_id": "3", "food_name": "Orange Juice", "brand_name": "Tropicana"},
        ]
        kb = build_food_results_keyboard(foods)

        # Should be an InlineKeyboardMarkup
        assert kb is not None
        inline_kb = kb.inline_keyboard
        assert len(inline_kb) == 3

        # First button shows food name only when no brand
        assert inline_kb[0][0].text == "Apple (Generic)"
        assert inline_kb[0][0].callback_data == "select:1"

        # Second button — no brand
        assert inline_kb[1][0].text == "Banana"
        assert inline_kb[1][0].callback_data == "select:2"

        # Third button with brand
        assert "Orange Juice" in inline_kb[2][0].text
        assert inline_kb[2][0].callback_data == "select:3"

    def test_empty_list_returns_empty_keyboard(self):
        kb = build_food_results_keyboard([])
        assert len(kb.inline_keyboard) == 0

    def test_long_names_are_truncated(self):
        foods = [
            {"food_id": "1", "food_name": "A" * 100, "brand_name": "B" * 100},
        ]
        kb = build_food_results_keyboard(foods)
        text = kb.inline_keyboard[0][0].text
        # PTB has no hard limit but we keep it reasonable
        assert len(text) < 80


class TestBuildConfirmKeyboard:
    """Confirm / cancel inline keyboard for photo analysis."""

    def test_builds_yes_no_keyboard(self):
        kb = build_confirm_keyboard("12345")
        inline_kb = kb.inline_keyboard

        assert len(inline_kb) == 1  # one row
        assert len(inline_kb[0]) == 2  # two buttons
        assert inline_kb[0][0].text == "✅ Yes, log it"
        assert inline_kb[0][0].callback_data == "confirm:12345"
        assert inline_kb[0][1].text == "❌ Cancel"
        assert inline_kb[0][1].callback_data == "cancel"


class TestBuildServingKeyboard:
    """Serving size selection keyboard."""

    def test_builds_keyboard_from_servings(self):
        servings = [
            {"serving_id": "100", "serving_description": "1 cup (240ml)"},
            {"serving_id": "101", "serving_description": "100g"},
            {"serving_id": "102", "serving_description": "1 tbsp (15ml)"},
        ]
        kb = build_serving_keyboard(servings, "12345")
        inline_kb = kb.inline_keyboard

        assert len(inline_kb) == 3
        assert inline_kb[0][0].text == "1 cup (240ml)"
        assert inline_kb[0][0].callback_data == "serving:12345:100"
        assert inline_kb[1][0].text == "100g"
        assert inline_kb[1][0].callback_data == "serving:12345:101"
