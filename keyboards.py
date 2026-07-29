"""Inline keyboard builders for Telegram bot."""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_food_results_keyboard(
    foods: list[dict[str, Any]],
) -> InlineKeyboardMarkup:
    """Build an inline keyboard from ``foods`` search results.

    Each button shows ``food_name (brand_name)`` and has callback
    ``select:<food_id>``.
    """
    buttons: list[list[InlineKeyboardButton]] = []
    for food in foods:
        fid = str(food["food_id"])
        name = food.get("food_name", "Unknown")
        brand = food.get("brand_name", "")
        label = f"{name} ({brand})" if brand else name
        # Keep it under ~60 chars for readability
        if len(label) > 60:
            label = label[:57] + "..."
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"select:{fid}")]
        )
    return InlineKeyboardMarkup(buttons)


def build_confirm_keyboard(food_id: str) -> InlineKeyboardMarkup:
    """Build a Yes/No confirmation keyboard for photo analysis results."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes, log it", callback_data=f"confirm:{food_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]
        ]
    )


def build_serving_keyboard(
    servings: list[dict[str, Any]], food_id: str
) -> InlineKeyboardMarkup:
    """Build a serving-size selection keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []
    for s in servings:
        sid = str(s["serving_id"])
        desc = s.get("serving_description", sid)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=desc, callback_data=f"serving:{food_id}:{sid}"
                )
            ]
        )
    return InlineKeyboardMarkup(buttons)
