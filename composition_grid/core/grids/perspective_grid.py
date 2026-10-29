"""Perspective grid type: a one-vanishing-point ray fan.

Pure-math core: no Qt, no krita — only stdlib.

Pinned semantics (exactly ONE vanishing point in
v1 — it is driven by its own element marker handle in the edit session,
and no ``vanishing_points`` multi-VP parameter exists):
- Rays are aimed at the frame, not at angles.  Each of the four edges of
  the element's normalized [0,1] square is split into ``divisions`` equal
  segments; the division points (corners included, so shared corners are
  deduplicated) are the ray targets.  One-point perspective calls for this
  construction: receding scene parallels that are equally spaced cross any
  image line parallel to the picture plane — the frame edges among them —
  at EQUAL intervals, so dividing the frame evenly and drawing one ray per
  division point reproduces that spacing, whereas an equal-angle fan does
  not (moving the VP would merely translate a rigid fan).
- Each ray is the HALF-line that starts at the VP, passes through its
  target and is extended until it reaches the EXTENDED clip rect
  ``Bounds(-0.5, -0.5, 2.0, 2.0)``; the clip helper is kept as-is.  The
  frame targets already surround the VP, so the half-lines tile every
  direction once: opposite targets butt together into one full line
  without ever drawing it twice.  Rays may legitimately leave the [0,1]
  element (this grid escapes bounds) but never that extended rect.  A
  target that coincides with the VP (within 1e-9) defines no direction and
  is skipped, as is a target sharing the direction of a ray already
  emitted (a VP sitting on an edge sees several of that edge's division
  points in one direction).
- Construction happens in NORMALIZED space throughout: equal spacing along
  an edge is preserved by any affine map, so no doc-space aspect
  (width/height) correction is needed — the doc transform applied later
  takes care of it.
- A Dot marks the VP; ``show_horizon`` adds the horizontal segment at
  ``y = vp_y`` spanning the extended rect's x range (-0.5 .. 1.5).
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds, Point
from composition_grid.core.geometry_primitives import (
    Dot,
    GridGeometry,
    Segment,
    clip_segment_to_bounds,
)
from composition_grid.core.grids.base import (
    GridType,
    Param,
    resolve_bool,
    resolve_int,
    resolve_number,
)
from composition_grid.core.grids.grid_registry import register

# Extended clip rect: perspective rays may leave the element but stay in here.
_EXT_RECT = Bounds(x=-0.5, y=-0.5, w=2.0, h=2.0)

# Ray reach in normalized units: every VP and every frame edge lies in
# [0,1], so a VP-relative delta of 2.0 always overshoots the extended rect.
_EXT_NORM = 2.0

# A target this close to the VP defines no ray direction.
_VP_TOL = 1e-9

# Duplicate-ray threshold, measured on the unit direction vector.
_DIR_TOL = 1e-9


def _edge_divisions(divisions: int) -> "tuple[Point, ...]":
    """Division points of the [0,1] square's four edges, corners once each."""
    step = 1.0 / divisions
    targets: list[Point] = []
    seen: set[Point] = set()
    for i in range(divisions + 1):
        for point in (
            (i * step, 0.0),      # top edge, left to right
            (i * step, 1.0),      # bottom edge
            (0.0, i * step),      # left edge, top to bottom
            (1.0, i * step),      # right edge
        ):
            if point not in seen:
                seen.add(point)
                targets.append(point)
    return tuple(targets)


class PerspectiveGrid(GridType):
    """One-vanishing-point ray fan with an optional horizon line."""

    id = "perspective_grid"
    name = "Perspective"
    escapes_bounds = True
    params = (
        Param(name="vp_x", kind="number", default=0.5, minimum=0.0, maximum=1.0),
        Param(name="vp_y", kind="number", default=0.5, minimum=0.0, maximum=1.0),
        Param(name="divisions", kind="number", default=8, minimum=2, maximum=32),
        Param(name="show_horizon", kind="bool", default=True),
    )

    def build(self, bounds: Bounds, params: Mapping[str, Any]) -> GridGeometry:
        vp_x = resolve_number(self, params, "vp_x")
        vp_y = resolve_number(self, params, "vp_y")
        divisions = resolve_int(self, params, "divisions")
        show_horizon = resolve_bool(self, params, "show_horizon")

        if bounds.w <= 0.0 or bounds.h <= 0.0:
            return ()

        vp: Point = (vp_x, vp_y)
        geometry: list[Segment | Dot] = []
        directions: list[Point] = []

        for target in _edge_divisions(divisions):
            dx = target[0] - vp_x
            dy = target[1] - vp_y
            length = (dx * dx + dy * dy) ** 0.5
            if length <= _VP_TOL:
                continue
            ux, uy = dx / length, dy / length
            if any(
                abs(ux - vx) <= _DIR_TOL and abs(uy - vy) <= _DIR_TOL
                for vx, vy in directions
            ):
                continue
            directions.append((ux, uy))
            clipped = clip_segment_to_bounds(
                Segment(vp, (vp_x + ux * _EXT_NORM, vp_y + uy * _EXT_NORM)),
                _EXT_RECT,
            )
            if clipped is not None:
                geometry.append(clipped)

        geometry.append(Dot(vp))

        if show_horizon:
            geometry.append(
                Segment(
                    (_EXT_RECT.x, vp_y),
                    (_EXT_RECT.x + _EXT_RECT.w, vp_y),
                )
            )

        return tuple(geometry)


register(PerspectiveGrid)
