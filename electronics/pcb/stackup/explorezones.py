"""odw stackup explore's model: the builds, the roles a layer may take under each zone of a board, the
symmetric dielectric sequences from the fab's list, and the geometry they give. The search and the ranking
are in explore.py. Method: docs/specs/stackup-method.md §5."""
from __future__ import annotations

import itertools
import math
import tomllib


ROLES_INNER = ("G", "P", "F", "D", "L")
# laser levels per side; "any-layer" has a laser level at every dielectric
BUILDS = {"through": 0, "1+N+1": 1, "2+N+2": 2, "3+N+3": 3, "any-layer": None}
COST = {"through": 0, "1+N+1": 1, "2+N+2": 2, "3+N+3": 3, "any-layer": 4}


def laser_levels(n, build):
    """Laser levels per side of a build on n copper layers (any-layer: every dielectric)."""
    b = BUILDS[build]
    return n - 1 if b is None else b


class ExploreError(Exception):
    pass


def load(path):
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ExploreError(f"{path}: {e}")
    if doc.get("version") != 1:
        raise ExploreError(f"{path}: version = 1 expected")
    for key in ("layer_counts", "builds", "thickness", "copper", "dielectric", "zone"):
        if key not in doc:
            raise ExploreError(f"{path}: missing {key}")
    for b in doc["builds"]:
        if b not in BUILDS:
            raise ExploreError(f"unknown build {b!r} ({', '.join(BUILDS)})")
    return doc


# ---------------------------------------------------------------- roles per zone

def _neighbours(r, i):
    return [r[j] for j in (i - 1, i + 1) if 0 <= j < len(r)]


def layer_ok(r, i, zone):
    """Local rule for layer i of role vector r (index 0 = top, n - 1 = bottom)."""
    role, nb = r[i], _neighbours(r, i)
    if role == "F":
        return all(x == "G" for x in nb)
    if role == "D":
        return (zone.get("fast_dual_ok", False) and "G" in nb and "P" not in nb
                and any(x in ("D", "L") for x in nb))
    if role == "L":
        return "G" in nb or zone.get("low_speed_next_to_power", False)
    return True


def outer_roles(zone, side):
    """Allowed roles of the outer layer on SIDE ('top' or 'bottom') in a zone."""
    fast = zone.get("outer_fast", "both")
    need_fast = fast == "both" or fast == side or (fast == "soc" and side == zone.get("soc", "bottom"))
    if need_fast:
        return ("F",)
    out = ["F", "L"]
    if side in zone.get("outer_power_ok", []):
        out.append("P")
    return tuple(out)


def zone_vectors(n, build, zone):
    """Every role vector of a zone that meets its topology needs, by depth-first generation
    with the local rules checked as soon as both neighbours are known."""
    if zone.get("soc_side_inner_fast") and build == "through":
        return []          # the 0.4 mm ring of a fine-pitch BGA needs microvias
    tops, bots = outer_roles(zone, "top"), outer_roles(zone, "bottom")
    out = []
    r = [None] * n

    def rec(i):
        if i == n:
            if layer_ok(r, n - 1, zone) and (n < 2 or layer_ok(r, n - 2, zone)) and needs_ok(r, n, build, zone):
                out.append(tuple(r))
            return
        choices = tops if i == 0 else (bots if i == n - 1 else ROLES_INNER)
        for c in choices:
            r[i] = c
            if i >= 1 and not layer_ok(r, i - 1, zone):
                continue
            rec(i + 1)
        r[i] = None
    rec(0)
    return out


def needs_ok(r, n, build, zone, skip=()):
    inner = range(1, n - 1)
    fast_inner = [i for i in inner if r[i] == "F" or (r[i] == "D" and zone.get("fast_dual_ok"))]
    if "inner_fast" not in skip and len(fast_inner) < zone.get("inner_fast", 0):
        return False
    if "stripline_gg" not in skip and sum(1 for i in inner if r[i] == "F") < zone.get("stripline_gg", 0):
        return False
    need = zone.get("soc_side_inner_fast", 0)
    if need and "soc_side_inner_fast" not in skip:
        soc = n - 1 if zone.get("soc", "bottom") == "bottom" else 0
        step = -1 if soc == n - 1 else 1
        b = BUILDS[build]
        if b is None:
            # any-layer: a laser level at every dielectric, stacked microvias reach every layer;
            # the SoC side's own reference stays ground
            if r[soc + step] != "G" or len(fast_inner) < need:
                return False
        elif b >= 2:
            # the stacked microvias reach the layers up to b laser levels in; the first level is
            # the SoC side's ground, the escape layer is one of the levels behind it
            if r[soc + step] != "G":
                return False
            if not any(r[soc + k * step] == "F" for k in range(2, b + 1) if 0 < soc + k * step < n - 1):
                return False
            if len(fast_inner) < need:
                return False
        elif b == 1:
            # one laser level: the escape layer is reached by a buried via stacked on the
            # microvia; a BGA whose escape sites only hold a laser land (`escape_laser_only`,
            # a fine-pitch ring) cannot use it
            if zone.get("escape_laser_only") or r[soc + step] != "G" or len(fast_inner) < need:
                return False
        else:
            return False
    # outer P layers count as power planes unless the zone keeps its outer layers for parts
    # (decoupling): `outer_power_counts = false`
    planes = [i for i in range(n) if r[i] == "P"
              and (zone.get("outer_power_counts", True) or 0 < i < n - 1)]
    if "power_planes" not in skip and len(planes) < zone.get("power_planes", 0):
        return False
    if "pour_layers" not in skip and sum(1 for i in inner if r[i] == "P") < zone.get("pour_layers", 0):
        return False
    if "paired_power" not in skip and zone.get("paired_power", 0) and not pair_candidates(
            r, not zone.get("outer_power_counts", True)):
        return False
    if "rf" not in skip and zone.get("rf") and not any(x == "G" for x in r[1:-1]):
        return False
    return True


def pair_candidates(r, inner_only=False):
    """Dielectric indices (between copper i and i + 1) where a P layer faces a G layer
    (inner_only: the P layer is not an outer layer)."""
    n = len(r)
    return frozenset(i for i in range(n - 1) if {r[i], r[i + 1]} == {"P", "G"}
                     and not (inner_only and ((r[i] == "P" and i == 0) or (r[i + 1] == "P" and i + 1 == n - 1))))


def zone_failure(n, build, zone):
    """The needs whose relaxation makes an infeasible zone feasible (the reason it failed)."""
    if zone.get("soc_side_inner_fast") and build == "through":
        return "the fine-pitch BGA ring needs microvias (no through-hole build)"
    if zone.get("soc_side_inner_fast") and zone.get("escape_laser_only") and BUILDS[build] == 1:
        return "the BGA escape needs stacked laser vias to its inner layer (escape_laser_only; one laser level)"
    names = ["inner_fast", "stripline_gg", "soc_side_inner_fast", "power_planes", "pour_layers",
             "paired_power", "rf"]
    blocking = []
    for name in names:
        if not zone.get(name):
            continue
        z2 = dict(zone)
        z2.pop(name)
        if zone_vectors(n, build, z2):
            blocking.append(name)
    return "needs " + (" or ".join(blocking) if blocking else "more than the layers give")


# ---------------------------------------------------------------- dielectrics

def dielectric_sequences(n, build, doc):
    """Symmetric dielectric sequences (n - 1 dielectrics, top to bottom) for a build."""
    b = BUILDS[build]
    pal = doc["dielectric"]
    rules = doc.get("rules", {})
    laser_max = rules.get("laser_max_um", 150)
    laser = [d for d in pal if d.get("laser")]
    prepreg = [d for d in pal if d["kind"] == "prepreg"]
    core = [d for d in pal if d["kind"] == "core"]
    if b is None:
        # any-layer: every dielectric is laser-drilled: laser prepregs, and a thin core in the
        # middle (a core within the laser limit is drilled as the prepregs are)
        thin = [d for d in core if d["um"] <= laser_max]
        kinds = ["core" if i == (n - 2) // 2 else "laser" for i in range(n - 1)]
        options_of = {"laser": laser, "core": thin or laser}
    else:
        m = n - 2 * b               # copper layers of the core sub-composite
        if m < 2:
            return []
        if b == 0:
            kinds = ["prepreg" if i % 2 == 0 else "core" for i in range(m - 1)]
        else:
            kinds = ["core" if i % 2 == 0 else "prepreg" for i in range(m - 1)]
        kinds = ["laser"] * b + kinds + ["laser"] * b
        options_of = {"laser": laser, "prepreg": prepreg, "core": core}
    if not rules.get("symmetric", True):
        # asymmetric stacks: every position chosen on its own (warpage is the fab's to judge)
        options = [options_of[k] for k in kinds]
        count = math.prod(len(o) for o in options)
        if count > rules.get("max_sequences", 500000):
            raise ExploreError(f"{n} layers {build}: {count} asymmetric sequences exceed "
                               f"rules.max_sequences ({rules.get('max_sequences', 500000)})")
        return [list(p) for p in itertools.product(*options)]
    half = (len(kinds) + 1) // 2
    options = [options_of[k] for k in kinds[:half]]
    seqs = []
    for pick in itertools.product(*options):
        full = list(pick) + list(reversed(pick[:len(kinds) - half]))
        seqs.append(full)
    return seqs


def geometry(n, build, seq, doc):
    """Copper thicknesses, z of each copper top surface and the total thickness (µm)."""
    cu = doc["copper"]
    b = laser_levels(n, build)
    t_cu = []
    for i in range(n):
        if i in (0, n - 1):
            t = cu["outer_um"]
        elif b >= 1 and i in (1, n - 2):
            t = cu.get("hdi_plated_um", cu["inner_um"])
        else:
            t = cu["inner_um"]
        t_cu.append(t)
    z, ztop = cu.get("mask_um", 0), []
    for i in range(n):
        ztop.append(z)
        z += t_cu[i]
        if i < n - 1:
            z += seq[i]["um"]
    total = z + cu.get("mask_um", 0)
    return t_cu, ztop, total


def rf_distance_ok(r, ztop, t_cu, n, rf, side):
    """An outer F layer on SIDE with a G layer within [min, max] µm of its copper surface."""
    if side == "top":
        if r[0] != "F":
            return None
        base = ztop[0] + t_cu[0]
        cands = [(i, ztop[i] - base) for i in range(1, n - 1) if r[i] == "G"]
    else:
        if r[n - 1] != "F":
            return None
        base = ztop[n - 1]
        cands = [(i, base - (ztop[i] + t_cu[i])) for i in range(1, n - 1) if r[i] == "G"]
    ok = [d for _, d in cands if rf["min_um"] <= d <= rf["max_um"]]
    return min(ok, key=lambda d: abs(d - (rf["min_um"] + rf["max_um"]) / 2)) if ok else None
