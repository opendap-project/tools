# Workspace sync

- **Status:** draft
- **Tool:** `odw sync` (`workspace/odw/sync.py`); templates in the workspace's [`templates/`](https://github.com/opendap-project/workspace/tree/main/templates)

The boilerplate every repository shares (agent settings and rules, lint configuration, and later the composed files) exists once, as a template in the workspace. Copies in repositories are rendered from it; editing a copy is drift.

## Manifest

`templates/sync.toml` (`opendap.sync/v1`) lists `[[file]]` entries:

| Field | Meaning |
|---|---|
| `template` | Path under `templates/` |
| `target` | Path inside each repository |
| `repos` | Globs over the repository paths listed in every `repos.tsv` |
| `exclude` | Globs left out (a repository with a variant of its own on purpose) |
| `when` | `always`: the file must exist and match; `present`: it must match where the repository has it |

## Installed hooks

A repository with a `.pre-commit-config.yaml` must have its pre-commit hook installed: a fresh clone carries no hooks, so its commits would skip every check. `--check` reports a missing hook (`.git/hooks/pre-commit`, linked worktrees included), and a missing `.git/hooks/pre-push` when the configuration has a pre-push stage (`stages:` or `default_stages:` naming `pre-push`), since `pre-commit install` alone installs only the pre-commit hook; `--apply` runs `pre-commit install --hook-type <type>` for each one missing.

## Managed blocks

A composed file (`.gitignore`, `.gitattributes`) is listed as a `[[block]]`: its `[[block.fragment]]` templates are joined into one block between `>>> odapkit sync` and `<<< odapkit sync` comment lines at the top of the file. A fragment applies to a repository when one of its `when_tracked` patterns matches a tracked file name (KiCad, FreeCAD, Blender, Rust, Python, CAD binaries for LFS); without matching fragments no file is needed. Lines outside the block are the repository's own; applying a block drops the outside lines it already covers (equal up to a trailing `/`) and the section headers it leaves empty. A block takes its target's comment syntax: `comment` (default `#`) and, for Markdown, `comment_end` (`<!--` … `-->`). Its `place` is `top` (default) or `after-intro`: a Markdown block sits after the document's title and first paragraph, stays where it is once present, and is never written into a repository that has no such document. With `place = "end"` the block is the file's last lines, so it continues a YAML list (pre-commit's `repos:`); it stays where it is once present. A fragment is a `template`, or a `generator` that renders it per repository from its tracked files, and adds nothing when it renders nothing.

### odw check hooks

The `odw-check` generator (`workspace/odw/check_hooks.py`) renders the hooks that run `odw check` (command standard §7) in `.pre-commit-config.yaml`. A repository's check roots are its folders that hold a tracked `check.toml` (the repository itself, or folders such as `system/` and `emulator/` of a merged root; folders under `tests/` or `fixtures/` are not). Each root gets one local hook per stage its `check.toml` declares: `odw-check-fast` before each commit and `odw-check-full` with `stages: [pre-push]` before each push; `release` has no hook, and a root that declares neither gets none, so `odw check` never meets `check.no_stage`. The hook passes the changed paths and `odw check` runs the steps whose `paths` match them, so a step without `paths` runs on every commit or push. A root in a folder gets the folder as an id suffix, `files: ^<folder>/` and an entry that runs inside it with each path given back relative to it (`sh -c 'cd system && … exec odw check --stage full "$@"' --`). `require_serial` keeps one `odw check` at a time. The folder goes into the entry and the YAML as written, so a root whose path segments are not plain (letters, digits, `.`, `_` and `-`, never `..`) is refused with `sync.unsafe-root` instead of rendered: a tracked folder named `a$(cmd)`, or holding a quote or a newline, would otherwise run a command, or add a key, in every hook. The steps the block runs are not listed again in the configuration: a repository that takes the block removes its own hooks for them. `odw rename config` renders the same block for a cut root.

## Generated files

A `[[generated]]` entry renders one `target` per repository from a workspace `source` with a named `generator`; `repos` and `exclude` are globs as for `[[file]]`, and `.` is the workspace repository itself. A repository the source names nothing for needs no file; one whose file is missing or differs is drift. The generator is:

| `generator` | Source | Target |
|---|---|---|
| `codeowners` | `owners.tsv` at the workspace root (`schemas/owners.schema.json`) | `.github/CODEOWNERS` |

`owners.tsv` is tab-separated with the header `path<TAB>owner`; lines starting with `#` are comments. `path` is a glob over repository paths as `repos.tsv` writes them (`*` also crosses `/`; `.` is the workspace repository), optionally followed by `:` and a CODEOWNERS pattern inside the repository (default `*`). `owner` is one or more GitHub handles or teams (`@user`, `@org/team`) separated by spaces. The file is public and never names a private path. Rows are written in file order, so a later row wins as GitHub reads CODEOWNERS. An invalid file is the error `owners.invalid`.

## Commands

`odw sync --check` reports each target that is missing or differs and exits 1 when there is any; `odw sync --apply` writes them, and `--apply --dry-run` names them and writes nothing. `--json` prints `opendap.check-result/v1`: with `--check` each drift is a `fail`, with `--apply` each target written (or that would be) is `info`. A missing `sync.toml` is the error `templates.not_found` (exit 2). The workspace's pre-commit runs `--check`.

## Stages

Verbatim files, the generated `.github/CODEOWNERS`, the `.gitignore` and `.gitattributes` blocks and the `CLAUDE.md` contributor block are in place. The odw check hooks of `.pre-commit-config.yaml` are a generated block (HL-103), taken one repository at a time. The rest of `.pre-commit-config.yaml` by profile and `REUSE.toml` from a licensing policy follow, rendered by the same tool.
