"""Handle /start command."""
from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    greeting = (
        f"👋 Hello, {user.first_name}!\n\n"
        "I'm your nutrition tracking bot.  I can help you look up food products "
        "from the FatSecret database and log your meals.\n\n"
        "Use /help to see what I can do."
    )
    await update.message.reply_text(greeting)
