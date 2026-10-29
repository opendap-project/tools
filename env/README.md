# Toolkit env

The maintainers' host environment: the QEMU that virtual devices run, a LAN address for them, the build VM, and keeping generated trees out of the indexer.

## Layout

- `bin/odw-vmnet` — a LAN address for virtual devices: `odw vmnet install` once, as yourself (it asks for administrator rights only for the socket_vmnet daemon, and only on a terminal); `status [--json]`, `uninstall`; `--dry-run` names the steps
- `bin/odw-qemu` — the QEMU that virtual devices run: `odw qemu install` builds QEMU's pinned release with the patches in `qemu/patches/` into `~/.local/opt/opendap` and links it into `~/.local/bin` (no administrator rights); `status [--json]`; `install --dry-run` names the steps
- `bin/odw-noindex` — keeps generated trees (`target/`, `.venv/`, test caches) out of the Spotlight index: `odw noindex --apply <path>` ([development workflow](../host/docs/specs/development-workflow.md), Generated trees)
- `qemu/patches/` — the patches OpenDAP carries to QEMU's audio, applied in order by `odw qemu install`
- `build-vm.yaml` — the build VM's Lima configuration, which `odw image vm` fills in and starts
- `odw/noindex.py` — the `odw` package's module behind `odw noindex`
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `tests/` — `odw noindex`'s tests, run by `uv run pytest` from the tools root

The host setup (`setup.sh`, the `Brewfile`) is still in [`host/`](../host/README.md) and moves here with the link pass (HL-71).

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)
- `qemu/patches/`: **MIT**, QEMU's licence for the files they change

Copyright © 2026 The OpenDAP Project.
