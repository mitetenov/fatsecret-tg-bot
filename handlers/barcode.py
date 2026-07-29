"""/barcode handler — decode barcode from photo and look up product."""

from __future__ import annotations

import logging

from PIL import Image
from pyzbar.pyzbar import decode
from telegram import Update
from telegram.ext import ContextTypes

from fatsecret_client import FatSecretClient, FatSecretError, FatSecretNotFoundError

logger = logging.getLogger(__name__)


async def barcode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Decode a barcode from a photo and look up the product on FatSecret."""
    if not update.message.photo:
        await update.message.reply_text(
            "📸 Please *send a photo* of a barcode.\n"
            "Make sure the barcode is clearly visible and well-lit."
        )
        return

    await update.message.reply_text("🔍 Scanning barcode...")

    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
    except Exception as exc:
        logger.error("Failed to download barcode photo: %s", exc)
        await update.message.reply_text("❌ Could not download the photo.")
        return

    # Decode barcode with pyzbar
    try:
        image = Image.open(__import__("io").BytesIO(bytes(image_bytes)))
        barcodes = decode(image)
    except Exception as exc:
        logger.warning("Failed to decode image: %s", exc)
        await update.message.reply_text(
            "❌ Could not process the image. Please send a clear barcode photo."
        )
        return

    if not barcodes:
        await update.message.reply_text(
            "❌ No barcode found in the image.\n"
            "Make sure the barcode is well-lit and clearly visible, then try again."
        )
        return

    barcode_data = barcodes[0].data.decode("utf-8").strip()
    logger.info("Decoded barcode: %s", barcode_data)

    # Look up on FatSecret
    client = FatSecretClient()
    try:
        result = client.find_by_barcode(barcode_data)
    except FatSecretNotFoundError:
        await update.message.reply_text(
            f"❌ Barcode `{barcode_data}` not found in the FatSecret database."
        )
        return
    except FatSecretError as exc:
        logger.error("FatSecret barcode lookup error: %s", exc)
        await update.message.reply_text(f"❌ Lookup failed: {exc}")
        return

    food = result.get("food", {})
    name = food.get("food_name", "Unknown product")
    brand = food.get("brand_name", "")
    fid = food.get("food_id", "")

    text = (
        f"📦 **Barcode:** `{barcode_data}`\n\n"
        f"🍽 *{name}*\n"
    )
    if brand:
        text += f"🏭 {brand}\n"

    servings = food.get("servings", {}).get("serving", [])
    if servings:
        s = servings[0] if isinstance(servings, list) else servings
        text += (
            f"\n📏 {s.get('serving_description', '')}\n"
            f"🔥 Calories: {s.get('calories', '—')} kcal\n"
            f"💪 Protein: {s.get('protein', '—')}g\n"
            f"🧈 Fat: {s.get('fat', '—')}g\n"
            f"🍞 Carbs: {s.get('carbohydrate', '—')}g\n"
        )

    from keyboards import build_confirm_keyboard
    kb = build_confirm_keyboard(str(fid))
    await update.message.reply_text(text, reply_markup=kb)
