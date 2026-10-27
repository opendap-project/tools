"""2D quasi-static (quasi-TEM) field solver for PCB transmission-line cross-sections.

The cross-section is a rectangle of layered dielectrics between optional ground planes, with
rectangular signal conductors (and optional coplanar grounds). Laplace's equation is solved
with linear triangular finite elements (scikit-fem) on a graded tensor-product grid that has a
grid line on every conductor and dielectric edge. Each signal conductor is driven at 1 V in turn
(the others and every ground at 0 V); the Maxwell capacitance matrix is the energy matrix
Phi^T K Phi. The same is done in vacuum; the inductance matrix is L = C0^-1 / c^2. Single-ended
lines give Z0 = sqrt(L/C); a symmetric pair gives the odd and even modes, Zdiff = 2 Zodd.

Units: geometry in micrometres, results in ohms and picoseconds per millimetre.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from skfem import Basis, BilinearForm, ElementTriP0, ElementTriP1, MeshTri
from skfem.helpers import dot, grad

C0 = 299_792_458.0          # m/s
EPS0 = 8.8541878128e-12     # F/m


@dataclass
class Box:
    """Axis-aligned rectangle, micrometres."""
    x0: float
    x1: float
    y0: float
    y1: float

    def contains(self, x, y, tol=1e-9):
        return (x >= self.x0 - tol) & (x <= self.x1 + tol) & (y >= self.y0 - tol) & (y <= self.y1 + tol)


@dataclass
class CrossSection:
    """A 2D problem: dielectric boxes (later ones win), conductors, ground planes and extent.

    `bottom_ground` and `top_ground` put a 0 V boundary on the bottom and top edges of the
    domain (the reference planes). The side edges and, without `top_ground`, the top edge are
    0 V as well but placed far away (an enclosure much larger than the line), the usual
    truncation for a quasi-static solve; `extent_um` sets how far.
    """
    width_um: float
    y_min: float
    y_max: float
    background_er: float = 1.0
    dielectrics: list[tuple[Box, float]] = field(default_factory=list)
    signals: list[Box] = field(default_factory=list)
    grounds: list[Box] = field(default_factory=list)
    bottom_ground: bool = True
    top_ground: bool = False


@dataclass
class LineResult:
    z0: float                  # single-ended (or odd-mode for pairs, per line)
    eps_eff: float
    delay_ps_per_mm: float
    zdiff: float | None = None
    zodd: float | None = None
    zeven: float | None = None
    zcommon: float | None = None
    cap_pf_per_m: np.ndarray | None = None
    nodes: int = 0


def _graded_axis(breaks, lo, hi, fine, coarse, growth=1.15):
    """Grid coordinates that include every break, fine next to breaks, growing geometrically.

    The step is capped at `coarse` near the breaks and at a quarter of the distance from the
    nearest break further out, so the far field of an open (microstrip) problem stays cheap.
    """
    breaks = sorted({float(b) for b in breaks if lo <= b <= hi} | {lo, hi})
    pts = set(breaks)
    for a, b in zip(breaks[:-1], breaks[1:]):
        span = b - a
        if span <= fine * 1.0001:
            continue
        # grow from both ends towards the middle
        left, right = [a], [b]
        step = fine
        while left[-1] + step < (a + b) / 2 and right[-1] - step > (a + b) / 2:
            left.append(left[-1] + step)
            right.append(right[-1] - step)
            step = min(step * growth, max(coarse, 0.25 * (left[-1] - a)))
        pts.update(left)
        pts.update(right)
    return np.array(sorted(pts))


def _mesh(cs: CrossSection, fine: float, coarse: float):
    xb, yb = [], []
    boxes = [b for b, _ in cs.dielectrics] + cs.signals + cs.grounds
    for b in boxes:
        xb += [b.x0, b.x1]
        yb += [b.y0, b.y1]
    x = _graded_axis(xb, -cs.width_um / 2, cs.width_um / 2, fine, coarse)
    y = _graded_axis(yb, cs.y_min, cs.y_max, fine, coarse)
    return MeshTri.init_tensor(x, y)


def _assemble(mesh, cs: CrossSection, vacuum: bool):
    basis = Basis(mesh, ElementTriP1())
    if vacuum:
        er = np.ones(mesh.t.shape[1])
    else:
        cx, cy = mesh.p[:, mesh.t].mean(axis=1)
        er = np.full(mesh.t.shape[1], cs.background_er)
        for box, value in cs.dielectrics:
            er[box.contains(cx, cy)] = value
    basis0 = basis.with_element(ElementTriP0())
    epsr = basis0.interpolate(er)

    @BilinearForm
    def laplace(u, v, w):
        return w.epsr * dot(grad(u), grad(v))

    return laplace.assemble(basis, epsr=epsr) * EPS0 * 1.0  # F/m (2D, per unit length)


def _fixed_sets(mesh, cs: CrossSection):
    px, py = mesh.p
    ground = np.zeros(px.shape[0], dtype=bool)
    ground |= np.isclose(px, px.min()) | np.isclose(px, px.max())
    if cs.bottom_ground:
        ground |= np.isclose(py, py.min())
    ground |= np.isclose(py, py.max())  # enclosure (or the top plane)
    for g in cs.grounds:
        ground |= g.contains(px, py)
    sig = [s.contains(px, py) for s in cs.signals]
    return ground, sig


def capacitance_matrix(cs: CrossSection, vacuum: bool, fine: float, coarse: float):
    mesh = _mesh(cs, fine, coarse)
    K = _assemble(mesh, cs, vacuum).tocsr()
    ground, sig = _fixed_sets(mesh, cs)
    fixed = ground.copy()
    for s in sig:
        fixed |= s
    free = ~fixed
    Kff = K[free][:, free].tocsc()
    lu = spla.splu(Kff)
    n = len(sig)
    phis = []
    for i in range(n):
        phi = np.zeros(K.shape[0])
        phi[sig[i]] = 1.0
        rhs = -K[free][:, fixed] @ phi[fixed]
        phi[free] = lu.solve(rhs)
        phis.append(phi)
    P = np.array(phis)
    C = P @ (K @ P.T)   # F/m (coordinates in µm cancel in 2D)
    return C, mesh.p.shape[1]


def solve(cs: CrossSection, fine: float = 0.25, coarse: float = 5.0) -> LineResult:
    """Characteristic impedance of one line, or the modes of a symmetric pair."""
    C, nodes = capacitance_matrix(cs, vacuum=False, fine=fine, coarse=coarse)
    Cv, _ = capacitance_matrix(cs, vacuum=True, fine=fine, coarse=coarse)
    L = np.linalg.inv(Cv) / C0 ** 2
    if len(cs.signals) == 1:
        c, l = C[0, 0], L[0, 0]
        z0 = float(np.sqrt(l / c))
        eeff = float(C[0, 0] / Cv[0, 0])
        return LineResult(z0=z0, eps_eff=eeff, delay_ps_per_mm=np.sqrt(eeff) / C0 * 1e9,
                          cap_pf_per_m=C * 1e12, nodes=nodes)
    if len(cs.signals) == 2:
        c_odd, c_even = C[0, 0] - C[0, 1], C[0, 0] + C[0, 1]
        l_odd, l_even = L[0, 0] - L[0, 1], L[0, 0] + L[0, 1]
        zodd = float(np.sqrt(l_odd / c_odd))
        zeven = float(np.sqrt(l_even / c_even))
        cv_odd = Cv[0, 0] - Cv[0, 1]
        eeff = float(c_odd / cv_odd)
        return LineResult(z0=zodd, eps_eff=eeff, delay_ps_per_mm=np.sqrt(eeff) / C0 * 1e9,
                          zdiff=2 * zodd, zodd=zodd, zeven=zeven, zcommon=zeven / 2,
                          cap_pf_per_m=C * 1e12, nodes=nodes)
    raise ValueError("one signal conductor or a symmetric pair")
