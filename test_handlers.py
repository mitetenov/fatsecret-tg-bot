"""Tests for bot handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- /start ----------------------------------------------------------------


class TestStartHandler:
    @patch("handlers.start.logger")
    async def test_start_sends_welcome(self, mock_logger):
        from handlers.start import start

        update = MagicMock()
        update.effective_user.first_name = "Alice"
        update.message.reply_text = AsyncMock()

        context = MagicMock()

        await start(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Alice" in text
        assert "FatSecret" in text


# --- /help -----------------------------------------------------------------


class TestHelpHandler:
    async def test_help_lists_commands(self):
        from handlers.help import help_cmd

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await help_cmd(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "/start" in text
        assert "/search" in text
        assert "/photo" in text
        assert "/barcode" in text
        assert "/setamount" in text
        assert "/cancel" in text


# --- /search ---------------------------------------------------------------


class TestSearchHandler:
    @patch("handlers.search.FatSecretClient")
    async def test_search_displays_results_keyboard(self, mock_fs_cls):
        from handlers.search import search_cmd

        mock_client = MagicMock()
        mock_client.search_food.return_value = {
            "foods_search": {
                "total_results": "2",
                "food": [
                    {"food_id": "1", "food_name": "Apple", "food_type": "Generic", "brand_name": ""},
                    {"food_id": "2", "food_name": "Banana Bread", "food_type": "Brand", "brand_name": "BestBake"},
                ],
            }
        }
        mock_fs_cls.return_value = mock_client

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["apple"]

        await search_cmd(update, context)

        update.message.reply_text.assert_called_once()
        call_kwargs = update.message.reply_text.call_args[1]
        assert "Found" in update.message.reply_text.call_args[0][0]
        assert call_kwargs["reply_markup"] is not None

    @patch("handlers.search.FatSecretClient")
    async def test_search_no_args_prompts_user(self, mock_fs_cls):
        from handlers.search import search_cmd

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []

        await search_cmd(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text or "usage" in text.lower()

    @patch("handlers.search.FatSecretClient")
    async def test_search_no_results(self, mock_fs_cls):
        from handlers.search import search_cmd

        mock_client = MagicMock()
        mock_client.search_food.return_value = {
            "foods_search": {"total_results": "0", "food": []}
        }
        mock_fs_cls.return_value = mock_client

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["xyznonexistent"]

        await search_cmd(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "not found" in text.lower() or "no result" in text.lower()


# --- /photo ----------------------------------------------------------------


class TestPhotoHandler:
    @patch("handlers.photo.VisionClient")
    async def test_photo_analyzes_and_prompts_confirm(self, mock_vision_cls):
        from handlers.photo import photo_cmd

        mock_vision = MagicMock()
        mock_vision.analyze_food.return_value = MagicMock(
            food_name="Salad",
            calories=300,
            protein=15.0,
            fat=20.0,
            carbs=10.0,
            serving_size="1 plate",
        )
        mock_vision_cls.return_value = mock_vision

        update = MagicMock()
        update.message.photo = [MagicMock()]
        photo_file = MagicMock()
        photo_file.download_as_bytearray = AsyncMock(return_value=b"fake-photo")
        update.message.photo[-1].get_file = AsyncMock(return_value=photo_file)
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await photo_cmd(update, context)

        # First call: "Analysing..."  |  Second call: results
        assert update.message.reply_text.call_count == 2
        result_text = update.message.reply_text.call_args_list[1][0][0]
        assert "Salad" in result_text
        assert "300" in result_text

    async def test_photo_no_photo_shows_usage(self):
        from handlers.photo import photo_cmd

        update = MagicMock()
        update.message.photo = []
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await photo_cmd(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "send" in text.lower() or "photo" in text.lower()


# --- /barcode --------------------------------------------------------------


class TestBarcodeHandler:
    @classmethod
    def setup_class(cls):
        """Create a minimal valid PNG for use in tests."""
        import struct, zlib
        sig = b'\x89PNG\r\n\x1a\n'
        def chunk(ctype, data):
            c = ctype + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        idat = chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00'))
        iend = chunk(b'IEND', b'')
        cls.valid_png = sig + ihdr + idat + iend

    @patch("handlers.barcode.decode")
    @patch("handlers.barcode.FatSecretClient")
    async def test_barcode_decodes_and_looks_up(self, mock_fs_cls, mock_decode):
        from handlers.barcode import barcode_cmd

        mock_decode.return_value = [MagicMock(data=b"5901234123457")]
        mock_client = MagicMock()
        mock_client.find_by_barcode.return_value = {
            "food": {
                "food_id": "999",
                "food_name": "Test Bar",
                "brand_name": "TestCo",
                "servings": {"serving": []},
            }
        }
        mock_fs_cls.return_value = mock_client

        update = MagicMock()
        update.message.photo = [MagicMock()]
        photo_file = MagicMock()
        photo_file.download_as_bytearray = AsyncMock(return_value=bytearray(self.valid_png))
        update.message.photo[-1].get_file = AsyncMock(return_value=photo_file)
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await barcode_cmd(update, context)

        # First: "Scanning...", Second: result
        assert update.message.reply_text.call_count == 2
        text = update.message.reply_text.call_args_list[1][0][0]
        assert "Test Bar" in text

    @patch("handlers.barcode.decode")
    async def test_barcode_no_barcode_found(self, mock_decode):
        from handlers.barcode import barcode_cmd

        mock_decode.return_value = []

        update = MagicMock()
        update.message.photo = [MagicMock()]
        photo_file = MagicMock()
        photo_file.download_as_bytearray = AsyncMock(return_value=bytearray(self.valid_png))
        update.message.photo[-1].get_file = AsyncMock(return_value=photo_file)
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        await barcode_cmd(update, context)

        # First: "Scanning...", Second: "No barcode found"
        assert update.message.reply_text.call_count == 2
        text = update.message.reply_text.call_args_list[1][0][0]
        assert "barcode" in text.lower()


# --- /setamount ------------------------------------------------------------


class TestSetAmountHandler:
    async def test_setamount_no_args_shows_current(self):
        """Without args, /setamount shows the current default."""
        from handlers.amount import setamount_cmd, sessions, SessionManager
        # Wire a fresh session manager
        sessions = SessionManager()

        update = MagicMock()
        update.effective_user.id = 42
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []

        # Import the module fresh so it picks up our wired sessions
        import importlib
        import handlers.amount
        importlib.reload(handlers.amount)
        handlers.amount.sessions = sessions

        await handlers.amount.setamount_cmd(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "100g" in text  # default

    async def test_setamount_sets_new_value(self):
        """With args, /setamount updates the default."""
        from handlers.amount import setamount_cmd, SessionManager
        import importlib
        import handlers.amount
        importlib.reload(handlers.amount)

        sm = SessionManager()
        handlers.amount.sessions = sm

        update = MagicMock()
        update.effective_user.id = 42
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["200ml"]

        await setamount_cmd(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "200ml" in text
        assert sm.get_or_create(42).default_serving_size == "200ml"


# --- Amount parsing --------------------------------------------------------


class TestParseAmount:
    def test_parse_grams(self):
        from handlers.amount import parse_amount
        assert parse_amount("150g") == 150.0
        assert parse_amount("150") == 150.0  # default assumption

    def test_parse_kg(self):
        from handlers.amount import parse_amount
        assert parse_amount("0.5kg") == 500.0
        assert parse_amount("1,5 кг") == 1500.0  # comma as decimal + russian

    def test_parse_ml(self):
        from handlers.amount import parse_amount
        assert parse_amount("200ml") == 200.0
        assert parse_amount("200 мл") == 200.0

    def test_parse_litres(self):
        from handlers.amount import parse_amount
        assert parse_amount("1l") == 1000.0
        assert parse_amount("0.5 л") == 500.0

    def test_parse_pieces_needs_serving_grams(self):
        from handlers.amount import parse_amount
        assert parse_amount("2 pcs", serving_grams=50.0) == 100.0
        assert parse_amount("3 pieces") is None  # no serving grams

    def test_parse_servings(self):
        from handlers.amount import parse_amount
        assert parse_amount("1 serving", serving_grams=200.0) == 200.0
        assert parse_amount("2 порции") is None  # no serving grams

    def test_parse_invalid_returns_none(self):
        from handlers.amount import parse_amount
        assert parse_amount("hello") is None
        assert parse_amount("") is None
        assert parse_amount("0g") is None  # zero is invalid
        assert parse_amount("-5g") is None


# --- Amount received -------------------------------------------------------


class TestAmountReceived:
    async def test_amount_received_logs_and_confirms(self):
        import importlib
        import handlers.amount
        from handlers.amount import amount_received, AWAITING_AMOUNT
        from session import SessionManager
        from db import CacheDB
        import tempfile
        import os

        importlib.reload(handlers.amount)

        sm = SessionManager()
        sess = sm.get_or_create(42)
        sess.select_food("5", "Banana")
        sess.serving_calories = 100.0
        sess.serving_protein = 1.0
        sess.serving_fat = 0.5
        sess.serving_carbs = 25.0
        sess.selected_serving_grams = 100.0
        sess.state = "awaiting_amount"  # set AFTER select_food()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            db = CacheDB(db_path)
            handlers.amount.sessions = sm
            handlers.amount.db = db

            update = MagicMock()
            update.effective_user.id = 42
            update.message.text = "150g"
            update.message.reply_text = AsyncMock()
            context = MagicMock()

            result = await amount_received(update, context)

            assert result == -1  # ConversationHandler.END

            update.message.reply_text.assert_called_once()
            text = update.message.reply_text.call_args[0][0]
            assert "Banana" in text
            assert "150" in text  # kcal
            assert "Logged" in text

            # Verify DB entry
            entries = db.get_user_log(42)
            assert len(entries) == 1
            assert entries[0]["food_name"] == "Banana"
            assert entries[0]["amount_raw"] == "150g"
            assert entries[0]["amount_grams"] == 150.0
            assert entries[0]["servings_multiplier"] == 1.5
            assert entries[0]["calories"] == 150.0

            db.close()
        finally:
            os.unlink(db_path)

    async def test_amount_received_invalid_input_retries(self):
        import importlib
        import handlers.amount
        from handlers.amount import amount_received, AWAITING_AMOUNT
        from session import SessionManager
        from db import CacheDB
        import tempfile
        import os

        importlib.reload(handlers.amount)

        sm = SessionManager()
        sess = sm.get_or_create(42)
        sess.select_food("5", "Banana")
        sess.serving_calories = 100.0
        sess.selected_serving_grams = 100.0
        sess.state = "awaiting_amount"  # set AFTER select_food()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            db = CacheDB(db_path)
            handlers.amount.sessions = sm
            handlers.amount.db = db

            update = MagicMock()
            update.effective_user.id = 42
            update.message.text = "blah"
            update.message.reply_text = AsyncMock()
            context = MagicMock()

            result = await amount_received(update, context)

            # Still waiting for amount
            assert result == AWAITING_AMOUNT

            update.message.reply_text.assert_called_once()
            text = update.message.reply_text.call_args[0][0]
            assert "understand" in text.lower()

            db.close()
        finally:
            os.unlink(db_path)

    async def test_amount_cancel_resets_session(self):
        from handlers.amount import amount_cancel
        from session import SessionManager
        import importlib
        import handlers.amount

        importlib.reload(handlers.amount)

        sm = SessionManager()
        sess = sm.get_or_create(42)
        sess.select_food("5", "Banana")
        sess.state = "awaiting_amount"  # set AFTER select_food()
        handlers.amount.sessions = sm

        update = MagicMock()
        update.effective_user.id = 42
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        result = await amount_cancel(update, context)

        assert result == -1  # ConversationHandler.END
        assert sm.get_or_create(42).state == "idle"

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Cancelled" in text
