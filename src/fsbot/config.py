"""Конфигурация из окружения. Секреты только здесь и только из env — не в коде."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TZ = "Asia/Tbilisi"

# openrouter/free — не модель, а роутер: он сам выбирает бесплатную модель под запрос.
# Это снимает заботу о том, что состав бесплатных моделей меняется, но взамен модель
# на каждом запросе может быть разной — в том числе без поддержки json_schema.
# Разбор ответа поэтому обязан оставаться устойчивым (см. llm/parsing.py).
# Пиновать конкретные модели можно через OPENROUTER_TEXT_MODELS / _VISION_MODELS.
# Основная — платная gemini-3.5-flash-lite (замерено: ~$0.0005 за фото, ~$0.0002 за
# реплику), запасная — бесплатный роутер. Порядок именно такой: бесплатные модели то
# заняты, то игнорируют json_schema, и цена вопроса тут — центы в месяц.
DEFAULT_TEXT_MODELS = "google/gemini-3.5-flash-lite,openrouter/free"
DEFAULT_VISION_MODELS = "google/gemini-3.5-flash-lite,openrouter/free"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass(frozen=True, slots=True)
class Config:
    telegram_token: str
    owner_id: int
    consumer_key: str
    consumer_secret: str
    openrouter_key: str
    text_models: list[str] = field(default_factory=list)
    vision_models: list[str] = field(default_factory=list)
    state_dir: Path = Path("/data")
    default_tz: str = DEFAULT_TZ

    @property
    def db_path(self) -> Path:
        return self.state_dir / "fsbot.sqlite3"

    @classmethod
    def from_env(cls, dotenv: Path | None = None) -> Config:
        if dotenv:
            _load_dotenv(dotenv)

        missing = [
            name
            for name in (
                "TELEGRAM_BOT_TOKEN",
                "BOT_OWNER_ID",
                "FATSECRET_CONSUMER_KEY",
                "FATSECRET_CONSUMER_SECRET",
                "OPENROUTER_API_KEY",
            )
            if not os.environ.get(name)
        ]
        if missing:
            raise SystemExit(
                "Не заданы переменные окружения: " + ", ".join(missing) +
                "\nЗаполни .env по образцу .env.example."
            )

        return cls(
            telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
            owner_id=int(os.environ["BOT_OWNER_ID"]),
            consumer_key=os.environ["FATSECRET_CONSUMER_KEY"],
            consumer_secret=os.environ["FATSECRET_CONSUMER_SECRET"],
            openrouter_key=os.environ["OPENROUTER_API_KEY"],
            text_models=_split(os.environ.get("OPENROUTER_TEXT_MODELS", DEFAULT_TEXT_MODELS)),
            vision_models=_split(
                os.environ.get("OPENROUTER_VISION_MODELS", DEFAULT_VISION_MODELS)
            ),
            state_dir=Path(os.environ.get("FSBOT_STATE_DIR", "/data")),
            default_tz=os.environ.get("FSBOT_DEFAULT_TZ", DEFAULT_TZ),
        )


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
