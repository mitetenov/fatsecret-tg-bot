"""Main entry point — Telegram bot for FatSecret nutrition tracking.

Wires up:
- /start, /help  — simple commands
- /add           — ConversationHandler (search → select → quantity → unit → log)
- /photo         — ConversationHandler (photo → confirm → quantity → unit → log)
- /barcode       — ConversationHandler (barcode → confirm → quantity → unit → log)
- /log           — CommandHandler (show today's log with inline keyboard actions)
- Photo messages  — ConversationHandler (try barcode first, fall back to food recognition)
"""

import logging
import sys

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from commands.add import build_add_handler
from commands.barcode import build_barcode_handler
from commands.help import help_cmd
from commands.log import build_log_handler, log_callback, log_show
from commands.photo import build_photo_handler
from commands.start import start
from config import get_config
from database import init_db
from image_processing import configure as ip_configure

# -- Logging --------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# -- Direct photo message ConversationHandler -----------------------------
#
# Catches uncaptioned photo messages, tries barcode scanning first
# (fast, offline), then falls back to food photo recognition.
# Both paths return CONFIRMING_FOOD so the shared confirmation ->
# quantity -> unit -> log flow is used.


async def direct_photo_start(update, context):
    """Entry point for the direct-photo ConversationHandler.

    Downloads the photo, tries barcode scanning; if that fails,
    falls back to food recognition via Gemini Vision.
    Returns CONFIRMING_FOOD on success or ConversationHandler.END.
    """
    import image_processing

    message = update.message
    if message is None or not message.photo:
        return ConversationHandler.END

    photos = message.photo
    photo_file = photos[-1]
    file = await context.bot.get_file(photo_file.file_id)
    image_bytes = bytes(await file.download_as_bytearray())

    # Try barcode first (fast, offline)
    barcode_result = image_processing.process_barcode_photo(image_bytes)
    if barcode_result.success and barcode_result.food:
        return await _show_result_and_confirm(update, context, barcode_result.food)

    # Fall back to food photo recognition
    await message.reply_text("No barcode found -- analysing meal photo ...")
    food_result = image_processing.process_food_photo(image_bytes)
    if food_result.success and food_result.foods:
        return await _show_result_and_confirm(update, context, food_result.foods[0])

    # Neither worked -- show a useful message
    text = food_result.error if food_result and food_result.error else barcode_result.error
    await message.reply_text(
        text
        or "Couldn't identify this photo. Try a clearer shot or use /add to search manually.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def _show_result_and_confirm(update, context, food: dict):
    """Store food data and show confirm keyboard.

    Shared helper for both barcode and photo paths.
    """
    from conversation_helpers import CONFIRMING_FOOD, _build_confirm_keyboard

    message = update.message
    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    nut = _extract_nutrition(food)

    context.user_data["pending_nutrition"] = nut
    context.user_data["pending_brand"] = brand
    context.user_data["pending_product_name"] = name

    lines = [f"🍽 *{name}*"]
    if brand:
        lines.append(f"   _({brand})_")
    lines.append(f"   🔥 {nut.get('calories', '?')} kcal")
    lines.append("")
    lines.append("Would you like to log this?")

    await message.reply_text(
        "\n".join(lines),
        reply_markup=_build_confirm_keyboard("confirm_food"),
        parse_mode="Markdown",
    )
    return CONFIRMING_FOOD


def _extract_nutrition(food: dict) -> dict:
    """Extract per-serving nutrition from a FatSecret food dict."""
    servings = food.get("servings", {}).get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]
    if not servings:
        return {}
    s = servings[0]
    return {
        "calories": _to_float(s.get("calories")),
        "fat": _to_float(s.get("fat")),
        "carbs": _to_float(s.get("carbohydrate")),
        "protein": _to_float(s.get("protein")),
        "serving_description": s.get("serving_description", "1 serving"),
    }


def _to_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


async def direct_photo_confirm(update, context):
    """Handle confirm/cancel callback from direct-photo result."""
    from commands.photo import photo_confirm

    return await photo_confirm(update, context)


async def direct_photo_receive_quantity(update, context):
    """Receive quantity during direct-photo flow."""
    from conversation_helpers import receive_quantity

    return await receive_quantity(update, context)


async def direct_photo_select_unit(update, context):
    """Select unit and log during direct-photo flow."""
    from conversation_helpers import select_unit_and_log

    return await select_unit_and_log(update, context)


def build_direct_photo_handler() -> ConversationHandler:
    """Return a ConversationHandler for uncaptioned photo messages."""
    from conversation_helpers import (
        CONFIRMING_FOOD,
        ENTERING_QUANTITY,
        SELECTING_UNIT,
        cancel_log,
    )

    return ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, direct_photo_start)],
        states={
            CONFIRMING_FOOD: [
                CallbackQueryHandler(
                    direct_photo_confirm, pattern="^confirm_food:"
                ),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
            ENTERING_QUANTITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, direct_photo_receive_quantity
                ),
                CommandHandler("cancel", cancel_log),
            ],
            SELECTING_UNIT: [
                CallbackQueryHandler(
                    direct_photo_select_unit, pattern="^select_unit:"
                ),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_log)],
        name="direct_photo",
    )


# -- App builder -----------------------------------------------------------


def build_app(token: str) -> Application:
    """Create and wire up the PTB Application with all handlers registered."""
    app = Application.builder().token(token).build()

    # -- Simple command handlers --------------------------------------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # -- /log command (show today's entries) --------------------------
    app.add_handler(CommandHandler("log", log_show))

    # -- /log inline keyboard handlers (refresh, delete) --------------
    app.add_handler(
        CallbackQueryHandler(log_callback, pattern="^(refresh_log|delete_entry:)")
    )

    # -- ConversationHandlers (multi-step flows) ----------------------
    # Order matters: more specific handlers first
    app.add_handler(build_add_handler())
    app.add_handler(build_photo_handler())
    app.add_handler(build_barcode_handler())
    app.add_handler(build_log_handler())  # edit flow

    # -- Direct photo ConversationHandler (uncaptioned photos) -------
    # Tries barcode first, then food recognition, with full conversation flow
    app.add_handler(build_direct_photo_handler())

    return app


# -- Main ------------------------------------------------------------------


def main() -> None:
    cfg = get_config()

    missing = cfg.validate()
    if missing:
        logger.error(
            "Missing required environment variables: %s", ", ".join(missing)
        )
        sys.exit(1)

    logger.info("Starting FatSecret Telegram Bot ...")

    # Init database schema
    init_db()

    # Configure image processing module
    ip_configure(
        api_key=cfg.gemini_api_key or cfg.ai_api_key,
        model=(
            cfg.ai_model if cfg.ai_model.startswith("gemini") else "gemini-2.0-flash"
        ),
    )

    # Build the Application
    app = build_app(cfg.bot_token)

    logger.info("Bot is polling ...")
    app.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()