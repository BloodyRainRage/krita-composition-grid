"""Qt painter tests for the overlay painter (grid primitives) and
handle painter (edit-session markers).

ALL tests are gated on PyQt availability with a two-layer gate: a
``pytest.mark.skipif`` evaluated from a non-raising importlib probe (a clean
whole-module SKIP without PyQt — ``pytest.skip`` cannot be called during
module import), plus an autouse fixture calling the shared
``tests.conftest.try_import_qt()`` inside test scope (legal ``skip``) so each
test stays gated even if Qt disappears between collection and run.
Rendering targets are offscreen
``QImage`` rasters (``QT_QPA_PLATFORM=offscreen`` is set before any Qt import
runs), pixel-inspected with ``QImage.pixelColor``; no assertions depend on
platform reasoning beyond the pure raster engine.

Pinned semantics implemented here (and in the painters):

* One combined painter transform maps element-normalized primitive points to
  widget pixels: ``QTransform(a, b, 0, c, d, 0, e, f, 1)`` must apply exactly
  like ``Affine.apply`` (``(x, y) -> (a*x + c*y + e, b*x + d*y + f)``).
* Widget-px pen width: painter-space width
  ``max(width_px, 0.1) / (s_e * view_scale)`` with
  ``s_e = sqrt(|det element_affine|)`` cancels the combined path->widget
  scale ``s_e * view_scale`` exactly, so the rendered ink thickness is
  ``width_px`` WIDGET px — CONSTANT under zoom (a 2 px line is 2 px at 0.5x
  and at 2.0x). The former doc-ink variant (thickness ``width_px *
  view_scale``) and the older doc-space acceptance numbers are superseded
  by the widget-px rule above.
* Arc angle convention: our samples are
  ``(cx + rx*cos t, cy + ry*sin t)`` in y-down space with ``t`` increasing;
  Qt's drawArc parameterization is ``(cx + rx*cos q, cy - ry*sin q)`` with a
  startAngle/spanAngle in 1/16 degree units, hence ``start = -a0*16`` and
  ``span = (a0 - a1)*16`` — for ``a0=0, a1=90`` the arc must lie in the
  BOTTOM-RIGHT quadrant of the screen (from 3 o'clock clockwise down).
"""

from __future__ import annotations

import os

# The offscreen platform is set before any Qt import in this module.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# --- GATE: no PyQt -> whole module skips at collection ----------------------
try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]


import composition_grid.qt_compat  # noqa: F401  (imports must not crash without Qt)
from composition_grid.core.geometry import Affine
from composition_grid.core.geometry_primitives import Arc, Dot, Polygon, Segment
from composition_grid.core.styles import Style
from composition_grid.presentation.overlay_painter import (
    DOT_RADIUS_PX,
    paint_primitives,
    q_transform_from_affine,
)
from composition_grid.presentation.handle_painter import paint_handles


def _qt_available() -> bool:
    """Non-raising probe that mirrors ``tests.conftest.try_import_qt`` order
    (PyQt6 first, then PyQt5). Never calls ``pytest.skip``: safe at import.

    Probes the ``PyQt{major}.QtWidgets`` SUBMODULE, not the bare package: a
    partially-removed install can leave the bare ``PyQt6`` package importable
    while actual bindings are gone (seen after ``uv pip uninstall PyQt6``
    left ``PyQt6-Qt6`` dist remnants), which must read as "not available".
    """
    import importlib

    for major in (6, 5):
        try:
            importlib.import_module(f"PyQt{major}.QtWidgets")
        except ImportError:
            continue
        return True
    return False


def _import_qt_modules():
    """Resolve ``(QtWidgets, QtCore, QtGui)`` from the first binding whose
    submodules all import (same order as conftest); ``None`` without PyQt."""
    import importlib

    for major in (6, 5):
        try:
            return tuple(
                importlib.import_module(f"PyQt{major}.{name}")
                for name in ("QtWidgets", "QtCore", "QtGui")
            )
        except ImportError:
            continue
    return None


pytestmark = pytest.mark.skipif(
    not _qt_available(), reason="PyQt not installed (uv install if needed)"
)

QtWidgets, QtCore, QtGui = _import_qt_modules() or (None, None, None)

_ARGB32 = (
    (
        QtGui.QImage.Format.Format_ARGB32
        if hasattr(QtGui.QImage, "Format")
        else QtGui.QImage.Format_ARGB32
    )
    if QtGui is not None
    else None
)
_BG = (255, 255, 0)  # yellow — distinguishable from white fills and dark marks


@pytest.fixture(autouse=True)
def _qt_gate():
    """Per-test gate through the shared conftest helper: inside test scope its
    ``pytest.skip`` is legal, so a disappearing PyQt still skips cleanly."""
    try_import_qt()
    yield


_KEEP_APP_REF = None


def _ensure_qapp():
    """Create the shared QApplication singleton once (offscreen platform).

    PyQt6 GOTCHA: the created ``QApplication`` wrapper MUST be kept referenced
    for the whole test lifetime — a helper-local wrapper dies at return,
    whereupon PyQt deletes the underlying C++ application and later painting
    runs against a dead app instance (misleading pixel drifts / qFatal
    crashes). The module global holds the reference explicitly."""
    global _KEEP_APP_REF
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        _KEEP_APP_REF = QtWidgets.QApplication([])
    elif _KEEP_APP_REF is None:
        _KEEP_APP_REF = inst


def _image(width: int = 200, height: int = 200) -> QtGui.QImage:
    _ensure_qapp()
    img = QtGui.QImage(width, height, _ARGB32)
    img.fill(QtGui.QColor(*_BG))
    return img


def _paint_on(img: QtGui.QImage, fn) -> QtGui.QImage:
    painter = QtGui.QPainter(img)
    try:
        fn(painter)
    finally:
        painter.end()
    return img


def _color_at(img: QtGui.QImage, x: int, y: int) -> QtGui.QColor:
    return img.pixelColor(int(x), int(y))


def _is_dark(color: QtGui.QColor) -> bool:
    """Any painted mark: background/white have red == 255, marks are darker."""
    return color.red() < 128


def _is_white(color: QtGui.QColor) -> bool:
    return (color.red(), color.green(), color.blue()) == (255, 255, 255)


def _dark_column_count(img: QtGui.QImage, x: int) -> int:
    return sum(1 for y in range(img.height()) if _is_dark(_color_at(img, x, y)))


def _dark_points(img: QtGui.QImage, x0, x1, y0, y1):
    return [
        (x, y)
        for x in range(x0, x1)
        for y in range(y0, y1)
        if _is_dark(_color_at(img, x, y))
    ]


def _runs_along_row(img: QtGui.QImage, row: int, x0: int, x1: int):
    """Boolean run list [(is_dark, length), ...] of row x0..x1-1."""
    darker = [_is_dark(_color_at(img, x, row)) for x in range(x0, x1)]
    runs, cur, ln = [], darker[0], 1
    for value in darker[1:]:
        if value == cur:
            ln += 1
        else:
            runs.append((cur, ln))
            cur, ln = value, 1
    runs.append((cur, ln))
    return runs


def _stroke_row(img: QtGui.QImage, x0: int, x1: int, y_hint: int) -> int:
    """Row nearest y_hint carrying the most dark pixels in x0..x1-1."""
    return max(
        range(max(0, y_hint - 8), min(img.height(), y_hint + 9)),
        key=lambda ry: sum(
            1 for x in range(x0, x1) if _is_dark(_color_at(img, x, ry))
        ),
    )


# --- transform convention pin ------------------------------------------------


def test_transform_maps_points_identically():
    """``QTransform`` built from an Affine must map points exactly like
    ``Affine.apply`` (row a/b/c/d/e/f vs Qt's m11/m12/m21/m22/dx/dy slots)."""
    affine = (
        Affine.rotation_deg(30.0)
        .composed(Affine.scaling(1.5, 2.0))
        .composed(Affine.translation(7.0, -3.0))
    )
    transform = q_transform_from_affine(affine)
    probes = [(0.0, 0.0), (1.0, 2.0), (-3.0, 0.5), (10.0, -7.25)]
    for px, py in probes:
        mapped = transform.map(QtCore.QPointF(px, py))
        expected = affine.apply((px, py))
        assert abs(mapped.x() - expected[0]) < 1e-9, (probes, px, py)
        assert abs(mapped.y() - expected[1]) < 1e-9, (probes, px, py)


# --- pen width scale compensation ---------------------------------------------


def test_pen_width_scales_with_view():
    """Widget-px pen width is CONSTANT under zoom: on-screen thickness ==
    width_px (2 widget px here) at BOTH view scales (0.5 and 2.0).

    Derivation (also pinned in module docstring): painter-space pen width =
    max(width_px, 0.1) / (s_e * view_scale); the combined path->widget scale
    is s_e * view_scale, which multiplies the pen back up to exactly
    max(width_px, 0.1) widget px at every zoom. +-1 px raster tolerance for
    anti-alias/rounding.
    """
    _ensure_qapp()
    style = Style(color_rgba=(0.0, 0.0, 0.0, 1.0), opacity=1.0, width_px=2.0)
    seg = Segment((0.25, 0.5), (0.75, 0.5))

    # LOW zoom: doc 400x400 -> widget 200x200 (view_scale = 200/400 = 0.5);
    # the element occupies doc (100,100)..(300,300) and the segment's doc
    # midpoint is y = 200 -> widget y = 100, widget x = 75..125.
    element_low = Affine.scaling(200.0, 200.0).composed(Affine.translation(100.0, 100.0))
    to_widget_low = Affine.scaling(0.5, 0.5)
    img_low = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp, (seg,), element_low, to_widget_low, 0.5, style, doc_size=(400, 400)
        ),
    )
    thickness_low = _dark_column_count(img_low, 100)
    assert abs(thickness_low - 2) <= 1  # constant rule: width_px == 2 widget px

    # HIGH zoom: doc 100x100 -> widget 200x200 (view_scale = 200/100 = 2);
    # element occupies doc (25,25)..(75,75); the segment lands at the same
    # widget band (y = 100, x = 75..125) for a controlled comparison.
    element_high = Affine.scaling(50.0, 50.0).composed(Affine.translation(25.0, 25.0))
    to_widget_high = Affine.scaling(2.0, 2.0)
    img_high = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp, (seg,), element_high, to_widget_high, 2.0, style, doc_size=(100, 100)
        ),
    )
    thickness_high = _dark_column_count(img_high, 100)
    assert abs(thickness_high - 2) <= 1  # constant rule: width_px == 2 widget px

    assert abs(thickness_high - thickness_low) <= 1, (
        "widget-px thickness must stay constant under zoom"
    )


# --- deterministic snapshot of basic primitives --------------------------------


def test_snapshot_basic_primitives_image():
    """Every primitive lands in its expected widget band (computed from the
    same public matrices, not from the implementation); nothing strays more
    than the stated band width, and no golden image dump is used."""
    _ensure_qapp()
    style = Style(color_rgba=(0.0, 0.0, 0.0, 1.0), opacity=1.0, width_px=1.0)
    element = Affine.scaling(200.0, 200.0)  # normalized 0..1 -> widget 0..200
    doc_to_widget = Affine.identity()  # doc 200x200 == widget 200x200
    doc_size = (200, 200)

    def expected(p):
        return doc_to_widget.apply(element.apply(p))

    seg = Segment((0.25, 0.5), (0.75, 0.5))
    arc = Arc(center=(0.5, 0.5), rx=0.3, ry=0.3, a0=0.0, a1=90.0)
    poly = Polygon(points=((0.2, 0.1), (0.8, 0.1), (0.2, 0.9)))
    dot = Dot(position=(0.5, 0.1))

    img = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp, (seg, arc, poly, dot), element, doc_to_widget, 1.0, style, doc_size
        ),
    )

    # NOTE: the polygon's hypotenuse passes EXACTLY through the arc's center,
    # and the polygon top edge grazes the dot — so the arc's quadrant/annulus
    # pin and the dot's radius pin run on their OWN uncontaminated images
    # below; the combined image is only used for the segment / polygon bands
    # (which never collide).

    # Segment: opaque pixels exactly along the expected segment (band +-2 px).
    ex1, ey1 = expected((0.25, 0.5))
    ex2, ey2 = expected((0.75, 0.5))
    assert abs(ey1 - round(ey1)) < 1e-9 and abs(ey2 - round(ey2)) < 1e-9
    assert abs(ey1 - ey2) < 1e-9  # horizontal segment
    row = int(round(ey1))
    seg_dark = [(x, y) for x in range(int(ex1), int(ex2) + 1) for y in range(max(0, row - 2), min(img.height(), row + 3)) if _is_dark(_color_at(img, x, y))]
    assert seg_dark, "segment pixels missing"
    assert all(abs(y - row) <= 2 for _, y in seg_dark), "segment leaked out of its band"

    # Arc (a0=0, a1=90, quarter), on its own image: all arc pixels must lie
    # on the expected 1-px annulus AND in the bottom-right quadrant
    # (y-down: from 3 o'clock DOWN to 6 o'clock) — pins the Qt angle mapping.
    arc_img = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp, (arc,), element, doc_to_widget, 1.0, style, doc_size
        ),
    )
    cx, cy = expected(arc.center)
    radius = arc.rx * element.a
    arc_dark = _dark_points(arc_img, 0, arc_img.width(), 0, arc_img.height())
    assert len(arc_dark) >= 10, "arc has no pixels"
    distances = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in arc_dark]
    assert all(radius - 4 <= d <= radius + 3 for d in distances), (
        "arc pixels outside the expected 1-px annulus"
    )
    assert all(x >= cx - 2 and y >= cy - 2 for x, y in arc_dark), (
        "arc leaked outside the bottom-right quadrant"
    )

    # Polygon top edge: expected horizontal line at y of (0.2,0.1)..(0.8,0.1).
    px1, py1 = expected(poly.points[0])
    px2, py2 = expected(poly.points[1])
    prow = int(round(py1))
    assert abs(py2 - py1) < 1e-9
    poly_dark = [y for x in range(int(px1), int(px2) + 1) for y in range(prow - 2, prow + 3) if _is_dark(_color_at(img, x, y))]
    assert poly_dark, "polygon edge pixels missing"
    assert all(abs(y - prow) <= 2 for y in poly_dark)

    # Dot, on its own image: a small filled marker at the expected position
    # (radius DOT_RADIUS_PX; unpolluted by the polygon top edge that grazes it
    # in the combined image).
    dot_img = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp, (dot,), element, doc_to_widget, 1.0, style, doc_size
        ),
    )
    dx, dy = expected(dot.position)
    dot_radius = DOT_RADIUS_PX  # view_scale 1: widget px == doc px here
    around = _dark_points(dot_img, int(dx) - int(dot_radius) - 3, int(dx) + int(dot_radius) + 4, int(dy) - int(dot_radius) - 3, int(dy) + int(dot_radius) + 4)
    assert around, "dot marker missing"
    assert max(
        ((x - dx) ** 2 + (y - dy) ** 2) ** 0.5 for x, y in around
    ) <= dot_radius + 2, "dot marker bigger than the documented radius"


# --- dash pattern --------------------------------------------------------------


_DASH = (6.0, 4.0)
_DASH_PERIOD_PX = 10.0  # sum(style.dash): WIDGET px, constant under zoom/width


def _dash_run_starts(runs, x0):
    """Dark-run start x's, dropping the first (line-cap) run."""
    starts, x = [], x0
    for value, length in runs:
        if value:
            starts.append(x)
        x += length
    return starts[1:]


@pytest.mark.parametrize("width_px", [1.0, 2.0, 3.0])
@pytest.mark.parametrize(
    "elem_doc,view_scale",
    [(200.0, 1.0), (200.0, 0.5), (100.0, 1.0), (100.0, 2.0), (50.0, 2.0)],
)
def test_dash_period_is_widget_px_at_every_zoom_and_width(
    elem_doc, view_scale, width_px
):
    """Raster-pinned: widget dash period is sum(style.dash) px at any zoom/width."""
    _ensure_qapp()
    style = Style(
        color_rgba=(0.0, 0.0, 0.0, 1.0),
        opacity=1.0,
        width_px=width_px,
        dash=_DASH,
    )
    element = Affine.scaling(elem_doc, elem_doc)
    doc_to_widget = Affine.scaling(view_scale, view_scale)
    widget_w = int(elem_doc * view_scale)
    seg = Segment((0.02, 0.5), (0.98, 0.5))
    img = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp,
            (seg,),
            element,
            doc_to_widget,
            view_scale,
            style,
            doc_size=(elem_doc, elem_doc),
        ),
    )

    x0, x1 = 3, widget_w - 3
    row = _stroke_row(img, x0, x1, int(round(elem_doc * view_scale * 0.5)))
    runs = _runs_along_row(img, row, x0, x1)

    dark_starts = _dash_run_starts(runs, x0)
    assert len(dark_starts) >= 4, f"few dash periods in one line: {runs[:12]}"
    periods = [b - a for a, b in zip(dark_starts, dark_starts[1:])]
    assert all(
        abs(p - _DASH_PERIOD_PX) <= 1.0 for p in periods
    ), f"dash period must be {int(_DASH_PERIOD_PX)} widget px, got {periods} {runs[:14]}"

    interior = runs[2:-2]
    for value, length in interior:
        if value:
            assert _DASH[0] - 1.0 <= length <= _DASH[0] + width_px + 1.0, (
                f"dark run {length} outside dash+cap band {runs[:14]}"
            )
        else:
            assert _DASH[1] - width_px - 1.0 <= length <= _DASH[1] + 1.0, (
                f"gap run {length} outside dash-cap band {runs[:14]}"
            )


def test_dash_pattern_applications():
    """A dash style yields an alternating on/off line (>=1 gap run and >=1
    opaque run along the row), which pins ``CustomDashLine`` being active at
    paint time without needing to intercept the painter."""
    _ensure_qapp()
    style = Style(
        color_rgba=(0.0, 0.0, 0.0, 1.0),
        opacity=1.0,
        width_px=1.0,
        dash=(6.0, 4.0),
    )
    # view_scale 1: doc 200x200 == widget 200x200, element fills the widget.
    element = Affine.scaling(200.0, 200.0)
    doc_to_widget = Affine.identity()
    seg = Segment((0.25, 0.5), (0.75, 0.5))  # widget x 50..150 -> y = 100
    img = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp, (seg,), element, doc_to_widget, 1.0, style, doc_size=(200, 200)
        ),
    )

    xs = range(60, 141)
    darker = [_is_dark(_color_at(img, x, 100)) for x in xs]
    assert any(darker), "no painted dashes"

    # group into runs of dark / non-dark along the row
    runs = []
    current = darker[0]
    length = 1
    for value in darker[1:]:
        if value == current:
            length += 1
        else:
            runs.append((current, length))
            current, length = value, 1
    runs.append((current, length))

    dark_runs = [n for value, n in runs if value]
    gap_runs = [n for value, n in runs if not value]
    assert len(dark_runs) >= 2, f"expected several dash runs, got {runs}"
    assert len(gap_runs) >= 2, f"expected several gap runs, got {runs}"


# --- doc-rect clip --------------------------------------------------------------


def test_paint_clipped_to_doc_rect():
    """With ``doc_size`` given, nothing may paint outside the widget-space
    bounding box of the document rect, even when the primitive overshoots it."""
    _ensure_qapp()
    style = Style(color_rgba=(0.0, 0.0, 0.0, 1.0), opacity=1.0, width_px=1.0)
    # Contract: primitives are ALWAYS element-normalized, so the element affine
    # must map those units onto the claimed doc space: scale (100, 100) makes
    # the element cover the whole doc rect (0,0)..(100,100). The segment then
    # spans doc-widget x -50..150 at y = 50 (doc_to_widget is identity,
    # view_scale 1) — half of it lies outside the doc rect and must be clipped.
    element = Affine.scaling(100.0, 100.0)
    seg = Segment((-0.5, 0.5), (1.5, 0.5))  # widget x -50..150, y = 50
    img = _paint_on(
        _image(),
        lambda qp: paint_primitives(
            qp,
            (seg,),
            element,
            Affine.identity(),
            1.0,
            style,
            doc_size=(100, 100),
        ),
    )

    assert _is_dark(_color_at(img, 50, 50)), "visible part must be painted"
    leaked = _dark_points(img, 101, 200, 45, 56)
    assert not leaked, f"clip failed: painted outside the doc rect at {leaked[:5]}"


# --- handle markers --------------------------------------------------------------


def _handle_positions():
    return {
        "nw": (50.0, 50.0),
        "se": (150.0, 150.0),
        "rotate": (100.0, 100.0),
        "move": (100.0, 150.0),
    }


def test_handles_paint_markers_and_hover():
    """edit_session=True paints white-filled markers (rotate = circle +
    upward bar, move = visible 4-arrow cross); hover turns the hovered marker
    gray; with edit_session=False the image stays untouched."""
    _ensure_qapp()
    positions = _handle_positions()

    img_default = _paint_on(
        _image(),
        lambda qp: paint_handles(qp, positions, None, True),
    )
    # white marker centers at the three visible anchors
    for handle_id in ("nw", "se", "rotate"):
        x, y = positions[handle_id]
        color = _color_at(img_default, x, y)
        assert (color.red(), color.green(), color.blue(), color.alpha()) == (255, 255, 255, 255), (
            f"{handle_id} marker missing its white fill at {(x, y)}"
        )
    # move anchor now carries a visible cross marker (dark ink at the center)
    mx, my = positions["move"]
    assert _is_dark(_color_at(img_default, mx, my)), (
        "move handle must paint its cross marker (the old invisible move "
        "handle was the whole point of the drag-anywhere fix)"
    )
    # the rotate bar extends upward from the circle: dark pixel above it
    rx, ry = positions["rotate"]
    bar_ys = [y for y in range(int(ry) - 20, int(ry) - 10) if _is_dark(_color_at(img_default, rx, y))]
    assert bar_ys, "rotate bar above the circle is missing"

    # hover: the hovered marker's fill turns mid-gray, others stay white
    img_hover = _paint_on(
        _image(),
        lambda qp: paint_handles(qp, positions, "se", True),
    )
    hx, hy = positions["se"]
    hcolor = _color_at(img_hover, hx, hy)
    assert (hcolor.red(), hcolor.green(), hcolor.blue()) == (128, 128, 128), (
        "hovered marker must use the mid-gray accent fill"
    )
    ox, oy = positions["nw"]
    ocolor = _color_at(img_hover, ox, oy)
    assert (ocolor.red(), ocolor.green(), ocolor.blue()) == (255, 255, 255), (
        "non-hovered markers must stay white under hover"
    )

    # edit_session False: no markers at all (image stays at background)
    img_off = _paint_on(
        _image(),
        lambda qp: paint_handles(qp, positions, None, False),
    )
    for x in range(img_off.width()):
        for y in range(img_off.height()):
            color = _color_at(img_off, x, y)
            assert (color.red(), color.green(), color.blue()) == _BG, (
                f"edit_session=False must paint nothing at {(x, y)}"
            )


def test_move_marker_is_a_bounded_cross_at_its_anchor():
    """The move marker is a small cross centered on the move anchor: dark ink
    reaches along the arms, but it stays within a few widget px of the anchor
    (it must not smear across the element the way the old invisible handle did
    nothing, nor paint an oversized block)."""
    _ensure_qapp()
    positions = {"move": (100.0, 100.0)}
    img = _paint_on(_image(), lambda qp: paint_handles(qp, positions, None, True))

    assert _is_dark(_color_at(img, 100, 100)), "cross center must be inked"
    # ink along both axes near the anchor (the cross arms)
    assert any(_is_dark(_color_at(img, 100, y)) for y in range(95, 106)), "vertical arm missing"
    assert any(_is_dark(_color_at(img, x, 100)) for x in range(95, 106)), "horizontal arm missing"
    # contained: nothing far from the anchor is touched
    far = _dark_points(img, 0, 80, 80, 120) + _dark_points(img, 120, 200, 80, 120)
    far += _dark_points(img, 80, 120, 0, 80) + _dark_points(img, 80, 120, 120, 200)
    assert not far, f"move marker escaped its neighborhood: {far[:5]}"

    # hover keeps the same ink presence (the marker is drawn, just accented)
    img_hover = _paint_on(_image(), lambda qp: paint_handles(qp, positions, "move", True))
    assert _is_dark(_color_at(img_hover, 100, 100)), "hovered move marker still inked"

    # edit_session False: still paints nothing at the move anchor
    img_off = _paint_on(_image(), lambda qp: paint_handles(qp, positions, None, False))
    assert _is_white_or_bg(_color_at(img_off, 100, 100)), (
        "no move marker outside an edit session"
    )


def _is_white_or_bg(color: QtGui.QColor) -> bool:
    return not _is_dark(color)
