"""Vision API client — analyses food photos via OpenRouter (GPT-4o / Gemini)."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)


@dataclass
class FoodAnalysis:
    """Structured KBJU estimate from a food photo."""

    food_name: str = ""
    calories: int = 0
    protein: float = 0.0
    fat: float = 0.0
    carbs: float = 0.0
    serving_size: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FoodAnalysis:
        return cls(
            food_name=str(data.get("food_name", "")),
            calories=int(data.get("calories", 0)),
            protein=float(data.get("protein", 0.0)),
            fat=float(data.get("fat", 0.0)),
            carbs=float(data.get("carbs", 0.0)),
            serving_size=str(data.get("serving_size", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "food_name": self.food_name,
            "calories": self.calories,
            "protein": self.protein,
            "fat": self.fat,
            "carbs": self.carbs,
            "serving_size": self.serving_size,
        }


class VisionClient:
    """Sends food photos to an OpenAI-compatible vision model for KBJU estimation.

    Defaults to OpenRouter's ``openai/gpt-4o``; override via ``model``.
    """

    SYSTEM_PROMPT = """You are a nutrition assistant. Analyse the food in this photo.
Return a JSON object with these fields:
- food_name: short descriptive name of the dish/meal
- calories: estimated total calories (integer, kcal)
- protein: estimated grams of protein (float)
- fat: estimated grams of fat (float)
- carbs: estimated grams of carbohydrates (float)
- serving_size: approximate portion size (e.g. "1 plate (400g)", "1 cup (250ml)")

Wrap the JSON in a ```json code fence.  Be conservative — prefer to underestimate
rather than overestimate.  If you cannot see food, set food_name to "unknown" and
all numeric fields to 0."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "openai/gpt-4o",
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        key = api_key or os.environ.get("AI_API_KEY", "")
        if not key:
            raise ValueError(
                "AI_API_KEY is required.  Pass api_key= or set the env var."
            )
        self.model = model
        self._client = OpenAI(api_key=key, base_url=base_url)

    def analyze_food(self, image_bytes: bytes) -> FoodAnalysis:
        """Send ``image_bytes`` (JPEG / PNG) to the vision model and return KBJU."""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:image/jpeg;base64,{b64}"

        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What food is in this photo?  Estimate its KBJU."},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            max_tokens=500,
            temperature=0.2,
        )

        text = resp.choices[0].message.content or ""
        data = _extract_json(text)
        logger.debug("Vision model returned: %s", data)
        return FoodAnalysis.from_dict(data)


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object from text, even inside ``` fences."""
    # Strip ```json fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        # Try to find a JSON object with a regex
        match = re.search(r"\{[^{}]*\}", cleaned)
        if match:
            try:
                return json.loads(match.group())  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass
    logger.warning("Could not parse vision model response: %s", text[:200])
    return {}
