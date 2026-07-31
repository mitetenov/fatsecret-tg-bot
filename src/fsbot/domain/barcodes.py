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

PRODUCT_SYMBOLOGIES = {"EAN13", "EAN8", "UPCA", "UPCE", "ISBN13", "ISBN10"}


def plausible(candidates: list[tuple[str, str]]) -> str | None:
    """Выбрать товарный код из того, что декодер нашёл на картинке.

    На фото упаковки часто несколько кодов: сам GTIN, QR со ссылкой на сайт, код
    партии. Берём первый, похожий на товарный, и предпочитаем товарные символогии.
    """
    best: str | None = None
    for value, symbology in candidates:
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) not in GTIN_LENGTHS:
            continue
        if symbology.upper() in PRODUCT_SYMBOLOGIES:
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
