# Toolkit hardware

What is left of the former toolkit-hardware: the [component export contract](docs/specs/component-export.md) every component follows, kept at its published path until the link pass, and the hardware tools' Python project.

Its tools moved into the tools root's domain folders (HL-71): the craft checks, `odw stackup` and `odw variant` to [`electronics/`](../electronics/README.md), `odw sim` to [`sim/`](../sim/README.md), `odw parts` to [`parts/`](../parts/README.md), `odw component`, `odw render`, `clihost.py` and the KiCad S-expression model to [`core/`](../core/README.md), and the FreeCAD export to [`mechanical/`](../mechanical/README.md).

## Layout

- `tools/pyproject.toml`, `tools/uv.lock` — the Python dependencies of `odw stackup`, `odw component` and the craft rules' schema test (numpy, scipy, scikit-fem, pyyaml, jsonschema), hash-locked, until the one Python workspace at the root
- `docs/` — requirements and the component export contract

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
