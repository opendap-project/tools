"""odw stackup's computations on the model: design (solve a class for its target), check (a recorded
geometry, with the fab's corners), validate (the solver against closed forms) and coupling (two traces on
two layers). Method: docs/specs/stackup-method.md."""
from __future__ import annotations

import fnmatch
import math

from stackmodel import Geometry, StackupError, _er_of_cut, analytic, copper, fs, impedance


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
