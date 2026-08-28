"""Чтение штрих-кода с фото.

Раньше цифры читала vision-модель. Это неверный инструмент: она путает цифры, а
неправильный GTIN не находится в базе молча — человек видит «продукт не найден» и не
понимает, что бот просто прочитал код с ошибкой. Декодер даёт либо точный код, либо
честное «кода нет», и делает это локально, без сети, лимитов и оплаты.

Разбор кандидатов вынесен в чистую функцию: она и тестируется.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# GTIN-8, GTIN-12 (UPC-A), GTIN-13 (EAN-13), GTIN-14. Всё остальное — не код товара:
# QR с ссылкой, номер партии, случайный текст.
GTIN_LENGTHS = {8, 12, 13, 14}

PRODUCT_SYMBOLOGIES = {"EAN13", "EAN8", "UPCA", "UPCE"}
NON_FOOD_SYMBOLOGIES = {"ISBN10", "ISBN13"}


def valid_gtin(value: str) -> bool:
    """Соответствует ли строка GTIN поддерживаемой длины и контрольной цифре."""
    if not value.isdigit() or len(value) not in GTIN_LENGTHS:
        return False
    data, check = value[:-1], int(value[-1])
    total = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(data))
    )
    return (10 - total % 10) % 10 == check


def expand_upce(value: str) -> str | None:
    """Развернуть восьмизначный UPC-E в двенадцатизначный UPC-A."""
    if len(value) != 8 or not value.isdigit():
        return None
    number_system, body, check = value[0], value[1:7], value[7]
    if number_system not in {"0", "1"}:
        return None

    tail = body[-1]
    if tail in "012":
        data = number_system + body[:2] + tail + "0000" + body[2:5]
    elif tail == "3":
        data = number_system + body[:3] + "00000" + body[3:5]
    elif tail == "4":
        data = number_system + body[:4] + "00000" + body[4]
    else:
        data = number_system + body[:5] + "0000" + tail

    upca = data + check
    return upca if valid_gtin(upca) else None


def fatsecret_gtin13(value: str, symbology: str | None = None) -> str | None:
    """GTIN-13 для FatSecret или None, если код неверен/не поддерживается API."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if (symbology or "").upper() == "UPCE":
        digits = expand_upce(digits) or ""
    elif len(digits) == 8 and not valid_gtin(digits):
        # При ручном вводе символика неизвестна: EAN-8 проверяем первым, UPC-E вторым.
        digits = expand_upce(digits) or digits
    if not valid_gtin(digits) or len(digits) == 14:
        return None
    return digits.zfill(13)


def plausible(candidates: list[tuple[str, str]]) -> str | None:
    """Выбрать товарный код из того, что декодер нашёл на картинке.

    На фото упаковки часто несколько кодов: сам GTIN, QR со ссылкой на сайт, код
    партии. Берём первый, похожий на товарный, и предпочитаем товарные символогии.
    """
    best: str | None = None
    for value, symbology in candidates:
        digits = "".join(ch for ch in value if ch.isdigit())
        kind = symbology.upper()
        if kind in NON_FOOD_SYMBOLOGIES:
            continue
        if kind == "UPCE":
            digits = expand_upce(digits) or ""
        if not valid_gtin(digits):
            continue
        if kind in PRODUCT_SYMBOLOGIES:
            return digits
        best = best or digits
    return best


def _teach_ctypes_to_find_zbar() -> None:
    """На musl нет ldconfig, поэтому ctypes.util.find_library('zbar') всегда пуст,
    и pyzbar сдаётся, хотя сама библиотека лежит рядом и грузится по soname."""
    import ctypes.util

    original = ctypes.util.find_library

    def find_library(name: str):
        if name == "zbar":
            return original(name) or "libzbar.so"
        return original(name)

    ctypes.util.find_library = find_library


def available() -> bool:
    """Загружается ли декодер здесь и сейчас.

    Отвечает на вопрос фактом, а не убеждением: библиотека может отсутствовать в
    образе или не находиться загрузчиком, и знать об этом надо при старте, а не когда
    человек пришлёт первое фото.
    """
    try:
        _teach_ctypes_to_find_zbar()
        import pyzbar.pyzbar  # noqa: F401
    except Exception:
        return False
    return True


def decode(image: bytes) -> str | None:
    """Точный код с фото или None. Отсутствие декодера не должно ронять бота."""
    try:
        from io import BytesIO

        _teach_ctypes_to_find_zbar()
        from PIL import Image
        from pyzbar.pyzbar import decode as zbar_decode
    except Exception as exc:  # pragma: no cover — зависит от окружения, не от логики
        log.warning("декодер штрих-кодов недоступен (%s), фото уйдёт в LLM", exc)
        return None

    try:
        found = zbar_decode(Image.open(BytesIO(image)))
    except Exception as exc:
        log.info("не удалось декодировать изображение: %s", exc)
        return None

    code = plausible([(item.data.decode("utf-8", "ignore"), item.type) for item in found])
    if code:
        log.info("штрих-код распознан локально: %s", code)
    return code
