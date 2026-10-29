"""Golden Spiral grid type: quarter-arc spiral over a phi-subdivision.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.

Pinned construction (frame-fitted "stretched" golden spiral, depth 6)
--------------------------------------------------------------------
The spiral is inscribed in the WHOLE frame ``[0, 1] x [0, 1]``: the start
rect is the unit frame itself, so no band of the element is left empty.
Each step cuts the fraction ``PHI = (sqrt(5) - 1) / 2`` of the current rect
along the current cut axis, taken from the side named by the cut-side cycle,
and keeps the complementary ``1 - PHI`` strip as the remainder.  The cut
axis alternates every step (W/E cut -> N/S cut -> W/E ...), so the cells are
golden rectangles rather than squares: cell ``k`` measures ``PHI^k`` by
``PHI^(k-1)``.

The FIRST cut axis follows the element aspect: a landscape or square element
(``bounds.w >= bounds.h``) starts with a W/E cut, whose cut line is the
vertical phi line (``x = PHI`` from the west, ``x = 1 - PHI`` from the east);
a portrait element starts with the matching N/S cut.

===============  =====================  =====================
``origin``       landscape cut cycle    portrait cut cycle
===============  =====================  =====================
``tl``           W, S, E, N             N, E, S, W
``tr``           E, S, W, N             N, W, S, E
``bl``           W, N, E, S             S, E, N, W
``br``           E, N, W, S             S, W, N, E
===============  =====================  =====================

The portrait cycle is the landscape one read backwards, entered at the N/S
side adjacent to the origin corner: the first cell then still contains that
corner and the whirl direction per origin is preserved.  Consecutive cells
share exactly one corner — the pivot where the arcs connect.

Each arc is the quarter ellipse inscribed in its cell: ``rx = cell width``,
``ry = cell height`` (normalized), centered on the cell corner adjacent to
both connection corners, ``a0``/``a1`` multiples of 90 degrees exactly 90
degrees apart.  ``tl``/``br`` sweep decreasing screen angles (-90,
clockwise), ``tr``/``bl`` increasing (+90).  The two candidate corners are
told apart by the SIGN of the 2D cross product of the incoming and outgoing
radii — the square-cell trick of rotating one radius by 90 degrees into the
other would fail now that the radii have different lengths.  The first arc
starts at the origin's frame corner and consecutive arcs connect end to end
(C0 continuity).

Subdivision lines (``show_subdivision``): for every cell the two arc radii
(center -> back corner, center -> forward corner); these lie on the cell
edges and describe the rectangular phi-subdivision.

Stop: after 6 cuts max (pinned depth 6), or earlier when the remaining
rect's short side falls below 0.001.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import (
    Arc,
    GridGeometry,
    Point,
    Segment,
)
from composition_grid.core.grids.base import GridType, Param, resolve_bool, resolve_choice
from composition_grid.core.grids.grid_registry import register

PHI = (math.sqrt(5) - 1) / 2  # == 0.6180339887498949 (pinned)
MAX_CUTS = 6
MIN_SIDE = 0.001

# Cut side cycles: the landscape one starts on the side the phi line of the
# matching grid crosses, the portrait one is its reverse entered at the N/S
# side next to the origin corner.
_LANDSCAPE_CYCLES = {
    "tl": ("W", "S", "E", "N"),
    "tr": ("E", "S", "W", "N"),
    "bl": ("W", "N", "E", "S"),
    "br": ("E", "N", "W", "S"),
}
_PORTRAIT_CYCLES = {
    "tl": ("N", "E", "S", "W"),
    "tr": ("N", "W", "S", "E"),
    "bl": ("S", "E", "N", "W"),
    "br": ("S", "W", "N", "E"),
}


def _cut_side_cycle(origin: str, portrait: bool) -> tuple[str, ...]:
    """Cut side cycle per origin (screen coords, y down) and element aspect."""
    cycles = _PORTRAIT_CYCLES if portrait else _LANDSCAPE_CYCLES
    return cycles[origin]


def _sweep_sign(origin: str) -> float:
    """Angle delta per arc: -90 (screen-clockwise) for tl/br, +90 for tr/bl."""
    return -90.0 if origin in ("tl", "br") else 90.0


def _cut_cell(rect: list[float], side: str) -> tuple[list[float], list[float]]:
    """Cut the PHI-fraction cell touching ``side`` off ``rect``.

    Returns ``(cell, remainder)`` as [x0, y0, x1, y1]: W/E cuts split the
    width along the vertical phi line, N/S cuts the height along the
    horizontal one.  The pinned cycle alternates the two axes, which keeps
    consecutive cells touching in exactly one corner.
    """
    x0, y0, x1, y1 = rect
    if side in ("W", "E"):
        cut = PHI * (x1 - x0)
        cx0 = x0 if side == "W" else x1 - cut
        cell = [cx0, y0, cx0 + cut, y1]
        remainder = [cx0 + cut, y0, x1, y1] if side == "W" else [x0, y0, cx0, y1]
    else:  # "N" / "S"
        cut = PHI * (y1 - y0)
        cy0 = y0 if side == "N" else y1 - cut
        cell = [x0, cy0, x1, cy0 + cut]
        remainder = [x0, cy0 + cut, x1, y1] if side == "N" else [x0, y0, x1, cy0]
    return cell, remainder


_CORNER_TOL = 1e-9


def _corners(rect: list[float]) -> set[tuple[float, float]]:
    x0, y0, x1, y1 = rect
    return {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}


def _shared_corner(sq_a: list[float], sq_b: list[float]) -> tuple[float, float]:
    """The unique corner shared by two consecutive phi cells (tolerant match)."""
    shared = [
        ca
        for ca in _corners(sq_a)
        for cb in _corners(sq_b)
        if abs(ca[0] - cb[0]) < _CORNER_TOL and abs(ca[1] - cb[1]) < _CORNER_TOL
    ]
    if len(shared) != 1:
        raise ValueError(f"expected exactly one shared corner, got {shared!r}")
    return shared[0]


def _outer_corner(origin: str) -> tuple[float, float]:
    """First cell's free frame corner (where the spiral starts)."""
    corners = {
        "tl": (0.0, 0.0),
        "tr": (1.0, 0.0),
        "bl": (0.0, 1.0),
        "br": (1.0, 1.0),
    }
    return corners[origin]


def _pick_center(
    cell: list[float],
    corner_in: tuple[float, float],
    corner_out: tuple[float, float],
    sweep: float,
) -> tuple[float, float]:
    """Arc center: the cell corner the arc turns around from in to out.

    ``corner_in`` and ``corner_out`` are diagonal corners of ``cell``; the
    two remaining corners are the candidates.  Their radii have different
    lengths (``rx != ry`` in a stretched spiral), so the rotation direction
    is read off the sign of the 2D cross product of the two radius vectors:
    positive means the screen angle increases over the sweep.
    """
    x0, y0, x1, y1 = cell
    candidates = {(x0, y0), (x1, y0), (x1, y1), (x0, y1)} - {corner_in, corner_out}
    want = 1.0 if sweep > 0 else -1.0
    for cand in sorted(candidates):
        din = (corner_in[0] - cand[0], corner_in[1] - cand[1])
        dout = (corner_out[0] - cand[0], corner_out[1] - cand[1])
        cross = din[0] * dout[1] - din[1] * dout[0]
        if cross * want > 0.0:
            return cand
    raise ValueError(
        f"no candidate arc center in {cell!r} "
        f"for {corner_in!r} -> {corner_out!r} sweep {sweep!r}"
    )


def _angle_of(p: Point, center: Point) -> float:
    return math.degrees(math.atan2(p[1] - center[1], p[0] - center[0]))


class GoldenSpiral(GridType):
    """Golden spiral: 6 quarter arcs over a deterministic phi-subdivision."""

    id = "golden_spiral"
    name = "Golden Spiral"
    params = (
        Param(
            name="origin",
            kind="choice",
            default="tl",
            choices=("tl", "tr", "br", "bl"),
        ),
        Param(name="show_subdivision", kind="bool", default=True),
    )

    def build(self, bounds: Bounds, params: Mapping[str, Any]) -> GridGeometry:
        origin = resolve_choice(self, params, "origin")
        show_subdivision = resolve_bool(self, params, "show_subdivision")

        # The element aspect picks the FIRST cut axis; the frame is the start
        # rect, so the spiral covers the whole element.
        portrait = bounds.h > bounds.w
        cycle = _cut_side_cycle(origin, portrait)
        sweep = _sweep_sign(origin)

        # Cut the (at most 6) golden cells of the frame.
        rect = [0.0, 0.0, 1.0, 1.0]
        cells: list[list[float]] = []
        for index in range(MAX_CUTS):
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if min(w, h) < MIN_SIDE:
                break
            cell, rect = _cut_cell(rect, cycle[index % 4])
            cells.append(cell)

        if not cells:
            return ()

        # Connection corners: where consecutive cells touch; the first
        # cell starts at the origin's frame corner, the last cell ends at
        # the pivot toward the finally remaining rect (the spiral's eye).
        prevs = [_outer_corner(origin)] + [
            _shared_corner(cells[i], cells[i + 1]) for i in range(len(cells) - 1)
        ]
        # One extra provisional cut supplies the eye corner.
        nexts = [
            _shared_corner(cells[i], cells[i + 1]) for i in range(len(cells) - 1)
        ]
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if min(w, h) >= MIN_SIDE:
            _tail, _ = _cut_cell(rect, cycle[len(cells) % 4])
            nexts.append(_shared_corner(cells[len(cells) - 1], _tail))
        else:  # pragma: no cover - unreachable at pinned depth 6
            x0, y0, x1, y1 = rect
            nexts.append((next(iter((x0, x1))), next(iter((y0, y1)))))

        segments: list[Segment] = []
        arcs: list[Arc] = []
        for cell, prev, nxt in zip(cells, prevs, nexts):
            # One center per cell, shared by the subdivision radii and the arc.
            center = _pick_center(cell, prev, nxt, sweep)
            if show_subdivision:
                segments.append(Segment(center, prev))
                segments.append(Segment(center, nxt))
            rx = cell[2] - cell[0]
            ry = cell[3] - cell[1]
            a0 = _angle_of(prev, center)
            arcs.append(Arc(center=center, rx=rx, ry=ry, a0=a0, a1=a0 + sweep))

        return tuple(segments) + tuple(arcs)


register(GoldenSpiral)
