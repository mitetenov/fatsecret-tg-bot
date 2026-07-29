"""Image processing for food photos and barcode scanning.

Photo recognition
------------------
Sends the image to Gemini Vision API to identify food items, then cross-
references with FatSecret for accurate nutritional data.

Fallback chain: Gemini Vision → FatSecret image-recognition (if premium
credentials) → user-friendly error.

Barcode scanning
----------------
Uses pyzbar (free, offline) to decode EAN-13 / UPC-A / EAN-8 barcodes from
a photo, normalises the code to GTIN-13, and looks it up in FatSecret.

Fallback chain: pyzbar → error (can't decode).
"""

import io
import json
import logging
import re
from typing import Optional

import requests
from PIL import Image

import fatsecret_client as fs

logger = logging.getLogger(__name__)

# ── Gemini Vision integration ────────────────────────────────────────

_GEMINI_FOOD_PROMPT = (
    "Identify all distinct food items visible in this photo. "
    "Return ONLY a JSON array of objects — no other text. "
    "Each object must have these keys:\n"
    '  "food_name": short, common name (e.g. "grilled chicken breast"),\n'
    '  "estimated_quantity": number (e.g. 1.5),\n'
    '  "unit": "serving" | "piece" | "g" | "ml" | "cup" | "tbsp",\n'
    '  "estimated_calories": integer calories for the identified portion.\n'
    "If no food is visible return an empty array: []"
)

_GEMINI_LIST_PROMPT = (
    "List every distinct food item visible in this photo. "
    "For each food, output a short common name on its own line — nothing else. "
    "If no food is visible, output a single line: NONE"
)


def _extract_json(text: str) -> list[dict]:
    """Robustly extract a JSON array from a model response."""
    text = text.strip()
    # Find the first '[' and last ']'
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    # Try to parse the whole response as JSON
    return json.loads(text)


def _identify_foods_with_gemini(
    image_data: bytes, api_key: str, model: str = "gemini-2.0-flash"
) -> list[dict]:
    """Send *image_data* to Gemini Vision and return a list of food dicts."""
    try:
        import google.generativeai as genai
    except ImportError:
        logger.error("google-generativeai not installed – cannot use Gemini Vision")
        raise RuntimeError("Gemini Vision support not installed") from None

    genai.configure(api_key=api_key)
    gemini = genai.GenerativeModel(model)

    response = gemini.generate_content(
        [
            _GEMINI_FOOD_PROMPT,
            {"mime_type": "image/jpeg", "data": image_data},
        ]
    )

    raw = (response.text or "").strip()
    logger.debug("Gemini raw response: %s", raw[:300])

    if not raw:
        return []

    return _extract_json(raw)


# ── Barcode scanning ─────────────────────────────────────────────────


def _normalise_gtin13(barcode: str) -> str:
    """Convert UPC-A / EAN-8 / EAN-13 to a 13-digit GTIN-13 string.

    FatSecret barcode endpoints require GTIN-13 (13 digits).
    UPC-A (12 digits) → left-padded with ``"0"``.
    EAN-8 (8 digits)  → left-padded with ``"00000"``.
    """
    code = re.sub(r"\s+", "", barcode)
    if len(code) == 12:
        return "0" + code
    if len(code) == 8:
        return "00000" + code
    if len(code) == 13:
        return code
    raise ValueError(
        f"Cannot normalise barcode '{code}' (length {len(code)}). "
        "Expected UPC-A (12), EAN-8 (8), or EAN-13 (13)."
    )


def _scan_barcode_pyzbar(image_data: bytes) -> Optional[tuple[str, str]]:
    """Decode the first product barcode from *image_data* using pyzbar.

    Returns ``(data, type)`` — e.g. ``("0078742075581", "EAN13")`` — or
    ``None`` if no barcode is found.
    """
    try:
        from pyzbar.pyzbar import decode
    except ImportError:
        logger.error("pyzbar not installed – cannot scan barcodes")
        raise RuntimeError("Barcode scanning (pyzbar) not installed") from None

    img = Image.open(io.BytesIO(image_data))
    results = decode(img)

    if not results:
        return None

    # Prefer product-barcode symbologies
    preferred = {"EAN13", "UPCA", "EAN8", "UPCE", "I25", "CODE128"}
    for r in results:
        if r.type in preferred:
            return r.data.decode("utf-8"), r.type

    # Fall back to the first result
    r = results[0]
    return r.data.decode("utf-8"), r.type


# ── Public API ───────────────────────────────────────────────────────

# Cache the Gemini API key so callers don't need to pass it every time.
_gemini_api_key: Optional[str] = None
_gemini_model: str = "gemini-2.0-flash"


def configure(api_key: Optional[str] = None, model: Optional[str] = None) -> None:
    """One-time configuration of the image processing module."""
    global _gemini_api_key, _gemini_model
    if api_key is not None:
        _gemini_api_key = api_key
    if model is not None:
        _gemini_model = model


# ── Barcode pipeline ─────────────────────────────────────────────────


class BarcodeResult:
    """Result from a barcode scanning + lookup operation."""

    def __init__(
        self,
        barcode: str,
        barcode_type: str,
        gtin: str,
        food: Optional[dict],
        error: Optional[str] = None,
    ) -> None:
        self.barcode = barcode
        self.barcode_type = barcode_type
        self.gtin = gtin
        self.food = food  # FatSecret food dict or None
        self.error = error  # user-facing error message or None

    @property
    def success(self) -> bool:
        return self.error is None and self.food is not None


def process_barcode_photo(image_data: bytes, region: str = "US") -> BarcodeResult:
    """Full barcode pipeline: decode → normalise → FatSecret lookup.

    Returns a ``BarcodeResult``.
    """
    # Step 1 — decode barcode with pyzbar
    result = _scan_barcode_pyzbar(image_data)
    if result is None:
        return BarcodeResult(
            barcode="",
            barcode_type="",
            gtin="",
            food=None,
            error=(
                "🔎 No barcode detected in the image. "
                "Make sure the barcode is clear, well-lit, and fills most of the frame."
            ),
        )
    raw_code, barcode_type = result

    # Step 2 — normalise to GTIN-13
    try:
        gtin = _normalise_gtin13(raw_code)
    except ValueError as exc:
        return BarcodeResult(
            barcode=raw_code,
            barcode_type=barcode_type,
            gtin="",
            food=None,
            error=f"⚠️ Unsupported barcode format: {exc}",
        )

    # Step 3 — FatSecret lookup
    try:
        food = fs.lookup_barcode(gtin, region=region)
        return BarcodeResult(barcode=raw_code, barcode_type=barcode_type, gtin=gtin, food=food)
    except fs.FatSecretError as exc:
        if exc.code == "211":
            return BarcodeResult(
                barcode=raw_code,
                barcode_type=barcode_type,
                gtin=gtin,
                food=None,
                error=f"📭 Barcode *{gtin}* not found in FatSecret database.",
            )
        return BarcodeResult(
            barcode=raw_code,
            barcode_type=barcode_type,
            gtin=gtin,
            food=None,
            error=f"⚠️ FatSecret lookup failed: {exc}",
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Network error during barcode lookup: %s", exc)
        return BarcodeResult(
            barcode=raw_code,
            barcode_type=barcode_type,
            gtin=gtin,
            food=None,
            error="🌐 Network error while looking up the barcode. Please try again later.",
        )
    except Exception:
        logger.exception("Unexpected error in barcode pipeline")
        return BarcodeResult(
            barcode=raw_code,
            barcode_type=barcode_type,
            gtin=gtin,
            food=None,
            error="❌ An unexpected error occurred. Please try again.",
        )


# ── Food photo pipeline ──────────────────────────────────────────────


class FoodRecognitionResult:
    """Result from food photo recognition."""

    def __init__(
        self,
        items: Optional[list[dict]] = None,
        foods: Optional[list[dict]] = None,
        error: Optional[str] = None,
    ) -> None:
        self.items = items or []   # raw Gemini food items
        self.foods = foods or []   # matched FatSecret foods
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None and len(self.foods) > 0

    @property
    def no_food_detected(self) -> bool:
        return self.error is None and len(self.foods) == 0


def process_food_photo(
    image_data: bytes,
    region: str = "US",
    language: str = "en",
    api_key: Optional[str] = None,
) -> FoodRecognitionResult:
    """Full food photo pipeline: Gemini Vision → FatSecret search.

    Fallback chain:
    1. Gemini Vision identifies food → FatSecret search for each item.
    2. If Gemini fails, try FatSecret built-in image recognition (premium).
    3. If that also fails, return error.

    Returns a ``FoodRecognitionResult``.
    """
    key = api_key or _gemini_api_key

    # ── Step 1: Gemini Vision ────────────────────────────────────
    try:
        if not key:
            raise RuntimeError("No Gemini API key configured")

        items = _identify_foods_with_gemini(image_data, key, model=_gemini_model)
    except RuntimeError as exc:
        # Gemini key missing — try FatSecret built-in
        logger.warning("Gemini Vision unavailable: %s — trying FatSecret fallback", exc)
        return _fatsecret_image_fallback(image_data, region, language)
    except Exception:
        logger.exception("Gemini Vision failed — trying FatSecret fallback")
        return _fatsecret_image_fallback(image_data, region, language)

    if not items:
        return FoodRecognitionResult(error=None)  # no food detected — not an error

    # ── Step 2: FatSecret search for each identified item ────────
    foods = []
    for item in items:
        name = item.get("food_name", "").strip()
        if not name:
            continue
        try:
            results = fs.search_foods(name, region=region, language=language, max_results=1)
            if results:
                foods.append(results[0])
        except Exception:
            logger.warning("FatSecret search failed for '%s'", name)

    # If Gemini found items but FatSecret couldn't match any, still
    # return the Gemini items so the caller can show estimated data.
    if not foods:
        return FoodRecognitionResult(
            items=items,
            error=(
                "🤖 I identified these items but couldn't find exact matches "
                "in FatSecret's database:\n"
                + "\n".join(
                    f"  • {i['food_name']} (~{i.get('estimated_calories', '?')} kcal)"
                    for i in items
                )
            ),
        )

    return FoodRecognitionResult(items=items, foods=foods)


def _fatsecret_image_fallback(
    image_data: bytes, region: str, language: str
) -> FoodRecognitionResult:
    """Try FatSecret's built-in image recognition as a fallback."""
    try:
        result = fs.recognize_food_image(image_data, region=region, language=language)
    except fs.FatSecretError as exc:
        if exc.code == "211":
            return FoodRecognitionResult(error=None)  # no food detected
        return FoodRecognitionResult(error=f"⚠️ Image recognition failed: {exc}")
    except Exception:
        logger.exception("FatSecret image recognition fallback failed")
        return FoodRecognitionResult(
            error="❌ Food recognition is currently unavailable. Please try again later."
        )

    if not result or not result.get("food_id"):
        return FoodRecognitionResult(error=None)

    # Wrap in expected list shape for downstream consumers
    return FoodRecognitionResult(items=[], foods=[result])


# ── Formatting helpers ───────────────────────────────────────────────


def format_food_item(food: dict, item_index: int = 0) -> str:
    """Format a single food dict as a user-readable message line."""
    return fs.format_nutrition(food)


def format_barcode_result(result: BarcodeResult) -> str:
    """Build a user-facing Telegram message string from a barcode result."""
    if result.error:
        return result.error
    if not result.food:
        return f"📭 Barcode *{result.gtin}* not found in any database."

    food = result.food
    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    label = f"{name} ({brand})" if brand else name

    lines = [f"✅ Barcode *{result.gtin}* matched:", f"  🏷 *{label}*"]

    # Try to extract nutrition from the first serving
    servings = food.get("servings", {}).get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]
    if servings:
        s = servings[0]
        desc = s.get("serving_description", "1 serving")
        lines.append(f"  📏 {desc}")
        kcal = s.get("calories")
        protein = s.get("protein")
        fat = s.get("fat")
        carbs = s.get("carbohydrate")
        if kcal is not None:
            lines.append(f"  🔥 {kcal} kcal")
        if protein is not None:
            lines.append(f"  🥩 Protein {protein}g")
        if fat is not None:
            lines.append(f"  🧈 Fat {fat}g")
        if carbs is not None:
            lines.append(f"  🍞 Carbs {carbs}g")

    return "\n".join(lines)


def format_food_result(result: FoodRecognitionResult) -> str:
    """Build a user-facing Telegram message string from a recognition result."""
    if result.error:
        return result.error

    if result.no_food_detected:
        return (
            "🤔 I don't see any food in this photo. "
            "Try taking a closer, well-lit photo of the meal."
        )

    lines = ["📸 *Food recognised:*"]
    for i, food in enumerate(result.foods):
        lines.append(format_food_item(food, i))
    return "\n".join(lines)
