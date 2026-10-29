# Workspace audit

- **Status:** draft
- **Tool:** `odw audit` (`host/src/odw/audit.py`)

Workspace health is measured, never estimated: `odw audit` reads every repository's tracked files (`git ls-files`) and reports metrics with stable keys, so dated snapshots compare over time and programmes state exit criteria as thresholds on them.

## Inputs

- Repositories: every `repos.tsv` at the workspace root or two levels below it. A listed repository without a clone is reported under `repositories.missing`.
- `--rename-map TSV`: a programme's map of moves (`old` column, `*` globs allowed); tracked files no row covers are listed under `rename_map.unmapped`.

## Metrics

| Key | Measures |
|---|---|
| `repositories.total`, `.missing` | Cloned repositories; listed ones without a clone |
| `repositories.pre_commit_variants` | Distinct `.pre-commit-config.yaml` contents |
| `repositories.hooks_pins` | Distinct pinned versions of the shared hooks repository (`toolkit-hooks`, or `tools` after the tools cut) |
| `repositories.repos_without_pre_commit` | Repositories with no pre-commit configuration |
| `repositories.claude_rules_copies` | Tracked `.claude/rules/*` files |
| `repositories.claude_md_lowercase_open_source` | `CLAUDE.md` files that write `open source` in lower case |
| `software.rust_files_over_600_lines`, `slint_files_over_500_lines`, `python_files_over_500_lines` | Files over the line budgets (whole file, tests included) |
| `docs.docs_markdown_files` | Markdown files under `docs/` |
| `docs.specs_with_inline_history` | Specs that carry change history ("(v1.3:", "formerly", "previously", "superseded", "was renamed") |
| `docs.docs_with_working_notes` | Docs with working-note phrases (`for now`, `next step`, `not started`, `TODO`) |
| `identifiers.rdns_org_opendap_uses` | `org.opendap.*` reverse-DNS names outside `docs/` (the project's domain is opendap.cc) |
| `identifiers.opendap_env_uses` | `OPENDAP_*` environment variable names outside `docs/` |
| `rename_map.unmapped_count` | Tracked files no rename-map row covers |

## Outputs

`--write DIR` appends a dated `[[snapshot]]` to `DIR/drift.toml` and rewrites `DIR/conformance.toml` with one table per repository (pre-commit present, hooks pin, CLAUDE.md capitalisation, rule files, tracked files). Both are generated and never edited by hand. `--dry-run` with `--write` names the two files and writes nothing. `--json` prints the full result as `opendap.workspace-audit/v1`.
