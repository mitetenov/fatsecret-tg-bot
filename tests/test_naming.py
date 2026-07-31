"""Читаемость названия продукта.

Название уходит в FatSecret навсегда. Израильский хумус приезжает из Open Food Facts
как «חומוס לבנוני» — это и нечитаемо, и разворачивает всю строку карточки, потому что
иврит пишется справа налево.
"""

from fsbot.domain.naming import is_readable, pick_name, strip_bidi


def test_latin_and_cyrillic_are_readable():
    assert is_readable("Beans and Smoked Tuna")
    assert is_readable("Салат из тунца")
    assert is_readable("Coca-Cola 0.5")  # цифры и знаки нейтральны


def test_hebrew_arabic_georgian_are_not():
    assert not is_readable("חומוס לבנוני")
    assert not is_readable("حمص")
    assert not is_readable("შებოლილი ტუნა")


def test_mixed_script_is_not_readable():
    # «Hummus חומוס» прочитается наполовину, а строку развернёт целиком.
    assert not is_readable("Hummus חומוס")


def test_string_without_letters_is_not_readable():
    assert not is_readable("100 %")
    assert not is_readable("")


def test_pick_prefers_readable_over_first():
    assert pick_name([None, "חומוס לבנוני", "Lebanese Hummus"]) == "Lebanese Hummus"


def test_pick_falls_back_to_unreadable_when_nothing_else():
    # Нечитаемое название — не отказ: его ещё можно перевести.
    assert pick_name(["", None, "חומוס לבנוני"]) == "חומוס לבנוני"


def test_pick_returns_none_when_empty():
    assert pick_name([None, "", "   "]) is None


def test_bidi_controls_are_stripped():
    assert strip_bidi("‫Hummus‬") == "Hummus"
    assert is_readable("‫Hummus‬")
