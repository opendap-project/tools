"""odw stackup's model: the layer stack (load, layers, thickness, the stack report) and its cross-sections
(the field solver's geometry and impedance). Format and method: docs/specs/stackup-method.md."""
from __future__ import annotations

import math
import os
import sys
import tomllib
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

try:  # numpy, scipy and scikit-fem: tools/stackup's own environment (uv run, as odw stackup runs it)
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
        raise StackupError(f"{path}: an explore file: use `odw stackup explore`")
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
