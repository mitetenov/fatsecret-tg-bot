"""Сверка Кандидата с этикеткой.

Поиск по названию иногда возвращает совсем другой продукт: на грузинский салат из
тунца (186 ккал/100 г) бот однажды выдал шоколад Chuao Chocolatier (500 ккал/100 г) и
принял его молча. Ошибка была видна невооружённым глазом, но код её не замечал, хотя
оба числа у него были — с этикетки и из базы.

Калорийность на 100 г — самый простой и устойчивый признак: она не зависит ни от
языка, ни от бренда, ни от того, как продукт назван в базе.
"""

from __future__ import annotations

from fsbot.domain.servings import Serving

# 25% — примерно предел, в который укладываются честные расхождения: разные
# производители, «в среднем по категории», округления на упаковке. Всё, что дальше,
# почти всегда другой продукт.
TOLERANCE = 0.25

# Для отдельного макронутриента порог шире: белки и жиры у одного и того же продукта
# гуляют сильнее калорийности, а округления на упаковке бьют по ним заметнее.
MACRO_TOLERANCE = 0.40

# Ниже этого значения (г на 100 г) сравнивать бессмысленно: 0.2 против 0.4 — это
# «следы», а не разница продуктов.
MACRO_FLOOR = 1.0


def kcal_per_100(servings: list[Serving], unit: str = "g") -> float | None:
    """Калорийность продукта на 100 г, если её вообще можно вычислить."""
    for serving in servings:
        if (
            serving.is_metric
            and serving.metric_unit == unit
            and serving.metric_amount
            and serving.calories
        ):
            return serving.calories / serving.metric_amount * 100
    return None


def kcal_per_100g(servings: list[Serving]) -> float | None:
    return kcal_per_100(servings, "g")


def deviation(label_kcal: float, candidate_kcal: float) -> float:
    """Относительное расхождение: 0.0 — совпало, 1.0 — вдвое больше."""
    if label_kcal <= 0:
        return 0.0
    return abs(candidate_kcal - label_kcal) / label_kcal


def per_100(servings: list[Serving], unit: str = "g") -> dict[str, float] | None:
    """Полный профиль продукта на 100 г либо 100 мл."""
    for serving in servings:
        if (
            serving.is_metric
            and serving.metric_unit == unit
            and serving.metric_amount
            and serving.calories
        ):
            scale = 100 / serving.metric_amount
            return {
                "kcal": serving.calories * scale,
                "protein": serving.protein * scale,
                "fat": serving.fat * scale,
                "carbs": serving.carbohydrate * scale,
            }
    return None


def per_100g(servings: list[Serving]) -> dict[str, float] | None:
    return per_100(servings, "g")


def matches_label(
    label: dict[str, float] | float,
    servings: list[Serving],
    basis_unit: str = "g",
) -> tuple[bool, float | None]:
    """Похож ли Кандидат на то, что написано на этикетке.

    Сравниваются калории и все три макронутриента. Одних калорий мало: копчёный тунец
    (201 ккал, Б 25.7, У 0) и салат из тунца с фасолью (186 ккал, Б 12, У 6.7)
    расходятся по калорийности на 8% и проходили проверку, будучи разными продуктами.

    Возвращает (похож, расхождение). Расхождение None означает «сравнить не удалось» —
    у продукта нет метрической порции, и это не повод его отвергать.
    """
    if isinstance(label, (int, float)):
        label = {"kcal": float(label)}

    candidate = per_100(servings, basis_unit)
    if candidate is None:
        return True, None

    kcal_gap = deviation(label["kcal"], candidate["kcal"])
    if kcal_gap > TOLERANCE:
        return False, kcal_gap

    worst = kcal_gap
    for macro in ("protein", "fat", "carbs"):
        expected = label.get(macro)
        if expected is None:
            continue
        # Оба значения ниже порога — сравнивать нечего.
        if expected < MACRO_FLOOR and candidate[macro] < MACRO_FLOOR:
            continue
        gap = deviation(max(expected, MACRO_FLOOR), candidate[macro])
        worst = max(worst, gap)
        if gap > MACRO_TOLERANCE:
            return False, gap

    return True, worst
