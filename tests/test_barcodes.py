"""Выбор товарного кода из того, что декодер нашёл на фото.

На упаковке обычно несколько кодов: GTIN, QR со ссылкой, номер партии. Ошибиться здесь
дорого: неверный код не находится в базе молча, и человек видит «продукт не найден»,
не понимая, что бот прочитал не тот код.
"""

from fsbot.domain.barcodes import plausible


def test_ean13_is_taken():
    assert plausible([("5449000000996", "EAN13")]) == "5449000000996"


def test_product_symbology_wins_over_other_codes():
    # QR со ссылкой на сайт производителя стоит первым, но товарный код — второй.
    found = [("https://example.com/promo", "QRCODE"), ("4820000000000", "EAN13")]
    assert plausible(found) == "4820000000000"


def test_qr_with_digits_of_gtin_length_is_used_only_as_fallback():
    # Тринадцать цифр в QR могут оказаться номером партии, но если товарного кода на
    # фото нет — лучше попробовать их, чем сдаться.
    assert plausible([("1234567890123", "QRCODE")]) == "1234567890123"
    assert plausible([("1234567890123", "QRCODE"), ("5449000000996", "EAN13")]) == "5449000000996"


def test_non_gtin_lengths_are_ignored():
    assert plausible([("12345", "CODE128"), ("123456789012345678", "CODE128")]) is None


def test_separators_inside_the_code_are_stripped():
    assert plausible([("4 820000 000000", "EAN13")]) == "4820000000000"


def test_nothing_found():
    assert plausible([]) is None
