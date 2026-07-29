"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Application configuration."""

    # Telegram
    bot_token: str = field(default_factory=lambda: os.environ.get("BOT_TOKEN", ""))

    # FatSecret API
    fatsecret_client_id: str = field(
        default_factory=lambda: os.environ.get("FATSECRET_CLIENT_ID", "")
    )
    fatsecret_client_secret: str = field(
        default_factory=lambda: os.environ.get("FATSECRET_CLIENT_SECRET", "")
    )
    fatsecret_region: str = field(
        default_factory=lambda: os.environ.get("FATSECRET_REGION", "US")
    )
    fatsecret_language: str = field(
        default_factory=lambda: os.environ.get("FATSECRET_LANGUAGE", "en")
    )

    # AI / LLM
    ai_api_key: str = field(default_factory=lambda: os.environ.get("AI_API_KEY", ""))
    ai_provider: str = field(
        default_factory=lambda: os.environ.get("AI_PROVIDER", "openai")
    )
    ai_model: str = field(
        default_factory=lambda: os.environ.get("AI_MODEL", "gpt-4o")
    )

    # Gemini Vision (can be set independently of AI_API_KEY)
    gemini_api_key: str = field(
        default_factory=lambda: os.environ.get("GEMINI_API_KEY", "")
    )

    # Database
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", f"sqlite:///{Path(__file__).parent / 'data' / 'bot.db'}"
        )
    )

    # General
    log_level: str = field(
        default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO")
    )
    debug: bool = field(
        default_factory=lambda: os.environ.get("DEBUG", "false").lower() == "true"
    )

    def validate(self) -> list[str]:
        """Check required configuration; returns a list of missing keys."""
        missing: list[str] = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        return missing


# Singleton
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
