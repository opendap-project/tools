# Workspace schemas

- **Status:** draft
- **Schemas:** [`schemas/`](../../schemas/) (JSON Schema 2020-12), validated by `odw.schemas` (`src/odw/schemas.py`)

The formats the maintainers' tools read and write. Each one is a schema with good and bad fixtures (`tests/fixtures/schemas/<name>/`); a format changes only with its schema, its fixtures and this spec in the same change.

| Schema | File it describes | Used by |
|---|---|---|
| `check` (`opendap.check/v1`) | A root's `check.toml`: the checks per lifecycle stage (`fast` before each commit, `full` before each push, `release` before a release); each step has a `name`, the command to `run`, optional `paths` it applies to, whether it `emits` an `opendap.check-result/v1` document, and the requirement IDs it `verifies` | the umbrella command's `check` |
| `tools` (`opendap.tools/v1`) | A root's `tools.toml`: the tools it adds to one umbrella command (`front`: `opendap` for developers and users, `odw` for maintainers), each with `name`, `summary`, `entry`, `audience` (maintainer, developer, user) and `stability` (internal, preview, stable, deprecated), and optionally `replaces` and `probe` (the conformance test's read-only `--json` call, or a list of calls when the tool's scripts answer different calls, each a list of arguments or a table `{ call, reaches }` naming the script it runs, from the folder of the `tools.toml`; odw reads every form since HL-105) | the umbrella commands' discovery and `help` |
| `tools-list` (`opendap.tools-list/v1`) | What `<front> help --json` prints: `items`, one per tool the front offers, each with `name`, `summary`, `audience` and `stability` (`unknown` for a tool found only on `PATH`), `replaces`, `entry` (the executable) and `root` (the root's path in the workspace manifest, empty for a tool found only on `PATH`) ([command standard](command-standard.md) §4) | `odw help --json` |
| `check-result` (`opendap.check-result/v1`) | What any check, test runner or study reports: the tool, the subject (repository, path, commit, variant) and one result per check with its `verdict` (pass, fail, warn, skip, error, or info for a value measured with no limit), a `message`, optional value, limit, margin and unit, the quality or integration `level`, the requirement IDs it `verifies` and its `evidence` | verification matrices, audits |
| `matrix` (`opendap.matrix/v1`) | An evaluation matrix: options × criteria (hard, soft, info) → a state per cell (pass, warning, fail, unverified, n/a, info) with a mandatory reason, a verdict per option under one aggregation rule (all-hard-pass, worst-state, first-fail, rule), rank, score, sensitivity and a recommendation that is always labelled as one; every value cites a source key with its status (sourced, UNVERIFIED, EST) | studies and selections |
| `plan-tasks` (`opendap.plan-tasks/v1`) | Tasks of a programme as `odapkit plan list` and `next` print them with `--json`: `items`, each with at least id, phase, title and status ([programme plan](programme-plan.md)) | `odapkit plan` |
| `cli-inventory` (`opendap.cli-inventory/v1`) | Every command-line surface of the public roots: path, root, name, surface kind (umbrella, plugin, device-binary, host-binary, script, hook), whether it may be run and why not, and the facts read from its source ([command-line conformance](cli-conformance.md)) | `odapkit cli` |
| `requirements` (`opendap.requirements/v1`) | A component's `docs/requirements/requirements.toml`: prose blocks and requirement records ([requirements as data](requirements-data.md)) | `odapkit req` |
| `verification` (`opendap.verification/v1`) | A verification matrix: its vocabulary and one case per row, observed by hand or proven again by named check results ([verification matrices](verification.md)) | `odapkit verify` |
| `rename-plan` (`opendap.rename-plan/v1`) | What one root's cut moves and rewrites: moves, deletes, rows done, missing, pending or crossing roots, conflicts, identifier rows with their status (apply, later, manual) and every link, URL and identifier edit ([rename](rename.md)) | `odapkit rename` |
| `homes` (`opendap.homes/v1`) | A programme's rules and the permanent homes that state them (path and phrase), or the decision a rule waits for | `odw plan homes` |
| `owners` | The workspace's `owners.tsv`, read as one object per row: a public repository path glob (optionally `:` and a pattern inside the repository) and its owners (`@user`, `@org/team`) ([workspace sync](workspace-sync.md)) | `odapkit sync` (`.github/CODEOWNERS`) |
| `plan-mirror-config` (`opendap.plan-mirror-config/v1`) | A programme's `mirror.toml`: the RFC repository, the workspace repository, the issue label, the RFC per phase, and per-task RFCs and public titles ([programme plan](programme-plan.md)) | `odapkit plan mirror` |
| `plan-mirror` (`opendap.plan-mirror/v1`) | What `odapkit plan mirror --json` prints: one issue per accepted RFC (title, body with its marker, tasks with public evidence, and after a real run its action and URL) and the unmapped tasks | `odapkit plan mirror` |
| `task` | A maintainers' programme task record (one TOML file per task): phase, dependencies, blocking decisions, status, and the tests, docs and commit evidence a `done` task must carry | `odw plan` |
| `decisions` | A programme's decisions file: each decision's question, options, recommendation and status; a `decided` entry records the decision, its date and who took it | `odw plan` |

## Rules

- A `done` task carries at least one evidence commit and one doc; a `dropped` task says why.
- A recommendation in a matrix is labelled as such; it never stands for a maintainers' decision.
- Identifiers: task ids `<SCOPE>-<nn>`, decision ids `D-<NAME>`, requirement ids `<PREFIX>-<nnn>`, levels `<letter><n>` (Q quality, I integration, V software verification, T test (unit to device, RFC 0018 §7), C HAT compatibility, H hardware support); `opendap.check-result/v1` accepts `^[QIVTCH][0-9]+$`.
