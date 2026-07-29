"""/search handler — search FatSecret database and show results."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from fatsecret_client import FatSecretClient, FatSecretError
from keyboards import build_food_results_keyboard

logger = logging.getLogger(__name__)


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search the FatSecret food database and display results as inline keyboard."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /search <food name>\nExample: `/search chicken breast`"
        )
        return

    query = " ".join(context.args)
    client = _get_client()

    try:
        result = client.search_food(query, max_results=10)
        foods = result.get("foods_search", {}).get("food", [])
    except FatSecretError as exc:
        logger.error("FatSecret search error for %r: %s", query, exc)
        await update.message.reply_text(f"❌ Search failed: {exc}")
        return

    if not foods:
        await update.message.reply_text(
            f"🔍 No results found for *{query}*.\nTry a different search term."
        )
        return

    total = result.get("foods_search", {}).get("total_results", len(foods))
    kb = build_food_results_keyboard(foods)

    await update.message.reply_text(
        f"🔍 Found {total} results for *{query}*:\n"
        "Tap a food to see details:",
        reply_markup=kb,
    )


def _get_client() -> FatSecretClient:
    """Lazy-init the FatSecret client (reads env vars on first use)."""
    return FatSecretClient()
