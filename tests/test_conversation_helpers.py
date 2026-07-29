"""Unit tests for conversation_helpers module."""

from unittest import mock

import pytest

from conversation_helpers import (
    UNITS,
    _build_confirm_keyboard,
    _build_unit_keyboard,
    _clear_pending,
    build_food_info_lines,
    build_food_info_text,
    extract_nutrition,
)


# ── Nutrition extraction ─────────────────────────────────────────────


class TestExtractNutrition:
    def test_extract_from_food_dict(self):
        food = {
            "food_name": "Chicken",
            "servings": {
                "serving": [
                    {
                        "serving_description": "100 g",
                        "calories": "165",
                        "protein": "31",
                        "fat": "3.6",
                        "carbohydrate": "0",
                    }
                ]
            },
        }
        nut = extract_nutrition(food)
        assert nut["calories"] == 165.0
        assert nut["protein"] == 31.0
        assert nut["fat"] == 3.6
        assert nut["carbs"] == 0.0
        assert nut["serving_description"] == "100 g"

    def test_extract_single_serving_not_list(self):
        """FatSecret sometimes returns a single dict, not a list."""
        food = {
            "servings": {
                "serving": {
                    "serving_description": "1 cup",
                    "calories": "100",
                    "protein": "2",
                    "fat": "1",
                    "carbohydrate": "20",
                }
            }
        }
        nut = extract_nutrition(food)
        assert nut["calories"] == 100.0

    def test_extract_empty_servings(self):
        nut = extract_nutrition({})
        assert nut == {}

    def test_extract_with_specific_index(self):
        food = {
            "servings": {
                "serving": [
                    {"serving_description": "small", "calories": "50"},
                    {"serving_description": "large", "calories": "200"},
                ]
            }
        }
        nut = extract_nutrition(food, servings_index=1)
        assert nut["calories"] == 200.0

    def test_extract_index_out_of_range(self):
        """Clamps to last available serving."""
        food = {
            "servings": {
                "serving": [{"calories": "100"}]
            }
        }
        nut = extract_nutrition(food, servings_index=5)
        assert nut["calories"] == 100.0


# ── Food info text ───────────────────────────────────────────────────


class TestBuildFoodInfoText:
    def test_basic(self):
        food = {
            "food_id": "123",
            "food_name": "Apple",
            "brand_name": None,
            "food_type": "Generic",
            "servings": {
                "serving": [{
                    "serving_description": "1 medium",
                    "calories": "95",
                    "protein": "0.5",
                    "fat": "0.3",
                    "carbohydrate": "25",
                }]
            },
        }
        text = build_food_info_text(food, index=3)
        assert "3." in text
        assert "Apple" in text
        assert "95 kcal" in text

    def test_with_brand(self):
        food = {
            "food_id": "456",
            "food_name": "Yogurt",
            "brand_name": "Chobani",
            "food_type": "",
            "servings": {"serving": []},
        }
        text = build_food_info_text(food, index=1)
        assert "Chobani" in text
        assert "1." in text

    def test_no_nutrition(self):
        food = {
            "food_id": "789",
            "food_name": "Unknown",
            "brand_name": None,
            "food_type": "",
        }
        text = build_food_info_text(food)
        assert "Unknown" in text
        # No nutrition info should not crash
        assert len(text) > 0


# ── Food info lines ──────────────────────────────────────────────────


class TestBuildFoodInfoLines:
    def test_multiple_foods(self):
        foods = [
            {"food_id": "1", "food_name": "Apple", "brand_name": None, "food_type": ""},
            {"food_id": "2", "food_name": "Banana", "brand_name": None, "food_type": ""},
        ]
        text, keyboard = build_food_info_lines(foods)
        assert "Apple" in text
        assert "Banana" in text
        assert "Search results" in text
        # Keyboard rows: one per food + cancel button
        assert len(keyboard) == 3
        # First row has button with callback
        assert "select_food:1" in keyboard[0][0].callback_data
        assert "select_food:2" in keyboard[1][0].callback_data
        # Last row is cancel
        assert "cancel_log" in keyboard[2][0].callback_data


# ── Unit keyboard ────────────────────────────────────────────────────


class TestBuildUnitKeyboard:
    def test_has_all_units(self):
        kb = _build_unit_keyboard()
        # Count total buttons: 7 units + 1 cancel = 8
        all_buttons = []
        for row in kb.inline_keyboard:
            all_buttons.extend(row)
        assert len(all_buttons) == len(UNITS) + 1  # + cancel
        # Check cancel is last
        assert all_buttons[-1].text == "❌ Cancel"

    def test_all_unit_callbacks(self):
        kb = _build_unit_keyboard()
        callbacks = []
        for row in kb.inline_keyboard:
            for btn in row:
                callbacks.append(btn.callback_data)
        for value, _label in UNITS:
            assert f"select_unit:{value}" in callbacks


# ── Confirm keyboard ─────────────────────────────────────────────────


class TestBuildConfirmKeyboard:
    def test_yes_and_cancel(self):
        kb = _build_confirm_keyboard()
        buttons = [b for row in kb.inline_keyboard for b in row]
        assert len(buttons) == 2
        assert buttons[0].callback_data == "confirm_food:yes"
        assert buttons[0].text == "✅ Log this"
        assert buttons[1].callback_data == "cancel_log"


# ── Clear pending ────────────────────────────────────────────────────


class TestClearPending:
    def test_clears_all_keys(self):
        ctx = mock.MagicMock()
        ctx.user_data = {
            "pending_product_name": "Apple",
            "pending_quantity": 2.0,
            "pending_nutrition": {"calories": 100},
            "pending_brand": "Brand",
            "pending_food": {"id": "1"},
            "other_data": "keep_me",
        }
        _clear_pending(ctx)
        assert "pending_product_name" not in ctx.user_data
        assert "pending_quantity" not in ctx.user_data
        assert "pending_nutrition" not in ctx.user_data
        assert "pending_brand" not in ctx.user_data
        assert "pending_food" not in ctx.user_data
        assert "other_data" in ctx.user_data  # preserved
