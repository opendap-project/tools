# Toolkit hooks

Shared [pre-commit](https://pre-commit.com) hooks for every OpenDAP repository. Everything runs locally; there is no CI.

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit, which lives in the workspace under `public/toolkit/` as one repository per tool domain.

## Hooks

| ID | What it checks |
|---|---|
| `kicad-erc` | ERC on changed KiCad schematics (errors only) |
| `kicad-drc` | DRC, with schematic parity, on changed KiCad boards (errors only) |
| `no-fab-outputs` | No generated fab outputs (Gerbers, drill files, packages) are committed |
| `no-stray-files` | No PDFs, and no HTML outside `renders/web/` |
| `kicad-dru-composed` | Every `<project>.kicad_dru` with a `<project>.dru.toml` manifest equals the composition of the rule sources it lists; no duplicate rule names |

Use them from a repository's `.pre-commit-config.yaml`, pinned to a tag:

```yaml
  - repo: https://github.com/opendap-project/tools/tree/main/hooks
    rev: v0.5.0
    hooks:
      - id: no-stray-files
```

## KiCad ERC and DRC

`kicad-erc` and `kicad-drc` run `hooks/kicad-check.py erc|drc FILE…` on the changed files. Only a file with a same-named `.kicad_pro` next to it is checked, with `kicad-cli` at error severity; a failing project prints `KiCad ERC failed: FILE` (or DRC) and the first 60 lines of the report. `--json` also prints an [`opendap.check-result/v1`](../host/schemas/check-result.schema.json) document on stdout: one result per file (`pass`, `fail`, `skip` outside a project, `error` when `kicad-cli` gave no report) and one per violation, with its KiCad type as `rule`. `kicad-cli` is `$KICAD_CLI`, else KiCad.app's (`$KICAD_APP`, default `/Applications/KiCad/KiCad.app`), else the first on `PATH`. Python 3.9 or later, standard library only. `hooks/kicad-check.sh` is the former entry point and runs the same engine.

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

Paths are relative to the component's folder: the repository root, or, in a root that merged several components (the `t1` root's `board/`), the nearest folder above the manifest from which every source resolves. The composed file has `(version 1)` once, then each source's rules, each block preceded by a comment with the source path and its sha256; two rules with the same name are an error. `hooks/kicad-dru.py compose MANIFEST` writes it (`--dry-run` composes and names the file, writing nothing) and `hooks/kicad-dru.py check [--json] [MANIFEST…]` verifies it; with no manifest, `check` covers every `*.dru.toml` in the repository. Python 3.11 or later, standard library only.

The same engine is the maintainers' `odapkit dru compose|check` (`bin/odapkit-dru`, declared in `tools.toml`), so the tool and the `kicad-dru-composed` hook never disagree; one test suite covers both.

## Layout

- `.pre-commit-hooks.yaml` — hook definitions
- `hooks/` — the hook scripts and their engines
- `bin/` — the tools this repository adds to `odapkit` (`tools.toml`), thin wrappers over the engines in `hooks/`
- `lib/cli.sh` — the command-line helpers of the shell wrappers (toolkit-host's, with a minimal fallback): `host/` of the same repository when this checkout is one of its folders (the merged `tools` root), else `ODAP_TOOLKIT_HOST_DIR`, else the `toolkit-host` row of `repos.tsv` (`tests/test_host_lookup.py` builds both layouts)
- `tests/` — tests and their fixtures: `python3 -m unittest discover tests` (pre-commit runs them). They pass in both toolkit layouts: this repository, and `hooks/` of the merged `tools` root, where `.pre-commit-hooks.yaml` sits at the root with entries under `hooks/hooks/`

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
