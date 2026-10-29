"""Diagonal Method grid type: 45-degree-in-doc diagonals and bisectors.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes), but unlike most grid types it USES the
real ``bounds`` aspect: the method's 45-degree rule holds in
document pixels, so every line carries a normalized slope computed from the
actual bounds; after the element transform scales normalized (x, y) ->
(w*x, h*y) each line is exactly 45 degrees in doc space.

Pinned build (ruled semantics: every line is a doc-45° line and the result
is mirror-symmetric about x=0.5 and y=0.5 for ANY aspect):
- Four corner diagonals: one 45°-in-doc line out of EACH normalized frame
  corner (slope magnitude w/h) heading into the frame; both horizontal
  directions are offered per corner and the one leaving the frame collapses
  to a point under clipping.  Landscape frames: the lines from the top
  corners meet the bottom edge (for w=1000, h=500 at (0.5, 1)); a square
  frame yields the two corner-to-corner diagonals; portrait frames span the
  short sides and the lines cross at (0.5, (w/h)/2).
- ``show_45_bisectors`` (default True): from the midpoint of each LONG side
  of the bounds (landscape: the top/bottom edge midpoints; portrait: the
  left/right edge midpoints) the two 45°-in-doc rays heading into the frame
  in BOTH horizontal (landscape) / BOTH vertical (portrait) directions, with
  normalized slope +/- w/h, clipped to bounds.  For the square frame this
  completes the classic star with the two corner diagonals.
- Repeats are dropped before returning (equal endpoints in either order,
  tolerance 1e-9): corner diagonals and bisector rays coincide for many
  aspects, and a stroke drawn twice would read darker.
- ``variant`` (resolved with :func:`resolve_choice`): ``"panels"`` adds the
  two midlines x=0.5 and y=0.5 (full-span segments).
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds, Point
from composition_grid.core.geometry_primitives import (
    GridGeometry,
    Segment,
    clip_segment_to_bounds,
)
from composition_grid.core.grids.base import GridType, Param, resolve_bool, resolve_choice
from composition_grid.core.grids.grid_registry import register

# Bisector ray length threshold: a clipped ray shorter than this does not
# cross the frame meaningfully (degenerate corner graze) and is discarded.
_MIN_RAY_LEN = 1e-9

# Duplicate match tolerance for the endpoint-pair de-duplication below.
_DUP_TOL = 1e-9

_FRAME = Bounds(x=0.0, y=0.0, w=1.0, h=1.0)

# Normalized frame corners, clockwise from the top-left.
_CORNERS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))

# Line extension: candidates start at a frame corner and are extended by a
# factor of 2 along their normalized direction before clipping, which always
# overshoots the unit frame regardless of the bounds aspect.
_EXT = 2.0


def _frame_segment(p1: Point, p2: Point) -> Segment | None:
    """Clip a frame-space candidate segment, dropping degenerate remains."""
    clipped = clip_segment_to_bounds(Segment(p1, p2), _FRAME)
    if clipped is None:
        return None
    dx = clipped.p2[0] - clipped.p1[0]
    dy = clipped.p2[1] - clipped.p1[1]
    if abs(dx) < _MIN_RAY_LEN and abs(dy) < _MIN_RAY_LEN:
        return None
    return clipped


def _same_point(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) < _DUP_TOL and abs(a[1] - b[1]) < _DUP_TOL


def _dedup(segments: list[Segment]) -> list[Segment]:
    """Keep the first occurrence of every line (endpoints in either order)."""
    kept: list[Segment] = []
    for seg in segments:
        duplicate = any(
            (_same_point(seg.p1, done.p1) and _same_point(seg.p2, done.p2))
            or (_same_point(seg.p1, done.p2) and _same_point(seg.p2, done.p1))
            for done in kept
        )
        if not duplicate:
            kept.append(seg)
    return kept


class DiagonalMethod(GridType):
    """45-degree (document space) diagonal grid with optional bisectors."""

    id = "diagonal_method"
    name = "Diagonal Method"
    params = (
        Param(name="show_45_bisectors", kind="bool", default=True),
        Param(
            name="variant",
            kind="choice",
            default="grid_only",
            choices=("grid_only", "panels"),
        ),
    )

    def build(self, bounds: Bounds, params: Mapping[str, Any]) -> GridGeometry:
        show_bisectors = resolve_bool(self, params, "show_45_bisectors")
        variant = resolve_choice(self, params, "variant")

        w, h = bounds.w, bounds.h
        if w <= 0.0 or h <= 0.0:
            return ()
        wh = w / h  # normalized slope of EVERY 45deg-in-doc line of this grid

        geometry: list[Segment] = []

        # Corner diagonals: a 45deg-in-doc line out of every frame corner,
        # heading into the frame.  Both horizontal directions are offered;
        # the one leaving the frame clips to a point and is dropped.
        for cx, cy in _CORNERS:
            dy_dir = 1.0 if cy == 0.0 else -1.0  # y down: a top corner dives
            for dx_dir in (1.0, -1.0):
                seg = _frame_segment(
                    (cx, cy),
                    (cx + dx_dir * _EXT, cy + dy_dir * wh * _EXT),
                )
                if seg is not None:
                    geometry.append(seg)

        # Bisectors: 45deg-in-doc rays from the two long-side midpoints, into
        # the frame in both directions along the long axis.
        if show_bisectors:
            if w >= h:
                rays = [
                    ((0.5, 0.0), (dx_dir, wh)) for dx_dir in (1.0, -1.0)
                ] + [
                    ((0.5, 1.0), (dx_dir, -wh)) for dx_dir in (1.0, -1.0)
                ]
            else:
                rays = [
                    ((0.0, 0.5), (1.0, dy_dir * wh)) for dy_dir in (1.0, -1.0)
                ] + [
                    ((1.0, 0.5), (-1.0, dy_dir * wh)) for dy_dir in (1.0, -1.0)
                ]
            for start, (ddx, ddy) in rays:
                seg = _frame_segment(
                    start,
                    (start[0] + ddx * _EXT, start[1] + ddy * _EXT),
                )
                if seg is not None:
                    geometry.append(seg)

        # Panels variant: the two full-span midlines.
        if variant == "panels":
            geometry.append(Segment((0.5, 0.0), (0.5, 1.0)))
            geometry.append(Segment((0.0, 0.5), (1.0, 0.5)))

        return tuple(_dedup(geometry))


register(DiagonalMethod)
