"""FatSecret Platform API client — OAuth 2.0 client credentials flow.

Handles: food search, barcode lookup, food details, and image recognition.
"""

import base64
import logging
import time
from io import BytesIO
from typing import Optional

import requests

from config import get_config

logger = logging.getLogger(__name__)

# ── OAuth 2.0 token cache ────────────────────────────────────────────

_token_cache: Optional[dict] = None


def _get_access_token() -> str:
    """Obtain or refresh an OAuth 2.0 access token.

    Tokens are cached in memory; auto-refreshed when expired or within 60 s
    of expiry.
    """
    global _token_cache

    cfg = get_config()

    if _token_cache and _token_cache["expires_at"] > time.time() + 60:
        return _token_cache["access_token"]

    credentials = f"{cfg.fatsecret_client_id}:{cfg.fatsecret_client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()

    logger.info("Requesting new FatSecret OAuth 2.0 access token ...")

    resp = requests.post(
        "https://oauth.fatsecret.com/connect/token",
        data={
            "grant_type": "client_credentials",
            "scope": "premier image-recognition",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache = {
        "access_token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 86400),
    }
    logger.info("FatSecret access token obtained.")
    return _token_cache["access_token"]


def _bearer_header() -> dict:
    return {"Authorization": f"Bearer {_get_access_token()}"}


def _check_fatsecret_error(data: dict, context: str = "API request") -> dict:
    """FatSecret returns HTTP 200 for API errors — check for 'error' key."""
    if "error" in data:
        code = data["error"].get("code", "unknown")
        message = data["error"].get("message", "Unknown error")
        logger.error("%s: error %s — %s", context, code, message)
        raise FatSecretError(message, code)
    return data


class FatSecretError(Exception):
    """Raised when FatSecret API returns an error response."""

    def __init__(self, message: str, code: str = "unknown") -> None:
        self.code = code
        super().__init__(f"FatSecret error {code}: {message}")


# ── Public API ───────────────────────────────────────────────────────


def search_foods(
    query: str,
    region: str = "US",
    language: str = "en",
    max_results: int = 5,
) -> list[dict]:
    """Search FatSecret database for foods matching *query*.

    Returns a list of food dicts (food_id, food_name, brand_name, food_type,
    food_url).  Pass *region* and *language* to target a specific locale.
    """
    resp = requests.get(
        "https://platform.fatsecret.com/rest/foods/search/v5",
        params={
            "search_expression": query,
            "region": region,
            "language": language,
            "max_results": max_results,
            "format": "json",
        },
        headers=_bearer_header(),
        timeout=30,
    )
    resp.raise_for_status()
    data = _check_fatsecret_error(resp.json(), f"Food search for '{query}'")

    foods_search = data.get("foods_search")
    if foods_search is not None:
        foods = foods_search.get("food", [])
    else:
        logger.warning(
            "'foods_search' key not in response, falling back to 'foods' key"
        )
        foods = data.get("foods", {}).get("food", [])
    if isinstance(foods, dict):  # single result is not wrapped in a list
        foods = [foods]
    return foods


def get_food_details(food_id: str, region: str = "US") -> dict:
    """Retrieve full nutritional details for a specific food.

    Returns the ``food`` dict including servings with serving_id, calories,
    protein, carbohydrate, fat, and other micronutrients.
    """
    resp = requests.get(
        "https://platform.fatsecret.com/rest/food/v5",
        params={
            "food_id": food_id,
            "region": region,
            "format": "json",
        },
        headers=_bearer_header(),
        timeout=30,
    )
    resp.raise_for_status()
    data = _check_fatsecret_error(resp.json(), f"Food details for id {food_id}")
    return data.get("food", {})


def lookup_barcode(barcode: str, region: str = "US") -> dict:
    """Look up a GTIN-13 barcode in the FatSecret database.

    Returns the full food object (same shape as ``get_food_details``) or
    raises ``FatSecretError`` with code 211 if not found.
    """
    resp = requests.post(
        "https://platform.fatsecret.com/rest/server.api",
        data={
            "method": "food.find_id_for_barcode.v2",
            "barcode": barcode,
            "region": region,
            "format": "json",
        },
        headers=_bearer_header(),
        timeout=30,
    )
    resp.raise_for_status()
    data = _check_fatsecret_error(resp.json(), f"Barcode lookup for '{barcode}'")
    return data.get("food", data)


def recognize_food_image(
    image_data: bytes,
    region: str = "US",
    language: str = "en",
    include_food_data: bool = True,
) -> dict:
    """Use FatSecret's built-in image recognition (premier plan required).

    Returns the ``food_response`` dict with food_id, food_name, suggested
    serving, and nutritional content if *include_food_data* is True.
    Raises ``FatSecretError`` code 211 when no food is detected.
    """
    # Downscale if over ~750 KB
    if len(image_data) > 750_000:
        from PIL import Image

        img = Image.open(BytesIO(image_data))
        img.thumbnail((512, 512))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        image_data = buf.getvalue()

    b64 = base64.b64encode(image_data).decode("ascii")

    resp = requests.post(
        "https://platform.fatsecret.com/rest/image-recognition/v1",
        json={
            "image_b64": b64,
            "include_food_data": include_food_data,
            "region": region,
            "language": language,
        },
        headers=_bearer_header(),
        timeout=60,
    )
    resp.raise_for_status()
    data = _check_fatsecret_error(
        resp.json(), "FatSecret image recognition"
    )
    return data.get("food_response", data)


def format_nutrition(food: dict) -> str:
    """Format a food dict into a human-readable single-line nutrition string.

    Extracts the first serving's calories / macros when available.
    """
    name = food.get("food_name", "Unknown food")
    brand = food.get("brand_name")
    label = f"{name} ({brand})" if brand else name

    servings = food.get("servings", {}).get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]
    if not servings:
        return f"🍽 {label} — nutrition data unavailable"

    s = servings[0]
    desc = s.get("serving_description", "1 serving")
    parts = [f"🍽 *{label}*", f"_{desc}_"]

    kcal = s.get("calories")
    protein = s.get("protein")
    fat = s.get("fat")
    carbs = s.get("carbohydrate")

    if kcal is not None:
        parts.append(f"🔥 {kcal} kcal")
    if protein is not None:
        parts.append(f"🥩 P:{protein}g")
    if fat is not None:
        parts.append(f"🧈 F:{fat}g")
    if carbs is not None:
        parts.append(f"🍞 C:{carbs}g")
    return "  ".join(parts)
