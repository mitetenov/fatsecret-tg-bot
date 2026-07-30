#!/usr/bin/env python3
"""Спайк первого дня: выяснить, что реально доступно на нашем тарифе FatSecret.

У FatSecret две независимые схемы авторизации с разными парами ключей:

  * OAuth 2.0 (client_id / client_secret) — чтение: поиск продуктов, штрих-код.
    Требует, чтобы исходящий IP был в IP Restrictions (иначе code 21).
  * OAuth 1.0 (consumer_key / consumer_secret) — единственный путь к дневнику
    конкретного пользователя: запись Позиций, создание Своих продуктов.

Четыре вопроса, от которых зависит состав фич:

  1. Работает ли `food.create` v2 — может ли бот сам создавать продукты из этикетки.
  2. Работает ли `food.find_id_for_barcode` — на Basic scope `barcode` не выдаётся,
     проверяем что будет после одобрения Premier Free.
  3. Доступен ли новый REST дневника (`/profile/diary/entries`) — иначе придётся
     считать `number_of_units` для legacy `food_entry.create`.
  4. Что отдаёт поиск на кириллицу.

Запуск:
    .venv/bin/python spike/check_fatsecret.py --two-legged-only   # только чтение
    .venv/bin/python spike/check_fatsecret.py                     # + PIN-авторизация
    .venv/bin/python spike/check_fatsecret.py --write             # + пробы, меняющие аккаунт
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fsbot.fatsecret.oauth1 import signed_params  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# В контейнере состояние живёт в примонтированном каталоге, иначе токен теряется
# вместе с контейнером и PIN приходится вводить заново.
STATE_DIR = Path(os.environ.get("FSBOT_STATE_DIR", ROOT))
TOKEN_CACHE = STATE_DIR / ".spike-token.json"

LEGACY_API = "https://platform.fatsecret.com/rest/server.api"
OAUTH2_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
REQUEST_TOKEN_URL = "https://authentication.fatsecret.com/oauth/request_token"
AUTHORIZE_URL = "https://authentication.fatsecret.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://authentication.fatsecret.com/oauth/access_token"

# Полная база нового REST в документации не названа — путь указан относительно,
# поэтому правильный кандидат выясняется эмпирически.
REST_DIARY_CANDIDATES = [
    "https://platform.fatsecret.com/rest/profile/diary/entries",
    "https://platform.fatsecret.com/rest/v1/profile/diary/entries",
    "https://platform.fatsecret.com/rest/v2/profile/diary/entries",
]

ALL_SCOPES = ["basic", "premier", "barcode", "localization", "nlp", "image-recognition"]


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    """Ключи из .env, поверх — переменные окружения (в контейнере их даёт compose)."""
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("'\"")

    env.update({k: v for k, v in os.environ.items() if k.startswith("FATSECRET_") and v})

    if not env.get("FATSECRET_CONSUMER_KEY"):
        sys.exit(
            "Нет ключей FatSecret. Заполни .env (см. .env.example) — либо, в Docker, "
            "убедись, что compose передаёт .env через env_file."
        )
    return env


def report(label: str, response: httpx.Response, limit: int = 400) -> dict | None:
    body = response.text
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None

    error = payload.get("error") if isinstance(payload, dict) else None
    if error:
        verdict = f"НЕТ — code {error.get('code')}: {error.get('message')}"
    elif response.status_code >= 400:
        verdict = f"НЕТ — HTTP {response.status_code}"
    else:
        verdict = "ДА"

    print(f"\n--- {label}\n    {verdict}")
    print(f"    {body[:limit]}{'…' if len(body) > limit else ''}")
    return payload if not error and response.status_code < 400 else None


class OAuth2Reader:
    """Двуногие вызовы: поиск продуктов и штрих-код."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.auth = (client_id, client_secret)
        self.client = httpx.Client(timeout=30.0)

    def token(self, scope: str = "basic") -> tuple[str | None, str]:
        response = self.client.post(
            OAUTH2_TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": scope},
            auth=self.auth,
        )
        if response.status_code != 200:
            return None, f"HTTP {response.status_code} {response.text[:120]}"
        access = response.json()["access_token"]
        claims = json.loads(base64.urlsafe_b64decode(access.split(".")[1] + "=="))
        return access, str(claims.get("scope") or claims.get("aud"))

    def call(self, access_token: str, api_method: str, **params) -> httpx.Response:
        return self.client.post(
            LEGACY_API,
            params={"method": api_method, "format": "json", **params},
            headers={"Authorization": f"Bearer {access_token}"},
        )


class OAuth1Profile:
    """Трёхногие вызовы: дневник и Свои продукты конкретного пользователя."""

    def __init__(self, consumer_key: str, consumer_secret: str) -> None:
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.client = httpx.Client(timeout=30.0)

    def call(
        self,
        method: str,
        url: str,
        params: dict[str, object],
        token: str | None = None,
        token_secret: str = "",
        **oauth_extra: object,
    ) -> httpx.Response:
        signed = signed_params(
            method,
            url,
            params,
            self.consumer_key,
            self.consumer_secret,
            token=token,
            token_secret=token_secret,
            **oauth_extra,  # type: ignore[arg-type]
        )
        if method.upper() == "GET":
            return self.client.get(url, params=signed)
        return self.client.post(url, data=signed)

    def legacy(self, api_method: str, token=None, token_secret="", **params):
        # GET: legacy server.api проверяет подпись по query string.
        return self.call(
            "GET",
            LEGACY_API,
            {"method": api_method, "format": "json", **params},
            token=token,
            token_secret=token_secret,
        )

    def access_token(self) -> tuple[str, str]:
        """3-legged OAuth 1.0 в режиме oob: FatSecret показывает PIN, вводим руками."""
        if TOKEN_CACHE.exists():
            cached = json.loads(TOKEN_CACHE.read_text())
            print(f"Использую сохранённый access token из {TOKEN_CACHE.name}")
            return cached["token"], cached["token_secret"]

        response = self.call("POST", REQUEST_TOKEN_URL, {}, callback="oob")
        if response.status_code >= 400:
            sys.exit(
                f"request_token не выдан: HTTP {response.status_code}\n{response.text}\n"
                "Проверь, что FATSECRET_CONSUMER_KEY/SECRET — это ключи OAuth 1.0, "
                "а не Client ID/Secret от OAuth 2.0."
            )
        request_token = dict(item.split("=", 1) for item in response.text.split("&"))

        print(
            f"\nОткрой в браузере и разреши доступ:\n"
            f"{AUTHORIZE_URL}?oauth_token={request_token['oauth_token']}\n"
        )
        verifier = input("Введи PIN, который показал FatSecret: ").strip()

        response = self.call(
            "POST",
            ACCESS_TOKEN_URL,
            {},
            token=request_token["oauth_token"],
            token_secret=request_token["oauth_token_secret"],
            verifier=verifier,
        )
        if response.status_code >= 400:
            sys.exit(f"access_token не выдан: HTTP {response.status_code}\n{response.text}")
        access = dict(item.split("=", 1) for item in response.text.split("&"))

        token, token_secret = access["oauth_token"], access["oauth_token_secret"]
        TOKEN_CACHE.write_text(json.dumps({"token": token, "token_secret": token_secret}))
        TOKEN_CACHE.chmod(0o600)
        print(f"Access token сохранён в {TOKEN_CACHE.name} (в .gitignore)")
        return token, token_secret


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                       help="разрешить пробы, создающие продукт в твоём аккаунте")
    parser.add_argument("--barcode", default="0049000028904", help="GTIN-13 для проверки")
    parser.add_argument("--two-legged-only", action="store_true",
                       help="только чтение, без браузера и PIN")
    args = parser.parse_args()

    env = load_env()

    print("=" * 72)
    print("OAuth 2.0 — какие scope выдаёт аккаунт (это и есть ответ про тариф)")
    print("=" * 72)

    client_id = env.get("FATSECRET_CLIENT_ID")
    client_secret = env.get("FATSECRET_CLIENT_SECRET")
    reader = access = None
    if client_id and client_secret:
        reader = OAuth2Reader(client_id, client_secret)
        for scope in ALL_SCOPES:
            token, info = reader.token(scope)
            print(f"  scope={scope:<18} {'ДА  ' if token else 'НЕТ '} {info}")
        access, _ = reader.token("basic")
    else:
        print("  FATSECRET_CLIENT_ID / SECRET не заданы — пропускаю")

    if reader and access:
        print("\n" + "=" * 72)
        print("Чтение по OAuth 2.0 (нужен IP в IP Restrictions, иначе code 21)")
        print("=" * 72)
        report("0. Связь: foods.search 'chicken breast'",
               reader.call(access, "foods.search",
                           search_expression="chicken breast", max_results=2))
        report("4. Кириллица: foods.search 'творог'",
               reader.call(access, "foods.search",
                           search_expression="творог", max_results=3))
        report("2. Штрих-код: food.find_id_for_barcode",
               reader.call(access, "food.find_id_for_barcode", barcode=args.barcode))

    consumer_key = env.get("FATSECRET_CONSUMER_KEY")
    consumer_secret = env.get("FATSECRET_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        print("\nFATSECRET_CONSUMER_KEY / SECRET (OAuth 1.0) не заданы — "
              "дневник проверить нечем, это ключевая пара для записи.")
        return

    profile = OAuth1Profile(consumer_key, consumer_secret)

    print("\n" + "=" * 72)
    print("OAuth 1.0 — двуногая проба (заодно видно, действует ли тут IP-whitelist)")
    print("=" * 72)
    report("foods.search через подпись OAuth 1.0",
           profile.legacy("foods.search", search_expression="oats", max_results=1))

    if args.two_legged_only:
        print("\nДальше нужна привязка аккаунта — запусти без --two-legged-only.")
        return

    print("\n" + "=" * 72)
    print("OAuth 1.0 — трёхногие вызовы (доступ к твоему аккаунту)")
    print("=" * 72)
    token, token_secret = profile.access_token()

    report("Профиль отвечает: profile.get",
           profile.legacy("profile.get", token=token, token_secret=token_secret))

    print("\n3. Новый REST дневника — какой базовый путь отвечает:")
    for url in REST_DIARY_CANDIDATES:
        report(f"   GET {url}",
               profile.call("GET", url, {"date": "2026-07-30", "format": "json"},
                            token=token, token_secret=token_secret),
               limit=200)

    if not args.write:
        print("\n1. food.create v2 — пропущено (нужен --write: создаёт продукт "
              "в твоём аккаунте, удалить его через API нечем)")
        return

    report("1. Создание Своего продукта: food.create.v2",
           profile.legacy("food.create.v2", token=token, token_secret=token_secret,
                          food_name="СПАЙК-ТЕСТ удали меня",
                          brand_type="manufacturer", brand_name="fsbot spike",
                          serving_size="100 g",
                          calories=100, fat=1, carbohydrate=2, protein=3))


if __name__ == "__main__":
    main()
