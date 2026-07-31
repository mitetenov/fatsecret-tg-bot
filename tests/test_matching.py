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
