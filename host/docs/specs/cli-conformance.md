# Command-line conformance

- **Status:** draft
- **Tool:** `odapkit cli` (`bin/odapkit-cli`, `src/odw/cli_inventory.py`, `src/odw/cli_probe.py`, `src/odw/cli_rules.py`, `src/odw/cli_conformance.py`)
- **Implements:** the [command standard](command-standard.md) §5 (every command-line surface) and §8 (conformance test), item 9 and, for plugins, items 1 to 6
- **Schemas:** `opendap.cli-inventory/v1` (`schemas/cli-inventory.schema.json`), `opendap.check-result/v1`, `opendap.matrix/v1`
- **Requirements:** TK-009, TK-010, TK-011

`odapkit cli list` inventories every command-line surface of the public roots; `odapkit cli check` grades each one against its row of the command standard's §5 table and writes the result as an evaluation matrix.

## Inventory

Roots are the paths of the workspace's `repos.tsv`; `--root` limits the run to some of them. An executable is:

- a tracked file in a `bin/`, `scripts/`, `tools/` or `hooks/` folder, at any depth, that has mode 755, a shebang or a Python `__main__` block; tests (`test_*`, `tests/`), fixtures, `vendor/` trees, `__init__.py` and library modules are not executables;
- a binary target of a Cargo package: each `[[bin]]`, else `src/main.rs`, and every `src/bin/*.rs`.

| Kind | Which executables | §5 row |
| --- | --- | --- |
| `umbrella` | `odapkit`, `odw`, `opendap` | `opendap` and `odw` plugins |
| `plugin` | `odapkit-*`, `odw-*`, `opendap-*` | `opendap` and `odw` plugins |
| `device-binary` | Cargo binaries of `system` and of a HAT's `software/`; files an image installs (`mkosi.extra/`) | device binaries |
| `host-binary` | every other Cargo binary, and other files in a `bin/` folder | device binaries |
| `script` | everything else | repository scripts, generators, studies |
| `hook` | files under `hooks/`, files a `.pre-commit-hooks.yaml` or a plugin's `hooks.json` runs, and image build scripts (`*.chroot`) | hooks |

Each entry carries the facts read from its source and from the sources it runs, one level down (`python -m`, a script it `exec`s, a shell library it sources, a module it imports): the flags it names (`--help`, `--json`, `--dry-run`, `--check`), whether it prompts, whether it writes files or changes state, whether it uses the network, a VM or a disk image, exit statuses other than 0, 1 and 2, subcommands outside the closed verb set, and the argument parser (`argparse`, `clap`) that guarantees `--help` and exit 2 on an unknown flag.

## Probes

An executable is run only when its source handles `--help` and it is not a device binary, a hook, a Cargo binary (not built by the test), or a command that uses the network, a VM, a disk image or a system service; the inventory says why each other executable was read from source only. Each run:

- has stdin at end of file and no terminal, `NO_COLOR=1`, an empty temporary working folder and a timeout (`--timeout`, 20 s by default);
- on macOS runs inside a sandbox that denies the network and every file write outside that folder, the uv cache and Python virtual environments.

The probes are `--help`, an unknown flag (`--odw-no-such-flag`) and, for the tools listed in `JSON_PROBES` (`src/odw/cli_probe.py`) with a read-only call, that call with `--json`. `--jobs` (4 by default) runs that many executables at a time.

## Rules

| Criterion | Rule | Applies to |
| --- | --- | --- |
| `help` | `--help` exits 0 with usage on stdout | all but hooks |
| `help-first-line` | the first line is `<front> <tool>: <summary>`, with the `tools.toml` summary when declared (§6) | plugins |
| `unknown-flag` | an unknown flag exits 2 | all but hooks |
| `json` | `--json` is offered, and the probe's output is one JSON document (or NDJSON ending in one) with a `schema`; a plugin without machine output says so in `--help` | all but hooks; a warning for non-plugins |
| `no-prompt` | no prompt in source; every probe returns with stdin at end of file and no terminal | all |
| `exit-status` | no exit status other than 0, 1, 2 in source or in a probe (`opendap*` may also use 3 to 11 and 130) | all |
| `error-code` | errors carry a stable dotted code and a hint | umbrellas, plugins, device and host binaries |
| `dry-run` | a command that changes state takes `--dry-run` | umbrellas, plugins, device and host binaries |
| `generator-check` | a script that writes files takes `--check` | scripts |
| `verbs` | no subcommand named `info`, `show`, `ls`, `rm` or `remove` | umbrellas, plugins, device and host binaries |
| `tools-toml` | declared in its root's `tools.toml` | plugins |
| `hook-message` | reports on stderr | hooks |

A cell says whether it comes from a probe or from source. A rule read from source with no parser that guarantees it is `unverified`. The verdict per executable follows the matrix's `worst-state` rule: `fail` when any cell fails, `incomplete` when one is a warning or unverified, `pass` otherwise.

## Output

- `odapkit cli list [--json]`: the inventory (`opendap.cli-inventory/v1`: `items`, `counts` by kind).
- `odapkit cli check [--json]`: one `opendap.check-result/v1` result per cell that applies (`<path>#<criterion>`); without `--json`, a summary per root and the executables that fail each rule.
- `--write DIR`: `DIR/cli-conformance.json` (`opendap.matrix/v1`, options = executables grouped by root, criteria = the rules above) and `DIR/cli-conformance.md` (the matrix, its reasons and a table by root), both marked generated; `--dry-run` names them and writes nothing.
- Exit status: 0 every executable passes or is incomplete (or `list` succeeded), 1 one fails, 2 usage error. Errors carry a dotted code, a hint and a fix on stderr, and with `--json` an `opendap.error/v1` document on stdout.
