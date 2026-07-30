"""Дата и Приём пищи для Позиции.

У Позиции в FatSecret нет времени — только дата и Приём пищи, и решение принимается
один раз, в момент записи. Поэтому правило вынесено в чистые функции: см. ADR 0002
(сутки кончаются в 04:00) — менять его нужно осознанно и вместе с тестами.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

# Ночная еда относится к предыдущему дню: календарно новый день, по смыслу — ужин.
DAY_STARTS_AT_HOUR = 4

EPOCH = date(1970, 1, 1)


class Meal(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    OTHER = "other"


MEAL_RU = {
    Meal.BREAKFAST: "завтрак",
    Meal.LUNCH: "обед",
    Meal.DINNER: "ужин",
    Meal.OTHER: "другое",
}


def local_now(tz: str, now_utc: datetime | None = None) -> datetime:
    moment = now_utc or datetime.now(tz=ZoneInfo("UTC"))
    return moment.astimezone(ZoneInfo(tz))


def diary_date(local: datetime) -> date:
    """Дата Дневника: до 04:00 еда относится к предыдущему календарному дню."""
    if local.hour < DAY_STARTS_AT_HOUR:
        return (local - timedelta(days=1)).date()
    return local.date()


def meal_by_hour(local: datetime) -> Meal:
    hour = local.hour
    if hour < DAY_STARTS_AT_HOUR:
        return Meal.OTHER  # ночной перекус предыдущих суток
    if hour < 11:
        return Meal.BREAKFAST
    if hour < 16:
        return Meal.LUNCH
    if hour < 22:
        return Meal.DINNER
    return Meal.OTHER


def resolve(
    tz: str,
    now_utc: datetime | None = None,
    meal_hint: str | None = None,
    date_hint: str | None = None,
) -> tuple[date, Meal]:
    """Явное указание в Реплике перебивает автоопределение по часам."""
    local = local_now(tz, now_utc)
    day = diary_date(local)
    meal = meal_by_hour(local)

    if meal_hint:
        try:
            meal = Meal(meal_hint)
        except ValueError:
            pass
    if date_hint == "yesterday":
        day -= timedelta(days=1)

    return day, meal


def to_fatsecret_date(day: date) -> int:
    """Legacy food_entry.create принимает дату как число дней с 1 января 1970."""
    return (day - EPOCH).days


def from_fatsecret_date(value: int) -> date:
    return EPOCH + timedelta(days=value)
