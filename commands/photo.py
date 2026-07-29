"""/photo command — analyse meal photos with AI, then log.

Flow:
  1. /photo with photo attached → Gemini Vision + FatSecret → show results
  2. Confirm → ask quantity
  3. Enter quantity → ask unit
  4. Pick unit → log to database, show confirmation
"""

import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import database
import image_processing
from conversation_helpers import (
    CONFIRMING_FOOD,
    ENTERING_QUANTITY,
    SELECTING_UNIT,
    _build_confirm_keyboard,
    ask_quantity,
    cancel_log,
    extract_nutrition,
    receive_quantity,
    select_unit_and_log,
)

logger = logging.getLogger(__name__)


# ── Entry point ─────────────────────────────────────────────────────


async def photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /photo — process attached photo, show results."""
    message = update.message
    if message is None:
        return ConversationHandler.END

    photos = message.photo
    if not photos:
        await message.reply_text(
            "📸 Send a photo of your meal along with the /photo command.\n"
            "Example: attach a photo and caption it `/photo`.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Download the largest photo
    photo_file = photos[-1]
    file = await context.bot.get_file(photo_file.file_id)
    image_bytes = bytes(await file.download_as_bytearray())

    await message.reply_text("🔍 Analysing your meal photo …")

    result = await _process_photo(image_bytes)

    if not result["success"]:
        await message.reply_text(
            result["text"],
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Store data for logging
    context.user_data["pending_nutrition"] = result["nutrition"]
    context.user_data["pending_brand"] = result.get("brand")
    context.user_data["pending_product_name"] = result["name"]

    await message.reply_text(
        result["text"],
        reply_markup=_build_confirm_keyboard("confirm_food"),
        parse_mode="Markdown",
    )
    return CONFIRMING_FOOD


# ── State: CONFIRMING_FOOD ──────────────────────────────────────────


async def photo_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle confirm/cancel callback from food recognition result."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_log":
        await query.edit_message_text("❌ Logging cancelled.")
        return ConversationHandler.END

    if query.data != "confirm_food:yes":
        await query.edit_message_text("❌ Logging cancelled.")
        return ConversationHandler.END

    product_name = context.user_data.get("pending_product_name", "this food")
    await query.edit_message_text(
        "✅ Confirmed! Now let's log it.",
    )
    return await ask_quantity(update, context, product_name)


# ── State: ENTERING_QUANTITY ────────────────────────────────────────


async def photo_receive_quantity(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await receive_quantity(update, context)


# ── State: SELECTING_UNIT ───────────────────────────────────────────


async def photo_select_unit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await select_unit_and_log(update, context)


# ── Internal helpers ────────────────────────────────────────────────


async def _process_photo(image_bytes: bytes) -> dict:
    """Process a food photo and return {success, text, name, nutrition, brand}."""
    try:
        result = image_processing.process_food_photo(image_bytes)
    except Exception:
        logger.exception("Photo processing failed")
        return {
            "success": False,
            "text": "❌ Food recognition failed. Please try again later.",
        }

    if result.error:
        return {"success": False, "text": result.error}

    if result.no_food_detected:
        return {
            "success": False,
            "text": (
                "🤔 I don't see any food in this photo.\n"
                "Try taking a closer, well-lit photo of the meal."
            ),
        }

    if not result.foods:
        return {
            "success": False,
            "text": (
                "🤔 I couldn't identify any food items.\n"
                "Try a clearer photo or use /add to search manually."
            ),
        }

    # Take the first matched food
    food = result.foods[0]
    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    nut = extract_nutrition(food)

    lines = ["📸 *Food recognised:*"]
    label = f"*{name}*"
    if brand:
        label += f" _({brand})_"
    lines.append(f"  🍽 {label}")

    if nut:
        sd = nut.get("serving_description", "1 serving")
        lines.append(f"  📏 {sd}")
        macro_parts = []
        kcal = nut.get("calories")
        if kcal is not None:
            macro_parts.append(f"🔥 {kcal:.0f} kcal")
        if nut.get("protein") is not None:
            macro_parts.append(f"🥩 P:{nut['protein']:.1f}g")
        if nut.get("fat") is not None:
            macro_parts.append(f"🧈 F:{nut['fat']:.1f}g")
        if nut.get("carbs") is not None:
            macro_parts.append(f"🍞 C:{nut['carbs']:.1f}g")
        if macro_parts:
            lines.append(f"  {'  '.join(macro_parts)}")

    lines.append("\nWould you like to log this?")

    return {
        "success": True,
        "text": "\n".join(lines),
        "name": name,
        "brand": brand,
        "nutrition": nut,
    }


# ── ConversationHandler builder ─────────────────────────────────────


def build_photo_handler() -> ConversationHandler:
    """Return a ConversationHandler for the /photo flow."""
    return ConversationHandler(
        entry_points=[CommandHandler("photo", photo_start)],
        states={
            CONFIRMING_FOOD: [
                CallbackQueryHandler(photo_confirm, pattern="^confirm_food:"),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
            ENTERING_QUANTITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, photo_receive_quantity
                ),
                CommandHandler("cancel", cancel_log),
            ],
            SELECTING_UNIT: [
                CallbackQueryHandler(photo_select_unit, pattern="^select_unit:"),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_log)],
        name="photo_food",
    )
