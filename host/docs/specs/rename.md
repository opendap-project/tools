# Rename

- **Status:** draft
- **Tool:** `odapkit rename` (`src/odw/rename.py`, `src/odw/rename_map.py`, `src/odw/rename_rewrite.py`); plan schema [`schemas/rename-plan.schema.json`](../../schemas/rename-plan.schema.json)
- **Applies:** [RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md) §7, steps 3 and 4 (apply the rename map, rewrite links), one root at a time

The topology cutover moves each root's source repositories into one repository and renames what the rename map lists. `odapkit rename` applies the mechanical part of the map to one root: it moves files with `git mv`, rewrites relative Markdown links, GitHub URLs and identifiers, and reports what is left. It never touches another root or repository and never pushes.

```sh
odapkit rename plan libs --map rename-map.tsv            # dry run: every move and rewrite
odapkit rename plan libs --map rename-map.tsv --json     # the same as opendap.rename-plan/v1
odapkit rename apply libs --map rename-map.tsv --root-dir ../libs
odapkit rename check libs --map rename-map.tsv --root-dir ../libs --json
```

## The rename map

A TSV file with a header row. Columns: `old`, `new`, `kind`, `action` (required), `notes`, `scope`, and an optional `root`. The tool reads three kinds of row:

| Row | `old` | `new` | Used for |
| --- | --- | --- | --- |
| Repository | `repo:opendap-project/<name>` | `repo:opendap-project/<root> (<folder>/)` | Which root a source repository joins, and the folder its files land in |
| Path | A workspace path (`public/libs/kicad/README.md`), or a folder pattern ending in `/**` | `<root>/<path>`; several targets separated by `;`; empty for a delete | Moves, deletes, and the rows left to other tools |
| Identifier | `<kind>:<value>` (`env:ODAP_X`, `crate:odap-y`, `identifier:<sub>:<value>`); several values separated by `;` | the same kind with the new values | Identifier rewrites |

A repository's landing folder is the single folder its repository row names (`(kicad/)`, `(folder board/)`, `env/`). When the row names none or several, the landing is unknown and the repository's path rows stay pending; `--landing <name>=<folder>` sets it.

## Verbs

| Verb | Does | Exit status |
| --- | --- | --- |
| `plan ROOT` | Lists every move, delete, identifier rewrite and link or URL rewrite the root needs. Changes nothing | 0; 2 on usage |
| `apply ROOT` | Performs the plan in the root checkout and commits it locally. `--dry-run` prints the plan instead | 0 applied; 1 refused; 2 on usage |
| `check ROOT` | The naming lint: old paths and old references left, as `opendap.check-result/v1` | 0 nothing old left; 1 old names left; 2 on usage |

**What the tool reads.** When the root checkout (`--root-dir`, default `<workspace>/public/<root>`) is a git repository, the tool reads its tracked files (`view: root`). Before the history merge it reads each source repository's tracked files under its landing folder instead (`view: sources`), so a cut can be planned before it starts. `apply` needs the root checkout.

**Which rows apply.** Rows whose source or target is in the root. Per path row:

| Action | Result |
| --- | --- |
| `move`, `rename`, `keep` with one target in the same root | A move (nothing when the path does not change). A `dir/**` row moves every file below `dir` |
| `delete` | A delete |
| A target in another root | `cross_root` (`out` or `in`): moved at the later root's cut, never by this one |
| `merge`, `convert`, `split`, `extract`, `generate`, `archive`, or several targets | `pending`: done by the tool that renders, converts or generates the file, or by hand |

A move whose source is gone and whose target exists is `done`; one whose source and target are both gone is `missing` (a stale row). Two rows moving files to one path, or a target that exists and stays (compared without case, for case-insensitive file systems), is a `conflict`.

## Rewrites

Every text file of the root (UTF-8, no NUL byte, at most 8 MiB) is read once. Files that are deleted are skipped.

1. **GitHub URLs** `https://github.com/opendap-project/<repo>[/(blob|tree|raw)/<ref>/<path>]` to a source repository of a root already cut become URLs of that root: `kicad-lib/blob/main/README.md` → `libs/blob/main/kicad/README.md`; a bare repository URL points to its folder (`libs/tree/main/kicad`), and `<repo>.git` to `<root>.git`. Only refs `main`, `master` and `HEAD` move: a tag or commit keeps pointing to the archived repository, which still serves it. Issue, pull request and other repository pages stay.
2. **Relative Markdown links** (`[text](target)` and reference definitions) are recomputed for files that move and for targets that move, anchors kept. A folder target follows its files when they all go to one folder. Links inside fenced code blocks are examples and stay.
3. **Links into the root itself**: inside the root's Markdown, a link to the root's own URL at `main` becomes a relative link.
4. **Identifiers** listed in the map, in every text file. Values match as whole tokens (not inside a longer name or path), longest first, in one pass, so `/usr/bin/odap-x` wins over `odap-x` and no rewrite feeds another.

**Which roots are cut.** URLs and identifiers are rewritten only for roots already cut: the roots before ROOT in RFC 0017's order (`libs`, `tools`, `hats`, `host`, `t1`, `blackbird`) and ROOT itself; roots outside that list come after it. `--cut a,b` overrides the order. A reference to a root not cut yet stays until that root's cut.

**Identifier status.** Each identifier row found in the root is one of:

| Status | Meaning |
| --- | --- |
| `apply` | Rewritten: a safe token whose owner root is cut |
| `later` | A safe token whose owner root is not cut yet |
| `manual` | Needs a hand; `reason` says why: `plain-word` (a single word with no separator, such as a manufacturer name that also appears in prose), `owner-unknown`, `kind-mismatch`, `count-mismatch` |

The owner of an identifier is the row's `root` column; without one, a root named as a token in the new value (`blackbird-composer.service` → `blackbird`, `ODAP_LIBS_DIR` → `libs`); without one, the root of a source repository named in the old value or the notes (`kicad-lib library key` → `libs`). Rows whose action is not `rename` or `merge`, or whose values are not tokens (parentheses, brackets, ellipses, `(removed)`), are not scanned.

## Apply

`apply` refuses, changing nothing, when:

| Code | When |
| --- | --- |
| `rename.no-root` | The root checkout does not exist (the sources are not merged with history yet) |
| `rename.not-toplevel` | `--root-dir` is a folder inside another repository |
| `rename.not-merged` | None of the landing folders is in the root checkout |
| `rename.dirty` | The checkout has uncommitted or untracked files, whoever they belong to |
| `rename.conflict` | The plan has a conflict |

Otherwise it moves every file with `git mv` in two steps through a temporary folder (so case-only renames work on case-insensitive file systems), deletes with `git rm`, removes the folders left empty, and commits the moves alone, so `git log --follow` and blame keep the history. It then writes the rewritten files and commits them separately. Both commits run the root's hooks. The result is the plan document with `mode: apply`, the `base` commit and the two `commits`; `git reset --keep <base>` undoes the cut. Pushing is left to the person running the cut.

## Output

`--json` (the default when stdout is not a terminal) prints one JSON document; `--text` prints one line per item. Errors print `code`, `hint` and `fix` on stderr, and with `--json` an `opendap.error/v1` document (`error.code`, `message`, `hint`, `fix`) on stdout. Usage errors (`rename.no-map`, `rename.unknown-root`, `rename.no-workspace`, `rename.bad-map`, unknown flags) exit 2. The map defaults to `ODAP_RENAME_MAP`.

`check` results: `rename.old-path:<path>` and `rename.old-reference:<path>:<line>` fail; `rename.conflict:<path>` fails; `rename.manual-identifier:<old>` and `rename.missing:<path>` warn; `rename.pending` warns with the count of pending rows, and `rename.cross-root` is skipped with the count of rows another cut moves.

## Limits

- Only Markdown links are rewritten as paths. Paths inside other formats (KiCad library tables, TOML, YAML, scripts) change through identifier rows or the pending rows' own tools; `check` does not look for them.
- `apply` does not merge histories (RFC 0017 §7 step 2), render shared configuration, archive repositories or update `repos.tsv`.
