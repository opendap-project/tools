"""The board model of the PCB craft check (tools/pcb-craft-check.py): KiCad's pcbnew loaded on demand, the
geometry and footprint helpers, a check's result (Check) and the board as the checks read it (Board).

The limits come from the rules data through the check script: bind(names) gives this module the script's
constants (LEVELS, GRID, PASSIVES…), so they are read from tools/craft/rules.toml in one place. Python 3.9.
"""
from __future__ import annotations

import fnmatch
import math
import os
import re
import sys

pcbnew = None  # KiCad's module, imported by load_pcbnew() once a board is checked
GRID = PASSIVES = None  # bound from the check script (bind)


def bind(names):
    """Take the check script's upper-case constants (its limits and sets)."""
    globals().update({k: v for k, v in names.items() if k.isupper()})


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


pcbnew = None  # KiCad's module, imported once a board is checked: --help and usage errors need no KiCad


def load_pcbnew():
    global pcbnew
    try:
        with Quiet():
            import pcbnew as module
    except ImportError:  # pragma: no cover
        sys.exit("pcb-craft-check: pcbnew not found: run it with KiCad's Python, or through "
                 "`odapkit craft check`")
    pcbnew = module


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


def _select(b, spec):
    if isinstance(spec, str) and spec.startswith("group:"):
        name = spec[len("group:"):]
        return [fp for fp in b.fps if fp.GetParentGroup() is not None and fp.GetParentGroup().GetName() == name]
    pats = [spec] if isinstance(spec, str) else list(spec)
    return [fp for fp in b.fps if any(fnmatch.fnmatchcase(fp.GetReference(), p) for p in pats)]


def symmetry(b, sym, by_ref, c):
    """One [[symmetry]] table (CRAFT-34): its pairs, the mirror axis or shift, and the findings in c."""
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
        return
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
