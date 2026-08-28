"""Сверка Кандидата с этикеткой по калорийности.

Реальный случай: на фото грузинского салата из тунца (186 ккал/100 г) поиск вернул
шоколад Chuao Chocolatier (500 ккал/100 г), и бот записал бы его молча. Оба числа у
бота были — не хватало сравнения.
"""

from fsbot.domain.matching import TOLERANCE, deviation, kcal_per_100g, matches_label
from fsbot.domain.servings import parse_servings

TUNA_SALAD_LABEL_KCAL = 186

CHOCOLATE = {
    "servings": {
        "serving": {
            "serving_id": "1",
            "serving_description": "100 g",
            "metric_serving_amount": "100.000",
            "metric_serving_unit": "g",
            "number_of_units": "100.000",
            "calories": "500",
            "protein": "5",
            "fat": "37.5",
            "carbohydrate": "55",
        }
    }
}

TUNA_SALAD = {
    "servings": {
        "serving": {
            "serving_id": "2",
            "serving_description": "1 serving (160 g)",
            "metric_serving_amount": "160.000",
            "metric_serving_unit": "g",
            "number_of_units": "1.000",
            "calories": "298",  # 186 ккал на 100 г
            "protein": "19",
            "fat": "18",
            "carbohydrate": "12",
        }
    }
}

PIECES_ONLY = {
    "servings": {
        "serving": {
            "serving_id": "3",
            "serving_description": "1 slice",
            "number_of_units": "1.000",
            "calories": "80",
        }
    }
}


def test_chocolate_is_rejected_for_a_tuna_salad_label():
    ok, gap = matches_label(TUNA_SALAD_LABEL_KCAL, parse_servings(CHOCOLATE))
    assert not ok
    assert gap > 1.5  # почти втрое калорийнее


def test_matching_product_passes_even_with_non_hundred_gram_serving():
    ok, gap = matches_label(TUNA_SALAD_LABEL_KCAL, parse_servings(TUNA_SALAD))
    assert ok
    assert gap < 0.01


def test_kcal_per_100g_scales_from_any_metric_serving():
    assert kcal_per_100g(parse_servings(TUNA_SALAD)) == 186.25


def test_products_without_metric_serving_are_not_rejected():
    # Сравнить не с чем — это не повод отвергать Кандидата, иначе штучные продукты
    # («1 ломтик») перестанут находиться вовсе.
    ok, gap = matches_label(TUNA_SALAD_LABEL_KCAL, parse_servings(PIECES_ONLY))
    assert ok
    assert gap is None


def test_tolerance_boundary():
    # Разные производители одного и того же продукта расходятся на проценты —
    # такие расхождения не должны считаться ошибкой.
    assert deviation(200, 200 * (1 + TOLERANCE - 0.01)) < TOLERANCE
    assert deviation(200, 200 * (1 + TOLERANCE + 0.01)) > TOLERANCE


def test_zero_label_kcal_never_rejects():
    assert deviation(0, 500) == 0.0


SMOKED_TUNA = {
    "servings": {
        "serving": {
            "serving_id": "9",
            "serving_description": "100 g",
            "metric_serving_amount": "100.000",
            "metric_serving_unit": "g",
            "number_of_units": "100.000",
            "calories": "201",
            "protein": "25.7",
            "fat": "10.1",
            "carbohydrate": "0",
        }
    }
}

TUNA_SALAD_LABEL = {"kcal": 186, "protein": 12, "fat": 12, "carbs": 6.7}


def test_calories_alone_let_a_wrong_product_through():
    # Регрессия: копчёный тунец расходится с салатом из тунца всего на 8% по калориям,
    # и проверка по одним калориям его пропускала.
    ok, _ = matches_label(TUNA_SALAD_LABEL["kcal"], parse_servings(SMOKED_TUNA))
    assert ok


def test_full_profile_rejects_it():
    ok, gap = matches_label(TUNA_SALAD_LABEL, parse_servings(SMOKED_TUNA))
    assert not ok
    assert gap > 1.0  # белок вдвое выше


def test_same_product_passes_full_profile():
    ok, _ = matches_label(
        {"kcal": 186, "protein": 19, "fat": 18, "carbs": 12}, parse_servings(TUNA_SALAD)
    )
    assert ok


def test_trace_amounts_do_not_count_as_mismatch():
    # 0.2 против 0.6 г углеводов — это «следы», а не разные продукты.
    label = {"kcal": 201, "protein": 25.7, "fat": 10.1, "carbs": 0.2}
    ok, _ = matches_label(label, parse_servings(SMOKED_TUNA))
    assert ok


def test_label_per_100ml_uses_ml_serving_not_gram_serving():
    liquid = {
        "servings": {
            "serving": [
                {
                    "serving_id": "g",
                    "metric_serving_amount": "100",
                    "metric_serving_unit": "g",
                    "number_of_units": "100",
                    "calories": "90",
                    "protein": "3",
                    "fat": "3",
                    "carbohydrate": "5",
                },
                {
                    "serving_id": "ml",
                    "metric_serving_amount": "100",
                    "metric_serving_unit": "ml",
                    "number_of_units": "100",
                    "calories": "60",
                    "protein": "3",
                    "fat": "3.2",
                    "carbohydrate": "4.7",
                },
            ]
        }
    }
    label = {"kcal": 60, "protein": 3, "fat": 3.2, "carbs": 4.7}

    ok, gap = matches_label(label, parse_servings(liquid), basis_unit="ml")

    assert ok
    assert gap < 0.01
