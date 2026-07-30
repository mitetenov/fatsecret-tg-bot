"""Различение причин отказа LLM.

Перегрузка провайдера (429) лечится повтором через минуту, всё остальное — нет.
Если бот путает эти случаи, человек переписывает текст вместо того, чтобы подождать.
"""

from fsbot.bot.handlers import _llm_failure_text
from fsbot.llm.openrouter import LLMError


def test_all_models_rate_limited():
    exc = LLMError("qwen: HTTP 429; free: HTTP 429", [429, 429])
    assert exc.rate_limited
    assert "перегружена" in _llm_failure_text(exc, "фото")


def test_mixed_failures_are_not_treated_as_rate_limit():
    # Одна модель занята, вторая ответила мусором — совет «подожди минуту» тут врёт.
    exc = LLMError("qwen: HTTP 429; free: HTTP 500", [429, 500])
    assert not exc.rate_limited
    assert "фото" in _llm_failure_text(exc, "фото")


def test_no_statuses_means_not_rate_limited():
    exc = LLMError("нет доступных моделей")
    assert not exc.rate_limited


def test_text_and_photo_get_different_advice():
    exc = LLMError("free: пустой ответ", [0])
    assert _llm_failure_text(exc, "фото") != _llm_failure_text(exc, "текст")
    assert "овсянка" in _llm_failure_text(exc, "текст")
