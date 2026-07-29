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
    selected_serving_desc: str | None = None
    selected_serving_grams: float = 0.0
    # Per-serving KBJU (from FatSecret)
    serving_calories: float = 0.0
    serving_protein: float = 0.0
    serving_fat: float = 0.0
    serving_carbs: float = 0.0
    # Amount input
    amount_raw: str | None = None
    amount_grams: float = 0.0
    servings_multiplier: float = 1.0
    default_serving_size: str = "100g"
    analysis: FoodAnalysis | None = None

    def select_food(self, food_id: str, food_name: str) -> None:
        self.selected_food_id = food_id
        self.selected_food_name = food_name
        self.state = "food_selected"

    def select_serving(
        self,
        serving_id: str,
        description: str,
        grams: float,
        calories: float,
        protein: float,
        fat: float,
        carbs: float,
    ) -> None:
        """Store selected serving details and transition to awaiting amount."""
        self.selected_serving_id = serving_id
        self.selected_serving_desc = description
        self.selected_serving_grams = grams
        self.serving_calories = calories
        self.serving_protein = protein
        self.serving_fat = fat
        self.serving_carbs = carbs
        self.state = "awaiting_amount"

    def set_amount(self, amount_raw: str, amount_grams: float) -> None:
        """Set the user's amount input and calculate the servings multiplier."""
        self.amount_raw = amount_raw
        self.amount_grams = amount_grams
        if self.selected_serving_grams > 0:
            self.servings_multiplier = amount_grams / self.selected_serving_grams
        else:
            self.servings_multiplier = 1.0

    def get_calculated_kbju(self) -> dict[str, float]:
        """Return KBJU scaled by the servings multiplier."""
        m = self.servings_multiplier
        return {
            "calories": round(self.serving_calories * m, 1),
            "protein": round(self.serving_protein * m, 1),
            "fat": round(self.serving_fat * m, 1),
            "carbs": round(self.serving_carbs * m, 1),
        }

    def set_analysis(self, fa: FoodAnalysis) -> None:
        self.analysis = fa
        self.state = "analysis_ready"

    def set_default_serving(self, size: str) -> None:
        """Set the user's default serving size (e.g. '100g', '1 cup')."""
        self.default_serving_size = size

    def reset(self) -> None:
        self.selected_food_id = None
        self.selected_food_name = None
        self.selected_serving_id = None
        self.selected_serving_desc = None
        self.selected_serving_grams = 0.0
        self.serving_calories = 0.0
        self.serving_protein = 0.0
        self.serving_fat = 0.0
        self.serving_carbs = 0.0
        self.amount_raw = None
        self.amount_grams = 0.0
        self.servings_multiplier = 1.0
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
