# Recognition P0 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Устранить четыре подтверждённых источника систематических ошибок: формат GTIN для FatSecret, ложный LLM fallback, неправдоподобные КБЖУ и серверную дату в callback-кнопках.

**Architecture:** Сохранить текущий pipeline и добавить три чистые доменные границы: GTIN-нормализацию в `domain/barcodes.py`, общий nutrition validator в новом `domain/nutrition.py` и timezone-aware `shift_day`. В OpenRouter отделить один HTTP-вызов модели от обхода моделей, чтобы критерием успеха распознавания был валидный `Recognition`, а не HTTP 200.

**Tech Stack:** Python 3.12+, asyncio, httpx, aiogram 3, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-28-recognition-p0-hardening-design.md`

## Global Constraints

- Сохранить пользовательский сценарий «реплика → черновик → явное подтверждение».
- Не добавлять телеметрию, новый ranker, confidence и поддержку нескольких фотографий.
- Не менять формат сохранённых черновиков и barcode bindings.
- Не обращаться к сети из тестов.
- Каждое изменение production-кода начинается с теста, который падает по ожидаемой причине.
- После каждого task запускать его целевые тесты и полный `pytest`.

---

## File Map

- `src/fsbot/domain/barcodes.py` — checksum, UPC-E expansion, GTIN validation and FatSecret normalization.
- `src/fsbot/fatsecret/client.py` — гарантирует, что в FatSecret уходит только GTIN-13.
- `src/fsbot/llm/openrouter.py` — один HTTP-вызов и parse-aware обход моделей.
- `src/fsbot/domain/nutrition.py` — единый чистый валидатор КБЖУ.
- `src/fsbot/llm/parsing.py` — применяет nutrition validator к ответам LLM.
- `src/fsbot/foodfacts.py` — применяет тот же validator к Open Food Facts.
- `src/fsbot/bot/pipeline.py` — timezone-aware ручное переключение даты.
- `src/fsbot/bot/handlers.py` — передаёт timezone пользователя в `shift_day`.
- `tests/test_barcodes.py`, `tests/test_fatsecret_client.py` — регрессии GTIN.
- `tests/test_openrouter_fallback.py` — управляемый HTTP transport для fallback.
- `tests/test_nutrition.py`, `tests/test_llm_parsing.py`, `tests/test_foodfacts.py` — единый nutrition contract.
- `tests/test_daybounds.py` — регрессии callback-даты.

---

### Task 1: GTIN validation and FatSecret normalization

**Files:**
- Modify: `src/fsbot/domain/barcodes.py`
- Modify: `src/fsbot/fatsecret/client.py:156-160`
- Modify: `tests/test_barcodes.py`
- Create: `tests/test_fatsecret_client.py`

**Interfaces:**
- Produces: `valid_gtin(value: str) -> bool`
- Produces: `expand_upce(value: str) -> str | None`
- Produces: `fatsecret_gtin13(value: str, symbology: str | None = None) -> str | None`
- Preserves: `plausible(candidates: list[tuple[str, str]]) -> str | None`
- `FatSecretClient.food_id_by_barcode()` returns `None` without HTTP when normalization fails.

- [ ] **Step 1: Write failing domain tests**

Append focused cases to `tests/test_barcodes.py`:

```python
from fsbot.domain.barcodes import fatsecret_gtin13, valid_gtin


def test_fatsecret_normalizes_supported_gtin_lengths():
    assert fatsecret_gtin13("96385074") == "0000096385074"
    assert fatsecret_gtin13("036000291452") == "0036000291452"
    assert fatsecret_gtin13("4006381333931") == "4006381333931"


def test_upce_is_expanded_before_fatsecret_lookup():
    assert fatsecret_gtin13("04252614", "UPCE") == "0042100005264"


def test_invalid_checksum_and_gtin14_are_not_sent_to_fatsecret():
    assert not valid_gtin("4006381333932")
    assert fatsecret_gtin13("4006381333932") is None
    assert valid_gtin("10012345678902")
    assert fatsecret_gtin13("10012345678902") is None


def test_isbn_is_not_selected_as_food_barcode():
    assert plausible([("9780306406157", "ISBN13")]) is None


def test_unknown_symbology_needs_a_valid_checksum():
    assert plausible([("1234567890123", "QRCODE")]) is None
    assert plausible([("4006381333931", "QRCODE")]) == "4006381333931"
```

Update old QR fallback fixtures from invalid `1234567890123` to valid
`4006381333931` where the test is about precedence rather than checksum.

- [ ] **Step 2: Run the barcode tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_barcodes.py -q
```

Expected: collection fails because `fatsecret_gtin13` and `valid_gtin` do not exist.

- [ ] **Step 3: Implement the pure GTIN functions**

In `src/fsbot/domain/barcodes.py`:

```python
NON_FOOD_SYMBOLOGIES = {"ISBN10", "ISBN13"}


def valid_gtin(value: str) -> bool:
    if not value.isdigit() or len(value) not in GTIN_LENGTHS:
        return False
    data, check = value[:-1], int(value[-1])
    total = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(data))
    )
    return (10 - total % 10) % 10 == check


def expand_upce(value: str) -> str | None:
    if len(value) != 8 or not value.isdigit():
        return None
    number_system, body, check = value[0], value[1:7], value[7]
    if number_system not in {"0", "1"}:
        return None
    tail = body[-1]
    if tail in "012":
        data = number_system + body[:2] + tail + "0000" + body[2:5]
    elif tail == "3":
        data = number_system + body[:3] + "00000" + body[3:5]
    elif tail == "4":
        data = number_system + body[:4] + "00000" + body[4]
    else:
        data = number_system + body[:5] + "0000" + tail
    upca = data + check
    return upca if valid_gtin(upca) else None


def fatsecret_gtin13(value: str, symbology: str | None = None) -> str | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    if (symbology or "").upper() == "UPCE":
        digits = expand_upce(digits) or ""
    elif len(digits) == 8 and not valid_gtin(digits):
        # При ручном вводе символика неизвестна: сначала трактуем код как EAN-8,
        # затем как UPC-E, если EAN checksum не сошёлся.
        digits = expand_upce(digits) or digits
    if not valid_gtin(digits) or len(digits) == 14:
        return None
    return digits.zfill(13)
```

Change `plausible()` so it skips `NON_FOOD_SYMBOLOGIES`, accepts only
`valid_gtin(digits)`, and expands UPC-E to canonical UPC-A before returning it.
This early expansion is necessary because downstream barcode bindings intentionally
store only digits, not pyzbar metadata.

- [ ] **Step 4: Run barcode tests and verify GREEN**

Run the same command. Expected: all `tests/test_barcodes.py` cases pass.

- [ ] **Step 5: Write the failing FatSecret boundary test**

Create `tests/test_fatsecret_client.py`:

```python
import pytest

from fsbot.fatsecret.client import FatSecretClient


class RecordingClient(FatSecretClient):
    def __init__(self):
        self.calls = []

    async def _call(self, api_method, token=None, token_secret="", **params):
        self.calls.append((api_method, params))
        return {"food_id": {"value": "4384"}}


@pytest.mark.asyncio
async def test_barcode_lookup_sends_only_normalized_gtin13():
    client = RecordingClient()
    assert await client.food_id_by_barcode("036000291452") == "4384"
    assert client.calls == [
        ("food.find_id_for_barcode", {"barcode": "0036000291452"})
    ]


@pytest.mark.asyncio
async def test_invalid_or_gtin14_barcode_skips_http():
    client = RecordingClient()
    assert await client.food_id_by_barcode("4006381333932") is None
    assert await client.food_id_by_barcode("10012345678902") is None
    assert client.calls == []
```

- [ ] **Step 6: Run the boundary test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_fatsecret_client.py -q
```

Expected: raw UPC-A is present in `client.calls` and invalid codes call `_call`.

- [ ] **Step 7: Enforce normalization in the FatSecret client**

Import `fatsecret_gtin13` in `client.py` and change the method to:

```python
async def food_id_by_barcode(self, barcode: str) -> str | None:
    normalized = fatsecret_gtin13(barcode)
    if normalized is None:
        return None
    payload = await self._call("food.find_id_for_barcode", barcode=normalized)
    food_id = (payload.get("food_id") or {}).get("value")
    return str(food_id) if food_id and str(food_id) != "0" else None
```

UPC-E decoded by pyzbar has already become UPC-A inside `plausible`; typed eight-digit
codes are treated as EAN-8.

- [ ] **Step 8: Verify Task 1**

Run:

```bash
.venv/bin/python -m pytest tests/test_barcodes.py tests/test_fatsecret_client.py -q
.venv/bin/python -m pytest -q
```

Expected: both commands pass with zero failures.

- [ ] **Step 9: Commit Task 1**

```bash
git add src/fsbot/domain/barcodes.py src/fsbot/fatsecret/client.py tests/test_barcodes.py tests/test_fatsecret_client.py
git commit -m "fix: нормализовать GTIN перед поиском FatSecret"
```

---

### Task 2: Parse-aware OpenRouter model fallback

**Files:**
- Modify: `src/fsbot/llm/openrouter.py:125-158,271-286`
- Create: `tests/test_openrouter_fallback.py`

**Interfaces:**
- Produces private `_complete_one(model: str, messages: list[dict], schema: bool) -> str`.
- Preserves `_complete(models, messages, schema) -> str` for translation and web lookup.
- Changes `_recognize()` success condition to a parsed `Recognition`.

- [ ] **Step 1: Write failing fallback tests**

Create `tests/test_openrouter_fallback.py` using `httpx.MockTransport`:

```python
import json

import httpx
import pytest

from fsbot.llm.openrouter import OpenRouter


VALID = {
    "choices": [{"message": {"content": (
        '{"kind":"text","items":[{"query_en":"oats",'
        '"name_ru":"овсянка","amount":60,"unit":"g"}]}'
    )}}]
}


def client_with(handler):
    client = OpenRouter("key", ["bad", "good"], ["bad", "good"])
    old = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, old


@pytest.mark.asyncio
async def test_invalid_primary_response_falls_back_to_second_model():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        payload = VALID if body["model"] == "good" else {
            "choices": [{"message": {"content": "not json"}}]
        }
        return httpx.Response(200, json=payload)

    client, old = client_with(handler)
    await old.aclose()
    try:
        result = await client.recognize_text("овсянка 60 г")
    finally:
        await client.close()

    assert result.items[0].query_en == "oats"
    assert [body["model"] for body in seen] == ["bad", "bad", "good"]


@pytest.mark.asyncio
async def test_repair_can_save_current_model_without_using_fallback():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        payload = VALID if len(seen) == 2 else {
            "choices": [{"message": {"content": "not json"}}]
        }
        return httpx.Response(200, json=payload)

    client, old = client_with(handler)
    await old.aclose()
    try:
        await client.recognize_text("овсянка 60 г")
    finally:
        await client.close()

    assert [body["model"] for body in seen] == ["bad", "bad"]
    assert seen[0]["provider"] == {"require_parameters": True}
    assert "provider" not in seen[1]
```

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_openrouter_fallback.py -q
```

Expected: first test raises `LLMError` after retrying only model `bad`.

- [ ] **Step 3: Extract one-model HTTP completion**

Move the body of the current loop into `_complete_one`. It must:

- add `provider={"require_parameters": True}` only when `schema=True`;
- catch `httpx.HTTPError`, `ValueError`, missing/non-list choices, missing message,
  and non-string content;
- raise `LLMError` with one status (`response.status_code` or `0`) rather than leak
  envelope exceptions.

Keep `_complete()` as a thin loop that calls `_complete_one()` and aggregates its
messages/statuses for non-recognition callers.

- [ ] **Step 4: Make `_recognize` own the parse-aware loop**

Implement this control flow:

```python
async def _recognize(self, models, messages):
    errors = []
    statuses = []
    for model in models:
        try:
            raw = await self._complete_one(model, messages, schema=True)
            return parse_recognition(raw)
        except (LLMError, ParseError) as first:
            errors.append(f"{model}: {first}")
            statuses.extend(first.statuses if isinstance(first, LLMError) else [0])

        retry = [*messages, {"role": "user", "content": "Верни только валидный JSON."}]
        try:
            raw = await self._complete_one(model, retry, schema=False)
            return parse_recognition(raw)
        except (LLMError, ParseError) as second:
            errors.append(f"{model} repair: {second}")
            statuses.extend(second.statuses if isinstance(second, LLMError) else [0])

    raise LLMError("; ".join(errors) or "нет доступных моделей", statuses)
```

Keep warning logs for the first parse failure and an info/warning entry when moving
to the next model. Do not log raw user text or image bytes.

- [ ] **Step 5: Verify Task 2**

```bash
.venv/bin/python -m pytest tests/test_openrouter_fallback.py tests/test_llm_errors.py tests/test_llm_parsing.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass and no unclosed-client warnings appear.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/fsbot/llm/openrouter.py tests/test_openrouter_fallback.py
git commit -m "fix: переключать LLM после невалидного ответа"
```

---

### Task 3: Shared nutrition plausibility validation

**Files:**
- Create: `src/fsbot/domain/nutrition.py`
- Create: `tests/test_nutrition.py`
- Modify: `src/fsbot/llm/parsing.py:175-191`
- Modify: `src/fsbot/foodfacts.py:58-70`
- Modify: `tests/test_llm_parsing.py`
- Modify: `tests/test_foodfacts.py`

**Interfaces:**
- Produces: `plausible(kcal: float, protein: float, fat: float, carbs: float) -> bool`.
- LLM and OFF both treat `False` as absence of a complete nutrition profile.

- [ ] **Step 1: Write failing validator tests**

Create `tests/test_nutrition.py`:

```python
import math

import pytest

from fsbot.domain.nutrition import plausible


@pytest.mark.parametrize(
    "values",
    [
        (186, 12, 12, 6.7),
        (60, 3, 3.2, 4.7),
        (231, 0, 0, 0),
    ],
)
def test_plausible_profiles_are_accepted(values):
    assert plausible(*values)


@pytest.mark.parametrize(
    "values",
    [
        (math.nan, 1, 1, 1),
        (math.inf, 1, 1, 1),
        (1001, 1, 1, 1),
        (100, -1, 1, 1),
        (100, 101, 1, 1),
        (50, 25, 25, 25),
    ],
)
def test_impossible_profiles_are_rejected(values):
    assert not plausible(*values)
```

- [ ] **Step 2: Run validator tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_nutrition.py -q
```

Expected: import fails because `fsbot.domain.nutrition` does not exist.

- [ ] **Step 3: Implement the minimal validator**

Create `src/fsbot/domain/nutrition.py`:

```python
from __future__ import annotations

import math

MAX_KCAL = 1000.0
MAX_MACRO = 100.0
MIN_ENERGY_TOLERANCE = 80.0
RELATIVE_ENERGY_TOLERANCE = 0.35


def plausible(kcal: float, protein: float, fat: float, carbs: float) -> bool:
    values = (kcal, protein, fat, carbs)
    if not all(math.isfinite(value) for value in values):
        return False
    if not 0 < kcal <= MAX_KCAL:
        return False
    if any(not 0 <= value <= MAX_MACRO for value in (protein, fat, carbs)):
        return False
    macro_energy = 4 * protein + 9 * fat + 4 * carbs
    if macro_energy < 1:
        return True
    tolerance = max(MIN_ENERGY_TOLERANCE, RELATIVE_ENERGY_TOLERANCE * kcal)
    return abs(macro_energy - kcal) <= tolerance
```

- [ ] **Step 4: Run validator tests and verify GREEN**

Run the same command. Expected: all validator tests pass.

- [ ] **Step 5: Write failing integration regressions**

Add to `tests/test_llm_parsing.py`:

```python
def test_impossible_complete_nutrition_is_refused():
    raw = '''{"kind":"label","items":[{"query_en":"x","name_ru":"Икс",
      "amount":100,"unit":"g","kcal_100g":50,"protein_100g":25,
      "fat_100g":25,"carbs_100g":25}]}'''
    assert parse_recognition(raw).items[0].nutrition is None
```

Add to `tests/test_foodfacts.py`:

```python
def test_impossible_complete_nutrition_is_refused():
    payload = {**TUNA_SALAD, "product": {
        **TUNA_SALAD["product"],
        "nutriments": {
            "energy-kcal_100g": 50,
            "proteins_100g": 25,
            "fat_100g": 25,
            "carbohydrates_100g": 25,
        },
    }}
    assert parse_product(payload) is None
```

- [ ] **Step 6: Run integration regressions and verify RED**

```bash
.venv/bin/python -m pytest tests/test_llm_parsing.py::test_impossible_complete_nutrition_is_refused tests/test_foodfacts.py::test_impossible_complete_nutrition_is_refused -q
```

Expected: both tests fail because the impossible profile is currently accepted.

- [ ] **Step 7: Apply one validator at both boundaries**

Import `fsbot.domain.nutrition` under an unambiguous alias in both modules.

In `_parse_nutrition`, construct `Nutrition` only if:

```python
if not nutrition_rules.plausible(**values):
    return None
return Nutrition(**values)
```

In `foodfacts.parse_product`, after parsing all values:

```python
if not nutrition_rules.plausible(
    values["kcal_100g"],
    values["protein_100g"],
    values["fat_100g"],
    values["carbs_100g"],
):
    return None
```

Remove the now-redundant local `kcal <= 0` checks.

- [ ] **Step 8: Verify Task 3**

```bash
.venv/bin/python -m pytest tests/test_nutrition.py tests/test_llm_parsing.py tests/test_foodfacts.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 3**

```bash
git add src/fsbot/domain/nutrition.py src/fsbot/llm/parsing.py src/fsbot/foodfacts.py tests/test_nutrition.py tests/test_llm_parsing.py tests/test_foodfacts.py
git commit -m "fix: проверять правдоподобие КБЖУ"
```

---

### Task 4: Timezone-aware manual date selection

**Files:**
- Modify: `src/fsbot/bot/pipeline.py:294-299`
- Modify: `src/fsbot/bot/handlers.py:609-720`
- Modify: `tests/test_daybounds.py`

**Interfaces:**
- Changes: `shift_day(draft: dict, hint: str, tz: str, now_utc: datetime | None = None) -> None`.
- Callback receives existing `Config` dependency and uses `user.tz or cfg.default_tz`.

- [ ] **Step 1: Write failing date tests**

Add to `tests/test_daybounds.py`:

```python
from fsbot.bot.pipeline import shift_day


def test_shift_day_uses_users_timezone_and_diary_boundary():
    moment = utc("2026-07-30 02:30")
    tbilisi = {"day": "2000-01-01"}
    los_angeles = {"day": "2000-01-01"}

    shift_day(tbilisi, "today", "Asia/Tbilisi", moment)
    shift_day(los_angeles, "today", "America/Los_Angeles", moment)

    assert tbilisi["day"] == "2026-07-30"
    assert los_angeles["day"] == "2026-07-29"


def test_shift_day_yesterday_is_relative_to_diary_today():
    draft = {"day": "2000-01-01"}
    shift_day(draft, "yesterday", "Asia/Tbilisi", utc("2026-07-30 02:30"))
    assert draft["day"] == "2026-07-29"


def test_unknown_shift_hint_keeps_existing_date():
    draft = {"day": "2026-01-02"}
    shift_day(draft, "tomorrow", "Asia/Tbilisi", utc("2026-07-30 02:30"))
    assert draft["day"] == "2026-01-02"
```

- [ ] **Step 2: Run date tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_daybounds.py -q
```

Expected: `shift_day` rejects the added timezone and clock arguments.

- [ ] **Step 3: Implement timezone-aware `shift_day`**

Import `datetime` for the type annotation and reuse the existing `resolve` import:

```python
def shift_day(
    draft: dict,
    hint: str,
    tz: str,
    now_utc: datetime | None = None,
) -> None:
    if hint not in {"today", "yesterday"}:
        return
    today, _ = resolve(tz, now_utc)
    target = today if hint == "today" else today - timedelta(days=1)
    draft["day"] = target.isoformat()
```

Remove the obsolete `date` import only if no other pipeline function uses it; it is
still required by `write_draft`, so keep it.

- [ ] **Step 4: Update the callback call site**

Add `cfg: Config` to `callbacks()` dependencies. In `PICK_DATE`, fetch the callback
user and call:

```python
user = await storage.get_user(call.from_user.id)
tz = user.tz if user and user.tz else cfg.default_tz
shift_day(draft, arg, tz)
```

No storage migration or callback_data change is required.

- [ ] **Step 5: Verify Task 4**

```bash
.venv/bin/python -m pytest tests/test_daybounds.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/fsbot/bot/pipeline.py src/fsbot/bot/handlers.py tests/test_daybounds.py
git commit -m "fix: учитывать timezone при ручном выборе даты"
```

---

### Task 5: Final verification and handoff

**Files:**
- Review only: all files changed in Tasks 1-4
- Modify only if verification exposes a regression, following a new RED/GREEN cycle

**Interfaces:**
- Consumes all deliverables from Tasks 1-4.
- Produces verification evidence and a clean working tree.

- [ ] **Step 1: Check formatting and accidental changes**

```bash
git diff --check HEAD~4..HEAD
git status --short
```

Expected: no whitespace errors; only explicitly intended uncommitted artifacts, or a
clean tree if every task was committed.

- [ ] **Step 2: Run the complete test suite fresh**

```bash
.venv/bin/python -m pytest -q
```

Expected: zero failures.

- [ ] **Step 3: Compile all Python sources and tests**

```bash
.venv/bin/python -m compileall -q src tests
```

Expected: exit code 0 and no output.

- [ ] **Step 4: Build and run the Docker test stage when Docker is available**

```bash
docker build --target test -t fsbot:test .
docker run --rm --network none fsbot:test
```

Expected: image builds and all tests pass without network. If Docker is unavailable,
record the exact environment error rather than claiming this verification succeeded.

- [ ] **Step 5: Review requirements against the spec**

Confirm explicitly:

1. invalid/unsupported GTIN causes zero FatSecret HTTP calls;
2. parse failure on model one reaches model two;
3. LLM and OFF import the same nutrition validator;
4. `shift_day` has no `date.today()` dependency;
5. no draft schema or callback_data changed.

- [ ] **Step 6: Report the result**

Include changed behavior, exact test counts, Docker verification status, commit hashes,
and any intentionally deferred work from the spec. Do not claim success for a command
that was not run in this final verification task.
