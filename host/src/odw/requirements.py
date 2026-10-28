"""odw req: a component's requirements as data (opendap.requirements/v1) and the README rendered from them.

Usage:
    odapkit req import README.md [--write [--dry-run]]   Markdown tables → requirements.toml (stdout, or beside it)
    odapkit req render requirements.toml [--check | --dry-run]   requirements.toml → README.md beside it, or check
                                                  it is current; --dry-run says whether it would change
    odapkit req index [--workspace DIR] [--json]  every requirement of every repository (README or TOML);
                                                  --json prints opendap.requirements-index/v1
    odapkit req prefixes [--workspace DIR]        every id prefix is registered in an id-prefixes.tsv for its folder
    odapkit req refs [--workspace DIR] [--strict] citations of unknown ids (error) and withdrawn or moved ids (warning)

The document is a list of blocks: Markdown prose kept as written, and requirement tables
(`| ID | Requirement | Priority | Status |`) as records with id, text, priority (must, should), status (confirmed,
draft, withdrawn, moved) and an optional note (the status cell's parenthesis). Rendering a parsed README gives it
back byte for byte. Exit status: 0 ok, 1 stale or invalid, 2 usage. Spec: docs/specs/requirements-data.md.
"""
import json
import os
import pathlib
import re
import sys
import tomllib

from odw import clikit, schemas, workspace

TOOL = "odapkit req"

HEADER = "| ID | Requirement | Priority | Status |"
RULE = "|---|---|---|---|"
STATUS = re.compile(r"^(Confirmed|Draft|Withdrawn|Moved)(?: \((.+)\))?$")


def _cells(line):
    inner = line.strip()[1:-1]
    parts, cur, i = [], "", 0
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner) and inner[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if inner[i] == "|":
            parts.append(cur)
            cur = ""
        else:
            cur += inner[i]
        i += 1
    parts.append(cur)
    return parts


def _row(line):
    cells = _cells(line)
    if len(cells) != 4 or any(not (c.startswith(" ") and c.endswith(" ")) for c in cells):
        return None
    rid, text, prio, status = (c[1:-1] for c in cells)
    m = STATUS.match(status)
    if not m:
        return {"id": rid, "text": text, "priority": prio, "status": status}
    r = {"id": rid, "text": text, "priority": prio, "status": m.group(1).lower()}
    if m.group(2):
        r["note"] = m.group(2)
    return r


def parse(text):
    lines = text.split("\n")
    blocks, prose, i = [], [], 0
    while i < len(lines):
        if lines[i] == HEADER and i + 1 < len(lines) and lines[i + 1] == RULE:
            rows, j = [], i + 2
            while j < len(lines) and lines[j].startswith("| ") and (row := _row(lines[j])) is not None:
                rows.append(row)
                j += 1
            if rows:
                blocks.append({"markdown": "\n".join(prose)})
                prose = []
                blocks.append({"requirement": rows})
                i = j
                continue
        prose.append(lines[i])
        i += 1
    blocks.append({"markdown": "\n".join(prose)})
    return {"schema": "opendap.requirements/v1", "block": blocks}


def _status(r):
    s = r["status"].capitalize() if r["status"] in ("confirmed", "draft", "withdrawn", "moved") else r["status"]
    return f"{s} ({r['note']})" if r.get("note") else s


def render(doc):
    parts = []
    for b in doc["block"]:
        if "markdown" in b:
            parts.append(b["markdown"])
        else:
            rows = [HEADER, RULE] + [
                f"| {r['id']} | {r['text'].replace('|', chr(92) + '|')} | {r['priority']} | {_status(r)} |"
                for r in b["requirement"]]
            parts.append("\n".join(rows))
    return "\n".join(parts)


def rows(doc):
    return [r for b in doc["block"] if "requirement" in b for r in b["requirement"]]


def problems(doc):
    seen, out = set(), []
    for r in rows(doc):
        if r["id"] in seen:
            out.append(f"duplicate id {r['id']}")
        seen.add(r["id"])
    return out


def dumps(doc):
    out = [f'schema = "{doc["schema"]}"']
    for b in doc["block"]:
        out.append("\n[[block]]")
        if "markdown" in b:
            out.append("markdown = " + json.dumps(b["markdown"], ensure_ascii=False))
        else:
            for r in b["requirement"]:
                out.append("[[block.requirement]]")
                out += [f"{k} = {json.dumps(r[k], ensure_ascii=False)}" for k in ("id", "text", "priority", "status", "note") if k in r]
    return "\n".join(out) + "\n"


def load(path):
    return tomllib.loads(pathlib.Path(path).read_text())


def is_current(toml_path, readme_path):
    readme = pathlib.Path(readme_path)
    return readme.is_file() and readme.read_text() == render(load(toml_path))


def index(ws):
    """Every requirement of every repository listed in a repos.tsv, skipping nested repositories (submodules)."""
    from odw import audit
    ws = pathlib.Path(ws)
    out = []
    for repo in audit.repositories(ws):
        root = ws / repo
        for base in sorted(root.glob("**/docs/requirements")):
            inner = base.relative_to(root).parts
            if any((root.joinpath(*inner[:i]) / ".git").exists() for i in range(1, len(inner))):
                continue
            src = base / "requirements.toml"
            doc = load(src) if src.is_file() else parse((base / "README.md").read_text()) if (base / "README.md").is_file() else None
            if doc is None:
                continue
            out += [{**r, "file": str(base.relative_to(ws))} for r in rows(doc)]
    return out


REF = re.compile(r"\b([A-Z][A-Z0-9]*)-([0-9]{3})\b")
REF_SUFFIXES = {".md", ".rs", ".py", ".toml", ".slint", ".sh", ".yaml", ".yml"}
SKIP_DIRS = {".git", "target", "node_modules", ".venv", "__pycache__"}


def registry(ws):
    """prefix -> set of requirement folders allowed to define it, from every id-prefixes.tsv (root and overlays)."""
    ws = pathlib.Path(ws)
    reg = {}
    for f in [ws / "id-prefixes.tsv", *sorted(ws.glob("*/*/id-prefixes.tsv"))]:
        if not f.is_file():
            continue
        for line in f.read_text().splitlines()[1:]:
            if line.strip():
                prefix, path = line.split("\t")[:2]
                reg.setdefault(prefix, set()).add(path)
    return reg


def check_prefixes(ws):
    reg, out, used = registry(ws), [], {}
    for r in index(ws):
        used.setdefault(r["id"].split("-")[0], set()).add(r["file"])
    for prefix in sorted(used):
        for f in sorted(used[prefix]):
            if prefix not in reg:
                out.append(f"{prefix}: used in {f} but not in any id-prefixes.tsv")
            elif f not in reg[prefix]:
                out.append(f"{prefix}: used in {f}, registered for {', '.join(sorted(reg[prefix]))}")
    return out


def check_refs(ws):
    """(errors, warnings): citations of unknown requirement ids, and of withdrawn or moved ones."""
    from odw import audit
    ws = pathlib.Path(ws)
    reg = registry(ws)
    known = {r["id"]: r["status"] for r in index(ws)}
    errors, warnings = [], []
    for repo in audit.repositories(ws):
        root = ws / repo
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".noindex")
                                 and not (pathlib.Path(dirpath, d) / ".git").exists())
            for name in sorted(files):
                path = pathlib.Path(dirpath, name)
                if path.suffix not in REF_SUFFIXES or path.parent.name == "requirements" and path.parent.parent.name == "docs":
                    continue
                rel = path.relative_to(ws)
                for rid in sorted({m.group(0) for m in REF.finditer(path.read_text(errors="replace")) if m.group(1) in reg}):
                    if rid not in known:
                        errors.append(f"{rel}: unknown requirement {rid}")
                    elif known[rid] in ("withdrawn", "moved"):
                        warnings.append(f"{rel}: cites {rid}, which is {known[rid]}")
    return errors, warnings


def main(argv=None):
    return clikit.main(TOOL, _main, argv)


def _parser():
    ap = clikit.Parser(prog=TOOL, summary=clikit.summary("req"), description="Requirements as data.",
                       examples=["odapkit req render docs/requirements/requirements.toml --check",
                                 "odapkit req index --json", "odapkit req refs --strict"])
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=clikit.Parser)
    im = sub.add_parser("import")
    im.add_argument("readme")
    im.add_argument("--write", action="store_true")
    im.add_argument("--dry-run", action="store_true", help="with --write: name the file and write nothing")
    rd = sub.add_parser("render")
    rd.add_argument("toml")
    mode = rd.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="say whether README.md would change; write nothing")
    ix = sub.add_parser("index")
    ix.add_argument("--workspace")
    ix.add_argument("--json", action="store_true")
    pf = sub.add_parser("prefixes")
    pf.add_argument("--workspace")
    rf = sub.add_parser("refs")
    rf.add_argument("--workspace")
    rf.add_argument("--strict", action="store_true", help="warnings fail too")
    return ap


def _main(argv):
    args = _parser().parse_args(argv)
    if args.cmd == "import":
        md = pathlib.Path(args.readme)
        doc = parse(md.read_text())
        errs = schemas.errors(doc, "requirements") + problems(doc)
        for e in errs:
            print(f"{md}: {e}", file=sys.stderr)
        if args.write and args.dry_run:
            print(f"{TOOL}: would write {md.parent / 'requirements.toml'}", file=sys.stderr)
        elif args.write:
            (md.parent / "requirements.toml").write_text(dumps(doc))
        else:
            print(dumps(doc), end="")
        return 1 if errs else 0
    if args.cmd == "render":
        src = pathlib.Path(args.toml)
        doc = load(src)
        errs = schemas.errors(doc, "requirements") + problems(doc)
        for e in errs:
            print(f"{src}: {e}", file=sys.stderr)
        readme = src.parent / "README.md"
        if args.check:
            if errs or not is_current(src, readme):
                if not errs:
                    print(f"{readme} is not current: run odapkit req render {src}", file=sys.stderr)
                return 1
            return 0
        if args.dry_run:
            state = "unchanged" if is_current(src, readme) else "changed"
            print(f"{TOOL}: would write {readme} ({state})", file=sys.stderr)
            return 1 if errs else 0
        readme.write_text(render(doc))
        return 1 if errs else 0
    ws = pathlib.Path(args.workspace) if args.workspace else workspace.root()
    if ws is None:
        raise clikit.no_workspace(f"{TOOL} {args.cmd}")
    if args.cmd == "prefixes":
        problems_ = check_prefixes(ws)
        for p in problems_:
            print(p)
        return 1 if problems_ else 0
    if args.cmd == "refs":
        errors, warnings = check_refs(ws)
        for e in errors:
            print(f"error: {e}")
        for w in warnings:
            print(f"warning: {w}")
        return 1 if errors or (args.strict and warnings) else 0
    idx = index(ws)
    if args.json:
        print(json.dumps({"schema": "opendap.requirements-index/v1", "requirements": idx}, ensure_ascii=False, indent=1))
    else:
        for r in idx:
            print(f"{r['id']:10} {r['priority']:6} {r['status']:9} {r['file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
