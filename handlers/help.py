"""/help handler — show available commands."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all available commands."""
    text = (
        "🍽 **FatSecret Bot — Help**\n\n"
        "**Commands:**\n"
        "/start — welcome message\n"
        "/help — this help\n"
        "/search <food> — search the FatSecret food database\n"
        "/photo — send a food photo, I'll estimate calories (KBJU)\n"
        "/barcode — send a barcode photo, I'll look up the product\n"
        "/setamount <size> — set your default serving size (e.g. 100g)\n"
        "/cancel — cancel the current operation\n\n"
        "**How to use:**\n"
        "1. `/search chicken breast` — find foods\n"
        "2. Tap a result to see servings\n"
        "3. Tap a serving, then enter how much you ate (e.g. `150g`)\n"
        "4. Send a photo to `/photo` or `/barcode`\n"
        "5. Use `/setamount 100g` to save your default portion\n\n"
        "Happy tracking! 🥗"
    )
    await update.message.reply_text(text)
