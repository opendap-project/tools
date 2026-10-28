"""Reference rewriting for one root's cut: relative Markdown links, GitHub URLs and identifiers, line by line.

Every function takes a text and returns the new text plus one edit per changed reference (line, old, new), so a
dry run and an apply report the same edits. Spec: docs/specs/rename.md.
"""
import dataclasses
import posixpath
import re

ORG = "https://github.com/opendap-project/"
URL = re.compile(r"https://github\.com/opendap-project/((?>\.?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*))"
                 r"(?:/(blob|tree|raw)/([^/\s)\]\"'>#?]+)((?:/[^\s)\]\"'>#?]*[^\s)\]\"'>#?.,;:/])?)/?|/?(?![\w/]))")
LINK = re.compile(r"(\]\()(<?)([^)\s>]+)(>?)((?:\s+\"[^\"]*\")?\))")
REFDEF = re.compile(r"^(\s{0,3}\[[^\]]+\]:\s*)(<?)(\S+?)(>?)(\s.*)?$")
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
MOVABLE_REFS = {"main", "master", "HEAD"}
PIN_LINE = re.compile(r"^\s*(?:-\s+)?repo:\s")  # a pre-commit pin: it moves with its rev, after the cut
CARGO_PIN = re.compile(r"\bgit\s*=\s*\"|^\s*source\s*=\s*\"git\+")  # a Cargo git pin moves with its rev, after the cut
ISSUES = re.compile(r"https://github\.com/opendap-project/([A-Za-z0-9_.-]+)/issues(?![\w/])")
METADATA_LINE = re.compile(r"^\s*(?:repository|homepage|documentation|Repository|Homepage|Source)\s*=")
FROZEN = ("tests/",)  # tests and their fixtures are inputs (URLs as test data, sha256 goldens): never rewritten


def frozen(path):
    """True for a file no rewrite touches: anything under a `tests/` folder, whose text is test input."""
    return any(f"/{d}" in f"/{path}" for d in FROZEN)


@dataclasses.dataclass
class Edit:
    kind: str  # link | url | identifier
    line: int
    old: str
    new: str


class Paths:
    """Where paths inside roots go: exact file moves, `dir/**` prefixes, and folders inferred from their files."""

    def __init__(self, moves, deleted=(), prefixes=()):
        self.moves = dict(moves)
        self.deleted = set(deleted)
        self.prefixes = sorted(prefixes, key=lambda p: -len(p[0]))
        self.dirs = {}
        for a, b in self.moves.items():
            pa, pb = a.split("/")[:-1], b.split("/")[:-1]
            sa, sb = a.split("/")[-1], b.split("/")[-1]
            while pa and pb and sa == sb:
                self.dirs.setdefault("/".join(pa), set()).add("/".join(pb))
                sa, sb = pa[-1], pb[-1]
                pa, pb = pa[:-1], pb[:-1]

    def map(self, p):
        """The new path of `p` (file or folder), p itself when nothing moves it, None when it is ambiguous."""
        p = p.rstrip("/")
        if p in self.moves:
            return self.moves[p]
        for a, b in self.prefixes:
            if p == a or p.startswith(a + "/"):
                return b + p[len(a):]
        hits = self.dirs.get(p)
        if hits is None:
            return p
        return next(iter(hits)) if len(hits) == 1 else None


def _split_anchor(target):
    for sep in ("#", "?"):
        if sep in target:
            i = target.index(sep)
            return target[:i], target[i:]
    return target, ""


def _relink(target, src_dir, dst_dir, paths):
    """The relative target as seen from the file's new folder, or None when it does not change."""
    if not target or target.startswith(("#", "/")) or SCHEME.match(target):
        return None
    path, anchor = _split_anchor(target)
    if not path:
        return None
    trailing = "/" if path.endswith("/") else ""
    resolved = posixpath.normpath(posixpath.join(src_dir, path))
    if resolved.startswith(".."):
        return None
    new = paths.map(resolved)
    if new is None or (new == resolved and src_dir == dst_dir):
        return None
    rel = posixpath.relpath(new, dst_dir or ".") + trailing
    return None if rel + anchor == target else rel + anchor


def links(text, src, dst, paths):
    """Rewrite the relative links of a Markdown file that moves from `src` to `dst` (paths inside the root), for
    the files `paths` moves. Links inside fenced code blocks are examples and stay."""
    src_dir, dst_dir = posixpath.dirname(src), posixpath.dirname(dst)
    out, edits, fence = [], [], False
    for n, line in enumerate(text.split("\n"), start=1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if fence:
            out.append(line)
            continue

        def sub(m, target_group=3):
            new = _relink(m.group(target_group), src_dir, dst_dir, paths)
            if new is None:
                return m.group(0)
            edits.append(Edit("link", n, m.group(target_group), new))
            return m.group(0).replace(m.group(target_group), new, 1)

        line = LINK.sub(sub, line)
        line = REFDEF.sub(sub, line)
        out.append(line)
    return "\n".join(out), edits


def urls(text, sources, root, paths_of, path=""):
    """Rewrite GitHub URLs to source repositories of cut roots (`sources`: name → Source) into URLs of their root.
    Pinned refs (tags, commits) stay: the archived repository keeps serving them. A pre-commit `repo:` pin stays
    (its `rev` is a tag of the old repository), and so does a Cargo git pin (`git =`, `source = "git+`); package
    metadata (`repository = `) gets the root's own URL, and an issue tracker (`<repo>/issues`) the root's tracker."""
    edits = []
    landed = {}  # a root that reuses a source's name (t1): its URLs under a landing folder are already new
    for s in sources.values():
        if s.landing:
            landed.setdefault(s.root, set()).add(s.landing)
    base_name = posixpath.basename(path)
    pin_line = PIN_LINE if base_name == ".pre-commit-config.yaml" else (
        CARGO_PIN if base_name in ("Cargo.toml", "Cargo.lock") else None)

    def sub(m, n, bare):
        name, kind, ref, path = m.group(1), m.group(2), m.group(3), (m.group(4) or "").lstrip("/")
        s = sources.get(name.removesuffix(".git"))
        if s is None or s.landing is None or (kind and ref not in MOVABLE_REFS):
            return m.group(0)
        if s.name == s.root and any(path == d or path.startswith(d + "/") for d in landed.get(s.root, ())):
            return m.group(0)
        base = f"{s.landing}/{path}".strip("/") if s.landing else path
        new_path = paths_of(s.root).map(base) if base else base
        if new_path is None:
            return m.group(0)
        if name.endswith(".git") and not kind:
            new = f"{ORG}{s.root}.git"
        elif not new_path or (bare and not kind):  # a repository URL in package metadata
            new = f"{ORG}{s.root}"
        else:
            new = f"{ORG}{s.root}/{kind or 'tree'}/main/{new_path}"
        if new == m.group(0):
            return m.group(0)
        edits.append(Edit("url", n, m.group(0), new))
        return new

    def tracker(m, n):
        s = sources.get(m.group(1))
        if s is None or s.landing is None:
            return m.group(0)
        new = f"{ORG}{s.root}/issues"
        edits.append(Edit("url", n, m.group(0), new))
        return new

    out = []
    for n, line in enumerate(text.split("\n"), start=1):
        if pin_line is not None and pin_line.search(line):
            out.append(line)
            continue
        bare = bool(METADATA_LINE.match(line))
        line = ISSUES.sub(lambda m: tracker(m, n), line)
        out.append(URL.sub(lambda m: sub(m, n, bare), line))
    return "\n".join(out), edits


def to_relative(text, root, dst):
    """Inside the root's own Markdown, a link to the root's own GitHub URL at main becomes a relative link."""
    prefix = re.compile(rf"(\]\()({re.escape(ORG + root)}/(?:blob|tree)/main/([^)\s#]+))(#[^)\s]*)?")
    dst_dir = posixpath.dirname(dst)
    edits, out = [], []
    for n, line in enumerate(text.split("\n"), start=1):
        def sub(m):
            rel = posixpath.relpath(m.group(3), dst_dir or ".")
            edits.append(Edit("link", n, m.group(2), rel))
            return f"{m.group(1)}{rel}{m.group(4) or ''}"
        out.append(prefix.sub(sub, line))
    return "\n".join(out), edits


def _bounded(value):
    left = r"(?<![\w./-])" if re.match(r"\w", value) else ""
    right = r"(?![\w-])(?!\.\w)" if re.search(r"\w$", value) else ""
    return left + re.escape(value) + right


def identifier_pattern(pairs):
    """One pattern for every old value, longest first, so `/usr/bin/odap-x` wins over `odap-x`."""
    olds = sorted({o for o, _ in pairs}, key=len, reverse=True)
    if not olds:
        return None
    return re.compile("|".join(_bounded(o) for o in olds))


def identifiers(text, pattern, table):
    if pattern is None:
        return text, []
    edits, out = [], []
    for n, line in enumerate(text.split("\n"), start=1):
        def sub(m):
            new = table[m.group(0)]
            edits.append(Edit("identifier", n, m.group(0), new))
            return new
        out.append(pattern.sub(sub, line))
    return "\n".join(out), edits


def count(text, pattern):
    return [(n, m.group(0)) for n, line in enumerate(text.split("\n"), start=1) for m in pattern.finditer(line)]
