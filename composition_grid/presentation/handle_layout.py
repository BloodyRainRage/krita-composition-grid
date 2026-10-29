"""Edit-session marker layout and hit-testing for the handles.

Pure arithmetic: no Qt, no krita — only stdlib plus the pure modules
``composition_grid.core`` and ``composition_grid.canvas.doc_view_mapper``.

Conventions (fixed; see the referenced modules):

* Coordinate spaces are screen-style with **y growing down**, in both
  document space and widget pixels — see
  :mod:`composition_grid.core.geometry`.
* The ELEMENT rotation (``TransformState.rotation_deg``) reaches handle
  positions only through ``TransformState.element_matrix`` (rotation about
  the element center, applied in y-down doc space); every handle follows
  that matrix exactly, so a rotated element rotates its handles with it.
* The VIEW transform arrives pre-composed inside ``doc_to_widget_affine`` —
  the host's own doc->widget affine when ``ViewState.host_affine`` carries one,
  otherwise the reconstructed rect/rotation/mirror model (whose
  ``ViewState.rotation_deg`` is the fallback's own CCW-on-screen convention and
  is exact only at 0°, see :mod:`composition_grid.canvas.doc_view_mapper`);
  this module never applies a view transform itself and never reads the
  orientation fields.
* The rotate handle rides the element's local up axis ``24`` widget px
  (``24 / view_scale`` doc px) beyond the top-edge midpoint: under zoom it
  stays 24 widget px off the top edge, and for a rotated element it stays
  perpendicular off the element's own (still local) top face — "above the
  top edge in doc terms".
* V1 has exactly ONE vanishing point: the only vp handle id is
  ``"vp0"``; no ``"vp1"`` id exists and none is ever emitted.
"""

from __future__ import annotations

import math
from typing import Mapping

from composition_grid.canvas.doc_view_mapper import (
    ViewState,
    doc_to_widget_affine,
    view_scale,
)
from composition_grid.core.geometry import Affine, Point
from composition_grid.core.transform_state import TransformState, element_matrix

__all__ = [
    "HandleId",
    "DEFAULT_VP_X",
    "DEFAULT_VP_Y",
    "ROTATE_HANDLE_OFFSET_PX",
    "cursor_shape",
    "effective_tolerance",
    "handle_ids",
    "handle_positions",
    "hit_test",
]

HandleId = str
"""A handle identifier: ``nw/ne/se/sw/n/e/s/w/move/rotate/vp0``."""

ROTATE_HANDLE_OFFSET_PX = 24.0
"""Rotate-handle distance beyond the top-edge midpoint, in widget px."""

# Defaults for the perspective vp handle position, element-normalized (0..1).
# They mirror the ``perspective_grid`` Param defaults ``(vp_x, vp_y) = (0.5,
# 0.5)``; the drift-guard test pins that equality (imported at test time only,
# since the pure layer must not import grid types at runtime).
DEFAULT_VP_X = 0.5
DEFAULT_VP_Y = 0.5

_VP_ID: HandleId = "vp0"

_CORNERS: tuple[HandleId, ...] = ("nw", "ne", "se", "sw")
_EDGES: tuple[HandleId, ...] = ("n", "e", "s", "w")

# Element-local landmark coordinates (0..1 per axis; the top edge is y = 0).
# "move" anchors on the element center; hit_test also treats the whole element
# quad as the move drag target (see the interior check in ``hit_test``).
_ELEMENT_LOCAL: dict[str, Point] = {
    "nw": (0.0, 0.0),
    "ne": (1.0, 0.0),
    "se": (1.0, 1.0),
    "sw": (0.0, 1.0),
    "n": (0.5, 0.0),
    "e": (1.0, 0.5),
    "s": (0.5, 1.0),
    "w": (0.0, 0.5),
    "move": (0.5, 0.5),
}

# Hit-testing priority (positions are widget px): rotate, then corners,
# then the vanishing point, then edges, then move.
_HIT_PRIORITY: tuple[HandleId, ...] = (
    ("rotate",) + _CORNERS + (_VP_ID,) + _EDGES + ("move",)
)

# Qt cursor NAME per handle (no Qt here: the handle painter maps these
# names onto ``composition_grid.qt_compat`` cursors).
_CURSOR_BY_HANDLE: dict[str, str] = {
    "nw": "size_fdiag",
    "se": "size_fdiag",
    "ne": "size_bdiag",
    "sw": "size_bdiag",
    "n": "size_ver",
    "s": "size_ver",
    "e": "size_hor",
    "w": "size_hor",
    "vp0": "pointing_hand",
    "rotate": "cross",
    "move": "size_all",
}


def handle_ids(include_vp: int = 0) -> tuple[HandleId, ...]:
    """Marker ids in layout order: corners, edges, move, rotate, then ``vp0``.

    ``vp0`` is appended when ``include_vp >= 1``. V1 has exactly one
    vanishing point, so no other vp id is ever produced.
    """
    ids: tuple[HandleId, ...] = _CORNERS + _EDGES + ("move", "rotate")
    if include_vp >= 1:
        ids += (_VP_ID,)
    return ids


def handle_positions(
    state: ViewState,
    transform: TransformState,
    vp_count: int = 0,
    vp_x: float = DEFAULT_VP_X,
    vp_y: float = DEFAULT_VP_Y,
) -> dict[HandleId, Point]:
    """Marker anchor positions in widget pixels for one element.

    The corner/edge/``move`` handles sit on the element's local landmarks
    (normalized 0..1 per axis, top edge at local ``y = 0``), mapped by
    ``TransformState.element_matrix`` into doc space (the element rotation
    is already inside that matrix) and then by ``doc_to_widget_affine(state)``
    into widget pixels.

    The rotate handle is the top-edge midpoint pushed a further
    ``ROTATE_HANDLE_OFFSET_PX / view_scale(state)`` doc pixels along the
    element's local up axis — i.e. the same point ``element_matrix`` yields
    for the local coordinates ``(0.5, -offset / height)``, equivalently the
    rendered top-edge midpoint shifted by the rendered ``(0, -1)`` local
    direction — so it stays 24 widget px beyond the top edge under any zoom
    and orbits with the element under rotation.

    ``vp0`` (emitted only when ``vp_count >= 1``) is the perspective
    vanishing point: ``vp_x`` / ``vp_y`` are element-normalized coordinates
    (0..1), exactly the ``perspective_grid`` params whose defaults are
    ``(0.5, 0.5)``, mapped through the same two matrices. V1 has exactly one
    vanishing point, so no other vp handle is emitted for larger counts.
    """
    matrix = element_matrix(transform)
    view = doc_to_widget_affine(state)
    to_widget = matrix.composed(view)

    positions: dict[HandleId, Point] = {
        handle: to_widget.apply(local) for handle, local in _ELEMENT_LOCAL.items()
    }

    offset_doc = ROTATE_HANDLE_OFFSET_PX / view_scale(state)
    up: Point = Affine.rotation_deg(transform.rotation_deg).apply((0.0, -1.0))
    top_mid_doc = matrix.apply((0.5, 0.0))
    positions["rotate"] = view.apply(
        (top_mid_doc[0] + up[0] * offset_doc, top_mid_doc[1] + up[1] * offset_doc)
    )

    if vp_count >= 1:
        positions[_VP_ID] = to_widget.apply((vp_x, vp_y))
    return positions


def effective_tolerance(positions: Mapping[HandleId, Point], tol_px: float) -> float:
    """Effective widget-px grab radius, clamped by the positions frame.

    ``min(tol_px, frame_diagonal / 6)`` where the diagonal is taken over the
    bounding box of ALL given handle positions (with a tiny
    element, a raw corner radius must not eat the rotate handle's priority
    space). An empty ``positions`` mapping clamps the radius to 0.
    """
    if not positions:
        return 0.0
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    return min(tol_px, diagonal / 6.0)


def _is_inside_quad(
    probe: Point, nw: Point, ne: Point, se: Point, sw: Point
) -> bool:
    """Winding-independent point-in-convex-quad test (boundary counts).

    The element quad ``nw -> ne -> se -> sw`` is convex, so the probe lies
    inside (or on an edge) exactly when every cross product
    ``edge_i x (probe - vertex_i)`` shares one sign. A mirrored view reverses
    that sign without changing the test, so no winding assumption is made:
    ``all >= 0`` or ``all <= 0`` both read as "inside".
    """
    quad = (nw, ne, se, sw)
    positive = False
    negative = False
    for index in range(4):
        ax, ay = quad[index]
        bx, by = quad[(index + 1) % 4]
        cross = (bx - ax) * (probe[1] - ay) - (by - ay) * (probe[0] - ax)
        if cross > 0.0:
            positive = True
        elif cross < 0.0:
            negative = True
        if positive and negative:
            return False
    return True


def hit_test(
    positions: Mapping[HandleId, Point],
    probe: Point,
    tol_px: float,
    view_scale: float,
) -> HandleId | None:
    """First handle by priority within tolerance of probe, else the interior.

    Priority order for the point handles: ``rotate`` → corners
    ``nw, ne, se, sw`` → ``vp0`` → edges ``n, e, s, w`` → ``move`` centre. If
    no point handle is within reach and all four corners are present, a probe
    lying INSIDE the element quad (widget coords, may be rotated and/or
    mirrored — see :func:`_is_inside_quad`) still resolves to ``"move"``: the
    whole element is its drag target. A probe outside the quad → ``None``.
    ``tol_px`` is the raw widget-px grab radius, clamped by
    :func:`effective_tolerance` (positions-frame diagonal / 6).
    ``view_scale`` (widget px per doc px) is accepted for the edit-controller
    call-signature compatibility; the clamp itself works in widget pixels on
    the given positions frame and does not need it.
    """
    del view_scale
    tolerance = effective_tolerance(positions, tol_px)
    for handle in _HIT_PRIORITY:
        pos = positions.get(handle)
        if pos is not None and math.hypot(probe[0] - pos[0], probe[1] - pos[1]) <= tolerance:
            return handle
    nw = positions.get("nw")
    ne = positions.get("ne")
    se = positions.get("se")
    sw = positions.get("sw")
    if nw is not None and ne is not None and se is not None and sw is not None:
        if _is_inside_quad(probe, nw, ne, se, sw):
            return "move"
    return None


def cursor_shape(handle: HandleId) -> str:
    """Qt cursor NAME for a handle (no Qt here; the handle painter maps
    these names onto ``qt_compat`` cursors). Unknown ids → ``"arrow"``."""
    return _CURSOR_BY_HANDLE.get(handle, "arrow")
