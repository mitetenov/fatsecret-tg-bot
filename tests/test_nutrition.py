"""Физическая правдоподобность полного профиля КБЖУ на 100 г."""

import math

import pytest

from fsbot.domain.nutrition import plausible


@pytest.mark.parametrize(
    "values",
    [
        (186, 12, 12, 6.7),
        (60, 3, 3.2, 4.7),
        (231, 0, 0, 0),
    ],
)
def test_plausible_profiles_are_accepted(values):
    assert plausible(*values)


@pytest.mark.parametrize(
    "values",
    [
        (math.nan, 1, 1, 1),
        (math.inf, 1, 1, 1),
        (1001, 1, 1, 1),
        (100, -1, 1, 1),
        (100, 101, 1, 1),
        (50, 25, 25, 25),
    ],
)
def test_impossible_profiles_are_rejected(values):
    assert not plausible(*values)
