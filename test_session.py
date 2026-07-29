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
