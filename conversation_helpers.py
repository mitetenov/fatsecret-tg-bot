"""Shared conversation helpers for multi-step food logging flows.

Provides the common quantity → unit → log sequence used by /add, /photo,
and /barcode conversation handlers.
"""

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

import database

logger = logging.getLogger(__name__)

# ── Conversation states (shared across all food flows) ──────────────

(
    SELECTING_FOOD,       # 0 – user picks a search result
    CONFIRMING_FOOD,      # 1 – user confirms photo/barcode result
    ENTERING_QUANTITY,    # 2 – user enters a quantity number
    SELECTING_UNIT,       # 3 – user picks a unit from inline keyboard
) = range(4)

# ── Unit choices ────────────────────────────────────────────────────

UNITS = [
    ("serving", "🍽 Serving"),
    ("g", "⚖️ g"),
    ("ml", "🥛 ml"),
    ("oz", "📏 oz"),
    ("cup", "☕ Cup"),
    ("piece", "🍪 Piece"),
    ("tbsp", "🥄 Tbsp"),
]


def _build_unit_keyboard() -> InlineKeyboardMarkup:
    """Build an inline keyboard with unit choices (3 per row)."""
    buttons = [
        InlineKeyboardButton(label, callback_data=f"select_unit:{value}")
        for value, label in UNITS
    ]
    # Arrange in rows of 3
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    # Add a cancel button on its own row
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_log")])
    return InlineKeyboardMarkup(rows)


def _build_confirm_keyboard(prefix: str = "confirm_food") -> InlineKeyboardMarkup:
    """Build confirm / cancel inline keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Log this", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_log"),
            ]
        ]
    )


# ── Shared handlers ─────────────────────────────────────────────────


async def ask_quantity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    product_name: str,
) -> int:
    """Ask the user to enter a quantity, then transition to ENTERING_QUANTITY.

    Call this from any flow after a food is selected/confirmed.
    """
    context.user_data["pending_product_name"] = product_name
    await (update.callback_query.message if update.callback_query else update.message).reply_text(
        f"How many servings of *{product_name}* would you like to log?\n\n"
        "Reply with a number, e.g. `1`, `2.5`, or `0.5`.",
        parse_mode="Markdown",
    )
    return ENTERING_QUANTITY


async def receive_quantity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Handle a quantity number input; transition to unit selection."""
    text = update.message.text.strip() if update.message else ""
    try:
        qty = float(text)
        if qty <= 0:
            await update.message.reply_text(
                "⚠️ Please enter a positive number, e.g. `1` or `0.5`.",
                parse_mode="Markdown",
            )
            return ENTERING_QUANTITY
    except ValueError:
        await update.message.reply_text(
            "⚠️ That doesn't look like a number. Please enter a quantity like `1` or `1.5`.",
            parse_mode="Markdown",
        )
        return ENTERING_QUANTITY

    context.user_data["pending_quantity"] = qty
    product_name = context.user_data.get("pending_product_name", "this item")

    reply_markdown = (
        f"📏 *{qty}* × *{product_name}* — which unit?\n\n"
        "Pick one or type your own:"
    )

    await update.message.reply_text(
        reply_markdown,
        reply_markup=_build_unit_keyboard(),
        parse_mode="Markdown",
    )
    return SELECTING_UNIT


async def select_unit_and_log(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Handle unit selection callback; log the meal and finish."""
    query = update.callback_query
    await query.answer()

    # Check for cancel
    if query.data == "cancel_log":
        await query.edit_message_text("❌ Logging cancelled.")
        _clear_pending(context)
        return ConversationHandler.END

    unit = query.data.removeprefix("select_unit:")

    product_name = context.user_data.get("pending_product_name", "Unknown")
    quantity = context.user_data.get("pending_quantity", 1.0)
    user_id = update.effective_user.id if update.effective_user else 0

    # Gather nutritional info stored from earlier steps
    per_serving = context.user_data.get("pending_nutrition", {})

    try:
        entry = database.log_meal(
            user_id=user_id,
            product_name=product_name,
            brand=context.user_data.get("pending_brand"),
            quantity=quantity,
            unit=unit,
            calories=per_serving.get("calories"),
            fat=per_serving.get("fat"),
            carbs=per_serving.get("carbs"),
            protein=per_serving.get("protein"),
        )
    except Exception:
        logger.exception("Failed to log meal")
        await query.edit_message_text("❌ Failed to save log entry. Please try again.")
        _clear_pending(context)
        return ConversationHandler.END

    # Build confirmation message
    lines = [f"✅ *Logged!*"]
    lines.append(f"  🍽 {quantity} × {unit} of *{product_name}*")
    if entry.calories is not None:
        lines.append(f"  🔥 {entry.calories:.0f} kcal")
    macros = []
    if entry.protein is not None:
        macros.append(f"P:{entry.protein:.1f}g")
    if entry.fat is not None:
        macros.append(f"F:{entry.fat:.1f}g")
    if entry.carbs is not None:
        macros.append(f"C:{entry.carbs:.1f}g")
    if macros:
        lines.append(f"  {'  '.join(macros)}")

    # Show daily totals
    totals = database.get_daily_totals(user_id)
    if totals["entries"] > 0:
        lines.append("")
        lines.append("📊 *Today's totals:*")
        lines.append(f"  🔥 {totals['calories']:.0f} kcal")
        lines.append(
            f"  🥩 P:{totals['protein']:.1f}g  "
            f"🧈 F:{totals['fat']:.1f}g  "
            f"🍞 C:{totals['carbs']:.1f}g"
        )
        lines.append(f"  📋 {totals['entries']} entries")

    await query.edit_message_text(
        "\n".join(lines), parse_mode="Markdown"
    )

    _clear_pending(context)
    return ConversationHandler.END


async def cancel_log(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Cancel the current logging flow."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Logging cancelled.")
    elif update.message:
        await update.message.reply_text("❌ Logging cancelled.")
    _clear_pending(context)
    return ConversationHandler.END


# ── Helpers ─────────────────────────────────────────────────────────


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear temporary conversation data."""
    for key in (
        "pending_product_name",
        "pending_quantity",
        "pending_nutrition",
        "pending_brand",
        "pending_food",
    ):
        context.user_data.pop(key, None)


def extract_nutrition(food: dict, servings_index: int = 0) -> dict:
    """Extract per-serving nutrition from a FatSecret food dict."""
    servings = food.get("servings", {}).get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]
    if not servings:
        return {}
    idx = min(servings_index, len(servings) - 1)
    s = servings[idx]
    return {
        "calories": _to_float(s.get("calories")),
        "fat": _to_float(s.get("fat")),
        "carbs": _to_float(s.get("carbohydrate")),
        "protein": _to_float(s.get("protein")),
        "serving_description": s.get("serving_description", "1 serving"),
    }


def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_food_info_text(food: dict, index: int = 1) -> str:
    """Build a user-readable text for a food search result."""
    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    food_id = food.get("food_id", "")
    food_type = food.get("food_type", "")

    label = f"*{name}*"
    if brand:
        label += f" _({brand})_"
    if food_type:
        label += f" — {food_type}"

    lines = [f"{index}. {label}"]

    nut = extract_nutrition(food)
    if nut:
        parts = []
        if nut.get("calories"):
            parts.append(f"🔥 {nut['calories']:.0f} kcal")
        if nut.get("protein"):
            parts.append(f"🥩 P:{nut['protein']:.1f}g")
        if nut.get("fat"):
            parts.append(f"🧈 F:{nut['fat']:.1f}g")
        if nut.get("carbs"):
            parts.append(f"🍞 C:{nut['carbs']:.1f}g")
        if parts:
            lines.append(f"   {'  '.join(parts)}")
        if nut.get("serving_description"):
            lines.append(f"   📏 _{nut['serving_description']}_")

    return "\n".join(lines)


def build_food_info_lines(
    foods: list[dict],
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Build info text and inline keyboard rows for search results."""
    lines = ["🔍 *Search results:*\n"]
    keyboard_rows = []
    for i, food in enumerate(foods, 1):
        lines.append(build_food_info_text(food, i))
        lines.append("")  # blank line between items
        food_id = food.get("food_id", str(i))
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"{i}. {food.get('food_name', 'Unknown')[:40]}",
                    callback_data=f"select_food:{food_id}:{i - 1}",
                )
            ]
        )
    keyboard_rows.append(
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_log")]
    )
    return "\n".join(lines), keyboard_rows
