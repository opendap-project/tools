"""odw docs rfc-status: which affected repositories recorded each RFC's outcome.

An RFC's `- **Affected repositories:**` header item (inline, or a bullet list under it) lists repository names
separated by commas, semicolons or "and"; a note in parentheses after a name is ignored, except `(formerly X)`,
`(from X)` and `(becomes Y)`, which declare a rename that every RFC's list may use. A name resolves through the
workspace's repos.tsv files (`workspace` is the workspace root). A repository recorded the outcome when a Markdown
or TOML file under its `docs/requirements/`, `docs/adr/` or `docs/specs/` cites the RFC by number (`RFC 0004`) or by its GitHub URL.
Spec: docs/specs/docs-gates.md.
"""
import dataclasses
import datetime as dt
import json
import pathlib
import re

FIELD = re.compile(r"^- \*\*Affected repositories:\*\* ?(.*)$")
CONTINUATION = re.compile(r"^\s+(?:- )?(\S.*)$")
RENAME = re.compile(r"^(formerly|from|becomes)\s+`?([A-Za-z0-9._-]+)`?")
NUMBER = re.compile(r"(?:^|/)(\d{4})-[^/]*\.md$")
FAILING_STATUSES = ("accepted", "implemented")
RECORD_DIRS = ("requirements", "adr", "specs")


@dataclasses.dataclass
class Entry:
    name: str
    renamed: tuple | None = None  # (old name, new name)


def _split_top(text):
    """Split on , ; and ' and ' outside parentheses and code spans."""
    parts, buf, depth, code, i = [], "", 0, False, 0
    while i < len(text):
        c = text[i]
        if c == "`":
            code = not code
        elif not code and c == "(":
            depth += 1
        elif not code and c == ")":
            depth = max(depth - 1, 0)
        if not code and depth == 0 and (c in ",;" or text.startswith(" and ", i)):
            parts.append(buf)
            buf = ""
            i += 1 if c in ",;" else 5
            continue
        buf += c
        i += 1
    return [p for p in (s.strip() for s in parts + [buf]) if p]


def _notes(segment):
    """(text outside parentheses, [top-level notes]), nested parentheses kept inside their note."""
    outside, notes, depth = "", [], 0
    for c in segment:
        if c == "(":
            depth += 1
            if depth == 1:
                notes.append("")
                continue
        elif c == ")" and depth:
            depth -= 1
            if depth == 0:
                continue
        if depth:
            notes[-1] += c
        else:
            outside += c
    return outside, notes


def _entry(segment):
    name, notes = _notes(segment)
    name = name.rsplit(":", 1)[-1] if ":" in name else name
    name = name.replace("`", "").strip().rstrip(".;").strip()
    renamed = None
    for note in notes:
        m = RENAME.match(note.strip())
        if m:
            renamed = (name, m.group(2)) if m.group(1) == "becomes" else (m.group(2), name)
    return Entry(name, renamed) if name else None


def parse_affected(text):
    """The entries of the Affected repositories item, or None when the RFC has no such item."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = FIELD.match(line)
        if not m:
            continue
        chunks = [m.group(1)]
        for nxt in lines[i + 1:]:
            c = CONTINUATION.match(nxt)
            if not c:
                break
            chunks.append(c.group(1))
        return [e for e in map(_entry, _split_top(", ".join(x for x in chunks if x.strip()))) if e]
    return None


def _repos(ws):
    """{repository name: workspace-relative path} from every repos.tsv, plus `workspace` for the root."""
    out = {"workspace": "."}
    for f in [ws / "repos.tsv", *sorted(ws.glob("*/*/repos.tsv"))]:
        if f.is_file():
            for row in f.read_text().splitlines()[1:]:
                if "\t" in row:
                    path, repo = row.split("\t")[:2]
                    out[repo.split("/")[-1]] = path
    return out


def _rfc_dir(ws, repos):
    d = ws / repos.get("rfcs", "public/rfcs") / "text"
    return d if d.is_dir() else None


def _cites(number):
    n = number.lstrip("0") or "0"
    return re.compile(
        rf"\bRFCs?[\s-]?(?:0*\d+(?:,\s*|\s+and\s+|\s*/\s*))*0*{n}\b"
        rf"|opendap-project/rfcs/(?:blob|tree)/[^/\s]+/text/{number}-")


def _citations(repo_dir, rx):
    found = []
    for kind in RECORD_DIRS:
        for f in sorted((repo_dir / "docs" / kind).rglob("*")):
            if f.suffix in (".md", ".toml") and f.is_file() and rx.search(f.read_text(errors="replace")):
                found.append(f.relative_to(repo_dir).as_posix())
    return found


def _status_word(text):
    for line in text.splitlines():
        m = re.match(r"^- \*\*Status:\*\* ?(\w*)", line)
        if m:
            return m.group(1).lower()
    return ""


def status(ws):
    """The application report: one entry per RFC, one row per affected repository."""
    ws = pathlib.Path(ws)
    repos = _repos(ws)
    d = _rfc_dir(ws, repos)
    if d is None:
        return None
    texts = {}
    for f in sorted(d.glob("*.md")):
        m = NUMBER.search(f.as_posix())
        if m:
            texts[m.group(1)] = (f, f.read_text(errors="replace"))
    parsed = {n: parse_affected(t) for n, (_, t) in texts.items()}
    renames = {e.renamed[0]: e.renamed[1] for es in parsed.values() for e in es or [] if e.renamed}
    rfcs, failing, ready = [], [], []
    for number, (f, text) in texts.items():
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), f.stem)
        word = _status_word(text)
        rx, rows = _cites(number), []
        for e in parsed[number] or []:
            target = e.name if e.name in repos else renames.get(e.name)
            row = {"repository": e.name, "resolved_as": target or "", "path": repos.get(target, ""), "citations": []}
            if target not in repos:
                row["state"] = "unresolved"
            elif not (ws / row["path"]).is_dir():
                row["state"] = "absent"
            else:
                row["citations"] = _citations(ws / row["path"], rx)
                row["state"] = "recorded" if row["citations"] else "missing"
            rows.append(row)
        declared = parsed[number] is not None
        complete = declared and bool(rows) and all(r["state"] == "recorded" for r in rows)
        open_rows = (not declared) or any(r["state"] in ("missing", "unresolved") for r in rows)
        if word in FAILING_STATUSES and open_rows:
            failing.append(number)
        if word == "accepted" and complete:
            ready.append(number)
        rfcs.append({"number": number, "title": title, "status": word, "affected_declared": declared,
                     "complete": complete, "rows": rows})
    return {"rfcs": rfcs, "failing": failing, "ready_to_implement": ready}


def _v(v):
    return ("true" if v else "false") if isinstance(v, bool) else json.dumps(v, ensure_ascii=False)


def write(report, out, date=None):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lines = ["# @generated by odapkit docs rfc-status: one table per RFC, one row per affected repository. Do not edit.",
             f'date = "{date or dt.date.today().isoformat()}"', f"failing = {_v(report['failing'])}",
             f"ready_to_implement = {_v(report['ready_to_implement'])}"]
    for r in report["rfcs"]:
        lines += ["", "[[rfc]]"] + [f"{k} = {_v(r[k])}" for k in ("number", "title", "status", "affected_declared", "complete")]
        for row in r["rows"]:
            lines += ["", "[[rfc.row]]"] + [f"{k} = {_v(v)}" for k, v in row.items()]
    (out / "rfc-application.toml").write_text("\n".join(lines) + "\n")


def render(report):
    out = []
    for r in report["rfcs"]:
        mark = "complete" if r["complete"] else ("no Affected repositories item" if not r["affected_declared"] else "open")
        out.append(f"RFC {r['number']} ({r['status'] or 'no status'}): {mark}")
        for row in r["rows"]:
            alias = f" -> {row['resolved_as']}" if row["resolved_as"] and row["resolved_as"] != row["repository"] else ""
            cited = f" ({', '.join(row['citations'])})" if row["citations"] else ""
            out.append(f"  {row['state']:<10} {row['repository']}{alias}{cited}")
    out.append(f"failing: {' '.join(report['failing']) or 'none'}")
    out.append(f"ready to mark Implemented: {' '.join(report['ready_to_implement']) or 'none'}")
    return "\n".join(out)
