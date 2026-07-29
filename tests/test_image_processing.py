"""Unit tests for image_processing — all external APIs are mocked."""

import json
from unittest import mock

import pytest
import requests

import fatsecret_client as fs
import image_processing as ip


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _configure_module(monkeypatch):
    """Ensure the module is configured with a fake API key."""
    ip.configure(api_key="fake-gemini-key")
    monkeypatch.setattr(fs, "_token_cache", None)


# ── GTIN-13 normalisation ────────────────────────────────────────────


class TestNormaliseGtin13:
    def test_upc_a_12_digit(self):
        assert ip._normalise_gtin13("078742075581") == "0078742075581"

    def test_ean_13(self):
        assert ip._normalise_gtin13("0078742075581") == "0078742075581"

    def test_ean_8(self):
        assert ip._normalise_gtin13("12345678") == "0000012345678"

    def test_strips_whitespace(self):
        assert ip._normalise_gtin13("  078742075581  ") == "0078742075581"

    def test_invalid_length_raises(self):
        with pytest.raises(ValueError, match="length 5"):
            ip._normalise_gtin13("12345")


# ── Barcode scanning (pyzbar) ───────────────────────────────────────


class TestScanBarcodePyzbar:
    # Minimal 1x1 white PNG — valid image data PIL can open
    _VALID_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
        b"\x00\x05\xfe\x02\xfe\r\xefF\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def test_returns_data_and_type(self, monkeypatch):
        """pyzbar finds an EAN-13 barcode."""
        fake_barcode = mock.MagicMock()
        fake_barcode.type = "EAN13"
        fake_barcode.data = b"0078742075581"

        monkeypatch.setattr("pyzbar.pyzbar.decode", mock.MagicMock(return_value=[fake_barcode]))

        result = ip._scan_barcode_pyzbar(self._VALID_PNG)
        assert result == ("0078742075581", "EAN13")

    def test_no_barcode_returns_none(self, monkeypatch):
        """pyzbar finds nothing."""
        monkeypatch.setattr("pyzbar.pyzbar.decode", mock.MagicMock(return_value=[]))

        result = ip._scan_barcode_pyzbar(self._VALID_PNG)
        assert result is None

    def test_prefers_ean_over_code128(self, monkeypatch):
        """When multiple barcodes found, prefer EAN type."""
        barcode1 = mock.MagicMock()
        barcode1.type = "QRCODE"
        barcode1.data = b"http://example.com"

        barcode2 = mock.MagicMock()
        barcode2.type = "EAN13"
        barcode2.data = b"0078742075581"

        monkeypatch.setattr(
            "pyzbar.pyzbar.decode",
            mock.MagicMock(return_value=[barcode1, barcode2]),
        )

        result = ip._scan_barcode_pyzbar(self._VALID_PNG)
        # QRCODE is not in the preferred set, so EAN13 should be picked
        assert result == ("0078742075581", "EAN13")

    def test_import_error_raises(self, monkeypatch):
        """pyzbar not installed — import fails."""
        # Mock the import to fail inside _scan_barcode_pyzbar
        original_import = __builtins__["__import__"]

        def _mock_import(name, *args, **kwargs):
            if name == "pyzbar.pyzbar" or name == "pyzbar":
                raise ImportError("No module named 'pyzbar'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _mock_import)

        with pytest.raises(RuntimeError, match="not installed"):
            ip._scan_barcode_pyzbar(self._VALID_PNG)


# ── Barcode pipeline ────────────────────────────────────────────────


class TestProcessBarcodePhoto:
    def test_full_success(self, monkeypatch):
        """Scan → normalise → FatSecret lookup all succeed."""
        monkeypatch.setattr(ip, "_scan_barcode_pyzbar", mock.MagicMock(return_value=("0078742075581", "EAN13")))

        fake_food = {"food_id": "999", "food_name": "Protein Bar", "brand_name": "Quest"}
        monkeypatch.setattr(fs, "lookup_barcode", mock.MagicMock(return_value=fake_food))

        result = ip.process_barcode_photo(b"image")
        assert result.success
        assert result.barcode == "0078742075581"
        assert result.food["food_name"] == "Protein Bar"

    def test_no_barcode_detected(self, monkeypatch):
        """pyzbar finds nothing — returns error result."""
        monkeypatch.setattr(ip, "_scan_barcode_pyzbar", mock.MagicMock(return_value=None))

        result = ip.process_barcode_photo(b"image")
        assert not result.success
        assert result.error is not None
        assert "No barcode" in result.error

    def test_unsupported_barcode_format(self, monkeypatch):
        """pyzbar finds a barcode with invalid length."""
        monkeypatch.setattr(ip, "_scan_barcode_pyzbar", mock.MagicMock(return_value=("12345", "CODE128")))

        result = ip.process_barcode_photo(b"image")
        assert not result.success
        assert "Unsupported barcode format" in result.error

    def test_fatsecret_not_found(self, monkeypatch):
        """Barcode decoded but FatSecret returns 211."""
        monkeypatch.setattr(ip, "_scan_barcode_pyzbar", mock.MagicMock(return_value=("0078742075581", "EAN13")))
        monkeypatch.setattr(fs, "lookup_barcode", mock.MagicMock(side_effect=fs.FatSecretError("Not found", "211")))

        result = ip.process_barcode_photo(b"image")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_network_error(self, monkeypatch):
        """Network error during FatSecret API call."""
        monkeypatch.setattr(ip, "_scan_barcode_pyzbar", mock.MagicMock(return_value=("0078742075581", "EAN13")))
        monkeypatch.setattr(fs, "lookup_barcode", mock.MagicMock(side_effect=requests.ConnectionError("Timeout")))

        result = ip.process_barcode_photo(b"image")
        assert not result.success
        assert "Network error" in result.error


# ── Food photo pipeline ──────────────────────────────────────────────


class TestProcessFoodPhoto:
    def _mock_gemini_response(self, monkeypatch, items):
        """Configure _identify_foods_with_gemini to return *items*."""
        monkeypatch.setattr(ip, "_identify_foods_with_gemini", mock.MagicMock(return_value=items))

    def test_success_with_fatsecret_matches(self, monkeypatch):
        """Gemini identifies foods → FatSecret finds them."""
        items = [
            {"food_name": "grilled chicken", "estimated_quantity": 1, "unit": "serving", "estimated_calories": 350},
            {"food_name": "steamed rice", "estimated_quantity": 200, "unit": "g", "estimated_calories": 260},
        ]
        self._mock_gemini_response(monkeypatch, items)

        chicken_food = {"food_id": "1", "food_name": "Chicken Breast"}
        rice_food = {"food_id": "2", "food_name": "White Rice"}
        monkeypatch.setattr(fs, "search_foods", mock.MagicMock(side_effect=[[chicken_food], [rice_food]]))

        result = ip.process_food_photo(b"image-data")
        assert result.success
        assert len(result.foods) == 2
        assert result.foods[0]["food_name"] == "Chicken Breast"
        assert result.foods[1]["food_name"] == "White Rice"

    def test_no_food_detected(self, monkeypatch):
        """Gemini returns empty array."""
        self._mock_gemini_response(monkeypatch, [])

        result = ip.process_food_photo(b"image-data")
        assert not result.success
        assert result.no_food_detected
        assert result.error is None

    def test_gemini_fails_falls_back_to_fatsecret(self, monkeypatch):
        """Gemini raises exception → try FatSecret built-in image recognition."""
        monkeypatch.setattr(ip, "_identify_foods_with_gemini", mock.MagicMock(side_effect=RuntimeError("API error")))

        fatsecret_result = {"food_id": "55", "food_name": "Salad"}
        monkeypatch.setattr(fs, "recognize_food_image", mock.MagicMock(return_value=fatsecret_result))

        result = ip.process_food_photo(b"image-data")
        assert result.success
        assert result.foods[0]["food_name"] == "Salad"

    def test_gemini_found_but_fatsecret_no_match(self, monkeypatch):
        """Gemini identifies foods but FatSecret can't find any matches."""
        items = [{"food_name": "obscure dish", "estimated_quantity": 1, "unit": "serving", "estimated_calories": 500}]
        self._mock_gemini_response(monkeypatch, items)

        monkeypatch.setattr(fs, "search_foods", mock.MagicMock(return_value=[]))

        result = ip.process_food_photo(b"image-data")
        assert not result.success
        assert result.error is not None
        assert "couldn't find exact matches" in result.error
        assert len(result.items) == 1

    def test_missing_api_key(self, monkeypatch):
        """No Gemini API key configured → fallback to FatSecret."""
        ip.configure(api_key=None)

        fatsecret_result = {"food_id": "77", "food_name": "Pasta"}
        monkeypatch.setattr(fs, "recognize_food_image", mock.MagicMock(return_value=fatsecret_result))

        result = ip.process_food_photo(b"image-data")
        assert result.success or result.no_food_detected

    def test_fatsecret_fallback_error_211(self, monkeypatch):
        """Both Gemini and FatSecret fail; FatSecret returns 211 (no food)."""
        monkeypatch.setattr(ip, "_identify_foods_with_gemini", mock.MagicMock(side_effect=RuntimeError("fail")))
        monkeypatch.setattr(fs, "recognize_food_image", mock.MagicMock(side_effect=fs.FatSecretError("No food", "211")))

        result = ip.process_food_photo(b"image-data")
        assert result.no_food_detected


# ── Formatting ──────────────────────────────────────────────────────


class TestFormatBarcodeResult:
    def test_success_formatting(self):
        """Barcode result with food data returns formatted string."""
        food = {
            "food_id": "123",
            "food_name": "Cereal",
            "brand_name": "Kellogg's",
            "servings": {
                "serving": [{
                    "serving_id": "1",
                    "serving_description": "1 cup",
                    "calories": 150,
                    "protein": 3,
                    "fat": 1,
                    "carbohydrate": 34,
                }],
            },
        }
        result = ip.BarcodeResult(barcode="0078742075581", barcode_type="EAN13", gtin="0078742075581", food=food)
        text = ip.format_barcode_result(result)
        assert "0078742075581" in text
        assert "Cereal" in text
        assert "Kellogg's" in text
        assert "150 kcal" in text

    def test_error_result(self):
        """Error result returns the error string."""
        result = ip.BarcodeResult(barcode="", barcode_type="", gtin="", food=None, error="No barcode found")
        text = ip.format_barcode_result(result)
        assert text == "No barcode found"


class TestFormatFoodResult:
    def test_success_with_foods(self):
        """Multiple foods formatted."""
        foods = [
            {"food_name": "Apple", "servings": {"serving": [{"serving_description": "1", "calories": 95, "protein": 0.5, "fat": 0.3, "carbohydrate": 25}]}},
            {"food_name": "Banana", "servings": {"serving": [{"serving_description": "1", "calories": 105, "protein": 1.3, "fat": 0.4, "carbohydrate": 27}]}},
        ]
        result = ip.FoodRecognitionResult(items=[], foods=foods)
        text = ip.format_food_result(result)
        assert "Apple" in text
        assert "Banana" in text
        assert "Food recognised" in text

    def test_no_food_detected(self):
        """Empty result with no error."""
        result = ip.FoodRecognitionResult()
        text = ip.format_food_result(result)
        assert "don't see any food" in text.lower()

    def test_error_result(self):
        """Error is passed through."""
        result = ip.FoodRecognitionResult(error="Something broke")
        text = ip.format_food_result(result)
        assert text == "Something broke"
