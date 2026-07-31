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


def kcal_per_100g(servings: list[Serving]) -> float | None:
    """Калорийность продукта на 100 г, если её вообще можно вычислить."""
    for serving in servings:
        if serving.is_metric and serving.metric_amount and serving.calories:
            return serving.calories / serving.metric_amount * 100
    return None


def deviation(label_kcal: float, candidate_kcal: float) -> float:
    """Относительное расхождение: 0.0 — совпало, 1.0 — вдвое больше."""
    if label_kcal <= 0:
        return 0.0
    return abs(candidate_kcal - label_kcal) / label_kcal


def matches_label(label_kcal: float, servings: list[Serving]) -> tuple[bool, float | None]:
    """Похож ли Кандидат на то, что написано на этикетке.

    Возвращает (похож, расхождение). Расхождение None означает «сравнить не удалось» —
    у продукта нет метрической порции, и это не повод его отвергать.
    """
    candidate = kcal_per_100g(servings)
    if candidate is None:
        return True, None
    gap = deviation(label_kcal, candidate)
    return gap <= TOLERANCE, gap
