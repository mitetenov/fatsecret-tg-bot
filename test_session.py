"""Tests for user session management (session.py)."""

from unittest.mock import MagicMock, patch

import pytest


class TestUserSession:
    """UserSession — ConversationHandler state tracking."""

    def test_init_defaults(self):
        from session import UserSession

        sess = UserSession(user_id=12345)
        assert sess.user_id == 12345
        assert sess.selected_food_id is None
        assert sess.selected_food_name is None
        assert sess.analysis is None
        assert sess.state == "idle"

    def test_set_food_selection(self):
        from session import UserSession

        sess = UserSession(user_id=12345)
        sess.select_food("99", "Test Food")
        assert sess.selected_food_id == "99"
        assert sess.selected_food_name == "Test Food"
        assert sess.state == "food_selected"

    def test_set_analysis(self):
        from session import UserSession
        from vision import FoodAnalysis

        sess = UserSession(user_id=12345)
        fa = FoodAnalysis(food_name="Pizza", calories=800, protein=30.0, fat=35.0, carbs=70.0)
        sess.set_analysis(fa)
        assert sess.analysis is fa
        assert sess.state == "analysis_ready"

    def test_reset(self):
        from session import UserSession
        from vision import FoodAnalysis

        sess = UserSession(user_id=12345)
        sess.select_food("1", "Food")
        sess.set_analysis(FoodAnalysis(food_name="X"))
        sess.reset()

        assert sess.selected_food_id is None
        assert sess.analysis is None
        assert sess.state == "idle"

    def test_select_serving_stores_details(self):
        from session import UserSession

        sess = UserSession(user_id=1)
        sess.select_serving(
            serving_id="s1",
            description="100g",
            grams=100.0,
            calories=50.0,
            protein=5.0,
            fat=2.0,
            carbs=10.0,
        )
        assert sess.selected_serving_id == "s1"
        assert sess.selected_serving_desc == "100g"
        assert sess.selected_serving_grams == 100.0
        assert sess.serving_calories == 50.0
        assert sess.state == "awaiting_amount"

    def test_set_amount_calculates_multiplier(self):
        from session import UserSession

        sess = UserSession(user_id=1)
        sess.selected_serving_grams = 100.0
        sess.set_amount("200g", 200.0)
        assert sess.amount_raw == "200g"
        assert sess.amount_grams == 200.0
        assert sess.servings_multiplier == 2.0

    def test_set_amount_zero_grams_defaults_to_one(self):
        from session import UserSession

        sess = UserSession(user_id=1)
        sess.selected_serving_grams = 0.0
        sess.set_amount("1 plate", 0.0)
        assert sess.servings_multiplier == 1.0

    def test_get_calculated_kbju_scales_correctly(self):
        from session import UserSession

        sess = UserSession(user_id=1)
        sess.serving_calories = 100.0
        sess.serving_protein = 10.0
        sess.serving_fat = 5.0
        sess.serving_carbs = 20.0
        sess.selected_serving_grams = 100.0
        sess.set_amount("150g", 150.0)

        kbju = sess.get_calculated_kbju()
        assert kbju["calories"] == 150.0
        assert kbju["protein"] == 15.0
        assert kbju["fat"] == 7.5
        assert kbju["carbs"] == 30.0

    def test_set_default_serving(self):
        from session import UserSession

        sess = UserSession(user_id=1)
        assert sess.default_serving_size == "100g"
        sess.set_default_serving("200ml")
        assert sess.default_serving_size == "200ml"

    def test_reset_clears_amount_fields(self):
        from session import UserSession

        sess = UserSession(user_id=1)
        sess.select_serving("s1", "100g", 100.0, 50.0, 5.0, 2.0, 10.0)
        sess.set_amount("150g", 150.0)
        sess.reset()

        assert sess.selected_serving_id is None
        assert sess.amount_raw is None
        assert sess.amount_grams == 0.0
        assert sess.servings_multiplier == 1.0
        assert sess.state == "idle"


class TestSessionManager:
    """SessionManager — per-user session registry."""

    def test_get_or_create_returns_existing(self):
        from session import SessionManager

        sm = SessionManager()
        s1 = sm.get_or_create(111)
        s2 = sm.get_or_create(111)
        assert s1 is s2

    def test_get_or_create_different_users(self):
        from session import SessionManager

        sm = SessionManager()
        s1 = sm.get_or_create(1)
        s2 = sm.get_or_create(2)
        assert s1 is not s2
        assert s1.user_id == 1
        assert s2.user_id == 2

    def test_remove(self):
        from session import SessionManager

        sm = SessionManager()
        sm.get_or_create(999)
        sm.remove(999)
        # After remove, get_or_create returns a new session
        s_new = sm.get_or_create(999)
        assert s_new.state == "idle"
