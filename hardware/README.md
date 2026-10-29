# Toolkit hardware

Hardware tooling for OpenDAP that has not moved to its domain folder yet, and the [component export contract](docs/specs/component-export.md) every component follows.

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit. Its content moves into the tools root's domain folders (HL-71): the craft checks, `odw stackup` and `odw variant` are in [`electronics/`](../electronics/README.md), `odw sim` in [`sim/`](../sim/README.md), `odw parts` in [`parts/`](../parts/README.md), and `odw component`, `clihost.py` and the KiCad S-expression model in [`core/`](../core/README.md).

## Tools

- `odw render` — a component's committed visual outputs (RFC 0021): `tools/render/`, its tests (`tests/test_render.py`) and fixtures (`tests/fixtures/render/`).

## Layout

- `bin/` — `odw-render` (and its `odapkit-render` link)
- `tools/render/` — the engine of `odw render`
- `tools/freecad-export.py` — STEP and other exports from FreeCAD
- `tools/pyproject.toml`, `tools/uv.lock` — the Python dependencies of `odw stackup`, `odw component` and the craft rules' schema test (numpy, scipy, scikit-fem, pyyaml, jsonschema), hash-locked, until the one Python workspace at the root
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `docs/` — requirements and the component export contract

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)
- `tests/fixtures/render/`'s schematic: **CERN-OHL-W-2.0**, a copy of kicad-lib's example

Copyright © 2026 The OpenDAP Project.
