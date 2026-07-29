"""Tests for the bot entry point (bot.py) — handler registration."""

from unittest.mock import MagicMock, patch

import pytest


class TestBotApplication:
    """bot.py creates the Application with all handlers registered."""

    @patch("bot.Application")
    @patch("bot.CommandHandler")
    @patch("bot.MessageHandler")
    @patch("bot.CallbackQueryHandler")
    def test_build_app_registers_all_handlers(
        self,
        mock_cb,
        mock_msg,
        mock_cmd,
        mock_app_cls,
    ):
        from bot import build_app

        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app

        app = build_app("dummy-token")

        # At least 5 command handlers + 1 callback handler + 1 photo handler = 7
        assert mock_app.add_handler.call_count >= 7
        assert app is mock_app
