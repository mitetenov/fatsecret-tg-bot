"""/setamount handler and amount-input ConversationHandler.

After a serving is selected the bot asks the user how much they ate.
The user replies with a weight / volume / count (e.g. "150g", "200ml",
"2 pieces") and the bot scales the serving KBJU accordingly, logs the
entry in the food_log table, and shows a confirmation.
"""

from __future__ import annotations

import logging
import re
from typing import cast

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db import CacheDB
from session import SessionManager

logger = logging.getLogger(__name__)

# -- Conversation states ----------------------------------------------------

AWAITING_AMOUNT = 1

# Shared state (populated by bot.py)
sessions: SessionManager | None = None
db: CacheDB | None = None


def _get_sessions() -> SessionManager:
    assert sessions is not None, "sessions not wired"
    return sessions


def _get_db() -> CacheDB:
    assert db is not None, "db not wired"
    return db


# -- Amount parsing ---------------------------------------------------------

_UNIT_PATTERN = re.compile(
    r"^\s*([\d.,]+)\s*(g|grams?|gramm?|г|гр|kg|kgs?|кг|ml|мл|millilitres?|milliliters?|l|litres?|liters?|л|pcs?|pieces?|шт|штук?|units?|servings?|порц(?:ий|ия|ии)?)?\s*$",
    re.IGNORECASE,
)

_ML_TO_G: dict[str, float] = {
    "ml": 1.0,
    "мл": 1.0,
    "millilitre": 1.0,
    "milliliter": 1.0,
    "l": 1000.0,
    "л": 1000.0,
    "litre": 1000.0,
    "liter": 1000.0,
}


def parse_amount(text: str, serving_grams: float = 0.0) -> float | None:
    """Parse user input like '150g', '200ml', '2 pieces' into grams.

    Returns grams (float) or None if unparseable.
    - 'g'/'grams'/etc. → value is already in grams
    - 'kg' → value * 1000
    - 'ml'/'l' → treated as grams (1:1 for water-based foods)
    - 'pc(s)'/'pieces'/'units' → value * serving_grams
    """
    m = _UNIT_PATTERN.match(text)
    if not m:
        return None

    value_str = m.group(1).replace(",", ".")
    try:
        value = float(value_str)
    except ValueError:
        return None
    if value <= 0:
        return None

    unit_raw = (m.group(2) or "").strip().lower()

    if unit_raw in ("kg", "kgs", "кг"):
        return value * 1000.0
    if unit_raw in ("l", "л", "litre", "litres", "liter", "liters"):
        return value * 1000.0
    if unit_raw in _ML_TO_G:
        return value * _ML_TO_G[unit_raw]
    if unit_raw in ("pc", "pcs", "piece", "pieces", "шт", "штук", "unit", "units"):
        if serving_grams > 0:
            return value * serving_grams
        return None  # Can't convert pieces without serving size
    if unit_raw in ("serving", "servings", "порция", "порций", "порции"):
        if serving_grams > 0:
            return value * serving_grams
        return None

    # Default: assume grams
    return value


# -- Conversation handlers --------------------------------------------------


def build_amount_handler() -> ConversationHandler:
    """Return a ConversationHandler for the amount-input stage."""

    return ConversationHandler(
        entry_points=[],  # entered programmatically via bot-level state
        states={
            AWAITING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received),
            ],
        },
        fallbacks=[],
        name="amount_input",
        persistent=False,
        allow_reentry=True,
    )


async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask the user how much they ate; returns AWAITING_AMOUNT state."""
    sess = _get_sessions().get_or_create(update.effective_user.id)

    hint = ""
    if sess.default_serving_size:
        hint = f"\n(e.g., `{sess.default_serving_size}`)"

    await update.effective_message.reply_text(
        f"⚖️ How much did you eat?{hint}\n\n"
        "Type a weight (150g, 0.2kg), volume (200ml), or pieces (2 pcs).\n"
        "Or tap /cancel to abort.",
    )
    return AWAITING_AMOUNT


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse the user's amount, calculate KBJU, log, and confirm."""
    user_id = update.effective_user.id
    sess = _get_sessions().get_or_create(user_id)

    if sess.state != "awaiting_amount":
        return ConversationHandler.END

    text = update.message.text.strip()
    grams = parse_amount(text, sess.selected_serving_grams)

    if grams is None:
        await update.message.reply_text(
            "❓ I didn't understand that. Please type a number with a unit.\n"
            "Examples: `150g`, `200ml`, `2 pcs`, `1 serving`\n"
            "Or tap /cancel to abort.",
        )
        return AWAITING_AMOUNT

    sess.set_amount(text, grams)
    kbju = sess.get_calculated_kbju()

    # Persist to DB
    db_ = _get_db()
    db_.log_food(
        user_id=user_id,
        food_id=sess.selected_food_id or "",
        food_name=sess.selected_food_name or "unknown",
        serving_description=sess.selected_serving_desc or "",
        amount_raw=sess.amount_raw or "",
        amount_grams=sess.amount_grams,
        servings_multiplier=sess.servings_multiplier,
        calories=kbju["calories"],
        protein=kbju["protein"],
        fat=kbju["fat"],
        carbs=kbju["carbs"],
    )

    # Daily totals for extra motivation
    totals = db_.get_daily_totals(user_id)

    desc_line = f"📏 {sess.selected_serving_desc}" if sess.selected_serving_desc else ""
    multiplier_line = ""
    if sess.servings_multiplier != 1.0:
        multiplier_line = f"⚖️ Amount: {text} (~{grams:.0f}g, ×{sess.servings_multiplier:.2f})"

    msg = (
        f"✅ Logged: *{sess.selected_food_name}*\n"
        f"{desc_line}\n"
        f"{multiplier_line}\n"
        f"\n"
        f"🔥 Calories: *{kbju['calories']:.0f} kcal*\n"
        f"💪 Protein: {kbju['protein']:.1f}g\n"
        f"🧈 Fat: {kbju['fat']:.1f}g\n"
        f"🍞 Carbs: {kbju['carbs']:.1f}g\n"
        f"\n"
        f"📊 Today so far:\n"
        f"🔥 {totals['calories']:.0f} kcal · "
        f"💪 {totals['protein']:.0f}g · "
        f"🧈 {totals['fat']:.0f}g · "
        f"🍞 {totals['carbs']:.0f}g\n"
        f"\n"
        f"Keep tracking! 🎯"
    )

    sess.reset()
    await update.message.reply_text(msg)

    return ConversationHandler.END


async def amount_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the amount input flow."""
    user_id = update.effective_user.id
    _get_sessions().get_or_create(user_id).reset()
    await update.message.reply_text("❌ Cancelled. Try /search or send another photo!")
    return ConversationHandler.END


# -- /setamount handler -----------------------------------------------------


async def setamount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the user's default serving size for future entries.

    Usage: /setamount 100g
           /setamount 1 serving
           /setamount 200ml
    """
    user_id = update.effective_user.id
    sess = _get_sessions().get_or_create(user_id)

    if not context.args:
        current = sess.default_serving_size
        await update.message.reply_text(
            f"⚖️ Your current default amount: *{current}*\n\n"
            "Change it: `/setamount 150g`\n"
            "Examples: `100g`, `200ml`, `1 serving`, `2 pcs`",
        )
        return

    new_amount = " ".join(context.args).strip()
    sess.set_default_serving(new_amount)

    await update.message.reply_text(
        f"✅ Default amount set to *{new_amount}*\n"
        "I'll suggest this amount whenever you log food.",
    )
