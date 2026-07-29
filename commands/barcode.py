"""/barcode command — scan barcode photos, look up product, then log.

Flow:
  1. /barcode with photo attached → pyzbar decode → FatSecret lookup → show result
     OR /barcode <number>  → normalise → FatSecret lookup → show result
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
import fatsecret_client as fs
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


async def barcode_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /barcode — process barcode photo, show result with confirm."""
    message = update.message
    if message is None:
        return ConversationHandler.END

    photos = message.photo
    if not photos:
        # No photo — maybe typed barcode number
        if context.args:
            code = context.args[0].strip()
            return await _lookup_text_barcode(update, context, code)
        await message.reply_text(
            "📸 Send a photo of a barcode along with the /barcode command, "
            "or type the number: `/barcode 0078742075581`\n\n"
            "Supported: UPC-A, EAN-13, EAN-8.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Download photo
    photo_file = photos[-1]
    file = await context.bot.get_file(photo_file.file_id)
    image_bytes = bytes(await file.download_as_bytearray())

    await message.reply_text("🔎 Scanning barcode …")

    result = await _process_barcode(image_bytes)

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


async def barcode_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle confirm/cancel callback from barcode lookup result."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_log":
        await query.edit_message_text("❌ Logging cancelled.")
        return ConversationHandler.END

    if query.data != "confirm_food:yes":
        await query.edit_message_text("❌ Logging cancelled.")
        return ConversationHandler.END

    product_name = context.user_data.get("pending_product_name", "this product")
    await query.edit_message_text("✅ Confirmed! Now let's log it.")
    return await ask_quantity(update, context, product_name)


# ── State: ENTERING_QUANTITY ────────────────────────────────────────


async def barcode_receive_quantity(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await receive_quantity(update, context)


# ── State: SELECTING_UNIT ───────────────────────────────────────────


async def barcode_select_unit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await select_unit_and_log(update, context)


# ── Internal helpers ────────────────────────────────────────────────


async def _process_barcode(image_bytes: bytes) -> dict:
    """Process a barcode photo and return {success, text, name, nutrition, brand}."""
    try:
        result = image_processing.process_barcode_photo(image_bytes)
    except Exception:
        logger.exception("Barcode processing failed")
        return {
            "success": False,
            "text": "❌ Barcode scanning failed. Please try again.",
        }

    if result.error:
        return {"success": False, "text": result.error}

    if not result.food:
        return {
            "success": False,
            "text": f"📭 Barcode *{result.gtin}* not found in any database.",
        }

    food = result.food
    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    nut = extract_nutrition(food)

    lines = [f"✅ Barcode *{result.gtin}* matched:"]
    label = f"🏷 *{name}*"
    if brand:
        label += f" _({brand})_"
    lines.append(f"  {label}")

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


async def _lookup_text_barcode(
    update: Update, context: ContextTypes.DEFAULT_TYPE, code: str
) -> int:
    """Look up a typed barcode number, show result with confirm."""
    message = update.message

    try:
        gtin = image_processing._normalise_gtin13(code)
    except ValueError as exc:
        await message.reply_text(str(exc))
        return ConversationHandler.END

    await message.reply_text(f"🔎 Looking up barcode *{gtin}* …")

    try:
        # Check cache first
        cached = database.get_cached_by_barcode(gtin)
        if cached:
            food = {
                "food_id": cached.food_id,
                "food_name": cached.product_name,
                "brand_name": cached.brand,
                "servings": {
                    "serving": [
                        {
                            "serving_description": cached.serving_description or "1 serving",
                            "calories": str(cached.calories) if cached.calories else None,
                            "fat": str(cached.fat) if cached.fat else None,
                            "carbohydrate": str(cached.carbs) if cached.carbs else None,
                            "protein": str(cached.protein) if cached.protein else None,
                        }
                    ]
                },
            }
        else:
            food = fs.lookup_barcode(gtin)
            # Cache it
            nut = extract_nutrition(food)
            servings = food.get("servings", {}).get("serving", [])
            if isinstance(servings, dict):
                servings = [servings]
            sd = servings[0].get("serving_description") if servings else None
            database.cache_product(
                barcode=gtin,
                food_id=food.get("food_id"),
                product_name=food.get("food_name", "Unknown"),
                brand=food.get("brand_name"),
                serving_description=sd,
                calories=nut.get("calories"),
                fat=nut.get("fat"),
                carbs=nut.get("carbs"),
                protein=nut.get("protein"),
            )
    except fs.FatSecretError as exc:
        if exc.code == "211":
            await message.reply_text(
                f"📭 Barcode *{gtin}* not found in FatSecret database.",
                parse_mode="Markdown",
            )
        else:
            await message.reply_text(f"⚠️ Lookup failed: {exc}")
        return ConversationHandler.END
    except Exception:
        logger.exception("Text barcode lookup failed")
        await message.reply_text("❌ Failed to look up barcode. Please try again.")
        return ConversationHandler.END

    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    nut = extract_nutrition(food)

    lines = [f"✅ Barcode *{gtin}* matched:"]
    label = f"🏷 *{name}*"
    if brand:
        label += f" _({brand})_"
    lines.append(f"  {label}")

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

    context.user_data["pending_nutrition"] = nut
    context.user_data["pending_brand"] = brand
    context.user_data["pending_product_name"] = name

    await message.reply_text(
        "\n".join(lines),
        reply_markup=_build_confirm_keyboard("confirm_food"),
        parse_mode="Markdown",
    )
    return CONFIRMING_FOOD


# ── ConversationHandler builder ─────────────────────────────────────


def build_barcode_handler() -> ConversationHandler:
    """Return a ConversationHandler for the /barcode flow."""
    return ConversationHandler(
        entry_points=[CommandHandler("barcode", barcode_start)],
        states={
            CONFIRMING_FOOD: [
                CallbackQueryHandler(barcode_confirm, pattern="^confirm_food:"),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
            ENTERING_QUANTITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, barcode_receive_quantity
                ),
                CommandHandler("cancel", cancel_log),
            ],
            SELECTING_UNIT: [
                CallbackQueryHandler(barcode_select_unit, pattern="^select_unit:"),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_log)],
        name="barcode_food",
    )
