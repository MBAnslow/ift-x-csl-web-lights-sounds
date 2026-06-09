"""Generators for sample webs."""
from __future__ import annotations

import math

from spiderweb.web import Web


def radial_web(center=None, spokes=8, rings=7, r0=None, dr=52.0, size=(900, 760)) -> Web:
    """A classic orb-weaver web: a central node, radial spokes, and `rings`
    concentric rings crossing them.

    Radial spacing is uniform by default (the centre-to-first-ring gap equals
    the ring-to-ring gap, i.e. r0 == dr), so a ripple/propagation from the
    centre reaches each ring at evenly spaced times.
    """
    if r0 is None:
        r0 = dr
    web = Web(size=tuple(size))
    cx, cy = center if center else (size[0] / 2, size[1] / 2)

    # central node first, so it takes chain index 0
    center_id = web.add_node(cx, cy, led=True).id

    grid: dict[tuple[int, int], int] = {}
    for s in range(spokes):
        ang = 2 * math.pi * s / spokes
        for r in range(rings):
            rad = r0 + dr * r
            x = cx + rad * math.cos(ang)
            y = cy + rad * math.sin(ang)
            grid[(s, r)] = web.add_node(x, y, led=True).id

    # spokes (radial strands), each starting at the centre
    for s in range(spokes):
        web.add_strand(center_id, grid[(s, 0)])
        for r in range(rings - 1):
            web.add_strand(grid[(s, r)], grid[(s, r + 1)])
    # rings (strands between neighbouring spokes)
    for r in range(rings):
        for s in range(spokes):
            web.add_strand(grid[(s, r)], grid[((s + 1) % spokes, r)])

    return web
