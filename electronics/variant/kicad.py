"""KiCad side of `odw variant`: kicad-cli, the XML netlist, and KiCad 10 variant data in S-expression files.

- `cli()` finds kicad-cli (KICAD_CLI, PATH, the macOS application); `run()` runs it and drops font warnings.
- `components(xml)` reads `sch export netlist --format kicadxml`: one record per reference with its fields, its
  DNP and BOM flags and the overrides of every KiCad variant (`<variants>`).
- `write_sch_variant()` / `write_pcb_variant()` write one variant's data into a copy of a schematic sheet or board,
  as KiCad 10 stores it: `(variant (name …) (dnp yes|no) (field (name …) (value …)))` inside each symbol instance
  path and each footprint, and the board's `(variants (variant (name …) (description …)))` list.
- `nets(xml)` reads the same export's nets: name and nodes (reference, pin, pin type);
  `interface_tables(text)` reads a sheet's interface tables (a `Net` column and an `Absent state…` column).
- `drop_symbols()` removes the symbols of a set of references from a sheet (the variant's ERC copy).

Edits are made on the text, by span, so everything else in the file stays byte-identical.
"""
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

MAC_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
NOISE = re.compile(r"^(Fontconfig warning|.*assert .* failed in Get\(\)).*$\n?", re.M)


def cli():
    for c in (os.environ.get("KICAD_CLI"), shutil.which("kicad-cli"), MAC_CLI):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def run(kcli, args, cwd=None, timeout=1800):
    r = subprocess.run([kcli] + args, capture_output=True, text=True, cwd=cwd, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    r.stdout, r.stderr = NOISE.sub("", r.stdout), NOISE.sub("", r.stderr)
    return r


def version(kcli):
    r = run(kcli, ["version"])
    return r.stdout.strip() if r.returncode == 0 else None


# ------------------------------------------------------------------ XML netlist


def components(xml_path):
    root = ET.parse(xml_path).getroot()
    out = []
    for c in root.iter("comp"):
        fields = {f.get("name"): (f.text or "") for f in c.findall("./fields/field")}
        props = {p.get("name"): p.get("value") for p in c.findall("./property")}
        rec = {"ref": c.get("ref"), "value": c.findtext("value") or "", "footprint": c.findtext("footprint") or "",
               "fields": fields, "dnp": "dnp" in props, "in_bom": "exclude_from_bom" not in props,
               "sheetfile": props.get("Sheetfile", ""), "sheet": (c.find("sheetpath").get("names")
                                                                  if c.find("sheetpath") is not None else ""),
               "variants": {}}
        for v in c.findall("./variants/variant"):
            vp = {p.get("name"): p.get("value") for p in v.findall("./property")}
            rec["variants"][v.get("name")] = {
                "dnp": None if "dnp" not in vp else vp["dnp"] not in ("0", "no", "false"),
                "in_bom": None if "exclude_from_bom" not in vp else vp["exclude_from_bom"] in ("0", "no", "false"),
                "fields": {f.get("name"): (f.text or "") for f in v.findall("./fields/field")}}
        out.append(rec)
    return out


def nets(xml_path):
    root = ET.parse(xml_path).getroot()
    return [{"name": n.get("name"), "nodes": [{"ref": x.get("ref"), "pin": x.get("pin"),
                                               "pintype": x.get("pintype") or ""} for x in n.findall("node")]}
            for n in root.iter("net")]


def netlist(kcli, sch, out_xml, with_nets=False):
    """Export from a copy of the project's sheets: kicad-cli writes local state (.kicad_prl) next to what it opens,
    and the base design is never written."""
    src = os.path.dirname(os.path.abspath(sch))
    work = os.path.join(os.path.dirname(os.path.abspath(out_xml)), ".netlist-src")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    for f in os.listdir(src):
        if f.endswith((".kicad_sch", ".kicad_pro")) and not f.startswith(("_autosave", "~")):
            shutil.copy(os.path.join(src, f), os.path.join(work, f))
    try:
        comps = _netlist(kcli, os.path.join(work, os.path.basename(sch)), out_xml)
        return (comps, nets(out_xml)) if with_nets else comps
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _netlist(kcli, sch, out_xml):
    r = run(kcli, ["sch", "export", "netlist", "--format", "kicadxml", "-o", out_xml, sch])
    if r.returncode != 0 or not os.path.isfile(out_xml):
        raise RuntimeError(f"kicad-cli netlist export failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return components(out_xml)


# ------------------------------------------------------------------ S-expression spans


def _unq(s):
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), s)


def interface_tables(text):
    """(net names cell, absent state cell) of every row of every table in a sheet whose header has a `Net` column
    and a column starting with `Absent state` (the interface tables of RFC 0011 block sheets)."""
    out = []
    for m in re.finditer(r"\(table \(column_count (\d+)\)", text):
        span = (m.start(), _scan(text, m.start()))
        cells = [_unq(c) for c in re.findall(r'\(table_cell "((?:[^"\\]|\\.)*)"', text[span[0]:span[1]])]
        n = int(m.group(1))
        rows = [cells[i:i + n] for i in range(0, len(cells) - n + 1, n)]
        if not rows:
            continue
        head = rows[0]
        net = next((i for i, h in enumerate(head) if h.strip() == "Net"), None)
        state = next((i for i, h in enumerate(head) if h.strip().startswith("Absent state")), None)
        if net is None or state is None:
            continue
        out += [(r[net], r[state]) for r in rows[1:] if r[net].strip()]
    return out


def _scan(text, start):
    """End index (exclusive) of the list opening at text[start] == '('."""
    depth, i, n, in_str = 0, start, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced S-expression")


def children(text, start, end, _head=None):
    """(start, end, head) of every list directly inside the list text[start:end]."""
    out, i, in_str = [], start + 1, False
    while i < end - 1:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            j = _scan(text, i)
            m = re.match(r"\(([^\s()\"]+)", text[i:i + 64])
            out.append((i, j, m.group(1) if m else ""))
            i = j
            continue
        i += 1
    return out


def _first(text, span, head):
    return next((c for c in children(text, *span) if c[2] == head), None)


def _str_arg(text, span):
    m = re.match(r'\(\S+\s+"((?:[^"\\]|\\.)*)"', text[span[0]:span[1]])
    return m.group(1).replace('\\"', '"').replace("\\\\", "\\") if m else None


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _variant_sexpr(name, dnp, fields, indent="", desc=None):
    parts = [f"(name {q(name)})"]
    if desc is not None:
        parts.append(f"(description {q(desc)})")
    if dnp is not None:
        parts.append(f"(dnp {'yes' if dnp else 'no'})")
    parts += [f"(field (name {q(k)}) (value {q(v)}))" for k, v in sorted(fields.items())]
    return f"{indent}(variant " + " ".join(parts) + ")"


def _apply(text, edits):
    for s, e, rep in sorted(edits, key=lambda x: x[0], reverse=True):
        text = text[:s] + rep + text[e:]
    return text


def _drop_named_variant(text, span, name, edits):
    for c in children(text, *span):
        if c[2] == "variant":
            n = _first(text, c, "name")
            if n and _str_arg(text, n) == name:
                s = c[0]
                while s > span[0] and text[s - 1] in " \t\n":
                    s -= 1
                edits.append((s, c[1], ""))


def symbol_spans(text):
    root = (0, _scan(text, text.index("(")))
    return [c for c in children(text, *root) if c[2] == "symbol"]


def write_sch_variant(text, name, plan):
    """plan: ref -> (dnp or None, {field: value}); writes `name`'s data into every instance path of those refs."""
    edits = []
    for sym in symbol_spans(text):
        inst = _first(text, sym, "instances")
        if not inst:
            continue
        for proj in children(text, *inst):
            for path in children(text, *proj):
                if path[2] != "path":
                    continue
                ref_span = _first(text, path, "reference")
                ref = _str_arg(text, ref_span) if ref_span else None
                if ref not in plan:
                    continue
                _drop_named_variant(text, path, name, edits)
                dnp, fields = plan[ref]
                if dnp is None and not fields:
                    continue
                edits.append((path[1] - 1, path[1] - 1, " " + _variant_sexpr(name, dnp, fields)))
    return _apply(text, edits)


def power_nets(text):
    """The net names a sheet's power symbols create: the Value of every placed symbol whose library symbol carries
    the `(power …)` flag."""
    root = (0, _scan(text, text.index("(")))
    libs = _first(text, root, "lib_symbols")
    power = set()
    for c in children(text, *libs) if libs else []:
        if c[2] == "symbol" and any(k[2] == "power" for k in children(text, *c)):
            name = re.match(r'\(symbol\s+"((?:[^"\\]|\\.)*)"', text[c[0]:c[1]])
            if name:
                power.add(_unq(name.group(1)))
    out = set()
    for sym in symbol_spans(text):
        lib = _first(text, sym, "lib_id")
        if lib and _str_arg(text, lib) in power:
            for p in children(text, *sym):
                m = re.match(r'\(property\s+"Value"\s+"((?:[^"\\]|\\.)*)"', text[p[0]:p[1]]) if p[2] == "property" \
                    else None
                if m:
                    out.add(_unq(m.group(1)))
    return out


def drop_symbols(text, refs):
    """Remove the symbols whose every instance reference is in `refs`; returns (text, removed refs)."""
    edits, removed = [], set()
    for sym in symbol_spans(text):
        inst = _first(text, sym, "instances")
        if not inst:
            continue
        sym_refs = set()
        for proj in children(text, *inst):
            for path in children(text, *proj):
                r = _first(text, path, "reference") if path[2] == "path" else None
                if r:
                    sym_refs.add(_str_arg(text, r))
        if sym_refs and sym_refs <= refs:
            edits.append((sym[0], sym[1], ""))
            removed |= sym_refs
    return _apply(text, edits), removed


def footprint_refs(text):
    root = (0, _scan(text, text.index("(")))
    out = []
    for c in children(text, *root):
        if c[2] != "footprint":
            continue
        ref = None
        for p in children(text, *c):
            if p[2] == "property" and text[p[0]:p[0] + 32].startswith('(property "Reference"'):
                m = re.match(r'\(property "Reference" "((?:[^"\\]|\\.)*)"', text[p[0]:p[1]])
                ref = m.group(1) if m else None
                break
        out.append((c, ref))
    return root, out


def _append(text, span, payload, edits):
    """Put payload as the last element of the list at span, on its own line, replacing the whitespace before ')'."""
    at = span[1] - 1
    while text[at - 1] in " \t\n":
        at -= 1
    edits.append((at, span[1] - 1, payload))


def write_pcb_variant(text, name, description, plan):
    root, fps = footprint_refs(text)
    edits = []
    for span, ref in fps:
        if ref not in plan:
            continue
        _drop_named_variant(text, span, name, edits)
        dnp, fields = plan[ref]
        if dnp is None and not fields:
            continue
        _append(text, span, "\n" + _variant_sexpr(name, dnp, fields, "\t\t") + "\n\t", edits)
    entry = _variant_sexpr(name, None, {}, "\t\t", desc=description)
    vs = _first(text, root, "variants")
    if vs:
        _drop_named_variant(text, vs, name, edits)
        _append(text, vs, "\n" + entry + "\n\t", edits)
    else:
        at = fps[0][0][0] if fps else root[1] - 1
        edits.append((at, at, "(variants\n" + entry + "\n\t)\n\t"))
    return _apply(text, edits)


def pcb_variant_names(text):
    root = (0, _scan(text, text.index("(")))
    vs = _first(text, root, "variants")
    if not vs:
        return []
    return [_str_arg(text, _first(text, c, "name")) for c in children(text, *vs) if c[2] == "variant"]
