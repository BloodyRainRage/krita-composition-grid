"""Qt handle painter: edit-session markers over the canvas overlay.

Qt-dependent module — ALL Qt naming goes through
:mod:`composition_grid.qt_compat` (the single legal PyQt import route), and
painting raises ``RuntimeError`` when PyQt is absent (no import-time crash).

Marker design (validated by
``tests/presentation/test_overlay_painter_qt.py``):

* ``paint_handles`` works in **widget pixels**: the positions dict comes from
  ``handle_layout.handle_positions`` (already widget px), so the painter
  transform is reset to identity before painting (state restored afterwards).
* ``edit_session`` False or an empty ``positions`` dict paints NOTHING —
  outside an edit session the canvas must not show any markers.
* Every handle is drawn with a **white fill** ``(255, 255, 255)`` and a
  **black 1 px border**; the hovered handle switches to a mid-gray accent
  fill ``(128, 128, 128)`` with a thicker (2 px) border. ``hover=None``
  keeps every marker in the default look.
* Shapes:
  - corners (``nw/ne/se/sw``) and edges (``n/e/s/w``): filled square,
    ``HANDLE_SIZE_PX`` x ``HANDLE_SIZE_PX`` px centered on the anchor;
  - ``rotate``: filled circle of radius ``ROTATE_RADIUS_PX`` plus an upward
    radial bar (``ROTATE_BAR_PX`` px long) starting at the circle top — the
    icon-like "rotate around the element" cue;
  - ``vp0`` (the single V1 vanishing point): filled diamond with an
    ``VP_DIAMOND_PX`` bounding square for a distinct feel;
  - ``move``: a small 4-arrow cross (``MOVE_MARKER_HALF_PX`` arms with
    ``MOVE_ARROW_PX`` heads) centered on the anchor — the visible cue that
    you can grab the whole element and drag it anywhere (the edit-controller
    resolves an interior press to ``"move"`` as well, see
    ``handle_layout.hit_test``).
* Unknown handle ids render as squares (documented simple fallback).
"""

from __future__ import annotations

from composition_grid import qt_compat
from composition_grid.core.geometry import Point
from composition_grid.presentation.handle_layout import HandleId

__all__ = [
    "BORDER_COLOR",
    "BORDER_WIDTH_PX",
    "HANDLE_SIZE_PX",
    "HOVER_BORDER_WIDTH_PX",
    "HOVER_FILL_COLOR",
    "FILL_COLOR",
    "MOVE_ARROW_PX",
    "MOVE_MARKER_HALF_PX",
    "ROTATE_BAR_PX",
    "ROTATE_RADIUS_PX",
    "VP_DIAMOND_PX",
    "paint_handles",
]

HANDLE_SIZE_PX = 8.0
"""Side length (widget px) of the corner/edge square markers."""

VP_DIAMOND_PX = 8.0
"""Bounding square (widget px) of the vanishing-point diamond marker."""

ROTATE_RADIUS_PX = 7.0
"""Widget-px radius of the rotate handle's circle."""

ROTATE_BAR_PX = 8.0
"""Widget-px length of the radial bar extending upward from the circle top."""

MOVE_MARKER_HALF_PX = 6.0
"""Half-length (widget px) of each arm of the move handle's 4-arrow cross."""

MOVE_ARROW_PX = 3.0
"""Widget-px length of the arrowhead strokes at each move-cross arm tip."""

BORDER_WIDTH_PX = 1.0
"""Default marker border width (widget px)."""

HOVER_BORDER_WIDTH_PX = 2.0
"""Hovered marker border width (widget px)."""

FILL_COLOR = (255, 255, 255)
"""Default marker fill: white RGB (full alpha)."""

HOVER_FILL_COLOR = (128, 128, 128)
"""Hovered marker fill: style-less mid-gray accent RGB (full alpha)."""

BORDER_COLOR = (0, 0, 0)
"""Marker border color: black RGB (full alpha)."""

# Fixed vocabulary from handle_layout (HandleId docstring); handle_layout keeps
# these ids internal, so they are re-stated here ONCE per file.
_MOVE_ID = "move"
_ROTATE_ID = "rotate"
_VP_ID = "vp0"


def _require_qt(entry: str) -> None:
    """Fail loudly (never silently) when the painter is used without PyQt."""
    if not qt_compat.QT_AVAILABLE:
        raise RuntimeError(
            f"composition_grid.presentation.handle_painter.{entry} requires "
            "PyQt, but composition_grid.qt_compat reports QT_AVAILABLE=False"
        )


def _paint_move_cross(qp, QtCore, x: float, y: float) -> None:
    """Draw the move handle's 4-arrow cross centered on ``(x, y)`` (widget px).

    Uses the painter's current pen (the border pen the caller set), so the
    ink is black and thickens under hover like every other marker. The two
    full-length arms pass through the anchor, so the center is always inked;
    an arrowhead (``MOVE_ARROW_PX`` strokes at +-45 degrees) caps each tip."""
    half = MOVE_MARKER_HALF_PX
    head = MOVE_ARROW_PX
    tips = (
        (x, y - half, 0.0, -1.0),
        (x, y + half, 0.0, 1.0),
        (x - half, y, -1.0, 0.0),
        (x + half, y, 1.0, 0.0),
    )
    # the cross itself (vertical + horizontal arm through the center)
    qp.drawLine(QtCore.QLineF(x, y - half, x, y + half))
    qp.drawLine(QtCore.QLineF(x - half, y, x + half, y))
    for tip_x, tip_y, dir_x, dir_y in tips:
        # perpendicular for the two head barbs, swept back from the tip
        perp_x, perp_y = -dir_y, dir_x
        base_x = tip_x - dir_x * head
        base_y = tip_y - dir_y * head
        qp.drawLine(QtCore.QLineF(tip_x, tip_y, base_x + perp_x * head, base_y + perp_y * head))
        qp.drawLine(QtCore.QLineF(tip_x, tip_y, base_x - perp_x * head, base_y - perp_y * head))


def paint_handles(
    qp,
    positions: "dict[HandleId, Point]",
    hover: "HandleId | None",
    edit_session: bool,
) -> None:
    """Paint the edit-session markers at ``positions`` (widget px).

    Args:
        qp: live ``QtGui.QPainter``; the current transform is reset to
            identity (markers are specified in widget pixels) and the previous
            state restored afterwards.
        positions: handle anchor positions in widget px — the ``dict[HandleId,
            Point]`` shape emitted by ``handle_layout.handle_positions``; the
            ``move`` anchor is painted as the visible 4-arrow cross marker
            (see the module docstring).
        hover: the hovered handle id or ``None``; hovered markers use the
            mid-gray accent fill and a thicker border.
        edit_session: when False (or ``positions`` empty) nothing is painted
            and the caller's painter content is left exactly as it was.

    Raises:
        RuntimeError: without PyQt (``qt_compat.QT_AVAILABLE`` is False).
    """
    if not edit_session or not positions:
        return
    _require_qt("paint_handles")
    QtCore = qt_compat.QtCore
    QtGui = qt_compat.QtGui

    qp.save()
    try:
        qp.resetTransform()
        fill_brushes: dict[bool, QtGui.QBrush] = {
            False: QtGui.QBrush(QtGui.QColor(*FILL_COLOR, 255)),
            True: QtGui.QBrush(QtGui.QColor(*HOVER_FILL_COLOR, 255)),
        }
        border_pens: dict[bool, QtGui.QPen] = {
            False: QtGui.QPen(QtGui.QColor(*BORDER_COLOR, 255), BORDER_WIDTH_PX),
            True: QtGui.QPen(QtGui.QColor(*BORDER_COLOR, 255), HOVER_BORDER_WIDTH_PX),
        }

        for handle, point in positions.items():
            hovered = handle == hover
            x, y = float(point[0]), float(point[1])
            half_side = HANDLE_SIZE_PX / 2.0
            qp.setPen(border_pens[hovered])
            qp.setBrush(fill_brushes[hovered])
            if handle == _MOVE_ID:
                _paint_move_cross(qp, QtCore, x, y)
            elif handle == _ROTATE_ID:
                center = QtCore.QPointF(x, y)
                qp.drawEllipse(center, ROTATE_RADIUS_PX, ROTATE_RADIUS_PX)
                bar_top = y - ROTATE_RADIUS_PX - ROTATE_BAR_PX
                qp.drawLine(
                    QtCore.QLineF(
                        QtCore.QPointF(x, y - ROTATE_RADIUS_PX),
                        QtCore.QPointF(x, bar_top),
                    )
                )
            elif handle == _VP_ID:
                half = VP_DIAMOND_PX / 2.0
                diamond = QtGui.QPolygonF(
                    [
                        QtCore.QPointF(x, y - half),
                        QtCore.QPointF(x + half, y),
                        QtCore.QPointF(x, y + half),
                        QtCore.QPointF(x - half, y),
                    ]
                )
                qp.drawPolygon(diamond)
            else:
                qp.drawRect(QtCore.QRectF(x - half_side, y - half_side, HANDLE_SIZE_PX, HANDLE_SIZE_PX))
    finally:
        qp.restore()
