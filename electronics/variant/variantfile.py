"""Variant files (`opendap.board-variant/v1`): loading, validation against the catalog, resolved block states.

`load(path)` reads one file and returns a `Variant` (raises VariantError only when the file is not TOML);
`validate(variant, catalog)` returns findings; `resolve(variant, catalog)` returns one row per catalog block.
The format is in docs/specs/variant-tool.md §2; the rules are those of RFC 0011 §2 and the board spec it came from.
"""
import os
import re
import tomllib

SCHEMA = "opendap.board-variant/v1"
TOP = {"schema", "board", "name", "kind", "status", "description", "target_level", "catalog", "promise",
       "instrumentation", "blocks"}
REQUIRED = ("schema", "board", "name", "kind", "status", "target_level", "catalog", "blocks")
ENTRY = {"state", "alternate", "params", "open", "note"}
STATES = ("fit", "dnp", "alternate")
LINKS = ("shunt", "zero-ohm")
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
REQ = re.compile(r"[A-Z][A-Z0-9]*-\d{2,}")


class VariantError(Exception):
    pass


class Variant:
    def __init__(self, path, data):
        self.path, self.data = path, data
        self.name = data.get("name") if isinstance(data.get("name"), str) else os.path.splitext(os.path.basename(path))[0]
        self.blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}

    @property
    def links(self):
        inst = self.data.get("instrumentation")
        return inst.get("links") if isinstance(inst, dict) else None

    def catalog_path(self):
        """The catalog named by `catalog`, relative to the component root: the nearest folder above the variant
        file in which that path exists (the repository root at most)."""
        rel = self.data.get("catalog")
        if not isinstance(rel, str):
            return None
        d = os.path.dirname(os.path.abspath(self.path))
        while True:
            cand = os.path.join(d, rel)
            if os.path.exists(cand):
                return cand
            if os.path.isdir(os.path.join(d, ".git")) or os.path.dirname(d) == d:
                return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(self.path))), rel)
            d = os.path.dirname(d)


def load(path):
    try:
        with open(path, "rb") as f:
            return Variant(path, tomllib.load(f))
    except tomllib.TOMLDecodeError as e:
        raise VariantError(f"{path}: not valid TOML: {e}")


def discover(folder):
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".toml"))


def _f(level, code, where, message):
    return {"level": level, "code": code, "where": where, "message": message}


def validate(v, catalog):
    out, d, where = [], v.data, v.path
    for k in sorted(set(d) - TOP):
        out.append(_f("error", "variant.field-unknown", where, f"unknown field {k}"))
    for k in REQUIRED:
        if k not in d:
            out.append(_f("error", "variant.field-missing", where, f"{k} is required"))
    if "schema" in d and d["schema"] != SCHEMA:
        out.append(_f("error", "variant.schema", where, f"schema must be \"{SCHEMA}\""))
    stem = os.path.splitext(os.path.basename(where))[0]
    if "name" in d and (not isinstance(d["name"], str) or not NAME.fullmatch(d["name"]) or d["name"] != stem):
        out.append(_f("error", "variant.name", where, f"name must be lowercase and equal the file name ({stem})"))
    if "kind" in d and (not isinstance(d["kind"], str) or not NAME.fullmatch(d["kind"])):
        out.append(_f("error", "variant.kind", where, "kind is full, production or an application name"))
    if "status" in d and d["status"] not in ("proposed", "accepted"):
        out.append(_f("error", "variant.status", where, "status is proposed or accepted"))
    if "target_level" in d and not re.fullmatch(r"Q[0-9]", str(d["target_level"])):
        out.append(_f("error", "variant.target-level", where, "target_level is an RFC 0010 level, Q0 to Q9"))
    for k in ("board", "description", "catalog"):
        if k in d and not isinstance(d[k], str):
            out.append(_f("error", "variant.field-type", where, f"{k} must be a string"))
    promise = d.get("promise", [])
    if not isinstance(promise, list) or not all(isinstance(p, str) and REQ.fullmatch(p) for p in promise):
        out.append(_f("error", "variant.promise", where, "promise is a list of requirement IDs (PREFIX-NNN)"))
        promise = []
    inst = d.get("instrumentation", {})
    if not isinstance(inst, dict):
        out.append(_f("error", "variant.field-type", where, "instrumentation must be a table"))
    else:
        for k in sorted(set(inst) - {"links", "test_points"}):
            out.append(_f("error", "variant.field-unknown", where, f"unknown field instrumentation.{k}"))
        if "links" in inst and inst["links"] not in LINKS:
            out.append(_f("error", "variant.instrumentation", where, f"instrumentation.links is one of {', '.join(LINKS)}"))
        if "test_points" in inst and inst["test_points"] != "fit":
            out.append(_f("error", "variant.instrumentation", where, "instrumentation.test_points is \"fit\""))
    if "blocks" in d and not isinstance(d["blocks"], dict):
        out.append(_f("error", "variant.field-type", where, "blocks must be a table"))
    if catalog is None:
        return out
    active = catalog.active()
    opened = []
    for bid, e in v.blocks.items():
        w = f"{where} [blocks.\"{bid}\"]"
        if bid not in catalog.blocks:
            out.append(_f("error", "block.unknown", w, f"{bid} is not in the catalog"))
            continue
        if bid not in active:
            out.append(_f("error", "block.removed", w, f"{bid} is removed from the catalog"))
            continue
        if not isinstance(e, dict):
            out.append(_f("error", "variant.field-type", w, "a block entry is a table {state, alternate, params, open, note}"))
            continue
        for k in sorted(set(e) - ENTRY):
            out.append(_f("error", "variant.field-unknown", w, f"unknown field {k}"))
        state = e.get("state")
        if state not in STATES:
            out.append(_f("error", "block.state", w, "state is fit, dnp or alternate"))
            continue
        blk = catalog.blocks[bid]
        if state == "alternate":
            alt = e.get("alternate")
            if not blk.alternates:
                out.append(_f("error", "alternate.not-allowed", w, f"the catalog lists no alternate for {bid}"))
            elif alt is None:
                out.append(_f("error", "alternate.missing", w, "state = \"alternate\" needs alternate = \"<name>\""))
            elif alt not in blk.alternates:
                out.append(_f("error", "alternate.unknown", w,
                              f"{alt!r} is not an alternate of {bid} (catalog: {', '.join(blk.alternates)})"))
        elif "alternate" in e:
            out.append(_f("error", "alternate.unexpected", w, f"alternate is set but state is {state}"))
        params = e.get("params", {})
        if not isinstance(params, dict):
            out.append(_f("error", "variant.field-type", w, "params must be a table"))
        else:
            for p in sorted(set(params) - set(blk.params)):
                out.append(_f("error", "param.undeclared", w, f"parameter {p} is not declared by {bid}"
                              + (f" (declared: {', '.join(blk.params)})" if blk.params else " (it declares none)")))
        for k in ("open", "note"):
            if k in e and not isinstance(e[k], str):
                out.append(_f("error", "variant.field-type", w, f"{k} must be a string"))
        if "open" in e:
            opened.append(bid)
        if state == "dnp":
            unmet = sorted(blk.must & set(promise))
            if unmet:
                out.append(_f("error", "promise.unmet", w, f"{bid} is dnp but bound by must requirements of the "
                              f"variant's promise: {', '.join(unmet)}"))
            if d.get("kind") == "full":
                out.append(_f("warning", "variant.full-dnp", w, f"{bid} is dnp in a full variant (RFC 0011 rule 1)"))
        cell = blk.table_states.get(v.name)
        if cell:
            want = "open" if cell.startswith("open") else "dnp" if cell.startswith("dnp") else \
                "fit" if cell.startswith("fit") else None
            have = "open" if "open" in e else state
            if want and want != have:
                out.append(_f("warning", "catalog.table-state", w,
                              f"the catalog's {v.name} column says {cell!r}, the variant file {have}"))
    for bid in sorted(set(active) - set(v.blocks)):
        out.append(_f("error", "block.missing", where, f"{bid} is in the catalog but not under [blocks]"))
    if d.get("status") == "accepted" and opened:
        out.append(_f("error", "open.accepted", where, f"an accepted variant has open entries: {', '.join(opened)}"))
    return out


def resolve(v, catalog):
    rows = []
    for bid, blk in (catalog.active() if catalog else {}).items():
        e = v.blocks.get(bid) if isinstance(v.blocks.get(bid), dict) else {}
        rows.append({"block": bid, "function": blk.function, "state": e.get("state"),
                     "alternate": e.get("alternate"), "params": e.get("params", {}), "open": e.get("open"),
                     "note": e.get("note"), "must": sorted(blk.must)})
    return rows


def summary(v):
    states = [e.get("state") for e in v.blocks.values() if isinstance(e, dict)]
    return {"name": v.name, "path": v.path, "kind": v.data.get("kind"), "status": v.data.get("status"),
            "target_level": v.data.get("target_level"), "board": v.data.get("board"),
            "fit": states.count("fit"), "dnp": states.count("dnp"), "alternate": states.count("alternate"),
            "open": sum(1 for e in v.blocks.values() if isinstance(e, dict) and "open" in e)}
