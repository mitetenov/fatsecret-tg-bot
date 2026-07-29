"""Main entry point — Telegram bot for FatSecret nutrition tracking."""

import logging
import sys

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from commands.add import add
from commands.barcode import barcode, barcode_photo_handler
from commands.help import help_cmd
from commands.log import log
from commands.photo import photo, photo_message_handler
from commands.start import start
from config import get_config
from database import init_db
from image_processing import configure as ip_configure

# ── Logging ─────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Main ────────────────────────────────────────────────────────────


def main() -> None:
    cfg = get_config()

    missing = cfg.validate()
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    logger.info("Starting FatSecret Telegram Bot ...")

    # Init database schema
    init_db()

    # Configure image processing module
    ip_configure(
        api_key=cfg.gemini_api_key or cfg.ai_api_key,
        model=cfg.ai_model if cfg.ai_model.startswith("gemini") else "gemini-2.0-flash",
    )

    # Build the Application
    app = Application.builder().token(cfg.bot_token).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("photo", photo))
    app.add_handler(CommandHandler("barcode", barcode))
    app.add_handler(CommandHandler("log", log))

    # Register photo message handlers (for direct photo sends)
    app.add_handler(MessageHandler(filters.PHOTO, barcode_photo_handler))

    logger.info("Bot is polling ...")
    app.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
