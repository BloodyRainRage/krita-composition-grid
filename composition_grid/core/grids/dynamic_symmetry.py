"""Dynamic Symmetry grid type: inscribed root-n rectangle + 4 reciprocals.

Pure-math core: no Qt, no krita — only stdlib.

Pinned semantics:
- A root-n rectangle — short:long DOC ratio ``1:root_n`` (the classic
  Hambidge rectangle) — is inscribed in the element, centered, fitted by
  the smaller side and ORIENTED like the element: a wide or square
  element (``w >= h``) takes a landscape root rect
  (``rect_h = min(h, w/root_n), rect_w = root_n * rect_h``); a tall
  element (``w < h``) takes a portrait root rect (``rect_w = min(w,
  h/root_n), rect_h = root_n * rect_w``).  For ``w >= h`` this is exactly
  the elements-rule ``if w/h >= root_n: w_r = h*root_n else w_r = w,
  h_r = w/root_n``; the tall-element portrait orientation is pinned by
  the root-3 test (300x900 -> h_r = 300*sqrt(3)).
- Emitted geometry is NORMALIZED element-local with an anisotropic
  mapping: the doc-space insets ``px = (w - w_r)/2, py = (h - h_r)/2``
  become the normalized inner rect ``Bounds(px/w, py/h, w_r/w, h_r/h)``.
  The doc-space ratio really is ``1:root_n``; the later element_matrix
  mapping scales normalized (x, y) by (w, h) and thus reproduces it.
- Emitted: the 4 side segments of the inner rect, its 2 corner-to-corner
  diagonals (nw->se, sw->ne), and — when ``show_reciprocal`` — the FOUR
  Hambidge reciprocals, measured in DOC space (perpendicularity is a
  real-space notion; a normalized-space "perpendicular" would differ for
  non-square elements): the perpendicular from ne and the one from sw onto
  the nw->se diagonal, plus the perpendicular from nw and the one from se
  onto the sw->ne diagonal.  Each is emitted as a full reciprocal: it
  starts at its own corner, runs through the foot on the diagonal and is
  produced past the foot until it leaves the inner rect (a long doc-space
  ray clipped against the doc-space inner rect, then normalized back), so
  the 4 reciprocals + 2 diagonals + 4 sides give the 10 lines of the
  classic dynamic-symmetry armature.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from composition_grid.core.geometry import Bounds, Point
from composition_grid.core.geometry_primitives import (
    GridGeometry,
    Segment,
    clip_segment_to_bounds,
)
from composition_grid.core.grids.base import GridType, Param, resolve_bool, resolve_choice
from composition_grid.core.grids.grid_registry import register


def _normalized(point: Point, w: float, h: float) -> Point:
    """Element-local normalized coordinates of an element-space doc point."""
    return (point[0] / w, point[1] / h)


def _foot(origin: Point, direction: Point, point: Point, length_sq: float) -> Point:
    """Doc-space projection of ``point`` onto ``origin + t * direction``."""
    t = (
        (point[0] - origin[0]) * direction[0]
        + (point[1] - origin[1]) * direction[1]
    ) / length_sq
    return (origin[0] + t * direction[0], origin[1] + t * direction[1])


class DynamicSymmetry(GridType):
    """Inscribed Hambidge root-n rectangle with the reciprocal construction."""

    id = "dynamic_symmetry"
    name = "Dynamic Symmetry"
    params = (
        Param(
            name="root",
            kind="choice",
            default="2",
            choices=("2", "3", "5"),
        ),
        Param(name="show_reciprocal", kind="bool", default=True),
    )

    def build(self, bounds: Bounds, params: Mapping[str, Any]) -> GridGeometry:
        root_n = math.sqrt(float(resolve_choice(self, params, "root")))
        show_reciprocal = resolve_bool(self, params, "show_reciprocal")

        w, h = bounds.w, bounds.h
        if w <= 0.0 or h <= 0.0:
            return ()

        # Root-n rectangle (short:long doc ratio 1:root_n), fitted by the
        # smaller element side and centered in the element; its orientation
        # follows the element's (landscape for wide/square frames,
        # portrait for tall frames).
        if w >= h:
            rect_h = min(h, w / root_n)
            rect_w = root_n * rect_h
        else:
            rect_w = min(w, h / root_n)
            rect_h = root_n * rect_w
        px = (w - rect_w) / 2.0
        py = (h - rect_h) / 2.0

        # Normalized (anisotropically inset) inner rect — the emitted space.
        inner = Bounds(x=px / w, y=py / h, w=rect_w / w, h=rect_h / h)
        nw, ne, se, sw = inner.corners()

        geometry = [
            Segment(nw, ne),  # top side
            Segment(sw, se),  # bottom side
            Segment(nw, sw),  # left side
            Segment(ne, se),  # right side
            Segment(nw, se),  # inner diagonal
            Segment(sw, ne),  # inner diagonal
        ]

        if show_reciprocal:
            # The four Hambidge reciprocals, measured in DOC space: each
            # starts at an inner-rect corner, crosses the diagonal it does
            # not lie on at the perpendicular foot, and is produced until it
            # leaves the inner rect.  ne - nw == (rect_w, 0) and friends are
            # the doc-local offsets the projection parameter is taken from.
            inner_doc = Bounds(x=px, y=py, w=rect_w, h=rect_h)
            nw_doc, ne_doc, se_doc, sw_doc = inner_doc.corners()
            length_sq = rect_w**2 + rect_h**2
            diag_se = (rect_w, rect_h)  # nw -> se
            diag_ne = (rect_w, -rect_h)  # sw -> ne
            reach = 2.0 * (rect_w + rect_h)  # overshoots the whole rect
            for corner, origin, direction in (
                (ne_doc, nw_doc, diag_se),
                (sw_doc, nw_doc, diag_se),
                (nw_doc, sw_doc, diag_ne),
                (se_doc, sw_doc, diag_ne),
            ):
                foot_doc = _foot(origin, direction, corner, length_sq)
                ux = foot_doc[0] - corner[0]
                uy = foot_doc[1] - corner[1]
                norm = math.hypot(ux, uy)
                far_doc = (
                    corner[0] + ux / norm * reach,
                    corner[1] + uy / norm * reach,
                )
                clipped = clip_segment_to_bounds(
                    Segment(corner, far_doc), inner_doc
                )
                if clipped is not None:
                    geometry.append(
                        Segment(
                            _normalized(clipped.p1, w, h),
                            _normalized(clipped.p2, w, h),
                        )
                    )

        return tuple(geometry)


register(DynamicSymmetry)
