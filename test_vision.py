"""Tests for the Vision API client (vision.py)."""

import base64
from unittest.mock import MagicMock, patch

import pytest

from vision import VisionClient, FoodAnalysis


class TestFoodAnalysis:
    """FoodAnalysis dataclass / dict parsing."""

    def test_from_dict_parses_valid_response(self):
        data = {
            "food_name": "Caesar Salad",
            "calories": 450,
            "protein": 30.5,
            "fat": 25.0,
            "carbs": 20.0,
            "serving_size": "1 bowl (300g)",
        }
        result = FoodAnalysis.from_dict(data)
        assert result.food_name == "Caesar Salad"
        assert result.calories == 450
        assert result.protein == 30.5
        assert result.fat == 25.0
        assert result.carbs == 20.0
        assert result.serving_size == "1 bowl (300g)"

    def test_from_dict_handles_missing_fields(self):
        data = {"food_name": "Apple"}
        result = FoodAnalysis.from_dict(data)
        assert result.food_name == "Apple"
        assert result.calories == 0
        assert result.protein == 0.0

    def test_to_dict_roundtrips(self):
        fa = FoodAnalysis(
            food_name="Pizza",
            calories=800,
            protein=35.0,
            fat=40.0,
            carbs=70.0,
            serving_size="1 slice",
        )
        d = fa.to_dict()
        fa2 = FoodAnalysis.from_dict(d)
        assert fa2.food_name == "Pizza"
        assert fa2.calories == 800

    def test_repr_format(self):
        fa = FoodAnalysis(food_name="Burger", calories=600)
        r = repr(fa)
        assert "Burger" in r
        assert "600" in r


class TestVisionClient:
    """VisionClient — OpenRouter GPT-4o Vision integration."""

    @patch("vision.OpenAI")
    def test_analyze_food_returns_structured_result(self, mock_openai_cls):
        """analyze_food() sends an image and returns a FoodAnalysis."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='```json\n{"food_name": "Salad", "calories": 300, "protein": 15.0, "fat": 20.0, "carbs": 10.0, "serving_size": "1 plate"}\n```'
                )
            )
        ]
        mock_client.chat.completions.create.return_value = mock_response

        client = VisionClient(api_key="test-key")
        result = client.analyze_food(b"fake-image-bytes")

        assert result.food_name == "Salad"
        assert result.calories == 300
        assert result.protein == 15.0

        # Verify API was called with the right model and image
        call_args = mock_client.chat.completions.create.call_args
        kwargs = call_args[1]
        assert kwargs["model"] == "openai/gpt-4o"
        messages = kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        user_content = messages[1]["content"]
        assert isinstance(user_content, list)
        assert user_content[0]["type"] == "text"
        assert user_content[1]["type"] == "image_url"
        url = user_content[1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        # Bytes are base64-encoded in the data URI
        assert len(url) > len("data:image/jpeg;base64,")

    @patch("vision.OpenAI")
    def test_analyze_food_uses_custom_model(self, mock_openai_cls):
        """analyze_food() uses the model specified at init."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"food_name": "Test", "calories": 100, "protein": 5.0, "fat": 5.0, "carbs": 5.0, "serving_size": "100g"}'
                )
            )
        ]
        mock_client.chat.completions.create.return_value = mock_response

        client = VisionClient(api_key="test-key", model="google/gemini-2.0-flash-001")
        client.analyze_food(b"img")

        kwargs = mock_client.chat.completions.create.call_args[1]
        assert kwargs["model"] == "google/gemini-2.0-flash-001"

    @patch("vision.OpenAI")
    def test_analyze_food_defaults_api_key_from_env(self, mock_openai_cls):
        """VisionClient() reads API_KEY from env when not passed explicitly."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"food_name": "X", "calories": 0, "protein": 0, "fat": 0, "carbs": 0, "serving_size": ""}'
                )
            )
        ]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("os.environ", {"AI_API_KEY": "env-key"}, clear=True):
            client = VisionClient()
            client.analyze_food(b"img")

        # Verify the client was created with the env key
        mock_openai_cls.assert_called_once()
        call_kwargs = mock_openai_cls.call_args[1]
        assert call_kwargs["api_key"] == "env-key"
