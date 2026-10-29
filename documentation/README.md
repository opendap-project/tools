# Toolkit documentation

The documentation engines of the tools root: requirements as data, the docs gates and the docs index, RFC application status, and verification matrices.

## Layout

- `bin/odw-req` — requirements as data: import, render with `--check`, index every repository: [requirements as data](docs/specs/requirements-data.md)
- `bin/odw-docs` — docs gates (links, private mentions, terms, working notes, status), the generated docs index and RFC application status: [docs gates](docs/specs/docs-gates.md)
- `bin/odw-verify` — verification matrices as data: render, collect check results by variant, uncovered requirements: [verification matrices](docs/specs/verification.md)
- `odw/` — their modules of the `odw` package (`docs`, `requirements`, `rfcstatus`, `verify`)
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `tests/` — their tests, run by `uv run pytest` from the tools root
- `docs/specs/` — requirements as data, docs gates and verification matrices

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
