"""odw rename links: the workspace-wide link check after the cuts (RFC 0017 §7; spec docs/specs/rename.md, Links).

Usage: odapkit rename links [--workspace DIR] [--map TSV] [--apply] [--find-in REPO ...] [--keep GLOB ...]
       [--json | --text]

Reads the tracked files of every repository of the workspace (the workspace itself, repos.tsv and the repos.tsv of any
overlay cloned two folders down) and reports, as opendap.check-result/v1, every GitHub URL that still names
the source repository of a cut root at a movable ref (`rename.old-url`) and every URL at main into a repository of
the workspace whose path is not tracked there (`rename.broken-url`). --apply rewrites the old URLs whose new
target exists, with the rename's URL rule, and leaves the rest for a hand; with --find-in, a dead URL of a private
file goes to the one file of its name in that repository (never in a public file). History stays as it is: files under
history/, archive/, plans/, journal/ and runs/ (and --keep globs), and tests/ (test input). Exit status: 0 nothing
left, 1 links left, 2 usage.
"""
import dataclasses
import datetime as dt
import fnmatch
import json
import os
import pathlib
import posixpath
import sys
import urllib.parse

from odw import audit, clikit, rename, rename_map, rename_merge, rename_rewrite as rw, workspace

TOOL = "odapkit rename links"
KEEP = ("history/**", "archive/**", "plans/**", "journal/**", "runs/**")


def repositories(ws):
    """name -> (workspace path, checkout) for the workspace and every repository its lists name that is cloned."""
    out = {"workspace": (".", ws)} if (ws / ".git").exists() else {}
    # An overlay is any cloned repository two folders down with its own list, as rename_map reads them.
    lists = [ws / "repos.tsv", *sorted(ws.glob("*/*/repos.tsv"))]
    for tsv in (p for p in lists if p.is_file()):
        for line in tsv.read_text().splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) >= 2 and (ws / cols[0] / ".git").exists():
                out[cols[1].strip().rsplit("/", 1)[-1]] = (cols[0].strip(), ws / cols[0].strip())
    return out


def kept(path, globs):
    """True for history (a file under one of the KEEP folders at any depth, or a --keep glob) and test input."""
    parts = path.split("/")
    for g in globs:
        if fnmatch.fnmatch(path, g) or any(fnmatch.fnmatch("/".join(parts[i:]), g) for i in range(1, len(parts))):
            return True
    return rw.frozen(path)


class Targets:
    """What exists at main in each repository of the workspace: its tracked files and their folders."""

    def __init__(self, repos):
        self.files = {n: set(audit.tracked(d)) for n, (_, d) in repos.items()}
        self.dirs = {n: {"/".join(f.split("/")[:i]) for f in fs for i in range(1, f.count("/") + 1)}
                     for n, fs in self.files.items()}

    def missing(self, url):
        """For a URL at main into a repository of the workspace, the path that is not there; else None."""
        m = rw.URL.match(url)
        if not m or m.group(2) is None or m.group(3) not in rw.MOVABLE_REFS:
            return None
        name, path = m.group(1).removesuffix(".git"), urllib.parse.unquote((m.group(4) or "").strip("/"))
        if name not in self.files or not path or path in self.files[name] or path in self.dirs[name]:
            return None
        return path


def _sources(m, ws, cut):
    seen = {r: rename_merge.recorded(ws / "public" / r) for r in cut}
    cache = {}

    def paths_of(r):
        if r not in cache:
            cache[r] = rename._paths_from_rows(m, r, seen.get(r, (None,))[0])
        return cache[r]

    sources = {s.name: dataclasses.replace(s, landing=seen.get(s.root, (None, {}))[1].get(s.name, s.landing))
               for s in m.sources.values() if s.root in cut and s.kind != "root"}
    return sources, paths_of


def _old(url, sources):
    """True for a URL that names a source repository of a cut root at a movable ref (or bare)."""
    m = rw.URL.match(url)
    if not m:
        return False
    s = sources.get(m.group(1).removesuffix(".git"))
    if s is None or (m.group(2) and m.group(3) not in rw.MOVABLE_REFS):
        return False
    if s.name == s.root:  # a root that reuses the source's name: its own URLs are already new
        path = (m.group(4) or "").strip("/")
        return bool(path) and not any(path == d or path.startswith(d + "/") for d in _landings(sources, s.root))
    return True


def _landings(sources, root):
    return {s.landing for s in sources.values() if s.root == root and s.landing}


class Namesakes:
    """With --find-in: the one tracked file of a URL's file name in the named repositories, not counting frozen
    copies (history/, archive/) or test input; a plan or a journal is a target like any other file."""

    def __init__(self, targets, repos):
        self.index = {}
        for name in repos:
            for f in targets.files.get(name, ()):
                if not kept(f, ("history/**", "archive/**")):
                    self.index.setdefault(posixpath.basename(f), []).append(f"{rw.ORG}{name}/blob/main/{f}")

    def find(self, url):
        m = rw.URL.match(url)
        hits = self.index.get(posixpath.basename(urllib.parse.unquote(m.group(4) or ""))) if m else None
        return hits[0] if hits and len(hits) == 1 else None


@dataclasses.dataclass
class Context:
    sources: dict
    paths_of: object
    targets: Targets
    namesakes: Namesakes
    apply: bool


def _pinned(line, path):
    """A pre-commit or Cargo pin: its URL moves with its rev, after the cut (rename_rewrite.urls)."""
    base = posixpath.basename(path)
    pin = {".pre-commit-config.yaml": rw.PIN_LINE, "Cargo.toml": rw.CARGO_PIN, "Cargo.lock": rw.CARGO_PIN}.get(base)
    return bool(pin and pin.search(line))


def _line(line, path, private, c):
    """(new line, problems as (kind, message), URLs rewritten) for one line of a file."""
    if _pinned(line, path):
        return line, [], 0
    olds = [u.group(0) for u in rw.URL.finditer(line)]
    news = [u.group(0) for u in rw.URL.finditer(rw.urls(line, c.sources, None, c.paths_of, path)[0])]
    tos = dict(zip(olds, news)) if len(olds) == len(news) else {}
    problems, done = [], 0
    for url in olds:
        if _old(url, c.sources):
            to = tos.get(url, url)
            gone = c.targets.missing(to) or (to == url and "unmapped")
            kind, fix = "old-url", None if gone else to
            why = f"{url} -> {to}" + (f" (no such path: {gone})" if gone else "")
        elif c.targets.missing(url):
            kind, fix, why = "broken-url", None, f"{url}: no such path at main ({c.targets.missing(url)})"
        else:
            continue
        found = c.namesakes.find(url) if fix is None and private else None
        fix = fix or found
        if found:
            why += f"; {found} (found in {found[len(rw.ORG):].split('/')[0]})"
        if c.apply and fix and not c.targets.missing(fix):
            line, done = line.replace(url, fix, 1), done + 1
        else:
            problems.append((kind, why))
    return line, problems, done


def scan(ws, m, keep=KEEP, apply=False, find_in=()):
    """The check-result results, and with `apply` the files rewritten (repository path -> URLs rewritten)."""
    repos = repositories(ws)
    cut = [r for r in rename_map.cut_roots(ws, None) if r]
    sources, paths_of = _sources(m, ws, cut)
    targets = Targets(repos)
    c = Context(sources, paths_of, targets, Namesakes(targets, find_in), apply)
    results, written, counts = [], {}, {"files": 0, "old-url": 0, "broken-url": 0}
    for name, (rel, checkout) in sorted(repos.items(), key=lambda x: x[1][0]):
        for f in sorted(targets.files[name]):
            text = None if kept(f, keep) else rename._read_text(checkout / f)
            if text is None or "github.com/opendap-project/" not in text:
                continue
            counts["files"] += 1
            where, lines, done = f if rel == "." else f"{rel}/{f}", text.split("\n"), 0
            for n, line in enumerate(lines, start=1):
                lines[n - 1], problems, k = _line(line, f, rel.startswith("private/"), c)
                done += k
                for kind, why in problems:
                    counts[kind] += 1
                    results.append({"id": f"rename.{kind}:{where}:{n}", "verdict": "fail", "message": why,
                                    "location": {"path": where, "line": n}})
            if done:
                (checkout / f).write_text("\n".join(lines))
                written[where] = done
    left = counts["old-url"] + counts["broken-url"]
    results.append({"id": "rename.links", "verdict": "fail" if left else "pass", "value": left,
                    "message": f"{counts['old-url']} old and {counts['broken-url']} broken repository URLs left in "
                               f"{len(repos)} repositories ({counts['files']} files with URLs read; cut: "
                               f"{', '.join(cut)}); {sum(written.values())} URLs rewritten"})
    return results, written


def _parser():
    ap = clikit.Parser(prog=TOOL, summary="Old and broken repository URLs across the workspace, after the cuts",
                       examples=["odapkit rename links --map rename-map.tsv",
                                 "odapkit rename links --map rename-map.tsv --apply --json"])
    ap.add_argument("--workspace")
    ap.add_argument("--map", help="the rename map (default: $ODAP_RENAME_MAP)")
    ap.add_argument("--apply", action="store_true", help="rewrite the old URLs whose new target exists")
    ap.add_argument("--find-in", action="append", default=[], metavar="REPO",
                    help="point a dead URL of a private file to the one file of its name in REPO (outside history)")
    ap.add_argument("--keep", action="append", default=[], metavar="GLOB",
                    help=f"more history to leave as it is (always: {', '.join(KEEP)}, tests/)")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON (the default when stdout is not a terminal)")
    fmt.add_argument("--text", action="store_true")
    return ap


def main(argv=None):
    return clikit.main(TOOL, _main, sys.argv[1:] if argv is None else argv)


def _main(argv):
    args = _parser().parse_args(argv)
    as_json = args.json or (not args.text and not sys.stdout.isatty())
    ws = pathlib.Path(args.workspace) if args.workspace else workspace.root()
    if ws is None:
        raise clikit.no_workspace(TOOL)
    map_path = args.map or os.environ.get("ODAP_RENAME_MAP")
    if not map_path or not pathlib.Path(map_path).is_file():
        raise rename.Refused("rename.no-map", f"no rename map ({map_path or 'none given'})",
                             "pass the map with --map or set ODAP_RENAME_MAP",
                             "odapkit rename links --map <rename-map.tsv>", 2)
    results, written = scan(ws, rename_map.load(ws, map_path, {}), KEEP + tuple(args.keep), args.apply,
                            args.find_in)
    doc = {"schema": "opendap.check-result/v1", "tool": {"name": TOOL}, "subject": {"repo": "workspace"},
           "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "results": results}
    if as_json:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
    else:
        print("\n".join([f"wrote {p} ({n} URLs)" for p, n in written.items()]
                        + [f"{r['verdict']} {r['id']}: {r['message']}" for r in results]))
    return 1 if any(r["verdict"] == "fail" for r in results) else 0
