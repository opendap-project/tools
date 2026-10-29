"""CRAFT-13 geometry: whether a silk mark stands next to pin 1, as the PCB craft standard states it.

Next to pin 1: the mark's centre lies outside the part's body (its fabrication outline; the courtyard
where the part has none) and is nearer to the edge of pin 1's land than to the edge of any other land.
Distances run to the land's edge, not its centre, so a long pin-1 land counts along its whole length;
a mark as near to another land as to pin 1 marks neither. Pads drawn on one land (a USB-C receptacle's
A1 and B12) are one land: it is pin 1's when one of its numbers is.

Pure geometry, no pcbnew: boxes are (x0, y0, x1, y1) in millimetres. Keep it compatible with Python 3.9.
The libs repository's test_library.py applies the same rule to the library footprints.
"""

from __future__ import annotations

import math

EPS = 1e-6


def box_gap(x, y, box):
    """Distance from a point to a box's edge, 0 inside it."""
    dx = max(box[0] - x, 0.0, x - box[2])
    dy = max(box[1] - y, 0.0, y - box[3])
    return math.hypot(dx, dy)


def inside(x, y, box):
    """Strictly inside the box (a point on its edge is outside)."""
    return box[0] + EPS < x < box[2] - EPS and box[1] + EPS < y < box[3] - EPS


def next_to_pin1(centre, body, lands, first):
    """True when a mark centred at `centre` is next to pin `first`.

    body: the part's body box; lands: [(number, box)], one entry per copper pad (a number may repeat,
    and pads on one land share its box).
    """
    x, y = centre
    if inside(x, y, body):
        return False
    merged = {}
    for number, box in lands:
        key = tuple(round(v, 4) for v in box)
        merged.setdefault(key, set()).add(number)
    own = [box_gap(x, y, box) for box, numbers in merged.items() if first in numbers]
    if not own:
        return False
    others = [box_gap(x, y, box) for box, numbers in merged.items() if first not in numbers]
    return all(min(own) < g - EPS for g in others)
