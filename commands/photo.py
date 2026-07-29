"""Handle /photo command — analyse meal photos with AI.

Accepts a photo sent to the bot (as a caption to /photo or as a direct
photo message), identifies food items via Gemini Vision, and returns
nutritional data from FatSecret.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

import image_processing

logger = logging.getLogger(__name__)


async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /photo — expects a photo attached to the message.

    The user can send ``/photo`` with a photo attached, and the bot will
    analyse the meal and return recognised foods with nutrition data.
    """
    message = update.message
    if message is None:
        return

    photos = message.photo
    if not photos:
        await message.reply_text(
            "📸 Send a photo of your meal along with the /photo command. "
            "Example: attach a photo and caption it `/photo`."
        )
        return

    # Telegram sends multiple sizes; grab the largest
    photo_file = photos[-1]
    file = await context.bot.get_file(photo_file.file_id)

    # Download the photo to memory
    image_bytes = await file.download_as_bytearray()
    image_bytes = bytes(image_bytes)

    await message.reply_text("🔍 Analysing your meal photo …")

    # ── Process the photo ─────────────────────────────────────────
    try:
        result = image_processing.process_food_photo(image_bytes)
    except Exception:
        logger.exception("Unexpected error in /photo handler")
        await message.reply_text(
            "❌ An unexpected error occurred while analysing the photo. "
            "Please try again later."
        )
        return

    # ── Build and send response ───────────────────────────────────
    response_text = image_processing.format_food_result(result)

    await message.reply_markdown(response_text)


async def photo_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for direct photo messages (without /photo command).

    When a user sends a plain photo, treat it the same as /photo.
    """
    await photo(update, context)
