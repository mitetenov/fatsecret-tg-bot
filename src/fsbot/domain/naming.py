"""Пригодность названия продукта для Дневника.

Название уходит в аккаунт FatSecret навсегда — парного удаления в API нет. Значит оно
должно быть читаемым: «חומוס לבנוני» из Open Food Facts не только нечитаемо, но и
разворачивает всю строку карточки, потому что иврит пишется справа налево.
"""

from __future__ import annotations

import unicodedata

# Письменности, которые владелец дневника прочитает. Всё остальное — от иврита и
# арабского до грузинского и китайского — требует перевода.
READABLE_SCRIPTS = ("LATIN", "CYRILLIC")

# Управляющие символы направления текста: даже без букв они переворачивают строку.
BIDI_CONTROLS = {
    "‎", "‏", "‪", "‫", "‬", "‭", "‮",
    "⁦", "⁧", "⁨", "⁩",
}


def strip_bidi(text: str) -> str:
    return "".join(ch for ch in text if ch not in BIDI_CONTROLS)


def is_readable(text: str) -> bool:
    """Написано ли название понятной владельцу письменностью.

    Цифры, знаки и пробелы нейтральны: «Coca-Cola 0.5» читаемо, «חומוס» — нет.
    Пустая строка читаемой не считается: её не на что заменить, но и брать нечего.
    """
    letters = [ch for ch in strip_bidi(text) if ch.isalpha()]
    if not letters:
        return False
    return all(
        any(script in unicodedata.name(ch, "") for script in READABLE_SCRIPTS)
        for ch in letters
    )


def pick_name(candidates: list[str | None]) -> str | None:
    """Первое читаемое название из списка, иначе первое непустое.

    Возврат нечитаемого — не отказ: его ещё можно перевести, а вот отсутствие названия
    означает, что продукт создавать не из чего.
    """
    cleaned = [strip_bidi(c).strip() for c in candidates if c and c.strip()]
    if not cleaned:
        return None
    for name in cleaned:
        if is_readable(name):
            return name
    return cleaned[0]
