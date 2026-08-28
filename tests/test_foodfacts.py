"""Разбор ответа Open Food Facts.

База открытая и наполняется людьми, поэтому дыры в данных — норма, а не исключение:
у половины товаров нет ни одного нутриента. Создавать из такого продукт нельзя —
удалить его через API FatSecret нечем.
"""

import asyncio

import httpx

from fsbot.foodfacts import OpenFoodFacts, parse_product

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
    assert product["nutrition_basis"] == "g"
    assert product["confidence"] == 0.9


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


def test_impossible_complete_nutrition_is_refused():
    payload = {
        **TUNA_SALAD,
        "product": {
            **TUNA_SALAD["product"],
            "nutriments": {
                "energy-kcal_100g": 50,
                "proteins_100g": 25,
                "fat_100g": 25,
                "carbohydrates_100g": 25,
            },
        },
    }
    assert parse_product(payload) is None


def test_liquid_nutrition_keeps_100ml_basis():
    payload = {
        **TUNA_SALAD,
        "product": {
            **TUNA_SALAD["product"],
            "nutrition_data_per": "100g",
            "product_quantity_unit": "ml",
        },
    }

    product = parse_product(payload)

    assert product["nutrition_basis"] == "ml"


async def lookup_with_transport(handler):
    off = OpenFoodFacts()
    await off._client.aclose()
    off._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await off.lookup("0036000291452")
    finally:
        await off.close()


def test_lookup_sends_restricted_fields_and_parses_product():
    def handler(request):
        assert request.url.path.endswith("/0036000291452.json")
        fields = request.url.params["fields"]
        assert "nutriments" in fields
        assert "product_quantity_unit" in fields
        return httpx.Response(200, json=TUNA_SALAD)

    product = asyncio.run(lookup_with_transport(handler))

    assert product["name"] == "Beans and Smoked Tuna"
    assert product["confidence"] == 0.9


def test_lookup_network_failure_is_a_normal_miss_not_a_crash():
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    assert asyncio.run(lookup_with_transport(handler)) is None
