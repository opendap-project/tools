# Toolkit workspace

The maintainers' workspace tools: `odw` itself (dispatch, discovery, `check`, `doctor`), the programme plan, the audit, the shared files and the cut engine of the restructure.

## Layout

- `crates/odw-dispatch` — `odw`, the maintainers' umbrella command ([command standard](../core/docs/specs/command-standard.md) §1–§4): `odw <tool>` runs the tool a workspace root's `tools.toml` declares (then `odw-<tool>` or `odapkit-<tool>` on `PATH`); `odw help [--json] [--all]` lists them with summary, audience and stability; `odw check [--stage fast|full|release] [PATHS…]` runs a root's `check.toml` stage on the changed paths and reports `opendap.check-result/v1`; `odw doctor [--json]` reports duplicate names, unsafe or invalid `tools.toml` entries, summaries over 100 characters and tools whose `--help` fails. The dispatch is a library the `opendap` CLI can reuse. `setup.sh` builds it and links it into `~/.local/bin`
- `bin/odw-plan` — a maintainers' programme as data (tasks, decisions, claims, generated index, and `mirror`: its public tasks as one tracking issue per accepted RFC): [programme plan](docs/specs/programme-plan.md)
- `bin/odw-audit` — workspace metrics from every repository's tracked files, with dated snapshots: [workspace audit](docs/specs/workspace-audit.md)
- `bin/odw-sync` — shared boilerplate from the workspace templates: `--check` reports drift, `--apply` repairs it; it also generates each repository's `.github/CODEOWNERS` from the workspace's `owners.tsv`: [workspace sync](docs/specs/workspace-sync.md)
- `bin/odw-rename` — one root's cut from the rename map ([RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md) §7): `merge` builds the root repository from its sources with their history, `plan` lists every move and link, URL and identifier rewrite, `apply` performs them with `git mv` and local commits, `check` lists the old names left, `links` the old and broken repository URLs left across the workspace after the cuts: [rename](docs/specs/rename.md)
- `odw/` — their modules of the `odw` package (`audit`, `plan`, `plan_mirror`, `sync`, `owners`, `check_hooks`, and `rename*`, the cut engine)
- `rust-toolchain.toml` — the Rust toolchain `odw` builds with; `setup.sh` builds it from this folder into `target/`
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `tests/` — their tests, run by `uv run pytest` from the tools root; the crate's tests run with `cargo test` from this folder
- `docs/specs/` — the programme plan, the workspace audit, workspace sync and the rename (cut) engine

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)
- `crates/odw-dispatch/`: **Mozilla Public License 2.0** (`MPL-2.0`), a library the `opendap` CLI builds on

Copyright © 2026 The OpenDAP Project.
