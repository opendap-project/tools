# Toolkit schemas

The workspace formats as JSON Schemas (2020-12), validated by `odw.schemas`.

## Working here

- A format changes only with its schema, its good and bad fixtures (`tests/fixtures/schemas/<name>/`) and the table in `README.md`, in the same change.
- The schemas are MPL-2.0 so others can build on the formats; tests and fixtures are GPL-3.0-or-later (`REUSE.toml`).
- Tests: `uv run pytest schemas/tests` from the tools root.
