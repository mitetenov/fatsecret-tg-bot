"""Граница суток и Приём пищи — правило из ADR 0002."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fsbot.domain.daybounds import (
    Meal,
    diary_date,
    from_fatsecret_date,
    local_now,
    meal_by_hour,
    resolve,
    to_fatsecret_date,
)

TBILISI = "Asia/Tbilisi"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("UTC"))


@pytest.mark.parametrize(
    ("local_time", "expected"),
    [
        ("2026-07-30 00:40", date(2026, 7, 29)),  # ночная еда — предыдущий день
        ("2026-07-30 03:59", date(2026, 7, 29)),
        ("2026-07-30 04:00", date(2026, 7, 30)),  # ровно на границе — уже новый день
        ("2026-07-30 23:59", date(2026, 7, 30)),
    ],
)
def test_diary_date_shifts_before_four_am(local_time: str, expected: date):
    local = datetime.fromisoformat(local_time).replace(tzinfo=ZoneInfo(TBILISI))
    assert diary_date(local) == expected


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (2, Meal.OTHER),
        (4, Meal.BREAKFAST),
        (10, Meal.BREAKFAST),
        (11, Meal.LUNCH),
        (15, Meal.LUNCH),
        (16, Meal.DINNER),
        (21, Meal.DINNER),
        (22, Meal.OTHER),
    ],
)
def test_meal_by_hour(hour: int, expected: Meal):
    local = datetime(2026, 7, 30, hour, 0, tzinfo=ZoneInfo(TBILISI))
    assert meal_by_hour(local) == expected


def test_timezone_decides_the_day_not_the_server():
    # 22:30 UTC — в Тбилиси (UTC+4) уже 02:30 следующих суток, то есть по правилу
    # 04:00 это всё ещё предыдущий день; в Берлине — вечер того же дня.
    moment = utc("2026-07-30 22:30")
    assert diary_date(local_now(TBILISI, moment)) == date(2026, 7, 30)
    assert diary_date(local_now("Europe/Berlin", moment)) == date(2026, 7, 30)

    moment = utc("2026-07-30 20:30")
    assert local_now(TBILISI, moment).hour == 0
    assert diary_date(local_now(TBILISI, moment)) == date(2026, 7, 30)


def test_explicit_hints_override_autodetection():
    moment = utc("2026-07-30 09:00")  # 13:00 в Тбилиси → обед
    assert resolve(TBILISI, moment) == (date(2026, 7, 30), Meal.LUNCH)
    assert resolve(TBILISI, moment, meal_hint="dinner")[1] == Meal.DINNER
    assert resolve(TBILISI, moment, date_hint="yesterday")[0] == date(2026, 7, 29)


def test_unknown_meal_hint_is_ignored():
    moment = utc("2026-07-30 09:00")
    assert resolve(TBILISI, moment, meal_hint="brunch")[1] == Meal.LUNCH


def test_fatsecret_date_is_days_since_epoch():
    assert to_fatsecret_date(date(1970, 1, 1)) == 0
    assert to_fatsecret_date(date(1970, 1, 2)) == 1
    assert from_fatsecret_date(to_fatsecret_date(date(2026, 7, 30))) == date(2026, 7, 30)
