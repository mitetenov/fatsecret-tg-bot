"""Асинхронный клиент FatSecret на OAuth 1.0 (см. ADR 0003).

Двуногие вызовы — поиск и карточка продукта; трёхногие — Дневник конкретного
пользователя. Записи создаются legacy-методом `food_entry.create`, потому что базовый
путь нового REST на нашем аккаунте пока не подтверждён.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from fsbot.domain.barcodes import fatsecret_gtin13
from fsbot.domain.daybounds import Meal, to_fatsecret_date
from fsbot.fatsecret.oauth1 import signed_params

API = "https://platform.fatsecret.com/rest/server.api"
REQUEST_TOKEN_URL = "https://authentication.fatsecret.com/oauth/request_token"
AUTHORIZE_URL = "https://authentication.fatsecret.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://authentication.fatsecret.com/oauth/access_token"

# Коды FatSecret, означающие «доступ пользователя больше не действителен».
INVALID_TOKEN_CODES = {4, 9, 14}
# Метод недоступен на тарифе: на Basic так отвечали штрих-код и создание продуктов.
FORBIDDEN_CODES = {12, 13, 21}


class FatSecretError(Exception):
    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message

    @property
    def token_invalid(self) -> bool:
        return self.code in INVALID_TOKEN_CODES

    @property
    def not_available_on_tier(self) -> bool:
        return self.code in FORBIDDEN_CODES


@dataclass(frozen=True, slots=True)
class FoodSummary:
    food_id: str
    name: str
    brand: str | None
    description: str
    details: dict | None = None

    @property
    def title(self) -> str:
        return f"{self.brand} {self.name}" if self.brand else self.name


class FatSecretClient:
    def __init__(
        self, consumer_key: str, consumer_secret: str, timeout: float = 20.0
    ) -> None:
        self._key = consumer_key
        self._secret = consumer_secret
        self._client = httpx.AsyncClient(timeout=timeout)
        self._food_cache: dict[str, dict] = {}

    async def close(self) -> None:
        await self._client.aclose()

    # --- низкий уровень ---------------------------------------------------

    async def _call(
        self,
        api_method: str,
        token: str | None = None,
        token_secret: str = "",
        **params: object,
    ) -> dict:
        # GET: legacy server.api проверяет подпись по query string, не по телу POST.
        signed = signed_params(
            "GET",
            API,
            {"method": api_method, "format": "json", **params},
            self._key,
            self._secret,
            token=token,
            token_secret=token_secret,
        )
        response = await self._client.get(API, params=signed)
        payload = response.json()
        if isinstance(payload, dict) and "error" in payload:
            error = payload["error"]
            raise FatSecretError(error.get("code"), error.get("message", ""))
        return payload

    # --- привязка аккаунта ------------------------------------------------

    async def request_token(self) -> tuple[str, str, str]:
        """Шаг 1 и 2: неавторизованный токен + ссылка, где пользователь даст доступ."""
        signed = signed_params(
            "POST", REQUEST_TOKEN_URL, {}, self._key, self._secret, callback="oob"
        )
        response = await self._client.post(REQUEST_TOKEN_URL, data=signed)
        if response.status_code >= 400:
            raise FatSecretError(None, f"request_token: HTTP {response.status_code}")
        parsed = dict(item.split("=", 1) for item in response.text.split("&"))
        token, secret = parsed["oauth_token"], parsed["oauth_token_secret"]
        return token, secret, f"{AUTHORIZE_URL}?oauth_token={token}"

    async def access_token(self, token: str, token_secret: str, pin: str) -> tuple[str, str]:
        signed = signed_params(
            "POST",
            ACCESS_TOKEN_URL,
            {},
            self._key,
            self._secret,
            token=token,
            token_secret=token_secret,
            verifier=pin,
        )
        response = await self._client.post(ACCESS_TOKEN_URL, data=signed)
        if response.status_code >= 400:
            raise FatSecretError(None, "PIN не принят — проверь код и попробуй снова")
        parsed = dict(item.split("=", 1) for item in response.text.split("&"))
        return parsed["oauth_token"], parsed["oauth_token_secret"]

    # --- продукты ---------------------------------------------------------

    async def search_foods(self, query: str, max_results: int = 5) -> list[FoodSummary]:
        payload = await self._call(
            "foods.search.v5",
            search_expression=query,
            max_results=max_results,
            flag_default_serving=True,
        )
        search = payload.get("foods_search") or {}
        raw = ((search.get("results") or {}).get("food")
               or (payload.get("foods") or {}).get("food") or [])
        if isinstance(raw, dict):
            raw = [raw]
        return [
            FoodSummary(
                food_id=str(item["food_id"]),
                name=item.get("food_name", ""),
                brand=item.get("brand_name"),
                description=item.get("food_description", ""),
                details=item if item.get("servings") else None,
            )
            for item in raw
        ]

    async def get_food(self, food_id: str) -> dict:
        cached = getattr(self, "_food_cache", {}).pop(str(food_id), None)
        if cached:
            return cached
        payload = await self._call(
            "food.get.v5", food_id=food_id, flag_default_serving=True
        )
        return payload["food"]

    async def autocomplete(self, expression: str) -> list[str]:
        """Подсказки поиска. Спасают, когда LLM дала неточный английский запрос."""
        try:
            payload = await self._call(
                "foods.autocomplete.v2", expression=expression, max_results=4
            )
        except FatSecretError:
            return []
        raw = (payload.get("suggestions") or {}).get("suggestion") or []
        return [raw] if isinstance(raw, str) else list(raw)

    async def food_id_by_barcode(self, barcode: str) -> str | None:
        """GTIN → food_id. Пустой ответ означает «в базе нет», а не ошибку."""
        normalized = fatsecret_gtin13(barcode)
        if normalized is None:
            return None
        try:
            payload = await self._call(
                "food.find_id_for_barcode.v2",
                barcode=normalized,
                flag_default_serving=True,
            )
        except FatSecretError as exc:
            if exc.code == 211:
                return None
            raise
        food = payload.get("food") or {}
        food_id = food.get("food_id") or (payload.get("food_id") or {}).get("value")
        if food_id and food.get("servings"):
            getattr(self, "_food_cache", {}).update({str(food_id): food})
        return str(food_id) if food_id and str(food_id) != "0" else None

    async def create_food(
        self,
        token: str,
        token_secret: str,
        *,
        name: str,
        brand: str,
        kcal: float,
        protein: float,
        fat: float,
        carbs: float,
        basis_unit: str = "g",
    ) -> str:
        """Создать Свой продукт. Необратимо: парного метода удаления в API нет."""
        payload = await self._call(
            "food.create.v2",
            token=token,
            token_secret=token_secret,
            food_name=name[:60],
            brand_type="manufacturer",
            brand_name=brand[:60],
            serving_size=f"100 {basis_unit}",
            serving_amount=100,
            serving_amount_unit=basis_unit,
            calories=kcal,
            protein=protein,
            fat=fat,
            carbohydrate=carbs,
        )
        return str((payload.get("food_id") or {}).get("value", ""))

    # --- дневник ----------------------------------------------------------

    async def create_entry(
        self,
        token: str,
        token_secret: str,
        *,
        food_id: str,
        serving_id: str,
        units: float,
        entry_name: str,
        meal: Meal,
        day: date,
    ) -> str:
        payload = await self._call(
            "food_entry.create",
            token=token,
            token_secret=token_secret,
            food_id=food_id,
            serving_id=serving_id,
            number_of_units=f"{units:.4f}",
            food_entry_name=entry_name[:60],
            meal=meal.value,
            date=to_fatsecret_date(day),
        )
        return str((payload.get("food_entry_id") or {}).get("value", ""))

    async def delete_entry(self, token: str, token_secret: str, entry_id: str) -> None:
        await self._call(
            "food_entry.delete",
            token=token,
            token_secret=token_secret,
            food_entry_id=entry_id,
        )

    async def profile_status(self, token: str, token_secret: str) -> dict:
        return await self._call("profile.get", token=token, token_secret=token_secret)

    async def recently_eaten(self, token: str, token_secret: str) -> list[FoodSummary]:
        """Сигнал для ранжирования Кандидатов (решение 9). На части тарифов закрыт."""
        try:
            payload = await self._call(
                "foods.get_recently_eaten.v2",
                token=token,
                token_secret=token_secret,
            )
        except FatSecretError:
            return []
        raw = (payload.get("foods") or {}).get("food") or []
        if isinstance(raw, dict):
            raw = [raw]
        return [
            FoodSummary(
                food_id=str(item["food_id"]),
                name=item.get("food_name", ""),
                brand=item.get("brand_name"),
                description=item.get("food_description", ""),
            )
            for item in raw
        ]
