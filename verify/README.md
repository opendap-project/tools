# Toolkit verify

Verification tooling for OpenDAP: simulations and samples captured from sessions, on the virtual T1 now and on prototypes later. Dedicated lab instruments are not assumed (TK-012).

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit, which lives in the workspace under `public/toolkit/` as one repository per tool domain.

## Layout

- `bin/odw-motion` — `odw motion` (a thin entry point; the engine is `tools/motion/motioncli.py`): records motion (a QEMU machine's own display over QMP, or a region of the host's screen), measures it (segments, visible duration against the design system's tokens, effective frame rate, held frames, a tracked element's curve) and extracts only the frames worth looking at, cropped, in one contact sheet. Needs ffmpeg (toolkit-host's Brewfile). `record` and `frames` take `--dry-run`; `analyze --json` prints `opendap.motion-analysis/v1`; errors carry a dotted code, a hint and a fix from toolkit-host's [shared helpers](../host/docs/specs/cli-conformance.md#shared-helper), found as `host/` of the same repository when this checkout is one of its folders (the merged `tools` root), else `$ODAP_TOOLS_DIR/host` (`odw` exports `ODAP_TOOLS_DIR`), else `ODAP_TOOLKIT_HOST_DIR` (deprecated until HL-71 phase 4), else the `toolkit-host` row of `repos.tsv` (`tests/test_host_lookup.py` builds both layouts)
- `crates/odap-inject` — `odap-inject`: touch contacts (`tap`, `swipe`) through uinput on a running instance, for automated tests (development system extension only). `--dry-run` checks the gesture and creates no device; `--json` prints `opendap.inject/v1`; errors carry a dotted code, a hint and a fix
- `tools.toml` — the tools this root declares to the maintainers' umbrella command
- `tests/cli.sh` — `odw motion`'s command-line contract (`--help`, an unknown flag, error codes, `--dry-run`), without a screen or a recording; run by pre-commit
- `tests/test_motion.py` — the engine's argument parsers and, on a synthetic recording made with ffmpeg, its analysis (segment, visible duration, nearest token, tracked curve) and contact sheet: `python3 -m unittest discover tests`; run by pre-commit
- `docs/` — requirements and the [verification spec](docs/specs/verification.md): verification levels, the bit-perfect method, the architecture, the first checks and the traceability table

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
