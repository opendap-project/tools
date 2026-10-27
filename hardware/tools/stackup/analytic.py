"""Closed-form transmission-line formulas used to validate the field solver.

- Cohn (1954): centred stripline, zero-thickness strip, exact (conformal mapping).
- Hammerstad and Jensen (1980), as given in Wadell, *Transmission Line Design Handbook*
  (1991), §3.5: microstrip with the thickness correction, quasi-static; about 1 % against
  field solvers for 0.01 <= w/h <= 100. KiCad's transmission-line calculator uses the same
  model for microstrip (at DC, before its dispersion terms).
- IPC-2141A (2004), the simple formulas: microstrip and stripline, a few per cent within their
  ranges (0.1 < w/h < 2.0, 1 < er < 15), quoted for comparison only.

Units: micrometres and ohms.
"""
from __future__ import annotations

import math

from scipy.special import ellipk

ETA0 = 376.730313668


def cohn_stripline(w, b, er):
    """Exact Z0 of a zero-thickness strip of width w centred between planes b apart."""
    k = 1 / math.cosh(math.pi * w / (2 * b))
    kp = math.tanh(math.pi * w / (2 * b))
    return ETA0 / (4 * math.sqrt(er)) * ellipk(k * k) / ellipk(kp * kp)


def _z01(u):
    f = 6 + (2 * math.pi - 6) * math.exp(-((30.666 / u) ** 0.7528))
    return ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))


def _eps_e(u, er):
    a = (1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49
         + math.log(1 + (u / 18.1) ** 3) / 18.7)
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def hammerstad_jensen_microstrip(w, h, t, er):
    """(Z0, eps_eff) of an uncoated microstrip, Hammerstad and Jensen with thickness correction."""
    u = w / h
    if t > 0:
        tn = t / h
        du1 = tn / math.pi * math.log(1 + 4 * math.e / (tn / math.tanh(math.sqrt(6.517 * u)) ** 2))
        dur = 0.5 * (1 + 1 / math.cosh(math.sqrt(er - 1))) * du1
    else:
        du1 = dur = 0.0
    u1, ur = u + du1, u + dur
    eeff = _eps_e(ur, er) * (_z01(u1) / _z01(ur)) ** 2
    z0 = _z01(ur) / math.sqrt(_eps_e(ur, er))
    return z0, eeff


def ipc2141_microstrip(w, h, t, er):
    return 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))


def ipc2141_stripline(w, b, t, er):
    return 60 / math.sqrt(er) * math.log(4 * b / (0.67 * math.pi * (0.8 * w + t)))


def kicad_microstrip(w, h, t, er):
    """Z0 of KiCad's transmission-line calculator, microstrip, static part, no top cover.

    Transcribed from KiCad `common/transline_calculations/microstrip.cpp`, MICROSTRIP::
    microstrip_Z0() (master, read 2026-10-24; GPL-2.0-or-later, derived from Qucs): Hammerstad
    and Jensen with KiCad's extra thickness term q_t in the filling factor. The cover terms
    vanish for an infinitely distant cover. Used only to compare the solver with the number
    KiCad's calculator shows.
    """
    def dut(u, th, e):
        if th <= 0:
            return 0.0
        d = th / math.pi * math.log(1 + 4 * math.e * math.tanh(math.sqrt(6.517 * u)) ** 2 / th)
        return 0.5 * d * (1 + 1 / math.cosh(math.sqrt(e - 1)))

    def fill(u, e):
        a = 1 + math.log((u ** 4 + u ** 2 / 2704) / (u ** 4 + 0.432)) / 49 + math.log(1 + u ** 3 / 5929.741) / 18.7
        b = 0.564 * ((e - 0.9) / (e + 3)) ** 0.053
        return (1 + 10 / u) ** (-a * b)

    u, th = w / h, t / h
    u += dut(u, th, er)
    q = fill(u, er) - (2 * math.log(2) / math.pi) * (th / math.sqrt(u))
    eeff_t = 0.5 * (er + 1) + 0.5 * q * (er - 1)
    return _z01(u) / math.sqrt(eeff_t)
