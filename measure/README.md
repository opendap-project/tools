# Toolkit measure

Measurement tooling for OpenDAP: what a session shows or plays, recorded and measured against the design and the requirements. It runs on the virtual T1 now and on prototypes later; dedicated lab instruments are not assumed (TK-012).

## Layout

- `bin/odw-motion` — `odw motion` (a thin entry point; the engine is `motion/motioncli.py`): records motion (a QEMU machine's own display over QMP, or a region of the host's screen), measures it (segments, visible duration against the design system's tokens, effective frame rate, held frames, a tracked element's curve) and extracts only the frames worth looking at, cropped, in one contact sheet. Needs ffmpeg (toolkit-host's Brewfile). `record` and `frames` take `--dry-run`; `analyze --json` prints `opendap.motion-analysis/v1`; errors carry a dotted code, a hint and a fix from the [shared helpers](../core/docs/specs/cli-conformance.md#shared-helper), found as `core/` of the same repository when this checkout is one of its folders (the merged `tools` root), else `$ODAP_TOOLS_DIR/core` (`odw` exports `ODAP_TOOLS_DIR`), else `ODAP_TOOLKIT_HOST_DIR` (deprecated until HL-71 phase 4; its sibling `core/` when it names `host/` of a tools root), else the `toolkit-host` row of `repos.tsv` (`tests/test_host_lookup.py` builds both layouts)
- `motion/` — the engine of `odw motion` (`motioncli.py`) and its entry point (`__main__.py`)
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `tests/cli.sh` — `odw motion`'s command-line contract (`--help`, an unknown flag, error codes, `--dry-run`), without a screen or a recording; run by pre-commit
- `tests/test_motion.py` — the engine's argument parsers and, on a synthetic recording made with ffmpeg, its analysis (segment, visible duration, nearest token, tracked curve) and contact sheet: `python3 -m unittest discover tests`; run by pre-commit
- `tests/test_host_lookup.py` — where the engine finds toolkit-host's helpers, in both toolkit layouts

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
