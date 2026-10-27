# Command standard: `opendap` and `odw`

- **Status:** accepted (2026-10-27), with [RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md). It applies to each root at its cut; until then [RFC 0009](https://github.com/opendap-project/rfcs/blob/main/text/0009-odapkit.md) and the [`opendap` CLI spec](https://github.com/opendap-project/cli/blob/main/docs/specs/opendap-cli.md) are in force for that root's tools (§10 lists the delta).
- **Home:** this file; it moves to `tools/workspace/docs/specs/` at the `tools` cut (RFC 0017 §7).
- **Schemas:** `opendap.tools/v1` (`tools.schema.json`), `opendap.check/v1` (`check.schema.json`), `opendap.check-result/v1`, in [`schemas/`](../../schemas/) ([workspace schemas](workspace-schemas.md)).
- **Requirements:** TK-003, TK-009, TK-010, TK-011.
- **References:** Cloudflare's `cf` CLI and Forge, the pipeline that generates `cf`'s commands from the API schemas (Cloudflare's announcement of `cf` on its blog, blog.cloudflare.com); the UNIX conventions (clig.dev).

OpenDAP has two umbrella commands, one per side of the audience boundary. Both dispatch to independent plugin executables, discover them from data, list them with their audience and stability, and hold every plugin to the same contract, checked by a conformance test.

## 1. The two umbrellas

| | `opendap` | `odw` |
| --- | --- | --- |
| Audience | Developers (the devkit) and end users, advanced users included | Maintainers |
| Aliases | `odap`, `dap`, created by the installer only when free on the host (TK-003) | none |
| Plugins | `opendap-<tool>` | `odw-<tool>` |
| Grammar | `opendap <noun> <verb> [subject] [--flags]`, the CLI spec's closed verb set (§A.1) | `odw <tool> <verb> [subject] [--flags]` |
| Built-in commands | the CLI's own (device, exec, file, log, screen, input, image, vm, doctor, self, …) | `help`, `check`; everything else is a plugin |
| Needs a workspace checkout | never | yes, for discovery through the manifest; `PATH` otherwise |
| Implementation | Rust (`host/cli`), the reference dispatch crate | Rust, reusing the same dispatch crate |
| Environment prefix | `OPENDAP_*` | `ODAP_*` (`ODAP_<ROOT>_DIR`) |

Today `odw` is `odapkit` (`toolkit-host/bin/odapkit`, POSIX `sh`) and its plugins are `odapkit-<tool>` (§10).

## 2. Dispatch

- `<front> <tool> [ARGS…]` runs the plugin `<front>-<tool>` with `ARGS…`, replacing the umbrella's process (`exec`); its exit status is the plugin's.
- `opendap` resolves its built-in commands first. A plugin never shadows a built-in: a `tools.toml` entry whose `name` is a built-in noun fails the conformance test.
- A tool name is `^[a-z][a-z0-9-]*$`, short and unique across all `tools.toml` of one front. `odw` tool names carry no discipline prefix: `odw craft`, `odw stackup`, `odw sim`, `odw variant`, `odw bom`, `odw parts`, `odw image`, `odw plan`, `odw audit`, `odw sync`, `odw rename`, `odw req`, `odw rfc`, `odw verify`.
- A name containing `/`, starting with `.` or `-`, or not found exits 2 with `<front>: no tool <name> (<front> help lists them)` on stderr.

## 3. Discovery: `tools.toml`

Each root that contributes tools declares them in `tools.toml` at the root, one file per front:

```toml
schema = "opendap.tools/v1"
front = "odw"                 # opendap | odw

[[tool]]
name = "stackup"
summary = "Design and check the board stack-up"   # 1–100 characters, no trailing period
entry = "electronics/pcb/bin/odw-stackup"           # path from the root, executable
audience = "maintainer"       # maintainer | developer | user
stability = "stable"          # internal | preview | stable | deprecated
# replaces = "odapkit-stackup" # the name this tool takes over, kept as a deprecated alias
```

Resolution order, first match wins:

1. **Workspace roots.** The workspace manifest (`repos.tsv`) lists the roots; every root's `tools.toml` with this `front` is read. `ODAP_<ROOT>_DIR` overrides a root's path.
2. **`PATH`.** An executable `<front>-<tool>` on `PATH`. A tool found only here has `audience` and `stability` unknown and is listed as such.

Rules:

- Discovery never matches repository or folder names (today's `/toolkit-/` pattern goes).
- Two entries with the same `name` for one front are an error reported by `odw doctor`; dispatch takes the first in manifest order.
- `audience` follows the front: `odw` entries are `maintainer`; `opendap` entries are `developer` or `user`.
- `opendap` outside a workspace reads only `PATH` (plugins installed by the devkit or a package manager put their executables there).

## 4. Help

- `<front>` and `<front> help` print, on stdout, one line per tool: name, summary, audience, stability. Tools are grouped by stability (`stable`, `preview`, `deprecated` with its replacement); `internal` tools are listed only with `--all`.
- `<front> help <tool>` runs `<front>-<tool> --help`.
- `<front> help --json` prints `{"schema":"opendap.tools-list/v1","items":[{name, summary, audience, stability, replaces, entry, root}]}`, `root` empty for tools found on `PATH`.
- `opendap help` lists built-in commands first, then plugins. `opendap tools` keeps its meaning in the CLI spec (MCP tool definitions); it is not the plugin list.

## 5. Every command-line surface

The standard holds for everything run from a command line, not only the two umbrellas: their plugins, device binaries (run by services or by a person over SSH), repository scripts and generators, study scripts and hooks. Each is written for **agents first and humans second**: an agent must be able to discover it, call it without a terminal, read its result without parsing prose and recover from its errors; a person gets the same command with readable output.

- **One source of truth, generated surfaces.** As Forge generates `cf`'s commands from the API schemas, a tool's commands, flags, help, JSON schemas, docs and conformance cases come from one metadata source (the tool's command table or `tools.toml` entry), never written twice.
- **One vocabulary.** The CLI spec's closed verb set and nouns (§A.1) for every surface that has subcommands: `get`, never `info` or `show`; `list`, never `ls`. The same concept has the same name in every tool.
- **Machine output by default where it matters.** `--json` everywhere a tool reports something; JSON is the default when stdout is not a TTY or an agent is detected (§A.3), compact for agents; every document has a `schema`.
- **Discoverable.** `<front> help --json`, `schema`, and `search` find a command from a task's words; scripts list themselves through their root's `tools.toml`.
- **Errors an agent can act on.** A stable dotted `code`, a `hint` and a `fix` (a command to run), on stderr as text and in the JSON error document; never a stack trace as the only message.
- **Safe to call.** Every command that changes state takes `--dry-run`; destructive ones need `--force`; nothing prompts without a TTY.
- **UNIX behaviour.** stdout carries data and stderr everything else; quiet on success unless asked; reads stdin when given `-`; honours `--` and `NO_COLOR`; exit status 0, 1, 2 (§6).
- **Context-light.** Output carries what the caller asked for (`--fields`), no banners or decorations, so an agent spends few tokens on it.

| Surface | Holds to |
| --- | --- |
| `opendap` and `odw` plugins | all of §5 and §6, checked by the conformance test (§8) |
| Device binaries (`blackbird-*`, today `odap-*`) | §5's output, error, exit and interaction rules; `--json` where they report state; `--help` |
| Repository scripts, generators, studies | `--help`, exit 0/1/2, `--json` for results, `--check` for generators, no prompts, listed in `tools.toml` when someone else calls them |
| Hooks | exit status and a one-line stderr message saying what to run |

## 6. The plugin contract

Every plugin, of either front:

| Item | Rule |
| --- | --- |
| `--help`, `-h` | Prints usage on stdout and exits 0. The first line is `<front> <tool>: <summary>`, the same summary as `tools.toml`. At least one example; one with `--json` when the tool has machine output |
| `--json` | Machine-readable output on stdout, one JSON document (or NDJSON for streams, last line `result` or `error`), with a `schema` field. A tool that checks something emits `opendap.check-result/v1`. A tool with no machine output says so in `--help` and rejects `--json` with exit 2 |
| stdout / stderr | stdout carries data only; progress, logs, warnings and hints go to stderr |
| Exit status | **0** pass or success (including a no-op), **1** failure (a check that fails, an operation that fails), **2** usage error (unknown flag, bad argument, unknown subcommand). `opendap` plugins may also use the CLI spec's codes 3–11 and 130 (§A.4) with the same meaning, never another |
| Unknown flags | Exit 2, never ignored |
| Interaction | No prompt when stdin is not a TTY, under `CI`, or when an agent is detected; destructive operations need `--force` |
| Environment | Reads only its front's prefix (`OPENDAP_*` or `ODAP_*`) and documented third-party variables (`KICAD_CLI`, `NO_COLOR`…) |
| Workspace | An `opendap` plugin never requires a workspace checkout, `odw`, or a maintainer tool's path, and never names a maintainer tool as the only fix in a hint |
| Platforms | UNIX / POSIX only, tested on macOS (TK-010, TK-011) |

## 7. Lifecycle checks: `check.toml`

Each root declares its checks in `check.toml` (`opendap.check/v1`), one list per stage:

| Stage | When | Typical content |
| --- | --- | --- |
| `fast` | before each commit (pre-commit) | lint, format, schema validation, generated-is-current, unit tests of what changed |
| `full` | before each push | every test of the root, ERC/DRC, builds, the plugin conformance test |
| `release` | before a release | `full`, plus image builds, signed artefacts, end-to-end scenarios |

```toml
schema = "opendap.check/v1"

[[stage.fast]]
name = "schemas"
run = ["odw", "verify", "schemas"]
paths = ["schemas/**", "tests/fixtures/schemas/**"]
emits = "check-result"
verifies = ["TK-009"]
```

`odw check [--stage fast|full|release] [PATHS…]` finds the root from the current directory, selects the steps whose `paths` match the changed paths (all steps when none are given or a step has no `paths`), runs them, collects each `check-result`, and exits 0 when every step passes, 1 when one fails, 2 on a usage or `check.toml` error. Pre-commit calls `odw check --stage fast` on the staged paths; a pre-push hook calls `--stage full`.

## 8. Conformance test

A test in `tools/workspace`, run in every root's `full` stage for the tools that root declares. For each `tools.toml` entry it checks:

1. the file validates against `opendap.tools/v1`; the `name` is unique for its front and, for `opendap`, is not a built-in;
2. `entry` exists and is executable;
3. `--help` exits 0, prints on stdout, and its first line carries the `tools.toml` summary;
4. an unknown flag exits 2;
5. `--json` output (where the tool declares it) parses and carries `schema`; a check tool's output validates against `opendap.check-result/v1`;
6. with stdin closed and no TTY, the tool does not wait for input;
7. an `opendap` entry: run in a clean environment without a workspace, without `odw` on `PATH` and without `ODAP_*`, `--help` still works, and the source names no maintainer tool;
8. a `deprecated` entry names its replacement;
9. a device binary, script or generator found by the inventory (every executable in a root's `bin/`, `scripts/` and `tools/`, and every installed `blackbird-*` binary) holds to its row of the §5 table; the inventory is generated, so a new executable is checked from its first commit.

## 9. Audiences, the dependency rule and graduation

- **Rule.** `opendap` never needs `odw`; `odw` may call `opendap` for device operations it would otherwise duplicate. Developer and user tools depend only on published outputs (exports, schemas, images, SDK), never on maintainer tools or a workspace checkout.
- **Graduation.** A maintainer tool that developers need moves from `odw` to `opendap`:
  1. its engine is split from any maintainer-only step (for example, installing a sysroot graduates, building it does not);
  2. it becomes `opendap-<tool>`, declared with `front = "opendap"`, `audience = "developer"`, `stability = "preview"`, `replaces = "<old name>"`;
  3. the old `odw` name stays as a `deprecated` entry that forwards to the new one for one minor release, then is removed;
  4. it reads `OPENDAP_*` instead of `ODAP_*` (for example `ODAP_IMAGE` → `OPENDAP_IMAGE`) and passes conformance item 7.
- **First graduates:** `cross`, `deploy`, `dev-key`, the sysroot's install step, and the emulator (`odap-vdev` → `opendap emulator`).

## 10. Delta from what exists (measured 2026-10-26)

| Item | Today | This standard |
| --- | --- | --- |
| Maintainers' command | `odapkit` (POSIX `sh`), plugins `odapkit-<tool>` | `odw`, plugins `odw-<tool>`, Rust, shared dispatch crate |
| Discovery | `bin/` of the repositories whose `repos.tsv` entry matches `/toolkit-/`, then `PATH`; no `tools.toml` exists yet | `tools.toml` per root, then `PATH` |
| Help listing | name and the first comment or docstring line of each executable | name, summary, audience, stability; `--json` |
| Unknown tool | exit 2 | unchanged |
| `opendap` plugins | specified (CLI spec §3), not implemented: the CLI has no external-subcommand dispatch yet | §2–§5 |
| `opendap help` and plugins | RFC 0008 left open whether the CLI lists toolkit tools | `opendap help` lists `opendap` plugins only; maintainer tools are never listed |
| `check.toml` | schema exists; no root declares one yet | every root |
| Conformance test | none | §8 |
| Other command-line surfaces | device binaries, scripts, generators and studies follow their own habits; no inventory | §5, inventoried and checked by the conformance test |

The 17 `odapkit-*` tools today (`toolkit-hardware`: component, craft, dru, parts, sim, stackup; `toolkit-host`: audit, noindex, plan, vmnet; `toolkit-image`: cross, deploy, dev-key, image, security-check, sysroot; `toolkit-verify`: motion), probed on 2026-10-26:

| Check | Pass | Fail |
| --- | --- | --- |
| `--help` exits 0 | 17 | — |
| unknown flag exits 2 | 15 | `odapkit-cross` (exits 1), `odapkit-security-check` (exits 0) |
| `--json` offered (named in the source; not yet exercised) | 10 | vmnet, cross, deploy, dev-key, image, security-check, sysroot |
| declared in `tools.toml` | 0 | all |

## Open items

- **CS-2** `odw` verbs: the CLI's closed verb set where one fits, or each tool's own verbs (`stackup explore`, `motion record`) documented in its `--help`. *Proposal:* closed set preferred, own verbs allowed and listed in `--help`.
- **CS-3** Whether `opendap`, outside a workspace, also reads a `tools.toml` installed with the devkit (to show audience and stability for installed plugins). *Proposal:* yes, from `$XDG_DATA_HOME/opendap/tools/*.toml`, once the devkit ships plugins.
- **CS-4** Which tools graduate after the first set: `component` (export validation) and `vmnet` (emulator bridged networking) serve developers too. *Proposal:* decide per tool when its developer use exists.
- **CS-5** Help-list schema name: `opendap.tools-list/v1` needs a schema next to `opendap.tools/v1`.
