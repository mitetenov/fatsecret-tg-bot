"""Tests for the bot entry point (bot.py) — handler registration."""

from unittest.mock import MagicMock, patch

import pytest


class TestBotApplication:
    """bot.py build_app creates the Application with all handlers registered."""

    @patch("bot.Application")
    @patch("bot.CommandHandler")
    @patch("bot.MessageHandler")
    @patch("bot.CallbackQueryHandler")
    @patch("bot.ConversationHandler")
    def test_build_app_registers_all_handlers(
        self,
        mock_conv,
        mock_cb,
        mock_msg,
        mock_cmd,
        mock_app_cls,
    ):
        from bot import build_app

        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = (
            mock_app
        )

        # ConversationHandler should return a handler-like object
        mock_conv.return_value = MagicMock()
        # CommandHandler and other handlers also return MagicMock
        mock_cmd.return_value = MagicMock()
        mock_cb.return_value = MagicMock()
        mock_msg.return_value = MagicMock()

        app = build_app("dummy-token")

        # The current bot.py registers 9 handlers:
        # CommandHandler("start"), CommandHandler("help"), CommandHandler("log"),
        # CallbackQueryHandler(log_callback), build_add_handler(),
        # build_photo_handler(), build_barcode_handler(),
        # build_log_handler(), build_direct_photo_handler()
        assert mock_app.add_handler.call_count >= 9
        assert app is mock_app
