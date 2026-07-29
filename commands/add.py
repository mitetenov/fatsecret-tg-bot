"""Handle /add command — placeholder."""
from telegram import Update
from telegram.ext import ContextTypes


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args) if context.args else "…"
    await update.message.reply_text(
        f"🛠 /add is not wired up yet.  You searched for: *{query}*\n\n"
        "Soon you'll be able to log foods like `/add 1 banana` or `/add 100g chicken breast`.",
        parse_mode="Markdown",
    )
