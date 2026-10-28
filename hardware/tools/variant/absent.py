"""The absent-state check of `odapkit variant` (RFC 0011 rule 2; docs/specs/variant-tool.md §6).

For one variant, every net that touches a part the variant leaves unfitted is looked at with the fitted parts only:

- *removed*: no fitted node is left (the net lived inside what is not fitted);
- *rail*: a supply net (a node of pin type power_in or power_out, or a net a power symbol names); listed only as *rail-unsourced* when the removed
  parts held its only source (power_out) and fitted loads (power_in) remain;
- *single-pin*: one fitted part is left on a net that had two or more;
- *floating*: an input, bidirectional or tri-state pin is left with no driver (output, power_out) and no pull (a
  fitted two-pin resistor, reference `R<n>`, whose other pin is on a rail);
- *defined*: anything else.

Every net that touches a part of a `dnp` block (scope `block`) needs a declared absent state: a row of the block's
interface table on its schematic sheet (a `Net` column and an `Absent state…` column) or an entry of its record's
`absent_state` list. Nets touched only by other unfitted parts (option lands, alternate twins: scope `part`) are
reported when they become single-pin; one whose remaining part is in the same block is an expected option.

`check()` returns (report, findings); the report is `opendap.variant-absent-state/v1`.
"""
import os
import re
from collections import Counter

SCHEMA = "opendap.variant-absent-state/v1"
INPUTS = {"input", "bidirectional", "tri_state"}
DRIVERS = {"output", "power_out"}
POWER = {"power_in", "power_out"}
PULL_REF = re.compile(r"R\d+")
NET_TOKEN = re.compile(r"[A-Za-z0-9_.*#+\-\[\]{}]+")
LEVELS = {"error": 0, "warning": 1}


def base(pintype):
    return (pintype or "").split("+")[0]


def local(name):
    return name.rsplit("/", 1)[-1]


def natural(ref):
    m = re.match(r"(\D*)(\d*)(.*)", ref)
    return (m.group(1), int(m.group(2) or 0), m.group(3))


def patterns(cell):
    """Compiled patterns for the net names of an interface-table cell: names separated by commas; text in parentheses
    and after the first word of each item is ignored; `_RXD` after `BT_UART_TXD` is `BT_UART_RXD`; `SD_D0-D3` is
    SD_D0 to SD_D3; `*` stands for any characters, and so does a lowercase `x` in an otherwise uppercase name
    (`DSD_x`, `DACx_AOUT_P`)."""
    cell = re.sub(r"\([^)]*\)", " ", cell)
    out, prev = [], None
    for item in cell.split(","):
        words = item.split()
        if not words:
            continue
        tok = words[0].strip(";:.")
        if tok.startswith("_") and prev and "_" in prev:
            tok = prev.rsplit("_", 1)[0] + tok
        if not NET_TOKEN.fullmatch(tok) or not re.search(r"[A-Za-z]", tok):
            continue
        prev = tok
        rng = re.fullmatch(r"(.*?)(\d+)-([A-Za-z_]*?)(\d+)", tok)
        if rng and (not rng.group(3) or rng.group(1).endswith(rng.group(3))) and int(rng.group(2)) <= int(rng.group(4)):
            names = [f"{rng.group(1)}{i}" for i in range(int(rng.group(2)), int(rng.group(4)) + 1)]
            out += [re.compile(re.escape(n)) for n in names]
            continue
        rx = re.escape(tok).replace(r"\*", ".+")
        letters = re.sub(r"[^A-Za-z]", "", tok.replace("x", ""))
        if "x" in tok and letters and letters.isupper():
            rx = rx.replace("x", "[A-Z0-9]+")
        out.append(re.compile(rx))
    return out


def kind(text):
    """What an absent-state text declares: not-applicable, pull (a pull resistor on the board), internal-pull (the
    SoC's or a part's own pull), tied, floating (left open by design) or described (anything else)."""
    t = text.lower()
    if "not applicable" in t:
        return "not-applicable"
    if re.search(r"pull(ed)?[- ]?(up|down)s?\b|pulled (low|high)", t):
        own = re.search(r"\b(soc|internal|ball)\b", re.sub(r"blk\.[a-z0-9-]+", " ", t))   # block IDs name sheets
        return "internal-pull" if own else "pull"
    if re.search(r"\bti(ed|e)\b", t):
        return "tied"
    if re.search(r"float|\bopen\b|no load|unconnected", t):
        return "floating"
    return "described"


def declarations(catalog, comps, tables):
    """block -> [(patterns, names cell, text, source)] from the sheets' interface tables and the records."""
    decl = {}
    parts = {}
    for c in comps:
        sf, bf = c.get("sheetfile") or "", (c.get("fields") or {}).get("Block")
        if sf and bf:
            o = catalog.owner(bf)[0]
            if o:
                parts.setdefault(sf, Counter())[o] += 1
    for sf in sorted(tables):
        o = catalog.owner("blk." + os.path.splitext(os.path.basename(sf))[0])[0]
        if not o and sf in parts:
            o = parts[sf].most_common(1)[0][0]
        if not o:
            continue
        for names, text in tables[sf]:
            decl.setdefault(o, []).append((patterns(names), names, text, sf))
    for bid, b in catalog.blocks.items():
        for names, text, src in b.absent_nets:
            decl.setdefault(bid, []).append((patterns(", ".join(names)), ", ".join(names), text, src))
    return decl


def check(variant, catalog, rows, comps, nets, tables, power=()):
    """(report, findings) for one variant. rows: resolve.components(); comps, nets: the XML netlist; tables:
    {sheet file: [(net names cell, absent state cell)]} from kicad.interface_tables(); power: the net names the
    sheets' power symbols create (kicad.power_nets())."""
    states = {b: e.get("state") for b, e in variant.blocks.items() if isinstance(e, dict)}
    active = catalog.active()
    dnp_blocks = sorted(b for b, s in states.items() if s == "dnp" and b in active)
    dnp_set = set(dnp_blocks)
    fitted = {r["ref"]: not r["dnp"] for r in rows}
    owner = {r["ref"]: r["block"] for r in rows}
    decl = declarations(catalog, comps, tables)
    rails = {n["name"] for n in nets if n["name"] in power or any(base(x["pintype"]) in POWER for x in n["nodes"])}
    pins = {}
    for n in nets:
        for x in n["nodes"]:
            pins.setdefault(x["ref"], []).append(n["name"])

    def pulled(left, name):
        for x in left:
            other = [p for p in pins.get(x["ref"], []) if p != name]
            if PULL_REF.fullmatch(x["ref"]) and len(pins.get(x["ref"], [])) == 2 and len(other) == 1 \
                    and other[0] in rails:
                return True
        return False

    def lookup(name, blocks):
        for b in blocks:
            for pats, names, text, src in decl.get(b, []):
                if any(p.fullmatch(name) for p in pats):
                    return {"block": b, "nets": names, "text": text, "kind": kind(text), "source": src}
        return None

    listed, counts = [], Counter()
    groups = {}
    for net in nets:
        name = net["name"] or ""
        if name.startswith("unconnected-"):
            continue
        gone = [x for x in net["nodes"] if not fitted.get(x["ref"], True)]
        if not gone:
            continue
        counts["touched"] += 1
        gone_blocks = sorted({owner.get(x["ref"]) for x in gone} - {None})
        in_dnp = [b for b in gone_blocks if b in dnp_set]
        scope = "block" if in_dnp else "part"
        left = [x for x in net["nodes"] if fitted.get(x["ref"], True)]
        left_refs = sorted({x["ref"] for x in left}, key=natural)
        if not left:
            counts["removed"] += 1
            continue
        types = {base(x["pintype"]) for x in left}
        driven = bool(types & DRIVERS) or pulled(left, name)
        undriven_input = bool(types & INPUTS) and not driven
        if name in rails:
            if any(base(x["pintype"]) == "power_out" for x in gone) and "power_out" not in types \
                    and "power_in" in types:
                cls = "rail-unsourced"
            else:
                counts["rails"] += 1
                continue
        elif len(left_refs) == 1 and len({x["ref"] for x in net["nodes"]}) >= 2:
            cls = "single-pin"
        elif undriven_input:
            cls = "floating"
        else:
            cls = "defined"
        if scope == "part" and cls != "single-pin":
            continue
        declared = lookup(local(name), in_dnp or gone_blocks)
        in_block = scope == "part" and {owner.get(r) for r in left_refs} <= set(gone_blocks)
        finding = None
        if declared:
            if declared["kind"] == "not-applicable" and scope == "block":
                finding = ("error", "absent.not-applicable")
            elif declared["kind"] == "pull" and (cls == "floating" or (cls == "single-pin" and undriven_input)):
                finding = ("warning", "absent.declared-mismatch")
        elif not in_block:
            finding = {"floating": ("error", "absent.floating"),
                       "single-pin": ("error" if undriven_input else "warning", "absent.single-pin"),
                       "rail-unsourced": ("warning", "absent.rail-unsourced"),
                       "defined": ("warning", "absent.undeclared")}[cls]
        counts[cls] += 1
        counts["expected" if finding is None else "unexpected"] += 1
        listed.append({"net": name, "class": cls, "scope": scope, "blocks": gone_blocks,
                       "removed": sorted({x["ref"] for x in gone}, key=natural), "remaining": left_refs,
                       "pins": sorted(f"{x['ref']}.{x['pin']} {base(x['pintype'])}" for x in left),
                       "declared": declared, "expected": finding is None,
                       "finding": finding[1] if finding else None})
        if finding:
            groups.setdefault((finding, ", ".join(in_dnp or gone_blocks)), []).append(name)
    where = f"{variant.path} (schematic)"
    findings = []
    for b in dnp_blocks:
        if kind(catalog.blocks[b].absent_text) == "not-applicable":
            findings.append({"level": "error", "code": "absent.not-applicable", "where": where,
                             "message": f"{b} is dnp, but its catalog record says its absent state is not "
                                        f"applicable: {catalog.blocks[b].absent_text}"})
    what = {"absent.floating": "left floating with no declared absent state",
            "absent.single-pin": "left with a single part and no declared absent state",
            "absent.rail-unsourced": "rails left with loads and no source, with no declared absent state",
            "absent.undeclared": "defined by what stays fitted but with no declared absent state",
            "absent.declared-mismatch": "declared as pulled, but no fitted pull holds them",
            "absent.not-applicable": "declared not applicable (never omitted), yet the variant leaves them"}
    for ((level, code), blocks), names in groups.items():
        names = sorted(names)
        findings.append({"level": level, "code": code, "where": where,
                         "message": f"{blocks}: {len(names)} nets {what[code]}: {', '.join(names[:8])}"
                                    f"{'…' if len(names) > 8 else ''}"})
    findings.sort(key=lambda f: (LEVELS.get(f["level"], 2), f["code"], f["message"]))
    listed.sort(key=lambda n: n["net"])
    report = {"schema": SCHEMA, "board": variant.data.get("board"), "variant": variant.name,
              "dnp_blocks": dnp_blocks,
              "blocks": [{"block": b, "absent_state": catalog.blocks[b].absent_text,
                          "declared_nets": len(decl.get(b, []))} for b in dnp_blocks],
              "counts": {k: counts[k] for k in ("touched", "removed", "rails", "single-pin", "floating", "defined",
                                                "rail-unsourced", "expected", "unexpected")},
              "nets": listed, "findings": findings}
    return report, findings
