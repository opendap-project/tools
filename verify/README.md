# Toolkit verify

Verification tooling for OpenDAP: simulations and samples captured from sessions, on the virtual T1 now and on prototypes later. Dedicated lab instruments are not assumed (TK-012).

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit. `odw motion` moved to [`measure/`](../measure/README.md) (HL-71); what is left here is `odap-inject`, which graduates to Blackbird (HL-74), and its Cargo workspace.

## Layout

- `crates/odap-inject` — `odap-inject`: touch contacts (`tap`, `swipe`) through uinput on a running instance, for automated tests (development system extension only). `--dry-run` checks the gesture and creates no device; `--json` prints `opendap.inject/v1`; errors carry a dotted code, a hint and a fix
- The requirements are in the tools root's [`docs/requirements/`](../docs/requirements/README.md), and the [verification spec](../workspace/docs/specs/verification.md) in `workspace/` (HL-71): verification levels, the bit-perfect method, the architecture, the first checks and the traceability table

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
