"""/start handler — welcome message."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with available commands."""
    user = update.effective_user
    name = user.first_name if user else "there"

    text = (
        f"👋 Hello, {name}!\n\n"
        "I'm the **FatSecret Nutrition Bot**.  I can help you track what you eat.\n\n"
        "📝 **Commands:**\n"
        "/search <food> — search the FatSecret database\n"
        "/photo — send a food photo, I'll estimate KBJU with AI\n"
        "/barcode — send a barcode photo, I'll look it up\n"
        "/help — show this message again\n\n"
        "Just send me a photo any time and I'll analyse it! 🍎"
    )
    await update.message.reply_text(text)
