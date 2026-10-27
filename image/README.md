# Toolkit image

Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers. Part of the OpenDAP Developer Toolkit, which is a set of repositories, each with the tools of one domain.

Each tool is an `odapkit-<tool>` executable in `bin/`, run as `odapkit <tool>` through toolkit-host's `odapkit` command ([RFC 0009](https://github.com/opendap-project/rfcs/blob/main/text/0009-odapkit.md)); `odapkit help` lists them, `--help` describes each. Each does one thing, finds the repositories it works on through the workspace's `repos.tsv` (or `ODAP_<REPOSITORY>_DIR`), and prints its result on stdout. Exit status is 0 on success, 1 on failure and 2 on a usage error.

| Tool | Does |
|---|---|
| `odapkit image build [PROFILE]` | Builds a blackbird-os image profile (default `qemu`) with mkosi in the image build VM. It passes the profile's inputs from other repositories: the development key and the virtual board's support package for `qemu`. While it builds, the output has a `.building` marker beside it (`odap-vdev status` reports it). It prints the output's path |
| `odapkit image path [PROFILE]`, `odapkit image vm start\|stop\|status\|shell` | Where an output is; the build VM (Lima, created on first use from `vm/build-vm.yaml` with this workspace mounted). It holds its CPUs and memory while it runs, so it stops itself after `ODAP_BUILD_VM_IDLE` minutes (default 10, `0`: never) with no build and no shell into it, and starts again on the next use |
| `odapkit sysroot install [--no-build]`, `odapkit sysroot path` | The cross-compilation sysroot, from the image's own packages, in a case-sensitive volume on macOS |
| `odapkit cross <cargo args>` | Cross-builds the Cargo workspace in the current directory for aarch64 against the sysroot (cargo-zigbuild, the image's glibc) |
| `odapkit deploy [crate…]` | Deploys the current workspace's binaries to a running device as a development system extension (`odap-dev-<workspace>`). The default crates and the units to restart come from `[workspace.metadata.odap]` in its `Cargo.toml` |
| `odapkit dev-key [create\|path\|tree]` | The development SSH key that `qemu` images accept, kept host-wide in `~/.local/share/opendap/dev-ssh` |
| `odapkit security-check` | Checks built images' package manifests against Debian's security, updates and backports archives |

Run them as `odapkit <tool>` (toolkit-host `setup.sh` links `odapkit` into `~/.local/bin`), or call them by path. Host prerequisites: toolkit-host `setup.sh` (Lima, zig, cargo-zigbuild, QEMU). Process: [development workflow](https://github.com/opendap-project/toolkit-host/blob/main/docs/specs/development-workflow.md).

## Layout

- `bin/` — the tools
- `lib/odapkit.sh` — what they share: `--help`, finding repositories, host-wide paths
- `vm/` — the image build VM's definition
- `docs/` — requirements

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
