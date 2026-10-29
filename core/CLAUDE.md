# Toolkit core

What every domain of the tools root shares: command-line conventions, their conformance test, the evaluation-matrix engine.

## Working here

- `odw/` is part of one `odw` package across the domain folders: a module here must not import a domain's module, only the other way round.
- `odw.clikit` loads on Python 3.9 (KiCad's): no 3.10+ syntax and no `tomllib` at import time (`tests/test_clikit.py` checks it).
- Tests run from the tools root (`uv run pytest`), under the root's `conftest.py`.
