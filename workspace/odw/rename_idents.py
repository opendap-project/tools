"""Identifier rows by kind and context (HL-109): where a renamed crate, binary, path or user stands in a file.

Every row is a whole token by default (rename_rewrite.identifiers); a kind adds or narrows contexts:

- `crate`: in Cargo.toml and Cargo.lock it is also a key before `.workspace = true` and the like, and wins over a
  binary of the same name; in Rust sources its `snake_case` form is a path (`opendap_input::` → `blackbird_ui_input::`).
  A crate that is also a binary is the crate only in Cargo files and after `-p` or `--package`; elsewhere the text
  names the binary (`odap-boot handoff`).
- `binary`: also the last segment of a path (`target/release/odap-vdev-view`).
- `path` under /run, /var/lib, /var/cache, /var/log or /etc: also the value of the systemd key that takes it without
  its prefix (`RuntimeDirectory=opendap/composer`).
- `identifier:user`: a plain word, rewritten only where it names the user or group: User=, Group=, SocketUser=,
  SocketGroup=, SupplementaryGroups=, sysusers.d and tmpfiles.d columns, `~user`, `/home/user`, `chown user:group`.
Spec: docs/specs/rename.md.
"""
import os
import re

from odw import rename_keys as rk, rename_map, rename_rewrite as rw

DIRECTORIES = {"/run/": "RuntimeDirectory", "/var/lib/": "StateDirectory", "/var/cache/": "CacheDirectory",
               "/var/log/": "LogsDirectory", "/etc/": "ConfigurationDirectory"}
DIRECTORY_LINE = re.compile(r"^(\s*(?:" + "|".join(DIRECTORIES.values()) + r")\s*=)(.*)$")
PACKAGE = r"(?P<pre>(?<![\w-])(?:-p|--package)(?:=|\s+|[\"']\s*,\s*[\"'])){k}(?![\w-])"
USER_ANYWHERE = (
    r"(?P<pre>^\s*(?:User|Group|SocketUser|SocketGroup)\s*=\s*){k}(?=\s*$)",
    r"(?P<pre>^\s*SupplementaryGroups\s*=(?:.*\s)?){k}(?=\s|$)",
    r"(?P<pre>~){k}(?![\w-])",
    r"(?P<pre>/home/){k}(?![\w-])",
    r"(?P<pre>\bchown\s+(?:-\S+\s+)*){k}(?=[:.\s])",
    r"(?P<pre>\bchown\s+(?:-\S+\s+)*[\w.-]+:){k}(?![\w-])",
)
USER_SYSUSERS = (r"(?P<pre>^[ugmr]\s+){k}(?=\s)", r"(?P<pre>^m\s+\S+\s+){k}(?=\s|$)")
USER_TMPFILES = (r"(?P<pre>^[A-Za-z][+!=~^-]*\s+\S+\s+\S+\s+){k}(?=\s)",
                 r"(?P<pre>^[A-Za-z][+!=~^-]*\s+\S+\s+\S+\s+\S+\s+){k}(?=\s|$)")


# Test data stays as it is (goldens hash it); a test that drives the renamed thing is rewritten (HL-109 case 4).
FIXTURES = re.compile(r"(^|/)(fixtures|goldens?|snapshots?)/")
# An ADR whose status is settled is immutable: a later decision supersedes it, never an edit (case 7).
ADR = re.compile(r"(^|/)adr/[^/]+\.md$")
SETTLED = re.compile(r"^\s*-?\s*\*\*Status:?\*\*:?\s*(accepted|superseded|deprecated|rejected)\b", re.I | re.M)


def untouched(path):
    """Test data: a fixture, golden or snapshot is never rewritten."""
    return bool(FIXTURES.search(path))


def immutable(path, text):
    """An ADR whose status is accepted, superseded, deprecated or rejected."""
    return bool(ADR.search(path)) and bool(SETTLED.search(text))


def _snake(v):
    return v.replace("-", "_")


def _token(value, left_slash=False, cargo=False):
    """`value` as a whole token; a binary may follow a `/`, a crate in a Cargo file may precede `.key`."""
    left = (r"(?<![\w.-])" if left_slash else r"(?<![\w./-])") if re.match(r"\w", value) else ""
    right = (r"(?![\w-])" if cargo else r"(?![\w-])(?!\.\w)") if re.search(r"\w$", value) else ""
    return left + re.escape(value) + right


def _alternation(entries):
    """One pattern over (regex, old) entries, longest old first; None when there are none."""
    entries = sorted(entries, key=lambda e: len(e[1]), reverse=True)
    return re.compile("|".join(f"(?:{r})" for r, _ in entries)) if entries else None


def _contexts(patterns, table):
    if not table:
        return []
    alt = "|".join(re.escape(k) for k in sorted(table, key=len, reverse=True))
    return [re.compile(p.replace("{k}", f"(?P<k>{alt})")) for p in patterns]


class Rewriter:
    """The identifier rewrites of a cut: `rows` are (kind, old, new), kind `crate`, `binary`, `path`,
    `identifier:user` or any other (a whole token)."""

    def __init__(self, rows):
        by = {}
        for kind, old, new, *_ in rows:
            by.setdefault(kind, {})[old] = new
        self.crates, self.binaries = by.pop("crate", {}), by.pop("binary", {})
        self.users = by.get("identifier:user", {})
        others = {o: n for k, t in by.items() if k != "identifier:user" for o, n in t.items()}
        shared = set(self.crates) & set(self.binaries)
        plain = [(_token(o), o) for o in others]
        crate_only = {o: n for o, n in self.crates.items() if o not in shared}
        # Anywhere but Cargo files: binaries (as path ends too), crates that are not also binaries, the rest.
        self.table = {**others, **crate_only, **self.binaries}
        self.pattern = _alternation(plain + [(_token(o), o) for o in crate_only]
                                    + [(_token(o, left_slash=True), o) for o in self.binaries])
        # In Cargo files a crate wins over a binary of the same name and may precede `.workspace`.
        self.cargo_table = {**others, **self.binaries, **self.crates}
        self.cargo_pattern = _alternation(plain + [(_token(o, left_slash=True), o) for o in self.binaries
                                                   if o not in shared]
                                          + [(_token(o, cargo=True), o) for o in self.crates])
        self.package = _contexts([PACKAGE], {o: self.crates[o] for o in shared})
        snakes = {_snake(o): _snake(n) for o, n in self.crates.items() if _snake(o) != o or _snake(n) != n}
        self.snakes = snakes
        self.snake_pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, sorted(snakes, key=len, reverse=True)))
                                        + r")(?!\w)") if snakes else None
        self.directories = {}
        for o, n in by.get("path", {}).items():
            for prefix, key in DIRECTORIES.items():
                if o.startswith(prefix) and n.startswith(prefix):
                    self.directories.setdefault(key, {})[o[len(prefix):]] = n[len(prefix):]

    def _users(self, path):
        pats = list(USER_ANYWHERE)
        if "sysusers" in path:
            pats += USER_SYSUSERS
        if "tmpfiles" in path:
            pats += USER_TMPFILES
        return _contexts(pats, self.users)

    def rewrite(self, path, text):
        """The text with every identifier rewritten in its contexts, and one edit per rewrite."""
        name = path.rsplit("/", 1)[-1]
        cargo = name in ("Cargo.toml", "Cargo.lock")
        pattern, table = (self.cargo_pattern, self.cargo_table) if cargo else (self.pattern, self.table)
        contexts = [(p, self.crates) for p in self.package] + [(p, self.users) for p in self._users(path)]
        snake = self.snake_pattern if path.endswith(".rs") else None
        edits, out = [], []
        for n, line in enumerate(text.split("\n"), start=1):
            def note(old, new):
                edits.append(rw.Edit("identifier", n, old, new))
                return new
            line = self._directory(line, note)
            for p, t in contexts:
                line = p.sub(lambda m, t=t: m.group("pre") + note(m.group("k"), t[m.group("k")]), line)
            if pattern is not None:
                line = pattern.sub(lambda m: note(m.group(0), table[m.group(0)]), line)
            if snake is not None:
                line = snake.sub(lambda m: note(m.group(0), self.snakes[m.group(0)]), line)
            out.append(line)
        return "\n".join(out), edits

    def _directory(self, line, note):
        m = DIRECTORY_LINE.match(line)
        if not m:
            return line
        key = m.group(1).split("=")[0].strip()
        table = self.directories.get(key, {})
        values = [note(v, table[v]) if v in table else v for v in m.group(2).split(" ")]
        return m.group(1) + " ".join(values)


def collect(m, cut, kinds=None):
    """The identifier rows of a cut: (kind, old, new, map line) rows to apply, the maker keys (old, new, row line) and the scan
    (each identifier with its status: apply, later or manual)."""
    apply, keys, scan = [], [], []
    for r in rename_map.identifier_rows(m):
        if kinds and rk.kind_of(r.old) not in kinds:
            continue
        i = rename_map.identifier(m, r)
        if i.reason is None and i.owner in cut:
            status = "apply"
        elif i.reason is None:
            status = "later"
        else:
            status = "manual"
        if status == "apply" and rk.is_key_row(r):
            keys.extend((o, n, r.line) for o, n in zip(i.old, i.new))
        elif status == "apply":
            kind = rename_map.kind_of(r.old)
            apply.extend((kind, o, n, r.line) for o, n in zip(i.old, i.new))
        if not (i.reason or "").startswith("action-") and i.reason not in {"unparsed", "not-a-token"}:
            scan.append((i, status))
    return apply, keys, scan


# Kinds a file is named after (case 12): a unit file and its drop-in folder or wants link, a PAM service, a udev rule,
# an installed binary. A crate's folder is not (its path rows move it), nor a binary whose new value is a path.
# The folder a name must sit in, by kind (None: anywhere, its suffix makes it specific: `.service`, `.rules`), so a
# PAM service or a binary named like a crate folder never moves that folder.
NAMED = {"unit": None, "identifier:udev": None, "identifier:pam": {"pam.d"}, "binary": {"bin", "sbin", "libexec"}}


def _names(rows):
    """{old: (new, line, kind)} for the rows a file name can carry, and the pattern of their tokens in a segment."""
    table = {o: (n, line, kind) for kind, o, n, line in rows if kind in NAMED and "/" not in o + n}
    if not table:
        return table, None
    alt = "|".join(re.escape(o) for o in sorted(table, key=len, reverse=True))
    return table, re.compile(rf"(?<![\w-])(?:{alt})(?![\w-])")


def renamed(path, table, pattern):
    """The path with every token a segment holds renamed (`x.service.d/`, `70-x.rules`), and the first row's line."""
    lines, segs = [], path.split("/")

    def at(parent):
        def sub(m):
            new, line, kind = table[m.group(0)]
            if NAMED[kind] is not None and parent not in NAMED[kind]:
                return m.group(0)
            lines.append(line)
            return new
        return sub
    new = "/".join(pattern.sub(at(segs[i - 1] if i else ""), seg) for i, seg in enumerate(segs))
    return new, (lines[0] if lines else None)


def plan_name_moves(plan, files, moves, rows):
    """Add a move for every file whose path holds a named token (HL-109 case 12), then the clashes: two files that
    end at one path, or a move onto a file that exists and stays."""
    table, pattern = _names(rows)
    if pattern is None:
        return
    deleted = {d["path"] for d in plan.deletes}
    added = []
    for src in sorted(files):
        if src in deleted:
            continue
        dst = moves.get(src, src)
        new, line = renamed(dst, table, pattern)
        if new == dst:
            continue
        for mv in plan.moves:
            if mv["from"] == src:
                mv["to"] = new
                break
        else:
            plan.moves.append({"row": line, "from": src, "to": new})
        moves[src], _ = new, added.append(new)
    leaving = set(moves) | deleted
    targets = {}
    for mv in plan.moves:
        targets.setdefault(mv["to"], []).append(mv)
    lower = {p.lower(): p for p in files}
    for t in sorted(set(added)):
        mvs, clash = targets[t], lower.get(t.lower())
        if len(mvs) > 1:
            plan.conflicts.append({"path": t, "rows": sorted({m["row"] for m in mvs}),
                                   "reason": "name clash: " + " and ".join(m["from"] for m in mvs) + " both become this path"})
        elif clash is not None and clash not in leaving:
            plan.conflicts.append({"path": t, "rows": [mvs[0]["row"]], "reason": f"name clash: {clash} exists and stays"})


def link_target(file, rows):
    """A tracked link's new target, or None when its target names no token (a link is never read as text)."""
    table, pattern = _names(rows)
    target = os.readlink(file)
    new = renamed(target, table, pattern)[0] if pattern is not None else target
    return new if new != target else None


def relink(top, links):
    """Write each link (path inside `top` -> new target) as a link."""
    for rel, target in links.items():
        p = top / rel
        if p.is_symlink() or p.exists():
            p.unlink()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(target)
