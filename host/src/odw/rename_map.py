"""The rename map (`old, new, kind, action, notes, scope`, optional `root`) as a model one root's cut can use.

Rows are classified once: repository rows (`repo:` → where each source repository lands in its root), path rows
(`old` is a workspace path such as `public/libs/kicad/README.md`, `new` is `<root>/<path>`) and identifier rows
(`<kind>:<value>`, for example `env:ODAP_X`). Spec: docs/specs/rename.md.
"""
import dataclasses
import pathlib
import re
import subprocess

from odw import audit

ORDER = ["libs", "tools", "hats", "host", "t1", "blackbird"]
MOVE_ACTIONS = {"move", "rename", "keep"}
REPO_ROW = re.compile(r"repo:(?:opendap-project/)?([A-Za-z0-9._-]+)\s*(.*)")
SUBDIR = re.compile(r"(?<![\w/.-])([a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)*)/(?![\w])")
PREFIXED = re.compile(r"([a-z][a-z0-9-]*):(.+)")
SUBKIND = re.compile(r"([a-z][a-z0-9-]*):(.+)")
UNSAFE = set("<>()[]{}*…,")
SEPARATORS = set("-_./:@$")
# Kinds rewritten only in their own contexts, so a single word is safe: maker keys and users (HL-109).
CONTEXT_ONLY = {"mfr", "identifier:user"}


@dataclasses.dataclass
class Row:
    line: int
    old: str
    new: str
    kind: str
    action: str
    notes: str = ""
    scope: str = ""
    root: str = ""


@dataclasses.dataclass
class Source:
    """A repository listed in a repos.tsv, and where its files land inside its root after the history merge."""
    path: str  # workspace path, e.g. public/libs/kicad; "" for an archived source no repos.tsv lists any more
    name: str  # GitHub name, e.g. kicad-lib
    root: str = ""
    landing: str | None = None  # folder inside the root; None when the map does not say
    kind: str = "source"  # source; archived (its root is cut, only its repository row is left); root (public/<root>)


@dataclasses.dataclass
class Identifier:
    row: Row
    old: list
    new: list
    owner: str | None
    reason: str | None  # why it cannot be rewritten mechanically; None when it can


class Map:
    def __init__(self, rows, sources):
        self.rows = rows
        self.sources = sources  # name -> Source
        self.by_path = sorted((s for s in sources.values() if s.path), key=lambda s: -len(s.path))
        self.roots = sorted(set(ORDER) | {s.root for s in sources.values() if s.root} | {r.new.split("/")[0] for r in rows
                                                                             if is_path_row(self, r) and r.new})

    def source_of(self, ws_path):
        for s in self.by_path:
            if ws_path == s.path or ws_path.startswith(s.path + "/"):
                return s
        return None

    def sources_of(self, root):
        return [s for s in self.sources.values() if s.root == root and s.kind == "source"]

    def in_root(self, ws_path):
        """(root, path inside the root) of a workspace path, or (root, None) when its landing is unknown."""
        s = self.source_of(ws_path)
        if s is None:
            return None, None
        rest = ws_path[len(s.path):].lstrip("/")
        if s.landing is None:
            return s.root, None
        return s.root, f"{s.landing}/{rest}".strip("/") if s.landing else rest

    def cut(self, root, override=None, ws=None):
        """The roots already cut when `root` is: the override; else the roots the workspace already has as one
        repository (repos.tsv), and itself; else (no workspace) those before it in ORDER, and itself."""
        if override:
            return list(dict.fromkeys([*override, root]))
        if ws is not None and (pathlib.Path(ws) / "repos.tsv").is_file():
            return cut_roots(ws, root)
        if root in ORDER:
            return ORDER[:ORDER.index(root) + 1]
        return [*ORDER, root]


def read_tsv(path):
    lines = pathlib.Path(path).read_text().splitlines()
    head = lines[0].split("\t")
    out = []
    for n, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = dict(zip(head, line.split("\t")))
        out.append((n, cells))
    return head, out


# A merge commit's record of where each source landed: `- <name> → <folder>/: N commits, head <sha>` (rename merge).
LANDING_LINE = re.compile(r"^- (\S+) → (\S+?)/: \d+ commits, head ", re.M)


def recorded_landings(checkout):
    """{source name: folder} as a cut root's merge commits recorded them; {} without a checkout."""
    checkout = pathlib.Path(checkout)
    if not (checkout / ".git").exists():
        return {}
    log = subprocess.run(["git", "-C", str(checkout), "log", "--format=%B", "--grep=^Source-Head: "],
                         capture_output=True, text=True, env=audit.git_env()).stdout
    return {n: "" if f == "." else f for n, f in LANDING_LINE.findall(log)}


def former_paths(ws):
    """{repository name: workspace path} of the rows repos.tsv once had and dropped at a cut (its git history); {}
    when the workspace is not a git checkout."""
    if not (pathlib.Path(ws) / ".git").exists():
        return {}
    diff = subprocess.run(["git", "-C", str(ws), "log", "-p", "--format=", "--", "repos.tsv"], capture_output=True,
                          text=True, env=audit.git_env()).stdout
    out = {}
    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---") and "\t" in line:
            path, repo = line[1:].split("\t")[:2]
            out.setdefault(repo.strip().rsplit("/", 1)[-1], path.strip())
    return out


def repositories(ws):
    """Every repos.tsv entry: workspace path → GitHub name (the public list and any overlay two levels below)."""
    ws = pathlib.Path(ws)
    out = {}
    for f in [ws / "repos.tsv", *sorted(ws.glob("*/*/repos.tsv"))]:
        if not f.is_file():
            continue
        for _, c in read_tsv(f)[1]:
            path, repo = c.get("path", ""), c.get("repository", "")
            if path and repo:
                out[path] = repo.split("/")[-1]
    return out


def _landing(text, root, notes):
    """The single folder a repository row names: `(kicad/)`, `(folder board/)`, `env/`; None when it names none
    or several."""
    first = text.split(";")[0]
    found = [m for m in SUBDIR.findall(first)]
    if len(found) == 1:
        return found[0]
    m = re.search(rf"\b{re.escape(root)}/([a-z0-9][a-z0-9_/-]*[a-z0-9])", notes)
    if not found and m:
        return m.group(1)
    return None


def cut_roots(ws, root):
    """Roots the workspace has as one repository at public/<root> (repos.tsv), in ORDER, then `root`; a repository
    that only shares a root's name somewhere else (the t1 product repository today) is not a cut root."""
    names = set()
    for line in (pathlib.Path(ws) / "repos.tsv").read_text().splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 2 and cols[0].strip() == f"public/{cols[1].strip().rsplit('/', 1)[-1]}":
            names.add(cols[1].strip().rsplit("/", 1)[-1])
    return list(dict.fromkeys([r for r in ORDER if r in names and r != root] + [root]))


def load(ws, map_path, landings=None):
    head, raw = read_tsv(map_path)
    missing = {"old", "new", "kind", "action"} - set(head)
    if missing:
        raise ValueError(f"rename map lacks columns: {', '.join(sorted(missing))}")
    rows = [Row(n, c["old"], c.get("new", ""), c["kind"], c["action"], c.get("notes", ""), c.get("scope", ""),
                c.get("root", "")) for n, c in raw]
    sources = {name: Source(path, name) for path, name in repositories(ws).items()}
    for r in rows:
        m = REPO_ROW.fullmatch(r.old)
        if not m or r.kind != "repo":
            continue
        name = m.group(1)
        n = REPO_ROW.match(r.new.split(";")[0]) if r.new.startswith("repo:") else None
        if not n:
            continue
        s = sources.setdefault(name, Source("", name, kind="archived"))  # URLs to it still follow its root
        s.root = n.group(1)
        s.landing = "" if s.root == name else _landing(n.group(2), s.root, r.notes)
    roots = set(ORDER) | {s.root for s in sources.values()}
    for s in sources.values():
        if not s.root and s.name in roots and s.path == f"public/{s.name}":
            s.root, s.landing, s.kind = s.name, "", "root"  # a root already cut: its paths are root paths
    # A source that left repos.tsv at its root's cut (HL-71): its old path from the manifest's history, its landing
    # from the root's merge commit, so the map's rows for it still resolve inside the root.
    former, cuts = former_paths(ws), {}
    for s in sources.values():
        if s.kind != "archived" or not s.root:
            continue
        if s.root not in cuts:
            cuts[s.root] = recorded_landings(pathlib.Path(ws) / "public" / s.root)
        if s.name in cuts[s.root]:
            s.path, s.landing = s.path or former.get(s.name, ""), cuts[s.root][s.name]
    for name, sub in (landings or {}).items():
        if name in sources:
            sources[name].landing = sub
    return Map(rows, sources)


def is_path_row(m, r):
    return (r.old.startswith("public/") or r.old.startswith("private/")) and m.source_of(r.old) is not None


def owner(m, r, values):
    """The root that owns an identifier: the `root` column; else a root named in the new value; else a source
    repository named in the notes or the old value."""
    if r.root:
        return r.root
    for v in values:
        toks = set(re.split(r"[^a-z0-9]+", v.lower()))
        hit = [root for root in m.roots if root in toks and root != "workspace"]
        if len(hit) == 1:
            return hit[0]
    text = f"{r.old} {r.notes}"
    names = [n for n in m.sources if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text)]
    roots = {m.sources[n].root for n in names if m.sources[n].root}
    return roots.pop() if len(roots) == 1 else None


def _split_value(text):
    p = PREFIXED.fullmatch(text.strip())
    if not p:
        return None, None
    kind, rest = p.group(1), p.group(2)
    if kind == "identifier":
        s = SUBKIND.fullmatch(rest)
        if s and not rest.startswith("/"):
            kind, rest = f"identifier:{s.group(1)}", s.group(2)
    return kind, rest


# A path the text names without its scheme: `path:fw_cfg:opt/org.opendap/x` stands as `opt/org.opendap/x` (HL-109).
QUALIFIER = re.compile(r"^[a-z][a-z0-9_]*:(?!/)")


def _unqualified(kind, value):
    return QUALIFIER.sub("", value) if kind == "path" else value


def kind_of(value):
    """A map value's kind, with an identifier's subkind (`crate`, `identifier:user`); None when unprefixed."""
    return _split_value(value)[0]


def identifier(m, r):
    """The identifier row as old → new value lists, with the reason it needs a hand (or None)."""
    kind_o, old = _split_value(r.old)
    kind_n, new = _split_value(r.new) if r.new else (None, None)
    if r.new and new is None and kind_o:
        kind_n, new = kind_o, r.new.strip()  # a new value written without its kind takes the old one's (HL-109)
    olds = [_unqualified(kind_o, v.strip()) for v in (old or "").split(";")]
    news = [_unqualified(kind_n, v.strip()) for v in (new or "").split(";")]
    own = owner(m, r, news if new else [])
    reason = None
    if old is None:
        reason = "unparsed"
    elif r.action not in {"rename", "merge"}:
        reason = f"action-{r.action}"
    elif new is None or kind_n != kind_o:
        reason = "kind-mismatch"
    elif len(olds) != len(news):
        reason = "count-mismatch"
    elif any(set(v) & UNSAFE or not v for v in olds + news) or any(v.startswith("(") for v in news):
        reason = "not-a-token"
    elif not r.root and kind_o not in CONTEXT_ONLY and any(not (set(v) & SEPARATORS or " " in v) for v in olds):
        reason = "plain-word"  # a `root` set by hand says the word is the identifier wherever it stands alone;
        # maker keys and users are rewritten in their contexts only (rename_keys, rename_idents), never as words
    elif own is None:
        reason = "owner-unknown"
    return Identifier(r, olds, news, own, reason)


def identifier_rows(m):
    return [r for r in m.rows if r.kind != "repo" and not is_path_row(m, r) and PREFIXED.match(r.old)
            and r.old != r.new]
