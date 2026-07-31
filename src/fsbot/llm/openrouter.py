"""Клиент OpenRouter: разбор Реплик и фото с фоллбэком по списку моделей."""

from __future__ import annotations

import base64
import logging

import httpx

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
Если количество не названо — оцени типичную порцию и всё равно укажи число.
Штучные продукты переводи в граммы, если знаешь типичный вес: «2 яйца» → 110 g.
Для славянских продуктов, у которых нет точного английского аналога, давай
транслитерацию: «творог» → "tvorog", «кефир» → "kefir", «ряженка» → "ryazhenka".
В базе такие продукты есть именно под транслитерацией, а перевод по смыслу
(«cottage cheese») даёт другой продукт с другими БЖУ.
Отвечай только JSON, без пояснений."""

PLATE_PROMPT = """На фото еда. Определи блюда и оцени вес каждого в граммах.
Верни JSON: {"kind":"plate","items":[...]}, где на каждое блюдо — объект с полями
query_en (короткий английский поисковый запрос для базы продуктов США), name_ru
(название по-русски), amount (граммы, число), unit ("g").
Оценивай вес по видимому объёму; лучше приблизительно, чем пропустить блюдо.
Отвечай только JSON, без пояснений."""

LABEL_PROMPT = """На фото упаковка продукта. Прочитай бренд, название и таблицу
пищевой ценности.
Верни JSON: {"kind":"label","items":[{...}]} с одним объектом:
query_en (бренд и название по-английски для поиска в базе продуктов),
name_ru (как написано на упаковке), brand (бренд как на упаковке),
amount (100), unit ("g"),
kcal_100g, protein_100g, fat_100g, carbs_100g — числа из таблицы в пересчёте на 100 г.
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
"kcal_100g": число, "protein_100g": число, "fat_100g": число, "carbs_100g": число,
"source": "домен, откуда взяты данные"}}
Если данных нет — {{"found": false}}. Не выдумывай числа: без источника ставь false."""


# Столько попыток веб-поиска: при вероятности успеха около половины три попытки дают
# примерно 90%, а стоят вместе меньше одной десятой цента.
BARCODE_LOOKUP_ATTEMPTS = 3


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

    async def _complete(self, models: list[str], messages: list[dict], schema: bool) -> str:
        """Пробуем модели по списку: бесплатные то заняты, то не умеют json_schema."""
        errors: list[str] = []
        statuses: list[int] = []
        for model in models:
            body: dict = {"model": model, "messages": messages}
            if schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": RECOGNITION_SCHEMA,
                }
            try:
                response = await self._client.post(
                    ENDPOINT, headers=self._headers, json=body
                )
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {exc}")
                statuses.append(0)
                continue

            if response.status_code != 200:
                errors.append(f"{model}: HTTP {response.status_code} {response.text[:160]}")
                statuses.append(response.status_code)
                continue

            payload = response.json()
            choices = payload.get("choices") or []
            if not choices:
                errors.append(f"{model}: пустой ответ {payload.get('error')}")
                statuses.append(0)
                continue
            return choices[0]["message"]["content"] or ""

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
        log.info("код %s опознан в вебе: %s (%s)", barcode, data.get("name"), data.get("source"))
        return data

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
        raw = await self._complete(models, messages, schema=True)
        try:
            return parse_recognition(raw)
        except ParseError as first:
            log.warning("ответ модели не разобран (%s), повторяю без схемы", first)

        retry = [*messages, {"role": "user", "content": "Верни только валидный JSON."}]
        raw = await self._complete(models, retry, schema=False)
        # Разбор повтора тоже может не удаться — например, если человек прислал голое
        # число или модель ничего не распознала. Это нормальный исход, а не авария:
        # наружу уходит LLMError, который хендлеры умеют превращать в понятный ответ.
        try:
            return parse_recognition(raw)
        except ParseError as second:
            raise LLMError(f"разбор не удался дважды: {second}") from second
