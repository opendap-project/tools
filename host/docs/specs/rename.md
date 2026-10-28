# Rename

- **Status:** draft
- **Tool:** `odapkit rename` (`src/odw/rename.py`, `src/odw/rename_map.py`, `src/odw/rename_rewrite.py`, `src/odw/rename_keys.py`, `src/odw/rename_generated.py`, `src/odw/rename_merge.py`, `src/odw/rename_merge_git.py`, `src/odw/rename_config.py`, `src/odw/rename_precommit.py`); schemas [`schemas/rename-plan.schema.json`](../../schemas/rename-plan.schema.json), [`schemas/rename-merge.schema.json`](../../schemas/rename-merge.schema.json) and [`schemas/rename-config.schema.json`](../../schemas/rename-config.schema.json)
- **Applies:** [RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md) §7, steps 2 to 4 (merge with history, apply the rename map, rewrite links, render the shared configuration), one root at a time

The topology cutover moves each root's source repositories into one repository and renames what the rename map lists. `odapkit rename merge` builds the root repository from its sources with their history; `odapkit rename` then applies the mechanical part of the map to it: it moves files with `git mv`, rewrites relative Markdown links, GitHub URLs and identifiers, and reports what is left. It never touches another root or repository and never pushes.

```sh
odapkit rename merge libs --map rename-map.tsv --into ../libs --dry-run   # sources, heads, steps
odapkit rename merge libs --map rename-map.tsv --into ../libs            # the new root, with history
odapkit rename plan libs --map rename-map.tsv            # dry run: every move and rewrite
odapkit rename plan libs --map rename-map.tsv --json     # the same as opendap.rename-plan/v1
odapkit rename plan libs --map rename-map.tsv --kind mfr # only the maker-key rows
odapkit rename apply libs --map rename-map.tsv --root-dir ../libs
odapkit rename check libs --map rename-map.tsv --root-dir ../libs --json
odapkit rename apply tools --map rename-map.tsv --root-dir ../tools --structure-only   # each source whole in its folder
odapkit rename config libs --map rename-map.tsv --root-dir ../libs --dry-run           # the shared configuration
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
| `merge ROOT --into DIR` | Builds the root repository from its sources with their history (below). `--dry-run` lists the sources, their heads and the steps instead | 0 merged; 1 refused or a check failed; 2 on usage |
| `plan ROOT` | Lists every move, delete, identifier rewrite and link or URL rewrite the root needs. Changes nothing | 0; 2 on usage |
| `apply ROOT` | Performs the plan in the root checkout and commits it locally. `--dry-run` prints the plan instead | 0 applied; 1 refused; 2 on usage |
| `check ROOT` | The naming lint: old paths and old references left, and generated files not rendered from their source, as `opendap.check-result/v1` | 0 nothing old left; 1 old names left; 2 on usage |
| `config ROOT` | Renders the root's shared configuration (below) in one local commit. `--dry-run` lists what it does instead | 0 done or nothing to do; 1 refused; 2 on usage |

**What the tool reads.** When the root checkout (`--root-dir`, default `<workspace>/public/<root>`) is a git repository, the tool reads its tracked files (`view: root`). Before the history merge it reads each source repository's tracked files under its landing folder instead (`view: sources`), so a cut can be planned before it starts. `apply` needs the root checkout.

**Which rows apply.** Rows whose source or target is in the root. Per path row:

| Action | Result |
| --- | --- |
| `move`, `rename`, `keep` with one target in the same root | A move (nothing when the path does not change). A `dir/**` row moves every file below `dir` |
| `delete` | A delete |
| A target in another root | `cross_root` (`out` or `in`): moved at the later root's cut, never by this one |
| `merge`, `convert`, `split`, `extract`, `generate`, `archive`, or several targets | `pending`: done by the tool that renders, converts or generates the file, or by hand |

A move whose source is gone and whose target exists is `done`; one whose source and target are both gone is `missing` (a stale row). Two rows moving files to one path, or a target that exists and stays (compared without case, for case-insensitive file systems), is a `conflict`; so is a merge clash, two files that end at one path through maker keys (`Rockchip.kicad_sym` and `Rockchip_PMIC.kicad_sym` both becoming `rockchip.kicad_sym`), whose reason names both files.

**`--structure-only`** cuts the root as structure only: each source keeps its paths under its landing folder, so no path row moves or deletes anything and no identifier row applies. Rows into or out of other roots are still listed as `cross_root`; URLs and links still follow the landings (`toolkit-hooks/blob/main/README.md` → `tools/blob/main/hooks/README.md`, then a relative link inside the root). The plan says `structure_only: true`. A root cut this way (tools, 2026-10-27) does its content moves as tasks inside the root afterwards.

**`--kind K[,K]`** limits the identifier rows to those kinds (`mfr`, `env`, `crate`, …), so one kind can land on its own; path rows, links and URLs apply as without it.

## Rewrites

Every text file of the root (UTF-8, no NUL byte, at most 8 MiB) is read once. Files that are deleted are skipped, and so is everything under a `tests/` folder: test code and fixtures are inputs (a URL as test data, a fixture compared byte for byte by a sha256 golden), so no rewrite touches them and `check` does not report them.

1. **GitHub URLs** `https://github.com/opendap-project/<repo>[/(blob|tree|raw)/<ref>/<path>]` to a source repository of a root already cut become URLs of that root: `kicad-lib/blob/main/README.md` → `libs/blob/main/kicad/README.md`; a bare repository URL points to its folder (`libs/tree/main/kicad`), and `<repo>.git` to `<root>.git`. Only refs `main`, `master` and `HEAD` move: a tag or commit keeps pointing to the archived repository, which still serves it. The bare issue tracker `<repo>/issues` becomes `<root>/issues`, since an archived repository takes no issues; one issue (`issues/7`), pull requests and other repository pages stay. The sources of a root cut earlier are known from their repository rows even after `repos.tsv` dropped them (`kicad-lib` after the libs cut), so their URLs follow that root's map rows too; when that root's checkout is in the workspace (`public/<root>`), each source lands where that root's merge commit recorded it (`- toolkit-host → host/: …`), not where the map planned, and a row whose source file is still there and whose target is not has not landed (the root was cut as structure only) and is not followed. Three line contexts differ: a pre-commit `repo:` pin (`.pre-commit-config.yaml`) stays, because its `rev` is a tag of the old repository and the pin moves with it after the cut; a Cargo git pin (`git =` in `Cargo.toml`, `source = "git+…"` in `Cargo.lock`) stays for the same reason; and a repository URL in package metadata (`repository =`, `homepage =`, `documentation =`, `Source =`) becomes the root's own URL, never `<root>/tree/main/<folder>`. A root that reuses a source's name (`t1`, whose source `t1` lands in `product/`) leaves its URLs under one of its landing folders as they are (`t1/blob/main/board/…` is already new), so the rewrite is idempotent and `check` passes after `apply`.
2. **Relative Markdown links** (`[text](target)` and reference definitions) are recomputed for files that move and for targets that move, anchors kept. A folder target follows its files when they all go to one folder. Links inside fenced code blocks are examples and stay.
3. **Links into the root itself**: inside the root's Markdown, a link to the root's own URL at `main` becomes a relative link.
4. **Identifiers** listed in the map, in every text file. Values match as whole tokens (not inside a longer name or path), longest first, in one pass, so `/usr/bin/odap-x` wins over `odap-x` and no rewrite feeds another.

**Generated files** are never rewritten. A requirements README (`docs/requirements/README.md` beside a `requirements.toml`) is rendered from its source: the rewrites go to `requirements.toml`, and the README is rendered again from the rewritten TOML, so `odapkit req render --check` passes after the cut. The plan lists such files under `generated` (path and source); `check` fails `rename.generated:<path>` for a generated file that does not match its source.

**Which roots are cut.** URLs and identifiers are rewritten only for roots already cut: the roots the workspace's `repos.tsv` already lists as their own repository (`opendap-project/<root>`), and ROOT itself, since cuts need not follow RFC 0017's order; without a `repos.tsv`, the roots before ROOT in that order (`libs`, `tools`, `hats`, `host`, `t1`, `blackbird`). `--cut a,b` overrides both. A reference to a root not cut yet stays until that root's cut.

**Identifier status.** Each identifier row found in the root is one of:

| Status | Meaning |
| --- | --- |
| `apply` | Rewritten: a safe token whose owner root is cut |
| `later` | A safe token whose owner root is not cut yet |
| `manual` | Needs a hand; `reason` says why: `plain-word` (a single word with no separator and no `root`, which may also appear in prose), `owner-unknown`, `kind-mismatch`, `count-mismatch` |

The optional `root` column names the owner of an identifier row the tool cannot infer (`command:odapkit` → `tools`). Setting it on a plain word also states that the word is the identifier wherever it stands as a whole token, so it is rewritten (`odapkit`, never `odapkit-docs`); a word that also appears in prose keeps the column empty and stays `manual`. A value written as it appears in text is a token too (`skill:/opendap:requirement`, not the bare word). The owner of an identifier is the row's `root` column; without one, a root named as a token in the new value (`blackbird-composer.service` → `blackbird`, `ODAP_LIBS_DIR` → `libs`); without one, the root of a source repository named in the old value or the notes (`kicad-lib library key` → `libs`). Rows whose action is not `rename` or `merge`, or whose values are not tokens (parentheses, brackets, ellipses, `(removed)`), are not scanned.

## Merge

`merge` is step 2 of a cut. The root's sources are the repositories whose repository row joins the root; each lands in the folder that row names (`--landing <name>=<folder>` when it names none). The temporary clones go in a new folder inside `--work` (created when missing) or in a system temporary folder, and are removed afterwards. For each source, in name order:

1. A fresh clone of one branch (`--branch`, default `main`), with `git clone --no-local --no-tags --single-branch`, from the workspace clone or from `--source <name>=<bundle, URL or clone>`. The source clone is only read: its refs, index, objects and work tree do not change (status runs with `--no-optional-locks`; Git LFS's clean filter may leave a temporary file in its `.git/lfs/tmp`). Every git step runs with `GIT_LFS_SKIP_SMUDGE=1`, so LFS files stay pointers and nothing is downloaded.
2. Its history rewritten under the landing folder, by one of two engines (`--engine`, default `auto`: `filter-repo` when installed, else `fast-export`):

   | Engine | How |
   | --- | --- |
   | `filter-repo` | `git filter-repo --to-subdirectory-filter <folder>` on a second clone. Short commit ids in messages are rewritten to the new ids |
   | `fast-export` | `git fast-export` piped through the tool, which puts every `M` and `D` path (plain or C-quoted) under the folder and passes data blocks byte for byte, into `git fast-import` in a new repository. Messages are unchanged |

   Both engines give the same trees. A source whose landing is the root itself (empty) is not rewritten.
3. The rewritten branch fetched into the new root under `refs/odw-merge/<name>`.

The root's first commit is one merge commit whose parents are the rewritten heads and whose tree is each rewritten head's folder side by side. Its message lists each source's landing, commit count and head, and ends with one `Source-Head: <name> <sha>` trailer per source. `main` points to it, the work tree is checked out, and the temporary refs are removed. Tags are not carried: the archived source repositories keep them, and the rename's URL rule leaves URLs at a tag pointing there. `git log --follow` and blame work across the merge because no file changes in it. A file whose content is identical in several sources (a shared template such as `.gitignore`) can make `git log --follow` cross, at the merge commit, into its twin in another source; `git log --follow --no-merges` gives that file's own history.

**Refusals.** Nothing is created when:

| Code | When |
| --- | --- |
| `merge.no-sources` | The map joins no repository into the root |
| `merge.landing-unknown` | A source's row names no single folder, or the folder is not a plain path |
| `merge.landing-clash` | Two sources land in one folder, or one inside another |
| `merge.missing-source` | No clone, bundle or URL for a source, or the clone is not a repository of its own |
| `merge.no-branch` | The branch does not exist in a source |
| `merge.dirty` | A workspace clone has uncommitted or untracked files (the source is not frozen; `--source` with a bundle or URL of the pushed branch avoids reading another session's clone) |
| `merge.unpushed` | A workspace clone's branch has no upstream, or commits its upstream (as last fetched) does not have |
| `merge.into-exists` | `--into` exists and is not empty |
| `merge.into-inside-source` | `--into` is inside a source clone |
| `merge.no-engine` | `--engine filter-repo` without git filter-repo installed |

A git step that fails after the root is created (`merge.git`) removes what the merge wrote. `--dry-run` runs every refusal check and lists all of them in `problems` (exit 1 when there is one), then the sources and steps.

**Checks and report.** After the merge, for each source: the commit count before and after the rewrite, and that every original commit's tree equals its rewritten commit's tree at the landing folder (`trees_match`, through the commit map). For the root: its commit count equals the sources' total plus the merge, and its object size next to the sources'. A failed check exits 1 and keeps the root for inspection (`merge.mismatch`). Warnings: `merge.lfs` when a source's head holds Git LFS pointers, files whose first line is the pointer's version line (a file that only mentions that line is not one; their objects are in the source's LFS store and are pushed to the new root separately) and `merge.gitlinks` for submodules. The report is `opendap.rename-merge/v1`: printed with `--json`, written to `--report <file>`, and kept with the commit maps (`old new` per line) in `.git/odw-merge/` of the root.

After the merge, `odapkit rename plan ROOT --root-dir DIR` reads the merged root (`view: root`) and `apply` continues the cut.

**Maker keys** (`mfr:` rows) are rewritten by context, never as whole tokens: a maker key (`Texas_Instruments`, `Murata`, `Diodes-Inc.`) is also the maker's name in prose, and a hyphen does not make a value a key (`G-Switch` is a display name too). The key is rewritten only where the text uses it as one:

| Context | Example |
| --- | --- |
| A library id: the key, a colon and no space | `"Murata:LBES5PL2EL-923"` |
| A library file or folder | `Murata.kicad_sym`, `Murata.pretty`, `${OPENDAP_KICAD_3DMODELS}/Murata.3dshapes/` |
| A path segment followed by a file name with an extension, a further `/`, or a closing backtick or quote | `parts/Murata/GRM.md`, `bga/Micron/x/`, `` `parts/Murata/` `` |
| The value of a `library` field in TOML, JSON or Python (any case) | `library = "Murata"`, `"library": "Murata"`, `LIBRARY = "OpenDAP_Device"` |
| A lib table entry | `(name "Murata")` |
| A code span holding only the key | `` `Murata` `` |

URL text (`Texas-Instruments%20BQ.pdf`), display slashes (`Microchip/Atmel`, `NXP/Nexperia`), prose and footprint names (`Texas_DBV0005A_…`) stay. Files and folders whose path has the key as a segment move with the other moves: a folder named as the key (`parts/Murata/`), or `<key>.kicad_sym`, `<key>.pretty`, `<key>.3dshapes`. A `mfr:` row is never `plain-word`; it still needs an owner (the `root` column, or a source repository in the notes).

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

## Config

`config ROOT` runs after `apply`, in the merged root, and renders what the folders now share. It refuses a missing root (`config.no-root`), uncommitted work (`config.dirty`), a shared file whose copies differ (`config.differs`, naming them), and a root pre-commit configuration beside folder ones (`config.exists`). Otherwise, in one commit `<root>: one shared configuration at the root`:

| What | How |
| --- | --- |
| `LICENSES/`, `.claude/` (settings and rules), `.github/`, `.markdownlint-cli2.jsonc` | Hoisted to the root when every copy is the same: the first copy moves with `git mv` (history follows), the others are removed. `LICENSES/` becomes the union |
| `.pre-commit-config.yaml` | One configuration (below); the folder copies are removed |
| `.gitmodules` | Moved to the root, each submodule's name and `path` under its folder (`hardware/lib/opendap` → `board/hardware/lib/opendap`); the gitlinks are already there after `merge` |
| `.pre-commit-hooks.yaml` | Moved to the root; a `language: script` entry gets the folder as a prefix (`hooks/no-stray-files.sh` → `hooks/hooks/no-stray-files.sh`), since script entries are relative to the repository root |
| Folder `CLAUDE.md` | The managed block is removed, and the file ends with one newline; the root's `CLAUDE.md` carries the only one |
| Root `README.md`, `CLAUDE.md`, `REUSE.toml` | Written when absent, kept when present: the README's folder table (each folder's first README sentence, an image paragraph skipped, its relative links rebased onto the folder) and the former repositories; the CLAUDE.md title, folders, managed block and working rules; REUSE annotations for the root files, `.gitattributes` (and `.gitmodules` when there is one) included. A licence that REUSE.toml names and no folder brought (`CC-BY-SA-4.0`, `MIT`) is copied from the workspace's `LICENSES/` |

Folder `README.md`, `CLAUDE.md`, `REUSE.toml`, `.gitignore` and `.gitattributes` stay. The pre-commit configuration is joined as text, comments kept:

- a remote hook every folder pins identically is kept once, unscoped; one only some folders pin is scoped to them: through `exclude: ^(?!<folder>/)` when it sets no `files`, so the `files` of its manifest still apply (`kicad-erc` keeps `\.kicad_sch$`), else `files: ^<folder>/…`;
- a remote hook served by one of the root's own sources (`toolkit-hooks` inside `tools`) is dropped, and the providing folder's local copy runs for the whole root;
- every other local hook is scoped to its folder and runs inside it: `entry: sh -c 'cd <folder> && <entry>'` when it takes no file names, the folder as a prefix for a script; its name starts with the folder, and an id two folders share gets the folder as a suffix (`requirements-current-host`);
- a pre-push Rust hook (`types: [rust]` with `stages: [pre-push]`, such as clippy and cargo test) gets `types_or: [rust, cargo, cargo-lock]` instead, so a change to `Cargo.toml` or `Cargo.lock` alone (a pin) runs it too; rustfmt, given the file names, stays on Rust files;
- a `files` or `exclude` pattern keeps its meaning under the folder: an anchored alternative loses its `^` behind the prefix, an unanchored one becomes `.*(?:…)`; values that need it are quoted (a name holding a colon followed by a space).

The result is `opendap.rename-config/v1`; `odapkit sync --apply` then renders the root's `.gitignore` and `.gitattributes` blocks.

## Output

`--json` (the default when stdout is not a terminal) prints one JSON document; `--text` prints one line per item. Errors print `code`, `hint` and `fix` on stderr, and with `--json` an `opendap.error/v1` document (`error.code`, `message`, `hint`, `fix`) on stdout. Usage errors (`rename.no-map`, `rename.unknown-root`, `rename.no-workspace`, `rename.bad-map`, unknown flags) exit 2. The map defaults to `ODAP_RENAME_MAP`.

`check` results: `rename.old-path:<path>` and `rename.old-reference:<path>:<line>` fail; `rename.conflict:<path>` fails; `rename.manual-identifier:<old>` and `rename.missing:<path>` warn; `rename.pending` warns with the count of pending rows, and `rename.cross-root` is skipped with the count of rows another cut moves.

## Limits

- Only Markdown links are rewritten as paths. Paths inside other formats (KiCad library tables, TOML, YAML, scripts) change through identifier rows, the maker-key contexts, or the pending rows' own tools; `check` does not look for them otherwise.
- A maker-key context the table does not list (a path built in code, a cache path that follows the display name) is left for a hand.
- `merge` joins one branch per source and no tags; it creates no GitHub repository and pushes nothing. `apply` and `config` do not archive repositories or update `repos.tsv`. `--structure-only` applies to the root being cut; a later cut reads the earlier root's map rows for its URLs, and skips the rows its checkout shows have not landed.
