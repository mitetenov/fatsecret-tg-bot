"""Per-user session tracking for ConversationHandler state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vision import FoodAnalysis  # noqa: F401 — re-exported for handlers


@dataclass
class UserSession:
    """Conversation state for a single Telegram user."""

    user_id: int
    state: str = "idle"
    selected_food_id: str | None = None
    selected_food_name: str | None = None
    selected_serving_id: str | None = None
    analysis: FoodAnalysis | None = None

    def select_food(self, food_id: str, food_name: str) -> None:
        self.selected_food_id = food_id
        self.selected_food_name = food_name
        self.state = "food_selected"

    def set_analysis(self, fa: FoodAnalysis) -> None:
        self.analysis = fa
        self.state = "analysis_ready"

    def reset(self) -> None:
        self.selected_food_id = None
        self.selected_food_name = None
        self.selected_serving_id = None
        self.analysis = None
        self.state = "idle"


class SessionManager:
    """Thread-safe registry of per-user sessions."""

    def __init__(self) -> None:
        self._sessions: dict[int, UserSession] = {}

    def get_or_create(self, user_id: int) -> UserSession:
        if user_id not in self._sessions:
            self._sessions[user_id] = UserSession(user_id=user_id)
        return self._sessions[user_id]

    def remove(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)
