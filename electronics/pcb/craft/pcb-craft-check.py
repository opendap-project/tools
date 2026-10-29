#!/usr/bin/env python3
"""Check a KiCad board against the parts of the OpenDAP PCB craft standard that DRC cannot check.

Usage:
    pcb-craft-check.py [--json] [--config FILE] [--target Qn] [--no-drc] BOARD.kicad_pcb

Run it with KiCad's bundled Python (it needs pcbnew); `odw craft check` finds that Python.
Keep it compatible with Python 3.9, the version KiCad 10 ships on macOS.

The standard is kicad-lib's docs/specs/pcb-craft-standard.md
(https://github.com/opendap-project/libs/blob/main/kicad/docs/specs/pcb-craft-standard.md). Each
check carries its CRAFT ID and its quality level (RFC 0010, Q0 to Q4). Unless --no-drc, the board's
DRC (kicad-cli, with its own <project>.kicad_dru) is folded in: a rule whose name carries "[Qn]"
counts at that level; any other DRC error and every unconnected item count at Q0 (the fab and
electrical rules); other DRC warnings are listed but gate nothing.

The report groups results by level and prints the level the board reaches: the highest level at
which every item at or below it passes. Items above the target are debt, not failures.

Configuration, optional, next to the board as <project>.craft.toml (or --config):

    version = 1
    target = "Q1"                 # the level this build targets (default Q1)
    oshwa_uid = "XX000000"        # only once OSHWA has certified the board (CRAFT-23)

    [[symmetry]]                  # CRAFT-34, one table per mirrored pair of blocks
    name = "Headphone amplifier L/R"
    a = ["R1??", "C1??", "U1??"]  # references of block A (fnmatch patterns), or "group:NAME"
    offset = 100                  # block B reference = block A reference number + offset
    axis = "vertical"             # vertical (mirror about x = c), horizontal (about y = c),
                                  # or none (B is A translated)
    tolerance = 0.05              # mm (default 0.05)

Footprint exemptions: a footprint field Craft_Exempt = "CRAFT-30 CRAFT-31: reason" exempts that
footprint from the named checks. Exemptions are listed in the report.

Exit status: 0 every item at or below the target passes, 1 otherwise, 2 usage error.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # this check's models and report
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "craft"))  # craftrules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "kicad"))  # core/kicad: the S-expression model (HL-71)
import craftrules  # noqa: E402  (tools/craft: the rules as data, tools/craft/rules.toml)
import pcbmodel  # noqa: E402  (tools/craft: the board model and its helpers)
import pcbreport  # noqa: E402  (tools/craft: the DRC fold, the configuration and the report)
import pin1  # noqa: E402  (tools/craft: the CRAFT-13 geometry, "next to pin 1")

RULES = craftrules.load()
LEVELS = RULES.levels
GRID = RULES.limit("CRAFT-30", "placement_grid_mm")
TEXT_SET = tuple(tuple(p) for p in RULES.limit("CRAFT-02", "text_set_mm"))
SILK_EDGE = RULES.limit("CRAFT-08", "silk_to_edge_mm")
REF_NEAR = RULES.limit("CRAFT-12", "reference_to_courtyard_max_mm")
MAX_DRILLS = RULES.limit("CRAFT-46", "drill_sizes_max")
ZONE_MIN_WIDTH = RULES.limit("CRAFT-50", "zone_min_width_mm")
PASSIVES = ("R", "C", "L", "FB")
POLARISED = ("U", "IC", "D", "LED", "Q", "J", "P", "BT", "Y")
MOUNTING = ("H", "MH")
# Impedance-controlled net classes, named <name>_<ohms>D or <name>_<ohms>SE (as the T1 board's
# USB3_90D and PCIE_85D): the same patterns exempt them in the craft DRC rules.
IMPEDANCE = ("*_??D", "*_???D", "*_??SE", "*_???SE")
HOLDER = RULES.limit("CRAFT-21", "holder")
LICENCES = tuple(RULES.limit("CRAFT-21", "licences"))
KICAD_APP = os.environ.get("KICAD_APP", "/Applications/KiCad/KiCad.app")
SPEC = RULES.spec
pcbnew = None  # KiCad's module, set by main() once a board is checked
for _m in (pcbmodel, pcbreport):
    _m.bind(globals())
from pcbmodel import (Board, Check, Quiet, _groups, _silk_points, angle_ok, bbox_mm, body_box,  # noqa: E402
                      electrical, is_on_bottom, norm_deg, point_box_dist, prefix, seg_point_dist, shown,
                      silk_layers, stackup_colours, to_mm)
from pcbreport import fold_drc, load_config, order, render  # noqa: E402

# ---------------------------------------------------------------- checks

def check_refdes(b, out):
    c10 = Check("CRAFT-10", "Q2", "Every part has a visible reference designator on its side's silk")
    c11 = Check("CRAFT-11", "Q2", "Every part has ${REFERENCE} on its fabrication layer")
    c12 = Check("CRAFT-12", "Q3", "Reference designators one size per side, beside their part")
    sizes = {"top": {}, "bottom": {}}
    bodies = {fp.GetReference(): body_box(fp) for fp in b.fps}
    for fp in b.fps:
        if not electrical(fp):
            continue
        ref = fp.Reference()
        side_silk = pcbnew.B_SilkS if is_on_bottom(fp) else pcbnew.F_SilkS
        side_fab = pcbnew.B_Fab if is_on_bottom(fp) else pcbnew.F_Fab
        if not b.is_exempt(fp, "CRAFT-10", c10):
            if not ref.IsVisible() or ref.GetLayer() != side_silk:
                c10.fail(f"{fp.GetReference()}: reference hidden or not on its side's silk")
        if not b.is_exempt(fp, "CRAFT-11", c11):
            fab = [g for g in fp.GraphicalItems() if g.GetClass() == "PCB_TEXT"
                   and g.GetText() == "${REFERENCE}" and g.GetLayer() == side_fab]
            if not fab:
                c11.fail(f"{fp.GetReference()}: no ${{REFERENCE}} text on its fabrication layer")
        if ref.IsVisible() and ref.GetLayer() in silk_layers() and not b.is_exempt(fp, "CRAFT-12", c12):
            side = "bottom" if ref.GetLayer() == pcbnew.B_SilkS else "top"
            h = round(to_mm(ref.GetTextHeight()), 3)
            sizes[side].setdefault(h, []).append(fp.GetReference())
            pos = ref.GetPosition()
            x, y = to_mm(pos.x), to_mm(pos.y)
            own = point_box_dist(x, y, bodies[fp.GetReference()])
            if own > REF_NEAR:
                c12.fail(f"{fp.GetReference()}: reference {own:.2f} mm from its part (max {REF_NEAR} mm)")
            else:
                for other, box in bodies.items():
                    if other != fp.GetReference() and point_box_dist(x, y, box) < own - 1e-6:
                        c12.fail(f"{fp.GetReference()}: reference closer to {other} than to its own part")
                        break
    for side, hs in sizes.items():
        if len(hs) > 1:
            c12.fail(f"{side} reference designators in {len(hs)} sizes: "
                     + ", ".join(f"{h} mm ({len(r)})" for h, r in sorted(hs.items())))
    out += [c10, c11, c12]


def fab_body(fp):
    """The part's body for CRAFT-13: its fabrication outline's box, else its courtyard box."""
    layer = pcbnew.B_Fab if is_on_bottom(fp) else pcbnew.F_Fab
    boxes = [bbox_mm(g.GetBoundingBox()) for g in fp.GraphicalItems()
             if g.GetClass() == "PCB_SHAPE" and g.GetLayer() == layer]
    if not boxes:
        return body_box(fp)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def check_pin1(b, out):
    c = Check("CRAFT-13", "Q1", "Pin-1 and polarity marks on polarised parts")
    for fp in b.fps:
        if prefix(fp) not in POLARISED or not electrical(fp) or b.is_exempt(fp, c.id, c):
            continue
        pads = [p for p in fp.Pads() if p.GetNumber()]
        first = [p for p in pads if p.GetNumber() in ("1", "A1")]
        if not first or len(pads) < 2:
            continue
        body = fab_body(fp)
        lands = [(p.GetNumber(), bbox_mm(p.GetBoundingBox())) for p in pads]
        marked = False
        for s, owner in b.shapes:
            if owner is not fp:
                continue
            box = bbox_mm(s.GetBoundingBox())
            centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            if pin1.next_to_pin1(centre, body, lands, first[0].GetNumber()):
                marked = True
                break
        if not marked:
            c.fail(f"{fp.GetReference()}: no silk mark next to pin {first[0].GetNumber()}")
    out.append(c)


def check_power_marks(b, out):
    c = Check("CRAFT-14", "Q1", "Battery and power-input connectors marked + and −")
    texts = [(t, o) for t, o in b.silk_texts()]
    for fp in b.fps:
        role = fp.GetFieldText("Craft_Role") if fp.HasField("Craft_Role") else ""
        if not (prefix(fp) == "BT" or re.search(r"battery|power-input", role)):
            continue
        if b.is_exempt(fp, c.id, c):
            continue
        box = body_box(fp)
        near = [shown(t).strip() for t, _ in texts
                if point_box_dist(to_mm(t.GetPosition().x), to_mm(t.GetPosition().y), box) <= 3.0]
        if "+" not in near:
            c.fail(f"{fp.GetReference()}: no '+' silk mark within 3 mm")
    out.append(c)


def check_silk_edge(b, out):
    c = Check("CRAFT-08", "Q2", f"Silk {SILK_EDGE} mm from the board edge")
    segs = b.outline_segments()
    if not segs:
        c.fail("no board outline on Edge.Cuts")
        out.append(c)
        return
    poly = pcbnew.SHAPE_POLY_SET()
    try:
        have_poly = b.board.GetBoardPolygonOutlines(poly, True)
    except TypeError:
        have_poly = b.board.GetBoardPolygonOutlines(poly)
    items = [(t, o) for t, o in b.silk_texts()] + list(b.shapes)
    for item, owner in items:
        pts, half = _silk_points(item)
        worst = min(min(seg_point_dist(px, py, a[0], a[1], e[0], e[1]) for a, e in segs)
                    for px, py in pts) - half
        inside = True
        if have_poly:
            inside = all(poly.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(px), pcbnew.FromMM(py)))
                         for px, py in pts)
        if worst < SILK_EDGE - 1e-6 or not inside:
            what = shown(item) if item.GetClass() in ("PCB_TEXT", "PCB_FIELD") else item.GetShapeStr()
            who = f"{owner.GetReference()} " if owner else ""
            c.fail(f"{who}{what!r} on {item.GetLayerName()}: "
                   + (f"{worst:.2f} mm from the edge" if inside else "outside the board"))
    out.append(c)


def check_markings(b, cfg, out):
    tb = b.board.GetTitleBlock()
    title, rev, date, company = tb.GetTitle(), tb.GetRevision(), tb.GetDate(), tb.GetCompany()
    silk = " | ".join(shown(t) for t, o in b.silk_texts() if o is None)
    c20 = Check("CRAFT-20", "Q1", "Board name and revision on the silk")
    if not title or title not in silk:
        c20.fail("board name (title block title) not on the silk" if title else "title block has no title")
    if not rev or not re.search(re.escape(rev), silk):
        c20.fail("revision (title block revision) not on the silk" if rev else "title block has no revision")
    c21 = Check("CRAFT-21", "Q2", "Date, copyright holder and licence on the silk")
    if not date or date not in silk:
        c21.fail("issue date (title block date) not on the silk")
    if HOLDER not in silk:
        c21.fail(f"'{HOLDER}' not on the silk")
    if not any(lic in silk for lic in LICENCES):
        c21.fail("licence (CERN-OHL-S-2.0 or CERN-OHL-W-2.0) not on the silk")
    c22 = Check("CRAFT-22", "Q3", "Source location and the OpenDAP mark on the board")
    if not re.search(r"github\.com/opendap-project/[\w.-]+", silk):
        c22.fail("source location (github.com/opendap-project/<repository>) not on the silk")
    marks = [fp for fp in b.fps if re.match(r"OpenDAP_(Mark|Logo|Wordmark)", str(fp.GetFPID().GetLibItemName()))]
    if not marks:
        c22.fail("no OpenDAP mark footprint (OpenDAP_Mark*, OpenDAP_Logo*, OpenDAP_Wordmark*)")
    c23 = Check("CRAFT-23", "Q1", "OSHW mark only with an OSHWA certification UID")
    oshw_fp = [fp.GetReference() for fp in b.fps if "OSHW" in str(fp.GetFPID().GetLibItemName()).upper()]
    oshw_txt = "OSHW" in silk.upper()
    uid = cfg.get("oshwa_uid", "")
    if (oshw_fp or oshw_txt):
        if not re.fullmatch(r"[A-Z]{2}\d{1,6}", uid or ""):
            c23.fail("OSHW mark on the board but no OSHWA UID in the configuration (oshwa_uid)")
        elif uid not in silk:
            c23.fail(f"OSHWA UID {uid} not on the silk next to the mark")
    else:
        c23.info.append("no OSHW mark")
    c24 = Check("CRAFT-24", "Q2", "Title block complete")
    if not title:
        c24.fail("no title")
    if not rev:
        c24.fail("no revision")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        c24.fail(f"date {date!r} is not ISO 8601 (YYYY-MM-DD)")
    if company != HOLDER:
        c24.fail(f"company {company!r} is not '{HOLDER}'")
    out += [c20, c21, c22, c23, c24]


def check_placement(b, out):
    c30 = Check("CRAFT-30", "Q2", f"Footprints on the {GRID} mm placement grid")
    c31 = Check("CRAFT-31", "Q2", "Footprint rotations in multiples of 90 degrees")
    c32 = Check("CRAFT-32", "Q3", "Passives in a group share one orientation")
    c33 = Check("CRAFT-33", "Q3", "Passives in a group aligned in rows or columns")
    origin = b.board.GetDesignSettings().GetGridOrigin()
    step = pcbnew.FromMM(GRID)
    for fp in b.fps:
        pos = fp.GetPosition()
        if not b.is_exempt(fp, c30.id, c30):
            if (pos.x - origin.x) % step or (pos.y - origin.y) % step:
                c30.fail(f"{fp.GetReference()} at ({to_mm(pos.x):.4f}, {to_mm(pos.y):.4f}) mm")
        if not b.is_exempt(fp, c31.id, c31):
            rot = norm_deg(fp.GetOrientationDegrees())
            if abs(rot / 90.0 - round(rot / 90.0)) > 1e-6:
                c31.fail(f"{fp.GetReference()} at {rot:g} degrees")
    for key, fps in sorted(_groups(b).items()):
        passives = [fp for fp in fps if prefix(fp) in PASSIVES and len(list(fp.Pads())) == 2]
        if len(passives) < 2:
            continue
        orient = {}
        for fp in passives:
            if b.is_exempt(fp, c32.id, c32):
                continue
            orient.setdefault(round(norm_deg(fp.GetOrientationDegrees())) % 180, []).append(fp.GetReference())
        if len(orient) > 1:
            c32.fail(f"{key}: " + "; ".join(f"{a} deg: {', '.join(sorted(r))}" for a, r in sorted(orient.items())))
        pos = {fp.GetReference(): (fp.GetPosition().x, fp.GetPosition().y) for fp in passives}
        tol = pcbnew.FromMM(0.01)
        for fp in passives:
            if b.is_exempt(fp, c33.id, c33):
                continue
            x, y = pos[fp.GetReference()]
            if not any(r != fp.GetReference() and (abs(px - x) <= tol or abs(py - y) <= tol)
                       for r, (px, py) in pos.items()):
                c33.fail(f"{key}: {fp.GetReference()} shares no row or column with another passive")
    out += [c30, c31, c32, c33]


def check_symmetry(b, cfg, out):
    c = Check("CRAFT-34", "Q2", "Mirrored channels laid out symmetrically")
    pairs_cfg = cfg.get("symmetry", [])
    if not pairs_cfg:
        c.applicable = False
        c.info.append("no [[symmetry]] in the configuration")
        out.append(c)
        return
    by_ref = {fp.GetReference(): fp for fp in b.fps}
    for sym in pairs_cfg:
        pcbmodel.symmetry(b, sym, by_ref, c)
    out.append(c)


def check_mounting(b, out):
    c = Check("CRAFT-36", "Q3", "One mounting-hole (screw) size per board")
    holes = {}
    for fp in b.fps:
        if prefix(fp) in MOUNTING and not b.is_exempt(fp, c.id, c):
            holes.setdefault(str(fp.GetFPID().GetLibItemName()), []).append(fp.GetReference())
    if len(holes) > 1:
        c.fail("; ".join(f"{k}: {', '.join(sorted(v))}" for k, v in sorted(holes.items())))
    out.append(c)


def check_copper(b, out):
    c42 = Check("CRAFT-42", "Q3", "Arcs only on impedance-controlled net classes")
    c44 = Check("CRAFT-44", "Q3", "At most two track widths per net class")
    c46 = Check("CRAFT-46", "Q3", f"At most {MAX_DRILLS} mechanical drill sizes")
    c48 = Check("CRAFT-48", "Q3", "One teardrop policy for the board")
    c51 = Check("CRAFT-51", "Q3", "Stitching vias of one size, on the placement grid")
    widths, drills, vias, ends = {}, set(), {}, set()
    tracks = list(b.board.GetTracks())
    for t in tracks:
        cls = t.GetClass()
        if cls in ("PCB_TRACK", "PCB_ARC"):
            ends.add((t.GetNetCode(), t.GetStart().x, t.GetStart().y))
            ends.add((t.GetNetCode(), t.GetEnd().x, t.GetEnd().y))
        nc = t.GetNetClassName()
        if cls == "PCB_ARC" and not any(fnmatch.fnmatchcase(nc, p) for p in IMPEDANCE):
            c42.fail(f"arc on net {t.GetNetname() or '(no net)'} (class {nc})")
        if cls == "PCB_TRACK":
            widths.setdefault(nc, set()).add(round(to_mm(t.GetWidth()), 4))
    teardrops = {}
    for t in tracks:
        if t.GetClass() != "PCB_VIA":
            continue
        key = (round(to_mm(t.GetDrillValue()), 4), round(to_mm(t.GetWidth(pcbnew.F_Cu)), 4))
        micro = t.GetViaType() == pcbnew.VIATYPE_MICROVIA
        vias.setdefault(("micro " if micro else "") + f"{key[0]}/{key[1]}", 0)
        vias[("micro " if micro else "") + f"{key[0]}/{key[1]}"] += 1
        if not micro:
            drills.add(key[0])
        connected = (t.GetNetCode(), t.GetPosition().x, t.GetPosition().y) in ends
        if connected:
            teardrops.setdefault(bool(t.GetTeardropsEnabled()), []).append("via")
    for nc, ws in sorted(widths.items()):
        if len(ws) > 2:
            c44.fail(f"class {nc}: {len(ws)} widths ({', '.join(str(w) for w in sorted(ws))} mm)")
    for fp in b.fps:
        for p in fp.Pads():
            if p.HasHole() if hasattr(p, "HasHole") else p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                ds = p.GetDrillSize()
                if ds.x > 0:
                    drills.add(round(to_mm(min(ds.x, ds.y)), 4) if ds.x == ds.y
                               else f"slot {round(to_mm(ds.x), 4)}x{round(to_mm(ds.y), 4)}")
            if (p.GetNetCode(), p.GetPosition().x, p.GetPosition().y) in ends:
                teardrops.setdefault(bool(p.GetTeardropsEnabled()), []).append("pad")
    if len(drills) > MAX_DRILLS:
        c46.fail(f"{len(drills)} drill sizes")
    c46.info.append("drills (mm): " + ", ".join(str(d) for d in sorted(drills, key=str)))
    c46.info.append("vias (drill/land mm: count): " + ", ".join(f"{k}: {v}" for k, v in sorted(vias.items())))
    if len(teardrops) > 1:
        c48.fail(f"teardrops on {len(teardrops[True])} and off on {len(teardrops[False])} track junctions")
    stitch = {}
    step = pcbnew.FromMM(GRID)
    origin = b.board.GetDesignSettings().GetGridOrigin()
    for t in tracks:
        if t.GetClass() != "PCB_VIA":
            continue
        if (t.GetNetCode(), t.GetPosition().x, t.GetPosition().y) in ends:
            continue
        key = f"{round(to_mm(t.GetDrillValue()), 4)}/{round(to_mm(t.GetWidth(pcbnew.F_Cu)), 4)}"
        stitch.setdefault(key, 0)
        stitch[key] += 1
        p = t.GetPosition()
        if (p.x - origin.x) % step or (p.y - origin.y) % step:
            c51.fail(f"stitching via at ({to_mm(p.x):.4f}, {to_mm(p.y):.4f}) mm is off the grid")
    if len(stitch) > 1:
        c51.fail("stitching vias in several sizes: " + ", ".join(f"{k} ({v})" for k, v in sorted(stitch.items())))
    out += [c42, c44, c46, c48, c51]


def check_zones(b, out):
    c49 = Check("CRAFT-49", "Q2", "Copper zones remove islands")
    c50 = Check("CRAFT-50", "Q3", f"Copper zones: minimum width {ZONE_MIN_WIDTH} mm, outlines at 45-degree multiples")
    for z in b.board.Zones():
        if z.GetIsRuleArea():
            continue
        name = z.GetZoneName() or z.GetNetname() or "zone"
        if z.GetIslandRemovalMode() == pcbnew.ISLAND_REMOVAL_MODE_NEVER:
            c49.fail(f"{name}: island removal is off")
        if to_mm(z.GetMinThickness()) < ZONE_MIN_WIDTH - 1e-9:
            c50.fail(f"{name}: minimum width {to_mm(z.GetMinThickness()):g} mm")
        ol = z.Outline()
        for k in range(ol.OutlineCount()):
            o = ol.Outline(k)
            n = o.PointCount()
            for i in range(n):
                a, e = o.CPoint(i), o.CPoint((i + 1) % n)
                if not angle_ok(to_mm(e.x - a.x), to_mm(e.y - a.y)):
                    c50.fail(f"{name}: outline edge at ({to_mm(a.x):.3f}, {to_mm(a.y):.3f}) is not at a 45-degree multiple")
                    break
    out += [c49, c50]


def check_docs(b, out):
    c60 = Check("CRAFT-60", "Q2", "Fabrication notes on the board's documentation layers")
    c61 = Check("CRAFT-61", "Q2", "Stackup table on the board's documentation layers")
    c62 = Check("CRAFT-62", "Q2", "Board dimensions on User.Drawings")
    c71 = Check("CRAFT-71", "Q2", "Mask and silk colours recorded in the stackup, both sides alike")
    user = (pcbnew.Cmts_User, pcbnew.Dwgs_User)
    notes = [d for d in b.board.GetDrawings() if d.GetClass() in ("PCB_TEXT", "PCB_TEXTBOX")
             and d.GetLayer() in user and re.search(r"fabrication notes|fab notes", shown(d), re.I)]
    if not notes:
        c60.fail("no text titled 'Fabrication notes' on User.Comments or User.Drawings")
    tables = [g for g in b.board.Groups() if "stackup" in g.GetName().lower()]
    tables += [d for d in b.board.GetDrawings() if d.GetClass() == "PCB_TABLE" and d.GetLayer() in user]
    if not tables:
        c61.fail("no stackup table (Place > Add Stackup Table) on a User layer")
    dims = [d for d in b.board.GetDrawings() if d.GetClass().startswith("PCB_DIM_") and d.GetLayer() == pcbnew.Dwgs_User]
    if len(dims) < 2:
        c62.fail(f"{len(dims)} dimension(s) on User.Drawings, need at least 2 (outline width and height)")
    colours = stackup_colours(b.raw)
    for pair in (("F.Mask", "B.Mask"), ("F.SilkS", "B.SilkS")):
        got = [colours.get(layer) for layer in pair]
        if not all(got):
            c71.fail(f"no colour recorded for {', '.join(l for l, g in zip(pair, got) if not g)} in the stackup")
        elif got[0] != got[1]:
            c71.fail(f"{pair[0]} {got[0]} and {pair[1]} {got[1]} differ")
    if colours:
        c71.info.append(", ".join(f"{k} {v}" for k, v in sorted(colours.items())))
    out += [c60, c61, c62, c71]


def main(argv):
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    as_json = "--json" in args
    no_drc = "--no-drc" in args
    cfg_path, target = None, None
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--json", "--no-drc"):
            pass
        elif a in ("--config", "--target"):
            if i + 1 >= len(args):
                print(f"pcb-craft-check: {a} needs a value", file=sys.stderr)
                return 2
            if a == "--config":
                cfg_path = args[i + 1]
            else:
                target = args[i + 1]
            i += 1
        elif a.startswith("-"):
            print(f"pcb-craft-check: unknown option {a}", file=sys.stderr)
            return 2
        else:
            rest.append(a)
        i += 1
    if len(rest) != 1 or not rest[0].endswith(".kicad_pcb"):
        print("usage: pcb-craft-check.py [--json] [--config FILE] [--target Qn] [--no-drc] BOARD.kicad_pcb",
              file=sys.stderr)
        return 2
    path = os.path.abspath(rest[0])
    if not os.path.exists(path):
        print(f"pcb-craft-check: {path} not found", file=sys.stderr)
        return 2
    global pcbnew
    pcbmodel.load_pcbnew()
    pcbnew = pcbmodel.pcbnew
    cfg_path = cfg_path or path[: -len(".kicad_pcb")] + ".craft.toml"
    cfg = load_config(cfg_path)
    target = target or cfg.get("target", "Q1")
    if target not in LEVELS:
        print(f"pcb-craft-check: target {target!r} is not one of {', '.join(LEVELS)}", file=sys.stderr)
        return 2

    results = []
    with Quiet():
        b = Board(path)
        check_refdes(b, results)
        check_pin1(b, results)
        check_power_marks(b, results)
        check_silk_edge(b, results)
        check_markings(b, cfg, results)
        check_placement(b, results)
        check_symmetry(b, cfg, results)
        check_mounting(b, results)
        check_copper(b, results)
        check_zones(b, results)
        check_docs(b, results)
    if not no_drc:
        fold_drc(path, results)

    results.sort(key=order)

    gate = render(path, results, target, as_json, cfg_path, no_drc)
    return 1 if gate else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
