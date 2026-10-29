# Verification matrices

- **Status:** draft
- **Tool:** `odw verify` (`src/odw/verify.py`); schema `opendap.verification/v1` ([`schemas/verification.schema.json`](../../../schemas/verification.schema.json))

A verification matrix lists every case a component must prove, one row each, with what was expected, what was seen, the evidence, a status and the levels at which the case runs again. It is data; its Markdown is generated.

## Format

- `[matrix]`: the title and introduction (`title`, `intro`, and optional `status_note`, `level_note`, `level_after`, `done_label` for the rendered sentences), the vocabulary of statuses and levels (`[[matrix.status]]`, `[[matrix.level]]`, each with its meaning), which statuses count as done (`done`) and which levels are by hand only (`manual`).
- `[[case]]`: `id`, `area`, `primitive`, `conditions`, `case`, `expected`, `observed`, `evidence`, `status`, `levels`, `date`; optionally `verifies` (requirement ids), `checks` (the ids of `opendap.check-result/v1` results that prove the case again) and `variants`.

A case observed by hand keeps its observation in the matrix. A case with `checks` also gets its results collected per variant into `<matrix>.collected.json`, generated and never edited: each result is referenced by the path of its check-result document and the commit it ran on, never copied. It is run evidence, local to the machine that ran the checks: repositories ignore it and never commit it, and `render` uses it only when it is there.

## Commands

| Command | Does |
|---|---|
| `odw verify render MATRIX [--check \| --dry-run]` | Writes the Markdown beside the matrix: coverage by area and status, cases by level, the cases seen only by hand, then every case by area; `--check` fails when it is stale |
| `odw verify collect MATRIX --results DIR... [--check \| --dry-run]` | Joins the check results found under the folders to the cases that name them, per variant (pass, fail, warn, measured when every result is an `info` value with no limit, missing) |
| `odw verify coverage MATRIX [--prefix P...] [--json]` | Lists the confirmed and draft requirements (of the given id prefixes, or the whole workspace) that no case `verifies`; `--json` prints `opendap.verify-coverage/v1` (`matrix`, `uncovered`) |

## Rules

- A case whose status counts as done has at least one level.
- A case that counts as done while one of its collected checks fails on a variant is a problem: the matrix and the results disagree.
- Ids are unique within a matrix; statuses and levels come from the matrix's own vocabulary.
