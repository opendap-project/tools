"""odapkit stackup: layer stack and impedance-controlled trace geometry of a PCB.

Usage:
    odapkit stackup stack    STACKUP.toml [--json]
    odapkit stackup design   STACKUP.toml [--only CLASS]... [--json]
    odapkit stackup check    STACKUP.toml [--only CLASS]... [--json] [--no-corners]
    odapkit stackup validate [--json]
    odapkit stackup coupling STACKUP.toml [--json]
    odapkit stackup explore  EXPLORE.toml [--csv OUT.csv] [--json] [--set KEY=VALUE]...
    odapkit stackup pdn      PDN.toml [--json]

stack     prints the layer table: z positions, total thickness (laminate and finished), and the
          [[distance]] and [thickness] checks of the file.
design    solves each [[class]] for its target: the free dimension named by `solve` ("width",
          or "gap" for a pair) is searched with the others fixed; prints the geometry to record.
check     recomputes every class at its recorded geometry: nominal impedance within
          target +- design_window_pct (default 2 %), the recorded z_ohm not stale (0.5 ohm),
          and the fab corners ([tolerance]) inside target +- tolerance_pct (a corner outside is
          reported as a warning, not a failure, since the fab trims widths to its coupon).
coupling  solves each [[coupling]] case: two traces on two routing layers between the same pair of
          reference planes (a dual stripline), at a lateral offset; reports each trace's
          impedance with the other present and the near-end (backward) crosstalk coefficient.
explore   enumerates candidate stackups of a zoned board (tools/stackup/explore.py) and ranks them;
          [[variant]] tables run several sets of constraints in one matrix.
pdn       a lumped PDN model per rail and plane geometry (tools/stackup/pdn.py): |Z(f)| against
          the rail's target impedance, to size the plane-to-ground distance.
validate  runs the solver against closed forms: Cohn's exact stripline and Hammerstad-Jensen
          microstrip (as in Wadell), plus IPC-2141A for comparison.

STACKUP.toml format: docs/specs/stackup-method.md in toolkit-hardware.
Exit status: 0 pass, 1 a check failed, 2 usage or file error.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import sys
import tomllib
from dataclasses import dataclass

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

try:  # numpy, scipy and scikit-fem: tools/stackup's own environment (uv run, as odapkit stackup runs it)
    import analytic  # noqa: E402
    import fieldsolver as fs  # noqa: E402
    MISSING = None
except ImportError as e:  # elsewhere --help and usage errors still work; a command says what to run
    analytic = fs = None
    MISSING = e

COUPLING_KEYS = {"name", "layers", "reference", "width_um", "offset_um", "classes", "notes"}
CLASS_KEYS = {"name", "type", "target_ohm", "tolerance_pct", "design_window_pct", "layer",
              "reference", "width_um", "gap_um", "solve", "coplanar_gap_um", "z_ohm",
              "constraints", "notes", "cutout_um", "min_width_um", "max_width_um", "role", "netclasses"}
LAYER_KEYS = {"name", "type", "thickness_um", "role", "material", "er", "df", "base_um",
              "notes", "fills"}


class StackupError(Exception):
    pass


# ---------------------------------------------------------------- stack model

@dataclass
class Layer:
    name: str
    type: str          # copper | dielectric | mask
    t: float           # µm
    er: float = 1.0
    role: str = ""
    material: str = ""
    fills: str = ""    # for a copper layer: "above" or "below", the dielectric pressed around its traces
    z_top: float = 0.0
    z_bot: float = 0.0


def load(path):
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise StackupError(f"{path}: {e}")
    if doc.get("version") != 1:
        raise StackupError(f"{path}: version = 1 expected")
    if "layer" not in doc and "zone" in doc:
        raise StackupError(f"{path}: an explore file: use `odapkit stackup explore`")
    layers = []
    for i, d in enumerate(doc.get("layer", [])):
        unknown = set(d) - LAYER_KEYS
        if unknown:
            raise StackupError(f"layer {i}: unknown keys {sorted(unknown)}")
        if d.get("type") not in ("copper", "dielectric", "mask"):
            raise StackupError(f"layer {i}: type must be copper, dielectric or mask")
        layers.append(Layer(name=d.get("name", f"#{i}"), type=d["type"], t=float(d["thickness_um"]),
                            er=float(d.get("er", 1.0)), role=d.get("role", ""),
                            material=d.get("material", ""), fills=d.get("fills", "")))
    if not layers:
        raise StackupError(f"{path}: no [[layer]]")
    z = 0.0
    for L in layers:              # z grows downward from the top surface
        L.z_top, L.z_bot = z, z + L.t
        z += L.t
    for c in doc.get("class", []):
        unknown = set(c) - CLASS_KEYS
        if unknown:
            raise StackupError(f"class {c.get('name')}: unknown keys {sorted(unknown)}")
    return doc, layers


def copper(layers, name):
    for i, L in enumerate(layers):
        if L.name == name and L.type == "copper":
            return i, L
    raise StackupError(f"no copper layer {name!r}")


def finished_thickness(layers):
    return sum(L.t for L in layers)


def laminate_thickness(layers):
    return sum(L.t for L in layers if L.type != "mask")


def stack_report(doc, layers):
    rows = [{"name": L.name, "type": L.type, "role": L.role, "material": L.material,
             "thickness_um": L.t, "er": L.er if L.type != "copper" else None,
             "z_top_um": round(L.z_top, 2)} for L in layers]
    checks = []
    for d in doc.get("distance", []):
        _, a = copper(layers, d["from"])
        _, b = copper(layers, d["to"])
        upper, lower = (a, b) if a.z_top < b.z_top else (b, a)
        dist = lower.z_top - upper.z_bot    # copper surface to copper surface
        ok = bool(d.get("min_um", -math.inf) <= dist <= d.get("max_um", math.inf))
        checks.append({"check": f"{d['from']} to {d['to']}", "value_um": round(dist, 1),
                       "min_um": d.get("min_um"), "max_um": d.get("max_um"),
                       "source": d.get("source", ""), "ok": ok})
    th = doc.get("thickness", {})
    fin = finished_thickness(layers) / 1000
    lam = laminate_thickness(layers) / 1000
    tol = th.get("tolerance_mm", 0.0)
    if "max_mm" in th or "min_mm" in th:
        # The nominal must reach the minimum; the nominal plus the fab tolerance must stay
        # within the maximum (a z budget is a ceiling, the minimum a design target).
        lo, hi = fin - tol, fin + tol
        ok = bool(th.get("min_mm", -math.inf) <= fin and hi <= th.get("max_mm", math.inf))
        checks.append({"check": "finished thickness (nominal >= min, nominal + tolerance <= max)",
                       "value_mm": round(fin, 3),
                       "range_mm": [round(lo, 3), round(hi, 3)], "min_mm": th.get("min_mm"),
                       "max_mm": th.get("max_mm"), "source": th.get("source", ""), "ok": ok})
    return {"layers": rows, "laminate_mm": round(lam, 4), "finished_mm": round(fin, 4),
            "checks": checks, "ok": all(c["ok"] for c in checks)}


# ---------------------------------------------------------------- cross-sections

@dataclass
class Geometry:
    width: float          # drawn (bottom) width, µm
    gap: float | None     # drawn edge-to-edge gap of a pair, µm
    scale_h: float = 1.0  # dielectric thickness factor (tolerance corners)
    scale_er: float = 1.0
    scale_t: float = 1.0
    dwidth: float = 0.0   # added to the width (tolerance corners), µm


def _er_of_cut(layers, i):
    """A copper layer cut out under the line is filled by the dielectrics around it."""
    ers = [layers[j].er for j in (i - 1, i + 1)
           if 0 <= j < len(layers) and layers[j].type == "dielectric"]
    return sum(ers) / len(ers) if ers else 1.0


def build(doc, layers, cls, g: Geometry):
    """CrossSection for a class: the line's layer, its reference plane(s), what lies between."""
    i_sig, sig = copper(layers, cls["layer"])
    refs = [copper(layers, r) for r in cls["reference"]]
    above = [(i, L) for i, L in refs if i < i_sig]
    below = [(i, L) for i, L in refs if i > i_sig]
    if len(above) > 1 or len(below) > 1 or not (above or below):
        raise StackupError(f"{cls['name']}: reference must be one plane on each side at most")
    if above and not below:
        # A line referenced only from above (bottom side): solve the mirrored stack.
        mirrored = []
        for L in reversed(layers):
            m = Layer(**{**L.__dict__})
            m.fills = {"above": "below", "below": "above"}.get(L.fills, L.fills)
            mirrored.append(m)
        return build(doc, mirrored, cls, g)
    etch = doc.get("etch", {})
    outer = i_sig in (_first_copper(layers), _last_copper(layers))
    reduction = etch.get("outer_reduction_um" if outer else "inner_reduction_um", 0.0)
    w_mean = g.width + g.dwidth - reduction / 2          # trapezoid -> mean width
    gap_mean = None if g.gap is None else g.gap - g.dwidth + reduction / 2
    t_sig = sig.t * g.scale_t

    # Vertical extent: from the lower reference plane's top surface (y = 0) upward.
    i_lo = below[0][0]
    i_hi = above[0][0] if above else None

    # Layers strictly between the lower plane and the signal, then the signal band, then above.
    boxes = []   # (y0, y1, er) bands, y upward
    y = 0.0
    cut_bands = []   # copper layers between the line and its plane, cut out under the line
    for j in range(i_lo - 1, i_sig, -1):
        L = layers[j]
        h = L.t * (g.scale_h if L.type == "dielectric" else g.scale_t)
        er = L.er if L.type == "dielectric" else _er_of_cut(layers, j)
        boxes.append((y, y + h, er * g.scale_er))
        if L.type == "copper":
            cut_bands.append((y, y + h))
        y += h
    y_sig0 = y
    y_sig1 = y + t_sig
    # dielectric pressed around the traces
    fill = sig.fills or ("above" if i_hi is not None else "")
    j_fill = i_sig - 1 if fill == "above" else i_sig + 1
    fill_er = layers[j_fill].er if 0 <= j_fill < len(layers) and layers[j_fill].type in ("dielectric", "mask") else 1.0
    if i_hi is None:
        fill_er = 1.0                    # outer layer: air, the mask is added below
    boxes.append((y_sig0, y_sig1, fill_er * (g.scale_er if fill_er > 1 else 1)))
    y = y_sig1
    top_ground = False
    mask = None
    if i_hi is not None:
        for j in range(i_sig - 1, i_hi, -1):
            L = layers[j]
            h = L.t * (g.scale_h if L.type == "dielectric" else g.scale_t)
            er = L.er if L.type == "dielectric" else _er_of_cut(layers, j)
            boxes.append((y, y + h, er * g.scale_er))
            y += h
        y_top = y
        top_ground = True
    else:
        # outer layer: the mask (if any) is the layer above the copper
        if i_sig - 1 >= 0 and layers[i_sig - 1].type == "mask":
            mask = layers[i_sig - 1]
        y_top = None

    h_ref = y_sig0                                   # line to its lower plane
    if top_ground:
        width = max(40 * (y_top), 20 * (w_mean * 2 + (gap_mean or 0)))
        cs = fs.CrossSection(width_um=width, y_min=0.0, y_max=y_top, top_ground=True)
    else:
        width = max(60 * h_ref, 20 * (w_mean * 2 + (gap_mean or 0)))
        cs = fs.CrossSection(width_um=width, y_min=0.0, y_max=width / 2, top_ground=False)
    W = cs.width_um / 2
    for y0, y1, er in boxes:
        if y1 > y0:
            cs.dielectrics.append((fs.Box(-W, W, y0, y1), er))
    if gap_mean is None:
        xs = [(-w_mean / 2, w_mean / 2)]
    else:
        xs = [(-gap_mean / 2 - w_mean, -gap_mean / 2), (gap_mean / 2, gap_mean / 2 + w_mean)]
    if mask is not None:
        tm = mask.t
        er_m = mask.er * g.scale_er
        cs.dielectrics.append((fs.Box(-W, W, y_sig0, y_sig0 + tm), er_m))
        for x0, x1 in xs:
            cs.dielectrics.append((fs.Box(x0 - tm, x1 + tm, y_sig0, y_sig1 + tm), er_m))
    for x0, x1 in xs:
        cs.signals.append(fs.Box(x0, x1, y_sig0, y_sig1))
    cut = cls.get("cutout_um")
    if cut:
        # the cut-out is `cut` wide, centred on the line; the layer is ground copper outside it
        for y0, y1 in cut_bands:
            cs.grounds.append(fs.Box(-W, -cut / 2, y0, y1))
            cs.grounds.append(fs.Box(cut / 2, W, y0, y1))
    cpg = cls.get("coplanar_gap_um")
    if cpg:
        xmin = min(x for x, _ in xs) - cpg
        xmax = max(x for _, x in xs) + cpg
        cs.grounds.append(fs.Box(-W, xmin, y_sig0, y_sig1))
        cs.grounds.append(fs.Box(xmax, W, y_sig0, y_sig1))
    return cs, h_ref


def _first_copper(layers):
    return next(i for i, L in enumerate(layers) if L.type == "copper")


def _last_copper(layers):
    return max(i for i, L in enumerate(layers) if L.type == "copper")


def impedance(doc, layers, cls, g: Geometry, draft=False):
    """Z of the class at geometry g. `draft` uses a coarser mesh (about 0.3 % low) for searches."""
    cs, _ = build(doc, layers, cls, g)
    r = fs.solve(cs, fine=1.0, coarse=10.0) if draft else fs.solve(cs)
    z = r.zdiff if cls["type"] == "diff" else r.z0
    return z, r


# ---------------------------------------------------------------- design and check

def _search(f, lo, hi, target, increasing, tol=0.02, it=60):
    """Bisection on a monotonic f between lo and hi."""
    for _ in range(it):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm - target) < tol or hi - lo < 0.05:
            return mid
        if (fm < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _refine(f, x, target, step=2.0):
    """Two secant steps on the fine mesh from the draft answer."""
    x0, x1 = x, x + step
    f0, f1 = f(x0), f(x1)
    for _ in range(2):
        if f1 == f0:
            break
        x2 = x1 + (target - f1) * (x1 - x0) / (f1 - f0)
        x0, f0, x1 = x1, f1, x2
        f1 = f(x1)
    return x1


def design(doc, layers, cls):
    """Free dimension for the target, rounded to 0.5 µm (the solver is accurate to about 0.3 %)."""
    solve = cls.get("solve", "width")
    target = cls["target_ohm"]
    if solve == "width":
        gap = cls.get("gap_um")
        if cls["type"] == "diff" and gap is None:
            raise StackupError(f"{cls['name']}: solve = width needs gap_um")
        lo, hi = cls.get("min_width_um", 20.0), cls.get("max_width_um", 1500.0)
        draft = _search(lambda w: impedance(doc, layers, cls, Geometry(w, gap), True)[0], lo, hi,
                        target, increasing=False)
        w = _refine(lambda w: impedance(doc, layers, cls, Geometry(w, gap))[0], draft, target)
        return {"width_um": round(w * 2) / 2, "gap_um": gap}
    if solve == "gap":
        w = cls["width_um"]
        draft = _search(lambda s: impedance(doc, layers, cls, Geometry(w, s), True)[0], 20.0, 2000.0,
                        target, increasing=True)
        g = _refine(lambda s: impedance(doc, layers, cls, Geometry(w, s))[0], draft, target)
        return {"width_um": w, "gap_um": round(g * 2) / 2}
    raise StackupError(f"{cls['name']}: solve must be width or gap")


def corners(doc, layers, cls, width, gap):
    tol = doc.get("tolerance", {})
    dh = tol.get("dielectric_pct", 10) / 100
    der = tol.get("er_pct", 5) / 100
    dw = tol.get("width_um", 10.0)
    dt = tol.get("copper_pct", 15) / 100
    base = Geometry(width, gap)
    z0 = impedance(doc, layers, cls, base)[0]
    sens = {}
    for key, lo, hi in (("dielectric", Geometry(width, gap, scale_h=1 - dh), Geometry(width, gap, scale_h=1 + dh)),
                        ("er", Geometry(width, gap, scale_er=1 - der), Geometry(width, gap, scale_er=1 + der)),
                        ("width", Geometry(width, gap, dwidth=-dw), Geometry(width, gap, dwidth=+dw)),
                        ("copper", Geometry(width, gap, scale_t=1 - dt), Geometry(width, gap, scale_t=1 + dt))):
        sens[key] = (impedance(doc, layers, cls, lo)[0] - z0, impedance(doc, layers, cls, hi)[0] - z0)
    # Worst case: every parameter at the end that pushes Z the same way (one-at-a-time deltas added).
    zmax = z0 + sum(max(a, b, 0) for a, b in sens.values())
    zmin = z0 + sum(min(a, b, 0) for a, b in sens.values())
    rss = math.sqrt(sum(max(abs(a), abs(b)) ** 2 for a, b in sens.values()))
    return {"z_nominal": z0, "sensitivity": {k: [round(a, 2), round(b, 2)] for k, (a, b) in sens.items()},
            "z_worst": [round(zmin, 2), round(zmax, 2)], "rss_ohm": round(rss, 2),
            "assumed": {"dielectric_pct": dh * 100, "er_pct": der * 100, "width_um": dw,
                        "copper_pct": dt * 100}}


def check_class(doc, layers, cls, do_corners=True):
    target = cls["target_ohm"]
    window = cls.get("design_window_pct", doc.get("design_window_pct", 2.0))
    tolp = cls.get("tolerance_pct", 10.0)
    w, gap = cls.get("width_um"), cls.get("gap_um")
    if w is None or (cls["type"] == "diff" and gap is None):
        return {"name": cls["name"], "status": "fail", "reason": "no geometry recorded"}
    z, r = impedance(doc, layers, cls, Geometry(w, gap))
    status, reasons = "pass", []
    if abs(z - target) > target * window / 100:
        status = "fail"
        reasons.append(f"nominal {z:.2f} ohm outside {target} +- {window} %")
    rec = cls.get("z_ohm")
    if rec is not None and abs(rec - z) > 0.5:
        status = "fail"
        reasons.append(f"recorded z_ohm {rec} stale (computed {z:.2f})")
    out = {"name": cls["name"], "layer": cls["layer"], "reference": cls["reference"],
           "type": cls["type"], "target_ohm": target, "width_um": w, "gap_um": gap,
           "z_ohm": round(z, 2), "eps_eff": round(r.eps_eff, 3),
           "delay_ps_per_mm": round(r.delay_ps_per_mm, 3)}
    if cls["type"] == "diff":
        out.update(zodd=round(r.zodd, 2), zeven=round(r.zeven, 2), zcommon=round(r.zcommon, 2))
    if do_corners:
        c = corners(doc, layers, cls, w, gap)
        out["corners"] = c
        lo, hi = target * (1 - tolp / 100), target * (1 + tolp / 100)
        if c["z_worst"][0] < lo or c["z_worst"][1] > hi:
            out["warning"] = (f"worst-case corner {c['z_worst']} outside {lo:.1f}-{hi:.1f} ohm: the "
                              "fab's width compensation and coupon must absorb it")
    out["status"] = status
    if reasons:
        out["reason"] = "; ".join(reasons)
    return out


# ---------------------------------------------------------------- validation

def validate():
    rows = []
    for w, b, er in ((100, 300, 4.0), (200, 300, 4.45), (60, 250, 4.2)):
        cs = fs.CrossSection(width_um=40 * b, y_min=0, y_max=b, top_ground=True,
                             dielectrics=[(fs.Box(-20 * b, 20 * b, 0, b), er)],
                             signals=[fs.Box(-w / 2, w / 2, b / 2, b / 2)])
        z = fs.solve(cs).z0
        ref = analytic.cohn_stripline(w, b, er)
        rows.append({"case": f"stripline w={w} b={b} er={er} t=0", "solver": float(round(z, 3)),
                     "reference": round(ref, 3), "formula": "Cohn (exact)",
                     "error_pct": float(round(100 * (z - ref) / ref, 3)), "limit_pct": 0.5})
        rows.append({"case": f"stripline w={w} b={b} er={er} t=0", "solver": float(round(z, 3)),
                     "reference": round(analytic.ipc2141_stripline(w, b, 0.0, er), 3),
                     "formula": "IPC-2141A (comparison)", "error_pct": None, "limit_pct": None})
    for w, h, t, er in ((150, 100, 35, 4.2), (110, 65, 35, 4.21), (420, 232, 35, 4.3)):
        W = 60 * h
        cs = fs.CrossSection(width_um=W, y_min=0, y_max=W / 2,
                             dielectrics=[(fs.Box(-W / 2, W / 2, 0, h), er)],
                             signals=[fs.Box(-w / 2, w / 2, h, h + t)])
        z = fs.solve(cs).z0
        ref, _ = analytic.hammerstad_jensen_microstrip(w, h, t, er)
        rows.append({"case": f"microstrip w={w} h={h} t={t} er={er}", "solver": float(round(z, 3)),
                     "reference": round(ref, 3), "formula": "Hammerstad-Jensen (Wadell 3.5)",
                     "error_pct": float(round(100 * (z - ref) / ref, 3)), "limit_pct": 1.5})
        rows.append({"case": f"microstrip w={w} h={h} t={t} er={er}", "solver": float(round(z, 3)),
                     "reference": round(analytic.ipc2141_microstrip(w, h, t, er), 3),
                     "formula": "IPC-2141A (comparison)", "error_pct": None, "limit_pct": None})
    # KiCad's calculator: equal to Hammerstad-Jensen for thin copper; its extra thickness
    # term reads high when the copper is thick relative to the dielectric (compared, not judged,
    # for t/h > 0.1).
    for w, h, t, er in ((150, 90, 1, 4.45), (150, 90, 35, 4.45)):
        W = 60 * h
        cs = fs.CrossSection(width_um=W, y_min=0, y_max=W / 2,
                             dielectrics=[(fs.Box(-W / 2, W / 2, 0, h), er)],
                             signals=[fs.Box(-w / 2, w / 2, h, h + t)])
        z = fs.solve(cs).z0
        ref = analytic.kicad_microstrip(w, h, t, er)
        judged = t / h <= 0.1
        rows.append({"case": f"microstrip w={w} h={h} t={t} er={er}", "solver": float(round(z, 3)),
                     "reference": round(ref, 3), "formula": "KiCad calculator model" + ("" if judged else " (comparison)"),
                     "error_pct": float(round(100 * (z - ref) / ref, 3)), "limit_pct": 1.0 if judged else None})
    # A pair far apart behaves as two single lines: Zdiff -> 2 Z0.
    h, w, t, er, s = 100, 150, 35, 4.2, 3000
    W = 60 * h + 2 * s
    single = fs.solve(fs.CrossSection(width_um=W, y_min=0, y_max=W / 2,
                                      dielectrics=[(fs.Box(-W / 2, W / 2, 0, h), er)],
                                      signals=[fs.Box(-w / 2, w / 2, h, h + t)])).z0
    pair = fs.solve(fs.CrossSection(width_um=W, y_min=0, y_max=W / 2,
                                    dielectrics=[(fs.Box(-W / 2, W / 2, 0, h), er)],
                                    signals=[fs.Box(-s / 2 - w, -s / 2, h, h + t),
                                             fs.Box(s / 2, s / 2 + w, h, h + t)])).zdiff
    rows.append({"case": f"uncoupled pair s={s}: Zdiff vs 2 x Z0", "solver": float(round(pair, 3)),
                 "reference": round(2 * single, 3), "formula": "2 x single-line solver",
                 "error_pct": float(round(100 * (pair - 2 * single) / (2 * single), 3)), "limit_pct": 0.5})
    for r in rows:
        r["ok"] = bool(r["limit_pct"] is None or abs(r["error_pct"]) <= r["limit_pct"])
    return {"rows": rows, "ok": all(r["ok"] for r in rows)}


# ---------------------------------------------------------------- CLI

def coupling(doc, layers, case):
    """Broadside coupling of two traces on two layers between the same two planes."""
    la, lb = case["layers"]
    i_a, A = copper(layers, la)
    i_b, B = copper(layers, lb)
    i_hi, _ = copper(layers, case["reference"][0])
    i_lo, _ = copper(layers, case["reference"][1])
    if not (i_hi < min(i_a, i_b) and max(i_a, i_b) < i_lo):
        raise StackupError(f"{case['name']}: references must be above and below both traces")
    etch = doc.get("etch", {}).get("inner_reduction_um", 0.0)
    w = case["width_um"] - etch / 2
    off = case.get("offset_um", 0.0)
    y, bands, pos = 0.0, [], {}
    for j in range(i_lo - 1, i_hi, -1):           # bottom plane upward
        L = layers[j]
        if L.type == "dielectric":
            er = L.er
        else:
            er = _er_of_cut(layers, j)
            if j in (i_a, i_b):
                pos[j] = (y, y + L.t)
        bands.append((y, y + L.t, er))
        y += L.t
    width = max(40 * y, 20 * (2 * w + abs(off)))
    cs = fs.CrossSection(width_um=width, y_min=0.0, y_max=y, top_ground=True)
    W = width / 2
    for y0, y1, er in bands:
        cs.dielectrics.append((fs.Box(-W, W, y0, y1), er))
    for j, x0 in ((i_a, -off / 2), (i_b, off / 2)):
        y0, y1 = pos[j]
        cs.signals.append(fs.Box(x0 - w / 2, x0 + w / 2, y0, y1))
    C, _ = fs.capacitance_matrix(cs, vacuum=False, fine=0.25, coarse=5.0)
    Cv, _ = fs.capacitance_matrix(cs, vacuum=True, fine=0.25, coarse=5.0)
    import numpy as np
    Lm = np.linalg.inv(Cv) / fs.C0 ** 2
    kc = -C[0, 1] / math.sqrt(C[0, 0] * C[1, 1])
    kl = Lm[0, 1] / math.sqrt(Lm[0, 0] * Lm[1, 1])
    return {"name": case["name"], "layers": [la, lb], "reference": case["reference"],
            "width_um": case["width_um"], "offset_um": off,
            "z_a_ohm": round(math.sqrt(Lm[0, 0] / C[0, 0]), 2), "z_b_ohm": round(math.sqrt(Lm[1, 1] / C[1, 1]), 2),
            "kb_pct": round(100 * (kc + kl) / 4, 2), "kc": round(kc, 4), "kl": round(kl, 4),
            "classes": case.get("classes", [])}


def _select(doc, only):
    classes = doc.get("class", [])
    if only:
        classes = [c for c in classes if any(fnmatch.fnmatch(c["name"], p) for p in only)]
    return classes


def _fmt(v, nd=1):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="odapkit stackup", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("stack", "design", "check"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("--json", action="store_true")
        if name != "stack":
            p.add_argument("--only", action="append", default=[])
        if name == "check":
            p.add_argument("--no-corners", action="store_true")
    p = sub.add_parser("validate")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("coupling")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("explore")
    p.add_argument("file")
    p.add_argument("--csv")
    p.add_argument("--json", action="store_true")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override a number of the file, e.g. thickness.min_mm=0.95 (sensitivity runs)")
    p = sub.add_parser("pdn")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if MISSING is not None:
        print(f"odapkit stackup: error env.missing: {MISSING}\n  hint: the solver runs in tools/stackup's locked "
              "environment\n  fix: odapkit stackup " + " ".join(argv if argv is not None else sys.argv[1:]),
              file=sys.stderr)
        return 2

    if args.cmd == "pdn":
        import pdn
        try:
            return pdn.main(args)
        except pdn.PdnError as e:
            print(f"odapkit stackup pdn: {e}", file=sys.stderr)
            return 2

    if args.cmd == "explore":
        import explore
        try:
            return explore.main(args)
        except explore.ExploreError as e:
            print(f"odapkit stackup explore: {e}", file=sys.stderr)
            return 2

    try:
        if args.cmd == "validate":
            rep = validate()
            if args.json:
                print(json.dumps({"schema": "opendap.stackup-validate/v1", **rep}, indent=2))
            else:
                for r in rep["rows"]:
                    err = "" if r["error_pct"] is None else f"{r['error_pct']:+.2f} %" + ("" if r["limit_pct"] is None else f" (limit {r['limit_pct']} %)")
                    print(f"{r['case']:<44} solver {r['solver']:8.3f}  {r['formula']:<32} {r['reference']:8.3f}  {err}")
                print("validate:", "pass" if rep["ok"] else "FAIL")
            return 0 if rep["ok"] else 1

        doc, layers = load(args.file)
        if args.cmd == "coupling":
            out = []
            for case in doc.get("coupling", []):
                unknown = set(case) - COUPLING_KEYS
                if unknown:
                    raise StackupError(f"coupling {case.get('name')}: unknown keys {sorted(unknown)}")
                out.append(coupling(doc, layers, case))
            if args.json:
                print(json.dumps({"schema": "opendap.stackup-coupling/v1", "items": out}, indent=2))
            else:
                for o in out:
                    print(f"{o['name']:<28}{'/'.join(o['layers']):<8} offset {o['offset_um']:>6.0f} µm  "
                          f"Z {o['z_a_ohm']:.1f} / {o['z_b_ohm']:.1f} Ω  Kb {o['kb_pct']:.2f} %")
            return 0
        if args.cmd == "stack":
            rep = stack_report(doc, layers)
            if args.json:
                print(json.dumps({"schema": "opendap.stackup-stack/v1", **rep}, indent=2))
            else:
                print(f"{'layer':<13}{'type':<11}{'t µm':>7}{'er':>6}{'z µm':>9}  role")
                for r in rep["layers"]:
                    print(f"{r['name']:<13}{r['type']:<11}{r['thickness_um']:>7.1f}"
                          f"{_fmt(r['er'], 2):>6}{r['z_top_um']:>9.1f}  {r['role'] or r['material'][:60]}")
                print(f"laminate {rep['laminate_mm']:.3f} mm, finished {rep['finished_mm']:.3f} mm")
                for c in rep["checks"]:
                    print(("ok   " if c["ok"] else "FAIL ") + json.dumps({k: v for k, v in c.items() if k != "ok"}))
            return 0 if rep["ok"] else 1

        classes = _select(doc, args.only)
        if args.cmd == "design":
            out = []
            for cls in classes:
                geo = design(doc, layers, cls)
                z, r = impedance(doc, layers, cls, Geometry(geo["width_um"], geo["gap_um"]))
                out.append({"name": cls["name"], **geo, "z_ohm": round(z, 2)})
            if args.json:
                print(json.dumps({"schema": "opendap.stackup-design/v1", "items": out}, indent=2))
            else:
                for o in out:
                    print(f"{o['name']:<20} width_um = {o['width_um']:<7} gap_um = {_fmt(o['gap_um'])!s:<7} z_ohm = {o['z_ohm']}")
            return 0

        res = [check_class(doc, layers, c, not args.no_corners) for c in classes]
        stack = stack_report(doc, layers)
        ok = stack["ok"] and all(r["status"] == "pass" for r in res)
        if args.json:
            print(json.dumps({"schema": "opendap.stackup-check/v1", "stack": stack, "classes": res, "ok": ok}, indent=2))
        else:
            print(f"{'class':<20}{'layer':<6}{'ref':<10}{'W µm':>7}{'S µm':>7}{'Z Ω':>8}{'target':>8}"
                  f"{'worst-case Ω':>16}  status")
            for r in res:
                wc = r.get("corners", {}).get("z_worst")
                wcs = "-" if not wc else f"{wc[0]:.1f}-{wc[1]:.1f}"
                print(f"{r['name']:<20}{r.get('layer', ''):<6}{'/'.join(r.get('reference', [])):<10}"
                      f"{_fmt(r.get('width_um')):>7}{_fmt(r.get('gap_um')):>7}{_fmt(r.get('z_ohm'), 2):>8}"
                      f"{_fmt(r.get('target_ohm')):>8}{wcs:>16}  {r['status']}"
                      + (f" ({r['reason']})" if r.get("reason") else "")
                      + (" warning" if r.get("warning") else ""))
            for c in stack["checks"]:
                if not c["ok"]:
                    print("FAIL " + json.dumps(c))
            print("check:", "pass" if ok else "FAIL")
        return 0 if ok else 1
    except StackupError as e:
        print(f"odapkit stackup: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
