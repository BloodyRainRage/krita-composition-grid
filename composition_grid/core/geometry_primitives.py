"""Geometry primitives (Segment, Arc, Polygon, Dot) and GridGeometry type.

Pure math core: no Qt, no krita — only stdlib.

Coordinates are in normalized document space with y down (screen-style).
Arc angles are degrees in that same screen convention: ``a0 = 0`` starts at
the +x axis (point ``center + (rx, 0)``), samples are computed as
``(cx + rx*cos(t), cy + ry*sin(t))`` for ``t`` linear from ``a0`` to ``a1``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from composition_grid.core.geometry import Bounds, Point


@dataclass(frozen=True)
class Segment:
    p1: Point
    p2: Point


@dataclass(frozen=True)
class Arc:
    center: Point
    rx: float
    ry: float
    a0: float
    a1: float


@dataclass(frozen=True)
class Polygon:
    points: tuple[Point, ...]


@dataclass(frozen=True)
class Dot:
    position: Point


GridGeometry = tuple[Segment | Arc | Polygon | Dot, ...]


def arc_points(arc: Arc, n: int = 24) -> list[Point]:
    """Sample the arc linearly in angle from a0 to a1, inclusive."""
    cx, cy = arc.center
    rad0 = math.radians(arc.a0)
    rad1 = math.radians(arc.a1)
    if n < 2:
        return [(cx + arc.rx * math.cos(rad0), cy + arc.ry * math.sin(rad0))]
    step = (rad1 - rad0) / (n - 1)
    return [
        (
            cx + arc.rx * math.cos(rad0 + i * step),
            cy + arc.ry * math.sin(rad0 + i * step),
        )
        for i in range(n)
    ]


def _point_within(p: Point, bounds: Bounds, tol: float) -> bool:
    return (
        bounds.x - tol <= p[0] <= bounds.x + bounds.w + tol
        and bounds.y - tol <= p[1] <= bounds.y + bounds.h + tol
    )


# Liang-Barsky parallel-edge tolerance for the degenerate (zero-length) case.
_EPS = 1e-12


def clip_segment_to_bounds(seg: "Segment", bounds: Bounds) -> "Segment | None":
    """Clip ``seg`` to the axis-aligned ``bounds`` (Liang-Barsky formulation).

    Returns the clipped Segment, or ``None`` when the segment lies fully
    outside ``bounds`` (no part of it crosses the rectangle). Degenerate
    inputs are handled: both endpoints inside -> the segment is returned
    unchanged; a zero-length segment inside -> returned unchanged; a
    zero-length segment outside -> ``None`` (touching a corner counts as
    inside: endpoint exactly on the rect boundary is kept).
    """
    dx = seg.p2[0] - seg.p1[0]
    dy = seg.p2[1] - seg.p1[1]
    x0, y0 = seg.p1
    t0, t1 = 0.0, 1.0
    xmin, ymin = bounds.x, bounds.y
    xmax, ymax = bounds.x + bounds.w, bounds.y + bounds.h

    for p, q in (
        (-dx, x0 - xmin),  # left edge
        (dx, xmax - x0),   # right edge
        (-dy, y0 - ymin),  # top edge
        (dy, ymax - y0),   # bottom edge
    ):
        if abs(p) <= _EPS:
            # Segment parallel to this pair of edges: outside if q < 0.
            if q < -_EPS:
                return None
            continue
        r = q / p
        if p < 0.0:
            if r > t1:
                return None
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return None
            if r < t1:
                t1 = r

    a = (x0 + t0 * dx, y0 + t0 * dy)
    b = (x0 + t1 * dx, y0 + t1 * dy)
    return Segment(a, b)


def primitive_within_bounds(prim, bounds: Bounds, tol: float = 1e-9) -> bool:
    """Whether every vertex/sample of the primitive lies within ``bounds``.

    - Segment: p1 and p2 inside
    - Arc: all 24 sampled points inside
    - Polygon: all vertices inside
    - Dot: position inside
    """
    if isinstance(prim, Segment):
        return _point_within(prim.p1, bounds, tol) and _point_within(
            prim.p2, bounds, tol
        )
    if isinstance(prim, Arc):
        return all(_point_within(p, bounds, tol) for p in arc_points(prim))
    if isinstance(prim, Polygon):
        return all(_point_within(p, bounds, tol) for p in prim.points)
    if isinstance(prim, Dot):
        return _point_within(prim.position, bounds, tol)
    raise TypeError(f"unsupported primitive: {prim!r}")
