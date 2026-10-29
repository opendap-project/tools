# Toolkit env

The maintainers' host environment: the host setup every contributor runs, the QEMU that virtual devices run, a LAN address for them, the build VM, and keeping generated trees out of the indexer. Everything runs locally; there is no CI.

Officially supported: UNIX / POSIX systems. Tested on macOS only (TK-010, TK-011). Windows is not supported.

```sh
public/tools/env/setup.sh
```

The script is safe to run again. It installs the Homebrew packages in [`Brewfile`](Brewfile), enables Git LFS, adds the Rust targets, installs the cargo tools and Interactive HTML BOM (inside KiCad's Python), builds `odw` (from [`workspace/`](../workspace/README.md)) and links it and the `odapkit` alias into `~/.local/bin`, and checks the design applications, QEMU and socket_vmnet.

## Installed

| Area | Tools |
|---|---|
| Design | KiCad 10, FreeCAD 1.1, Blender 5.2 (official installers) |
| Fab outputs | `kicad-cli` (bundled with KiCad), Interactive HTML BOM |
| Repository hygiene | pre-commit, REUSE, Git LFS, gh, jq, yq |
| Simulation | ngspice; Python studies use `uv` with their own `pyproject.toml` |
| Firmware | rustup targets for aarch64 Linux (glibc, musl), Cortex-M0+, Cortex-M33 and RISC-V; probe-rs, picotool, dfu-util, OpenOCD, tio, sigrok-cli |
| Rust | cargo-binstall, cargo-nextest, cargo-deny; zig and cargo-zigbuild, to cross-build for Blackbird OS against its sysroot |
| Emulation | QEMU; Lima, the Linux VM that builds images and the sysroot |

## Development workflow

Code is built and tested on the host; the build VM only builds images and the sysroot, and the virtual T1 and the T1 only run software: [development workflow](docs/guides/maintainers/development-workflow.md).

## Command standard

Every command-line surface, from the `opendap` and `odw` umbrellas and their plugins to device binaries, scripts, generators and hooks, follows the [command standard](../core/docs/specs/command-standard.md) (accepted with [RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md)): discovery through `tools.toml`, `--help`, `--json`, exit status 0, 1 or 2, and agents first, humans second. `odapkit` keeps working until the `tools` cut; the standard's §10 lists what changes.

## Layout

- `setup.sh` — installs and checks everything; safe to run again
- `Brewfile` — Homebrew packages
- `docs/guides/maintainers/development-workflow.md` — how code is built, tested and run (host, build VM, virtual T1)
- `bin/odw-vmnet` — a LAN address for virtual devices: `odw vmnet install` once, as yourself (it asks for administrator rights only for the socket_vmnet daemon, and only on a terminal); `status [--json]`, `uninstall`; `--dry-run` names the steps
- `bin/odw-qemu` — the QEMU that virtual devices run: `odw qemu install` builds QEMU's pinned release with the patches in `qemu/patches/` into `~/.local/opt/opendap` and links it into `~/.local/bin` (no administrator rights); `status [--json]`; `install --dry-run` names the steps
- `bin/odw-noindex` — keeps generated trees (`target/`, `.venv/`, test caches) out of the Spotlight index: `odw noindex --apply <path>` ([development workflow](docs/guides/maintainers/development-workflow.md), Generated trees)
- `qemu/patches/` — the patches OpenDAP carries to QEMU's audio, applied in order by `odw qemu install`
- `build-vm.yaml` — the build VM's Lima configuration, which `odw image vm` fills in and starts
- `odw/noindex.py` — the `odw` package's module behind `odw noindex`
- `tools.toml` — the tools this folder declares to the maintainers' umbrella command
- `tests/` — `odw noindex`'s tests, run by `uv run pytest` from the tools root

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)
- `qemu/patches/`: **MIT**, QEMU's licence for the files they change

Copyright © 2026 The OpenDAP Project.
