"""Tests for bot handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

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
