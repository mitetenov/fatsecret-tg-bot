"""Подпись запросов к FatSecret по OAuth 1.0a (HMAC-SHA1).

Сети здесь нет намеренно: подпись ломается тихо — сервер отвечает «invalid
signature» и не говорит, какой из шагов нормализации разошёлся. Единственный способ
ей доверять — держать её чистыми функциями и покрыть тестами по RFC 5849.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Iterable, Mapping
from urllib.parse import quote, urlsplit

# RFC 5849 §3.6: незарезервированными считаются только эти символы помимо букв и цифр.
# safe у quote() по умолчанию содержит "/", что даёт неверную подпись.
_UNRESERVED_EXTRA = "-._~"

_DEFAULT_PORTS = {"http": 80, "https": 443}


def percent_encode(value: object) -> str:
    return quote(str(value), safe=_UNRESERVED_EXTRA)


def normalize_parameters(params: Iterable[tuple[str, object]]) -> str:
    """RFC 5849 §3.4.1.3.2: закодировать, отсортировать по ключу и значению, склеить."""
    encoded = sorted((percent_encode(k), percent_encode(v)) for k, v in params)
    return "&".join(f"{key}={value}" for key, value in encoded)


def base_string_uri(url: str) -> str:
    """RFC 5849 §3.4.1.2: схема и хост в нижнем регистре, дефолтный порт опускается."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    scheme = parts.scheme.lower()
    if parts.port and parts.port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{parts.port}"
    return f"{scheme}://{host}{parts.path or '/'}"


def signature_base_string(
    method: str, url: str, params: Iterable[tuple[str, object]]
) -> str:
    return "&".join(
        [
            method.upper(),
            percent_encode(base_string_uri(url)),
            percent_encode(normalize_parameters(params)),
        ]
    )


def sign(
    method: str,
    url: str,
    params: Iterable[tuple[str, object]],
    consumer_secret: str,
    token_secret: str = "",
) -> str:
    key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(
        key.encode(),
        signature_base_string(method, url, params).encode(),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode()


def signed_params(
    method: str,
    url: str,
    params: Mapping[str, object],
    consumer_key: str,
    consumer_secret: str,
    token: str | None = None,
    token_secret: str = "",
    callback: str | None = None,
    verifier: str | None = None,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Полный набор параметров запроса вместе с oauth_signature.

    nonce и timestamp принимаются извне, чтобы подпись была воспроизводимой в тестах.
    """
    oauth: dict[str, object] = {
        "oauth_consumer_key": consumer_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
        "oauth_timestamp": timestamp if timestamp is not None else int(time.time()),
        "oauth_nonce": nonce or secrets.token_hex(8),
    }
    if token:
        oauth["oauth_token"] = token
    if callback:
        oauth["oauth_callback"] = callback
    if verifier:
        oauth["oauth_verifier"] = verifier

    all_params = {**params, **oauth}
    signature = sign(
        method, url, list(all_params.items()), consumer_secret, token_secret
    )
    return {k: str(v) for k, v in {**all_params, "oauth_signature": signature}.items()}
