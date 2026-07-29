"""Unit tests for FatSecret API client — all external API calls are mocked."""

import base64
import json
from unittest import mock

import pytest
import requests

import fatsecret_client as fs


# ── Helpers ──────────────────────────────────────────────────────────


def _mock_response(status_code=200, json_data=None, raise_error=None):
    """Build a mock requests.Response."""
    mock_resp = mock.create_autospec(requests.Response, instance=True)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {}
    mock_resp.raise_for_status.side_effect = raise_error
    return mock_resp


def _food_search_response(foods: list[dict]) -> dict:
    return {"foods_search": {"max_results": len(foods), "total_results": len(foods), "page_number": 0, "food": foods}}


def _food_detail_response(food_id: str, name: str) -> dict:
    return {"food": {
        "food_id": food_id,
        "food_name": name,
        "food_type": "Generic",
        "servings": {
            "serving": [{
                "serving_id": "1",
                "serving_description": "100 g",
                "calories": 165,
                "protein": 31,
                "fat": 3.6,
                "carbohydrate": 0,
            }],
        },
    }}


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """Reset the module-level token cache between tests."""
    fs._token_cache = None


@pytest.fixture
def mock_token(monkeypatch):
    """Mock a successful OAuth 2.0 token response."""
    monkeypatch.setattr("fatsecret_client.requests.post", mock.MagicMock())


# ── OAuth 2.0 token ─────────────────────────────────────────────────


def test_get_access_token_success(monkeypatch):
    """Token endpoint returns a valid access token."""
    post_mock = mock.MagicMock()
    post_mock.return_value = _mock_response(
        json_data={"access_token": "test-token-123", "expires_in": 86400},
    )
    monkeypatch.setattr("fatsecret_client.requests.post", post_mock)
    monkeypatch.setattr(fs, "_token_cache", None)

    token = fs._get_access_token()
    assert token == "test-token-123"
    # Second call should reuse cache, not call the API again
    assert fs._token_cache is not None
    token2 = fs._get_access_token()
    assert token2 == "test-token-123"
    post_mock.assert_called_once()


def test_get_access_token_http_error(monkeypatch):
    """Token endpoint returns HTTP error."""
    post_mock = mock.MagicMock()
    post_mock.return_value = _mock_response(
        status_code=401,
        raise_error=requests.HTTPError("Unauthorized"),
    )
    monkeypatch.setattr("fatsecret_client.requests.post", post_mock)
    monkeypatch.setattr(fs, "_token_cache", None)

    with pytest.raises(requests.HTTPError):
        fs._get_access_token()


# ── Food search ─────────────────────────────────────────────────────


def test_search_foods_success(monkeypatch):
    """Search returns food items and properly handles single/dict results."""
    get_mock = mock.MagicMock()
    get_mock.return_value = _mock_response(
        json_data=_food_search_response([
            {"food_id": "123", "food_name": "Apple", "brand_name": None, "food_type": "Generic"},
            {"food_id": "456", "food_name": "Banana", "brand_name": None, "food_type": "Generic"},
        ]),
    )
    monkeypatch.setattr("fatsecret_client.requests.get", get_mock)
    monkeypatch.setattr(fs, "_get_access_token", lambda: "fake-token")

    results = fs.search_foods("fruit")
    assert len(results) == 2
    assert results[0]["food_name"] == "Apple"
    assert results[1]["food_id"] == "456"


def test_search_foods_empty(monkeypatch):
    """Search with no results returns empty list."""
    get_mock = mock.MagicMock()
    get_mock.return_value = _mock_response(
        json_data=_food_search_response([]),
    )
    monkeypatch.setattr("fatsecret_client.requests.get", get_mock)
    monkeypatch.setattr(fs, "_get_access_token", lambda: "fake-token")

    results = fs.search_foods("xyznonexistentfood999")
    assert results == []


def test_search_foods_api_error(monkeypatch):
    """FatSecret returns an 'error' key in the JSON body."""
    get_mock = mock.MagicMock()
    get_mock.return_value = _mock_response(
        json_data={"error": {"code": "500", "message": "Internal server error"}},
    )
    monkeypatch.setattr("fatsecret_client.requests.get", get_mock)
    monkeypatch.setattr(fs, "_get_access_token", lambda: "fake-token")

    with pytest.raises(fs.FatSecretError, match="Internal server error"):
        fs.search_foods("anything")


# ── Food details ────────────────────────────────────────────────────


def test_get_food_details_success(monkeypatch):
    """Food details returns full nutrition info."""
    get_mock = mock.MagicMock()
    get_mock.return_value = _mock_response(
        json_data=_food_detail_response("123", "Chicken Breast"),
    )
    monkeypatch.setattr("fatsecret_client.requests.get", get_mock)
    monkeypatch.setattr(fs, "_get_access_token", lambda: "fake-token")

    food = fs.get_food_details("123")
    assert food["food_id"] == "123"
    assert food["food_name"] == "Chicken Breast"
    servings = food["servings"]["serving"]
    assert servings[0]["calories"] == 165


# ── Barcode lookup ──────────────────────────────────────────────────


def test_lookup_barcode_success(monkeypatch):
    """Barcode lookup returns full food object."""
    post_mock = mock.MagicMock()
    post_mock.return_value = _mock_response(
        json_data={"food": {
            "food_id": "999",
            "food_name": "Protein Bar",
            "brand_name": "Quest",
            "servings": {
                "serving": {
                    "serving_id": "1",
                    "serving_description": "1 bar",
                    "calories": 190,
                    "protein": 21,
                    "fat": 8,
                    "carbohydrate": 21,
                },
            },
        }},
    )
    monkeypatch.setattr("fatsecret_client.requests.post", post_mock)
    monkeypatch.setattr(fs, "_get_access_token", lambda: "fake-token")

    food = fs.lookup_barcode("0078742075581")
    assert food["food_name"] == "Protein Bar"
    assert food["brand_name"] == "Quest"


def test_lookup_barcode_not_found(monkeypatch):
    """Error code 211 is raised as FatSecretError."""
    post_mock = mock.MagicMock()
    post_mock.return_value = _mock_response(
        json_data={"error": {"code": "211", "message": "Barcode not found"}},
    )
    monkeypatch.setattr("fatsecret_client.requests.post", post_mock)
    monkeypatch.setattr(fs, "_get_access_token", lambda: "fake-token")

    with pytest.raises(fs.FatSecretError) as exc_info:
        fs.lookup_barcode("0000000000000")
    assert exc_info.value.code == "211"


# ── format_nutrition ────────────────────────────────────────────────


def test_format_nutrition():
    """Nutrition formatting returns readable string."""
    food = {
        "food_name": "Apple",
        "brand_name": None,
        "servings": {
            "serving": [{
                "serving_description": "1 medium",
                "calories": 95,
                "protein": 0.5,
                "fat": 0.3,
                "carbohydrate": 25,
            }],
        },
    }
    result = fs.format_nutrition(food)
    assert "Apple" in result
    assert "95 kcal" in result
    assert "P:0.5g" in result


def test_format_nutrition_no_brand():
    """Brand is omitted when None."""
    food = {
        "food_name": "Rice",
        "servings": {"serving": [{"serving_description": "1 cup", "calories": 206}]},
    }
    result = fs.format_nutrition(food)
    assert "Rice" in result
    assert "(" not in result  # no brand
