"""
Unit tests for fatsecret_client.py — uses mocked HTTP to avoid requiring
live API credentials.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from fatsecret_client import (
    FatSecretAPIError,
    FatSecretAuthError,
    FatSecretClient,
    FatSecretNotFoundError,
    FatSecretRateLimitError,
    _to_gtin13,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(json_data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    return resp


def _make_client(**kw) -> FatSecretClient:
    return FatSecretClient(consumer_key="test_key", consumer_secret="test_secret", **kw)


# ---------------------------------------------------------------------------
# Barcode conversion
# ---------------------------------------------------------------------------

class TestBarcodeConversion:
    def test_gtin13_already_13(self):
        assert _to_gtin13("0078742097833") == "0078742097833"

    def test_upca_12_digit(self):
        assert _to_gtin13("078742097833") == "0078742097833"

    def test_ean8_8_digit(self):
        assert _to_gtin13("12345678") == "0000012345678"

    def test_invalid_length(self):
        with pytest.raises(ValueError, match="Unsupported barcode length"):
            _to_gtin13("123")

    def test_strips_whitespace(self):
        assert _to_gtin13("  0078742097833\n") == "0078742097833"

    def test_non_digit_barcode_raises(self):
        with pytest.raises(ValueError, match="Barcode must be all digits"):
            _to_gtin13("abc123def4567")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

class TestCredentials:

    def test_missing_credentials_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(FatSecretAuthError, match="Missing FatSecret"):
                FatSecretClient()

    def test_falls_back_to_api_key_env(self):
        env = {"FATSECRET_API_KEY": "k", "FATSECRET_API_SECRET": "s"}
        with patch.dict(os.environ, env, clear=True):
            client = FatSecretClient()
            assert client.consumer_key == "k"
            assert client.consumer_secret == "s"

    def test_prefers_fatsecret_key_env(self):
        env = {
            "FATSECRET_KEY": "k1",
            "FATSECRET_SECRET": "s1",
            "FATSECRET_API_KEY": "k2",
            "FATSECRET_API_SECRET": "s2",
        }
        with patch.dict(os.environ, env, clear=True):
            client = FatSecretClient()
            assert client.consumer_key == "k1"
            assert client.consumer_secret == "s1"


# ---------------------------------------------------------------------------
# search_food
# ---------------------------------------------------------------------------

class TestSearchFood:

    def test_returns_search_results(self):
        payload = {
            "foods_search": {
                "max_results": 10,
                "total_results": 42,
                "page_number": 0,
                "food": [
                    {"food_id": 12345, "food_name": "Apple", "food_type": "Generic",
                     "brand_name": "", "food_url": "https://..."}
                ],
            }
        }
        client = _make_client()
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            result = client.search_food("apple")
        assert result["foods_search"]["total_results"] == 42
        assert result["foods_search"]["food"][0]["food_name"] == "Apple"

    def test_clamps_max_results(self):
        client = _make_client()
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = _mock_response({"foods_search": {"food": []}})
            client.search_food("test", max_results=100)
            body = urllib_parse_body(mock_post.call_args.kwargs["data"])
            assert body["max_results"] == "50"

    def test_passes_region_and_language(self):
        client = _make_client()
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = _mock_response({"foods_search": {"food": []}})
            client.search_food("pomme", region="FR", language="fr")
            body = urllib_parse_body(mock_post.call_args.kwargs["data"])
            assert body["region"] == "FR"
            assert body["language"] == "fr"


# ---------------------------------------------------------------------------
# find_by_barcode
# ---------------------------------------------------------------------------

class TestFindByBarcode:

    def test_returns_food_data(self):
        payload = {"food": {"food_id": 999, "food_name": "Cereal Bar"}}
        client = _make_client()
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            result = client.find_by_barcode("0078742097833")
        assert result["food"]["food_name"] == "Cereal Bar"

    def test_barcode_not_found(self):
        payload = {"error": {"code": 211, "message": "No food found"}}
        client = _make_client()
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            with pytest.raises(FatSecretNotFoundError):
                client.find_by_barcode("9999999999999")

    def test_auto_pads_upca(self):
        client = _make_client()
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = _mock_response({"food": {}})
            client.find_by_barcode("078742097833")
            body = urllib_parse_body(mock_post.call_args.kwargs["data"])
            assert body["barcode"] == "0078742097833"


# ---------------------------------------------------------------------------
# get_food_details
# ---------------------------------------------------------------------------

class TestGetFoodDetails:

    def test_returns_nutrition(self):
        payload = {
            "food": {
                "food_id": 12345,
                "food_name": "Whole Milk",
                "food_type": "Generic",
                "servings": {
                    "serving": [{
                        "serving_id": 1,
                        "serving_description": "1 cup",
                        "calories": "149",
                        "fat": "8.0",
                        "carbohydrate": "12.0",
                        "protein": "8.0",
                    }]
                },
            }
        }
        client = _make_client()
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            result = client.get_food_details(12345)
        assert result["food"]["food_name"] == "Whole Milk"
        assert result["food"]["servings"]["serving"][0]["calories"] == "149"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_http_401_raises_auth_error(self):
        payload = {"error": {"code": 5, "message": "Invalid signature"}}
        client = _make_client()
        with patch.object(client._session, "post",
                          return_value=_mock_response(payload, status=401)):
            with pytest.raises(FatSecretAuthError):
                client.search_food("apple")

    def test_http_429_raises_rate_limit(self):
        client = _make_client(retries=0)
        with patch.object(client._session, "post",
                          return_value=_mock_response({}, status=429)):
            with pytest.raises(FatSecretRateLimitError):
                client.search_food("apple")

    def test_retry_on_500(self):
        err_resp = _mock_response({"error": {"code": 500, "message": "Internal"}}, status=500)
        ok_resp = _mock_response({"foods_search": {"food": []}})
        client = _make_client(retries=2, backoff=0.01)
        with patch.object(client._session, "post",
                          side_effect=[err_resp, ok_resp]) as mock_post:
            result = client.search_food("apple")
        assert result["foods_search"]["food"] == []
        assert mock_post.call_count == 2

    def test_retry_exhausted_raises(self):
        client = _make_client(retries=2, backoff=0.01)
        payload = {"error": {"code": 500, "message": "Internal server error"}}
        with patch.object(client._session, "post",
                          return_value=_mock_response(payload, status=500)):
            with pytest.raises(FatSecretAPIError, match="Internal server error"):
                client.search_food("apple")

    def test_fatal_4xx_not_retried(self):
        client = _make_client(retries=2, backoff=0.01)
        payload = {"error": {"code": 4, "message": "Invalid key"}}
        with patch.object(client._session, "post",
                          return_value=_mock_response(payload, status=200)):
            with pytest.raises(FatSecretAuthError, match="Invalid key"):
                client.search_food("apple")

    def test_network_error_retry(self):
        import requests as req
        ok_resp = _mock_response({"foods_search": {"food": []}})
        client = _make_client(retries=1, backoff=0.01)
        with patch.object(client._session, "post",
                          side_effect=[req.ConnectionError("timeout"), ok_resp]) as mock_post:
            result = client.search_food("apple")
        assert result["foods_search"]["food"] == []
        assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def urllib_parse_body(data: bytes) -> dict:
    import urllib.parse
    return dict(urllib.parse.parse_qsl(data.decode("utf-8")))
