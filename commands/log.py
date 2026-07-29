"""/log command — view today's meals, edit or delete entries.

Shows a summary of today's logged meals with inline keyboard to:
- Edit quantity/unit for any entry
- Delete an entry
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import database
from conversation_helpers import (
    ENTERING_QUANTITY,
    SELECTING_UNIT,
    cancel_log,
    receive_quantity,
    select_unit_and_log,
)

logger = logging.getLogger(__name__)

# States for editing a log entry
EDITING_QUANTITY = ENTERING_QUANTITY  # reuse
EDITING_UNIT = SELECTING_UNIT        # reuse


# ── Entry: show today's log ─────────────────────────────────────────


async def log_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /log — show today's meal entries."""
    message = update.message
    if message is None:
        return

    user_id = update.effective_user.id if update.effective_user else 0

    try:
        entries = database.get_today_logs(user_id)
        totals = database.get_daily_totals(user_id)
    except Exception:
        logger.exception("Failed to query meal logs")
        await message.reply_text("❌ Could not retrieve meal log. Please try again.")
        return

    if not entries:
        await message.reply_text(
            "📋 No meals logged today.\n\n"
            "Use /add, /photo, or /barcode to start logging!",
        )
        return

    # Build summary
    lines = ["📋 *Today's meals*"]
    if totals:
        lines.append(
            f"🔥 {totals['calories']:.0f} kcal  "
            f"🥩 P:{totals['protein']:.1f}g  "
            f"🧈 F:{totals['fat']:.1f}g  "
            f"🍞 C:{totals['carbs']:.1f}g"
        )
    lines.append("")

    keyboard_rows = []
    for entry in entries:
        name = entry.product_name
        if entry.brand:
            name += f" ({entry.brand})"
        cal = f" {entry.calories:.0f} kcal" if entry.calories else ""
        lines.append(
            f"• *{entry.quantity}* {entry.unit} {name}{cal}"
        )

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"✏️ Edit #{entry.id}",
                    callback_data=f"edit_entry:{entry.id}",
                ),
                InlineKeyboardButton(
                    f"🗑 Delete #{entry.id}",
                    callback_data=f"delete_entry:{entry.id}",
                ),
            ]
        )

    keyboard_rows.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_log")]
    )

    await message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
        parse_mode="Markdown",
    )


# ── Callback handlers ───────────────────────────────────────────────


async def log_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle inline keyboard actions from /log message."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data == "refresh_log":
        # Re-show log
        await _refresh_log(update, context)
        return ConversationHandler.END

    if data.startswith("delete_entry:"):
        entry_id = int(data.split(":")[1])
        await _delete_entry(update, context, entry_id)
        return ConversationHandler.END

    if data.startswith("edit_entry:"):
        entry_id = int(data.split(":")[1])
        return await _start_edit(update, context, entry_id)

    return ConversationHandler.END


async def _refresh_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-fetch and update the log message."""
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else 0

    try:
        entries = database.get_today_logs(user_id)
        totals = database.get_daily_totals(user_id)
    except Exception:
        await query.edit_message_text("❌ Could not refresh log.")
        return

    if not entries:
        await query.edit_message_text(
            "📋 No meals logged today.\n\n"
            "Use /add, /photo, or /barcode to start logging!"
        )
        return

    lines = ["📋 *Today's meals*"]
    if totals:
        lines.append(
            f"🔥 {totals['calories']:.0f} kcal  "
            f"🥩 P:{totals['protein']:.1f}g  "
            f"🧈 F:{totals['fat']:.1f}g  "
            f"🍞 C:{totals['carbs']:.1f}g"
        )
    lines.append("")

    keyboard_rows = []
    for entry in entries:
        name = entry.product_name
        if entry.brand:
            name += f" ({entry.brand})"
        cal = f" {entry.calories:.0f} kcal" if entry.calories else ""
        lines.append(
            f"• *{entry.quantity}* {entry.unit} {name}{cal}"
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"✏️ Edit #{entry.id}",
                    callback_data=f"edit_entry:{entry.id}",
                ),
                InlineKeyboardButton(
                    f"🗑 Delete #{entry.id}",
                    callback_data=f"delete_entry:{entry.id}",
                ),
            ]
        )

    keyboard_rows.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_log")]
    )

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
        parse_mode="Markdown",
    )


async def _delete_entry(
    update: Update, context: ContextTypes.DEFAULT_TYPE, entry_id: int
) -> None:
    """Delete a meal log entry and refresh the view."""
    query = update.callback_query
    try:
        deleted = database.delete_log_entry(entry_id)
        if deleted:
            await query.answer("Deleted ✅", show_alert=False)
        else:
            await query.answer("Entry not found", show_alert=False)
    except Exception:
        logger.exception("Failed to delete log entry")
        await query.answer("Delete failed", show_alert=False)

    await _refresh_log(update, context)


async def _start_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, entry_id: int
) -> int:
    """Begin editing a log entry — ask for new quantity."""
    query = update.callback_query

    try:
        entry = database.get_log_entry(entry_id)
    except Exception:
        logger.exception("Failed to fetch log entry")
        await query.edit_message_text("❌ Could not fetch entry.")
        return ConversationHandler.END

    if entry is None:
        await query.edit_message_text("❌ Entry not found.")
        return ConversationHandler.END

    context.user_data["editing_entry_id"] = entry_id
    context.user_data["pending_product_name"] = entry.product_name
    context.user_data["pending_brand"] = entry.brand

    # Store per-serving nutrition for recalculation
    if entry.quantity and entry.quantity > 0:
        context.user_data["pending_nutrition"] = {
            "calories": entry.calories / entry.quantity if entry.calories else None,
            "fat": entry.fat / entry.quantity if entry.fat else None,
            "carbs": entry.carbs / entry.quantity if entry.carbs else None,
            "protein": entry.protein / entry.quantity if entry.protein else None,
        }
    else:
        context.user_data["pending_nutrition"] = {}

    await query.edit_message_text(
        f"✏️ Editing *{entry.product_name}* (current: {entry.quantity} {entry.unit})\n\n"
        "Enter a new quantity, e.g. `1`, `2.5`, or `0.5`.",
        parse_mode="Markdown",
    )
    return EDITING_QUANTITY


# ── Edit quantity handler ───────────────────────────────────────────


async def edit_receive_quantity(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle new quantity for editing a log entry."""
    text = update.message.text.strip() if update.message else ""
    try:
        qty = float(text)
        if qty <= 0:
            await update.message.reply_text(
                "⚠️ Please enter a positive number."
            )
            return EDITING_QUANTITY
    except ValueError:
        await update.message.reply_text(
            "⚠️ That doesn't look like a number. Please enter a quantity like `1` or `1.5`."
        )
        return EDITING_QUANTITY

    context.user_data["pending_quantity"] = qty

    # Update the log entry with new quantity
    entry_id = context.user_data.get("editing_entry_id")
    nut = context.user_data.get("pending_nutrition", {})
    try:
        database.update_log_entry(
            entry_id,
            quantity=qty,
            calories=nut.get("calories"),
            fat=nut.get("fat"),
            carbs=nut.get("carbs"),
            protein=nut.get("protein"),
        )
    except Exception:
        logger.exception("Failed to update log entry")
        await update.message.reply_text("❌ Failed to update entry.")
        return ConversationHandler.END

    product_name = context.user_data.get("pending_product_name", "item")
    await update.message.reply_text(
        f"✅ Updated *{product_name}* to {qty} servings.\n"
        "Use /log to view your updated meals.",
        parse_mode="Markdown",
    )
    # Clear edit state
    context.user_data.pop("editing_entry_id", None)
    return ConversationHandler.END


# ── Edit unit handler ───────────────────────────────────────────────


async def edit_select_unit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle unit selection for editing — update the entry."""
    return await select_unit_and_log(update, context)


# ── ConversationHandler builder ─────────────────────────────────────


def build_log_handler() -> ConversationHandler:
    """Return a ConversationHandler for the /log edit flow."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(log_callback, pattern="^edit_entry:"),
        ],
        states={
            EDITING_QUANTITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, edit_receive_quantity
                ),
                CommandHandler("cancel", cancel_log),
            ],
            EDITING_UNIT: [
                CallbackQueryHandler(edit_select_unit, pattern="^select_unit:"),
                CallbackQueryHandler(cancel_log, pattern="^cancel_log$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_log)],
        name="edit_log",
    )
