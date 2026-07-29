"""/photo handler — AI food recognition and KBJU estimation."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from keyboards import build_confirm_keyboard
from session import SessionManager
from vision import VisionClient

logger = logging.getLogger(__name__)

# Shared session manager (populated by bot.py)
sessions = SessionManager()


async def photo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Analyse a food photo with vision AI and prompt user to confirm KBJU."""
    if not update.message.photo:
        await update.message.reply_text(
            "📸 Please *send a photo* of your food.\n"
            "Use /photo and attach an image, or just send a photo and I'll analyse it."
        )
        return

    user_id = update.effective_user.id
    await update.message.reply_text("🔍 Analysing your photo with AI...")

    try:
        # Download the largest photo
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
    except Exception as exc:
        logger.error("Failed to download photo: %s", exc)
        await update.message.reply_text("❌ Could not download the photo. Please try again.")
        return

    try:
        vision = VisionClient()
        analysis = vision.analyze_food(bytes(image_bytes))
    except Exception as exc:
        logger.error("Vision API error: %s", exc)
        await update.message.reply_text(
            "❌ AI analysis failed.  Please try again later."
        )
        return

    if analysis.food_name == "unknown" or analysis.calories == 0:
        await update.message.reply_text(
            "🤔 I couldn't identify any food in that photo.  "
            "Try a clearer image or use /search instead."
        )
        return

    # Store analysis in session
    sess = sessions.get_or_create(user_id)
    sess.set_analysis(analysis)

    text = (
        f"📊 **AI Estimate**\n\n"
        f"🍽 *{analysis.food_name}*\n"
        f"📏 {analysis.serving_size}\n\n"
        f"🔥 Calories: **{analysis.calories} kcal**\n"
        f"💪 Protein: {analysis.protein:.1f}g\n"
        f"🧈 Fat: {analysis.fat:.1f}g\n"
        f"🍞 Carbs: {analysis.carbs:.1f}g\n\n"
        f"Is this correct?  I'll search FatSecret to find a matching product."
    )

    food_id = str(hash(analysis.food_name) % 10**9)  # placeholder for now
    kb = build_confirm_keyboard(food_id)
    await update.message.reply_text(text, reply_markup=kb)
