"""Клиент OpenRouter: разбор Реплик и фото с фоллбэком по списку моделей."""

from __future__ import annotations

import base64
import logging

import httpx

from fsbot.domain import nutrition as nutrition_rules
from fsbot.llm.parsing import (
    RECOGNITION_SCHEMA,
    ParseError,
    Recognition,
    extract_json,
    parse_recognition,
)

log = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

TEXT_PROMPT = """Ты разбираешь короткие реплики о съеденной еде на русском языке.
Верни JSON: {"kind":"text","items":[...]}.
На каждый продукт — объект: query_en (короткий английский поисковый запрос для базы
продуктов США, без брендов, если бренд не назван), name_ru (как сказал человек),
amount (число), unit ("g", "ml" или "piece"), meal (breakfast/lunch/dinner/other,
только если человек назвал приём пищи явно), date_hint ("yesterday", только если
человек сказал «вчера»).
confidence — число 0..1: насколько уверенно определены продукт и количество.
Если количество не названо — оцени типичную порцию и снизь confidence.
Штучные продукты переводи в граммы, если знаешь типичный вес: «2 яйца» → 110 g.
Для славянских продуктов, у которых нет точного английского аналога, давай
транслитерацию: «творог» → "tvorog", «кефир» → "kefir", «ряженка» → "ryazhenka".
В базе такие продукты есть именно под транслитерацией, а перевод по смыслу
(«cottage cheese») даёт другой продукт с другими БЖУ.
Отвечай только JSON, без пояснений."""

PLATE_PROMPT = """На фото еда или напиток. Определи блюда и оцени вес каждого в граммах.
Если это упаковка, банка или бутылка с напечатанным объёмом либо массой нетто
(«450 ml», «160g», «0,5 л») — бери это число как amount, а не оценивай на глаз:
напечатанное всегда точнее.
Верни JSON: {"kind":"plate","items":[...]}, где на каждое блюдо — объект с полями
query_en (короткий английский поисковый запрос для базы продуктов США), name_ru
(название по-русски), amount (граммы, число), unit ("g").
confidence — число 0..1; снижай его при неоднозначном блюде или приблизительном весе.
Оценивай вес по видимому объёму; лучше приблизительно, чем пропустить блюдо.
Отвечай только JSON, без пояснений."""

LABEL_PROMPT = """На фото упаковка продукта. Прочитай бренд, название и таблицу
пищевой ценности.
Верни JSON: {"kind":"label","items":[{...}]} с одним объектом:
query_en (бренд и название по-английски для поиска в базе продуктов),
amount — напечатанные на упаковке масса нетто или объём («160g» → 160, «450 ml» → 450);
только если их не видно, ставь 100.
name_ru — название по-русски или по-английски. На многих упаковках есть английская
строка рядом с местной («TUNA SALAD WITH BEANS» под грузинским текстом) — бери её.
Если её нет, переведи название на русский. Не оставляй название грузинским, греческим
или армянским: оно попадёт в дневник питания навсегда и станет нечитаемым.
brand (бренд как на упаковке, латиницей), confidence — число 0..1,
unit ("g" для веса, "ml" для объёма),
nutrition_basis — "100g" или "100ml" ровно как на этикетке;
kcal_per_100, protein_per_100, fat_per_100, carbs_per_100 — числа на эту базу.
Если таблица дана на порцию, а не на 100 г, пересчитай сам.
Указывай КБЖУ только если уверенно прочитал все четыре числа: неполные данные хуже
отсутствующих, по ним будет создан неверный продукт.
Отвечай только JSON, без пояснений."""

CLASSIFY_PROMPT = """На фото еда или упаковка продукта?
Ответь одним словом: plate — если приготовленная еда, напиток или блюдо;
label — если упаковка, этикетка, таблица пищевой ценности или штрих-код."""


class LLMError(Exception):
    """Ни одна модель из цепочки не ответила.

    Причины различаются по смыслу: перегрузка провайдера (429) лечится повтором через
    минуту, всё остальное — нет. Бот обязан говорить об этом по-разному, иначе человек
    решит, что бот не понял еду, и начнёт переписывать текст вместо повтора.
    """

    def __init__(self, message: str, statuses: list[int] | None = None) -> None:
        super().__init__(message)
        self.statuses = statuses or []

    @property
    def rate_limited(self) -> bool:
        return bool(self.statuses) and all(status == 429 for status in self.statuses)


BARCODE_PROMPT = """Найди товар со штрих-кодом {barcode}.
Название дай по-русски или по-английски — оно попадёт в дневник питания навсегда;
не оставляй его на языке сайта-источника.
Верни только JSON: {{"found": true/false, "name": "название", "brand": "бренд",
"nutrition_basis": "100g" или "100ml", "kcal_per_100": число,
"protein_per_100": число, "fat_per_100": число, "carbs_per_100": число,
"confidence": число 0..1,
"source": "домен, откуда взяты данные"}}
Если данных нет — {{"found": false}}. Не выдумывай числа: без источника ставь false."""


# Столько попыток веб-поиска: при вероятности успеха около половины три попытки дают
# примерно 90%, а стоят вместе меньше одной десятой цента.
BARCODE_LOOKUP_ATTEMPTS = 3

TRANSLATE_PROMPT = """Название продукта из базы: {name}
Бренд: {brand}
Верни только JSON: {{"name": "...", "brand": "..."}} — по-русски или по-английски.
Бренд пиши латиницей. Ничего не добавляй от себя: это перевод, а не описание."""


class OpenRouter:
    def __init__(
        self,
        api_key: str,
        text_models: list[str],
        vision_models: list[str],
        timeout: float = 90.0,
    ) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Title": "fsbot",
        }
        self._text_models = text_models
        self._vision_models = vision_models
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def _complete_one(
        self, model: str, messages: list[dict], schema: bool
    ) -> str:
        """Один запрос к конкретной модели с проверкой envelope OpenRouter."""
        body: dict = {"model": model, "messages": messages}
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": RECOGNITION_SCHEMA,
            }
            body["provider"] = {"require_parameters": True}
        try:
            response = await self._client.post(ENDPOINT, headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"{model}: {exc}", [0]) from exc

        if response.status_code != 200:
            raise LLMError(
                f"{model}: HTTP {response.status_code} {response.text[:160]}",
                [response.status_code],
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMError(f"{model}: ответ OpenRouter не JSON", [0]) from exc
        if not isinstance(payload, dict):
            raise LLMError(f"{model}: неожиданный ответ OpenRouter", [0])

        choices = payload.get("choices") or []
        if not isinstance(choices, list) or not choices:
            raise LLMError(f"{model}: пустой ответ {payload.get('error')}", [0])
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMError(f"{model}: ответ не содержит текст", [0])
        return content

    async def _complete(self, models: list[str], messages: list[dict], schema: bool) -> str:
        """Пробуем модели по списку: бесплатные то заняты, то не умеют json_schema."""
        errors: list[str] = []
        statuses: list[int] = []
        for model in models:
            try:
                return await self._complete_one(model, messages, schema)
            except LLMError as exc:
                errors.append(str(exc))
                statuses.extend(exc.statuses)
        raise LLMError("; ".join(errors) or "нет доступных моделей", statuses)

    async def lookup_barcode(self, barcode: str) -> dict | None:
        """Что известно о товаре в вебе. Только с grounding — без него модель выдумывает.

        Проверено на 5201340026780: с поиском — TRATA, салат из тунца, 186 ккал (что
        совпало с этикеткой), без поиска — «сыр фета Dodoni, 264 ккал» и выдуманный
        источник. Поэтому суффикс :online обязателен, а не желателен.
        """
        online = [f"{m}:online" for m in self._text_models if not m.endswith(":online")]
        if not online:
            return None

        # Веб-поиск недетерминирован: на один и тот же код модель отвечает то товаром,
        # то «не нашёл» — замерено, примерно поровну. Поэтому «не нашёл» с первой
        # попытки ничего не значит, и мы пробуем ещё; запрос стоит доли цента.
        data: dict = {}
        message = [{"role": "user", "content": BARCODE_PROMPT.format(barcode=barcode)}]
        for attempt in range(BARCODE_LOOKUP_ATTEMPTS):
            try:
                data = extract_json(await self._complete(online, message, schema=False))
            except (LLMError, ParseError) as exc:
                log.info("поиск товара по коду %s не удался: %s", barcode, exc)
                return None
            if data.get("found"):
                break
            log.info("попытка %d: товар по коду %s не найден", attempt + 1, barcode)

        if not data.get("found"):
            return None
        product = _normalize_lookup_product(data)
        if product:
            log.info(
                "код %s опознан в вебе: %s (%s)",
                barcode,
                product.get("name"),
                product.get("source"),
            )
        return product

    async def translate_product(self, name: str, brand: str) -> tuple[str, str] | None:
        """Перевести название и бренд в читаемый вид.

        Открытая база многоязычна: израильский хумус приезжает как «חומוס לבנוני», а
        это и нечитаемо, и разворачивает строку карточки. Числа при этом верные, так
        что выбрасывать находку из-за письменности неправильно — надо перевести.
        """
        try:
            raw = await self._complete(
                self._text_models,
                [
                    {
                        "role": "user",
                        "content": TRANSLATE_PROMPT.format(name=name, brand=brand or "—"),
                    }
                ],
                schema=False,
            )
            data = extract_json(raw)
        except (LLMError, ParseError) as exc:
            log.info("не удалось перевести название %r: %s", name, exc)
            return None

        translated = str(data.get("name") or "").strip()
        if not translated:
            return None
        return translated, str(data.get("brand") or brand or "").strip()

    async def recognize_text(self, text: str) -> Recognition:
        messages = [
            {"role": "system", "content": TEXT_PROMPT},
            {"role": "user", "content": text},
        ]
        return await self._recognize(self._text_models, messages)

    async def recognize_photo(
        self, image: bytes, caption: str | None = None, barcode: str | None = None
    ) -> Recognition:
        data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
        # Штрих-код на фото есть, но FatSecret его не знает — значит это упаковка,
        # и классифицировать нечего: сразу читаем этикетку.
        kind = "label" if barcode else await self._classify(data_url)
        prompt = LABEL_PROMPT if kind == "label" else PLATE_PROMPT
        content: list[dict] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        if barcode:
            content.append(
                {
                    "type": "text",
                    "text": f"На упаковке штрих-код {barcode}. Если знаешь этот товар — "
                    "используй знание для названия и бренда, но КБЖУ бери только с фото.",
                }
            )
        if caption:
            content.append({"type": "text", "text": f"Подпись пользователя: {caption}"})
        return await self._recognize(
            self._vision_models, [{"role": "user", "content": content}]
        )

    async def _classify(self, data_url: str) -> str:
        try:
            answer = await self._complete(
                self._vision_models,
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": CLASSIFY_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                schema=False,
            )
        except LLMError:
            return "plate"
        return "label" if "label" in answer.lower() else "plate"

    async def _recognize(self, models: list[str], messages: list[dict]) -> Recognition:
        errors: list[str] = []
        statuses: list[int] = []
        for model in models:
            try:
                raw = await self._complete_one(model, messages, schema=True)
                return parse_recognition(raw)
            except (LLMError, ParseError) as first:
                log.warning("ответ модели %s не разобран (%s), повторяю без схемы", model, first)
                errors.append(f"{model}: {first}")
                statuses.extend(first.statuses if isinstance(first, LLMError) else [0])

            retry = [
                *messages,
                {"role": "user", "content": "Верни только валидный JSON."},
            ]
            try:
                raw = await self._complete_one(model, retry, schema=False)
                return parse_recognition(raw)
            except (LLMError, ParseError) as second:
                errors.append(f"{model} repair: {second}")
                statuses.extend(second.statuses if isinstance(second, LLMError) else [0])
                log.warning("модель %s не прошла repair, пробую следующую", model)

        raise LLMError("; ".join(errors) or "нет доступных моделей", statuses)


def _normalize_lookup_product(data: dict) -> dict | None:
    name = str(data.get("name") or "").strip()
    source = str(data.get("source") or "").strip()
    if not name or not source:
        return None
    basis = str(data.get("nutrition_basis") or "100g").lower().replace(" ", "")
    basis_unit = "ml" if basis in {"100ml", "ml"} else "g"
    values: dict[str, float] = {}
    for field in ("kcal", "protein", "fat", "carbs"):
        raw = data.get(f"{field}_per_100")
        if raw is None:
            raw = data.get(f"{field}_100{basis_unit}")
        try:
            values[field] = float(raw)
        except (TypeError, ValueError):
            return None
    if not nutrition_rules.plausible(**values):
        return None
    try:
        confidence = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    return {
        **data,
        "name": name,
        "source": source,
        "nutrition_basis": basis_unit,
        "confidence": round(max(0.0, min(0.75, confidence)), 2),
        "kcal_per_100": values["kcal"],
        "protein_per_100": values["protein"],
        "fat_per_100": values["fat"],
        "carbs_per_100": values["carbs"],
    }
