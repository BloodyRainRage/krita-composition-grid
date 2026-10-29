"""Transformable grid-element state and pure dragging operations.

Pure math core: no Qt, no krita — only stdlib.

Element-local coordinates are normalized 0..1 on each axis. ``element_matrix``
maps them into document coordinates; the drag operations take deltas in
document space (the on-canvas controller converts handle movement), project
them onto the element's local axes (``R(-rotation)``), and anchor the rendered
opposite corner/face so it keeps its doc position for any rotation.

Coordinate convention: document space with y down (screen-style).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .geometry import Affine, Point

_MIN_EXTENT = 1e-6


@dataclass(frozen=True)
class TransformState:
    """State of the single transformable grid element."""

    cx: float
    cy: float
    width: float
    height: float
    rotation_deg: float = 0.0
    ratio_locked: bool = True


def element_matrix(ts: TransformState) -> Affine:
    """Map element-local (0..1 per axis) coordinates to document coordinates.

    Scale the local unit square onto the element rectangle, translate the local
    center (0.5, 0.5) onto ``(cx, cy)``, then rotate by ``rotation_deg`` about
    the element center.
    """
    scale = Affine.scaling(ts.width, ts.height)
    place = Affine.translation(ts.cx - ts.width / 2.0, ts.cy - ts.height / 2.0)
    about_center = (
        Affine.translation(-ts.cx, -ts.cy)
        .composed(Affine.rotation_deg(ts.rotation_deg))
        .composed(Affine.translation(ts.cx, ts.cy))
    )
    return scale.composed(place).composed(about_center)


def _local_delta(ts: TransformState, dx: float, dy: float) -> Point:
    """Project a doc-space drag delta onto the element's local axes (R(-theta))."""
    return Affine.rotation_deg(-ts.rotation_deg).apply((dx, dy))


def def_element(ts: TransformState | None, doc_w, doc_h) -> TransformState:
    """Default element geometry: doc-centered square of side ``0.8 * min``.

    ``ts`` (a previous state or ``None``) is accepted for signature
    compatibility and ignored. (No default value: ``doc_w``/``doc_h`` must stay
    positional-required, so ``ts`` is an annotated required parameter.)
    """
    del ts
    side = 0.8 * min(doc_w, doc_h)
    return TransformState(
        cx=doc_w / 2.0,
        cy=doc_h / 2.0,
        width=side,
        height=side,
        rotation_deg=0.0,
        ratio_locked=True,
    )


def move(ts: TransformState, dx: float, dy: float) -> TransformState:
    """Shift the element center; size and rotation are unchanged."""
    return replace(ts, cx=ts.cx + float(dx), cy=ts.cy + float(dy))


# Dragged corner -> position signs of the OPPOSITE corner relative to the
# center, e.g. for "nw" the fixed (opposite) corner is "se" at (+1, +1).
_OPPOSITE_SIGNS: dict[str, Point] = {
    "nw": (1.0, 1.0),
    "ne": (-1.0, 1.0),
    "sw": (1.0, -1.0),
    "se": (-1.0, -1.0),
}


def _free_corner_extents(
    ts: TransformState, corner: str, dx_local: float, dy_local: float
) -> tuple[float, float]:
    """Unconstrained (width, height) with the dragged corner moved by the LOCAL delta.

    The dragged edge moves by the delta on its axis; the opposite edge stays,
    so a negative delta on a left/top edge grows the size and a positive delta
    on a right/bottom edge grows it.
    """
    dragged_left = corner in ("nw", "sw")
    dragged_top = corner in ("nw", "ne")
    w_free = ts.width - dx_local if dragged_left else ts.width + dx_local
    h_free = ts.height - dy_local if dragged_top else ts.height + dy_local
    return w_free, h_free


def apply_corner_delta(
    ts: TransformState,
    corner: str,
    dx: float,
    dy: float,
    ratio_override: bool | None,
) -> TransformState:
    """Drag a corner handle by a doc-space delta (dx, dy).

    Fixity is about the RENDERED opposite corner: for any rotation its
    doc position under ``element_matrix`` stays exactly unchanged, while the
    dragged corner's rendered doc position follows the delta exactly (free
    mode). The delta is first projected onto the element's local axes
    (``R(-rotation)``), extents and the ratio-lock rule are decided in local
    space, and the center is re-anchored through the rotation matrix so the
    rendered opposite corner keeps its doc position.

    ``ratio_override`` (if not None) replaces ``ts.ratio_locked``; when locked,
    both dimensions scale by the factor driven by the larger local delta
    projection, preserving the pre-drag width/height ratio. Rotation is
    preserved untouched by resize.
    """
    if corner not in _OPPOSITE_SIGNS:
        raise ValueError(f"unknown corner: {corner!r}")

    dx_local, dy_local = _local_delta(ts, dx, dy)
    locked = ts.ratio_locked if ratio_override is None else bool(ratio_override)
    w_free, h_free = _free_corner_extents(ts, corner, dx_local, dy_local)
    use_ratio = locked and (ts.width > 0.0) and (ts.height > 0.0)

    if use_ratio:
        # Proportion preserved, size driven by the larger local delta projection.
        grow_x = w_free - ts.width
        grow_y = h_free - ts.height
        if abs(dx_local) >= abs(dy_local):
            factor = 1.0 + grow_x / ts.width
        else:
            factor = 1.0 + grow_y / ts.height
        factor = max(factor, _MIN_EXTENT)
        new_w = max(ts.width * factor, _MIN_EXTENT)
        new_h = max(ts.height * factor, _MIN_EXTENT)
    else:
        new_w = max(w_free, _MIN_EXTENT)
        new_h = max(h_free, _MIN_EXTENT)

    # Rotation-aware anchoring: the rendered opposite corner is at
    # center + R(theta) . (ox * w/2, oy * h/2); keep that doc position fixed:
    # center' = C_opposite_old - R(theta) . (ox * w'/2, oy * h'/2).
    rot = Affine.rotation_deg(ts.rotation_deg)
    ox, oy = _OPPOSITE_SIGNS[corner]
    old_off = rot.apply((ox * ts.width / 2.0, oy * ts.height / 2.0))
    new_off = rot.apply((ox * new_w / 2.0, oy * new_h / 2.0))
    return replace(
        ts,
        cx=ts.cx + old_off[0] - new_off[0],
        cy=ts.cy + old_off[1] - new_off[1],
        width=new_w,
        height=new_h,
    )


def apply_edge_delta(ts: TransformState, edge: str, dx: float, dy: float) -> TransformState:
    """Drag an edge handle by a doc-space delta (dx, dy).

    Fixity is about the RENDERED opposite face: for any rotation the opposite
    face midpoint keeps its doc position under ``element_matrix``, while the
    dragged face's rendered midpoint moves along the face normal by the local
    (rotation-projected) delta component. Only one dimension changes; the
    perpendicular delta component is ignored (the handle slides along its
    line, the extent does not change). Ratio locking is not applied to edge
    drags. Crossed drags (past the opposite face) clamp to a tiny positive
    extent instead of mirroring.
    """
    dx_local, dy_local = _local_delta(ts, dx, dy)
    rot = Affine.rotation_deg(ts.rotation_deg)
    if edge == "n":
        # height = height - dy_local, so a negative (up) local delta grows; the
        # center shifts by half the CLAMPED height change, keeping the south face fixed.
        new_h = max(ts.height - dy_local, _MIN_EXTENT)
        shift = rot.apply((0.0, (ts.height - new_h) / 2.0))
        return replace(ts, cx=ts.cx + shift[0], cy=ts.cy + shift[1], height=new_h)
    if edge == "s":
        new_h = max(ts.height + dy_local, _MIN_EXTENT)
        shift = rot.apply((0.0, (new_h - ts.height) / 2.0))
        return replace(ts, cx=ts.cx + shift[0], cy=ts.cy + shift[1], height=new_h)
    if edge == "w":
        new_w = max(ts.width - dx_local, _MIN_EXTENT)
        shift = rot.apply(((ts.width - new_w) / 2.0, 0.0))
        return replace(ts, cx=ts.cx + shift[0], cy=ts.cy + shift[1], width=new_w)
    if edge == "e":
        new_w = max(ts.width + dx_local, _MIN_EXTENT)
        shift = rot.apply(((new_w - ts.width) / 2.0, 0.0))
        return replace(ts, cx=ts.cx + shift[0], cy=ts.cy + shift[1], width=new_w)
    raise ValueError(f"unknown edge: {edge!r}")


def normalize_angle_degrees(deg: float) -> float:
    """Wrap a degree measure into the (-180, 180] range used by ``rotation_deg``.

    The single source of truth for the rotation wrap: :func:`rotate` adds a
    delta then calls it, and :class:`GridStateService.set_rotation` normalizes
    an absolute angle with it — so the docker's rotation field and the canvas
    drag handle can never drift apart. ``-180`` maps to ``+180`` (the range is
    open below, closed above).
    """
    wrapped = (float(deg) + 180.0) % 360.0 - 180.0
    if wrapped <= -180.0:  # (-180, 180]: -180 maps to +180
        wrapped = 180.0
    return wrapped


def rotate(ts: TransformState, delta_deg: float) -> TransformState:
    """Add ``delta_deg`` to the rotation, wrapped into (-180, 180]."""
    return replace(ts, rotation_deg=normalize_angle_degrees(ts.rotation_deg + float(delta_deg)))
