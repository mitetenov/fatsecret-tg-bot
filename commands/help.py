"""Handle /help command."""
from telegram import Update
from telegram.ext import ContextTypes


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "📖 *Available commands*\n\n"
        "/start — Greeting and intro\n"
        "/help  — This menu\n"
        "/add <product> — Search product in FatSecret and log it\n"
        "/photo — Send a meal photo → AI recognises food → log it\n"
        "/barcode <code|photo> — Scan barcode → look up product → log it\n"
        "/log — View today's meal log (edit/delete entries)\n\n"
        "Just send a photo without a command — the bot will try to scan\n"
        "a barcode first, then fall back to food recognition."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
