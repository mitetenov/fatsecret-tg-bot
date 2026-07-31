"""Обнаружение второго экземпляра бота на том же токене.

Два процесса на одном токене Telegram разводит по разным потребителям, и диалог
рвётся на середине: PIN уходит одному, часовой пояс — другому. Со стороны это
выглядит как «бот отвечает невпопад», а не как ошибка, поэтому признак ловится из
логов aiogram и гасит heartbeat.
"""

import logging

import pytest

from fsbot.bot.app import PollingConflict


@pytest.fixture(autouse=True)
def reset_flag():
    PollingConflict.detected = False
    yield
    PollingConflict.detected = False


def record(message: str) -> logging.LogRecord:
    return logging.LogRecord("aiogram.dispatcher", logging.ERROR, __file__, 1, message, (), None)


def test_conflict_is_detected():
    filt = PollingConflict()
    text = (
        "Failed to fetch updates - TelegramConflictError: Telegram server says - "
        "Conflict: terminated by other getUpdates request"
    )
    assert filt.filter(record(text)) is True  # запись не подавляется
    assert PollingConflict.detected


def test_ordinary_errors_do_not_trigger_it():
    filt = PollingConflict()
    filt.filter(record("Failed to fetch updates - TelegramNetworkError: timeout"))
    assert not PollingConflict.detected


def test_flag_stays_raised_until_restart():
    # Конфликт не «рассасывается»: пока второй экземпляр жив, апдейты продолжают
    # уходить к нему, поэтому один спокойный цикл опроса ничего не отменяет.
    filt = PollingConflict()
    filt.filter(record("Conflict: terminated by other getUpdates request"))
    filt.filter(record("Update id=1 is handled"))
    assert PollingConflict.detected
