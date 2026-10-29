"""The design checks of the schematic craft check (tools/sch-craft-check.py): run(design, cfg, cfg_dir, c, aliases)
fills the checks that sch-craft-check.py's check_design registers (their IDs and levels live there) from the
project file, the sheets, the symbols, the labels and the wires. bind(names) gives it the check script's
constants. Python 3.9."""
import os
import re

from schmodel import (CONSTRAINT, URL, VALUE_UNIT, check_symbol, interface_of, net_name_problem, project_vars,
                      rail_like, rails_of, ref_prefix, value_problem)
from sexpr import flag, fmt, font_of, hidden, kid, kids, load_toml, on_grid, prop_text, props, text_size, val, xy

# Bound from the check script (bind):
DRAWING_SHEETS = ERC_ERRORS = GROUND = HOLDER = LICENCES = REQUIRED_FIELDS = ROLES = SHOWN_FIELDS = \
    TEXT_SIZES = TEXT_VARS = None


def bind(names):
    """Take the check script's upper-case constants (its limits and sets, read from tools/craft/rules.toml)."""
    globals().update({k: v for k, v in names.items() if k.isupper()})


def run(design, cfg, cfg_dir, c, aliases):
    rails, power_found = rails_of(design, cfg)
    interfaces = interface_of(cfg, cfg_dir)
    root = design.sheets[0]

    # ---- project file
    pro_s = design.pro.get("schematic", {})
    wks = pro_s.get("page_layout_descr_file", "")
    if not design.pro_path:
        c["CRAFT-100"].fail("no .kicad_pro next to the root sheet")
        c["CRAFT-158"].fail("no .kicad_pro next to the root sheet")
    elif os.path.basename(wks) not in DRAWING_SHEETS:
        c["CRAFT-100"].fail(f"drawing sheet {wks!r} is not one of {', '.join(DRAWING_SHEETS)} from kicad-lib")
    else:
        resolved = wks.replace("${KIPRJMOD}", design.dir)
        if not os.path.isabs(resolved):
            resolved = os.path.join(design.dir, resolved)
        if "${" in resolved or not os.path.exists(resolved):
            # kicad-cli then plots KiCad's default sheet without a word (KiCad 10.0.7)
            c["CRAFT-100"].fail(f"drawing sheet {wks!r} not found (KiCad would fall back to its default silently)")
        c["CRAFT-100"].info.append(wks)
    tv = project_vars(design)
    for v in TEXT_VARS:
        if not tv.get(v):
            c["CRAFT-101"].fail(f"project text variable {v} not set")
    if tv.get("OPENDAP_LICENCE") and tv["OPENDAP_LICENCE"] not in LICENCES:
        c["CRAFT-101"].fail(f"OPENDAP_LICENCE {tv['OPENDAP_LICENCE']!r} not in {LICENCES}")
    if tv.get("OPENDAP_SOURCE") and not re.match(r"^(https://)?github\.com/opendap-project/[A-Za-z0-9._-]+", tv["OPENDAP_SOURCE"]):
        c["CRAFT-101"].fail(f"OPENDAP_SOURCE {tv['OPENDAP_SOURCE']!r} is not a github.com/opendap-project repository")
    sev = design.pro.get("erc", {}).get("rule_severities", {})
    if design.pro_path:
        for k in ERC_ERRORS:
            if sev.get(k) != "error":
                c["CRAFT-158"].fail(f"ERC check {k} is {sev.get(k, 'default')!r}, not error")

    # ---- per sheet
    revs = {}
    for s in design.sheets:
        where = f"{s.name} (page {s.page})"
        n = s.node
        # title block
        tb = s.tb
        for k in ("title", "date", "rev", "company", "comment1", "comment2"):
            if not tb.get(k, "").strip():
                c["CRAFT-80"].fail(f"{where}: title block {k} empty")
        if tb.get("company") and tb["company"] != HOLDER:
            c["CRAFT-80"].fail(f"{where}: company {tb['company']!r}, not {HOLDER!r}")
        if tb.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tb["date"]):
            c["CRAFT-80"].fail(f"{where}: date {tb['date']!r} is not ISO 8601")
        role = s.role
        if role and not (role in ROLES or re.fullmatch(r"blk\.[a-z0-9][a-z0-9-]*", role)):
            c["CRAFT-80"].fail(f"{where}: comment 1 {role!r} is not a block ID (blk.<id>) or root/power/interconnect")
        if tb.get("rev"):
            revs.setdefault(tb["rev"], []).append(s.name)
        # paper
        paper = kid(n, "paper")
        size = paper[1] if paper else "?"
        portrait = paper is not None and "portrait" in paper[2:]
        allowed = ("A4", "A3")
        if size not in allowed or portrait:
            c["CRAFT-81"].fail(f"{where}: paper {size}{' portrait' if portrait else ''} (A4 or A3, landscape)")
        elif size == "A3" and s.is_block:
            c["CRAFT-81"].info.append(f"{where}: A3 block sheet (allowed for dense blocks; review)")
        # one block per sheet
        if s is root:
            parts = [y for y in s.symbols() if not flag(s.lib.get(val(y, "lib_id", ""), ["x"]), "power")
                     and not s.reference(y).startswith("#")]
            if parts:
                c["CRAFT-110"].fail(f"root sheet holds {len(parts)} part(s); parts belong in block sheets")
        if s.is_block:
            stem = os.path.splitext(s.name)[0]
            if stem != role[4:]:
                c["CRAFT-82"].fail(f"{where}: file {s.name} not named after block {role} ({role[4:]}.kicad_sch)")
            if s.sheet_node is not None and s.block_field != role:
                c["CRAFT-82"].fail(f"{where}: sheet symbol Block field {s.block_field!r} differs from title block {role!r}")
        elif s is not root and role not in ROLES:
            c["CRAFT-82"].fail(f"{where}: sheet is neither a block nor an overview")
        # notes
        if s.is_block:
            notes = [t for t in kids(n, "text_box") + kids(n, "text") if re.match(r"\s*Notes\b", t[1])]
            if not notes:
                c["CRAFT-115"].fail(f"{where}: no text box beginning with 'Notes'")
            elif not any("Absent state" in t[1] for t in notes):
                c["CRAFT-115"].fail(f"{where}: notes do not state the block's absent state ('Absent state:')")
        # text
        for t in kids(n, "text") + kids(n, "text_box") + kids(n, "label") + kids(n, "global_label") \
                + kids(n, "hierarchical_label") + kids(n, "directive_label") + kids(n, "netclass_flag"):
            sz = text_size(t)
            f = font_of(t)
            if sz is not None and not any(abs(sz - a) < 1e-3 for a in TEXT_SIZES):
                c["CRAFT-173"].fail(f"{where}: {t[0]} {t[1][:30]!r} at {sz} mm (set {TEXT_SIZES})")
            if f is not None and kid(f, "face") is not None:
                c["CRAFT-102"].fail(f"{where}: {t[0]} {t[1][:30]!r} in {val(f, 'face')!r}")
            if f is not None and kid(f, "color") is not None and any(float(x) for x in kid(f, "color")[1:4]):
                c["CRAFT-103"].fail(f"{where}: {t[0]} {t[1][:30]!r} has a hand-set colour")
            if t[0] in ("text", "text_box") and len(re.findall(r"\|", t[1])) >= 4 and "\n" in t[1]:
                c["CRAFT-176"].fail(f"{where}: text drawn as a table ({t[1][:30]!r}); use a KiCad table")
        for g in kids(n, "rectangle") + kids(n, "polyline") + kids(n, "wire") + kids(n, "bus") + kids(n, "text_box"):
            st = kid(g, "stroke")
            col = kid(st, "color") if st is not None else None
            fill = kid(g, "fill")
            fcol = kid(fill, "color") if fill is not None else None
            ftype = val(fill, "type") if fill is not None else None
            if (col is not None and any(float(x) for x in col[1:5])) or \
               (fcol is not None and any(float(x) for x in fcol[1:5])) or ftype == "color":
                c["CRAFT-103"].fail(f"{where}: {g[0]} with a hand-set colour")
        # grid and geometry
        def grid(obj, what):
            p = xy(obj)
            if p and not (on_grid(p[0]) and on_grid(p[1])):
                c["CRAFT-86"].fail(f"{where}: {what} at {fmt(p[0], p[1])} off the 1.27 mm grid")
            if p and round(p[2]) % 90:
                c["CRAFT-86"].fail(f"{where}: {what} rotated {p[2]:g}°")
        for sym in s.symbols():
            grid(sym, f"symbol {s.reference(sym)}")
        for kind in ("label", "global_label", "hierarchical_label", "directive_label", "netclass_flag",
                     "junction", "no_connect"):
            for o in kids(n, kind):
                grid(o, f"{kind} {o[1] if isinstance(o[1], str) else ''}".strip())
        for sn in kids(n, "sheet"):
            grid(sn, f"sheet {prop_text(sn, 'Sheetname')}")
            for pin in kids(sn, "pin"):
                grid(pin, f"sheet pin {pin[1]}")
        ends = {}
        for w in kids(n, "wire") + kids(n, "bus"):
            pts = [(float(p[1]), float(p[2])) for p in kids(kid(w, "pts"), "xy")]
            for (x1, y1) in pts:
                if not (on_grid(x1) and on_grid(y1)):
                    c["CRAFT-86"].fail(f"{where}: {w[0]} end {fmt(x1, y1)} off the 1.27 mm grid")
            if len(pts) == 2:
                (x1, y1), (x2, y2) = pts
                if abs(x1 - x2) > 1e-3 and abs(y1 - y2) > 1e-3:
                    c["CRAFT-154"].fail(f"{where}: diagonal {w[0]} {fmt(x1, y1)}–{fmt(x2, y2)}")
                for p in pts:
                    key = (round(p[0], 2), round(p[1], 2))
                    ends[key] = ends.get(key, 0) + 1
            st = kid(w, "stroke")
            if st is not None and float(val(st, "width", "0")) != 0:
                c["CRAFT-154"].fail(f"{where}: {w[0]} with a hand-set width {val(st, 'width')}")
        wires = [[(float(p[1]), float(p[2])) for p in kids(kid(w, "pts"), "xy")] for w in kids(n, "wire")]
        for j in kids(n, "junction"):
            x, y, _ = xy(j)
            count = ends.get((round(x, 2), round(y, 2)), 0)
            for seg in wires:
                if len(seg) != 2:
                    continue
                (x1, y1), (x2, y2) = seg
                inside = (min(x1, x2) + 1e-3 < x < max(x1, x2) - 1e-3 and abs(y1 - y) < 1e-3 and abs(y2 - y) < 1e-3) or \
                         (min(y1, y2) + 1e-3 < y < max(y1, y2) - 1e-3 and abs(x1 - x) < 1e-3 and abs(x2 - x) < 1e-3)
                if inside:
                    count += 2
            if count >= 4:
                c["CRAFT-153"].fail(f"{where}: four-way junction at {fmt(x, y)}")
        # symbols on the sheet
        hundreds = {}
        for sym in s.symbols():
            lib_id = val(sym, "lib_id", "")
            lib = s.lib.get(lib_id)
            ref = s.reference(sym)
            pr = props(sym)
            is_power = lib is not None and flag(lib, "power")
            if is_power and ref.startswith("#FLG"):
                continue  # PWR_FLAG marks a driven net for ERC; it names no rail
            if is_power:
                net = prop_text(sym, "Value", "")
                if net not in rails:
                    c["CRAFT-85"].fail(f"{where}: power symbol {net!r} is not a rail of the power tree")
                ang = round(xy(sym)[2]) % 360
                if GROUND.match(net) and ang != 0:
                    c["CRAFT-85"].fail(f"{where}: ground symbol {ref} rotated {ang}° (points down at 0°)")
                continue
            if ref.startswith("#"):
                continue
            pfx = ref_prefix(ref)
            m = re.match(r"[A-Za-z]+(\d+)$", ref)
            if m:
                hundreds.setdefault(int(m.group(1)) // 100, []).append(ref)
            if pfx == "U" and kid(sym, "mirror") is not None:
                c["CRAFT-133"].fail(f"{where}: {ref} mirrored")
            in_bom = flag(sym, "in_bom") if kid(sym, "in_bom") is not None else True
            on_board = flag(sym, "on_board") if kid(sym, "on_board") is not None else True
            required = [f for f in REQUIRED_FIELDS
                        if (f not in ("Manufacturer", "MPN") or in_bom) and (f != "Footprint" or on_board)]
            for f in required:
                v = prop_text(sym, f, None)
                if v is None or not v.strip() or (f == "Datasheet" and v.strip() == "~" and in_bom):
                    c["CRAFT-140"].fail(f"{where}: {ref} field {f} missing or empty")
            ds = prop_text(sym, "Datasheet", "")
            if in_bom and ds and ds != "~" and not URL.match(ds):
                c["CRAFT-140"].fail(f"{where}: {ref} Datasheet {ds!r} is not an https URL")
            v = prop_text(sym, "Value", "")
            if pfx in VALUE_UNIT and v.upper() != "DNP":
                problem = value_problem(v, VALUE_UNIT[pfx])
                if problem:
                    c["CRAFT-141"].fail(f"{where}: {ref} value {v!r}: {problem}")
            for fname, p in pr.items():
                shown = not hidden(p)
                if fname in SHOWN_FIELDS and fname != "Spec" and not shown:
                    c["CRAFT-143"].fail(f"{where}: {ref} {fname} hidden")
                if fname not in SHOWN_FIELDS and shown and p[2].strip():
                    c["CRAFT-143"].fail(f"{where}: {ref} {fname} shown")
            if bool(prop_text(sym, "MPN_2")) != bool(prop_text(sym, "Manufacturer_2")):
                c["CRAFT-144"].fail(f"{where}: {ref} MPN_2 and Manufacturer_2 must come together")
            blk = prop_text(sym, "Block")
            if s.is_block and blk and blk != s.role:
                c["CRAFT-145"].fail(f"{where}: {ref} Block {blk!r} on the sheet of {s.role}")
            if not s.is_block and blk is None:
                pass
            cons = prop_text(sym, "Constraints")
            if cons and not CONSTRAINT.match(cons):
                c["CRAFT-146"].fail(f"{where}: {ref} Constraints {cons!r} (space-separated IDs such as PCB-A-07)")
            if flag(sym, "dnp") and not prop_text(sym, "Alternate"):
                c["CRAFT-147"].fail(f"{where}: {ref} DNP in the base design without an Alternate field")
            lib_path = prop_text(sym, "Sim.Library")
            if lib_path and (os.path.isabs(lib_path) and "${" not in lib_path):
                c["CRAFT-148"].fail(f"{where}: {ref} Sim.Library {lib_path!r} is an absolute path")
        if len(hundreds) > 1 and s.is_block:
            c["CRAFT-116"].fail(f"{where}: references in {len(hundreds)} hundred-blocks: "
                                + ", ".join(f"{h * 100}–{h * 100 + 99}" for h in sorted(hundreds)))
        # labels
        for gl in kids(n, "global_label"):
            if gl[1] not in rails:
                c["CRAFT-150"].fail(f"{where}: global label {gl[1]!r} is not a rail")
        for lab in kids(n, "label") + kids(n, "global_label") + kids(n, "hierarchical_label"):
            name = lab[1]
            base = re.sub(r"\{.*\}$", "", name)
            if base in rails:
                continue
            problem = net_name_problem(name, aliases)
            if problem:
                c["CRAFT-84"].fail(f"{where}: net {name!r} {problem}")
        # hierarchical labels by side
        if s.is_block:
            xs = [xy(o)[0] for o in s.symbols()] or [0]
            mid = (min(xs) + max(xs)) / 2
            for hl in kids(n, "hierarchical_label"):
                shape = val(hl, "shape")
                x = xy(hl)[0]
                if shape == "input" and x > mid and hl[1] not in rails:
                    c["CRAFT-114"].fail(f"{where}: input {hl[1]} on the right half")
                if shape == "output" and x < mid and hl[1] not in rails:
                    c["CRAFT-114"].fail(f"{where}: output {hl[1]} on the left half")
        # rail table
        decoupling = 0
        for sym in s.symbols():
            if ref_prefix(s.reference(sym)) == "C":
                decoupling += 1
        tables = kids(n, "table")
        if s.is_block and decoupling >= 2:
            heads = []
            for t in tables:
                cells = kids(kid(t, "cells") or ["cells"], "table_cell")
                if cells:
                    heads.append(cells[0][1].strip())
            if not any(h.lower().startswith("rail") for h in heads):
                c["CRAFT-172"].fail(f"{where}: {decoupling} capacitors and no rail table (a KiCad table headed 'Rail')")
    if len(revs) > 1:
        c["CRAFT-80"].fail("revisions differ between sheets: " + "; ".join(f"{r}: {', '.join(v)}" for r, v in revs.items()))

    # ---- hierarchy: overviews, block interfaces, sheet pins
    roles = [s.role for s in design.sheets]
    for r in ("power", "interconnect"):
        if r not in roles:
            c["CRAFT-110"].fail(f"no {r} overview sheet (title block comment 1 = {r!r})")
    if root.role != "root":
        c["CRAFT-110"].fail(f"root sheet's comment 1 is {root.role!r}, not 'root'")
    on_root = [(sn, child) for parent, sn, child in design.sheet_symbols if parent is root]
    if on_root:
        def reading(item):
            x, y, _ = xy(item[0])
            return (round(y / 25.4), x)
        ordered = [child.page for _, child in sorted(on_root, key=reading)]
        nums = [int(p) for p in ordered if str(p).isdigit()]
        if nums != sorted(nums):
            c["CRAFT-111"].fail("page numbers do not follow the root's reading order (rows top to bottom, "
                                f"left to right): {', '.join(map(str, ordered))}")
    for parent, sn, child in design.sheet_symbols:
        where = f"{parent.name}: sheet {prop_text(sn, 'Sheetname')}"
        sx, sy, _ = xy(sn)
        size = kid(sn, "size")
        w, h = float(size[1]), float(size[2])
        pins = {p[1]: p for p in kids(sn, "pin")}
        labels = {hl[1]: hl for hl in kids(child.node, "hierarchical_label")}
        if set(pins) != set(labels):
            only_p = sorted(set(pins) - set(labels))
            only_l = sorted(set(labels) - set(pins))
            c["CRAFT-112"].fail(f"{where}: sheet pins and hierarchical labels differ "
                                f"(pins only: {only_p}; labels only: {only_l})")
        if child.is_block:
            iface = interfaces.get(child.role)
            if iface is None:
                c["CRAFT-112"].fail(f"{where}: no interface declared for {child.role} in the configuration")
            elif set(iface) != set(pins):
                c["CRAFT-112"].fail(f"{where}: sheet pins differ from the {child.role} interface "
                                    f"(missing: {sorted(set(iface) - set(pins))}; extra: {sorted(set(pins) - set(iface))})")
        for name, p in pins.items():
            px, py, _ = xy(p)
            side = ("left" if abs(px - sx) < 1e-3 else "right" if abs(px - (sx + w)) < 1e-3
                    else "top" if abs(py - sy) < 1e-3 else "bottom" if abs(py - (sy + h)) < 1e-3 else "?")
            shape = p[2] if len(p) > 2 else "?"
            is_rail = rail_like(name, rails)
            if GROUND.match(name):
                want = ("bottom",)
            elif is_rail and shape == "output":
                want = ("right",)
            elif is_rail:
                want = ("top",)
            elif shape == "input":
                want = ("left",)
            elif shape == "output":
                want = ("right",)
            else:
                want = ("left", "right")
            if side not in want:
                c["CRAFT-113"].fail(f"{where}: pin {name} ({shape}) on the {side} edge, not {'/'.join(want)}")
            lab = labels.get(name)
            if lab is not None and val(lab, "shape") != shape:
                c["CRAFT-151"].fail(f"{where}: pin {name} is {shape}, its hierarchical label {val(lab, 'shape')}")
        if prop_text(sn, "Block") is None and child.is_block:
            c["CRAFT-82"].fail(f"{where}: sheet symbol without a Block field")

    # ---- rails and nets the configuration names
    if cfg.get("rails"):
        unknown = power_found - set(cfg["rails"]) - {"GND"}
        for r in sorted(unknown):
            c["CRAFT-85"].fail(f"power symbol {r!r} is not in the configured rails")
    nc = cfg.get("netclasses")
    if nc:
        toml_classes = {k["name"] for k in load_toml(os.path.join(cfg_dir, nc)).get("class", [])}
        ns = design.pro.get("net_settings", {})
        pro_classes = {k["name"] for k in ns.get("classes", [])} - {"Default"}
        used = {p.get("netclass") for p in ns.get("netclass_patterns", []) or []}
        for s in design.sheets:
            for d in kids(s.node, "directive_label") + kids(s.node, "netclass_flag"):
                v = prop_text(d, "Netclass")
                if v:
                    used.add(v)
        for k in sorted(toml_classes - pro_classes):
            c["CRAFT-155"].fail(f"class {k} of netclasses.toml missing from the project")
        for k in sorted((pro_classes | used) - toml_classes - {None}):
            c["CRAFT-155"].fail(f"class {k} is not in netclasses.toml")
    else:
        c["CRAFT-155"].applicable = False
        c["CRAFT-155"].info.append("no netclasses file configured")

    # ---- embedded library symbols (each once)
    seen = set()
    for s in design.sheets:
        for name, sym in s.lib.items():
            if name not in seen:
                seen.add(name)
                check_symbol(name, sym, c)
