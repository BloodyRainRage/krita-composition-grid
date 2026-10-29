"""Qt overlay painter: draw the grid primitives with a QPainter.

Qt-dependent module — ALL Qt naming goes through
:mod:`composition_grid.qt_compat` (the single legal PyQt import route), and
every entry point raises ``RuntimeError`` when PyQt is absent instead of
crashing at import time (functions only touch ``qt_compat.QtCore/QtGui``
lazily, inside their bodies).

Conventions (see also the Qt test module
``tests/presentation/test_overlay_painter_qt.py``):

* Painter path space: the primitives arrive in **element-local normalized**
  coordinates (the shape ``GridGeometry`` builders emit). One
  combined matrix ``element_affine ∘ doc_to_widget`` (composed in that order,
  mapping normalized points -> element doc points -> widget px) is installed
  as a single ``QGui`` ``QTransform`` via :func:`q_transform_from_affine`.
* Transform mapping: our ``Affine`` applies as
  ``(x, y) -> (a*x + c*y + e, b*x + d*y + f)``; Qt's affine ``QTransform``
  maps as ``x' = m11*x + m21*y + dx`` / ``y' = m12*x + m22*y + dy``, so the
  constructor argument mapping is
  ``QTransform(a, b, 0, c, d, 0, e, f, 1)``. Pinned numerically by
  ``test_transform_maps_points_identically``.
* Pen width (WIDGET px, constant under zoom):
  painter-space width is ``max(style.width_px, 0.1) / (s_e * view_scale)``
  where ``s_e = sqrt(|det element_affine|)``. Algebra: the combined
  path->widget length scale is ``s_e * view_scale``, the Qt pen width is
  multiplied by that scale, so dividing it out by BOTH factors renders an
  on-screen ink thickness equal to ``max(style.width_px, 0.1)`` widget px at
  EVERY zoom (a 2 px line stays 2 px at 0.5x and at 2.0x). The earlier
  doc-ink variant (thickness ``width_px * view_scale``) is explicitly ruled
  out.
* Dash: ``style.dash`` widget px -> ``d / max(width_px, 0.1)`` pen-width units.
* Opacity: ``Style.opacity`` is the LAYER opacity and ``color_rgba[3]`` the
  per-primitive alpha; they compose multiplicatively — ``QColor`` alpha =
  ``round(255 * color_rgba[3] * opacity)`` (styles.py gives no separate
  semantics, this is the documented choice).
* Clip: single simple rule — the painter is clipped to the axis-aligned
  widget-space bounding box of the document rect corners mapped through
  ``doc_to_widget`` (the canvas may show area outside the document; nothing
  must paint there), regardless of whether a primitive escapes the element
  bounds. NOTE: it is the bbox of the (possibly rotated) doc rect, so a
  corner pixel outside the rotated rect but inside its bbox may still paint;
  exact rotation-aware region clipping is out of scope. The document size is
  not part of the signature, so it arrives via the
  optional ``doc_size`` kwarg (doc px); without it no clip is installed (the
  caller's existing clip still applies). The clip is set BEFORE
  ``setTransform`` because clips are resolved in the coordinate system that
  is current at the time: doc-corner widget points need the identity
  transform.
* Arc: Qt parameterizes ``drawArc`` as ``(cx + rx*cos q, cy - ry*sin q)``
  with start/span angles in 1/16 degree; our samples
  ``(cx + rx*cos t, cy + ry*sin t)`` (y-down doc space) correspond to
  ``q = -t``, hence ``startAngle = round(-a0*16)`` and
  ``spanAngle = round((a0 - a1)*16)`` — a1 > a0 sweeps DOWN from 3 o'clock
  (bottom-right for the 0..90 quarter). Pinned by the snapshot test.
* Dot: a small filled center marker of radius ``DOT_RADIUS_PX`` widget px
  (converted to painter space via ``s_e * view_scale``); fixed size keeps the
  marker legible at any zoom.
* Antialiasing: the painter deliberately does NOT toggle render hints — the
  caller owns them. With AA off, the Qt raster engine renders these shapes
  with 1-px accuracy in the band tests; enabling AA in the caller
  spreads edges by ~1 px, which the tests' +-1/-2 px tolerances absorb.
* Polygons render OUTLINED (the painter brush is untouched, default
  ``NoBrush``); filled cells remain a call-site concern.
"""

from __future__ import annotations

import math

from composition_grid import qt_compat
from composition_grid.core.geometry import Affine, Point
from composition_grid.core.geometry_primitives import (
    Arc,
    Dot,
    GridGeometry,
    Polygon,
    Segment,
)
from composition_grid.core.styles import Style

__all__ = [
    "DOT_RADIUS_PX",
    "MIN_PEN_WIDTH_PX",
    "paint_primitives",
    "q_transform_from_affine",
]

DOT_RADIUS_PX = 3.0
"""Widget-space radius of the ``Dot`` center marker."""

MIN_PEN_WIDTH_PX = 0.1
"""Widget-px pen width floor (0.1 minimum, in widget px)."""

_EPS = 1e-9


def _require_qt(entry: str) -> None:
    """Fail loudly (never silently) when the painter is used without PyQt."""
    if not qt_compat.QT_AVAILABLE:
        raise RuntimeError(
            f"composition_grid.presentation.overlay_painter.{entry} requires "
            "PyQt, but composition_grid.qt_compat reports QT_AVAILABLE=False"
        )


def _element_scale(affine: Affine) -> float:
    """Element doc px per normalized unit: ``sqrt(|det|)`` of the 2x3 matrix.

    Rotation/mirror keep ``|det| = 1``; uniform scaling contributes its factor
    on both axes; anisotropic elements get the geometric mean (documented
    approximation — anisotropic pen widths are out of scope for V1).
    """
    det = affine.a * affine.d - affine.b * affine.c
    return max(math.sqrt(abs(det)), 1e-12)


def q_transform_from_affine(affine: Affine):
    """The single ``QtGui.QTransform`` matching this plugin's ``Affine``.

    See the module docstring for the slot mapping;
    ``test_transform_maps_points_identically`` pins it numerically.
    """
    _require_qt("q_transform_from_affine")
    return qt_compat.QtGui.QTransform(
        affine.a, affine.b, 0.0,
        affine.c, affine.d, 0.0,
        affine.e, affine.f, 1.0,
    )


def _doc_widget_clip(doc_to_widget: Affine, doc_size, QtCore):
    """Widget-space bounding rect over the document corners (0,0)-(w,h) doc.

This is the axis-aligned bbox of the (possibly rotated) doc rect; see the
module docstring clip rule for the corner-pixel caveat. The CLIP's coordinate
system: expressed in widget px, applied while the painter is still identity.
"""
    doc_w, doc_h = float(doc_size[0]), float(doc_size[1])
    xs: list[float] = []
    ys: list[float] = []
    for corner in (
        (0.0, 0.0),
        (doc_w, 0.0),
        (doc_w, doc_h),
        (0.0, doc_h),
    ):
        wx, wy = doc_to_widget.apply(corner)
        xs.append(wx)
        ys.append(wy)
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return QtCore.QRectF(x0, y0, max(x1 - x0, _EPS), max(y1 - y0, _EPS))


def _style_color(style: Style, QtGui):
    """``QColor`` from the style: rgb from color_rgba, alpha combining the
    per-primitive rgba alpha with the layer opacity multiplicatively."""
    r, g, b, a = style.color_rgba
    alpha = int(round(255.0 * a * style.opacity))
    alpha = max(0, min(255, alpha))
    return QtGui.QColor(int(round(r * 255.0)), int(round(g * 255.0)), int(round(b * 255.0)), alpha)


def _paint_segment(qp, QtCore, seg: Segment) -> None:
    qp.drawLine(
        QtCore.QLineF(
            QtCore.QPointF(seg.p1[0], seg.p1[1]),
            QtCore.QPointF(seg.p2[0], seg.p2[1]),
        )
    )


def _paint_arc(qp, QtCore, arc: Arc) -> None:
    rect = QtCore.QRectF(
        arc.center[0] - arc.rx,
        arc.center[1] - arc.ry,
        2.0 * arc.rx,
        2.0 * arc.ry,
    )
    # Stop before divide/round issues: degenerate radii still draw fine.
    start = int(round(-arc.a0 * 16.0))
    span = int(round((arc.a0 - arc.a1) * 16.0))
    qp.drawArc(rect, start, span)


def _paint_polygon(qp, QtGui, QtCore, poly: Polygon) -> None:
    qp.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in poly.points]))


def _paint_dot(qp, QtGui, QtCore, dot: Dot, painter_units_per_widget_px: float) -> None:
    radius = DOT_RADIUS_PX / max(painter_units_per_widget_px, _EPS)
    center = QtCore.QPointF(dot.position[0], dot.position[1])
    previous_brush = qp.brush()
    qp.setBrush(QtGui.QBrush(qp.pen().color()))
    qp.drawEllipse(center, radius, radius)
    qp.setBrush(previous_brush)


def paint_primitives(
    qp,
    primitives: GridGeometry,
    element_affine: Affine,
    doc_to_widget: Affine,
    view_scale: float,
    style: Style,
    doc_size: "tuple[float, float] | None" = None,
) -> None:
    """Draw ``primitives`` through one combined widget transform.

    Args:
        qp: live ``QtGui.QPainter`` (state is saved/restored around the call).
        primitives: element-normalized grid geometry (GridGeometry shapes).
        element_affine: normalized -> element doc coordinates.
        doc_to_widget: doc px -> widget px (may include rotation/mirror).
        view_scale: widget px per doc px (part of the widget-px scale
            compensation for pen width and Dot radius — see the module
            docstring for the derivation).
        style: pen color/width/opacity/dash (``core.styles.Style``).
        doc_size: document size in doc px ``(w, h)``; when given the painter
            is clipped to the doc-rect widget bbox (single clip rule, module
            docstring). ``None`` leaves the caller's clip untouched.

    Raises:
        RuntimeError: without PyQt (``qt_compat.QT_AVAILABLE`` is False).
        TypeError: on an unknown primitive type.
    """
    _require_qt("paint_primitives")
    QtCore = qt_compat.QtCore
    QtGui = qt_compat.QtGui

    qp.save()
    try:
        if doc_size is not None:
            # Clips bind to the coordinate system current NOW (identity) so the
            # doc-corner widget bbox is expressed directly in widget px.
            qp.setClipRect(_doc_widget_clip(doc_to_widget, doc_size, QtCore))

        combined = element_affine.composed(doc_to_widget)
        qp.setTransform(q_transform_from_affine(combined))

        # Widget-px scale compensation: the combined
        # path->widget length scale is s_e * view_scale, so pen widths and
        # Dot radii divide by it to render style px 1:1 on screen at every
        # zoom.
        scale_to_widget = _element_scale(element_affine) * max(float(view_scale), _EPS)
        widget_pen_width_px = max(style.width_px, MIN_PEN_WIDTH_PX)
        pen = QtGui.QPen(
            QtGui.QBrush(_style_color(style, QtGui)),
            widget_pen_width_px / scale_to_widget,
        )
        # Dash entries are pen-width units, not scale_to_widget units.
        if style.dash:
            pen.setStyle(qt_compat.CustomDashLine)
            pen.setDashPattern([float(d) / widget_pen_width_px for d in style.dash])
        qp.setPen(pen)

        painter_units_per_widget_px = scale_to_widget
        for primitive in primitives:
            if isinstance(primitive, Segment):
                _paint_segment(qp, QtCore, primitive)
            elif isinstance(primitive, Arc):
                _paint_arc(qp, QtCore, primitive)
            elif isinstance(primitive, Polygon):
                _paint_polygon(qp, QtGui, QtCore, primitive)
            elif isinstance(primitive, Dot):
                _paint_dot(qp, QtGui, QtCore, primitive, painter_units_per_widget_px)
            else:
                raise TypeError(f"unsupported primitive: {primitive!r}")
    finally:
        qp.restore()
