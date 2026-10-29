# Toolkit core

What every domain of the tools root shares: the command-line conventions and their conformance test, the evaluation-matrix engine, and the `odw` package's shared modules.

## Layout

- `odw/` — the shared modules of the `odw` package, which every domain folder's `odw/` joins (`__init__.py`): `clikit` (the command-line behaviour every tool shares: `--help` from `tools.toml`, errors with a code, a hint and a fix, exit status 0, 1, 2), `schemas` (the validator of the root's [`schemas/`](../schemas/README.md)), `workspace` (finding the workspace and its roots), `evaluate` (the [evaluation matrix](docs/specs/evaluation-matrix.md) engine), and `cli_inventory`, `cli_probe`, `cli_rules`, `cli_conformance` (the [command-line conformance](docs/specs/cli-conformance.md) test)
- `lib/cli.sh` — the same behaviour for POSIX shell tools, sourced by every folder's shell plugins
- `clihost.py` — how the Python engines of every domain load `odw.clikit` (core/ on their path, then `clihost.load()`), on KiCad's Python 3.9 too
- `kicad/sexpr.py` — the one KiCad S-expression model the craft checks read
- `render/`, `bin/odw-render` — `odw render list|update|check` ([spec](docs/specs/render-tool.md); RFC 0021): a component's committed visual outputs, generated and checked by their source-hash stamps; the dispatcher, the output manifest, the stamps and the normalisation are here, the KiCad adapter in `electronics/render/` and the CAD adapters in `mechanical/render/`; tests in `render/tests/`
- `component/`, `bin/odw-component` — `odw component validate`: a component's `export/component.yaml` against the [component export contract](../hardware/docs/specs/component-export.md) and [`schemas/component.schema.json`](../schemas/component.schema.json); it runs in the hardware tools' uv project until the one Python workspace
- `bin/odw-cli` — `odw cli`: every command-line surface of the public roots, inventoried and checked against the [command standard](docs/specs/command-standard.md) (`list`, `check`); writes the conformance matrix
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `tests/` — the tests of these modules, run by `uv run pytest` from the tools root; `tests/git-leak.sh DIR COMMAND…` proves a suite never writes into the repository a hook commits (HL-64); `tests/clifixtures.py` holds the fixture helpers other domains' tests share
- `docs/specs/` — the command standard, command-line conformance, the evaluation matrix and the render tool

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
