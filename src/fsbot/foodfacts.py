"""Open Food Facts — открытая база товаров по штрих-кодам.

Появилась в проекте не из планов, а из наблюдения: когда веб-поиск через модель всё же
находил грузинский салат, источником он называл openfoodfacts.org. Прямой запрос к ней
даёт тот же ответ детерминированно, бесплатно и без риска, что модель что-то придумает,
— а поиск через модель на том же коде срабатывал лишь в двух прогонах из пяти.

Порядок источников: FatSecret (там дневник) → Open Food Facts → модель с веб-поиском.
"""

from __future__ import annotations

import logging

import httpx

from fsbot.domain import nutrition as nutrition_rules
from fsbot.domain.naming import pick_name

log = logging.getLogger(__name__)

API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

# Open Food Facts просит представляться: без внятного User-Agent запросы режут.
USER_AGENT = "fsbot/0.1 (https://github.com/mitetenov/fatsecret-tg-bot)"

FIELDS = (
    "product_name,product_name_ru,product_name_en,brands,brands_en,nutriments,"
    "nutrition_data_per,product_quantity_unit"
)

NUTRIENTS = {
    "kcal_100g": "energy-kcal_100g",
    "protein_100g": "proteins_100g",
    "fat_100g": "fat_100g",
    "carbs_100g": "carbohydrates_100g",
}


def parse_product(payload: dict) -> dict | None:
    """Ответ Open Food Facts → описание продукта или None.

    Без полного набора КБЖУ продукт бесполезен: создавать в дневнике запись с дырами
    хуже, чем не создавать вовсе, — удалить её через API FatSecret нечем.
    """
    if payload.get("status") != 1:
        return None

    product = payload.get("product") or {}
    # База многоязычная: у части товаров есть готовые русское и английское названия,
    # и они предпочтительнее основного поля, которое заполняют на языке страны.
    name = pick_name(
        [
            product.get("product_name_ru"),
            product.get("product_name_en"),
            product.get("product_name"),
        ]
    )
    if not name:
        return None

    nutriments = product.get("nutriments") or {}
    values: dict[str, float] = {}
    for key, source in NUTRIENTS.items():
        raw = nutriments.get(source)
        if raw is None:
            return None
        try:
            values[key] = float(raw)
        except (TypeError, ValueError):
            return None

    if not nutrition_rules.plausible(
        values["kcal_100g"],
        values["protein_100g"],
        values["fat_100g"],
        values["carbs_100g"],
    ):
        return None

    brand = pick_name(
        [
            (product.get("brands_en") or "").split(",")[0],
            (product.get("brands") or "").split(",")[0],
        ]
    )
    # OFF исторически называет нормализованное поле `_100g` и для жидкостей; по их
    # схеме оно означает 100 г *или 100 мл*. Различаем это по единице количества.
    declared_basis = str(product.get("nutrition_data_per") or "").lower().replace(" ", "")
    quantity_unit = str(product.get("product_quantity_unit") or "").lower()
    basis_unit = (
        "ml"
        if declared_basis == "100ml" or quantity_unit in {"ml", "cl", "l"}
        else "g"
    )
    return {
        "found": True,
        "name": name,
        "brand": brand or "fsbot",
        "source": "openfoodfacts.org",
        "nutrition_basis": basis_unit,
        "confidence": 0.9,
        **values,
    }


class OpenFoodFacts:
    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def lookup(self, barcode: str) -> dict | None:
        try:
            response = await self._client.get(
                API.format(barcode=barcode), params={"fields": FIELDS}
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.info("Open Food Facts недоступен: %s", exc)
            return None

        product = parse_product(payload)
        if product:
            log.info("код %s опознан в Open Food Facts: %s", barcode, product["name"])
        return product
