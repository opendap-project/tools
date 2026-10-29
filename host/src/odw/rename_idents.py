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
        for kind, old, new in rows:
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
    """The identifier rows of a cut: (kind, old, new) rows to apply, the maker keys (old, new, row line) and the scan
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
            apply.extend((kind, o, n) for o, n in zip(i.old, i.new))
        if not (i.reason or "").startswith("action-") and i.reason not in {"unparsed", "not-a-token"}:
            scan.append((i, status))
    return apply, keys, scan
