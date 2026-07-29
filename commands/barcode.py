"""Handle /barcode command — scan barcode photos and look up products.

Accepts a photo of a barcode sent with /barcode, decodes the barcode
with pyzbar, normalises it to GTIN-13, and looks up the product in
FatSecret.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

import image_processing

logger = logging.getLogger(__name__)


async def barcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /barcode — expects a barcode photo attached to the message.

    The user sends ``/barcode`` with a photo of a barcode (UPC/EAN).
    The bot decodes it and returns nutrition data from FatSecret.
    """
    message = update.message
    if message is None:
        return

    photos = message.photo
    if not photos:
        # No photo attached — maybe the user typed a barcode number
        if context.args:
            code = context.args[0].strip()
            await _lookup_text_barcode(message, code)
            return
        await message.reply_text(
            "📸 Send a photo of a barcode along with the /barcode command, "
            "or type the barcode number: `/barcode 0078742075581`\n\n"
            "I support UPC-A, EAN-13, and EAN-8 barcodes."
        )
        return

    # Telegram sends multiple sizes; grab the largest
    photo_file = photos[-1]
    file = await context.bot.get_file(photo_file.file_id)

    image_bytes = await file.download_as_bytearray()
    image_bytes = bytes(image_bytes)

    await message.reply_text("🔎 Scanning barcode …")

    # ── Process the barcode photo ─────────────────────────────────
    try:
        result = image_processing.process_barcode_photo(image_bytes)
    except Exception:
        logger.exception("Unexpected error in /barcode handler")
        await message.reply_text(
            "❌ An unexpected error occurred while scanning the barcode. "
            "Please try again."
        )
        return

    response_text = image_processing.format_barcode_result(result)
    await message.reply_markdown(response_text)


async def _lookup_text_barcode(message, code: str) -> None:
    """Look up a barcode number typed by the user."""
    import fatsecret_client as fs

    try:
        gtin = image_processing._normalise_gtin13(code)
    except ValueError as exc:
        await message.reply_text(str(exc))
        return

    await message.reply_text(f"🔎 Looking up barcode *{gtin}* …")

    try:
        food = fs.lookup_barcode(gtin)
        # Build a BarcodeResult for formatting
        from image_processing import BarcodeResult

        result = BarcodeResult(barcode=code, barcode_type="manual", gtin=gtin, food=food)
        response_text = image_processing.format_barcode_result(result)
        await message.reply_markdown(response_text)
    except fs.FatSecretError as exc:
        if exc.code == "211":
            await message.reply_text(f"📭 Barcode *{gtin}* not found in FatSecret database.")
        else:
            await message.reply_text(f"⚠️ FatSecret lookup failed: {exc}")
    except Exception:
        logger.exception("Barcode text lookup failed")
        await message.reply_text("❌ Failed to look up the barcode. Please try again.")


async def barcode_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for direct photo messages — try barcode scan if no /photo intent."""
    # If the user sends a photo without a command, try barcode first
    # (most food photos will be sent with /photo anyway)
    message = update.message
    if message is None:
        return

    photos = message.photo
    if not photos:
        return

    photo_file = photos[-1]
    file = await context.bot.get_file(photo_file.file_id)
    image_bytes = await file.download_as_bytearray()
    image_bytes = bytes(image_bytes)

    # Try barcode first (fast, offline)
    result = image_processing.process_barcode_photo(image_bytes)
    if result.success:
        await message.reply_markdown(image_processing.format_barcode_result(result))
        return

    # Fall back to food photo recognition
    await photo(update, context)
