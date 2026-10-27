"""odapkit stackup explore: enumerate candidate stackups for a zoned board and rank them.

A board is divided into zones (floorplan): areas that share the physical layers but use them
for different roles. A layer can be ground under one zone and a power pour or low-speed
routing under another. This tool enumerates, for every layer count and build in the input
file, every assignment of roles per zone and every symmetric choice of dielectrics from the
fab's published list, prunes by hard constraints, and ranks the survivors:

1. fewest copper layers;
2. fewest full-layer grounds (a layer that is ground under every zone);
3. lowest cost class (build), then fewest zone-local grounds, then the RF and pairing margins.

Roles of a copper layer under one zone:

    G  ground (solid under the zone)
    P  power plane or pour
    F  fast routing: every neighbour is G (stripline between two grounds, or microstrip
       over ground on an outer layer); impedance classes, clocks, audio analog
    D  fast routing on a dual stripline: one neighbour G, the other a routing layer (D or L);
       only in zones whose fast classes tolerate broadside coupling (`fast_dual_ok`)
    L  low-speed routing: at least one neighbour G (or, inside a zone that allows it,
       `low_speed_next_to_power`, any neighbour)

Hard constraints (per zone, from the input file): which outer layers carry fast routing,
inner fast layers needed and where (the SoC escape: the layer two laser levels from the SoC
side), striplines between two grounds, power planes and their pairing with a ground across a
thin dielectric, pour layers, an RF microstrip with its ground within a distance window. Per
stack: total thickness within the window, a symmetric build, laser-drillable dielectrics for
the microvia levels.

Output: a CSV with one row per (layer count, build, dielectric sequence) and either the best
zone map with its scores or the reason the candidate failed, plus a summary on stdout.
Bounded and single-threaded; the input file sets the dielectric palette, so the run size is
known before it starts.
"""
from __future__ import annotations

import csv
import copy
import itertools
import json
import tomllib

# The model (builds, roles per zone, dielectrics) is explorezones; load, laser_levels, needs_ok, zone_failure and
# dielectric_sequences stay importable from here.
from explorezones import (COST, ExploreError, dielectric_sequences, geometry, laser_levels, load,  # noqa: F401
                          needs_ok, pair_candidates, rf_distance_ok, zone_failure, zone_vectors)

# ---------------------------------------------------------------- search

def best_assignment(per_zone, n):
    """Pick one vector per zone: fewest full-layer grounds, then fewest zone-local grounds.
    Dynamic programming over the mask of layers that are ground under every zone so far."""
    full_all = (1 << n) - 1
    states = {full_all: (0, ())}          # all-G mask -> (any-G mask, chosen)
    for vecs in per_zone:
        nxt = {}
        for allg, (anyg, chosen) in states.items():
            for v, gmask in vecs:
                a2, o2 = allg & gmask, anyg | gmask
                cur = nxt.get(a2)
                if cur is None or bin(o2).count("1") < bin(cur[0]).count("1"):
                    nxt[a2] = (o2, chosen + (v,))
        states = nxt
        if not states:
            return None
    allg, (anyg, chosen) = min(states.items(), key=lambda kv: (bin(kv[0]).count("1"),
                                                                bin(kv[1][0] & ~kv[0]).count("1")))
    return chosen, bin(allg).count("1"), bin(anyg & ~allg).count("1"), allg


def gmask(v):
    return sum(1 << i for i, x in enumerate(v) if x == "G")


def explore(doc):
    th = doc["thickness"]
    rows = []
    zones = doc["zone"]
    pair_max = doc.get("rules", {}).get("pair_max_um", 65)
    laser_max = doc.get("rules", {}).get("laser_max_um", 150)
    for n in doc["layer_counts"]:
        for build in doc["builds"]:
            base = {"layers": n, "build": build}
            topo, fail = [], None
            for z in zones:
                vs = zone_vectors(n, build, z)
                if not vs:
                    fail = f"zone {z['name']}: {zone_failure(n, build, z)}"
                    break
                topo.append(vs)
            seqs = dielectric_sequences(n, build, doc)
            if not seqs:
                rows.append({**base, "status": "pruned", "reason": "build needs more layers"})
                continue
            if fail:
                rows.append({**base, "dielectrics": f"{len(seqs)} sequences", "status": "pruned",
                             "reason": fail})
                continue
            for seq in seqs:
                row = {**base, "dielectrics": "/".join(d["name"] for d in seq)}
                lv = laser_levels(n, build)
                if any(d["um"] > laser_max for d in seq[:lv] + seq[len(seq) - lv:]):
                    rows.append({**row, "status": "pruned", "reason": "laser dielectric too thick"})
                    continue
                t_cu, ztop, total = geometry(n, build, seq, doc)
                row["thickness_mm"] = round(total / 1000, 3)
                if total / 1000 < th["min_mm"] or total / 1000 + th.get("tolerance_mm", 0) > th["max_mm"]:
                    # the value is in the thickness column; one reason text groups the summary
                    side = "under" if total / 1000 < th["min_mm"] else "over"
                    rows.append({**row, "status": "pruned",
                                 "reason": f"thickness {side} {th['min_mm']}-{th['max_mm']} mm "
                                           f"(with ±{th.get('tolerance_mm', 0)} mm)"})
                    continue
                per_zone, why = [], None
                slayers = make_layers(n, build, seq, doc)
                for z, vs in zip(zones, topo):
                    keep, fails = {}, {}
                    zpair = z.get("pair_max_um", pair_max)
                    inner_only = not z.get("outer_power_counts", True)
                    for v in vs:
                        if z.get("paired_power") and not any(seq[k]["um"] <= zpair for k in pair_candidates(v, inner_only)):
                            fails["pair"] = fails.get("pair", 0) + 1
                            continue
                        if z.get("rf") and not any(rf_distance_ok(v, ztop, t_cu, n, doc["rf"], sd) is not None
                                                   for sd in ("top", "bottom")):
                            fails["rf"] = fails.get("rf", 0) + 1
                            continue
                        if gmask(v) in keep:
                            # equal ground mask: keep the map with the thinner pairing
                            if z.get("paired_power") and (_pair_um(v, seq, inner_only)
                                                          < _pair_um(keep[gmask(v)], seq, inner_only)):
                                keep[gmask(v)] = v
                            continue
                        if not fast_layers_ok(doc, slayers, n, v):
                            fails["narrow"] = fails.get("narrow", 0) + 1
                            continue
                        keep[gmask(v)] = v
                    if not keep:
                        msg = {"pair": f"no power plane paired with a ground within {zpair} µm",
                               "rf": f"no ground {doc['rf']['min_um']}-{doc['rf']['max_um']} µm from an outer layer",
                               "narrow": f"every fast layer it could use is narrower than "
                                         f"{doc['rules'].get('min_trace_um')} µm for "
                                         f"{doc['rules'].get('width_gate_class')}"}
                        why = f"zone {z['name']}: " + "; ".join(msg[k] for k in ("narrow", "rf", "pair") if k in fails)
                        break
                    per_zone.append([(v, m) for m, v in keep.items()])
                if why:
                    rows.append({**row, "status": "pruned", "reason": why})
                    continue
                res = best_assignment(per_zone, n)
                if res is None:
                    rows.append({**row, "status": "pruned", "reason": "no combination"})
                    continue
                chosen, full, local, allg = res
                rf_um, pair_um = None, None
                for z, v in zip(zones, chosen):
                    if z.get("rf"):
                        ds = [rf_distance_ok(v, ztop, t_cu, n, doc["rf"], s) for s in ("top", "bottom")]
                        rf_um = min((d for d in ds if d is not None), default=None)
                    if z.get("paired_power"):
                        p = _pair_um(v, seq, not z.get("outer_power_counts", True))
                        pair_um = p if pair_um is None else max(pair_um, p)
                row.update({
                    "status": "pass", "full_grounds": full, "zone_local_grounds": local,
                    "cost_class": COST[build], "rf_um": None if rf_um is None else round(rf_um, 1),
                    "pair_um": pair_um,
                    "full_ground_layers": " ".join(f"L{i + 1}" for i in range(n) if allg >> i & 1) or "none",
                    "zone_maps": "; ".join(f"{z['name']}: " + " ".join(v) for z, v in zip(zones, chosen)),
                })
                row.update({"_seq": seq, "_zones": zones, "_chosen": chosen})
                rows.append(row)
    impedance_stage(rows, doc)
    rank(rows, doc)
    return rows


def _pair_um(v, seq, inner_only=False):
    """Thinnest dielectric between a power layer and a ground layer of a zone map."""
    c = pair_candidates(v, inner_only)
    return min(seq[k]["um"] for k in c) if c else float("inf")


def make_layers(n, build, seq, doc):
    """The candidate as stackup.py layers (top to bottom), for the impedance solver."""
    import stackmodel as su
    t_cu, _, _ = geometry(n, build, seq, doc)
    cu = doc["copper"]
    layers = [su.Layer(name="Mask top", type="mask", t=cu.get("mask_um", 0), er=cu.get("mask_er", 3.8))]
    for i in range(n):
        # an inner trace is pressed into the prepreg next to it (a core does not flow)
        fills = ""
        if 0 < i < n - 1:
            fills = "above" if seq[i - 1]["kind"] == "prepreg" or seq[i]["kind"] != "prepreg" else "below"
        layers.append(su.Layer(name=f"L{i + 1}", type="copper", t=t_cu[i], fills=fills))
        if i < n - 1:
            layers.append(su.Layer(name=f"D{i + 1}", type="dielectric", t=seq[i]["um"], er=seq[i]["er"]))
    layers.append(su.Layer(name="Mask bottom", type="mask", t=cu.get("mask_um", 0), er=cu.get("mask_er", 3.8)))
    return layers


_WIDTH_CACHE = {}


def _slice_key(layers, n, i, refs):
    idx = [k for k, L in enumerate(layers) if L.name in (f"L{i + 1}",) + refs]
    outer = i in (0, n - 1)
    lo, hi = min(idx) - (1 if outer and i == 0 else 0), max(idx) + (1 if outer and i == n - 1 else 0)
    return tuple((L.type, L.t, L.er, L.name == f"L{i + 1}", L.name in refs) for L in layers[lo:hi + 1])


def solve_width(doc, layers, n, i, refs, cls_def):
    """Drawn width of one class on layer i over refs (draft solve, cached on the local slice)."""
    import stackcheck as sc
    import stackmodel as su
    cname, typ, z, gap = cls_def
    outer = i in (0, n - 1)
    key = (_slice_key(layers, n, i, refs), cname)
    if key not in _WIDTH_CACHE:
        g = gap[0] if outer else gap[1]
        cls = {"name": cname, "type": typ, "target_ohm": z, "layer": f"L{i + 1}",
               "reference": list(refs), "gap_um": g if typ == "diff" else None}
        sdoc = {"etch": doc.get("etch", {})}
        w = sc._search(lambda w: su.impedance(sdoc, layers, cls, su.Geometry(w, cls["gap_um"]), True)[0],
                       20.0, 1500.0, z, increasing=False, tol=0.25)
        # near the fab's minimum the draft mesh (about 0.3 % low in Z) decides: refine on the
        # fine mesh, as `design` does
        if abs(w - doc.get("rules", {}).get("min_trace_um", 0)) < 4.0:
            w = sc._refine(lambda w: su.impedance(sdoc, layers, cls, su.Geometry(w, cls["gap_um"]))[0], w, z)
        _WIDTH_CACHE[key] = round(w, 1)
    return _WIDTH_CACHE[key]


def fast_layers_ok(doc, layers, n, v):
    """Every F layer of a role vector can carry the narrowest class at or above the fab minimum."""
    min_w = doc.get("rules", {}).get("min_trace_um", 0)
    gate = doc.get("rules", {}).get("width_gate_class")
    defs = {c[0]: c for c in doc.get("classes", [])}
    if not min_w or gate not in defs:
        return True
    for i, role in enumerate(v):
        if role != "F":
            continue
        refs = tuple(f"L{j + 1}" for j in (i - 1, i + 1) if 0 <= j < n and v[j] == "G")
        if solve_width(doc, layers, n, i, refs, defs[gate]) < min_w:
            return False
    return True


def class_widths(n, build, seq, doc, zones, chosen):
    """Drawn widths of every impedance class on every fast layer the zone maps use."""
    layers = make_layers(n, build, seq, doc)
    out = {}
    for v in chosen:
        for i, role in enumerate(v):
            if role != "F":
                continue
            refs = tuple(f"L{j + 1}" for j in (i - 1, i + 1) if 0 <= j < n and v[j] == "G")
            out[f"L{i + 1}"] = {c[0]: solve_width(doc, layers, n, i, refs, c) for c in doc.get("classes", [])}
    return out


def impedance_stage(rows, doc):
    """Impedance geometry for the best few candidates of each layer count and build; a candidate
    whose narrowest class is under the fab's minimum is pruned."""
    min_w = doc.get("rules", {}).get("min_trace_um", 0)
    top_k = doc.get("rules", {}).get("impedance_top_k", 5)
    if not doc.get("classes"):
        return
    groups = {}
    for r in rows:
        if r["status"] == "pass":
            groups.setdefault((r["layers"], r["build"]), []).append(r)
    for grp in groups.values():
        grp.sort(key=lambda r: (r["full_grounds"], r["zone_local_grounds"], abs((r["rf_um"] or 235) - 235)))
        for r in grp[:top_k]:
            widths = class_widths(r["layers"], r["build"], r["_seq"], doc, r["_zones"], r["_chosen"])
            r["widths_um"] = json.dumps(widths, sort_keys=True)
            narrow = min(w for d in widths.values() for w in d.values()) if widths else None
            r["min_width_um"] = narrow
            if narrow is not None and narrow < min_w:
                r["status"], r["reason"] = "pruned", f"narrowest impedance trace {narrow} µm < {min_w} µm"
        for r in grp[top_k:]:
            r["status"], r["reason"] = "not ranked", f"impedance not computed (beyond the best {top_k} of its group)"


def rank(rows, doc):
    mid = (doc["rf"]["min_um"] + doc["rf"]["max_um"]) / 2 if "rf" in doc else 0
    passing = [r for r in rows if r["status"] == "pass"]
    passing.sort(key=lambda r: (r["layers"], r["full_grounds"], r["cost_class"], r["zone_local_grounds"],
                                abs((r["rf_um"] or mid) - mid), abs(r["thickness_mm"] - doc["thickness"].get("target_mm", r["thickness_mm"]))))
    for i, r in enumerate(passing, 1):
        r["rank"] = i


COLUMNS = ["variant", "rank", "layers", "build", "dielectrics", "thickness_mm", "status", "reason", "full_grounds",
           "full_ground_layers", "zone_local_grounds", "cost_class", "rf_um", "pair_um", "min_width_um", "widths_um",
           "zone_maps"]


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r.get("variant", ""), r.get("rank") or 10 ** 9, r["layers"],
                                             r["build"], r.get("dielectrics", ""))):
            w.writerow(r)


def summary(rows):
    """Per variant, layer count and build: candidates, passes, the best row, and the pruning
    reasons."""
    out = []
    keyf = lambda r: (r.get("variant", ""), r["layers"], r["build"])  # noqa: E731
    for key, grp in itertools.groupby(sorted(rows, key=keyf), key=keyf):
        grp = list(grp)
        ok = [r for r in grp if r["status"] == "pass"]
        reasons = {}
        for r in grp:
            if r["status"] != "pass":
                reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        best = min(ok, key=lambda r: r["rank"]) if ok else None
        out.append({"variant": key[0], "layers": key[1], "build": key[2], "candidates": len(grp),
                    "pass": len(ok), "best": best, "pruned": reasons})
    return out


def _parse_value(text):
    """A --set value: a TOML value (number, string, boolean, array), else the bare string."""
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def set_path(doc, key, value, new=False, where="--set"):
    """Set a dotted key. A list of tables is indexed by the `name` of its entry
    (zone.core.power_planes, dielectric.c430.um). new: the last key may be new."""
    parts = key.split(".")
    node = doc
    for p in parts[:-1]:
        if isinstance(node, list):
            hit = [e for e in node if isinstance(e, dict) and e.get("name") == p]
            if not hit:
                raise ExploreError(f"{where} {key}: no entry named {p!r}")
            node = hit[0]
            continue
        if p not in node:
            raise ExploreError(f"{where} {key}: no table {p!r}")
        node = node[p]
    last = parts[-1]
    if isinstance(node, list) or (not new and last not in node):
        raise ExploreError(f"{where} {key}: no key {last!r}")
    if value is None:
        node.pop(last, None)
    else:
        node[last] = value


def apply_overrides(doc, sets):
    """--set table.key=value: a sensitivity run without editing the file."""
    for item in sets:
        key, _, value = item.partition("=")
        v = _parse_value(value)
        set_path(doc, key, float(v) if isinstance(v, int) and not isinstance(v, bool) else v)


def variants(doc):
    """The documents to explore: the file itself, or one per [[variant]]. A variant has a
    `name`, a `set` table of dotted keys (as --set; zone and dielectric entries by name, new
    keys allowed) and an `unset` list of dotted keys to remove (a zone need dropped)."""
    out = []
    for var in doc.get("variant", []):
        if "name" not in var:
            raise ExploreError("[[variant]] without a name")
        d = copy.deepcopy({k: v for k, v in doc.items() if k != "variant"})
        for key, value in var.get("set", {}).items():
            set_path(d, key, value, new=True, where=f"variant {var['name']}:")
        for key in var.get("unset", []):
            set_path(d, key, None, new=True, where=f"variant {var['name']}:")
        out.append((var["name"], d))
    return out or [("", doc)]


def explore_all(doc):
    rows = []
    for name, d in variants(doc):
        _WIDTH_CACHE.clear()
        for r in explore(d):
            r["variant"] = name
            rows.append(r)
    return rows


def main(args):
    doc = load(args.file)
    apply_overrides(doc, getattr(args, "set", []))
    rows = explore_all(doc)
    if args.csv:
        write_csv(rows, args.csv)
    summ = summary(rows)
    if args.json:
        print(json.dumps({"schema": "opendap.stackup-explore/v1", "summary": summ}, indent=2, default=str))
    else:
        for s in summ:
            b = s["best"]
            print((f"[{s['variant']}] " if s["variant"] else "") + f"{s['layers']:>2} layers {s['build']:<9} candidates {s['candidates']:>5}  pass {s['pass']:>5}"
                  + (f"  best #{b['rank']}: {b['dielectrics']} {b['thickness_mm']} mm, full grounds "
                     f"{b['full_grounds']} ({b['full_ground_layers']}), zone-local {b['zone_local_grounds']}, "
                     f"RF {b['rf_um']} µm, pair {b['pair_um']} µm" if b else ""))
            for reason, k in sorted(s["pruned"].items(), key=lambda t: -t[1]):
                print(f"      pruned {k:>5}: {reason}")
        for r in rows:
            if r.get("rank") == 1:
                print("top" + (f" [{r['variant']}]" if r.get("variant") else "") + ": "
                      + json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, default=str))
    return 0 if any(r["status"] == "pass" for r in rows) else 1
