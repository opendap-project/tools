# Toolkit parts

Part data for OpenDAP: the sourcing evaluation matrix and the sync of the files parts-lib lists.

## Layout

- `bin/odw-parts` — `odw parts matrix CANDIDATES.toml` — a sourcing evaluation matrix: stock, price breaks at the chosen quantities, lifecycle status and fetch date per part and channel (Digi-Key and LCSC without keys; Mouser, Newark, Arrow and TTI through their APIs when `MOUSER_API_KEY`, `ELEMENT14_API_KEY`, `ARROW_API_KEY` with `ARROW_LOGIN`, or `TTI_API_KEY` are set, and through the FindChips aggregator otherwise), scored availability first, then lifecycle, then price, with the reason each candidate passes or fails; writes Markdown, CSV and JSON to the `--out` folder, which is required and belongs in a private location: sourcing data is not committed to public repositories ([method and file format](docs/specs/sourcing-matrix.md)). `odw parts rk-ddr-list` reads Rockchip's DDR SDRAM Support List (PDF, through poppler's `pdftotext`) into rows or candidate stubs; `odw parts channels` shows which keys this host has. Standard library only.
- `sourcing/` — the engine of `odw parts`: the channel adapters, the matrix and its scoring, the command line (`partscli.py`)
- `selection/rkddr.py` — the Rockchip DDR SDRAM Support List reader behind `odw parts rk-ddr-list`
- `parts-sync.py` — fetches the files listed in parts-lib's `manifest.toml` into `~/.cache/opendap/parts/` (`--mpn` for one part; `--check` verifies the cache and downloads nothing); `odw sim fetch` uses it for models
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `sourcing/tests/` — the engine on synthetic fixtures, offline; `tests/` — the command-line contract of `odw parts` and `parts-sync.py`; both run by pre-commit
- `docs/specs/sourcing-matrix.md` — the method and the file format

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
