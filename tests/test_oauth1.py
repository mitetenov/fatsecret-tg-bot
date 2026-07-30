"""Проверка подписи против примера из RFC 5849 §3.4.1.

Это единственный внешний эталон, по которому можно убедиться, что нормализация
параметров и сборка signature base string совпадают со спецификацией — а значит и с
тем, что ждёт FatSecret.
"""

from fsbot.fatsecret.oauth1 import (
    base_string_uri,
    normalize_parameters,
    percent_encode,
    sign,
    signature_base_string,
)

# RFC 5849 §3.4.1.3.1: параметры запроса, тела и OAuth — в декодированном виде.
RFC_PARAMS = [
    ("b5", "=%3D"),
    ("a3", "a"),
    ("c@", ""),
    ("a2", "r b"),
    ("c2", ""),
    ("a3", "2 q"),
    ("oauth_consumer_key", "9djdj82h48djs9d2"),
    ("oauth_token", "kkk9d7dh3k39sjv7"),
    ("oauth_signature_method", "HMAC-SHA1"),
    ("oauth_timestamp", "137131201"),
    ("oauth_nonce", "7d8f3e4a"),
]

RFC_NORMALIZED = (
    "a2=r%20b&a3=2%20q&a3=a&b5=%3D%253D&c%40=&c2="
    "&oauth_consumer_key=9djdj82h48djs9d2&oauth_nonce=7d8f3e4a"
    "&oauth_signature_method=HMAC-SHA1&oauth_timestamp=137131201"
    "&oauth_token=kkk9d7dh3k39sjv7"
)

RFC_BASE_STRING = (
    "POST&http%3A%2F%2Fexample.com%2Frequest"
    "&a2%3Dr%2520b%26a3%3D2%2520q%26a3%3Da%26b5%3D%253D%25253D%26c%2540%3D%26c2%3D"
    "%26oauth_consumer_key%3D9djdj82h48djs9d2%26oauth_nonce%3D7d8f3e4a"
    "%26oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D137131201"
    "%26oauth_token%3Dkkk9d7dh3k39sjv7"
)


def test_normalize_parameters_matches_rfc():
    assert normalize_parameters(RFC_PARAMS) == RFC_NORMALIZED


def test_signature_base_string_matches_rfc():
    url = "http://example.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b"
    assert signature_base_string("POST", url, RFC_PARAMS) == RFC_BASE_STRING


def test_duplicate_keys_are_sorted_by_value():
    # "2 q" кодируется в "2%20q" и по строковому сравнению идёт раньше "a".
    assert normalize_parameters([("a3", "a"), ("a3", "2 q")]) == "a3=2%20q&a3=a"


def test_percent_encode_leaves_unreserved_alone():
    assert percent_encode("aZ09-._~") == "aZ09-._~"
    assert percent_encode("/ =&+") == "%2F%20%3D%26%2B"


def test_base_string_uri_normalizes_scheme_host_and_default_port():
    assert base_string_uri("HTTPS://Platform.FatSecret.com:443/rest/server.api") == (
        "https://platform.fatsecret.com/rest/server.api"
    )
    assert base_string_uri("http://example.com:8080/x") == "http://example.com:8080/x"
    assert base_string_uri("https://example.com") == "https://example.com/"


def test_signing_key_uses_empty_token_secret_for_two_legged_calls():
    # Двуногие вызовы (поиск продуктов) подписываются только consumer secret,
    # но амперсанд в ключе обязателен — без него FatSecret вернёт invalid signature.
    without = sign("GET", "https://example.com/x", [("a", "1")], "cs")
    with_empty = sign("GET", "https://example.com/x", [("a", "1")], "cs", "")
    assert without == with_empty
