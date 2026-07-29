"""Handle /help command."""
from telegram import Update
from telegram.ext import ContextTypes


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "📖 *Available commands*\n\n"
        "/start — Greeting and intro\n"
        "/help  — This menu\n"
        "/add \\<product\\> — Log a food item (coming soon)\n"
        "/photo — Analyse a meal photo (coming soon)\n"
        "/barcode \\<code\\> — Look up a barcode (coming soon)\n"
        "/log — View today's meal log (coming soon)\n\n"
        "The bot uses the FatSecret API for nutrition data and an AI model "
        "for meal photo analysis."
    )
    await update.message.reply_markdown(msg)
