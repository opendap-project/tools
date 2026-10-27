"""odw rename merge: a root's source repositories joined into one root repository with their history (RFC 0017
§7 step 2), before `odapkit rename apply`.

Usage: odapkit rename merge ROOT --into DIR [--workspace DIR] [--map TSV] [--branch B] [--source NAME=LOCATION]
       [--landing NAME=DIR] [--engine auto|filter-repo|fast-export] [--report FILE] [--dry-run] [--json | --text]

Each source is cloned fresh (never touched), its history rewritten under its landing folder, and the rewritten
histories joined by one merge commit, the root's first, that records every source's head. It never pushes.
Exit status: 0 merged or the dry run found nothing in the way; 1 refused; 2 usage. Spec: docs/specs/rename.md.
"""
import argparse
import dataclasses
import json
import os
import pathlib
import shutil
import sys
import tempfile

from odw import rename_map, workspace
from odw import rename_merge_git as g

SCHEMA = "opendap.rename-merge/v1"
REF = "refs/odw-merge"


class Refused(Exception):
    def __init__(self, code, message, hint, fix, status=1):
        super().__init__(message)
        self.code, self.message, self.hint, self.fix, self.status = code, message, hint, fix, status

    def doc(self):
        return {"code": self.code, "message": self.message, "hint": self.hint, "fix": self.fix}


@dataclasses.dataclass
class Src:
    name: str
    path: str
    landing: str
    location: str  # where it is cloned from: the workspace clone, or --source
    local: bool  # a working clone, so dirty and unpushed are checked
    head: str | None = None
    report: dict = dataclasses.field(default_factory=dict)


def sources(m, root, ws, overrides, branch):
    """The root's sources in name order, with every reason the merge would refuse them."""
    found = sorted(m.sources_of(root), key=lambda s: s.name)
    if not found:
        raise Refused("merge.no-sources", f"the map joins no repository into {root}",
                      "a root's sources are its `repo:` rows (`repo:opendap-project/<root> (<folder>/)`)",
                      f"grep '^repo:' <rename-map.tsv> | grep {root}")
    out, problems = [], []
    for s in found:
        if s.landing is None:
            raise Refused("merge.landing-unknown", f"{s.name}: the map names no single landing folder in {root}",
                          "a repository row names one folder, as `(kicad/)`", f"--landing {s.name}=<folder>")
        landing = s.landing.strip("/")
        if landing and not g.LANDING.fullmatch(landing):
            raise Refused("merge.landing-unknown", f"{s.name}: {landing!r} is not a plain folder path",
                          "letters, digits, '.', '_' and '-' separated by '/'", f"--landing {s.name}=<folder>")
        loc = overrides.get(s.name, str(ws / s.path))
        local = g.is_worktree(loc)
        src = Src(s.name, s.path, landing, loc, local)
        out.append(src)
        problems += _problems(src, branch)
    _clashes(out)
    return out, problems


def _clashes(srcs):
    for a in srcs:
        for b in srcs:
            if a is b:
                continue
            if a.landing == b.landing or not a.landing or b.landing.startswith(a.landing + "/"):
                raise Refused("merge.landing-clash", f"{a.name} ({a.landing or '.'}/) and {b.name} "
                              f"({b.landing or '.'}/) overlap", "each source lands in a folder of its own",
                              f"--landing {b.name}=<another folder>")


def _problems(src, branch):
    p = lambda code, msg, hint, fix: [Refused(code, f"{src.name}: {msg}", hint, fix)]  # noqa: E731
    loc = pathlib.Path(src.location)
    if not src.local:
        if loc.exists() and loc.is_dir() and not (loc / "HEAD").exists():
            return p("merge.missing-source", f"{loc} is not a git repository", "pass a clone, a bundle or a URL",
                     f"--source {src.name}=<clone, bundle or URL>")
        if not loc.exists() and "://" not in src.location and not src.location.startswith("git@"):
            return p("merge.missing-source", f"no clone at {loc}", "the workspace has no clone of it, or no access "
                     "(bootstrap reports it skipped)", f"scripts/bootstrap.sh, or --source {src.name}=<bundle or URL>")
        src.head = g.remote_head(src.location, branch)
        if src.head is None:
            return p("merge.no-branch", f"no branch {branch} at {src.location}", "merge the default branch",
                     f"git ls-remote {src.location}")
        return []
    if g.toplevel(loc) != loc.resolve():
        return p("merge.missing-source", f"{loc} is inside {g.toplevel(loc)}, not a clone of its own",
                 "bootstrap the workspace", "scripts/bootstrap.sh")
    if not g.ok(loc, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
        return p("merge.no-branch", f"no local branch {branch}", "merge the branch every source publishes",
                 f"git -C {loc} branch -a")
    src.head = g.git(loc, "rev-parse", f"refs/heads/{branch}").strip()
    left = g.dirty(loc)
    if left:
        return p("merge.dirty", f"{len(left)} uncommitted or untracked files ({', '.join(left[:3])})",
                 "a source is frozen before its cut: open work is committed and pushed first (RFC 0017 §7 step 1); "
                 "files you did not create are another session's, ask its owner",
                 f"git -C {loc} status, or --source {src.name}=<bundle or URL of the pushed branch>")
    up, ahead = g.unpushed(loc, branch)
    if up is None or ahead:
        what = "has no upstream" if up is None else f"has {ahead} commits not on {up}"
        return p("merge.unpushed", f"{branch} {what}", "the cut records the pushed history only; push the open work "
                 "first, or fetch if the upstream moved", f"git -C {loc} push, or git -C {loc} fetch")
    return []


def check_into(into, srcs):
    if into is None:
        return
    into = into.resolve()
    for s in srcs:
        if s.local and into.is_relative_to(pathlib.Path(s.location).resolve()):
            raise Refused("merge.into-inside-source", f"{into} is inside {s.name}'s clone",
                          "the root is built outside every source clone, so no source is touched",
                          "--into <a new folder outside the workspace's clones>")
    if into.exists() and (not into.is_dir() or any(into.iterdir())):
        raise Refused("merge.into-exists", f"{into} exists and is not empty", "the merge builds a new repository and "
                      "never writes over a folder", "--into <a new or empty folder>")


def engine_of(choice):
    if choice == "auto":
        return "filter-repo" if g.has_filter_repo() else "fast-export"
    if choice == "filter-repo" and not g.has_filter_repo():
        raise Refused("merge.no-engine", "git filter-repo is not installed", "the fast-export engine needs only git",
                      "--engine fast-export, or install git-filter-repo")
    return choice


def steps(root, srcs, into, branch, engine):
    out = [f"git init -b main {into}"]
    for s in srcs:
        out.append(f"git clone --no-local --no-tags --single-branch --branch {branch} {s.location}")
        if s.landing:
            out.append(f"rewrite {s.name} under {s.landing}/ ({engine})")
        out.append(f"git fetch <rewritten {s.name}> {branch}:{REF}/{s.name}")
    out.append(f"git commit-tree (one merge commit, parents {', '.join(s.name for s in srcs)}; "
               f"Source-Head trailers) → {root} main")
    return out


def _rewrite(s, work, branch, engine):
    """Clone, rewrite and check one source; return the rewritten repository and fill s.report."""
    orig, rew = work / f"{s.name}.orig", work / f"{s.name}.work"
    s.head = g.clone(s.location, orig, branch)
    g.clone(orig, rew, branch)
    n = g.count(orig, "HEAD")
    if not s.landing:  # the root keeps this source's own history: nothing to rewrite
        out, new = rew, g.git(rew, "rev-parse", "HEAD").strip()
        cmap = [(c, c) for c in g.git(orig, "rev-list", "HEAD").split()]
    elif engine == "filter-repo":
        new, cmap = g.rewrite_filter_repo(rew, s.landing, branch)
        out = rew
    else:
        (work / f"{s.name}.marks").mkdir()
        out = work / f"{s.name}.git"
        new, cmap = g.rewrite_fast_export(rew, out, s.landing, branch, work / f"{s.name}.marks")
    s.report = {"name": s.name, "path": s.path, "from": s.location, "landing": s.landing, "head": s.head,
                "rewritten": new, "commits": n, "commits_rewritten": g.count(out, new),
                "trees_match": len(cmap) == n and g.trees_match(orig, out, cmap, s.landing),
                "lfs_pointers": g.lfs_pointers(orig, "HEAD"), "gitlinks": g.gitlinks(orig, "HEAD"),
                "pack_kib": g.pack_kib(orig), "commit_map": f".git/odw-merge/{s.name}.commit-map"}
    return out, cmap


def merge(root, srcs, into, branch, engine, work):
    into.mkdir(parents=True, exist_ok=True)
    g.git(into, "init", "-q", "-b", "main")
    maps = {}
    for s in srcs:
        repo, maps[s.name] = _rewrite(s, work, branch, engine)
        g.git(into, "fetch", "-q", "--no-tags", str(repo), f"+refs/heads/{branch}:{REF}/{s.name}")
    g.git(into, "read-tree", "--empty")
    for s in srcs:
        if s.landing:
            g.git(into, "read-tree", f"--prefix={s.landing}/", f"{REF}/{s.name}:{s.landing}")
        else:
            g.git(into, "read-tree", f"{REF}/{s.name}")
    tree = g.git(into, "write-tree").strip()
    parents = [x for s in srcs for x in ("-p", s.report["rewritten"])]
    head = g.git(into, "commit-tree", tree, *parents, inp=message(root, srcs, engine)).strip()
    g.git(into, "update-ref", "refs/heads/main", head)
    g.git(into, "reset", "-q", "--hard", "main")
    for s in srcs:
        g.git(into, "update-ref", "-d", f"{REF}/{s.name}")
        g.write_commit_map(into / s.report["commit_map"], maps[s.name])
    return head


def message(root, srcs, engine):
    names = ", ".join(s.name for s in srcs)
    rows = "".join(f"- {s.name} → {s.landing or '.'}/: {s.report['commits']} commits, head {s.head}\n" for s in srcs)
    trailers = "".join(f"Source-Head: {s.name} {s.head}\n" for s in srcs)
    return (f"{root}: join {names} with their history\n\nRFC 0017 §7 step 2. Each source's history is rewritten "
            f"under its folder (odapkit rename merge {root}, engine {engine}); its last commit is recorded below.\n\n"
            f"{rows}\n{trailers}")


def document(root, into, branch, engine, srcs, mode, head=None, problems=(), into_dir=None):
    reports = []
    for s in srcs:
        r = s.report or {"name": s.name, "path": s.path, "from": s.location, "landing": s.landing, "head": s.head,
                         "rewritten": None, "commits": None, "commits_rewritten": None, "trees_match": None,
                         "lfs_pointers": None, "gitlinks": None, "pack_kib": None, "commit_map": None}
        reports.append(r)
    warnings = []
    for r in reports:
        if r["lfs_pointers"]:
            warnings.append({"code": "merge.lfs", "source": r["name"], "message": f"{r['lfs_pointers']} Git LFS "
                             "pointers at the head: their objects live in the source's LFS store, not in git",
                             "fix": f"git lfs fetch --all in a {r['name']} clone, copy its .git/lfs/objects into "
                             "the root's .git/lfs/objects, then git lfs push --all from the root once its remote "
                             "exists"})
        if r["gitlinks"]:
            warnings.append({"code": "merge.gitlinks", "source": r["name"], "message": "submodules: "
                             + ", ".join(r["gitlinks"]), "fix": "rewrite .gitmodules paths under the landing folder"})
    checks, size = None, None
    if head:
        total = sum(r["commits"] for r in reports)
        root_commits = g.count(into_dir, "main")
        checks = {"source_commits": total, "root_commits": root_commits,
                  "commits_match": root_commits == total + 1 and all(r["commits"] == r["commits_rewritten"]
                                                                     for r in reports),
                  "trees_match": all(r["trees_match"] for r in reports)}
        size = {"sources_pack_kib": sum(r["pack_kib"] for r in reports), "root_kib": g.pack_kib(into_dir)}
    return {"schema": SCHEMA, "mode": mode, "root": root, "into": str(into) if into else None, "branch": branch,
            "engine": engine, "commit": head, "sources": reports, "checks": checks, "size": size,
            "problems": [p.doc() for p in problems], "warnings": warnings,
            "steps": steps(root, srcs, into or "<into>", branch, engine)}


def _text(doc):
    out = [f"{doc['mode']} {doc['root']} into {doc['into']} (engine {doc['engine']}, branch {doc['branch']})"]
    for s in doc["sources"]:
        extra = (f", {s['commits']} commits → {s['commits_rewritten']}, trees match {s['trees_match']}"
                 if s["rewritten"] else "")
        out.append(f"  {s['name']} → {s['landing'] or '.'}/ head {s['head']} from {s['from']}{extra}")
    out += [f"  step: {x}" for x in doc["steps"]] if doc["mode"] == "dry-run" else []
    out += [f"commit {doc['commit']}"] if doc["commit"] else []
    out += [f"checks {doc['checks']}", f"size {doc['size']}"] if doc["checks"] else []
    out += [f"problem {p['code']}: {p['message']}\n  fix: {p['fix']}" for p in doc["problems"]]
    out += [f"warning {w['code']} ({w['source']}): {w['message']}" for w in doc["warnings"]]
    return "\n".join(out)


def _parser():
    ap = argparse.ArgumentParser(
        prog="odapkit rename merge", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="odapkit rename merge: join a root's source repositories with their history (RFC 0017 §7 "
                    "step 2). Never pushes; never touches the source clones.",
        epilog="examples:\n  odapkit rename merge libs --map rename-map.tsv --dry-run\n"
               "  odapkit rename merge libs --map rename-map.tsv --into ../cut/libs --json\n"
               "  odapkit rename merge libs --map rename-map.tsv --into ../cut/libs "
               "--source kicad-lib=https://github.com/opendap-project/kicad-lib")
    ap.add_argument("root")
    ap.add_argument("--into", help="the new root repository (a new or empty folder); not needed with --dry-run")
    ap.add_argument("--workspace")
    ap.add_argument("--map", help="the rename map (default: $ODAP_RENAME_MAP)")
    ap.add_argument("--branch", default="main", help="the branch merged from every source (default: main)")
    ap.add_argument("--source", action="append", default=[], metavar="NAME=LOCATION",
                    help="clone a source from a bundle, URL or other clone instead of the workspace's")
    ap.add_argument("--landing", action="append", default=[], metavar="NAME=DIR",
                    help="where a source lands in the root, when the map does not say")
    ap.add_argument("--engine", choices=["auto", "filter-repo", "fast-export"], default="auto")
    ap.add_argument("--report", help="also write the JSON report to this file")
    ap.add_argument("--work", help="folder for the temporary clones (default: a new temporary folder)")
    ap.add_argument("--dry-run", action="store_true", help="list the sources, heads and steps; create nothing")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON (the default when stdout is not a terminal)")
    fmt.add_argument("--text", action="store_true")
    return ap


def _pairs(values):
    return dict(v.split("=", 1) for v in values if "=" in v)


def run(a):
    ws = pathlib.Path(a.workspace) if a.workspace else workspace.root()
    if ws is None:
        raise Refused("merge.no-workspace", "not inside a workspace (no repos.tsv)", "run it from the workspace",
                      "odapkit rename merge ROOT --workspace <workspace>", 2)
    map_path = a.map or os.environ.get("ODAP_RENAME_MAP")
    if not map_path or not pathlib.Path(map_path).is_file():
        raise Refused("merge.no-map", f"no rename map ({map_path or 'none given'})",
                      "pass the map with --map or set ODAP_RENAME_MAP",
                      f"odapkit rename merge {a.root} --map <rename-map.tsv>", 2)
    m = rename_map.load(ws, map_path, _pairs(a.landing))
    if a.root not in m.roots:
        raise Refused("merge.unknown-root", f"the map names no root {a.root!r}", f"roots: {', '.join(m.roots)}",
                      f"odapkit rename merge {m.roots[0]} --dry-run", 2)
    if not a.into and not a.dry_run:
        raise Refused("merge.no-into", "no --into folder", "the merge builds the root in a new folder you name",
                      f"odapkit rename merge {a.root} --into <new folder>, or --dry-run", 2)
    into = pathlib.Path(a.into).resolve() if a.into else None
    engine = engine_of(a.engine)
    srcs, problems = sources(m, a.root, ws, _pairs(a.source), a.branch)
    try:
        check_into(into, srcs)
    except Refused as e:
        if not a.dry_run:
            raise
        problems.append(e)
    if a.dry_run:
        return document(a.root, into, a.branch, engine, srcs, "dry-run", problems=problems), 1 if problems else 0
    if problems:
        raise problems[0]
    created = not into.exists()
    work = pathlib.Path(tempfile.mkdtemp(prefix="odw-merge-", dir=a.work))
    try:
        head = merge(a.root, srcs, into, a.branch, engine, work)
    except (g.GitError, ValueError) as e:
        if created:
            shutil.rmtree(into, ignore_errors=True)
        else:
            for p in into.iterdir():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        raise Refused("merge.git", str(e), "a git step failed; the new root was removed and no source changed",
                      f"odapkit rename merge {a.root} --dry-run --text") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)
    doc = document(a.root, into, a.branch, engine, srcs, "merge", head=head, into_dir=into)
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    (into / ".git/odw-merge/report.json").write_text(text)
    if a.report:
        pathlib.Path(a.report).write_text(text)
    good = doc["checks"]["commits_match"] and doc["checks"]["trees_match"]
    if not good:
        doc["problems"].append(Refused("merge.mismatch", "a rewritten history lost commits or changed a tree",
                                       "the root is kept for inspection; compare the commit maps",
                                       f"ls {into}/.git/odw-merge").doc())
    return doc, 0 if good else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    as_json = a.json or (not a.text and not sys.stdout.isatty())
    try:
        doc, code = run(a)
    except Refused as e:
        print(f"odapkit rename merge: {e.code}: {e.message}\n  hint: {e.hint}\n  fix: {e.fix}", file=sys.stderr)
        if as_json:
            print(json.dumps({"schema": "opendap.error/v1", "error": e.doc()}))
        return e.status
    except ValueError as e:
        print(f"odapkit rename merge: merge.bad-map: {e}\n  hint: the map needs old, new, kind and action columns\n"
              "  fix: head -1 <rename-map.tsv>", file=sys.stderr)
        return 2
    print(json.dumps(doc, ensure_ascii=False, indent=1) if as_json else _text(doc))
    return code


if __name__ == "__main__":
    sys.exit(main())
