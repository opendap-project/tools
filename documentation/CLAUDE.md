# Toolkit documentation

The documentation engines: `odw req`, `odw docs`, `odw verify`.

## Working here

- `odw/` is part of one `odw` package across the domain folders; it may import `core/`'s modules, never another domain's.
- Tests run from the tools root (`uv run pytest`), under the root's `conftest.py`.
