"""/add command — text search, result selection, then log.

Flow:
  1. /add <query>  → search FatSecret, show inline keyboard results
  2. Pick a result → fetch details, show nutrition, ask quantity
  3. Enter quantity  → ask for unit
  4. Pick unit       → log to database, show confirmation
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
from config import get_config
from conversation_helpers import (
    ENTERING_QUANTITY,
    SELECTING_FOOD,
    SELECTING_UNIT,
    ask_quantity,
    build_food_info_lines,
    cancel_log,
    extract_nutrition,
    receive_quantity,
    select_unit_and_log,
)

logger = logging.getLogger(__name__)

# Additional state for /add: waiting for query text (when no args)
WAITING_QUERY = 100


# ── Entry point ─────────────────────────────────────────────────────


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /add — if query provided, search immediately; else ask."""
    query = " ".join(context.args) if context.args else ""

    if query:
        return await _search_and_show(update, context, query)
    else:
        await update.message.reply_text(
            "🔍 What food would you like to search for?\n"
            "Type a name, e.g. _banana_, _chicken breast_, or _Greek yogurt_.",
            parse_mode="Markdown",
        )
        return WAITING_QUERY


# ── State: WAITING_QUERY ─────────────────────────────────────────────


async def add_receive_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User typed a search query — perform search and show results."""
    query = update.message.text.strip() if update.message else ""
    if not query:
        await update.message.reply_text("Please type a food name to search.")
        return WAITING_QUERY
    return await _search_and_show(update, context, query)


# ── State: SELECTING_FOOD ────────────────────────────────────────────


async def add_select_food(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User picked a food from the inline keyboard — fetch details, ask quantity."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_log":
        await query.edit_message_text("❌ Search cancelled.")
        return ConversationHandler.END

    # Parse callback: "select_food:<food_id>:<index>"
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.edit_message_text("⚠️ Invalid selection.")
        return ConversationHandler.END

    food_id = parts[1]
    try:
        food_idx = int(parts[2])
    except ValueError:
        food_idx = 0

    # Fetch full details from FatSecret (or cache)
    cfg = get_config()
    try:
        # Check cache first
        cached = database.get_cached_by_food_id(food_id)
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
            food = fs.get_food_details(food_id, region=cfg.fatsecret_region)
            # Cache the result
            nut = extract_nutrition(food)
            servings = food.get("servings", {}).get("serving", [])
            if isinstance(servings, dict):
                servings = [servings]
            sd = servings[0].get("serving_description") if servings else None
            database.cache_product(
                food_id=food_id,
                product_name=food.get("food_name", "Unknown"),
                brand=food.get("brand_name"),
                serving_description=sd,
                calories=nut.get("calories"),
                fat=nut.get("fat"),
                carbs=nut.get("carbs"),
                protein=nut.get("protein"),
            )
    except fs.FatSecretError as exc:
        logger.error("FatSecret details error: %s", exc)
        await query.edit_message_text(
            f"⚠️ Could not fetch details: {exc}"
        )
        return ConversationHandler.END
    except Exception:
        logger.exception("Unexpected error fetching food details")
        await query.edit_message_text(
            "❌ Failed to fetch food details. Please try again."
        )
        return ConversationHandler.END

    # Store nutrition data for logging
    nut = extract_nutrition(food)
    context.user_data["pending_nutrition"] = nut
    context.user_data["pending_brand"] = food.get("brand_name")
    context.user_data["pending_food"] = food

    product_name = food.get("food_name", "Unknown food")
    brand = food.get("brand_name", "")

    info_lines = [f"🍽 *{product_name}*"]
    if brand:
        info_lines.append(f"  🏷 _{brand}_")
    if nut:
        sd = nut.get("serving_description", "1 serving")
        info_lines.append(f"  📏 {sd}")
        kcal = nut.get("calories")
        protein = nut.get("protein")
        fat = nut.get("fat")
        carbs = nut.get("carbs")
        macro_parts = []
        if kcal is not None:
            macro_parts.append(f"🔥 {kcal:.0f} kcal")
        if protein is not None:
            macro_parts.append(f"🥩 P:{protein:.1f}g")
        if fat is not None:
            macro_parts.append(f"🧈 F:{fat:.1f}g")
        if carbs is not None:
            macro_parts.append(f"🍞 C:{carbs:.1f}g")
        if macro_parts:
            info_lines.append(f"  {'  '.join(macro_parts)}")

    await query.edit_message_text(
        "\n".join(info_lines), parse_mode="Markdown"
    )

    return await ask_quantity(update, context, product_name)


# ── State: ENTERING_QUANTITY ────────────────────────────────────────


async def add_receive_quantity(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await receive_quantity(update, context)


# ── State: SELECTING_UNIT ───────────────────────────────────────────


async def add_select_unit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await select_unit_and_log(update, context)


# ── Internal helpers ────────────────────────────────────────────────


async def _search_and_show(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query: str
) -> int:
    """Search FatSecret and display results as inline keyboard."""
    cfg = get_config()
    try:
        # Check cache first
        cached = database.get_cached_by_query(query)
        if cached:
            # Build a minimal food dict from cache
            foods = [
                {
                    "food_id": cached.food_id,
                    "food_name": cached.product_name,
                    "brand_name": cached.brand,
                }
            ]
        else:
            foods = fs.search_foods(
                query,
                region=cfg.fatsecret_region,
                language=cfg.fatsecret_language,
                max_results=5,
            )
    except fs.FatSecretError as exc:
        logger.error("FatSecret search error: %s", exc)
        await update.message.reply_text(
            f"⚠️ Search failed: {exc}"
        )
        return ConversationHandler.END
    except Exception:
        logger.exception("Unexpected search error")
        await update.message.reply_text(
            "❌ Search failed. Please try again later."
        )
        return ConversationHandler.END

    if not foods:
        await update.message.reply_text(
            f"🔍 No results found for *{query}*.\n"
            "Try a different name or be more specific.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Build result message and keyboard
    text, keyboard_rows = build_food_info_lines(foods)

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
        parse_mode="Markdown",
    )
    return SELECTING_FOOD


# ── ConversationHandler builder ─────────────────────────────────────


def build_add_handler() -> ConversationHandler:
    """Return a ConversationHandler for the /add flow."""
    return ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            WAITING_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_query),
                CommandHandler("cancel", cancel_log),
            ],
            SELECTING_FOOD: [
                CallbackQueryHandler(add_select_food, pattern="^select_food:"),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
            ENTERING_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_quantity),
                CommandHandler("cancel", cancel_log),
            ],
            SELECTING_UNIT: [
                CallbackQueryHandler(add_select_unit, pattern="^select_unit:"),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_log)],
        name="add_food",
    )
