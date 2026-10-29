"""Maker keys (`mfr:` rows): the library key is rewritten where it is used as a key, the display name never.

A maker key (`Texas_Instruments`, `Diodes-Inc.`) is also a maker's name in prose, and a hyphen does not make it a
key (`G-Switch` is a display name too). So a `mfr:` row is never rewritten as a whole token: only where the text
uses it as a key (CONTEXTS), and files move where a path segment is the key (`Murata/`, `Murata.pretty`,
`Murata.3dshapes`, `Murata.kicad_sym`). Two files that end at one path (a `merge` row) are a conflict.
Spec: docs/specs/rename.md.
"""
import re

from odw import rename_rewrite as rw

KIND = "mfr"
EXTS = ("kicad_sym", "pretty", "3dshapes")
LEFT = r"(?<![\w.-])"
QUOTE = "`'\""
CONTEXTS = (  # each with the groups `pre` (kept) and `k` (the key)
    r"(?P<pre>)" + LEFT + "{k}(?=:[^\\s:])",  # a lib id `Key:Name`
    r"(?P<pre>)" + LEFT + "{k}(?=\\.(?:" + "|".join(EXTS) + ")(?![\\w-]))",  # a library file or folder
    r"(?P<pre>)" + LEFT + "{k}(?=/(?:[^\\s/" + QUOTE + "]+\\.\\w+|[^\\s/" + QUOTE + "]+/|[" + QUOTE + "]))",  # a folder
    r"(?P<pre>(?<![\w-])[\"']?(?i:library)[\"']?\s*[:=]\s*[\"']){k}(?=[\"'])",  # a `library` field
    r"(?P<pre>\(name\s+\"){k}(?=\")",  # a lib table entry
    r"(?P<pre>(?<!`)`){k}(?=`(?!`))",  # a code span holding only the key
)


def kind_of(old):
    return old.split(":", 1)[0]


def is_key_row(r):
    return kind_of(r.old) == KIND


def patterns(table):
    """The context patterns for every old key in `table` (old → new), longest key first."""
    if not table:
        return []
    alt = "|".join(re.escape(k) for k in sorted(table, key=len, reverse=True))
    return [re.compile(c.replace("{k}", f"(?P<k>{alt})")) for c in CONTEXTS]


def rewrite(text, pats, table):
    """The text with every key in a key context rewritten, and one edit per rewrite."""
    if not pats:
        return text, []
    edits, out = [], []
    for n, line in enumerate(text.split("\n"), start=1):
        for p in pats:
            def sub(m):
                edits.append(rw.Edit("identifier", n, m.group("k"), table[m.group("k")]))
                return m.group("pre") + table[m.group("k")]
            line = p.sub(sub, line)
        out.append(line)
    return "\n".join(out), edits


def _segment(seg, table, last):
    if not last and seg in table:
        return table[seg]
    stem, dot, ext = seg.rpartition(".")
    if dot and ext in EXTS and stem in table:
        return f"{table[stem]}.{ext}"
    return seg


def new_path(path, table):
    """The path with each key segment renamed: a folder named as the key, or `<key>.<library extension>`."""
    segs = path.split("/")
    return "/".join(_segment(s, table, i == len(segs) - 1) for i, s in enumerate(segs))


def key_of(path, table):
    """The first old key a path carries as a segment (for the row number of its move)."""
    segs = path.split("/")
    for i, s in enumerate(segs):
        if _segment(s, table, i == len(segs) - 1) != s:
            return s if s in table else s.rpartition(".")[0]
    return None


def plan_moves(plan, files, moves, table, rows):
    """Add the key moves to `plan` and `moves` (old → new path), then the merge clashes: two files that end at
    one path, or a key move onto a file that exists and stays (compared without case)."""
    if not table:
        return
    deleted = {d["path"] for d in plan.deletes}
    for src in sorted(files):
        if src in deleted:
            continue
        dst = moves.get(src, src)
        new = new_path(dst, table)
        if new == dst:
            continue
        row = rows[key_of(dst, table)]
        if src in moves:
            for mv in plan.moves:
                if mv["from"] == src:
                    mv["to"] = new
        else:
            plan.moves.append({"row": row, "from": src, "to": new})
        moves[src] = new
    by_target, lower = {}, {p.lower(): p for p in files}
    for mv in plan.moves:
        by_target.setdefault(mv["to"], []).append(mv)
    for t, mvs in sorted(by_target.items()):
        clash = lower.get(t.lower())
        if len(mvs) > 1 and not any(c["path"] == t for c in plan.conflicts):
            plan.conflicts.append({"path": t, "rows": sorted({m["row"] for m in mvs}),
                                   "reason": "merge clash: " + " and ".join(m["from"] for m in mvs)
                                   + " both become this path"})
        elif clash is not None and clash not in moves and clash not in deleted and not any(
                c["path"] == t for c in plan.conflicts):
            plan.conflicts.append({"path": t, "rows": [mvs[0]["row"]],
                                   "reason": f"merge clash: {clash} exists and stays"})
