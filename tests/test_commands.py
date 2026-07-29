"""Tests for command handlers — mocking Telegram's Update/Context.

Tests the conversation handler state transitions for /add, /photo,
/barcode, and /log.
"""

import os
import tempfile
from unittest import mock

import pytest
from telegram import CallbackQuery, Message, User
from telegram.ext import ConversationHandler

import commands.add as add_cmd
import commands.barcode as barcode_cmd
import commands.log as log_cmd
import commands.photo as photo_cmd
from conversation_helpers import (
    CONFIRMING_FOOD,
    ENTERING_QUANTITY,
    SELECTING_FOOD,
    SELECTING_UNIT,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _setup_db(monkeypatch):
    """Use temp SQLite database for all command tests."""
    db_path = os.path.join(tempfile.gettempdir(), f"test_cmd_{os.getpid()}.db")
    monkeypatch.setattr("database._engine", None)
    monkeypatch.setattr("database._SessionLocal", None)
    monkeypatch.setattr("config._config", None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import database
    database.init_db()
    yield
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def user():
    return User(id=12345, is_bot=False, first_name="Test")


@pytest.fixture
def message(user):
    """Build a mock Message."""
    msg = mock.create_autospec(Message, instance=True)
    msg.message_id = 1
    msg.chat_id = 12345
    msg.from_user = user
    msg.text = ""
    msg.photo = None
    msg.reply_text = mock.AsyncMock()
    msg.reply_markdown = mock.AsyncMock()
    return msg


@pytest.fixture
def update(user, message):
    """Build a mock Update."""
    upd = mock.create_autospec("telegram.Update", instance=True)
    upd.update_id = 1
    upd.effective_user = user
    upd.message = message
    upd.callback_query = None
    return upd


@pytest.fixture
def context():
    """Build a mock Context."""
    ctx = mock.MagicMock()
    ctx.user_data = {}
    ctx.args = []
    ctx.bot = mock.AsyncMock()
    return ctx


@pytest.fixture
def callback_query(message):
    """Build a mock CallbackQuery."""
    cq = mock.create_autospec(CallbackQuery, instance=True)
    cq.id = "cb-1"
    cq.from_user = message.from_user
    cq.message = message
    cq.data = ""
    cq.answer = mock.AsyncMock()
    cq.edit_message_text = mock.AsyncMock()
    return cq


# ── /add command tests ───────────────────────────────────────────────


class TestAddCommand:
    @pytest.mark.asyncio
    async def test_add_with_args_searches(self, update, context, monkeypatch):
        """/add banana → searches FatSecret, returns keyboard state."""
        context.args = ["banana"]

        fake_foods = [
            {"food_id": "1", "food_name": "Banana", "brand_name": None, "food_type": "Generic"},
        ]
        monkeypatch.setattr(
            "commands.add.fs.search_foods",
            mock.MagicMock(return_value=fake_foods),
        )

        result = await add_cmd.add_start(update, context)
        assert result == SELECTING_FOOD
        # Should have replied with search results
        update.message.reply_text.assert_called()
        call_args = update.message.reply_text.call_args
        assert "Banana" in str(call_args)

    @pytest.mark.asyncio
    async def test_add_without_args_asks_query(self, update, context):
        """/add with no args → asks for search query."""
        context.args = []

        result = await add_cmd.add_start(update, context)
        assert result == add_cmd.WAITING_QUERY
        update.message.reply_text.assert_called_once()
        # Should ask "what food"
        call_text = update.message.reply_text.call_args[0][0]
        assert "search" in call_text.lower()

    @pytest.mark.asyncio
    async def test_add_no_results(self, update, context, monkeypatch):
        """Search returns empty list."""
        context.args = ["nonexistent"]
        monkeypatch.setattr(
            "commands.add.fs.search_foods",
            mock.MagicMock(return_value=[]),
        )

        result = await add_cmd.add_start(update, context)
        assert result == ConversationHandler.END
        assert "No results" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_add_search_error(self, update, context, monkeypatch):
        """FatSecret API error during search."""
        context.args = ["error"]
        import fatsecret_client as fs
        monkeypatch.setattr(
            "commands.add.fs.search_foods",
            mock.MagicMock(side_effect=fs.FatSecretError("Server error", "500")),
        )

        result = await add_cmd.add_start(update, context)
        assert result == ConversationHandler.END

    @pytest.mark.asyncio
    async def test_add_select_food_cancel(self, update, context, callback_query):
        """Cancel callback ends conversation."""
        update.callback_query = callback_query
        update.message = None
        callback_query.data = "cancel_log"

        await add_cmd.add_select_food(update, context)
        callback_query.edit_message_text.assert_called()
        assert "cancelled" in callback_query.edit_message_text.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_add_receive_query_empty(self, update, context):
        """Empty query text keeps user in query state."""
        context.args = []
        update.message.text = ""

        result = await add_cmd.add_receive_query(update, context)
        assert result == add_cmd.WAITING_QUERY

    @pytest.mark.asyncio
    async def test_add_select_food_fetches_details(self, update, context, callback_query, monkeypatch):
        """Selecting a food fetches details and asks for quantity."""
        update.callback_query = callback_query
        update.message = None
        callback_query.data = "select_food:12345:0"

        fake_food = {
            "food_id": "12345",
            "food_name": "Chicken Breast",
            "brand_name": "Generic",
            "servings": {
                "serving": [
                    {
                        "serving_description": "100 g",
                        "calories": "165",
                        "protein": "31",
                        "fat": "3.6",
                        "carbohydrate": "0",
                    }
                ]
            },
        }
        monkeypatch.setattr(
            "commands.add.fs.get_food_details",
            mock.MagicMock(return_value=fake_food),
        )

        result = await add_cmd.add_select_food(update, context)
        assert result == ENTERING_QUANTITY
        assert "Chicken Breast" in context.user_data["pending_product_name"]

    @pytest.mark.asyncio
    async def test_add_receive_quantity(self, update, context, monkeypatch):
        """Valid quantity transitions to unit selection."""
        context.user_data["pending_product_name"] = "Apple"
        update.message.text = "2.5"

        result = await add_cmd.add_receive_quantity(update, context)
        assert result == SELECTING_UNIT
        assert context.user_data["pending_quantity"] == 2.5

    @pytest.mark.asyncio
    async def test_add_receive_quantity_invalid(self, update, context):
        """Invalid quantity keeps user in quantity state."""
        context.user_data["pending_product_name"] = "Apple"
        update.message.text = "abc"

        result = await add_cmd.add_receive_quantity(update, context)
        assert result == ENTERING_QUANTITY

    @pytest.mark.asyncio
    async def test_add_select_unit_logs_meal(self, update, context, callback_query):
        """Selecting a unit logs the meal and ends conversation."""
        update.callback_query = callback_query
        update.message = None
        callback_query.data = "select_unit:g"
        context.user_data["pending_product_name"] = "Rice"
        context.user_data["pending_quantity"] = 1.5
        context.user_data["pending_nutrition"] = {
            "calories": 200.0,
            "protein": 4.0,
            "fat": 1.0,
            "carbs": 45.0,
        }

        result = await add_cmd.add_select_unit(update, context)
        assert result == ConversationHandler.END
        # Verify meal was logged
        import database
        logs = database.get_today_logs(user_id=12345)
        assert len(logs) == 1
        assert logs[0].product_name == "Rice"
        assert logs[0].quantity == 1.5
        assert logs[0].unit == "g"

    @pytest.mark.asyncio
    async def test_add_select_unit_cancel(self, update, context, callback_query):
        """Cancel during unit selection."""
        update.callback_query = callback_query
        update.message = None
        callback_query.data = "cancel_log"

        result = await add_cmd.add_select_unit(update, context)
        assert result == ConversationHandler.END


# ── /photo command tests ─────────────────────────────────────────────


class TestPhotoCommand:
    @pytest.mark.asyncio
    async def test_photo_no_photo_attached(self, update, context):
        """No photo → asks user to send one."""
        update.message.photo = None

        result = await photo_cmd.photo_start(update, context)
        assert result == ConversationHandler.END
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_photo_with_photo_shows_confirm(self, update, context, monkeypatch):
        """Photo processing succeeds → shows confirm keyboard."""
        # Mock the photo file
        mock_photo = mock.MagicMock()
        mock_photo.file_id = "file-123"
        update.message.photo = [mock_photo]

        # Mock bot.get_file
        mock_file = mock.AsyncMock()
        mock_file.download_as_bytearray = mock.AsyncMock(return_value=bytearray(b"fake-image"))
        context.bot.get_file = mock.AsyncMock(return_value=mock_file)

        # Mock food recognition result
        fake_food = {
            "food_id": "1",
            "food_name": "Pizza",
            "brand_name": None,
            "servings": {
                "serving": [{
                    "serving_description": "1 slice",
                    "calories": "285",
                    "protein": "12",
                    "fat": "10",
                    "carbohydrate": "36",
                }]
            },
        }
        fake_result = photo_cmd.image_processing.FoodRecognitionResult(
            items=[],
            foods=[fake_food],
        )
        monkeypatch.setattr(
            "commands.photo.image_processing.process_food_photo",
            mock.MagicMock(return_value=fake_result),
        )

        result = await photo_cmd.photo_start(update, context)
        assert result == CONFIRMING_FOOD
        assert "Pizza" in context.user_data["pending_product_name"]

    @pytest.mark.asyncio
    async def test_photo_confirm_yes(self, update, context, callback_query):
        """Confirm yes → asks for quantity."""
        update.callback_query = callback_query
        update.message = None
        callback_query.data = "confirm_food:yes"
        context.user_data["pending_product_name"] = "Pizza"

        result = await photo_cmd.photo_confirm(update, context)
        assert result == ENTERING_QUANTITY

    @pytest.mark.asyncio
    async def test_photo_confirm_cancel(self, update, context, callback_query):
        """Confirm cancel → ends."""
        update.callback_query = callback_query
        update.message = None
        callback_query.data = "cancel_log"

        result = await photo_cmd.photo_confirm(update, context)
        assert result == ConversationHandler.END


# ── /barcode command tests ───────────────────────────────────────────


class TestBarcodeCommand:
    @pytest.mark.asyncio
    async def test_barcode_no_args_no_photo(self, update, context):
        """No barcode photo and no args → help message."""
        update.message.photo = None
        context.args = []

        result = await barcode_cmd.barcode_start(update, context)
        assert result == ConversationHandler.END
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_barcode_with_args_looksup(self, update, context, monkeypatch):
        """/barcode 0078742075581 → normalise + lookup."""
        update.message.photo = None
        context.args = ["0078742075581"]

        fake_food = {
            "food_id": "999",
            "food_name": "Protein Bar",
            "brand_name": "Quest",
            "servings": {
                "serving": [{
                    "serving_description": "1 bar",
                    "calories": "190",
                    "protein": "21",
                    "fat": "8",
                    "carbohydrate": "21",
                }]
            },
        }
        monkeypatch.setattr(
            "commands.barcode.fs.lookup_barcode",
            mock.MagicMock(return_value=fake_food),
        )

        result = await barcode_cmd.barcode_start(update, context)
        assert result == CONFIRMING_FOOD
        assert "Protein Bar" in context.user_data["pending_product_name"]

    @pytest.mark.asyncio
    async def test_barcode_invalid_number(self, update, context):
        """Invalid barcode number → error."""
        update.message.photo = None
        context.args = ["123"]  # too short

        result = await barcode_cmd.barcode_start(update, context)
        assert result == ConversationHandler.END
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_barcode_confirm_yes(self, update, context, callback_query):
        """Confirm barcode result → ask quantity."""
        update.callback_query = callback_query
        update.message = None
        callback_query.data = "confirm_food:yes"
        context.user_data["pending_product_name"] = "Protein Bar"

        result = await barcode_cmd.barcode_confirm(update, context)
        assert result == ENTERING_QUANTITY


# ── /log command tests ───────────────────────────────────────────────


class TestLogCommand:
    @pytest.mark.asyncio
    async def test_log_empty(self, update, context):
        """No meals logged → shows empty message."""
        result = await log_cmd.log_show(update, context)
        assert result is None
        call_text = update.message.reply_text.call_args[0][0]
        assert "No meals" in call_text

    @pytest.mark.asyncio
    async def test_log_with_entries(self, update, context, monkeypatch):
        """Has meals → shows summary with edit/delete buttons."""
        import database
        database.log_meal(
            user_id=12345, product_name="Apple", quantity=1.0,
            unit="piece", calories=95.0, protein=0.5, fat=0.3, carbs=25.0,
        )
        database.log_meal(
            user_id=12345, product_name="Banana", quantity=2.0,
            unit="piece", calories=105.0, protein=1.3, fat=0.4, carbs=27.0,
        )

        await log_cmd.log_show(update, context)
        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args
        text = str(call_args)
        assert "Apple" in text
        assert "Banana" in text

    @pytest.mark.asyncio
    async def test_log_delete_entry(self, update, context, callback_query, monkeypatch):
        """Delete an entry via callback."""
        import database
        entry = database.log_meal(
            user_id=12345, product_name="DeleteMe", quantity=1.0,
            unit="serving", calories=50.0,
        )

        update.callback_query = callback_query
        update.message = None
        callback_query.data = f"delete_entry:{entry.id}"

        from conversation_helpers import ConversationHandler as CH
        result = await log_cmd.log_callback(update, context)
        # Delete entries and refreshes
        assert database.get_log_entry(entry.id) is None

    @pytest.mark.asyncio
    async def test_log_edit_entry_starts(self, update, context, callback_query, monkeypatch):
        """Edit entry → transitions to quantity state."""
        import database
        entry = database.log_meal(
            user_id=12345, product_name="EditMe", quantity=1.0,
            unit="serving", calories=100.0, protein=10.0,
        )

        update.callback_query = callback_query
        update.message = None
        callback_query.data = f"edit_entry:{entry.id}"

        result = await log_cmd.log_callback(update, context)
        assert result == ENTERING_QUANTITY
        assert context.user_data["editing_entry_id"] == entry.id

    @pytest.mark.asyncio
    async def test_edit_receive_quantity(self, update, context, monkeypatch):
        """Enter new quantity → updates entry."""
        import database
        entry = database.log_meal(
            user_id=12345, product_name="EditMe", quantity=1.0,
            unit="serving", calories=100.0, protein=10.0,
        )

        context.user_data["editing_entry_id"] = entry.id
        context.user_data["pending_nutrition"] = {
            "calories": 100.0,
            "protein": 10.0,
        }
        update.message.text = "2.0"

        result = await log_cmd.edit_receive_quantity(update, context)
        assert result == ConversationHandler.END
        # Verify updated
        updated = database.get_log_entry(entry.id)
        assert updated.quantity == 2.0
        assert updated.calories == 200.0  # scaled
        assert updated.protein == 20.0


# ── Handler builders ─────────────────────────────────────────────────


class TestHandlerBuilders:
    def test_build_add_handler(self):
        handler = add_cmd.build_add_handler()
        assert handler is not None
        assert handler.name == "add_food"

    def test_build_photo_handler(self):
        handler = photo_cmd.build_photo_handler()
        assert handler is not None
        assert handler.name == "photo_food"

    def test_build_barcode_handler(self):
        handler = barcode_cmd.build_barcode_handler()
        assert handler is not None
        assert handler.name == "barcode_food"

    def test_build_log_handler(self):
        handler = log_cmd.build_log_handler()
        assert handler is not None
        assert handler.name == "edit_log"

    def test_build_direct_photo_handler(self):
        """build_direct_photo_handler returns a ConversationHandler with the correct name."""
        import bot

        handler = bot.build_direct_photo_handler()
        assert handler is not None
        assert handler.name == "direct_photo"
        assert len(handler.entry_points) == 1
        assert len(handler.states) == 3  # CONFIRMING_FOOD, ENTERING_QUANTITY, SELECTING_UNIT
        assert len(handler.fallbacks) == 1


class TestBotModule:
    """Tests for the bot.py module's general structure."""

    def test_main_imports(self):
        """bot.py module-level imports work correctly."""
        import bot

        assert hasattr(bot, "main")
        assert hasattr(bot, "build_direct_photo_handler")
        assert hasattr(bot, "direct_photo_start")
