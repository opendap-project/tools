"""odw docs: the docs gates and the generated docs index.

Usage:
    odapkit docs check [--strict] [--json] PATH...   Markdown files or folders
    odapkit docs index DOCS_DIR [--readme R] [--check] the table between <!-- index:start --> and <!-- index:end -->
    odapkit docs rfc-status [--workspace DIR] [--write DIR] [--json]   which affected repositories recorded each RFC

Errors: broken relative links, relative links that leave the repository (links between repositories are GitHub
URLs), links from a public repository to a private one (names from the private overlay's repos.tsv), lower-case project terms outside
code. Warnings (errors with --strict): working-note phrases outside an `## Open items` section, a status outside
the vocabulary (draft, accepted, confirmed, superseded, withdrawn, implemented). The header is the bullet list of
`**Key:**` items right after the title. Exit status: 0 clean, 1 findings that fail, 2 usage. rfc-status
(odw.rfcstatus) fails when an accepted or implemented RFC has an affected repository that has not recorded it.
Spec: docs/specs/docs-gates.md.
"""
import argparse
import dataclasses
import json
import os
import pathlib
import re
import sys

from odw import rfcstatus, workspace

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
GITHUB_REPO = re.compile(r"https://github\.com/opendap-project/([A-Za-z0-9._-]+)")
TERMS = {re.compile(r"\bopen source\b"): "Open Source", re.compile(r"\bopen hardware\b"): "Open Hardware",
         re.compile(r"\bdigital audio player\b"): "Digital Audio Player"}
WORKING = re.compile(r"\b(for now|next step|not started|TODO|FIXME|will take this over)\b", re.I)
STATUS_WORDS = ("draft", "accepted", "confirmed", "superseded", "withdrawn", "implemented")
HEADER_ITEM = re.compile(r"^- \*\*([^*:]+):\*\* ?(.*)$")
INDEX_START, INDEX_END = "<!-- index:start -->", "<!-- index:end -->"


@dataclasses.dataclass
class Finding:
    path: str
    line: int
    rule: str
    severity: str
    message: str


def header(text):
    """The `- **Key:** value` items right after the first heading, keys lower-cased."""
    out, seen_title = {}, False
    for line in text.splitlines():
        if not seen_title:
            seen_title = line.startswith("# ")
            continue
        if not line.strip() and not out:
            continue
        m = HEADER_ITEM.match(line)
        if not m:
            break
        out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def _strip_code(line):
    return re.sub(r"`[^`]*`", "", line)


def private_repo_names(ws):
    names = set()
    if ws:
        for f in sorted(pathlib.Path(ws).glob("*/*/repos.tsv")):
            for row in f.read_text().splitlines()[1:]:
                if "\t" in row:
                    names.add(row.split("\t")[1].split("/")[-1])
    return names


def private_repo_paths(ws):
    paths = set()
    if ws:
        for f in sorted(pathlib.Path(ws).glob("*/*/repos.tsv")):
            for row in f.read_text().splitlines()[1:]:
                if "\t" in row:
                    paths.add(row.split("\t")[0])
    return paths


def _repo_root(path):
    for p in (path.parent, *path.parents):
        if (p / ".git").exists():
            return p
    return path.parent


def check_file(path, repo_root=None, private_repos=frozenset(), repo_is_private=False):
    path = pathlib.Path(path)
    repo_root = pathlib.Path(repo_root) if repo_root else _repo_root(path)
    text = path.read_text(errors="replace")
    rel = str(path)
    out, in_code, open_items = [], False, False
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if raw.startswith("## "):
            open_items = raw.strip().lower() == "## open items"
        line = _strip_code(raw)
        for target in LINK.findall(line):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                m = GITHUB_REPO.match(target)
                if m and m.group(1) in private_repos and not repo_is_private:
                    out.append(Finding(rel, n, "private-link", "error", f"links the private repository {m.group(1)}"))
                continue
            dest = (path.parent / target.split("#")[0]).resolve()
            if not str(dest).startswith(str(repo_root.resolve())):
                out.append(Finding(rel, n, "cross-repo-link", "error", f"{target} leaves the repository: use its GitHub URL"))
            elif not dest.exists():
                out.append(Finding(rel, n, "link", "error", f"{target} does not exist"))
        for rx, right in TERMS.items():
            for m in rx.finditer(GITHUB_REPO.sub("", line)):
                out.append(Finding(rel, n, "term", "error", f"'{m.group(0)}' is written '{right}'"))
        if not open_items and WORKING.search(line):
            out.append(Finding(rel, n, "working-note", "warning",
                               f"working note '{WORKING.search(line).group(0)}': move it to ## Open items or a private plan"))
    status = header(text).get("status")
    if status is not None and not status.lower().startswith(STATUS_WORDS):
        out.append(Finding(rel, 1, "status", "warning", f"status '{status[:40]}' is outside the vocabulary {', '.join(STATUS_WORDS)}"))
    return out


def _title(text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def render_index(docs_dir, readme=None):
    docs_dir = pathlib.Path(docs_dir)
    readme = pathlib.Path(readme) if readme else docs_dir / "README.md"
    text = readme.read_text()
    rows = ["| Document | Status |", "|---|---|"]
    for f in sorted(docs_dir.rglob("*.md")):
        if f == readme or f.name == "README.md" and f.parent.name == "requirements":
            continue
        t = f.read_text(errors="replace")
        status = header(t).get("status", "").replace("|", "/")
        link = pathlib.Path(os.path.relpath(f, readme.parent)).as_posix()
        rows.append(f"| [{(_title(t) or f.stem).replace('|', '/')}]({link}) | {status} |")
    a, b = text.index(INDEX_START), text.index(INDEX_END)
    return text[:a + len(INDEX_START)] + "\n" + "\n".join(rows) + "\n" + text[b:]


def index_is_current(docs_dir, readme=None):
    readme = pathlib.Path(readme) if readme else pathlib.Path(docs_dir) / "README.md"
    return readme.read_text() == render_index(docs_dir, readme)


SKIP_DIRS = frozenset({".git", "node_modules", "target", "vendor"})


def _files(paths):
    """Markdown under each path; not inside build trees, test fixtures (data, not docs) or a linked worktree below
    the path (a `.git` file: another task's copy of a repository that is checked on its own)."""
    for p in map(pathlib.Path, paths):
        if not p.is_dir():
            if p.suffix == ".md":
                yield p
            continue
        out = []
        for dirpath, dirnames, filenames in os.walk(p):
            d = pathlib.Path(dirpath)
            dirnames[:] = sorted(n for n in dirnames
                                 if n not in SKIP_DIRS and not n.endswith(".noindex")
                                 and not (n == "fixtures" and d.name == "tests")
                                 and not (d / n / ".git").is_file())
            out += [d / f for f in filenames if f.endswith(".md")]
        yield from sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="odapkit docs", description="Docs gates and the docs index.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("paths", nargs="+")
    ck.add_argument("--strict", action="store_true")
    ck.add_argument("--json", action="store_true")
    ix = sub.add_parser("index")
    ix.add_argument("docs_dir")
    ix.add_argument("--check", action="store_true")
    ix.add_argument("--readme", help="the README that holds the index (default: DOCS_DIR/README.md)")
    rs = sub.add_parser("rfc-status")
    rs.add_argument("--workspace")
    rs.add_argument("--write", metavar="DIR", help="write DIR/rfc-application.toml")
    rs.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if args.cmd == "rfc-status":
        return _rfc_status(args)
    if args.cmd == "index":
        d = pathlib.Path(args.docs_dir)
        readme = pathlib.Path(args.readme) if args.readme else d / "README.md"
        if args.check:
            if index_is_current(d, readme):
                return 0
            print(f"{readme} index is not current: run odapkit docs index {d}", file=sys.stderr)
            return 1
        readme.write_text(render_index(d, readme))
        return 0
    ws = workspace.root()
    private = private_repo_names(ws)
    private_paths = {str((ws / p).resolve()) for p in private_repo_paths(ws)} if ws else set()
    findings = []
    for p in _files(args.paths):
        root = _repo_root(p.resolve())
        findings += check_file(p, repo_root=root, private_repos=private, repo_is_private=str(root) in private_paths)
    failing = [f for f in findings if f.severity == "error" or args.strict]
    if args.json:
        print(json.dumps([dataclasses.asdict(f) for f in findings], indent=1))
    else:
        for f in findings:
            print(f"{f.path}:{f.line}: {f.severity}: {f.rule}: {f.message}")
    return 1 if failing else 0


def _rfc_status(args):
    ws = pathlib.Path(args.workspace) if args.workspace else workspace.root()
    report = rfcstatus.status(ws) if ws else None
    if report is None:
        print("odapkit docs rfc-status: no workspace with an rfcs repository (repos.tsv); use --workspace", file=sys.stderr)
        return 2
    if args.write:
        rfcstatus.write(report, args.write)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else rfcstatus.render(report))
    return 1 if report["failing"] else 0


if __name__ == "__main__":
    sys.exit(main())
