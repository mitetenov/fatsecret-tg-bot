"""Разбор ответа Open Food Facts.

База открытая и наполняется людьми, поэтому дыры в данных — норма, а не исключение:
у половины товаров нет ни одного нутриента. Создавать из такого продукт нельзя —
удалить его через API FatSecret нечем.
"""

from fsbot.foodfacts import parse_product

TUNA_SALAD = {
    "status": 1,
    "product": {
        "product_name": "Beans and Smoked Tuna",
        "brands": "Trata",
        "nutriments": {
            "energy-kcal_100g": 186,
            "proteins_100g": 12,
            "fat_100g": 12,
            "carbohydrates_100g": 6.7,
        },
    },
}


def test_real_product_is_parsed():
    product = parse_product(TUNA_SALAD)
    assert product["name"] == "Beans and Smoked Tuna"
    assert product["brand"] == "Trata"
    assert product["kcal_100g"] == 186
    assert product["carbs_100g"] == 6.7
    assert product["source"] == "openfoodfacts.org"


def test_unknown_barcode():
    assert parse_product({"status": 0}) is None


def test_product_without_nutriments_is_refused():
    # Реальный случай: подсолнечное масло EXGSP есть в базе, но КБЖУ пустые.
    payload = {"status": 1, "product": {"product_name": "Refined sunflower oil",
                                        "brands": "EXGSP", "nutriments": {}}}
    assert parse_product(payload) is None


def test_partial_nutriments_are_refused():
    payload = {"status": 1, "product": {"product_name": "X", "brands": "Y",
               "nutriments": {"energy-kcal_100g": 100, "proteins_100g": 5}}}
    assert parse_product(payload) is None


def test_nameless_product_is_refused():
    payload = {"status": 1, "product": {"product_name": "  ", "brands": "Y",
               "nutriments": {"energy-kcal_100g": 100, "proteins_100g": 5,
                              "fat_100g": 1, "carbohydrates_100g": 2}}}
    assert parse_product(payload) is None


def test_first_brand_is_taken():
    payload = {**TUNA_SALAD, "product": {**TUNA_SALAD["product"], "brands": "Trata, Konva"}}
    assert parse_product(payload)["brand"] == "Trata"
