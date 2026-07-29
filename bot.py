"""FatSecret Telegram Bot — main entry point.

Wires up python-telegram-bot v20+ with all command handlers,
a ConversationHandler for inline-keyboard workflows, and the
callback query handler for food selection and confirmation.
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from handlers.barcode import barcode_cmd
from handlers.help import help_cmd
from handlers.photo import photo_cmd, sessions
from handlers.search import search_cmd
from handlers.start import start
from keyboards import build_serving_keyboard
from fatsecret_client import FatSecretClient, FatSecretError

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states
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
    """User confirmed — log the food entry."""
    user_id = query.from_user.id
    sess = sessions.get_or_create(user_id)

    food_name = sess.selected_food_name or sess.analysis.food_name if sess.analysis else "this item"

    # TODO: persist to user's daily log
    sess.reset()

    await query.edit_message_text(
        f"✅ Logged: *{food_name}*\n\nKeep tracking! 🎯",
        parse_mode="Markdown",
    )


async def _handle_cancel(query) -> None:
    """User cancelled."""
    user_id = query.from_user.id
    sessions.get_or_create(user_id).reset()
    await query.edit_message_text("❌ Cancelled.  Try /search or send another photo!")


async def _handle_serving(query, data: str) -> None:
    """User selected a specific serving size."""
    parts = data.split(":")
    food_id = parts[1]
    serving_id = parts[2] if len(parts) > 2 else None

    user_id = query.from_user.id
    sess = sessions.get_or_create(user_id)

    # Re-fetch details for confirmation
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

    if sel:
        desc = sel.get("serving_description", "")
        kcal = sel.get("calories", "—")
        protein = sel.get("protein", "—")
        fat = sel.get("fat", "—")
        carbs = sel.get("carbohydrate", "—")
        sess.select_food(food_id, name)
        sess.selected_serving_id = serving_id

        await query.edit_message_text(
            f"✅ *{name}*\n📏 {desc}\n"
            f"🔥 {kcal} kcal | P:{protein}g F:{fat}g C:{carbs}g\n\n"
            f"Logged! 🎯",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(f"✅ *{name}* logged!")

    sess.reset()


def build_app(token: str) -> Application:
    """Create and configure the PTB Application with all handlers."""
    app = Application.builder().token(token).build()

    # Simple commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("photo", photo_cmd))
    app.add_handler(CommandHandler("barcode", barcode_cmd))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(_callback_handler))

    # Catch-all — send any photo to photo analysis
    app.add_handler(
        MessageHandler(filters.PHOTO, photo_cmd)
    )

    return app


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
