"""Пересчёт количества в Порции FatSecret.

Legacy `food_entry.create` принимает не граммы, а `serving_id` + `number_of_units` —
множитель к выбранной Порции. Поэтому граммы приходится переводить в порции, а у
продукта метрической порции может не быть вовсе (у Quaker Old Fashioned Oats
единственная порция — «1/2 cup dry = 40 г»). Отсюда гибрид из решения 11: граммы по
умолчанию, штучные порции как fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

NUTRIENT_KEYS = ("calories", "protein", "fat", "carbohydrate")


def _decimal(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Serving:
    serving_id: str
    description: str
    metric_amount: float | None
    metric_unit: str | None
    calories: float
    protein: float
    fat: float
    carbohydrate: float
    # Сколько единиц measurement_description составляют эту Порцию: у «1 large» это 1,
    # у «100 g» — 100. Именно в этих единицах food_entry.create ждёт number_of_units.
    units_per_serving: float = 1.0

    @classmethod
    def from_api(cls, raw: dict) -> Serving:
        nutrients = {key: _decimal(raw.get(key)) or 0.0 for key in NUTRIENT_KEYS}
        return cls(
            serving_id=str(raw.get("serving_id", "")),
            description=str(raw.get("serving_description", "")).strip(),
            metric_amount=_decimal(raw.get("metric_serving_amount")),
            metric_unit=(raw.get("metric_serving_unit") or None),
            units_per_serving=_decimal(raw.get("number_of_units")) or 1.0,
            **nutrients,
        )

    @property
    def is_metric(self) -> bool:
        return bool(self.metric_amount) and self.metric_unit in {"g", "ml"}


@dataclass(frozen=True, slots=True)
class Portion:
    """Сколько съедено относительно Порции — и что из этого слать в API.

    `multiplier` — во сколько раз съеденное больше Порции; на него умножаются
    нутриенты. `api_units` — то же количество, но выраженное в единицах
    measurement_description, потому что именно этого ждёт `food_entry.create`.
    Для Порции «100 g» это отличается в сто раз, и путать их нельзя.
    """

    serving: Serving
    multiplier: float

    @property
    def api_units(self) -> float:
        return round(self.multiplier * self.serving.units_per_serving, 4)

    @property
    def grams(self) -> float | None:
        if not self.serving.is_metric or self.serving.metric_amount is None:
            return None
        return round(self.serving.metric_amount * self.multiplier, 1)

    def nutrient(self, key: str) -> float:
        return round(getattr(self.serving, key) * self.multiplier, 1)

    @property
    def calories(self) -> float:
        return self.nutrient("calories")

    def describe(self) -> str:
        if self.grams is not None:
            return f"{self.grams:g} {self.serving.metric_unit}"
        return f"{self.multiplier:g} × {self.serving.description}"


def parse_servings(food: dict) -> list[Serving]:
    """FatSecret отдаёт одну порцию объектом, несколько — списком."""
    raw = (food.get("servings") or {}).get("serving") or []
    if isinstance(raw, dict):
        raw = [raw]
    return [Serving.from_api(item) for item in raw]


def by_metric_amount(servings: list[Serving], amount: float, unit: str) -> Portion | None:
    """Перевести граммы или миллилитры в множитель к метрической Порции."""
    metric = [s for s in servings if s.is_metric and s.metric_unit == unit]
    if not metric:
        return None

    # Порция на 100 единиц даёт самый понятный множитель, если она есть.
    preferred = next((s for s in metric if s.metric_amount == 100), metric[0])
    assert preferred.metric_amount  # гарантировано is_metric
    return Portion(serving=preferred, multiplier=round(amount / preferred.metric_amount, 6))


def by_units(servings: list[Serving], serving_id: str, multiplier: float) -> Portion | None:
    serving = next((s for s in servings if s.serving_id == serving_id), None)
    return Portion(serving=serving, multiplier=multiplier) if serving else None


# Плотность напитков близка к единице (пиво ≈ 1.008, молоко ≈ 1.03), поэтому
# миллилитры и граммы взаимозаменяемы с точностью, которой для дневника достаточно.
INTERCHANGEABLE = {"g": "ml", "ml": "g"}


def default_portion(servings: list[Serving], amount: float, unit: str) -> Portion | None:
    """Основной путь — метрический; если метрики нет, берём первую штучную Порцию.

    Единицы приходится подменять: у пива в базе FatSecret все порции в граммах, а с
    упаковки читается «450 ml». Без подмены совпадения не находилось, и код молча
    сваливался на «1 штука порции» — карточка показывала 360 г независимо от того,
    что просил человек, и правка количества выглядела как сломанная.

    Для штучной порции количество трактуется как число единиц, а не как граммы:
    «2 яйца» → 2 × «1 large egg». Пересчитать граммы в штуки без веса штуки нельзя,
    поэтому такой Кандидат обязан попасть на подтверждение с явным вопросом.
    """
    if unit in INTERCHANGEABLE:
        metric = by_metric_amount(servings, amount, unit)
        if metric:
            return metric
        swapped = by_metric_amount(servings, amount, INTERCHANGEABLE[unit])
        if swapped:
            return swapped
    if not servings:
        return None
    return Portion(serving=servings[0], multiplier=amount if unit == "piece" else 1.0)
