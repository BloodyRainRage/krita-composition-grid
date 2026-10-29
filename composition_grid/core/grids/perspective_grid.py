"""Perspective grid type: a one-vanishing-point ray fan.

Pure-math core: no Qt, no krita — only stdlib.

Pinned semantics (exactly ONE vanishing point in
v1 — it is driven by its own element marker handle in the edit session,
and no ``vanishing_points`` multi-VP parameter exists):
- Rays leave the VP at DOC-space angles ``alpha = k * ray_step_deg`` over
  a HALF turn only: ``k = 0 .. ceil(180/step) - 1``, so alpha = 0 (the doc
  +x direction, i.e. horizon-right) always carries a ray.  Each ray is
  drawn through the VP in both directions, so a full turn would emit every
  line twice.  A doc-space direction ``(cos alpha, sin alpha)`` maps to the
  normalized delta ``(cos alpha * L_doc / w, sin alpha * L_doc / h)`` with
  ``L_doc = 2.5 * max(w, h)``: the degree step stays meaningful in
  document pixels for non-square elements.
- Each ray is the segment ``VP - delta .. VP + delta`` (through the VP in
  BOTH directions), clipped to the EXTENDED rect
  ``Bounds(-0.5, -0.5, 2.0, 2.0)``.  Rays may legitimately leave the
  [0,1] element (this grid escapes bounds) but never that extended rect.
- A Dot marks the VP; ``show_horizon`` adds the horizontal segment at
  ``y = vp_y`` spanning the extended rect's x range (-0.5 .. 1.5).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from composition_grid.core.geometry import Bounds, Point
from composition_grid.core.geometry_primitives import (
    Dot,
    GridGeometry,
    Segment,
    clip_segment_to_bounds,
)
from composition_grid.core.grids.base import GridType, Param, resolve_bool, resolve_number
from composition_grid.core.grids.grid_registry import register

# Extended clip rect: perspective rays may leave the element but stay in here.
_EXT_RECT = Bounds(x=-0.5, y=-0.5, w=2.0, h=2.0)

# Ray reach in doc pixels: 2.5 * max(w, h) always overshoots the extended
# rect for any doc-space angle once scaled back to normalized units.
_EXT_DOC = 2.5

# Cardinal-angle snap: cos(pi/2 as float) ~ 6.1e-17 is snapped to exactly
# 0.0 so the axis-aligned rays are exactly horizontal/vertical.
_SNAP = 1e-12


class PerspectiveGrid(GridType):
    """One-vanishing-point ray fan with an optional horizon line."""

    id = "perspective_grid"
    name = "Perspective"
    escapes_bounds = True
    params = (
        Param(name="vp_x", kind="number", default=0.5, minimum=0.0, maximum=1.0),
        Param(name="vp_y", kind="number", default=0.5, minimum=0.0, maximum=1.0),
        Param(
            name="ray_step_deg",
            kind="number",
            default=10,
            minimum=1.0,
            maximum=30.0,
        ),
        Param(name="show_horizon", kind="bool", default=True),
    )

    def build(self, bounds: Bounds, params: Mapping[str, Any]) -> GridGeometry:
        vp_x = resolve_number(self, params, "vp_x")
        vp_y = resolve_number(self, params, "vp_y")
        step = resolve_number(self, params, "ray_step_deg")
        show_horizon = resolve_bool(self, params, "show_horizon")

        w, h = bounds.w, bounds.h
        if w <= 0.0 or h <= 0.0:
            return ()

        vp: Point = (vp_x, vp_y)
        count = math.ceil(180.0 / step)
        l_doc = _EXT_DOC * max(w, h)

        geometry: list[Segment | Dot] = []
        for k in range(count):
            rad = math.radians(k * step)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            if abs(cos_a) < _SNAP:
                cos_a = 0.0
            if abs(sin_a) < _SNAP:
                sin_a = 0.0
            # Doc-space delta (cos_a, sin_a) * L_doc, re-normalized per axis;
            # the ray passes through the VP in both directions.
            dx_n = cos_a * l_doc / w
            dy_n = sin_a * l_doc / h
            clipped = clip_segment_to_bounds(
                Segment((vp_x - dx_n, vp_y - dy_n), (vp_x + dx_n, vp_y + dy_n)),
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
