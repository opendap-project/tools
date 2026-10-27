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
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

# The model (stackmodel), the computations on it (stackcheck); this file is the command line and its reports.
from stackcheck import _select, check_class, coupling, design, validate  # noqa: E402
from stackmodel import (COUPLING_KEYS, MISSING, Geometry, StackupError, impedance, load,  # noqa: E402
                        stack_report)


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
