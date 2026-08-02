"""Голое число после карточки — это правка количества, а не новая еда."""

from fsbot.bot.handlers import AMOUNT_ONLY


def matches(text: str) -> bool:
    return bool(AMOUNT_ONLY.match(text))


def test_plain_numbers_and_units_are_amounts():
    assert matches("450")
    assert matches("450 г")
    assert matches("360гр")
    assert matches("0,5")
    assert matches("12.5 ml")


def test_barcode_is_not_an_amount():
    # 13 цифр — это штрих-код, у него свой обработчик и свой смысл.
    assert not matches("7290106573598")


def test_text_with_food_is_not_an_amount():
    assert not matches("творог 200г")
    assert not matches("450 грамм печенья")
