# Python migration oracle

`python-parity.json` contains 1,415 deterministic results from the Python version
at commit `54c7f511de1d898b902aa4f5c5003ffdfd8bd417`:

- GTIN validation and FatSecret normalization;
- diary dates and meals across five IANA timezones, including DST and year changes;
- nutrition validation;
- LLM recognition parsing;
- serving selection, nutrients and API units.

`TestPythonParity` runs these cases against Go without Python or external services.
The old implementation's 194 tests passed in Docker before freezing the corpus.

Database migration/old-state/runtime integration tests and their fixture generator
were removed by request. The frozen JSON corpus remains used by isolated Go tests.
