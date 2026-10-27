"""odapkit stackup pdn: a lumped power-distribution-network model of one or more rails, to see
how much the distance between a power plane and its ground (and the plane's depth in the stack)
changes the impedance a load sees, against a target impedance.

The model, per rail and per [[case]] (a candidate stack's plane geometry):

    Z(f) = Z_load_vias + 1 / (Y_vrm + sum Y_cap + Y_plane [+ Y_package])

- Load vias: the rail's power vias of the ball field, each paired with a ground via; loop
  inductance of a via pair of length h, radius a, centre spacing p: (mu0 h / pi) arcosh(p / 2a),
  the pairs in parallel (mutual inductance between pairs neglected: optimistic).
  h = `load_via_um` of the case (from the load's layer to the plane pair).
- VRM: R + jwL (`vrm.r_mohm`, `vrm.l_nh`): the regulator above its loop bandwidth plus the pour
  from it to the load.
- Each capacitor group: `count` identical capacitors in parallel, each ESR + jw(ESL + L_mount +
  L_spread + extra_l) + 1/(jwC). L_mount: the capacitor's via pair of length `cap_via_um` (its
  side to the plane pair), `vias_per_pad` per pad. L_spread: the plane pair's radial inductance
  from the capacitor's via to the load, (mu0 d / 2 pi) ln(s / a), d the plane-to-ground
  dielectric, s `distance_mm`.
- Plane: C = eps0 er A / d with the dielectric's loss tangent (`plane.tan_d`).
- Package capacitance `load.package_c_nf` (optional, the maker's figure when there is one).

Target impedance: ripple / current step (`rail.ripple_mv` or `ripple_pct` of `v_nom`, `i_step_a`),
judged from `f_min_hz` to `band_max_hz`, the band the board is responsible for (above it the
package and die decouple). The report gives, per rail and case, the peak |Z| in the band, the
margin to the target, the frequency of the peak, f_cross (the first frequency where |Z| rises
over the target: the board holds the rail alone below it, the package must above it) and the
inductances that set it.

Closed-form, single-threaded, no solver: a screening model, not a substitute for a layout's PDN
simulation. Every coefficient it uses is printed with the result.
"""
from __future__ import annotations

import json
import math
import sys
import tomllib

MU0 = 4e-7 * math.pi
EPS0 = 8.8541878128e-12

RAIL_KEYS = {"name", "v_nom", "ripple_mv", "ripple_pct", "i_step_a", "vrm", "load", "cap", "notes"}
CAP_KEYS = {"name", "c_uf", "esr_mohm", "esl_nh", "count", "distance_mm", "vias_per_pad",
            "via_pitch_um", "extra_l_nh", "notes"}
CASE_KEYS = {"name", "d_um", "er", "tan_d", "area_cm2", "cap_via_um", "load_via_um", "notes", "rails"}


class PdnError(Exception):
    pass


def load(path):
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise PdnError(f"{path}: {e}")
    if doc.get("version") != 1:
        raise PdnError(f"{path}: version = 1 expected")
    for key in ("rail", "case"):
        if not doc.get(key):
            raise PdnError(f"{path}: at least one [[{key}]] expected")
    for r in doc["rail"]:
        bad = set(r) - RAIL_KEYS
        if bad:
            raise PdnError(f"rail {r.get('name')}: unknown keys {sorted(bad)}")
        for c in r.get("cap", []):
            bad = set(c) - CAP_KEYS
            if bad:
                raise PdnError(f"rail {r.get('name')} cap {c.get('name')}: unknown keys {sorted(bad)}")
    for c in doc["case"]:
        bad = set(c) - CASE_KEYS
        if bad:
            raise PdnError(f"case {c.get('name')}: unknown keys {sorted(bad)}")
    return doc


def via_pair_l(h_um, radius_um, pitch_um):
    """Loop inductance (H) of a power/ground via pair of length h: (mu0 h / pi) arcosh(p / 2a)."""
    if h_um <= 0:
        return 0.0
    x = max(pitch_um / (2 * radius_um), 1.0 + 1e-9)
    return MU0 * h_um * 1e-6 / math.pi * math.acosh(x)


def spread_l(d_um, s_mm, radius_um):
    """Radial inductance (H) of a plane pair d apart between a via of radius a and a ring s away."""
    s = max(s_mm * 1e3, radius_um * 1.0001)
    return MU0 * d_um * 1e-6 / (2 * math.pi) * math.log(s / radius_um)


def target_ohm(rail):
    if "ripple_mv" in rail:
        dv = rail["ripple_mv"] / 1e3
    elif "ripple_pct" in rail:
        dv = rail["v_nom"] * rail["ripple_pct"] / 100
    else:
        raise PdnError(f"rail {rail['name']}: ripple_mv or ripple_pct expected")
    return dv / rail["i_step_a"]


def branches(doc, rail, case):
    """The admittance branches (callables of omega) and the inductances that set them."""
    via = doc.get("via", {})
    a = via.get("radius_um", 100.0)
    p_def = via.get("pitch_um", 650.0)
    d = case["d_um"]
    out, parts = [], {}
    vrm = rail.get("vrm", {})
    r_v, l_v = vrm.get("r_mohm", 1.0) / 1e3, vrm.get("l_nh", 10.0) * 1e-9
    out.append(("vrm", lambda w: 1 / complex(r_v, w * l_v)))
    for c in rail.get("cap", []):
        n = c.get("count", 1)
        lm = via_pair_l(case["cap_via_um"], a, c.get("via_pitch_um", p_def)) / c.get("vias_per_pad", 1)
        ls = spread_l(d, c.get("distance_mm", 1.0), a)
        l_tot = c["esl_nh"] * 1e-9 + lm + ls + c.get("extra_l_nh", 0.0) * 1e-9
        cap, esr = c["c_uf"] * 1e-6, c["esr_mohm"] / 1e3
        parts[c["name"]] = {"count": n, "esl_nh": c["esl_nh"], "mount_nh": round(lm * 1e9, 4),
                            "spread_nh": round(ls * 1e9, 4), "total_nh": round(l_tot * 1e9, 4),
                            "srf_mhz": round(1 / (2 * math.pi * math.sqrt(l_tot * cap)) / 1e6, 2)}
        out.append((c["name"], lambda w, n=n, l=l_tot, cap=cap, esr=esr:
                    n / complex(esr, w * l - 1 / (w * cap))))
    cp = EPS0 * case.get("er", 4.4) * case["area_cm2"] * 1e-4 / (d * 1e-6)
    tan_d = case.get("tan_d", 0.02)
    parts["plane"] = {"c_pf": round(cp * 1e12, 2), "pf_per_cm2": round(cp * 1e12 / case["area_cm2"], 2)}
    out.append(("plane", lambda w: complex(w * cp * tan_d, w * cp)))
    pkg = rail.get("load", {}).get("package_c_nf", 0.0)
    if pkg:
        out.append(("package", lambda w: complex(0, w * pkg * 1e-9)))
    ld = rail.get("load", {})
    nv = ld.get("vias", 1)
    l_load = via_pair_l(case["load_via_um"], a, ld.get("via_pitch_um", p_def)) / nv
    parts["load_vias"] = {"vias": nv, "nh": round(l_load * 1e9, 4)}
    return out, l_load, parts


def impedance(doc, rail, case, f):
    w = 2 * math.pi * f
    br, l_load, _ = branches(doc, rail, case)
    y = sum(b(w) for _, b in br)
    return 1 / y + complex(0, w * l_load)


def freqs(doc):
    sw = doc.get("sweep", {})
    f0, f1, n = sw.get("f_min_hz", 1e4), sw.get("f_max_hz", 1e9), int(sw.get("points", 400))
    return [f0 * (f1 / f0) ** (i / (n - 1)) for i in range(n)]


def evaluate(doc):
    fs = freqs(doc)
    band = doc.get("sweep", {}).get("band_max_hz", 1e8)
    rows = []
    for case in doc["case"]:
        for rail in doc["rail"]:
            if case.get("rails") and rail["name"] not in case["rails"]:
                continue
            zt = target_ohm(rail)
            zs = [(f, abs(impedance(doc, rail, case, f))) for f in fs]
            inband = [(f, z) for f, z in zs if f <= band]
            fpk, zpk = max(inband, key=lambda t: t[1])
            over = [f for f, z in inband if z > zt]
            # the first crossing of the target from below: up to there the board holds the rail
            # alone; above it the package's own capacitance has to (f_cross)
            f_cross = next((f for f, z in zs if z > zt), None)
            _, _, parts = branches(doc, rail, case)
            rows.append({
                "case": case["name"], "rail": rail["name"], "d_um": case["d_um"],
                "target_mohm": round(zt * 1e3, 2), "peak_mohm": round(zpk * 1e3, 2),
                "peak_mhz": round(fpk / 1e6, 3), "margin_pct": round(100 * (zt - zpk) / zt, 1),
                "ok": zpk <= zt,
                "f_cross_mhz": None if f_cross is None else round(f_cross / 1e6, 2),
                "over_target_mhz": [round(min(over) / 1e6, 3), round(max(over) / 1e6, 3)] if over else None,
                "z_at_mhz": {str(m): round(abs(impedance(doc, rail, case, m * 1e6)) * 1e3, 2)
                             for m in doc.get("sweep", {}).get("report_mhz", [1, 10, 50, 100])},
                "parts": parts,
            })
    return rows


def _mhz(v):
    return "-" if v is None else f"{v:.1f}"


def main(args):
    doc = load(args.file)
    rows = evaluate(doc)
    if args.json:
        print(json.dumps({"schema": "opendap.stackup-pdn/v1", "rows": rows}, indent=2))
    else:
        rep = doc.get("sweep", {}).get("report_mhz", [1, 10, 50, 100])
        print(f"{'case':<26}{'rail':<14}{'d µm':>6}{'target mΩ':>11}{'peak mΩ':>9}{'at MHz':>8}{'margin':>8}"
              f"{'f_cross':>9}  "
              + "  ".join(f"|Z|@{m}MHz" for m in rep))
        for r in rows:
            print(f"{r['case']:<26}{r['rail']:<14}{r['d_um']:>6}{r['target_mohm']:>11.1f}{r['peak_mohm']:>9.1f}"
                  f"{r['peak_mhz']:>8.2f}{r['margin_pct']:>7.1f}%{_mhz(r['f_cross_mhz']):>9}  "
                  + "  ".join(f"{r['z_at_mhz'][str(m)]:>10.1f}" for m in rep)
                  + ("" if r["ok"] else "  OVER"))
    return 0 if all(r["ok"] for r in rows) else 1
