"""odw rename: one root's cut from the rename map (RFC 0017 §7 steps 3 and 4).

Usage: odapkit rename merge ROOT --into DIR [...]   (step 2, the history merge: odw/rename_merge.py)
       odapkit rename config ROOT [...]             (step 4, the shared configuration: odw/rename_config.py)
       odapkit rename links [...]                   (after the cuts, the workspace-wide link check: odw/rename_links.py)
       odapkit rename (plan | apply | check) ROOT [--workspace DIR] [--map TSV] [--root-dir DIR] [--cut ROOTS]
       [--landing REPO=DIR] [--kind KINDS] [--structure-only] [--dry-run] [--json | --text]

plan (a dry run, the default) lists every move, delete, identifier rewrite and link or URL rewrite the root needs;
apply performs them in the merged root checkout with `git mv` and two local commits (moves, then references) and
never pushes; check reports the old names left (opendap.check-result/v1). Exit status: 0 done or nothing left,
1 refused or old names left, 2 usage. Spec: docs/specs/rename.md.
"""
import argparse
import dataclasses
import datetime as dt
import importlib
import json
import os
import pathlib
import subprocess
import sys

from odw import (audit, clikit, rename_generated, rename_idents as ri, rename_keys as rk, rename_map, rename_merge,
                 rename_rewrite as rw, workspace)

TOOL = "odapkit rename"

MAX_TEXT = 8 * 1024 * 1024
TMP = ".odw-rename-tmp"


class Refused(clikit.CliError):
    """A refused step: a stable code, a hint and a fix (odw.clikit)."""


@dataclasses.dataclass
class Plan:
    root: str
    cut: list
    view: str
    moves: list = dataclasses.field(default_factory=list)
    deletes: list = dataclasses.field(default_factory=list)
    done: list = dataclasses.field(default_factory=list)
    missing: list = dataclasses.field(default_factory=list)
    pending: list = dataclasses.field(default_factory=list)
    cross_root: list = dataclasses.field(default_factory=list)
    conflicts: list = dataclasses.field(default_factory=list)
    identifiers: list = dataclasses.field(default_factory=list)
    edits: list = dataclasses.field(default_factory=list)
    generated: list = dataclasses.field(default_factory=list)
    structure_only: bool = False
    texts: dict = dataclasses.field(default_factory=dict)  # new path -> new content (not reported)


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=audit.hook_env())
    if check and r.returncode:
        raise Refused("rename.git", f"git {' '.join(args[:2])} failed: {r.stderr.strip()}",
                      "the root checkout refused a git operation; nothing after it ran", f"git -C {cwd} status")
    return r.stdout


def view(m, root, ws, root_dir):
    """Path inside the root → file on disk: the merged root checkout when it exists, else the source
    repositories as they are, each under its landing folder."""
    if root_dir is not None and (root_dir / ".git").exists():
        return "root", {p: root_dir / p for p in audit.tracked(root_dir)}
    files = {}
    for s in m.sources_of(root):
        repo = ws / s.path
        if s.landing is None or not (repo / ".git").exists():
            continue
        for f in audit.tracked(repo):
            files[f"{s.landing}/{f}".strip("/") if s.landing else f] = repo / f
    return "sources", files


def _paths_from_rows(m, root, tracked=None):
    """Where a cut root's paths went, from its map rows (used for URLs into roots cut earlier). With `tracked`, the
    files of that root's checkout, a row whose source is still there and whose target is not has not landed (a root
    cut as structure only) and is left out."""
    def landed(frm, to, prefix=False):
        has = (lambda p: any(f.startswith(p + "/") for f in tracked)) if prefix else (lambda p: p in tracked)
        return tracked is None or has(to) or not has(frm)

    moves, prefixes = {}, []
    for r in m.rows:
        if r.action not in rename_map.MOVE_ACTIONS or ";" in r.new or not rename_map.is_path_row(m, r):
            continue
        src_root, frm = m.in_root(r.old)
        if src_root != root or frm is None or r.new.split("/")[0] != root:
            continue
        to = r.new.split("/", 1)[1] if "/" in r.new else ""
        if frm.endswith("/**") and to.endswith("/**"):
            if landed(frm[:-3], to[:-3], prefix=True):
                prefixes.append((frm[:-3], to[:-3]))
        elif "*" not in frm and landed(frm, to):
            moves[frm] = to
    return rw.Paths(moves, prefixes=prefixes)


def _rows(m, root, files, plan):
    lower = {p.lower(): p for p in files}
    targets, prefixes = {}, []
    for r in m.rows:
        if not rename_map.is_path_row(m, r):
            continue
        src_root, frm = m.in_root(r.old)
        news = [n for n in r.new.split(";") if n] if r.new else []
        new_root = news[0].split("/")[0] if news else None
        if root not in (src_root, new_root):
            continue
        item = {"row": r.line, "old": r.old, "new": r.new, "action": r.action}
        if r.action == "delete" and src_root == root:
            if frm is None:
                plan.pending.append({**item, "reason": "landing-unknown"})
            elif frm in files:
                plan.deletes.append({"row": r.line, "path": frm})
            continue
        if r.action not in rename_map.MOVE_ACTIONS or len(news) != 1:
            plan.pending.append({**item, "reason": f"action-{r.action}"})
            continue
        if src_root != new_root:
            plan.cross_root.append({"row": r.line, "direction": "out" if src_root == root else "in",
                                    "from": r.old, "to": news[0]})
            continue
        if frm is None:
            plan.pending.append({**item, "reason": "landing-unknown"})
            continue
        to = news[0].split("/", 1)[1] if "/" in news[0] else ""
        if "*" in frm:
            if not (frm.endswith("/**") and to.endswith("/**")) or "*" in frm[:-3] + to[:-3]:
                plan.pending.append({**item, "reason": "pattern"})
                continue
            a, b = frm[:-3], to[:-3]
            prefixes.append((a, b))
            pairs = [(p, b + p[len(a):]) for p in files if p.startswith(a + "/")]
            if not pairs and any(p.startswith(b + "/") for p in files):
                plan.done.append({"row": r.line, "from": frm, "to": to})
        else:
            pairs = [(frm, to)]
        for f, t in pairs:
            if f == t:
                continue
            if f in files:
                plan.moves.append({"row": r.line, "from": f, "to": t})
                targets.setdefault(t, []).append(r.line)
            elif t in files or lower.get(t.lower()) == t:
                plan.done.append({"row": r.line, "from": f, "to": t})
            elif "*" not in frm:
                plan.missing.append({"row": r.line, "from": f, "to": t})
    leaving = {mv["from"] for mv in plan.moves} | {d["path"] for d in plan.deletes}
    for t, rows in targets.items():
        clash = lower.get(t.lower())
        if len(rows) > 1:
            plan.conflicts.append({"path": t, "rows": rows, "reason": "several rows move files here"})
        elif clash is not None and clash not in leaving:
            plan.conflicts.append({"path": t, "rows": rows, "reason": f"{clash} exists and stays"})
    return prefixes


def _read_text(path):
    try:
        if path.stat().st_size > MAX_TEXT:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def build(m, root, ws, root_dir, cut, kinds=None, structure_only=False):
    """The plan. `structure_only`: each source keeps its paths under its landing folder, so no path row moves or
    deletes and no identifier row applies; rows into other roots are still listed, and URLs and links follow."""
    kind, files = view(m, root, ws, root_dir)
    plan = Plan(root, cut, kind, structure_only=structure_only)
    if structure_only:
        rows_only = Plan(root, cut, kind)
        _rows(m, root, files, rows_only)
        plan.cross_root, prefixes, pairs, keys, scan = rows_only.cross_root, [], [], [], []
    else:
        prefixes = _rows(m, root, files, plan)
        pairs, keys, scan = ri.collect(m, cut, kinds)
    moves = {mv["from"]: mv["to"] for mv in plan.moves}
    key_table = {o: n for o, n, _ in keys}
    rk.plan_moves(plan, files, moves, key_table, {o: line for o, _, line in keys})
    key_patterns = rk.patterns(key_table)
    own = rw.Paths(moves, deleted=[d["path"] for d in plan.deletes], prefixes=prefixes)
    cache = {root: own}

    seen_roots = {r: rename_merge.recorded(ws / "public" / r) for r in cut if r != root}

    def paths_of(r):
        if r not in cache:
            cache[r] = _paths_from_rows(m, r, seen_roots.get(r, (None,))[0])
        return cache[r]

    sources = {s.name: dataclasses.replace(s, landing=seen_roots.get(s.root, (None, {}))[1].get(s.name, s.landing))
               for s in m.sources.values() if s.root in cut and s.kind != "root"}
    idents = ri.Rewriter(pairs)
    values = {v: (i, st) for i, st in scan for v in i.old}
    scan_pattern = rw.identifier_pattern([(v, v) for v in values])
    seen = {}
    deleted = {d["path"] for d in plan.deletes}
    generated = rename_generated.outputs(files)
    for src in sorted(files):
        if src in deleted or src in generated or ri.untouched(src):
            continue
        text = _read_text(files[src])
        if text is None or ri.immutable(src, text):
            continue
        # Tests keep their URLs and links as test data; the identifiers they drive follow (HL-109 case 4).
        frozen = rw.frozen(src)
        dst = moves.get(src, src)
        for n, v in rw.count(text, scan_pattern) if scan_pattern else []:
            seen.setdefault(values[v][0].row.line, []).append(f"{src}:{n}")
        new, edits = (text, []) if frozen else rw.urls(text, sources, root, paths_of, src)
        if src.endswith(".md") and not frozen:
            new, e2 = rw.links(new, src, dst, own)
            new, e3 = rw.to_relative(new, root, dst)
            edits += e2 + e3
        new, e4 = idents.rewrite(dst, new)
        new, e5 = rk.rewrite(new, key_patterns, key_table)
        edits += e4 + e5
        for e in edits:
            plan.edits.append({"path": dst, "source": src, "kind": e.kind, "line": e.line, "old": e.old, "new": e.new})
        if new != text:
            plan.texts[dst] = new

    def text_of(p):
        dst = moves.get(p, p)
        return plan.texts[dst] if dst in plan.texts else _read_text(files[p])

    for path, src, text in rename_generated.render(generated, text_of, lambda p: moves.get(p, p)):
        plan.generated.append({"path": path, "source": src})
        plan.texts[path] = text
    for i, status in scan:
        where = seen.get(i.row.line)
        if not where:
            continue
        item = {"row": i.row.line, "old": i.row.old, "new": i.row.new, "owner": i.owner, "status": status,
                "occurrences": len(where), "first": where[0]}
        if i.reason:
            item["reason"] = i.reason
        plan.identifiers.append(item)
    return plan


def document(plan, mode="plan", **extra):
    keys = ["moves", "deletes", "done", "missing", "pending", "cross_root", "conflicts", "identifiers", "edits",
            "generated"]
    summary = {k: len(getattr(plan, k)) for k in keys}
    for k in ("link", "url", "identifier"):
        summary[f"edits_{k}"] = sum(1 for e in plan.edits if e["kind"] == k)
    for s in ("apply", "later", "manual"):
        summary[f"identifiers_{s}"] = sum(1 for i in plan.identifiers if i["status"] == s)
    summary["files_rewritten"] = len(plan.texts)
    return {"schema": "opendap.rename-plan/v1", "mode": mode, "root": plan.root, "cut": plan.cut, "view": plan.view,
            "structure_only": plan.structure_only, "summary": summary, **{k: getattr(plan, k) for k in keys}, **extra}


def _inside(root_dir, rel):
    p = (root_dir / rel).resolve()
    if not p.is_relative_to(root_dir.resolve()):
        raise Refused("rename.outside", f"{rel} is outside the root", "the map names a path outside the root",
                      "fix the row in the rename map")
    return p


def _prune(root_dir, rels):
    for rel in sorted({str(pathlib.PurePosixPath(r).parent) for r in rels}, key=len, reverse=True):
        d = root_dir / rel
        while d != root_dir and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent


def apply(m, root, ws, root_dir, cut, kinds=None, structure_only=False):
    if root_dir is None or not (root_dir / ".git").exists():
        raise Refused("rename.no-root", f"no root checkout at {root_dir}",
                      "apply runs in the root repository after its sources are merged into it with history "
                      "(RFC 0017 §7 step 2); plan works on the sources before that",
                      f"odapkit rename plan {root} --root-dir <merged {root} checkout>")
    top = pathlib.Path(_git(root_dir, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != root_dir.resolve():
        raise Refused("rename.not-toplevel", f"{root_dir} is inside {top}, not a root checkout",
                      "pass the root repository's own folder", f"odapkit rename apply {root} --root-dir {top}")
    landed = [s.landing for s in m.sources_of(root) if s.landing]
    if landed and not any((root_dir / d).is_dir() for d in landed):
        raise Refused("rename.not-merged", f"none of {', '.join(landed)} is in {root_dir}",
                      "the sources are not merged into the root yet (RFC 0017 §7 step 2)",
                      f"ls {root_dir}")
    dirty = _git(root_dir, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    if dirty:
        raise Refused("rename.dirty", f"{len(dirty)} uncommitted or untracked files: "
                      + ", ".join(l[3:] for l in dirty[:5]),
                      "apply never runs over uncommitted work, yours or someone else's; commit, or ask its owner",
                      f"git -C {root_dir} status")
    plan = build(m, root, ws, root_dir, cut, kinds, structure_only)
    if plan.conflicts:
        raise Refused("rename.conflict", f"{len(plan.conflicts)} conflicting targets, first {plan.conflicts[0]['path']}",
                      "two rows move files to one path, or a target exists; fix the rename map",
                      f"odapkit rename plan {root} --root-dir {root_dir} --json")
    base, commits = _git(root_dir, "rev-parse", "HEAD").strip(), []
    if plan.moves or plan.deletes:
        tmp = [(mv, f"{TMP}/{n}") for n, mv in enumerate(plan.moves)]
        for mv, t in tmp:  # two steps, so case-only renames work on case-insensitive file systems
            _inside(root_dir, t).parent.mkdir(parents=True, exist_ok=True)
            _git(root_dir, "mv", "--", _rel(mv["from"], root_dir), t)
        for d in plan.deletes:
            _git(root_dir, "rm", "-q", "--", _rel(d["path"], root_dir))
        _prune(root_dir, [mv["from"] for mv in plan.moves] + [d["path"] for d in plan.deletes])
        for mv, t in tmp:
            _inside(root_dir, mv["to"]).parent.mkdir(parents=True, exist_ok=True)
            _git(root_dir, "mv", "--", t, mv["to"])
        _prune(root_dir, [t for _, t in tmp])
        _git(root_dir, "commit", "-q", "-m", f"{root}: moves and deletes from the rename map\n\n"
             f"{len(plan.moves)} moves, {len(plan.deletes)} deletes (odapkit rename apply {root}).")
        commits.append(_git(root_dir, "rev-parse", "HEAD").strip())
    if plan.texts:
        for rel, text in plan.texts.items():
            _inside(root_dir, rel).write_text(text)
        _git(root_dir, "add", "--", *plan.texts)
        _git(root_dir, "commit", "-q", "-m", f"{root}: references from the rename map\n\n"
             f"{len(plan.edits)} link, URL and identifier rewrites and {len(plan.generated)} generated files "
             f"rendered again, in {len(plan.texts)} files "
             f"(odapkit rename apply {root}).")
        commits.append(_git(root_dir, "rev-parse", "HEAD").strip())
    return document(plan, "apply", base=base, commits=commits)


def _rel(p, root_dir):
    _inside(root_dir, p)
    return p


def check(m, root, ws, root_dir, cut, kinds=None, structure_only=False):
    plan = build(m, root, ws, root_dir, cut, kinds, structure_only)
    res = []

    def add(id_, verdict, message, path=None, line=None, **kw):
        r = {"id": id_, "verdict": verdict, "message": message, **kw}
        if path:
            r["location"] = {"path": path, **({"line": line} if line else {})}
        res.append(r)

    for mv in plan.moves:
        add(f"rename.old-path:{mv['from']}", "fail", f"still at its old path; goes to {mv['to']}", mv["from"])
    for d in plan.deletes:
        add(f"rename.old-path:{d['path']}", "fail", "still present; the map deletes it", d["path"])
    for e in plan.edits:
        add(f"rename.old-reference:{e['source']}:{e['line']}", "fail", f"{e['kind']} {e['old']} -> {e['new']}",
            e["source"], e["line"])
    for i in plan.identifiers:
        if i["status"] == "manual":
            add(f"rename.manual-identifier:{i['old']}", "warn",
                f"{i['occurrences']} occurrences need a hand ({i.get('reason')}): {i['old']} -> {i['new']}",
                i["first"].rsplit(":", 1)[0], int(i["first"].rsplit(":", 1)[1]), value=i["occurrences"])
    for c in plan.conflicts:
        add(f"rename.conflict:{c['path']}", "fail", c["reason"], c["path"])
    for x in plan.generated:
        add(f"rename.generated:{x['path']}", "fail", f"not rendered from {x['source']}", x["path"])
    for x in plan.missing:
        add(f"rename.missing:{x['from']}", "warn", f"the map moves {x['from']}, which does not exist", x["from"])
    add("rename.pending", "warn" if plan.pending else "pass", f"{len(plan.pending)} rows need their own tool or a hand "
        "(convert, split, merge, generate, extract)", value=len(plan.pending))
    add("rename.cross-root", "skip" if plan.cross_root else "pass",
        f"{len(plan.cross_root)} rows cross into or out of another root; they move at that root's cut",
        value=len(plan.cross_root))
    commit = _git(root_dir, "rev-parse", "HEAD", check=False).strip() if plan.view == "root" else ""
    subject = {"repo": root, **({"commit": commit} if commit else {})}
    return {"schema": "opendap.check-result/v1", "tool": {"name": "odapkit rename"}, "subject": subject,
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "results": res}


def _text_plan(doc):
    out = [f"{doc['mode']} {doc['root']} (view: {doc['view']}; cut: {', '.join(doc['cut'])})"]
    out += [f"  {k} = {v}" for k, v in doc["summary"].items()]
    out += [f"move {x['from']} -> {x['to']}" for x in doc["moves"]]
    out += [f"delete {x['path']}" for x in doc["deletes"]]
    out += [f"conflict {x['path']}: {x['reason']}" for x in doc["conflicts"]]
    out += [f"missing {x['from']} (row {x['row']})" for x in doc["missing"]]
    out += [f"cross-root {x['direction']} {x['from']} -> {x['to']}" for x in doc["cross_root"]]
    out += [f"pending {x['action']} {x['old']} -> {x['new']} ({x['reason']})" for x in doc["pending"]]
    out += [f"identifier {x['old']} -> {x['new']}: {x['status']}"
            f"{' (' + x['reason'] + ')' if 'reason' in x else ''}, {x['occurrences']} occurrences, first {x['first']}"
            for x in doc["identifiers"]]
    out += [f"{x['path']}:{x['line']} {x['kind']} {x['old']} -> {x['new']}" for x in doc["edits"]]
    out += [f"commit {c}" for c in doc.get("commits", [])]
    return "\n".join(out)


def _parser():
    ap = clikit.Parser(
        prog=TOOL, summary=clikit.summary("rename"), description="One root's cut from the rename map (moves, links, "
        "identifiers).", formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  odapkit rename merge libs --map rename-map.tsv --into ../libs   (step 2: history merge)\n"
               "  odapkit rename plan libs --map rename-map.tsv\n"
               "  odapkit rename plan libs --map rename-map.tsv --json\n"
               "  odapkit rename apply libs --map rename-map.tsv --root-dir ../libs\n"
               "  odapkit rename check libs --map rename-map.tsv --root-dir ../libs --json")
    ap.add_argument("verb", choices=["plan", "apply", "check"], help="plan, apply or check; `merge` and `config` "
                    "have their own options (odapkit rename merge --help, odapkit rename config --help)")
    ap.add_argument("root")
    ap.add_argument("--workspace")
    ap.add_argument("--map", help="the rename map (default: $ODAP_RENAME_MAP)")
    ap.add_argument("--root-dir", help="the root repository checkout (default: <workspace>/public/<root>)")
    ap.add_argument("--cut", help="roots already cut, comma-separated (default: the RFC 0017 order up to ROOT)")
    ap.add_argument("--landing", action="append", default=[], metavar="REPO=DIR",
                    help="where a source repository lands in the root, when the map does not say")
    ap.add_argument("--kind", help="only identifier rows of these kinds, comma-separated (`mfr`, `env`); path "
                    "rows, links and URLs still apply")
    ap.add_argument("--structure-only", action="store_true", help="each source keeps its paths under its landing "
                    "folder: no path row moves or deletes, no identifier row applies; URLs and links follow")
    ap.add_argument("--dry-run", action="store_true", help="apply: print the plan and change nothing")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON (the default when stdout is not a terminal)")
    fmt.add_argument("--text", action="store_true")
    return ap


def _emit(doc, as_json, text):
    print(json.dumps(doc, ensure_ascii=False, indent=1) if as_json else text)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sub = {"merge": "rename_merge", "config": "rename_config", "links": "rename_links"}.get(argv[0] if argv else "")
    if sub:  # verbs with options of their own
        return importlib.import_module(f"odw.{sub}").main(argv[1:])
    return clikit.main(TOOL, _main, argv)


def _main(argv):
    args = _parser().parse_args(argv)
    as_json = args.json or (not args.text and not sys.stdout.isatty())
    try:
        ws = pathlib.Path(args.workspace) if args.workspace else workspace.root()
        if ws is None:
            raise Refused("rename.no-workspace", "not inside a workspace (no repos.tsv)", "run it from the workspace",
                          "odapkit rename plan ROOT --workspace <workspace>", 2)
        map_path = args.map or os.environ.get("ODAP_RENAME_MAP")
        if not map_path or not pathlib.Path(map_path).is_file():
            raise Refused("rename.no-map", f"no rename map ({map_path or 'none given'})",
                          "pass the map with --map or set ODAP_RENAME_MAP",
                          f"odapkit rename {args.verb} {args.root} --map <rename-map.tsv>", 2)
        landings = dict(x.split("=", 1) for x in args.landing if "=" in x)
        m = rename_map.load(ws, map_path, landings)
        if args.root not in m.roots:
            raise Refused("rename.unknown-root", f"the map names no root {args.root!r}",
                          f"roots in the map: {', '.join(m.roots)}", f"odapkit rename plan {m.roots[0]}", 2)
        root_dir = pathlib.Path(args.root_dir) if args.root_dir else ws / "public" / args.root
        cut = m.cut(args.root, [c for c in (args.cut or "").split(",") if c], ws)
        kinds = {k for k in (args.kind or "").split(",") if k}
        if args.verb == "check":
            doc = check(m, args.root, ws, root_dir, cut, kinds, args.structure_only)
            fails = [r for r in doc["results"] if r["verdict"] == "fail"]
            _emit(doc, as_json, "\n".join(f"{r['verdict']} {r['id']}: {r['message']}" for r in doc["results"]
                                          if r["verdict"] != "pass"))
            return 1 if fails else 0
        if args.verb == "plan" or args.dry_run:
            doc = document(build(m, args.root, ws, root_dir, cut, kinds, args.structure_only))
        else:
            doc = apply(m, args.root, ws, root_dir, cut, kinds, args.structure_only)
        _emit(doc, as_json, _text_plan(doc))
        return 0
    except Refused as e:
        return clikit.report(TOOL, e, as_json)
    except ValueError as e:
        return clikit.report(TOOL, Refused("rename.bad-map", str(e), "the map needs old, new, kind and action columns",
                                           "head -1 <rename-map.tsv>", 2), as_json)


if __name__ == "__main__":
    sys.exit(main())
