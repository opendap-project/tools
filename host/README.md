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

Every command-line surface, from the `opendap` and `odw` umbrellas and their plugins to device binaries, scripts, generators and hooks, follows the [command standard](docs/specs/command-standard.md) (accepted with [RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md)): discovery through `tools.toml`, `--help`, `--json`, exit status 0, 1 or 2, and agents first, humans second. `odapkit` keeps working until the `tools` cut; the standard's §10 lists what changes.

## Layout

- `setup.sh` — installs and checks everything; safe to run again
- `crates/odw-dispatch` — `odw`, the maintainers' umbrella command ([command standard](docs/specs/command-standard.md) §1–§4): `odw <tool>` runs the tool a workspace root's `tools.toml` declares (then `odw-<tool>` or `odapkit-<tool>` on `PATH`); `odw help [--json] [--all]` lists them with summary, audience and stability; `odw doctor [--json]` reports duplicate names, unsafe or invalid `tools.toml` entries, summaries over 100 characters and tools whose `--help` fails. The dispatch is a library the `opendap` CLI can reuse. `setup.sh` builds it and links it into `~/.local/bin`
- `bin/odapkit` — the same command under its earlier name (RFC 0009): an alias that runs `odw`, so every `odapkit <tool>` keeps working until the `tools` cut. `setup.sh` links it into `~/.local/bin`
- `bin/odapkit-vmnet` — a LAN address for virtual devices: `odapkit vmnet install` once, as yourself (it asks for administrator rights only for the socket_vmnet daemon, and only on a terminal); `status [--json]`, `uninstall`; `--dry-run` names the steps
- `bin/odapkit-qemu` — the QEMU that virtual devices run: `odapkit qemu install` builds QEMU's pinned release with the patches in `qemu/patches/` into `~/.local/opt/opendap` and links it into `~/.local/bin` (no administrator rights); `status [--json]`; `install --dry-run` names the steps
- `bin/odapkit-noindex` — keeps generated trees (`target/`, `.venv/`, test caches) out of the Spotlight index: `odapkit noindex --apply <path>` ([development workflow](docs/specs/development-workflow.md), Generated trees)
- `bin/odapkit-plan` — a maintainers' programme as data (tasks, decisions, claims, generated index, and `mirror`: its public tasks as one tracking issue per accepted RFC): [programme plan](docs/specs/programme-plan.md)
- `bin/odapkit-audit` — workspace metrics from every repository's tracked files, with dated snapshots: [workspace audit](docs/specs/workspace-audit.md)
- `bin/odapkit-req` — requirements as data: import, render with `--check`, index every repository: [requirements as data](docs/specs/requirements-data.md)
- `bin/odapkit-docs` — docs gates (links, private mentions, terms, working notes, status), the generated docs index and RFC application status: [docs gates](docs/specs/docs-gates.md)
- `bin/odapkit-sync` — shared boilerplate from the workspace templates: `--check` reports drift, `--apply` repairs it; it also generates each repository's `.github/CODEOWNERS` from the workspace's `owners.tsv`: [workspace sync](docs/specs/workspace-sync.md)
- `bin/odapkit-rename` — one root's cut from the rename map ([RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md) §7): `merge` builds the root repository from its sources with their history, `plan` lists every move and link, URL and identifier rewrite, `apply` performs them with `git mv` and local commits, `check` lists the old names left: [rename](docs/specs/rename.md)
- `bin/odapkit-cli` — every command-line surface of the public roots, inventoried and checked against the command standard (`list`, `check`); writes the conformance matrix: [command-line conformance](docs/specs/cli-conformance.md)
- `tools.toml` — the tools this root declares to the maintainers' umbrella command (command standard §3); `odapkit help --json` lists them
- `src/odw/clikit.py`, `lib/cli.sh` — the command-line behaviour every tool shares: `--help` from `tools.toml`, errors with a code, a hint and a fix, exit status 0, 1, 2: [command-line conformance](docs/specs/cli-conformance.md)
- `bin/odapkit-verify` — verification matrices as data: render, collect check results by variant, uncovered requirements: [verification matrices](docs/specs/verification.md)
- `Brewfile` — Homebrew packages
- `src/odw/evaluate.py` — the evaluation-matrix engine every study uses: [evaluation matrix](docs/specs/evaluation-matrix.md)
- `schemas/`, `src/odw/` — the workspace schemas (check, tools, check-result, matrix, task, decisions) and their validator: [workspace schemas](docs/specs/workspace-schemas.md). Tests: `uv run pytest`
- `docs/` — requirements and specs (development workflow, workspace schemas)

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)
- `schemas/`: **Mozilla Public License 2.0** (`MPL-2.0`), so others can build on the formats

Copyright © 2026 The OpenDAP Project.
