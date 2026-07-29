"""Main entry point — Telegram bot for FatSecret nutrition tracking.

Wires up:
- /start, /help  — simple commands
- /add           — ConversationHandler (search → select → quantity → unit → log)
- /photo         — ConversationHandler (photo → confirm → quantity → unit → log)
- /barcode       — ConversationHandler (barcode → confirm → quantity → unit → log)
- /log           — CommandHandler (show today's log with inline keyboard actions)
- Photo messages  — try barcode first, fall back to food recognition
"""

import logging
import sys

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from commands.add import build_add_handler
from commands.barcode import build_barcode_handler
from commands.help import help_cmd
from commands.log import build_log_handler, log_callback, log_show
from commands.photo import build_photo_handler
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


# ── Direct photo message handler ────────────────────────────────────


async def direct_photo_handler(update, context):
    """Handle direct photo messages (no command caption).

    Tries barcode scanning first (fast, offline), then falls back
    to food photo recognition.
    """
    import image_processing
    from commands.photo import photo_start

    message = update.message
    if message is None or not message.photo:
        return

    photos = message.photo
    photo_file = photos[-1]
    file = await context.bot.get_file(photo_file.file_id)
    image_bytes = bytes(await file.download_as_bytearray())

    # Try barcode first
    result = image_processing.process_barcode_photo(image_bytes)
    if result.success:
        from commands.barcode import barcode_start
        # Delegate to the barcode flow — but this won't work cleanly with ConversationHandler.
        # Instead, just show the result with inline keyboard for confirm.
        from conversation_helpers import CONFIRMING_FOOD
        return await barcode_start(update, context)

    # Fall back to food recognition via /photo flow
    return await photo_start(update, context)


# ── Main ────────────────────────────────────────────────────────────


def main() -> None:
    cfg = get_config()

    missing = cfg.validate()
    if missing:
        logger.error(
            "Missing required environment variables: %s", ", ".join(missing)
        )
        sys.exit(1)

    logger.info("Starting FatSecret Telegram Bot ...")

    # Init database schema
    init_db()

    # Configure image processing module
    ip_configure(
        api_key=cfg.gemini_api_key or cfg.ai_api_key,
        model=(
            cfg.ai_model if cfg.ai_model.startswith("gemini") else "gemini-2.0-flash"
        ),
    )

    # Build the Application
    app = Application.builder().token(cfg.bot_token).build()

    # ── Simple command handlers ──────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # ── /log command (show today's entries) ──────────────────────
    app.add_handler(CommandHandler("log", log_show))

    # ── /log inline keyboard handlers (refresh, delete) ──────────
    app.add_handler(
        CallbackQueryHandler(log_callback, pattern="^(refresh_log|delete_entry:)")
    )

    # ── ConversationHandlers (multi-step flows) ──────────────────
    # Order matters: more specific handlers first
    app.add_handler(build_add_handler())
    app.add_handler(build_photo_handler())
    app.add_handler(build_barcode_handler())
    app.add_handler(build_log_handler())  # edit flow

    # ── Direct photo handler for uncaptioned photos ──────────────
    # This is a fallback: if user sends a photo without a command caption,
    # try barcode first, then food recognition.
    app.add_handler(MessageHandler(filters.PHOTO, direct_photo_handler))

    logger.info("Bot is polling ...")
    app.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
