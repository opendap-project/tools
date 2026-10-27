# Toolkit hardware

Hardware tooling for OpenDAP: fab packages, FreeCAD exports, part data sync, and the [component export contract](docs/specs/component-export.md) every component follows.

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit, which lives in the workspace under `public/toolkit/` as one repository per tool domain.

## Tools

Reached through the Developer Toolkit's command, `odapkit <tool>` (RFC 0009), from `bin/`, and declared in `tools.toml`, whose summary is the first line of each tool's `--help`. Every tool follows the [command standard](https://github.com/opendap-project/toolkit-host/blob/main/docs/specs/command-standard.md): an unknown flag exits 2; errors carry a stable dotted code, a hint and a fix, from toolkit-host's [shared helpers](https://github.com/opendap-project/toolkit-host/blob/main/docs/specs/cli-conformance.md#shared-helper) (`tools/clihost.py`, `lib/cli.sh`); `--json` documents carry a `schema`; `odapkit sim` and `odapkit parts matrix` take `--dry-run`.

- `odapkit dru compose|check` — composes a KiCad project's `<project>.kicad_dru` from the fab rule set and the product rules listed in `<project>.dru.toml`, or checks that it is up to date. It runs [toolkit-hooks](https://github.com/opendap-project/toolkit-hooks)' `kicad-dru.py`, the script of the `kicad-dru-composed` pre-commit hook, where the manifest format is documented.
- `odapkit sim [run|list|fetch]` — runs the simulation regression checks of a board's `sim/*/checks.toml` ([spec](docs/specs/simulation-checks.md)).
- `odapkit craft check [--json] [--target Qn] [--no-drc] BOARD.kicad_pcb` — checks a board against the parts of kicad-lib's [PCB craft standard](https://github.com/opendap-project/kicad-lib/blob/main/docs/specs/pcb-craft-standard.md) that DRC cannot check (reference designators, pin-1 marks, markings, placement grid and groups, mirrored channels, drill and via sets, zones, documentation layers), folds in the board's DRC rules tagged `[Qn]`, and reports the results by quality level (RFC 0010) with the level the board reaches. Runs [`tools/pcb-craft-check.py`](tools/pcb-craft-check.py) with KiCad's Python; configuration in `<project>.craft.toml`.
- `odapkit craft check [--json] [--target Qn] [--no-erc] ROOT.kicad_sch` and `odapkit craft check LIBRARY.kicad_sym` — the schematic and symbol part of the standard (§9): title block and drawing sheet, one sheet per feature block and its interface, symbol rules (KLC), fields, net names and label kinds, net classes against `netclasses.toml`, grid, junctions, text and tables; folds in the project's ERC and reports by level. Runs [`tools/sch-craft-check.py`](tools/sch-craft-check.py) with python3 and kicad-cli; configuration in the `[schematic]` table of `<project>.craft.toml`.
- `odapkit craft rules render SPEC.md [--check]` — the craft rules are data: [`tools/craft/rules.toml`](tools/craft/rules.toml) (schema [`opendap.craft-rules/v1`](schemas/craft-rules.schema.json)) holds every CRAFT rule with its section, level, rule, value, reason, source and enforcement exactly as the standard states them, plus the limits the checks read (`[rule.limits]`, each value stated in its rule's row). Both craft checks take their levels and limits from it. `render` writes the standard's rule tables from it between their `<!-- >>> odapkit craft rules §<section>: … -->` and `<!-- <<< odapkit craft rules -->` markers; `--check` reports a stale table and exits 1. `tests/fixtures/craft-rules/pcb-craft-standard.md` is the standard with its tables marked.
- `odapkit stackup stack|design|check|validate|coupling|explore|pdn` — a board's layer stack and the trace geometry of its impedance classes from `hardware/stackup/stackup-<version>.toml`: a 2D quasi-static field solver (scikit-fem, pinned and hash-locked in `tools/stackup/uv.lock`, run through uv) with tolerance corners and a validation against closed forms; the zoned layer-count exploration with variant matrices, broadside and edge coupling, and a lumped PDN model for the plane pairing ([method](docs/specs/stackup-method.md)).
- `odapkit parts matrix CANDIDATES.toml` — a sourcing evaluation matrix: stock, price breaks at the chosen quantities, lifecycle status and fetch date per part and channel (Digi-Key and LCSC without keys; Mouser, Newark, Arrow and TTI through their APIs when `MOUSER_API_KEY`, `ELEMENT14_API_KEY`, `ARROW_API_KEY` with `ARROW_LOGIN`, or `TTI_API_KEY` are set, and through the FindChips aggregator otherwise), scored availability first, then lifecycle, then price, with the reason each candidate passes or fails; writes Markdown, CSV and JSON to the `--out` folder, which is required and belongs in a private location: sourcing data is not committed to public repositories ([method and file format](docs/specs/sourcing-matrix.md)). `odapkit parts rk-ddr-list` reads Rockchip's DDR SDRAM Support List (PDF, through poppler's `pdftotext`) into rows or candidate stubs; `odapkit parts channels` shows which keys this host has. Standard library only.
- `odapkit component validate` — validates `export/component.yaml` against [`schemas/component.schema.json`](schemas/component.schema.json) ([contract](docs/specs/component-export.md)).

Tests: `python3 -m unittest discover tests` (`test_parts.py` runs offline on synthetic fixtures; the others need ngspice, uv (also for `odapkit stackup`), KiCad 10, and toolkit-hooks as the sibling `../hooks`). The craft-check fixtures are generated by `tests/fixtures/craft/make_fixture.py` (run with KiCad's Python); the schematic fixtures are a copy of kicad-lib's `examples/load-switch` (`tests/fixtures/craft-sch/good/`) and its broken copy written by `tests/fixtures/craft-sch/make_fixture.py`. `test_craft_rules.py` checks the rules data against its schema (through uv), that the marked standard renders unchanged and is byte-identical to the standard without its markers, that every limit is stated in its rule's row, and that the checks' limits and levels equal the data; pre-commit runs it and `odapkit craft rules render --check`.

## Layout

- `bin/` — the `odapkit-*` tools
- `schemas/` — JSON Schemas (MPL-2.0)
- `tests/` — tests and their fixtures; `test_cli_contract.py` checks every tool's command line without KiCad, ngspice or the network, and pre-commit runs it
- `tools/fab-package.sh` — fab package from a KiCad project (runs ERC/DRC through [toolkit-hooks](https://github.com/opendap-project/toolkit-hooks), found as the sibling `../hooks` or `$ODAP_TOOLKIT_HOOKS_DIR`, formerly `$OPENDAP_TOOLKIT_HOOKS`); `--check` runs only the checks and writes nothing, `--json` prints `opendap.fab-package/v1`
- `tools/freecad-export.py` — STEP and other exports from FreeCAD
- `tools/pcb-craft-check.py`, `tools/sch-craft-check.py` — the board and the schematic craft checks behind `odapkit craft`: their command lines, the limits they read from the rules data and each check's ID and level
- `tools/craft/` — the craft rules as data (`rules.toml`) and their loader and table renderer (`craftrules.py`); the checks' models and reports: `pcbmodel.py` (the board) and `pcbreport.py` (DRC fold, report), `sexpr.py` (KiCad S-expressions), `schmodel.py` (the schematic and the symbol rules), `schdesign.py` (the design checks) and `schreport.py` (ERC fold, report). Each takes the check script's limits through `bind()`
- `tools/stackup/` — `odapkit stackup`: the command line and its reports (`stackup.py`), the stack model and its cross-sections (`stackmodel.py`), design, check, validation and coupling (`stackcheck.py`), the field solver (`fieldsolver.py`, closed forms in `analytic.py`), the layer-count exploration (`explore.py`, its zones and dielectrics in `explorezones.py`) and the PDN model (`pdn.py`)
- `tools/clihost.py`, `lib/cli.sh` — find toolkit-host's command-line helpers for the Python and shell tools
- `tools.toml` — the tools this root declares to the maintainers' umbrella command
- `tools/sourcing/` — the channel adapters, the matrix and the Rockchip DDR list reader behind `odapkit parts`
- `tools/parts-sync.py` — fetches the files listed in parts-lib's `manifest.toml` into `~/.cache/opendap/parts/` (`--mpn` for one part; `--check` verifies the cache and downloads nothing)
- `docs/` — requirements, the component export contract, simulation checks, the stackup method and the sourcing matrix tool

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)
- `schemas/`: **Mozilla Public License 2.0** (`MPL-2.0`)
- `tools/craft/rules.toml`: **CC-BY-SA-4.0**, the craft standard's rule tables as data
- `tests/fixtures/craft-sch/`: **CERN-OHL-W-2.0**, copied from kicad-lib's example and derived from it

Copyright © 2026 The OpenDAP Project.
