# Toolkit image

Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers. Part of the OpenDAP Developer Toolkit, which is a set of repositories, each with the tools of one domain.

Each tool is an `odapkit-<tool>` executable in `bin/`, run as `odapkit <tool>` through toolkit-host's `odapkit` command ([RFC 0009](https://github.com/opendap-project/rfcs/blob/main/text/0009-odapkit.md)); `odapkit help` lists them, `--help` describes each. Each does one thing, finds the repositories it works on through the workspace's `repos.tsv` (or `ODAP_<REPOSITORY>_DIR`), and prints its result on stdout. Exit status is 0 on success, 1 on failure and 2 on a usage error (an unknown flag included). Each tool is declared in `tools.toml`, whose summary is the first line of its `--help`; errors carry a stable dotted code, a hint and a fix (a command), from toolkit-host's [shared helpers](../host/docs/specs/cli-conformance.md#shared-helper). Every command that changes state takes `--dry-run`, which names what it would do and changes nothing; `--json` prints one document with a `schema` (below), and errors as `opendap.error/v1`.

| Tool | Does |
|---|---|
| `odw image build [PROFILE]` | Builds a blackbird-os image profile (default `qemu`) with mkosi in the image build VM. It passes the profile's inputs from other repositories: the development key and the virtual board's support package for `qemu`. While it builds, the output has a `.building` marker beside it (`odap-vdev status` reports it). It prints the output's path. `--dry-run` checks the profile and its inputs and names the mkosi command |
| `odw image path [PROFILE]`, `odw image vm start\|stop\|status\|shell` | Where an output is; the build VM (Lima, created on first use from `vm/build-vm.yaml` with this workspace mounted). It holds its CPUs and memory while it runs, so it stops itself after `ODAP_BUILD_VM_IDLE` minutes (default 10, `0`: never) with no build and no shell into it, and starts again on the next use. `path --json`: `opendap.image-output/v1`; `vm status --json`: `opendap.build-vm/v1` |
| `odw sysroot install [--no-build] [--dry-run]`, `odw sysroot path [--json]` | The cross-compilation sysroot, from the image's own packages, in a case-sensitive volume on macOS (`opendap.sysroot/v1`) |
| `odw cross [--dry-run [--json]] <cargo args>` | Cross-builds the Cargo workspace in the current directory for aarch64 against the sysroot (cargo-zigbuild, the image's glibc); its own flags come before the cargo subcommand. `--dry-run` prints the cargo command (`opendap.cross-command/v1`) |
| `odw deploy [--debug] [--dry-run] [--json] [crate…]` | Deploys the current workspace's binaries to a running device as a development system extension (`odap-dev-<workspace>`). The default crates and the units to restart come from `[workspace.metadata.odap]` in its `Cargo.toml`. `--json`: `opendap.deploy/v1` |
| `odw dev-key [create\|path\|tree] [--dry-run] [--json]` | The development SSH key that `qemu` images accept, kept host-wide in `~/.local/share/opendap/dev-ssh` (`opendap.dev-key/v1`) |
| `odw security-check [--json] [--dry-run]` | Checks built images' package manifests against Debian's security, updates and backports archives; `--json` prints `opendap.check-result/v1`, `--dry-run` does not record the run |

Run them as `odapkit <tool>` (toolkit-host `setup.sh` links `odapkit` into `~/.local/bin`), or call them by path. Host prerequisites: toolkit-host `setup.sh` (Lima, zig, cargo-zigbuild, QEMU). Process: [development workflow](../host/docs/specs/development-workflow.md).

## Layout

- `bin/` — the tools
- `lib/odapkit.sh` — what they share: `--help`, the error and flag helpers (toolkit-host's `lib/cli.sh`: `host/` of the same repository when this checkout is one of its folders, the merged `tools` root, else `ODAP_TOOLKIT_HOST_DIR`, else `repos.tsv`; `odw security-check` finds odw.clikit the same way, and `tests/test_host_lookup.py` builds both layouts), finding repositories, host-wide paths
- `tools.toml` — the tools this root declares to the maintainers' umbrella command
- `tests/cli.sh` — every tool's command-line contract (`--help`, an unknown flag, `--json`, `--dry-run`), on temporary folders; run by pre-commit
- `vm/` — the image build VM's definition
- `docs/` — requirements

## License

This repository follows the [REUSE](https://reuse.software) layout: license texts live in `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
