# Python migration oracle

`python-parity.json` contains 1,415 deterministic results from the Python version
at commit `54c7f511de1d898b902aa4f5c5003ffdfd8bd417`:

- GTIN validation and FatSecret normalization;
- diary dates and meals across five IANA timezones, including DST and year changes;
- nutrition validation;
- LLM recognition parsing;
- serving selection, nutrients and API units.

`python-state.sqlite` is a synthetic database created by that same Python version,
including a linked user, timezone, barcode binding, written and uncertain items,
and an undo batch. It contains no real credentials.

`TestPythonParity` runs these cases against Go without Python or external services.
The old implementation's 194 tests passed in Docker before freezing the corpus.

To regenerate from the committed baseline (no credentials or live API calls):

```sh
mkdir -p /tmp/fsbot-python-baseline
git archive 54c7f511de1d898b902aa4f5c5003ffdfd8bd417 | tar -x -C /tmp/fsbot-python-baseline
docker build --target test -t fsbot:python-baseline /tmp/fsbot-python-baseline
docker run --rm --network none \
  -v "$PWD/tools/migration:/scripts:ro" -v "$PWD/testdata:/out" \
  fsbot:python-baseline python /scripts/generate_parity.py
```

The Go suite additionally checks SQLite migration, concurrent claims, partial
writes, uncertain outcomes, incremental undo, HTTP contracts, Telegram callbacks,
local barcode images and runtime health.
