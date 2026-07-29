"""FatSecret Telegram Bot — main entry point.

Wires up python-telegram-bot v20+ with all command handlers,
a ConversationHandler for inline-keyboard workflows, the
callback query handler for food selection and confirmation,
and the amount-input conversation stage.
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db import CacheDB
from fatsecret_client import FatSecretClient, FatSecretError
from handlers.amount import (
    AWAITING_AMOUNT,
    amount_cancel,
    amount_received,
    ask_amount,
    build_amount_handler,
    setamount_cmd,
)
from handlers.barcode import barcode_cmd
from handlers.help import help_cmd
from handlers.photo import photo_cmd, sessions
from handlers.search import search_cmd
from handlers.start import start
from keyboards import build_food_results_keyboard, build_serving_keyboard

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Shared DB instance (reused across handlers)
food_db = CacheDB(os.environ.get("DB_PATH", "fatsecret_bot.db"))


def _wire_handlers() -> None:
    """Inject shared state into handler modules."""
    import handlers.amount as amt

    amt.sessions = sessions
    amt.db = food_db


# Conversation states (for bot-level routing)
AWAITING_SERVING = 1


async def _callback_handler(update: Update, context: object) -> None:
    """Handle inline keyboard callbacks: select, confirm, cancel, serving."""
    query = update.callback_query
    await query.answer()

    data: str = query.data or ""

    if data.startswith("select:"):
        await _handle_select(query, data)
    elif data.startswith("confirm:"):
        await _handle_confirm(query, data)
    elif data == "cancel":
        await _handle_cancel(query)
    elif data.startswith("serving:"):
        await _handle_serving(query, data)
    else:
        await query.edit_message_text("Unknown action.")


async def _handle_select(query, data: str) -> None:
    """User tapped a food result — fetch details and show servings."""
    food_id = data.split(":", 1)[1]
    client = FatSecretClient()

    try:
        result = client.get_food_details(food_id)
    except FatSecretError as exc:
        await query.edit_message_text(f"❌ Could not fetch food details: {exc}")
        return

    food = result.get("food", {})
    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    servings = food.get("servings", {}).get("serving", [])

    lines = [f"🍽 *{name}*"]
    if brand:
        lines.append(f"🏭 {brand}")

    if servings:
        # Normalise to list
        if isinstance(servings, dict):
            servings = [servings]

        lines.append("\n📏 **Servings:**")
        for s in servings:
            desc = s.get("serving_description", "—")
            kcal = s.get("calories", "—")
            protein = s.get("protein", "—")
            fat = s.get("fat", "—")
            carbs = s.get("carbohydrate", "—")
            lines.append(
                f"• {desc}: {kcal} kcal | P:{protein}g F:{fat}g C:{carbs}g"
            )
    else:
        lines.append("\n⚠ No serving data available.")

    # Store selection in session
    user_id = query.from_user.id
    sess = sessions.get_or_create(user_id)
    sess.select_food(food_id, name)

    kb = build_serving_keyboard(servings, food_id)
    await query.edit_message_text(
        "\n".join(lines), reply_markup=kb, parse_mode="Markdown"
    )


async def _handle_confirm(query, data: str) -> None:
    """User confirmed — log the food entry (from photo analysis)."""
    user_id = query.from_user.id
    sess = sessions.get_or_create(user_id)

    food_name = (
        sess.selected_food_name
        or (sess.analysis.food_name if sess.analysis else "this item")
    )

    # Transition to amount input for photo-based entries
    sess.state = "awaiting_amount"
    # Use analysis data as serving info if available
    if sess.analysis and not sess.selected_serving_id:
        sess.serving_calories = float(sess.analysis.calories)
        sess.serving_protein = float(sess.analysis.protein)
        sess.serving_fat = float(sess.analysis.fat)
        sess.serving_carbs = float(sess.analysis.carbs)
        sess.selected_serving_desc = sess.analysis.serving_size or "1 serving"
        sess.selected_serving_grams = 100.0  # default assumption

    await query.edit_message_text(
        f"🍽 *{food_name}*\n\n⚖️ How much did you eat?\n\n"
        "Type a weight (150g, 0.2kg), volume (200ml), or pieces (2 pcs).\n"
        "Or tap /cancel to abort.",
        parse_mode="Markdown",
    )


async def _handle_cancel(query) -> None:
    """User cancelled."""
    user_id = query.from_user.id
    sessions.get_or_create(user_id).reset()
    await query.edit_message_text("❌ Cancelled.  Try /search or send another photo!")


async def _handle_serving(query, data: str) -> None:
    """User selected a serving size — transition to amount input."""
    parts = data.split(":")
    food_id = parts[1]
    serving_id = parts[2] if len(parts) > 2 else None

    user_id = query.from_user.id
    sess = sessions.get_or_create(user_id)

    # Re-fetch details for serving info
    client = FatSecretClient()
    try:
        result = client.get_food_details(food_id)
    except FatSecretError:
        await query.edit_message_text("❌ Could not verify serving. Please try again.")
        return

    food = result.get("food", {})
    name = food.get("food_name", "Unknown")
    servings = food.get("servings", {}).get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]

    sel = next((s for s in servings if str(s.get("serving_id")) == serving_id), None)
    if not sel and servings:
        sel = servings[0]

    if not sel:
        await query.edit_message_text(f"✅ *{name}* logged!")
        sess.reset()
        return

    desc = sel.get("serving_description", "")
    kcal = float(sel.get("calories", 0) or 0)
    protein = float(sel.get("protein", 0) or 0)
    fat = float(sel.get("fat", 0) or 0)
    carbs = float(sel.get("carbohydrate", 0) or 0)

    # Extract grams from metric_serving_amount / metric_serving_unit
    grams = float(sel.get("metric_serving_amount", 0) or 0)
    unit = (sel.get("metric_serving_unit", "") or "").lower()
    if unit == "ml" or unit == "мл":
        grams = grams  # 1:1 for water-based
    elif unit == "kg" or unit == "кг":
        grams *= 1000
    # If grams is 0, try parsing from description
    if grams == 0:
        grams = _parse_grams_from_desc(desc)

    sess.select_food(food_id, name)
    sess.select_serving(
        serving_id=serving_id or "",
        description=desc,
        grams=grams,
        calories=kcal,
        protein=protein,
        fat=fat,
        carbs=carbs,
    )

    hint = ""
    if sess.default_serving_size:
        hint = f"\n(e.g., `{sess.default_serving_size}`)"

    await query.edit_message_text(
        f"✅ *{name}*\n📏 {desc}\n"
        f"🔥 {kcal:.0f} kcal | P:{protein:.1f}g F:{fat:.1f}g C:{carbs:.1f}g\n\n"
        f"⚖️ How much did you eat?{hint}\n\n"
        "Type a weight (150g, 0.2kg), volume (200ml), or pieces (2 pcs).\n"
        "Or tap /cancel to abort.",
        parse_mode="Markdown",
    )


def _parse_grams_from_desc(desc: str) -> float:
    """Try to extract grams from a serving description string."""
    import re

    m = re.search(r"\(?(\d+)\s*g\)?", desc, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return 0.0


def build_app(token: str) -> Application:
    """Create and configure the PTB Application with all handlers."""
    # Wire shared state
    _wire_handlers()

    app = Application.builder().token(token).build()

    # Simple commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("photo", photo_cmd))
    app.add_handler(CommandHandler("barcode", barcode_cmd))
    app.add_handler(CommandHandler("setamount", setamount_cmd))
    app.add_handler(CommandHandler("cancel", amount_cancel))

    # Amount input — ConversationHandler catches text after serving selection
    app.add_handler(_build_amount_conv())

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(_callback_handler))

    # Catch-all — send any photo to photo analysis
    app.add_handler(MessageHandler(filters.PHOTO, photo_cmd))

    return app


def _build_amount_conv() -> ConversationHandler:
    """Build the ConversationHandler for amount input.

    It catches text messages when a user session is in 'awaiting_amount' state.
    """
    return ConversationHandler(
        entry_points=[],  # entered via session state, not a command
        states={
            AWAITING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _amount_text_handler),
            ],
        },
        fallbacks=[CommandHandler("cancel", amount_cancel)],
        name="amount_input",
        persistent=False,
        allow_reentry=True,
    )


async def _amount_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Route text messages to amount_received if session is in awaiting_amount."""
    user_id = update.effective_user.id
    sess = sessions.get_or_create(user_id)

    if sess.state != "awaiting_amount":
        return ConversationHandler.END

    return await amount_received(update, context)


def main() -> None:
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        logger.error("BOT_TOKEN env var is not set.  Exiting.")
        return

    app = build_app(token)
    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
