"""Handle /log command — placeholder."""
from telegram import Update
from telegram.ext import ContextTypes


async def log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 /log will show your daily meal entries once the logging feature is built."
    )
