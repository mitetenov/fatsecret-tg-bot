"""
FatSecret Platform API client — OAuth 1.0a (HMAC-SHA1).

Uses raw signing for the method-based endpoint because FatSecret rejects
OAuth params in the Authorization header (requests-oauthlib's default).

References:
  https://platform.fatsecret.com/docs/guides/authentication/oauth1
  https://platform.fatsecret.com/docs/v5/foods.search
  https://platform.fatsecret.com/docs/v2/food.find_id_for_barcode
  https://platform.fatsecret.com/docs/v5/food.get
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
import urllib.parse
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class FatSecretError(Exception):
    """Base exception for FatSecret API errors."""


class FatSecretAuthError(FatSecretError):
    """Authentication / credential failures (HTTP 401)."""


class FatSecretNotFoundError(FatSecretError):
    """Resource not found (e.g. barcode lookup returned nothing)."""


class FatSecretRateLimitError(FatSecretError):
    """Rate limit hit — back off and retry later."""


class FatSecretAPIError(FatSecretError):
    """Generic API-level error with code + message."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"FatSecret API error {code}: {message}")


class FatSecretClient:
    """FatSecret Platform API client (OAuth 1.0a)."""

    BASE_URL = "https://platform.fatsecret.com/rest/server.api"

    def __init__(
        self,
        consumer_key: str | None = None,
        consumer_secret: str | None = None,
        *,
        retries: int = 3,
        backoff: float = 1.0,
        timeout: int = 30,
    ) -> None:
        """
        Parameters
        ----------
        consumer_key / consumer_secret:
            OAuth 1.0a Consumer Key & Secret from the FatSecret developer
            console (NOT the OAuth 2.0 Client ID/Secret pair).
            Defaults to env vars ``FATSECRET_KEY`` & ``FATSECRET_SECRET``,
            with fallback to ``FATSECRET_API_KEY`` & ``FATSECRET_API_SECRET``.
        retries:
            Number of automatic retries on transient failures (5xx, rate-limit).
        backoff:
            Initial backoff in seconds; doubles on each retry.
        timeout:
            HTTP request timeout in seconds.
        """
        self.consumer_key = consumer_key or _env_key()
        self.consumer_secret = consumer_secret or _env_secret()
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout

        if not self.consumer_key or not self.consumer_secret:
            raise FatSecretAuthError(
                "Missing FatSecret OAuth 1.0a credentials. "
                "Set FATSECRET_KEY / FATSECRET_SECRET (or "
                "FATSECRET_API_KEY / FATSECRET_API_SECRET) env vars."
            )
        self._session = requests.Session()

    # -- core request --------------------------------------------------------

    def _request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Signed POST to the method-based endpoint.  Returns decoded JSON."""
        body = {"method": method, "format": "json"}
        if params:
            body.update(params)

        signed = _sign("POST", self.BASE_URL, body, self.consumer_key, self.consumer_secret)
        encoded = urllib.parse.urlencode(signed).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._session.post(
                    self.BASE_URL,
                    data=encoded,
                    headers=headers,
                    timeout=self.timeout,
                )
                return _handle_response(resp)
            except FatSecretRateLimitError:
                logger.warning("Rate-limited (attempt %d/%d)", attempt + 1, self.retries + 1)
                last_err = FatSecretRateLimitError("Too many requests — retry exhausted")
            except FatSecretAPIError as exc:
                # 5xx-like / transient
                if exc.code >= 500 or exc.code in (8,):  # code 8 = rate limit
                    logger.warning(
                        "Transient API error %d (attempt %d/%d)", exc.code, attempt + 1, self.retries + 1
                    )
                    last_err = exc
                else:
                    raise
            except requests.RequestException as exc:
                logger.warning("HTTP error (attempt %d/%d): %s", attempt + 1, self.retries + 1, exc)
                last_err = exc

            if attempt < self.retries:
                wait = self.backoff * (2 ** attempt)
                time.sleep(wait)

        if last_err is not None:
            raise last_err
        raise FatSecretError(f"Request failed after {self.retries + 1} attempts")

    # -- public methods ------------------------------------------------------

    def search_food(
        self,
        query: str,
        *,
        max_results: int = 10,
        page_number: int = 0,
        region: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Search the FatSecret food database.

        Returns a dict with keys ``foods_search`` containing ``total_results``,
        ``max_results``, ``page_number``, and ``food`` (list of dicts).

        Each food dict has ``food_id``, ``food_name``, ``food_type``,
        ``brand_name``, ``food_url``.
        """
        params: dict[str, Any] = {
            "search_expression": query,
            "max_results": min(max(max_results, 1), 50),
            "page_number": max(page_number, 0),
        }
        if region:
            params["region"] = region
        if language:
            params["language"] = language

        return self._request("foods.search.v5", params)

    def find_by_barcode(self, barcode: str) -> dict[str, Any]:
        """Look up a food by barcode (GTIN-13 / EAN-13 / UPC-A / EAN-8).

        ``barcode`` is auto-padded to 13 digits when shorter (UPC-A → 0-prefix,
        EAN-8 → 00000-prefix).

        Returns full food data from ``food.find_id_for_barcode.v2``.
        Raises ``FatSecretNotFoundError`` if the barcode is unknown.
        """
        gtin = _to_gtin13(barcode)
        return self._request("food.find_id_for_barcode.v2", {"barcode": gtin})

    def get_food_details(self, food_id: int | str) -> dict[str, Any]:
        """Fetch full nutritional information for a ``food_id``.

        Returns a dict with ``food`` containing ``food_id``, ``food_name``,
        ``food_type``, ``brand_name``, ``servings`` (serving list with
        ``serving_id``, ``serving_description``, and per-serving nutrition),
        and per-100g/100ml/1oz standard servings.
        """
        return self._request("food.get.v5", {"food_id": str(food_id)})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qs(s: str) -> str:
    """RFC 3986 percent-encode — *nothing* is left in plaintext (safe='')."""
    return urllib.parse.quote(str(s), safe="")


def _sign(
    method: str,
    url: str,
    params: dict[str, Any],
    consumer_key: str,
    consumer_secret: str,
) -> dict[str, str]:
    """Build OAuth 1.0a HMAC-SHA1 signature and return the full signed dict."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    full = {**params, **oauth}

    # Normalized parameter string — sorted by key then value
    normalized = "&".join(
        f"{_qs(k)}={_qs(str(v))}" for k, v in sorted(full.items())
    )

    # Signature base string
    base = f"{method}&{_qs(url)}&{_qs(normalized)}"

    # Signing key: Consumer_Secret + "&" + Access_Secret (empty for non-3-legged)
    signing_key = f"{_qs(consumer_secret)}&"

    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()

    full["oauth_signature"] = sig
    return full


def _handle_response(resp: requests.Response) -> dict[str, Any]:
    """Raise typed exceptions on error; return decoded body on success."""
    # Always try to decode JSON so we get the FatSecret error code.
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}

    # HTTP-layer rate limit / auth
    if resp.status_code == 401:
        err = body.get("error", {})
        msg = err.get("message", "Authentication failed")
        raise FatSecretAuthError(msg)
    if resp.status_code == 429:
        raise FatSecretRateLimitError("Daily limit reached (5,000 calls on Basic plan)")

    # FatSecret wraps errors in an "error" key (even with 200-ish codes like 211).
    if "error" in body:
        err = body["error"]
        code = int(err.get("code", 0))
        msg = err.get("message", "Unknown error")

        if code == 211:
            raise FatSecretNotFoundError(msg)
        if code == 8:
            raise FatSecretRateLimitError(msg)
        if code in (4, 5, 7):  # auth / invalid key / deactivated
            raise FatSecretAuthError(msg)

        # 500+ from FatSecret (or any other API-level error) → retry
        raise FatSecretAPIError(code, msg)

    if not resp.ok:
        raise FatSecretError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    return body


def _to_gtin13(barcode: str) -> str:
    """Normalize to GTIN-13 (13 digits)."""
    barcode = barcode.strip()
    if not barcode.isdigit():
        raise ValueError(f"Barcode must be all digits, got: {barcode!r}")
    if len(barcode) == 13:
        return barcode
    if len(barcode) == 12:
        return "0" + barcode
    if len(barcode) == 8:
        return "00000" + barcode
    raise ValueError(f"Unsupported barcode length {len(barcode)} (need 8, 12, or 13 digits)")


def _env_key() -> str:
    return os.environ.get("FATSECRET_KEY", "") or os.environ.get("FATSECRET_API_KEY", "")


def _env_secret() -> str:
    return os.environ.get("FATSECRET_SECRET", "") or os.environ.get("FATSECRET_API_SECRET", "")
