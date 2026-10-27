# Toolkit hooks

Shared [pre-commit](https://pre-commit.com) hooks for every OpenDAP repository. Everything runs locally; there is no CI.

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit, which lives in the workspace under `public/toolkit/` as one repository per tool domain.

## Hooks

| ID | What it checks |
|---|---|
| `kicad-erc` | ERC on changed KiCad schematics |
| `kicad-drc` | DRC on changed KiCad boards |
| `no-fab-outputs` | No generated fab outputs (Gerbers, drill files, packages) are committed |
| `no-stray-files` | No PDFs, and no HTML outside `renders/web/` |
| `kicad-dru-composed` | Every `<project>.kicad_dru` with a `<project>.dru.toml` manifest equals the composition of the rule sources it lists; no duplicate rule names |

Use them from a repository's `.pre-commit-config.yaml`, pinned to a tag:

```yaml
  - repo: https://github.com/opendap-project/toolkit-hooks
    rev: v0.4.1
    hooks:
      - id: no-stray-files
```

## Composed KiCad rule files

A KiCad project reads one `<project>.kicad_dru`. OpenDAP composes it from a fab rule set (kicad-lib, through the `hardware/lib/opendap` submodule) and the board's product rules. The sources are listed, in order, in a manifest next to the project:

```toml
# hardware/t1-board.dru.toml
version = 1
sources = [
  "hardware/lib/opendap/design-rules/pcbway-hdi/pcbway-hdi.kicad_dru",
  "hardware/rules/t1-board.kicad_dru",
]
```

Paths are relative to the repository root. The composed file has `(version 1)` once, then each source's rules, each block preceded by a comment with the source path and its sha256; two rules with the same name are an error. `hooks/kicad-dru.py compose MANIFEST` writes it and `hooks/kicad-dru.py check [--json] [MANIFEST…]` verifies it; with no manifest, `check` covers every `*.dru.toml` in the repository. Python 3.11 or later, standard library only.

The same engine is the maintainers' `odapkit dru compose|check` (`bin/odapkit-dru`, declared in `tools.toml`), so the tool and the `kicad-dru-composed` hook never disagree; one test suite covers both.

## Layout

- `.pre-commit-hooks.yaml` — hook definitions
- `hooks/` — the hook scripts and their engines
- `bin/` — the tools this repository adds to `odapkit` (`tools.toml`), thin wrappers over the engines in `hooks/`
- `lib/cli.sh` — the command-line helpers of the shell wrappers (toolkit-host's, with a minimal fallback)
- `tests/` — tests and their fixtures: `python3 -m unittest discover tests` (pre-commit runs them)

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
