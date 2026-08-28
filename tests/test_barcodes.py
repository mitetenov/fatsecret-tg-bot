"""Выбор товарного кода из того, что декодер нашёл на фото.

На упаковке обычно несколько кодов: GTIN, QR со ссылкой, номер партии. Ошибиться здесь
дорого: неверный код не находится в базе молча, и человек видит «продукт не найден»,
не понимая, что бот прочитал не тот код.
"""

import pytest

from fsbot.domain.barcodes import expand_upce, fatsecret_gtin13, plausible, valid_gtin


def test_ean13_is_taken():
    assert plausible([("5449000000996", "EAN13")]) == "5449000000996"


def test_product_symbology_wins_over_other_codes():
    # QR со ссылкой на сайт производителя стоит первым, но товарный код — второй.
    found = [("https://example.com/promo", "QRCODE"), ("4820000000000", "EAN13")]
    assert plausible(found) == "4820000000000"


def test_qr_with_digits_of_gtin_length_is_used_only_as_fallback():
    # Тринадцать цифр в QR могут оказаться номером партии, но если товарного кода на
    # фото нет — лучше попробовать их, чем сдаться.
    assert plausible([("4006381333931", "QRCODE")]) == "4006381333931"
    assert plausible([("4006381333931", "QRCODE"), ("5449000000996", "EAN13")]) == "5449000000996"


def test_non_gtin_lengths_are_ignored():
    assert plausible([("12345", "CODE128"), ("123456789012345678", "CODE128")]) is None


def test_separators_inside_the_code_are_stripped():
    assert plausible([("4 820000 000000", "EAN13")]) == "4820000000000"


def test_nothing_found():
    assert plausible([]) is None


def test_fatsecret_normalizes_supported_gtin_lengths():
    assert fatsecret_gtin13("96385074") == "0000096385074"
    assert fatsecret_gtin13("036000291452") == "0036000291452"
    assert fatsecret_gtin13("4006381333931") == "4006381333931"


def test_upce_is_expanded_before_fatsecret_lookup():
    assert fatsecret_gtin13("04252614", "UPCE") == "0042100005264"


def test_invalid_checksum_and_gtin14_are_not_sent_to_fatsecret():
    assert not valid_gtin("4006381333932")
    assert fatsecret_gtin13("4006381333932") is None
    assert valid_gtin("10012345678902")
    assert fatsecret_gtin13("10012345678902") is None


def test_isbn_is_not_selected_as_food_barcode():
    assert plausible([("9780306406157", "ISBN13")]) is None


def test_unknown_symbology_needs_a_valid_checksum():
    assert plausible([("1234567890123", "QRCODE")]) is None
    assert plausible([("4006381333931", "QRCODE")]) == "4006381333931"


def _check_digit(data: str) -> str:
    total = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(data))
    )
    return str((10 - total % 10) % 10)


@pytest.mark.parametrize(
    ("body", "upca_data"),
    [
        ("421000", "04200000100"),  # последняя цифра 0/1/2
        ("123453", "01230000045"),  # последняя цифра 3
        ("123454", "01234000005"),  # последняя цифра 4
        ("123455", "01234500005"),  # последняя цифра 5..9
    ],
)
def test_every_upce_compression_rule_expands_to_expected_upca(body, upca_data):
    expected = upca_data + _check_digit(upca_data)
    upce = "0" + body + expected[-1]

    assert expand_upce(upce) == expected


@pytest.mark.parametrize("value", ["123", "A4210005", "24210005"])
def test_invalid_upce_shape_or_number_system_is_rejected(value):
    assert expand_upce(value) is None
