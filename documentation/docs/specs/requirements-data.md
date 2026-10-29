# Requirements as data

- **Status:** draft
- **Tool:** `odw req` (`documentation/odw/requirements.py`); schema `opendap.requirements/v1` ([`schemas/requirements.schema.json`](../../../schemas/requirements.schema.json))

A component's requirements are data that tools read: other repositories cite them, verification rows prove them, release manifests list them. Their source is `docs/requirements/requirements.toml`; `docs/requirements/README.md` is rendered from it and checked.

## Format

The document is a list of blocks, in order:

- `markdown`: prose kept as written (title, introduction, context);
- `requirement`: a table, one record per requirement with `id` (`<PREFIX>-<nnn>`), `text`, `priority` (`must`, `should`), `status` (`confirmed`, `draft`, `withdrawn`, `moved`) and an optional `note` (rendered in parentheses after the status, for example the date and the requirement that replaces a withdrawn one).

The rendered table is `| ID | Requirement | Priority | Status |`. Rendering a README that `import` parsed gives it back byte for byte, so a repository moves to the TOML source without changing its README.

## Commands

| Command | Does |
|---|---|
| `odw req import README.md [--write [--dry-run]]` | Parses the README's tables into `requirements.toml` (stdout, or beside the README); reports schema errors and duplicate ids; `--dry-run` names the file and writes nothing |
| `odw req render requirements.toml [--check \| --dry-run]` | Writes the README beside it, or fails when the README is not what the TOML renders; `--dry-run` says whether it would change and writes nothing |
| `odw req prefixes` | Every id prefix in use is registered in an `id-prefixes.tsv` (the workspace's, and a private overlay's for private repositories) for the folder that uses it |
| `odw req refs [--strict]` | Citations, in docs and code, of ids with a registered prefix: unknown ids are errors, withdrawn or moved ids are warnings (`--strict` fails on them) |
| `odw req index [--json]` | Every requirement of every repository listed in a `repos.tsv` (TOML source when present, README otherwise), skipping nested repositories; `--json` prints `opendap.requirements-index/v1` (`requirements`) |

## Rules

- Ids are never renumbered; a withdrawn or moved requirement keeps its row with a note.
- Each prefix belongs to one requirements folder (`id-prefixes.tsv`: `prefix`, `path`, `note`). A prefix shared before the registry existed keeps one row per folder; only the row noted `new ids` takes new ones.
- A repository with a `requirements.toml` runs `odw req render --check` in pre-commit; the README is never edited by hand.
