"""Пересчёт количества в Порции — на реальных ответах FatSecret.

Фикстуры сняты с живого API. Ключевой случай — порция «100 g» у яйца: у неё
`number_of_units` = 100, потому что единица измерения там грамм, а не «порция».
Именно на этом бот однажды записал 1,65 г вместо 165 г.
"""

from fsbot.domain.servings import Serving, by_metric_amount, default_portion, parse_servings

# food.get(27572): единственная порция, и она не 100 г
OATS = {
    "servings": {
        "serving": {
            "calories": "150",
            "carbohydrate": "27.00",
            "fat": "3.00",
            "measurement_description": "serving",
            "metric_serving_amount": "40.000",
            "metric_serving_unit": "g",
            "number_of_units": "1.000",
            "protein": "5.00",
            "serving_description": "1/2 cup dry",
            "serving_id": "73894",
        }
    }
}

# food.get(3092): у «100 g» number_of_units = 100, у штучных — 1
EGG = {
    "servings": {
        "serving": [
            {
                "serving_id": "11206",
                "serving_description": "1 large",
                "measurement_description": "large",
                "number_of_units": "1.000",
                "metric_serving_amount": "50.000",
                "metric_serving_unit": "g",
                "calories": "74",
                "protein": "6.3",
                "fat": "5",
                "carbohydrate": "0.4",
            },
            {
                "serving_id": "51772",
                "serving_description": "100 g",
                "measurement_description": "g",
                "number_of_units": "100.000",
                "metric_serving_amount": "100.000",
                "metric_serving_unit": "g",
                "calories": "147",
                "protein": "12.6",
                "fat": "9.94",
                "carbohydrate": "0.77",
            },
        ]
    }
}

SLICE_ONLY = {
    "servings": {
        "serving": {
            "serving_id": "1",
            "serving_description": "1 slice",
            "measurement_description": "slice",
            "number_of_units": "1.000",
            "calories": "80",
            "protein": "3",
            "fat": "1",
            "carbohydrate": "14",
        }
    }
}


def test_single_serving_is_parsed_from_object_not_list():
    servings = parse_servings(OATS)
    assert len(servings) == 1
    assert servings[0].serving_id == "73894"
    assert servings[0].is_metric


def test_grams_on_a_hundred_gram_serving_go_to_api_as_grams():
    """Регрессия: number_of_units измеряется в measurement_description ('g'),
    поэтому для 110 г при порции «100 g» в API уходит 110, а не 1.1."""
    portion = default_portion(parse_servings(EGG), 110, "g")
    assert portion is not None
    assert portion.serving.serving_id == "51772"
    assert portion.multiplier == 1.1  # нутриенты считаются по множителю
    assert portion.api_units == 110.0  # а в API уходят граммы
    assert portion.grams == 110
    assert portion.calories == 161.7
    assert portion.describe() == "110 g"


def test_grams_on_a_non_metric_baseline_serving():
    """У овсянки number_of_units = 1, поэтому множитель и значение для API совпадают —
    именно этот случай раньше маскировал ошибку."""
    portion = default_portion(parse_servings(OATS), 60, "g")
    assert portion is not None
    assert portion.multiplier == 1.5
    assert portion.api_units == 1.5
    assert portion.grams == 60
    assert portion.calories == 225


def test_hundred_gram_serving_is_preferred_when_available():
    portion = by_metric_amount(parse_servings(EGG), 50, "g")
    assert portion is not None
    assert portion.serving.serving_id == "51772"
    assert portion.api_units == 50.0


def test_falls_back_to_piece_serving_when_no_metric_exists():
    servings = parse_servings(SLICE_ONLY)
    assert by_metric_amount(servings, 100, "g") is None

    portion = default_portion(servings, 2, "piece")
    assert portion is not None
    assert portion.multiplier == 2
    assert portion.api_units == 2.0
    assert portion.grams is None  # вес штуки неизвестен — граммы не выдумываем
    assert portion.describe() == "2 × 1 slice"
    assert portion.calories == 160


def test_grams_requested_but_only_piece_serving_keeps_one_unit():
    portion = default_portion(parse_servings(SLICE_ONLY), 150, "g")
    assert portion is not None
    assert portion.multiplier == 1.0
    assert portion.api_units == 1.0


def test_missing_fields_do_not_crash_and_default_to_one_unit():
    serving = Serving.from_api({"serving_id": "9", "serving_description": "1 cup"})
    assert serving.calories == 0.0
    assert serving.units_per_serving == 1.0
    assert not serving.is_metric


def test_no_servings_at_all():
    assert default_portion([], 100, "g") is None


BEER = {
    "servings": {
        "serving": [
            {
                "serving_id": "1",
                "serving_description": "1 can or bottle (12 fl oz)",
                "measurement_description": "can",
                "number_of_units": "1.000",
                "metric_serving_amount": "360.000",
                "metric_serving_unit": "g",
                "calories": "155",
                "protein": "1.7",
                "fat": "0",
                "carbohydrate": "12.8",
            },
            {
                "serving_id": "2",
                "serving_description": "100 g",
                "measurement_description": "g",
                "number_of_units": "100.000",
                "metric_serving_amount": "100.000",
                "metric_serving_unit": "g",
                "calories": "43",
                "protein": "0.5",
                "fat": "0",
                "carbohydrate": "3.6",
            },
        ]
    }
}


def test_millilitres_fall_back_to_gram_servings():
    """Регрессия: у пива в базе только граммовые порции, а с банки читается «450 ml».

    Без подмены единиц выбиралась штучная порция «1 can», количество игнорировалось,
    и правка на другое число ничего не меняла.
    """
    portion = default_portion(parse_servings(BEER), 450, "ml")
    assert portion is not None
    assert portion.serving.serving_id == "2"
    assert portion.grams == 450
    assert portion.calories == 193.5


def test_amount_change_is_visible_for_drinks():
    servings = parse_servings(BEER)
    assert default_portion(servings, 450, "ml").calories == 193.5
    assert default_portion(servings, 330, "ml").calories == 141.9


def test_exact_unit_still_wins_over_swap():
    with_ml = {
        "servings": {
            "serving": [
                BEER["servings"]["serving"][1],
                {
                    "serving_id": "3",
                    "serving_description": "100 ml",
                    "measurement_description": "ml",
                    "number_of_units": "100.000",
                    "metric_serving_amount": "100.000",
                    "metric_serving_unit": "ml",
                    "calories": "41",
                    "protein": "0.4",
                    "fat": "0",
                    "carbohydrate": "3.4",
                },
            ]
        }
    }
    portion = default_portion(parse_servings(with_ml), 450, "ml")
    assert portion.serving.serving_id == "3"


def test_derived_v5_serving_cannot_be_used_for_diary_entry():
    food = {
        "servings": {
            "serving": [
                {
                    "serving_id": "0",
                    "serving_description": "100 ml",
                    "metric_serving_amount": "100",
                    "metric_serving_unit": "ml",
                    "number_of_units": "100",
                    "calories": "60",
                },
                {
                    "serving_id": "123",
                    "serving_description": "1 bottle",
                    "metric_serving_amount": "450",
                    "metric_serving_unit": "ml",
                    "number_of_units": "1",
                    "calories": "270",
                },
            ]
        }
    }

    portion = default_portion(parse_servings(food), 225, "ml")

    assert portion.serving.serving_id == "123"
    assert portion.api_units == 0.5
