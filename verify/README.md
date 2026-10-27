# Toolkit verify

Verification tooling for OpenDAP: simulations and samples captured from sessions, on the virtual T1 now and on prototypes later. Dedicated lab instruments are not assumed (TK-012).

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit, which lives in the workspace under `public/toolkit/` as one repository per tool domain.

## Layout

- `bin/odapkit-motion` — `odapkit motion`: records motion (a QEMU machine's own display over QMP, or a region of the host's screen), measures it (segments, visible duration against the design system's tokens, effective frame rate, held frames, a tracked element's curve) and extracts only the frames worth looking at, cropped, in one contact sheet. Needs ffmpeg (toolkit-host's Brewfile).
- `docs/` — requirements and the [verification spec](docs/specs/verification.md): verification levels, the bit-perfect method, the architecture, the first checks and the traceability table

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
