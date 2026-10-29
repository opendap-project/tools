# Toolkit sim

The study runner of the tools root: a board's simulation regression checks, run against limits taken from its constraints and ADRs (ngspice today).

## Layout

- `bin/odw-sim` — `odw sim [run|list|fetch]`: runs the checks of a board's `sim/*/checks.toml` ([study format](docs/specs/study-format.md)); `--dry-run` names what would run and writes nothing, `list --json` prints `opendap.sim-checks/v1`
- `__main__.py`, `simchecks.py` — the engine (`bin/odw-sim` only runs it); it loads the shared command-line helpers through core's `clihost.py`, and fetches models with the parts tool's sync
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `tests/` — the runner on an RC fixture (`test_runner.py`, needs ngspice) and the command-line contract (`test_cli.py`): `python3 -m unittest discover tests` from this folder; run by pre-commit
- `docs/specs/study-format.md` — the format of a study and its checks

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
