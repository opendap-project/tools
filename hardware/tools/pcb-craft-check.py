#!/usr/bin/env python3
"""Check a KiCad board against the parts of the OpenDAP PCB craft standard that DRC cannot check.

Usage:
    pcb-craft-check.py [--json] [--config FILE] [--target Qn] [--no-drc] BOARD.kicad_pcb

Run it with KiCad's bundled Python (it needs pcbnew); `odapkit craft check` finds that Python.
Keep it compatible with Python 3.9, the version KiCad 10 ships on macOS.

The standard is kicad-lib's docs/specs/pcb-craft-standard.md
(https://github.com/opendap-project/kicad-lib/blob/main/docs/specs/pcb-craft-standard.md). Each
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
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "craft"))
import craftrules  # noqa: E402  (tools/craft: the rules as data, tools/craft/rules.toml)

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

class Quiet:
    """Silence fd 2 while pcbnew works: without a GUI app, wxWidgets prints assertions there."""

    def __enter__(self):
        sys.stderr.flush()
        self.saved = os.dup(2)
        self.null = open(os.devnull, "w")
        os.dup2(self.null.fileno(), 2)

    def __exit__(self, *exc):
        os.dup2(self.saved, 2)
        os.close(self.saved)
        self.null.close()


try:
    with Quiet():
        import pcbnew
except ImportError:  # pragma: no cover
    sys.exit("pcb-craft-check: pcbnew not found: run it with KiCad's Python, or through "
             "`odapkit craft check`")


# ---------------------------------------------------------------- helpers

def to_mm(v):
    return pcbnew.ToMM(v)


def ref_parts(ref):
    m = re.fullmatch(r"([A-Za-z_#]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, None)


def prefix(fp):
    return ref_parts(fp.GetReference())[0].upper()


def is_on_bottom(fp):
    return fp.GetLayer() == pcbnew.B_Cu


def electrical(fp):
    return any(p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH and p.GetNumber() for p in fp.Pads())


def silk_layers():
    return (pcbnew.F_SilkS, pcbnew.B_SilkS)


def norm_deg(a):
    a = a % 360.0
    return 0.0 if abs(a - 360.0) < 1e-6 else a


def bbox_mm(box):
    return (to_mm(box.GetLeft()), to_mm(box.GetTop()), to_mm(box.GetRight()), to_mm(box.GetBottom()))


def body_box(fp):
    """Courtyard box on the footprint's side, else the pads' box."""
    layer = pcbnew.B_CrtYd if is_on_bottom(fp) else pcbnew.F_CrtYd
    try:
        cy = fp.GetCourtyard(layer)
        if not cy.IsEmpty():
            return bbox_mm(cy.BBox())
    except Exception:  # noqa: BLE001 - older API
        pass
    pads = list(fp.Pads())
    if pads:
        boxes = [bbox_mm(p.GetBoundingBox()) for p in pads]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
    p = fp.GetPosition()
    return (to_mm(p.x), to_mm(p.y), to_mm(p.x), to_mm(p.y))


def point_box_dist(x, y, box):
    dx = max(box[0] - x, 0.0, x - box[2])
    dy = max(box[1] - y, 0.0, y - box[3])
    return math.hypot(dx, dy)


def seg_point_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L))
    return math.hypot(px - ax - t * vx, py - ay - t * vy)


def angle_ok(dx, dy, tol=0.001):
    return abs(dx) < tol or abs(dy) < tol or abs(abs(dx) - abs(dy)) < tol


def shown(text_item):
    try:
        return text_item.GetShownText(False)
    except TypeError:
        return text_item.GetShownText()


def exemptions(fp):
    if not fp.HasField("Craft_Exempt"):
        return set(), ""
    text = fp.GetFieldText("Craft_Exempt")
    return set(re.findall(r"CRAFT-\d+", text)), text


class Check:
    def __init__(self, cid, level, title, enforcement="CHK"):
        self.id, self.level, self.title, self.enforcement = cid, level, title, enforcement
        self.findings, self.exempt, self.info, self.applicable = [], [], [], True

    def fail(self, msg):
        self.findings.append(msg)

    @property
    def status(self):
        if not self.applicable:
            return "n/a"
        return "fail" if self.findings else "pass"

    def as_dict(self):
        return {"id": self.id, "level": self.level, "title": self.title,
                "enforcement": self.enforcement, "status": self.status,
                "findings": self.findings, "exempt": self.exempt, "info": self.info}


# ---------------------------------------------------------------- the board

class Board:
    def __init__(self, path):
        self.path = path
        self.board = pcbnew.LoadBoard(path)
        self.fps = list(self.board.GetFootprints())
        self.exempt = {fp.GetReference(): exemptions(fp) for fp in self.fps}
        self.texts = []  # (item, owner footprint or None)
        self.shapes = []  # silk graphics: (item, owner)
        for d in self.board.GetDrawings():
            self._collect(d, None)
        for fp in self.fps:
            for f in fp.GetFields():
                self._collect(f, fp)
            for g in fp.GraphicalItems():
                self._collect(g, fp)
        with open(path, encoding="utf-8") as fh:
            self.raw = fh.read()

    def _collect(self, item, owner):
        cls = item.GetClass()
        if cls in ("PCB_TEXT", "PCB_FIELD"):
            self.texts.append((item, owner))
        elif cls == "PCB_SHAPE" and item.GetLayer() in silk_layers():
            self.shapes.append((item, owner))

    def is_exempt(self, fp, cid, check):
        ids, text = self.exempt.get(fp.GetReference(), (set(), ""))
        if cid in ids:
            check.exempt.append(f"{fp.GetReference()}: {text}")
            return True
        return False

    def silk_texts(self):
        return [(t, o) for t, o in self.texts
                if t.GetLayer() in silk_layers() and t.IsVisible() and shown(t).strip()]

    def outline_segments(self):
        segs = []
        for d in self.board.GetDrawings():
            if d.GetClass() != "PCB_SHAPE" or d.GetLayer() != pcbnew.Edge_Cuts:
                continue
            shape = d.GetShape()
            if shape == pcbnew.SHAPE_T_SEGMENT:
                segs.append((d.GetStart(), d.GetEnd()))
            elif shape == pcbnew.SHAPE_T_RECT:
                x1, y1, x2, y2 = d.GetStart().x, d.GetStart().y, d.GetEnd().x, d.GetEnd().y
                c = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                segs += [(pcbnew.VECTOR2I(*c[i]), pcbnew.VECTOR2I(*c[(i + 1) % 4])) for i in range(4)]
            else:
                pts = [d.GetStart()]
                try:
                    poly = d.GetPolyShape() if shape == pcbnew.SHAPE_T_POLY else None
                except Exception:  # noqa: BLE001
                    poly = None
                if poly is not None and poly.OutlineCount():
                    o = poly.Outline(0)
                    pts = [o.CPoint(i) for i in range(o.PointCount())] + [o.CPoint(0)]
                elif shape in (pcbnew.SHAPE_T_ARC, pcbnew.SHAPE_T_CIRCLE):
                    c = d.GetCenter()
                    r = d.GetRadius()
                    if shape == pcbnew.SHAPE_T_CIRCLE:
                        a0, a1 = 0.0, 2 * math.pi
                    else:
                        a0 = math.atan2(d.GetStart().y - c.y, d.GetStart().x - c.x)
                        a1 = a0 + math.radians(d.GetArcAngle().AsDegrees())
                    n = 64
                    pts = [pcbnew.VECTOR2I(int(c.x + r * math.cos(a0 + (a1 - a0) * i / n)),
                                           int(c.y + r * math.sin(a0 + (a1 - a0) * i / n)))
                           for i in range(n + 1)]
                segs += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        return [((to_mm(a.x), to_mm(a.y)), (to_mm(b.x), to_mm(b.y))) for a, b in segs]


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


def check_pin1(b, out):
    c = Check("CRAFT-13", "Q1", "Pin-1 and polarity marks on polarised parts")
    for fp in b.fps:
        if prefix(fp) not in POLARISED or not electrical(fp) or b.is_exempt(fp, c.id, c):
            continue
        pads = [p for p in fp.Pads() if p.GetNumber()]
        first = [p for p in pads if p.GetNumber() in ("1", "A1")]
        if not first or len(pads) < 2:
            continue
        p1 = first[0].GetPosition()
        body = body_box(fp)
        cx, cy = (body[0] + body[2]) / 2, (body[1] + body[3]) / 2
        size = max(body[2] - body[0], body[3] - body[1], 0.1)
        marked = False
        for s, owner in b.shapes:
            if owner is not fp:
                continue
            box = bbox_mm(s.GetBoundingBox())
            sx, sy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if math.hypot(sx - cx, sy - cy) < 0.1 * size:
                continue  # centred on the body: an outline, not a mark
            nearest = min(pads, key=lambda p: math.hypot(to_mm(p.GetPosition().x) - sx,
                                                         to_mm(p.GetPosition().y) - sy))
            if nearest.GetPosition() == p1:
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


def _silk_points(item):
    """Sample points (mm) on a silk item, with the half stroke to subtract."""
    cls = item.GetClass()
    if cls in ("PCB_TEXT", "PCB_FIELD"):
        x1, y1, x2, y2 = bbox_mm(item.GetBoundingBox())
        pts = []
        for i in range(11):
            f = i / 10
            pts += [(x1 + (x2 - x1) * f, y1), (x1 + (x2 - x1) * f, y2),
                    (x1, y1 + (y2 - y1) * f), (x2, y1 + (y2 - y1) * f)]
        return pts, 0.0
    w = to_mm(item.GetWidth()) / 2
    shape = item.GetShape()
    if shape == pcbnew.SHAPE_T_SEGMENT:
        a, b = item.GetStart(), item.GetEnd()
        n = max(1, int(math.hypot(to_mm(b.x - a.x), to_mm(b.y - a.y)) / 0.05))
        return [(to_mm(a.x + (b.x - a.x) * i / n), to_mm(a.y + (b.y - a.y) * i / n))
                for i in range(n + 1)], w
    x1, y1, x2, y2 = bbox_mm(item.GetBoundingBox())
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), ((x1 + x2) / 2, (y1 + y2) / 2)], 0.0


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


def _groups(b):
    """Placement groups: PCB groups, else the schematic sheet instance."""
    groups = {}
    for fp in b.fps:
        g = fp.GetParentGroup()
        if g is not None and g.GetName():
            key = "group " + g.GetName()
        else:
            path = fp.GetPath().AsString()
            key = "sheet " + (path.rsplit("/", 1)[0] or "/")
        groups.setdefault(key, []).append(fp)
    return groups


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


def _select(b, spec):
    if isinstance(spec, str) and spec.startswith("group:"):
        name = spec[len("group:"):]
        return [fp for fp in b.fps if fp.GetParentGroup() is not None and fp.GetParentGroup().GetName() == name]
    pats = [spec] if isinstance(spec, str) else list(spec)
    return [fp for fp in b.fps if any(fnmatch.fnmatchcase(fp.GetReference(), p) for p in pats)]


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
        name = sym.get("name", "symmetry")
        offset = int(sym.get("offset", 100))
        axis = sym.get("axis", "vertical")
        tol = float(sym.get("tolerance", GRID))
        pairs = []
        for fa in _select(b, sym["a"]):
            pre, num = ref_parts(fa.GetReference())
            if num is None:
                continue
            fb = by_ref.get(f"{pre}{num + offset}")
            if fb is None:
                c.fail(f"{name}: {fa.GetReference()} has no counterpart {pre}{num + offset}")
                continue
            pairs.append((fa, fb))
        if not pairs:
            c.fail(f"{name}: no parts selected by {sym['a']!r}")
            continue
        xa = [(to_mm(a.GetPosition().x), to_mm(a.GetPosition().y)) for a, _ in pairs]
        xb = [(to_mm(bb.GetPosition().x), to_mm(bb.GetPosition().y)) for _, bb in pairs]
        if axis == "vertical":
            centre = sum(p[0] + q[0] for p, q in zip(xa, xb)) / (2 * len(pairs))
        elif axis == "horizontal":
            centre = sum(p[1] + q[1] for p, q in zip(xa, xb)) / (2 * len(pairs))
        else:
            dx = sum(q[0] - p[0] for p, q in zip(xa, xb)) / len(pairs)
            dy = sum(q[1] - p[1] for p, q in zip(xa, xb)) / len(pairs)
        c.info.append(f"{name}: {len(pairs)} pairs, axis {axis}"
                      + (f" at {centre:.3f} mm" if axis in ("vertical", "horizontal") else f", shift ({dx:.3f}, {dy:.3f}) mm"))
        for (fa, fb), (ax, ay), (bx, by) in zip(pairs, xa, xb):
            if axis == "vertical":
                ex, ey = 2 * centre - ax, ay
            elif axis == "horizontal":
                ex, ey = ax, 2 * centre - ay
            else:
                ex, ey = ax + dx, ay + dy
            err = math.hypot(bx - ex, by - ey)
            label = f"{name}: {fa.GetReference()}/{fb.GetReference()}"
            if err > tol + 1e-9:
                c.fail(f"{label} off by {err:.3f} mm")
            if str(fa.GetFPID().GetLibItemName()) != str(fb.GetFPID().GetLibItemName()):
                c.fail(f"{label} use different footprints")
            if fa.GetLayer() != fb.GetLayer():
                c.fail(f"{label} on different sides")
            ra, rb = norm_deg(fa.GetOrientationDegrees()), norm_deg(fb.GetOrientationDegrees())
            if axis == "vertical":
                want = norm_deg(180.0 - ra)
            elif axis == "horizontal":
                want = norm_deg(-ra)
            else:
                want = ra
            mod = 180.0 if prefix(fa) in PASSIVES and len(list(fa.Pads())) == 2 else 360.0
            if abs((rb - want) % mod) > 1e-6 and abs((rb - want) % mod - mod) > 1e-6:
                c.fail(f"{label} rotations {ra:g}/{rb:g} degrees are not mirrored")
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
    colours = dict(re.findall(r'\(layer "([FB]\.(?:Mask|SilkS))"\s*\(type "[^"]*"\)\s*\(color "([^"]+)"\)', b.raw))
    for pair in (("F.Mask", "B.Mask"), ("F.SilkS", "B.SilkS")):
        got = [colours.get(layer) for layer in pair]
        if not all(got):
            c71.fail(f"no colour recorded for {', '.join(l for l, g in zip(pair, got) if not g)} in the stackup")
        elif got[0] != got[1]:
            c71.fail(f"{pair[0]} {got[0]} and {pair[1]} {got[1]} differ")
    if colours:
        c71.info.append(", ".join(f"{k} {v}" for k, v in sorted(colours.items())))
    out += [c60, c61, c62, c71]


# ---------------------------------------------------------------- DRC fold

def kicad_cli():
    bundled = os.path.join(KICAD_APP, "Contents", "MacOS", "kicad-cli")
    return bundled if os.path.exists(bundled) else shutil.which("kicad-cli")


def fold_drc(path, out):
    cli = kicad_cli()
    if not cli:
        c = Check("DRC", "Q0", "kicad-cli DRC", "DRC")
        c.fail("kicad-cli not found (set KICAD_APP or use --no-drc)")
        out.append(c)
        return
    with tempfile.TemporaryDirectory() as tmp:
        rep = os.path.join(tmp, "drc.json")
        subprocess.run([cli, "pcb", "drc", "--format", "json", "--severity-all", "--units", "mm",
                        "--output", rep, path], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with open(rep, encoding="utf-8") as fh:
            report = json.load(fh)
    tagged = {}
    dru = path[: -len(".kicad_pcb")] + ".kicad_dru"
    if os.path.exists(dru):
        with open(dru, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*\(rule\s+"([^"]*\[(Q[0-4])\][^"]*)"', line)
                if m:
                    tagged.setdefault(m.group(1), m.group(2))
    rules = {}
    for name, level in tagged.items():
        cid = re.search(r"([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)", name)
        rules[name] = Check(cid.group(1) if cid else name, level, name.split(": ", 1)[-1], "DRC")
    untagged_err = Check("DRC", "Q0", "Fab and electrical DRC (untagged errors, unconnected items)", "DRC")
    warn = Check("DRC-W", "Q4", "Untagged DRC warnings (listed, gate nothing)", "DRC")
    warn.applicable = False
    for v in report.get("violations", []):
        m = re.search(r"\(rule '([^']+)'", v["description"])
        where = ", ".join(i["description"] for i in v.get("items", []))
        name = m.group(1) if m else None
        level = re.search(r"\[(Q[0-4])\]", name).group(1) if name and re.search(r"\[Q[0-4]\]", name) else None
        if level:
            if name not in rules:
                rules[name] = Check(name, level, name, "DRC")
            rules[name].fail(where)
        elif v.get("severity") == "error":
            untagged_err.fail(f"{v['type']}: {where}")
        else:
            warn.info.append(f"{v['type']}: {where}")
    for u in report.get("unconnected_items", []):
        untagged_err.fail("unconnected: " + ", ".join(i["description"] for i in u.get("items", [])))
    out += list(rules.values()) + [untagged_err, warn]


# ---------------------------------------------------------------- report

def load_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ImportError:
        py = os.environ.get("ODAPKIT_PYTHON3", "python3")
        res = subprocess.run([py, "-c", "import json,sys,tomllib;print(json.dumps(tomllib.load(open(sys.argv[1],'rb'))))", path],
                             capture_output=True, text=True)
        if res.returncode:
            sys.exit(f"pcb-craft-check: cannot read {path}: {res.stderr.strip()}")
        return json.loads(res.stdout)


def reached(results):
    level = None
    for lv in LEVELS:
        if any(r.status == "fail" and r.level == lv for r in results):
            break
        level = lv
    return level


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

    def order(r):
        m = re.search(r"-(\d+)$", r.id)
        return (r.enforcement != "CHK", int(m.group(1)) if m else 0, r.id)
    results.sort(key=order)

    level = reached(results)
    gate = [r for r in results if r.status == "fail" and LEVELS.index(r.level) <= LEVELS.index(target)]
    debt = [r for r in results if r.status == "fail" and LEVELS.index(r.level) > LEVELS.index(target)]
    if as_json:
        print(json.dumps({
            "schema": "opendap.craft-check/v1", "board": path, "spec": SPEC,
            "config": cfg_path if os.path.exists(cfg_path) else None, "drc": not no_drc,
            "target": target, "reached": level, "target_met": not gate,
            "debt": [r.id for r in debt],
            "levels": {lv: [r.as_dict() for r in results if r.level == lv] for lv in LEVELS},
        }, indent=2))
    else:
        print(f"odapkit craft check: {os.path.relpath(path)}")
        for lv in LEVELS:
            group = [r for r in results if r.level == lv]
            if not group:
                continue
            print(f"{lv}")
            for r in group:
                mark = {"pass": "pass", "fail": "FAIL", "n/a": "n/a "}[r.status]
                print(f"  {mark}  {r.id:9} {r.title}  [{r.enforcement}]")
                for f in r.findings[:12]:
                    print(f"          - {f}")
                if len(r.findings) > 12:
                    print(f"          - ... {len(r.findings) - 12} more")
                for e in r.exempt:
                    print(f"          exempt: {e}")
                for n in r.info[:12]:
                    print(f"          {n}")
                if len(r.info) > 12:
                    print(f"          ... {len(r.info) - 12} more (--json lists all)")
        print(f"Level reached: {level or 'none (a Q0 item fails)'}. Target {target}: "
              f"{'met' if not gate else 'NOT met'}"
              f"{f'; {len(debt)} item(s) of debt above it' if debt else ''}.")
    return 1 if gate else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
