"""The feature-block catalog `odw variant` validates variant files against (RFC 0011 §1).

Two sources, chosen by the variant file's `catalog` path:

- a folder of block records, `blocks/<block>.toml` (`opendap.block/v1`, the subset in docs/specs/variant-tool.md §3);
- a Markdown catalog laid out as RFC 0011 asks (the board feature-blocks spec): a summary table with an `ID`
  column, then one record per block under a heading that names it in backticks, with an `Alternates` row.
  This reader is transitional: it goes when the catalog becomes block records.

Besides the fields `odw variant` validates against, each block carries what the absent-state check and the
software outputs read: `absent_text` (the block-level absent state), `absent_nets` (per-net declarations of a
record's `absent_state` table; the schematic's interface tables add theirs in absent.py) and `software` (devicetree
node, compatibles, `cc.opendap.Blackbird.Hw.*` interfaces, device-description sections), parsed from a record's `software`
table or from a Markdown `Software` cell by `parse_software`.

`load(path)` returns a `Catalog`; every problem in the catalog itself is a finding, never an exception, except
a path that does not exist (CatalogError).
"""
import os
import re
import tomllib

BLOCK_SCHEMA = "opendap.block/v1"
BLOCK_KEYS = {"schema", "id", "function", "status", "requirements", "alternates", "params", "sub_blocks",
              # RFC 0011 §1 fields this tool does not read, accepted so one record serves every tool
              "interface", "power_domain", "absent_state", "measure", "software", "sheet", "layout_block", "level",
              "note"}
ID_RE = re.compile(r"blk\.[a-z0-9][a-z0-9-]*")
REQ_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d{2,}\b")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
STATES = ("fit", "dnp", "alternate")


SOFTWARE_KEYS = {"devicetree", "compatible", "provides", "description", "note"}
COMPATIBLE_RE = re.compile(r"[a-z0-9][a-z0-9-]*,[a-z0-9][a-z0-9.,+_-]*")
PROVIDES_RE = re.compile(r"cc\.opendap\.Blackbird\.Hw(?:\.[A-Za-z][A-Za-z0-9]*)+")


class CatalogError(Exception):
    pass


def parse_software(text):
    """The software of a block from a Markdown `Software` cell (or a record's `software` string): the devicetree node
    named as "`<label>` node" (or "`/path` node"), backticked compatibles (`vendor,device`), `cc.opendap.Blackbird.Hw.*`
    interfaces (a backticked `.Name` continues the previous one's namespace) and the device-description sections
    backticked after the word "description"."""
    sw = {"devicetree": None, "compatible": [], "provides": [], "description": [], "note": text}
    m = re.search(r"`([^`]+)` node\b", text)
    if m:
        sw["devicetree"] = m.group(1)
    last = None
    for tok in re.findall(r"`([^`]+)`", text):
        if COMPATIBLE_RE.fullmatch(tok) and tok not in sw["compatible"]:
            sw["compatible"].append(tok)
        elif PROVIDES_RE.fullmatch(tok):
            last = tok
            sw["provides"].append(tok)
        elif last and re.fullmatch(r"\.[A-Za-z][A-Za-z0-9]*", tok):
            sw["provides"].append(last.rsplit(".", 1)[0] + tok)
    for clause in re.findall(r"\bdescription\b([^;.]*)", text):
        sw["description"] += [t for t in re.findall(r"`([a-z][a-z-]*)`", clause) if t not in sw["description"]]
    return sw


class Block:
    def __init__(self, bid):
        self.id, self.function, self.status = bid, "", "active"
        self.requirements = []          # [(requirement id, priority or None)]
        self.alternates, self.params, self.sub_blocks = [], [], []
        self.table_states = {}          # Markdown summary table: variant column -> cell text
        self.absent_text = ""           # the block-level absent state
        self.absent_nets = []           # [([net names or patterns], absent state text, source)]
        self.software = parse_software("")

    @property
    def must(self):
        return {r for r, p in self.requirements if p == "must"}

    def as_dict(self):
        return {"id": self.id, "function": self.function, "status": self.status,
                "requirements": [{"id": r, "priority": p} for r, p in self.requirements],
                "alternates": list(self.alternates), "params": list(self.params), "sub_blocks": list(self.sub_blocks)}


class Catalog:
    def __init__(self, path, kind):
        self.path, self.kind, self.blocks, self.findings = path, kind, {}, []

    def active(self):
        return {k: b for k, b in self.blocks.items() if b.status == "active"}

    def owner(self, block_field):
        """The catalog block a schematic `Block` value belongs to: (block id or None, how)."""
        if block_field in self.blocks:
            return block_field, "catalog"
        for b in self.blocks.values():
            if block_field in b.sub_blocks:
                return b.id, "sub_block"
        cands = [b for b in self.blocks if block_field.startswith(b + "-")]
        if cands:
            return max(cands, key=len), "prefix"
        return None, "unknown"

    def finding(self, level, code, where, message):
        self.findings.append({"level": level, "code": code, "where": where, "message": message})


def load(path):
    if os.path.isdir(path):
        return _load_records(path)
    if path.endswith(".md") and os.path.isfile(path):
        return _load_markdown(path)
    if not os.path.exists(path):
        raise CatalogError(f"catalog {path} not found")
    raise CatalogError(f"catalog {path}: neither a blocks/ folder of {BLOCK_SCHEMA} records nor a Markdown catalog")


# ------------------------------------------------------------------ block records


def _load_records(folder):
    cat = Catalog(folder, "records")
    files = sorted(f for f in os.listdir(folder) if f.endswith(".toml"))
    if not files:
        cat.finding("error", "catalog.empty", folder, "no block records (*.toml)")
    for f in files:
        where = os.path.join(folder, f)
        try:
            with open(where, "rb") as fh:
                d = tomllib.load(fh)
        except tomllib.TOMLDecodeError as e:
            cat.finding("error", "catalog.toml", where, str(e))
            continue
        if d.get("schema") != BLOCK_SCHEMA:
            cat.finding("error", "catalog.schema", where, f"schema must be \"{BLOCK_SCHEMA}\"")
            continue
        for k in sorted(set(d) - BLOCK_KEYS):
            cat.finding("error", "catalog.field-unknown", where, f"unknown field {k}")
        bid = d.get("id")
        if not isinstance(bid, str) or not ID_RE.fullmatch(bid):
            cat.finding("error", "catalog.id", where, "id must be blk.<name> (lowercase, digits, hyphens)")
            continue
        if bid in cat.blocks:
            cat.finding("error", "catalog.duplicate", where, f"{bid} is defined twice")
            continue
        b = Block(bid)
        b.function = d.get("function", "")
        b.status = d.get("status", "active")
        if b.status not in ("active", "removed"):
            cat.finding("error", "catalog.status", where, "status must be active or removed")
        for r in d.get("requirements", []):
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or \
                    r.get("priority") not in ("must", "should", None):
                cat.finding("error", "catalog.requirement", where, "requirements are {id, priority = must|should}")
                continue
            b.requirements.append((r["id"], r.get("priority")))
        for a in d.get("alternates", []):
            name = a.get("name") if isinstance(a, dict) else a
            if not isinstance(name, str) or not NAME_RE.fullmatch(name):
                cat.finding("error", "catalog.alternate", where, f"alternate name {name!r} is not a lowercase name")
                continue
            b.alternates.append(name)
        b.params = [p for p in d.get("params", []) if isinstance(p, str)]
        b.sub_blocks = [s for s in d.get("sub_blocks", []) if isinstance(s, str)]
        _record_absent(cat, b, d.get("absent_state"), where, os.path.join(os.path.basename(folder), f))
        _record_software(cat, b, d.get("software"), where)
        cat.blocks[bid] = b
    return cat


def _record_absent(cat, b, value, where, source):
    """absent_state: a string (the block-level state) or a list of {nets = [...], state = "..."}."""
    if value is None:
        return
    if isinstance(value, str):
        b.absent_text = value
        return
    if not isinstance(value, list):
        cat.finding("error", "catalog.absent-state", where, "absent_state is a string or a list of {nets, state}")
        return
    for e in value:
        if not (isinstance(e, dict) and set(e) <= {"nets", "state"} and isinstance(e.get("state"), str) and
                isinstance(e.get("nets"), list) and e["nets"] and all(isinstance(n, str) for n in e["nets"])):
            cat.finding("error", "catalog.absent-state", where,
                        "each absent_state entry is {nets = [\"NET\", ...], state = \"...\"}")
            continue
        b.absent_nets.append((list(e["nets"]), e["state"], source))


def _record_software(cat, b, value, where):
    """software: a string (read as a Markdown Software cell) or {devicetree, compatible, provides, description, note}."""
    if value is None:
        return
    if isinstance(value, str):
        b.software = parse_software(value)
        return
    ok = isinstance(value, dict) and set(value) <= SOFTWARE_KEYS
    ok = ok and all(isinstance(value.get(k, ""), str) for k in ("devicetree", "note"))
    ok = ok and all(isinstance(value.get(k, []), list) and all(isinstance(x, str) for x in value.get(k, []))
                    for k in ("compatible", "provides", "description"))
    if not ok:
        cat.finding("error", "catalog.software", where,
                    "software is a string or {devicetree = \"label or /path\", compatible, provides, description "
                    "(lists of strings), note}")
        return
    b.software = {"devicetree": value.get("devicetree"), "compatible": list(value.get("compatible", [])),
                  "provides": list(value.get("provides", [])), "description": list(value.get("description", [])),
                  "note": value.get("note", "")}


# ------------------------------------------------------------------ Markdown catalog


def _cells(line):
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return None
    return [c.strip() for c in re.split(r"(?<!\\)\|", s[1:-1])]


def _ticks(text):
    return re.findall(r"`([^`]+)`", text)


def _requirements(text):
    """Requirement IDs with the priority of the parenthesis that follows them: 'A-1, A-2 (must), B-3 (should)'."""
    out, pending = [], []
    for tok in re.finditer(r"\(([^)]*)\)|" + REQ_RE.pattern, text):
        if tok.group(1) is not None:
            word = re.match(r"\s*(must|should)\b", tok.group(1))
            prio = word.group(1) if word else None
            out += [(r, prio) for r in pending]
            pending = []
            out += [(r, None) for r in REQ_RE.findall(tok.group(1))]
        else:
            pending.append(tok.group(0))
    out += [(r, None) for r in pending]
    seen, uniq = set(), []
    for r, p in out:
        if r not in seen:
            seen.add(r)
            uniq.append((r, p))
    return uniq


def _load_markdown(path):
    cat = Catalog(path, "markdown")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    inherit = {}
    # 1. The summary table: the first table whose header has an ID column.
    i = 0
    while i < len(lines):
        head = _cells(lines[i])
        if head and "ID" in head and i + 1 < len(lines) and re.fullmatch(r"[|\s:-]+", lines[i + 1].strip()):
            col = {h: n for n, h in enumerate(head)}
            bound = next((n for h, n in col.items() if h.startswith("Bound by")), None)
            states = {h: n for h, n in col.items() if re.fullmatch(r"[a-z][a-z0-9-]*", h)}
            i += 2
            while i < len(lines) and _cells(lines[i]):
                row = _cells(lines[i])
                ids = ID_RE.findall(row[col["ID"]]) if len(row) == len(head) else []
                if len(ids) == 1:
                    b = Block(ids[0])
                    b.function = row[col["Function"]] if "Function" in col else ""
                    if b.function.lower().startswith("removed"):
                        b.status = "removed"
                    if bound is not None:
                        b.requirements = _requirements(row[bound])
                        as_ = re.search(r"\bas `(blk\.[a-z0-9-]+)`", row[bound])
                        if as_ and not b.requirements:
                            inherit[b.id] = as_.group(1)
                    b.table_states = {h: row[n] for h, n in states.items()}
                    if b.id in cat.blocks:
                        cat.finding("error", "catalog.duplicate", f"{path}:{i + 1}", f"{b.id} is listed twice")
                    cat.blocks[b.id] = b
                i += 1
            break
        i += 1
    if not cat.blocks:
        cat.finding("error", "catalog.empty", path, "no summary table with an ID column and blk.* rows")
        return cat
    for child, parent in inherit.items():
        if parent in cat.blocks:
            cat.blocks[child].requirements = list(cat.blocks[parent].requirements)
    # 2. Records: a heading naming blocks in backticks, then its field table.
    current = []
    for n, line in enumerate(lines, 1):
        if line.startswith("#"):
            current = [b for b in ID_RE.findall(" ".join(_ticks(line))) if b in cat.blocks]
            if current and re.search(r":\s*removed\s*$", line, re.I):
                for b in current:
                    cat.blocks[b].status = "removed"
            continue
        row = _cells(line)
        if current and row and len(row) >= 2 and row[0] == "Absent state":
            for b in current:
                cat.blocks[b].absent_text = row[1]
        if current and row and len(row) >= 2 and row[0] == "Software":
            for b in current:
                cat.blocks[b].software = parse_software(row[1])
        if current and row and len(row) >= 2 and row[0] == "Sub-blocks":
            # in a record of several blocks, a sub-block belongs to the one its name starts with (the longest)
            for t in [t for t in ID_RE.findall(" ".join(_ticks(row[1]))) if t not in cat.blocks]:
                own = max((b for b in current if t.startswith(b + "-")), key=len, default=current[0])
                cat.blocks[own].sub_blocks.append(t)
        if current and row and len(row) >= 2 and row[0] == "Alternates":
            cell = row[1]
            split = re.search(r"\bParameters?\b", cell)
            alt_part, par_part = (cell[:split.start()], cell[split.start():]) if split else (cell, "")
            alts = [t for t in _ticks(alt_part) if NAME_RE.fullmatch(t) and not t.startswith("blk.") and t not in STATES]
            params = [t for t in _ticks(par_part) if re.fullmatch(r"[a-z][a-z0-9_]*", t)]
            for b in current:
                cat.blocks[b].alternates = alts
                cat.blocks[b].params = params
    return cat
