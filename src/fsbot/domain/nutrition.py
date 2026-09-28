"""Проверка правдоподобия полного профиля КБЖУ на 100 г."""

from __future__ import annotations

import math

MAX_KCAL = 1000.0
MAX_MACRO = 100.0
MIN_ENERGY_TOLERANCE = 80.0
RELATIVE_ENERGY_TOLERANCE = 0.35


def plausible(kcal: float, protein: float, fat: float, carbs: float) -> bool:
    """Можно ли безопасно использовать профиль для создания Своего продукта."""
    values = (kcal, protein, fat, carbs)
    if not all(math.isfinite(value) for value in values):
        return False
    if not 0 <= kcal <= MAX_KCAL:
        return False
    if any(not 0 <= value <= MAX_MACRO for value in (protein, fat, carbs)):
        return False

    macro_energy = 4 * protein + 9 * fat + 4 * carbs
    if macro_energy < 1:
        return True
    tolerance = max(MIN_ENERGY_TOLERANCE, RELATIVE_ENERGY_TOLERANCE * kcal)
    return abs(macro_energy - kcal) <= tolerance


def validated_nutrition(values: dict[str, object]) -> dict[str, float] | None:
    """Нормализовать КБЖУ из внешнего источника перед использованием."""
    try:
        result = {key: float(values[key]) for key in ("kcal", "protein", "fat", "carbs")}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return result if plausible(**result) else None
