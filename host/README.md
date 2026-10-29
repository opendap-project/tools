# Toolkit host setup

Developer host setup for OpenDAP: the tools, toolchains and checks every contributor installs. Everything runs locally; there is no CI.

Part of the [OpenDAP](https://github.com/opendap-project) Developer Toolkit, which lives in the workspace under `public/toolkit/` as one repository per tool domain.

Officially supported: UNIX / POSIX systems. Tested on macOS only (TK-010, TK-011). Windows is not supported.

```sh
setup.sh
```

The script is safe to run again. It installs the Homebrew packages in [`Brewfile`](Brewfile), enables Git LFS, adds the Rust targets, installs the cargo tools and Interactive HTML BOM (inside KiCad's Python), and checks the design applications.

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

Code is built and tested on the host; the build VM only builds images and the sysroot, and the virtual T1 and the T1 only run software: [development workflow](docs/specs/development-workflow.md).

## Command standard

Every command-line surface, from the `opendap` and `odw` umbrellas and their plugins to device binaries, scripts, generators and hooks, follows the [command standard](../core/docs/specs/command-standard.md) (accepted with [RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md)): discovery through `tools.toml`, `--help`, `--json`, exit status 0, 1 or 2, and agents first, humans second. `odapkit` keeps working until the `tools` cut; the standard's §10 lists what changes.

## Layout

- `setup.sh` — installs and checks everything; safe to run again
- `bin/odapkit` — `odw` (in [`workspace/`](../workspace/README.md) since HL-71) under its earlier name (RFC 0009): an alias that runs `odw`, so every `odapkit <tool>` keeps working until the `tools` cut. `setup.sh` links it into `~/.local/bin`
- `Brewfile` — Homebrew packages
- `docs/` — requirements and the development workflow

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
