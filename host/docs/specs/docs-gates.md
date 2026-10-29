# Docs gates

- **Status:** draft
- **Tool:** `odw docs` (`src/odw/docs.py`); method behind it: living docs (maintainers)

Docs are a maintained product: the gates below keep them correct on every commit, so the practice holds without anyone remembering it.

## Header

A spec, ADR or RFC starts with its title and a header: the bullet list of `- **Key:** value` items right after the title (`Status`, `Date`, `Requirements`, `Supersedes`, `Affected repositories`, …). `Status` starts with one word of the vocabulary: `draft`, `accepted`, `confirmed`, `superseded`, `withdrawn`, `implemented`; what follows the word (a date, the decision that set it) is free text.

## Gates (`odw docs check PATH...`)

A directory path is searched for Markdown, leaving out build trees (`target/`, `node_modules/`, `*.noindex`), vendored third-party code (`vendor/`), test fixtures (`tests/fixtures/`: data, not docs) and linked worktrees below it (a folder whose `.git` is a file: another task's copy of a repository, checked on its own).

| Rule | Severity | Finds |
|---|---|---|
| `link` | error | A relative link whose target does not exist |
| `cross-repo-link` | error | A relative link that leaves the repository; links between repositories are GitHub URLs |
| `private-link` | error | A link from a public repository to a private one (names from the private overlay's `repos.tsv`, when present); private repositories may link each other by GitHub URL |
| `term` | error | Project terms in lower case outside code: `Open Source`, `Open Hardware`, `Digital Audio Player` |
| `working-note` | warning | `for now`, `not started`, `TODO`, `FIXME`, `will take this over`, and `next step` when it announces the document's own work (`Next step:`, `the next step is`), outside an `## Open items` section. Wording in double quotes, and `next step` as what a message tells the user (`the reason and the next step`), is not a working note |
| `status` | warning | A header status outside the vocabulary |

`--strict` makes warnings fail. `--json` prints `opendap.check-result/v1`: one result per finding, `fail` for an error (and for a warning with `--strict`), `warn` otherwise, with the path and line. Code spans and fenced blocks are not checked for terms or working notes.

## Index (`odw docs index DOCS_DIR [--check | --dry-run]`)

`docs/README.md` keeps its own introduction; the table of documents (title and status of every Markdown file under `docs/`, requirements READMEs excepted) is generated between `<!-- index:start -->` and `<!-- index:end -->`. `--readme PATH` puts the table in another README (an RFC repository indexes `text/` from its root README); `--check` fails when it is stale; `--dry-run` says whether the table would change and writes nothing.

## RFC application (`odw docs rfc-status [--workspace DIR] [--write DIR [--dry-run]] [--json]`)

An accepted RFC becomes Implemented when every repository it affects has recorded the outcome. The tool reads each RFC in the rfcs repository's `text/` and reports one row per affected repository:

- **Affected repositories.** The header item `Affected repositories`, inline or as a bullet list under it: repository names as in `repos.tsv` (`workspace` for the workspace root), separated by commas, semicolons or "and". A note in parentheses after a name is ignored, except `(formerly X)`, `(from X)` and `(becomes Y)`, which declare a rename that every RFC may use. Text before a colon is ignored.
- **Recorded.** A Markdown or TOML file under the repository's `docs/requirements/`, `docs/adr/` or `docs/specs/` cites the RFC by number (`RFC 0004`, `RFCs 0008 and 0009`) or by its GitHub URL. A repository without `docs/` (the workspace root, rfcs) records it in its `README.md` or `CONTRIBUTING.md` instead, with the same citation forms; a generated index (`<!-- index:start -->` to `<!-- index:end -->`) lists every RFC and does not count.

| Row state | Meaning |
|---|---|
| `recorded` | The repository cites the RFC; the citing files are listed |
| `missing` | The repository is cloned and cites nothing |
| `absent` | The repository is in `repos.tsv` but not cloned here |
| `unresolved` | The name matches no repository and no declared rename |

The check fails (exit status 1) when an RFC whose status is `accepted` or `implemented` has a `missing` or `unresolved` row, or no `Affected repositories` item; `absent` rows never fail, since checkouts differ in access. Accepted RFCs whose rows are all `recorded` are listed as ready to be marked Implemented. Exit status 2 is a usage error or a workspace without an rfcs repository (`workspace.no_rfcs`). `--json` prints the report as `opendap.rfc-status/v1`; `--dry-run` with `--write` names the file and writes nothing; `--write DIR` writes `DIR/rfc-application.toml` (generated, never edited by hand: one `[[rfc]]` table per RFC, one `[[rfc.row]]` per affected repository).

## Accepted ADRs

An accepted ADR is never rewritten: a change of decision is a new ADR that supersedes it. Editorial corrections that leave the meaning unchanged (capitalisation of project terms, a broken link, a typo) are allowed and say so in the commit message.
