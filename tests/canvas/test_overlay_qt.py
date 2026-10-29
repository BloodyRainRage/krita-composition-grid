"""Qt tests for the canvas overlay widget and the canvas context.

ALL tests are gated on PyQt availability with the same two-layer gate as
``tests.canvas.test_canvas_locator_qt``: a ``pytest.mark.skipif`` evaluated
from a non-raising importlib probe (a clean whole-module SKIP without PyQt —
no Qt value is dereferenced at module import either), plus an autouse
fixture calling the shared ``tests.conftest.try_import_qt()`` inside test
scope. ``QT_QPA_PLATFORM=offscreen`` is set before any Qt import runs.
Rendering goes to offscreen ``QImage`` rasters through the overlay's public
``paint_grid(qp)`` seam (the exact code path its ``paintEvent`` runs), and
pixel inspection follows the tolerance-band style of
``tests/presentation/test_overlay_painter_qt.py``.

Semantics implemented here:

* paint without a model, or with ``model.visible`` False, paints NOTHING
  (the target image stays byte-for-byte at its background fill).
* ``WA_TransparentForMouseEvents`` is True outside the edit session and
  False inside it (input transparency).
* edit-session mouse protocol: press ON a handle -> ``TransformController``
  drag (event accepted, model transform actually changes with the ratio
  lock and opposite-corner fixity); press with NO handle hit -> the edit
  session ENDS and the handler does NOT accept the event (live propagation
  of that press to the parent canvas is a manual check in
  Krita). Move without a press is hover + cursor only.
  Outside a session a press is never consumed.
* CanvasContext: the ViewState is COMPUTED on demand from the
  host getters — viewport size from the attached canvas widget, document extent
  from ``View.document().width()/.height()``, ``zoomLevel()``,
  ``preferredCenter()``, ``rotation()``, ``mirror()`` (a plain bool on 5.3.4.1)
  — through ``view_metrics``. The scale IS ``zoomLevel()``: the live
  host's two scrollbar values refute the ``zoom*resolution/72`` variant
  (see ``tests/canvas/test_view_metrics.py``), so
  ``Document.resolution()`` is not an input at all. Canvas signals are only invalidation
  accelerators (every handler repaints; on 6.x the precise
  ``documentRectInWidgetPixels()`` getter wins over the computed rect, on 5.3
  where NO Canvas signal and no rect getter exist at all — the
  computed rect is the only source). ``sizeChanged(QSize)`` is widget px, so it
  repaints only; the DOCUMENT extent belongs to the Document.
  The 5.3 repaint triggers are genuine Qt events with NO timers: the owning
  ``QAbstractScrollArea``'s horizontal/vertical scrollbar ``valueChanged`` and
  ``rangeChanged``, plus an event filter on the viewport for Wheel/Resize/
  Scroll/ScrollPrepare/``Paint`` (Paint covers mirror /
  rotation / slider-zoom changes that move neither bar) and a holder filter
  for ``LayoutRequest`` (the resetCanvas widget swap). Degradations (no canvas
  widget, absent signals/getters,
  unreadable document, ``removed``) hide the grid and never raise; ``unbind``
  disconnects every connection including the scrollbar ones.
* Bind-time plausibility: the lenient locator fallback
  can resolve a QDockWidget's scroll-area viewport — ``bind`` degrades that
  impostor exactly like a missing widget (INFO with the reason,
  ``False``, no overlay) while view-state tracking keeps running.
* ``activeViewChanged`` re-resolves FRESH objects before rebinding: a view-like
  payload contributes its own ``canvas()``, else the
  window's ``activeView()`` does, else a window-like payload re-resolves only
  the window, and the stored triplet survives as the last resort — after a
  real view switch the OLD canvas's signals no longer drive state or repaints.
* No krita import anywhere: canvas/window objects arrive duck-typed; the
  fake canvas is a real QObject whose signals are declared with
  ``pyqtSignal`` (the very object qt_compat re-exports as ``Signal``).
"""

from __future__ import annotations

import gc
import logging
import os

# The offscreen platform is set before any Qt import in this module.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# --- GATE: no PyQt -> whole module skips at collection ----------------------
try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]

import composition_grid.qt_compat as qt_compat  # noqa: F401  (pure-safe import)
from composition_grid.canvas.canvas_context import CanvasContext
from composition_grid.canvas.doc_view_mapper import (
    ViewState,
    doc_to_widget_affine,
    view_scale,
    widget_to_doc_affine,
)
from composition_grid.canvas.overlay import GridOverlay
from composition_grid.core.geometry import Bounds
from composition_grid.core.grids import default_params, get
from composition_grid.core.styles import Style
from composition_grid.core.transform_state import TransformState
from composition_grid.model.grid_model import GridModel, GridStateService
from composition_grid.presentation.handle_layout import handle_positions
from composition_grid.ui.transform_controller import TransformController


def _qt_available() -> bool:
    """Non-raising probe mirroring ``try_import_qt`` order (PyQt6 first);
    probes the ``QtWidgets`` SUBMODULE so a half-removed install reads as
    unavailable (same pattern as the sibling Qt test modules)."""
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
    submodules all import; ``None`` without PyQt."""
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

_BG = (0, 0, 255, 255)  # solid blue: grid ink and white handles both differ

# Fixture geometry: document 200x200 shown 1:1 in widget pixels (identity
# doc->widget, view scale 1). The element is 100x60 centered on (100, 100),
# so its doc/widget rect is x 50..150, y 70..130. Rule of thirds then lands
# verticals at widget x = 83 1/3 / 116 2/3 and horizontals at y = 90 / 110;
# the nw corner anchor (handle target) is (50, 70).
_VIEW = ViewState(
    document_rect_widget=Bounds(0.0, 0.0, 200.0, 200.0),
    rotation_deg=0.0,
    mirror_x=False,
    mirror_y=False,
    doc_width=200.0,
    doc_height=200.0,
)


@pytest.fixture(autouse=True)
def _qt_gate():
    """Per-test gate through the shared conftest helper."""
    try_import_qt()
    yield


@pytest.fixture(autouse=True)
def _no_filter_outlives_the_test(monkeypatch):
    """Test hygiene for the Paint trigger: viewport event filters installed
    during a test are REMOVED at teardown even when the test dropped the
    context without unbinding. Without this, the filter wrapper dies while the
    C++ viewport still holds it; a later Qt event then revives a
    wrapper-without-``__init__`` and the (caught, logged) trigger raises
    ``AttributeError`` into the log of whichever test follows. Production is
    unaffected — the Extension keeps the context alive and always unbinds."""
    from composition_grid.canvas import viewport_triggers as _vt

    installed: list = []
    real_install = _vt.install

    def _spy(widget, on_trigger, event_types=None):
        handle = real_install(widget, on_trigger, event_types=event_types)
        if handle is not None:
            installed.append(handle)
        return handle

    monkeypatch.setattr(_vt, "install", _spy)
    yield
    while installed:
        _vt.remove(installed.pop())


_KEEP_APP_REF = None


def _ensure_qapp():
    """Create the shared QApplication singleton once (offscreen platform).

    PyQt6 GOTCHA (pinned by the sibling Qt modules): the wrapper must be kept
    referenced for the whole test lifetime or Qt tears the C++ app down
    mid-suite."""
    global _KEEP_APP_REF
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        _KEEP_APP_REF = QtWidgets.QApplication([])
    elif _KEEP_APP_REF is None:
        _KEEP_APP_REF = inst


def _enum_member(scope: str, name: str):
    """Qt enum member with scoped (PyQt6) first, flat (PyQt5) fallback —
    the same resolution convention as ``qt_compat._pick``, test-side.
    Lazy: called only inside tests, never at module import."""
    base = QtCore.Qt
    scoped = getattr(base, scope, None)
    member = getattr(scoped, name, None) if scoped is not None else None
    if member is None:
        member = getattr(base, name)
    return member


def _mouse_event(kind, x: float, y: float, left: bool = False, holding: bool = False):
    """Synthetic widget-local QMouseEvent at ``(x, y)`` with the given
    button state; modifiers are always empty (shift behaviour is pinned in
    the controller's own pure tests)."""
    button = _enum_member("MouseButton", "LeftButton") if left else _enum_member(
        "MouseButton", "NoButton"
    )
    buttons = _enum_member("MouseButton", "LeftButton") if holding else _enum_member(
        "MouseButton", "NoButton"
    )
    return QtGui.QMouseEvent(
        kind,
        QtCore.QPointF(x, y),
        button,
        buttons,
        _enum_member("KeyboardModifier", "NoModifier"),
    )


def _model(visible=True, edit_session=False, grid_id="rule_of_thirds", label="1"):
    return GridModel(
        grid_type_id=grid_id,
        params=default_params(get(grid_id)),
        style=Style(),
        transform=TransformState(cx=100.0, cy=100.0, width=100.0, height=60.0),
        visible=visible,
        edit_session=edit_session,
        grid_id=f"g{label}",
        label=label,
    )


def _one_grid(service, doc_id: str, doc_w: float, doc_h: float):
    """The document of the single-grid tests: a routed container holding ONE
    grid. ``ensure`` alone creates an EMPTY container now (a fresh document has
    no grid), so these tests ask for the grid explicitly."""
    service.ensure(doc_id, doc_w, doc_h)
    service.set_active(doc_id)
    service.add_grid()
    return service.selected(doc_id)


def _make_overlay(model=None, grids=None, **kwargs):
    """Parentless overlay + explicit providers (the paint seam tests draw into
    a QImage, so no real parent widget is needed).

    ``model`` is the SELECTED grid (the handles + mouse route); ``grids`` is the
    document's list the overlay PAINTS — omitted it is the selected grid alone,
    which is what every single-grid test in this file means."""
    _ensure_qapp()
    if grids is None:
        grids = () if model is None else (model,)
    return GridOverlay(
        None,
        lambda: model,
        kwargs.pop("view_state_provider", lambda: _VIEW),
        grids_provider=lambda: tuple(grids),
        **kwargs,
    )


def _paint_to_image(overlay):
    """Run the overlay's real paint code onto a background-filled QImage."""
    img = QtGui.QImage(200, 200, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtGui.QColor(*_BG))
    qp = QtGui.QPainter(img)
    try:
        overlay.paint_grid(qp)
    finally:
        qp.end()
    return img


def _is_bg(color) -> bool:
    return (color.red(), color.green(), color.blue(), color.alpha()) == _BG


def _changed_points(img, x0, x1, y0, y1):
    return [
        (x, y)
        for x in range(x0, x1)
        for y in range(y0, y1)
        if not _is_bg(img.pixelColor(x, y))
    ]


# --- painting ----------------------------------------------------------------


def test_paint_without_model_is_noop():
    """With no model (and with ``visible`` False) the overlay paints
    a blank image — every pixel stays exactly the background fill."""
    overlay = _make_overlay(None)
    img = _paint_to_image(overlay)
    assert not _changed_points(img, 0, img.width(), 0, img.height()), (
        "no-model paint must not touch a single pixel"
    )

    hidden = _model(visible=False)
    overlay2 = _make_overlay(hidden)
    img2 = _paint_to_image(overlay2)
    assert not _changed_points(img2, 0, img2.width(), 0, img2.height()), (
        "visible=False must not paint either"
    )


def test_a_document_without_grids_paints_nothing():
    """The multi-grid rule: an empty container (the artist removed every grid)
    paints a blank image — there is no "removed but remembered" state."""
    overlay = _make_overlay(None, grids=())
    img = _paint_to_image(overlay)
    assert not _changed_points(img, 0, img.width(), 0, img.height()), (
        "a document with no grids must not touch a single pixel"
    )


def test_a_document_without_a_selection_takes_no_mouse_input():
    """The interaction route is the SELECTED grid: with no selection there is
    nothing to drag, so the press passes through to the canvas — even when the
    document still has grids to paint."""
    painted = _model()
    overlay = _make_overlay(None, grids=(painted,))
    press = _mouse_event(qt_compat.MousePress, 50.0, 70.0, left=True, holding=True)
    overlay.mousePressEvent(press)
    assert not press.isAccepted(), "no selection must not swallow the click"


def test_grid_lines_appear_for_simple_model():
    """Rule-of-thirds geometry lands where the rule puts the thirds: verticals
    at x = 83 1/3 and 116 2/3, horizontals at y = 90 and 110 (bands +-2 px);
    nothing paints outside the element rect (bounds contract at widget level)."""
    model = _model()
    overlay = _make_overlay(model)
    img = _paint_to_image(overlay)

    vertical = _changed_points(img, 81, 86, 71, 130)
    assert len(vertical) >= 50, "first vertical third missing"
    vertical2 = _changed_points(img, 114, 119, 71, 130)
    assert len(vertical2) >= 50, "second vertical third missing"
    horizontal = _changed_points(img, 51, 150, 88, 93)
    assert len(horizontal) >= 80, "first horizontal third missing"
    stray = _changed_points(img, 0, 200, 0, 60)
    assert not stray, f"grid leaked outside the element rect: {stray[:5]}"


def test_edit_session_paints_handles_outside_session_paints_none():
    """In an edit session the corner marker (white fill) lands exactly on the
    nw handle anchor; outside the session that pixel stays background."""
    edit_model = _model(edit_session=True)
    overlay = _make_overlay(edit_model)
    img = _paint_to_image(overlay)
    nw = handle_positions(_VIEW, edit_model.transform)["nw"]
    color = img.pixelColor(int(round(nw[0])), int(round(nw[1])))
    assert (color.red(), color.green(), color.blue()) == (255, 255, 255), (
        "edit session must paint the white-filled corner marker"
    )

    plain_model = _model(edit_session=False)
    overlay2 = _make_overlay(plain_model)
    img2 = _paint_to_image(overlay2)
    assert _is_bg(img2.pixelColor(int(round(nw[0])), int(round(nw[1])))), (
        "no markers outside the edit session"
    )


def test_every_visible_grid_paints_and_only_the_selected_one_has_handles():
    """The multi-grid paint contract: the overlay draws EVERY visible grid of the
    document, in list order and each with its own geometry, while the marker set
    belongs to the SELECTED grid in an edit session alone."""
    first = _model(grid_id="uniform_grid", label="1")
    first.transform = TransformState(cx=40.0, cy=100.0, width=60.0, height=100.0)
    selected = _model(grid_id="center_cross", label="2", edit_session=True)
    selected.transform = TransformState(cx=160.0, cy=100.0, width=60.0, height=100.0)
    hidden = _model(grid_id="rule_of_thirds", label="3", visible=False)
    hidden.transform = TransformState(cx=100.0, cy=20.0, width=60.0, height=30.0)

    overlay = _make_overlay(selected, grids=(first, selected, hidden))
    img = _paint_to_image(overlay)

    assert _changed_points(img, 11, 70, 50, 151), "the unselected grid is missing"
    assert _changed_points(img, 131, 190, 50, 151), "the selected grid is missing"
    assert not _changed_points(img, 71, 130, 6, 35), (
        "a hidden grid paints nothing, whatever its place in the list"
    )

    corner = handle_positions(_VIEW, selected.transform)["nw"]
    color = img.pixelColor(int(round(corner[0])), int(round(corner[1])))
    assert (color.red(), color.green(), color.blue()) == (255, 255, 255), (
        "the selected grid's corner marker is painted"
    )
    other_corner = handle_positions(_VIEW, first.transform)["nw"]
    assert _is_bg(img.pixelColor(int(round(other_corner[0])), int(round(other_corner[1])))), (
        "an unselected grid gets no markers"
    )


def test_a_press_on_an_unselected_grid_never_moves_it():
    """Interaction follows the selection: pressing where an UNSELECTED grid's
    handle would be is a MISS for the overlay — the other grid keeps its
    geometry and the canvas gets the event back (the miss itself, and the
    session end it triggers through the injected hook, is pinned by
    ``test_press_without_hit_ends_edit_session_and_passes_event``)."""
    first = _model(grid_id="uniform_grid", label="1")
    first.transform = TransformState(cx=40.0, cy=100.0, width=60.0, height=100.0)
    selected = _model(grid_id="center_cross", label="2", edit_session=True)
    selected.transform = TransformState(cx=160.0, cy=100.0, width=60.0, height=100.0)

    overlay = _make_overlay(selected, grids=(first, selected))
    anchor = handle_positions(_VIEW, first.transform)["move"]
    press = _mouse_event(qt_compat.MousePress, anchor[0], anchor[1], left=True, holding=True)
    overlay.mousePressEvent(press)

    assert first.transform == TransformState(
        cx=40.0, cy=100.0, width=60.0, height=100.0
    ), "the unselected grid is never a drag target"
    assert not press.isAccepted(), "and the event reaches the canvas"


# --- input transparency ------------------------------------------------------


def test_transparent_for_mouse_events_outside_edit_session():
    """``edit_session`` False -> WA_TransparentForMouseEvents True;
    ``edit_session`` True -> the attribute is cleared (the overlay becomes a
    mouse target only inside a session)."""
    model = _model(edit_session=False)
    overlay = _make_overlay(model)
    overlay.refresh()
    assert overlay.testAttribute(qt_compat.WA_TransparentForMouseEvents) is True

    model.edit_session = True
    overlay.refresh()
    assert overlay.testAttribute(qt_compat.WA_TransparentForMouseEvents) is False


# --- mouse protocol (edit session only) ---------------------------------------


def test_press_on_handle_drags_the_model_transform():
    """Press on the nw corner starts a controller drag; the move resizes the
    element with ratio lock (100x60 stays 5:3) while the rendered opposite
    (se) corner (150, 130) stays fixed; the release ends the drag, so later
    moves are hover-only and no longer mutate the model."""
    model = _model(edit_session=True)
    overlay = _make_overlay(model)  # default controller: TransformController
    press = _mouse_event(qt_compat.MousePress, 50.0, 70.0, left=True, holding=True)
    overlay.mousePressEvent(press)
    assert press.isAccepted(), "a press that hits a handle must be accepted"

    move = _mouse_event(qt_compat.MouseMove, 30.0, 50.0, holding=True)
    overlay.mouseMoveEvent(move)
    ts = model.transform
    assert ts.width == pytest.approx(120.0, abs=0.01)
    assert ts.height == pytest.approx(72.0, abs=0.01)  # 120 / (100 / 60)
    assert ts.cx + ts.width / 2.0 == pytest.approx(150.0, abs=0.01)
    assert ts.cy + ts.height / 2.0 == pytest.approx(130.0, abs=0.01)

    release = _mouse_event(qt_compat.MouseRelease, 30.0, 50.0, left=True)
    overlay.mouseReleaseEvent(release)
    assert release.isAccepted()

    before = (model.transform.cx, model.transform.width)
    overlay.mouseMoveEvent(_mouse_event(qt_compat.MouseMove, 10.0, 10.0))
    assert (model.transform.cx, model.transform.width) == before


def test_press_inside_element_moves_it():
    """Drag-anywhere fix: a press strictly INSIDE the element quad (far from
    every handle point) starts a move drag; a subsequent move translates the
    model transform centre by the doc-space delta, leaving the size untouched,
    and the session stays open. Hovering the same interior point sets the
    size_all move cursor."""
    model = _model(edit_session=True)
    overlay = _make_overlay(model)
    # element doc/widget rect is (50,70)..(150,130); (90, 90) is inside it and
    # more than MOUSE_TOLERANCE_PX from the move centre (100, 100).
    inside = (90.0, 90.0)

    hover = _mouse_event(qt_compat.MouseMove, inside[0], inside[1])
    overlay.mouseMoveEvent(hover)
    assert overlay.cursor().shape() == qt_compat.SizeAllCursor, (
        "an interior hover must show the move cursor"
    )

    press = _mouse_event(qt_compat.MousePress, inside[0], inside[1], left=True, holding=True)
    overlay.mousePressEvent(press)
    assert press.isAccepted(), "a press inside the element must start a drag, not end the session"
    assert model.edit_session is True, "an interior hit is not a miss-click"

    before = model.transform
    assert (before.cx, before.cy, before.width, before.height) == pytest.approx(
        (100.0, 100.0, 100.0, 60.0)
    )

    overlay.mouseMoveEvent(_mouse_event(qt_compat.MouseMove, 110.0, 110.0, holding=True))
    ts = model.transform
    # view scale is 1 here: the +20,+20 widget delta is a +20,+20 doc delta
    assert (ts.cx, ts.cy) == pytest.approx((120.0, 120.0))
    assert (ts.width, ts.height) == pytest.approx((100.0, 60.0)), "a move drag must not resize"

    overlay.mouseReleaseEvent(_mouse_event(qt_compat.MouseRelease, 110.0, 110.0, left=True))


def test_non_left_release_never_ends_or_consumes_a_drag():
    """Right/Middle releases during a drag pass through; only the left release ends it."""
    model = _model(edit_session=True)
    ends = []

    class SpyController(TransformController):
        def end_drag(self):
            ends.append(1)
            super().end_drag()

    overlay = _make_overlay(model, controller=SpyController(lambda: _VIEW, lambda: model))
    press = _mouse_event(qt_compat.MousePress, 50.0, 70.0, left=True, holding=True)
    overlay.mousePressEvent(press)
    assert press.isAccepted()

    repaints = []
    overlay.request_paint = lambda: repaints.append(1)  # type: ignore[assignment]
    for button in ("RightButton", "MiddleButton"):
        release = QtGui.QMouseEvent(
            qt_compat.MouseRelease,
            QtCore.QPointF(50.0, 70.0),
            _enum_member("MouseButton", button),
            _enum_member("MouseButton", "LeftButton"),
            _enum_member("KeyboardModifier", "NoModifier"),
        )
        release.accept()
        overlay.mouseReleaseEvent(release)
        assert not release.isAccepted(), f"{button} release must not be consumed"

    assert not ends and not repaints, "unrelated releases must not end the drag or repaint"
    overlay.mouseMoveEvent(_mouse_event(qt_compat.MouseMove, 30.0, 50.0, holding=True))
    assert model.transform.width == pytest.approx(120.0, abs=0.01), (
        "the drag must survive unrelated releases and keep resizing the grid"
    )

    left_release = _mouse_event(qt_compat.MouseRelease, 30.0, 50.0, left=True)
    overlay.mouseReleaseEvent(left_release)
    assert left_release.isAccepted(), "the left release ends and consumes the drag"
    assert ends == [1] and repaints == [1]

    frozen = (model.transform.cx, model.transform.width)
    overlay.mouseMoveEvent(_mouse_event(qt_compat.MouseMove, 10.0, 10.0, holding=True))
    assert (model.transform.cx, model.transform.width) == frozen


def test_press_without_hit_ends_edit_session_and_passes_event():
    """What THIS test proves (handler-level, direct call with a synthetic
    event): a session press that misses every handle ENDS the session
    (through the injected service hook) and leaves the event NOT accepted —
    i.e. the HANDLER does not swallow it (click outside the
    markers completes the session). That Qt then propagates the same ignored
    press up to the parent canvas widget, and that its implicit mouse grab
    keeps a mid-drag continuation delivered to the overlay, are documented
    Qt behaviour verified only by a manual check in
    Krita."""
    service = GridStateService()
    model = _one_grid(service, "doc", 200.0, 200.0)
    service.start_edit_session()
    assert model.edit_session is True

    overlay = _make_overlay(model, end_edit_session=service.end_edit_session)
    press = _mouse_event(qt_compat.MousePress, 500.0, 500.0, left=True, holding=True)
    overlay.mousePressEvent(press)
    assert model.edit_session is False, "miss-click must end the edit session"
    assert not press.isAccepted(), "the miss must not be swallowed by the overlay"


def test_outside_session_press_is_never_consumed():
    """Handler-level pin (direct call, like the test above — NOT a live
    Qt-routing proof): even if a press reaches the widget outside a session
    the HANDLER ignores it — the overlay never consumes mouse input outside
    the edit session (in live Qt the transparency attribute already blocks
    delivery; end-to-end routing is a manual check in Krita)."""
    model = _model(edit_session=False)
    overlay = _make_overlay(model)
    press = _mouse_event(qt_compat.MousePress, 50.0, 70.0, left=True, holding=True)
    overlay.mousePressEvent(press)
    assert not press.isAccepted()


def test_move_updates_hover_and_cursor():
    """Hover over the rotate anchor sets the cross cursor (``cursor_shape``
    name mapped onto the qt_compat scalar); hovering empty space resets to
    the arrow cursor."""
    model = _model(edit_session=True)
    overlay = _make_overlay(model)
    rotate_pos = handle_positions(_VIEW, model.transform)["rotate"]
    overlay.mouseMoveEvent(_mouse_event(qt_compat.MouseMove, rotate_pos[0], rotate_pos[1]))
    assert overlay.cursor().shape() == qt_compat.CrossCursor

    overlay.mouseMoveEvent(_mouse_event(qt_compat.MouseMove, 500.0, 500.0))
    assert overlay.cursor().shape() == qt_compat.ArrowCursor


def test_controller_receives_the_drag_protocol():
    """DI seam: with a recording controller injected, press-on-handle routes
    ``begin_drag(handle, point, shift)`` and release routes ``end_drag`` —
    the overlay delegates all transform math, it owns none."""
    calls = []

    class RecordingController(TransformController):
        def begin_drag(self, handle, point, shift_held):
            calls.append(("begin", handle, point))
            return True

        def drag_to(self, point, shift_held):
            calls.append(("move", point))
            return True

        def end_drag(self):
            calls.append(("end",))

    model = _model(edit_session=True)
    controller = RecordingController(lambda: _VIEW, lambda: model)
    overlay = _make_overlay(model, controller=controller)
    nw = handle_positions(_VIEW, model.transform)["nw"]
    overlay.mousePressEvent(
        _mouse_event(qt_compat.MousePress, nw[0], nw[1], left=True, holding=True)
    )
    overlay.mouseMoveEvent(_mouse_event(qt_compat.MouseMove, nw[0] - 10, nw[1] - 10, holding=True))
    overlay.mouseReleaseEvent(_mouse_event(qt_compat.MouseRelease, nw[0], nw[1], left=True))
    assert calls[0] == ("begin", "nw", (pytest.approx(nw[0]), pytest.approx(nw[1])))
    assert calls[1][0] == "move"
    assert calls[-1] == ("end",)
    assert model.transform.width == 100.0  # recording controller mutates nothing


# --- geometry sync (eventFilter) -----------------------------------------------


def test_resize_event_regeometrys_overlay():
    """The overlay tracks the canvas widget rect: the constructor syncs it and
    a Resize event on the parent re-runs ``setGeometry(parent.rect())``."""
    _ensure_qapp()
    canvas = QtWidgets.QWidget()
    canvas.resize(400, 300)
    overlay = GridOverlay(canvas, lambda: None, lambda: None)
    assert overlay.geometry() == canvas.rect()

    canvas.resize(500, 380)
    event = QtGui.QResizeEvent(QtCore.QSize(500, 380), QtCore.QSize(400, 300))
    QtCore.QCoreApplication.sendEvent(canvas, event)
    assert overlay.geometry() == canvas.rect(), (
        "Resize must re-geometry the overlay to the canvas rect"
    )


def test_event_filter_never_consumes_events():
    """The overlay's filter on the canvas widget passes everything through
    (returns False) — it only SYNCS state, it never eats Krita's events."""
    _ensure_qapp()
    canvas = QtWidgets.QWidget()
    canvas.resize(120, 90)
    overlay = GridOverlay(canvas, lambda: None, lambda: None)
    user_event = QtCore.QEvent(QtCore.QEvent.Type.User)
    assert overlay.eventFilter(canvas, user_event) is False
    mouse = _mouse_event(qt_compat.MousePress, 10.0, 10.0, left=True, holding=True)
    assert overlay.eventFilter(canvas, mouse) is False


# --- CanvasContext: fake canvas signals + recording overlay ---------------------


_FAKE_CANVAS_CLS = None


def _fake_canvas_cls():
    """Build the fake libkis Canvas class ON FIRST USE, not at module import.

    WHY a factory and not a module-level ``class``: the ``pytestmark`` skipif
    makes the whole module SKIP when PyQt is absent, but a class statement
    still RUNS at import — ``class FakeCanvas(QtCore.QObject)`` dereferences
    ``QtCore``, which is ``None`` in a pure environment, so collection raised
    AttributeError and INTERRUPTED the entire run. That violates the repo rule
    "Qt-тесты выполняются только если qt_compat импортировалась, иначе
    pytest.skip". Deferring the definition means no
    Qt value is touched unless a test actually executes (PyQt present)."""
    global _FAKE_CANVAS_CLS
    if _FAKE_CANVAS_CLS is not None:
        return _FAKE_CANVAS_CLS

    class FakeCanvas(QtCore.QObject):
        """Duck-typed stand-in for the libkis Canvas wrapper ON KRITA 6.x: real
        pyqtSignals with the documented payload shapes plus the view-state
        getters.

        The shape is the point: it exposes NO
        ``width()``/``height()``. ``libs/libkis/Canvas.h`` has no document-size
        getter at all — the wrapper's only size member is ``sizeChanged(QSize)``,
        documented as "the size in widget pixels", i.e. the VIEWPORT. The old
        fake invented ``width()``/``height()`` and the context happily read
        them, which hid both the missing source and the unit confusion.
        ``widget_w``/``widget_h`` below are the document rect in WIDGET px
        (viewport space); the document extent belongs to :class:`FakeDocument`.

        HOST-CONSISTENCY ADAPTATION (the fake was the one that was
        wrong, not the context): on a real Canvas the signal and the getter
        describe the SAME state — when ``rotationChanged(45)`` is emitted,
        ``rotation()`` answers 45 and ``documentRectInWidgetPixels()`` answers
        the rect that was just announced. The earlier fake announced values
        through its signals that its getters never reflected (they were frozen
        at the constructor numbers), which only ever worked because the context
        stored the payloads. The ViewState is now COMPUTED from the getters, so
        the fake keeps its own getters in step with its own emissions — a fake
        may not be LESS self-consistent than the machine it stands in for.
        ``sizeChanged`` is deliberately NOT stored anywhere: its payload is the
        viewport, never the document extent."""

        stateChanged = QtCore.pyqtSignal()
        documentOffsetChanged = QtCore.pyqtSignal(object, object)
        effectiveZoomChanged = QtCore.pyqtSignal(float)
        rotationChanged = QtCore.pyqtSignal(float)
        mirrorChanged = QtCore.pyqtSignal(bool, bool)
        sizeChanged = QtCore.pyqtSignal(object)
        documentRectInWidgetPixelsChanged = QtCore.pyqtSignal(object)
        removed = QtCore.pyqtSignal()

        def __init__(self, widget_w=800.0, widget_h=600.0):
            super().__init__()
            self._widget_w = widget_w
            self._widget_h = widget_h
            self._rect = QtCore.QRectF(0.0, 0.0, widget_w, widget_h)
            self._rotation = 0.0
            self._mirror_x = False
            self._mirror_y = False
            self.documentRectInWidgetPixelsChanged.connect(self._host_store_rect)
            self.rotationChanged.connect(self._host_store_rotation)
            self.mirrorChanged.connect(self._host_store_mirror)

        def _host_store_rect(self, rect):
            self._rect = rect

        def _host_store_rotation(self, value):
            self._rotation = float(value)

        def _host_store_mirror(self, horizontal, vertical):
            self._mirror_x = bool(horizontal)
            self._mirror_y = bool(vertical)

        def rotation(self):
            return self._rotation

        def mirror(self):
            return {"horizontal": self._mirror_x, "vertical": self._mirror_y}

        def documentRectInWidgetPixels(self):
            return self._rect

    _FAKE_CANVAS_CLS = FakeCanvas
    return FakeCanvas


def FakeCanvas(widget_w=800.0, widget_h=600.0):  # noqa: N802 — call sites keep class-style construction
    """Factory preserving the original ``FakeCanvas(...)`` call sites (the two
    numbers are the document rect in WIDGET px, not the document size)."""
    return _fake_canvas_cls()(widget_w=widget_w, widget_h=widget_h)


class FakeDocument:
    """Duck-typed libkis ``Document``: the documented ``width()``/``height()``
    — "the width and height of the image in pixels" — i.e. the only true
    DOCUMENT-px source in libkis, reached through ``View.document()``, plus
    the host's ``name()``/``fileName()`` pair — the same
    identity getters ``plugin_support.document_key`` routes a context's model
    by. Reads only: the fake exposes no mutation API whatsoever, so a
    context that tried to write the document could not even be built."""

    def __init__(self, doc_w=800.0, doc_h=600.0, name="untitled-1"):
        self._doc_w = doc_w
        self._doc_h = doc_h
        self._name = name

    def name(self):
        return self._name

    def fileName(self):
        # the host fills fileName() too; the key is name() or fileName()
        return self._name

    def width(self):
        return self._doc_w

    def height(self):
        return self._doc_h


class FakeView:
    """Duck-typed libkis ``View``: ``canvas()`` (the payload of
    ``Window.activeViewChanged`` per libkis) plus
    ``document()`` — the route the document size is READ through."""

    def __init__(self, canvas, document=None):
        self._canvas = canvas
        self._document = document

    def canvas(self):
        return self._canvas

    def document(self):
        return self._document


class RecordingOverlay:
    """Stands in for GridOverlay inside the context: records the contract
    calls the context is allowed to make on its overlay."""

    def __init__(
        self, parent, model_provider, view_state_provider, end_edit_session,
        grids_provider=None,
    ):
        self.parent = parent
        self.model_provider = model_provider
        self.view_state_provider = view_state_provider
        self.end_edit_session = end_edit_session
        self.grids_provider = grids_provider
        self.repaints = 0
        self.refreshes = 0
        self.hides = 0
        self.disposed = False

    def request_paint(self):
        self.repaints += 1

    def refresh(self):
        self.refreshes += 1

    def hide(self):
        self.hides += 1

    def dispose(self):
        self.disposed = True


def _mdi_window(track=None):
    """Krita-shaped QMainWindow (MDI area + scroll-area viewport); returns (window, viewport)."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    mdi = QtWidgets.QMdiArea()
    win.setCentralWidget(mdi)
    sub = QtWidgets.QMdiSubWindow()
    scroll = QtWidgets.QTextEdit()
    sub.setWidget(scroll)
    mdi.addSubWindow(sub)
    mdi.setActiveSubWindow(sub)
    win.show()
    if track is not None:
        track.append(win)
    return win, scroll.viewport()


def _bound_context(
    service=None,
    doc_w=800.0,
    doc_h=600.0,
    widget_w=800.0,
    widget_h=600.0,
    doc_name="untitled-1",
    track=None,
    document=None,
):
    """Recording-overlay context bound to a window/canvas/view; ``document`` overrides the fake."""
    _ensure_qapp()
    service = service if service is not None else GridStateService()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    win, viewport = _mdi_window(track)
    canvas = FakeCanvas(widget_w, widget_h)
    view = FakeView(canvas, document or FakeDocument(doc_w, doc_h, name=doc_name))
    context = CanvasContext(service, overlay_factory=factory)
    if track is not None:
        track.append(context)
    assert context.bind(win, view, canvas) is True
    return context, canvas, win, viewport, created


def _bound_real_window(
    service, name, doc_w, doc_h, widget_w, widget_h, track=None, document=None
):
    """Real context + production GridOverlay on its own named document (``document`` overrides)."""
    _ensure_qapp()
    win, viewport = _mdi_window(track)
    canvas = FakeCanvas(widget_w, widget_h)
    view = FakeView(canvas, document or FakeDocument(doc_w, doc_h, name=name))
    context = CanvasContext(service)
    if track is not None:
        track.append(context)
    assert context.bind(win, view, canvas) is True
    return context, win, viewport


def _close(win):
    win.close()


def _destroy_windows(things):
    """Unbind every tracked context and delete every tracked window, verifying destroyed."""
    contexts = [t for t in things if isinstance(t, CanvasContext)]
    windows = [t for t in things if isinstance(t, QtWidgets.QMainWindow)]
    for context in contexts:
        context.unbind()
    deferred = getattr(
        getattr(QtCore.QEvent, "Type", QtCore.QEvent), "DeferredDelete"
    )
    destroyed = []
    expected = len(windows)
    for win in windows:
        win.destroyed.connect(lambda *a, w=win: destroyed.append(w))
        win.close()
        win.setParent(None)
        win.deleteLater()
    del contexts, windows
    gc.collect()
    QtCore.QCoreApplication.sendPostedEvents(None, deferred)
    gc.collect()
    assert len(destroyed) == expected, (
        f"every owned window must emit destroyed: {len(destroyed)}/{expected}"
    )


@pytest.fixture()
def ownership_cleanup():
    """Finalizer: unbind all tracked contexts, delete all tracked windows."""
    tracked: list = []
    yield tracked
    _destroy_windows(tracked)


def test_bind_creates_overlay_and_initial_view_state():
    """bind() parents the overlay to the located canvas widget and performs
    ONE initial getter read (an initial sync, not timer polling): the
    ViewState is built from the canvas rect getter (widget px) plus the
    document extent read through ``view.document()`` (document px)."""
    context, canvas, win, viewport, created = _bound_context()
    try:
        assert len(created) == 1
        overlay = created[0]
        assert overlay.parent is viewport
        assert context.widget is viewport
        assert context.view_state == ViewState(
            document_rect_widget=Bounds(0.0, 0.0, 800.0, 600.0),
            rotation_deg=0.0,
            mirror_x=False,
            mirror_y=False,
            doc_width=800.0,
            doc_height=600.0,
        )
    finally:
        _close(win)


def test_view_signals_rebuild_state_and_request_paint():
    """Every view signal repaints and rebuilds the latest ViewState: the rect
    payload wins over the getter seed; rotation/mirror update their fields;
    zoom/offset/state repaint without touching the fields — and
    ``sizeChanged`` (the VIEWPORT, widget px) repaints ONLY: the document
    extent belongs to the Document."""
    context, canvas, win, viewport, created = _bound_context()
    overlay = created[0]
    try:
        canvas.documentRectInWidgetPixelsChanged.emit(
            QtCore.QRectF(10.0, 20.0, 300.0, 200.0)
        )
        assert context.view_state.document_rect_widget == Bounds(10.0, 20.0, 300.0, 200.0)
        canvas.rotationChanged.emit(45.0)
        assert context.view_state.rotation_deg == 45.0
        canvas.mirrorChanged.emit(True, False)
        assert context.view_state.mirror_x is True
        assert context.view_state.mirror_y is False

        repaints_before_size = overlay.repaints
        canvas.sizeChanged.emit(QtCore.QSize(1024, 768))
        assert overlay.repaints == repaints_before_size + 1, (
            "sizeChanged is a repaint source"
        )
        assert context.view_state.doc_width == 800.0, (
            "a widget-pixel sizeChanged payload must never become the document "
            "extent (it would make the mapper divide by viewport px)"
        )
        assert context.view_state.doc_height == 600.0
        assert context.doc_size == (800.0, 600.0)

        before = overlay.repaints
        canvas.effectiveZoomChanged.emit(2.0)
        assert overlay.repaints == before + 1
        assert context.view_state.document_rect_widget == Bounds(10.0, 20.0, 300.0, 200.0)
        canvas.stateChanged.emit()
        assert overlay.repaints == before + 2
        canvas.documentOffsetChanged.emit(QtCore.QPointF(1.0, 2.0), QtCore.QPointF(3.0, 4.0))
        assert overlay.repaints == before + 3
    finally:
        _close(win)


def test_unbind_disconnects_and_disposes():
    """After unbind: the overlay is disposed, later signals neither rebuild
    the state nor repaint, and a double unbind is a safe no-op.

    NOTE — this assertion got STRICTER, not weaker: while the ViewState
    was stored in fields, an UNBOUND context kept answering the last rotation it
    had been handed (a context claiming a view state for a canvas it no longer
    had). The state is computed from the bound objects now, so the post-unbind
    answer is ``None``, and the emissions below must not change that."""
    context, canvas, win, viewport, created = _bound_context()
    overlay = created[0]
    try:
        assert context.view_state is not None, "bound: a ViewState is computable"
        context.unbind()
        assert overlay.disposed is True
        repaints_before = overlay.repaints
        canvas.rotationChanged.emit(90.0)
        canvas.documentRectInWidgetPixelsChanged.emit(QtCore.QRectF(1.0, 2.0, 3.0, 4.0))
        assert context.view_state is None, (
            "an unbound context must claim no view state at all"
        )
        assert overlay.repaints == repaints_before
        context.unbind()  # idempotent, no exception
        assert context.overlay is None
    finally:
        _close(win)


def test_removed_signal_hides_overlay_without_crashing():
    """Canvas ``removed`` (degradation): the overlay is hidden,
    nothing is raised outward, the context object stays usable."""
    context, canvas, win, viewport, created = _bound_context()
    overlay = created[0]
    try:
        canvas.removed.emit()
        assert overlay.hides == 1
        assert context.overlay is overlay  # hidden, not destroyed
    finally:
        _close(win)


def test_service_change_refreshes_overlay():
    """A grid-state change routes through the service subscription into
    ``overlay.refresh()`` (mouse-transparency + repaint sync on every
    mutation of the active model)."""
    service = GridStateService()
    context, canvas, win, viewport, created = _bound_context(service)
    overlay = created[0]
    try:
        _one_grid(service, "doc", 800.0, 600.0)
        assert overlay.refreshes >= 3  # ensure + add + set_active notifications
        refreshes_before = overlay.refreshes
        service.start_edit_session()
        assert overlay.refreshes == refreshes_before + 1
    finally:
        _close(win)


def test_model_provider_routes_the_contexts_own_document(ownership_cleanup):
    """Provider follows the context's own document key, never the global pointer."""
    service = GridStateService()
    context, canvas, win, viewport, created = _bound_context(
        service, doc_name="a", doc_w=200.0, doc_h=200.0, track=ownership_cleanup
    )
    overlay = created[0]
    model_a = _one_grid(service, "a", 200.0, 200.0)
    model_b = _one_grid(service, "b", 200.0, 200.0)
    service.set_active("a")
    assert overlay.model_provider() is model_a
    service.set_active("b")
    assert overlay.model_provider() is model_a, (
        "the provider must never follow the globally active document"
    )
    assert overlay.model_provider() is not model_b
    assert overlay.view_state_provider() == context.view_state


def test_end_edit_session_hook_ends_the_own_document_only(ownership_cleanup):
    """The miss-click hook ends THIS context's document's session, not B's."""
    service = GridStateService()
    context, canvas, win, viewport, created = _bound_context(
        service, doc_name="own", doc_w=200.0, doc_h=200.0, track=ownership_cleanup
    )
    overlay = created[0]
    own = _one_grid(service, "own", 200.0, 200.0)
    other = _one_grid(service, "other", 200.0, 200.0)
    service.start_edit_session()
    service.set_active("own")
    service.start_edit_session()
    overlay.end_edit_session()
    assert own.edit_session is False
    assert other.edit_session is True, (
        "window A's miss must not end globally active window B's session"
    )


def test_bind_without_canvas_widget_degrades_quietly(caplog):
    """No resolvable canvas widget: bind returns False, no
    overlay is created, view-state tracking still works and stays crash-free
    (a later rebind can attach). The degradation is logged, never silent."""
    _ensure_qapp()
    context = CanvasContext(GridStateService())
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(QtWidgets.QWidget())  # no QMdiArea, no scroll area
    win.show()
    canvas = FakeCanvas()
    view = FakeView(canvas, FakeDocument())
    try:
        with caplog.at_level("DEBUG", logger="composition_grid"):
            assert context.bind(win, view, canvas) is False
        assert context.overlay is None
        canvas.rotationChanged.emit(30.0)  # no overlay: must not raise
        assert context.view_state.rotation_deg == 30.0
        assert any("hidden" in r.getMessage() for r in caplog.records)
    finally:
        _close(win)


def test_absent_optional_signals_are_logged_not_raised(caplog):
    """Krita version drift: a canvas exposing NO signals at all binds
    cleanly; each absent optional signal logs a DEBUG line (asserted AT
    DEBUG level, so the test would not pass on silence) and nothing raises
    outward. The canvas is also shaped like the real one (NO size getters), so
    with no view either the ViewState degrades to ``None`` and the missing
    document-size source is itself a logged note."""

    class MinimalCanvas(QtCore.QObject):
        """The least libkis can give: a Canvas with no signals and no getters
        (``width()``/``height()`` are not part of the real wrapper either)."""

    _ensure_qapp()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    context = CanvasContext(GridStateService(), overlay_factory=factory)
    win, viewport = _mdi_window()
    try:
        with caplog.at_level("DEBUG", logger="composition_grid"):
            assert context.bind(win, None, MinimalCanvas()) is True
        debug_messages = [
            r.getMessage() for r in caplog.records if r.levelno == 10  # DEBUG
        ]
        assert any("rotationChanged" in m for m in debug_messages), debug_messages
        assert any("mirrorChanged" in m for m in debug_messages), debug_messages
        assert any("document size unknown" in m for m in debug_messages), debug_messages
        assert context.view_state is None  # no rect, no document -> no state
    finally:
        _close(win)


def test_active_view_changed_rebinds_overlay():
    """``resetCanvas`` widget swap / active-view change: a
    ``Window.activeViewChanged`` emission re-runs the full attach — the old
    overlay is disposed, a fresh one is attached to the re-located canvas
    widget, and the signal subscriptions drive the NEW overlay."""
    _ensure_qapp()

    class SignalWindow(QtWidgets.QMainWindow):
        activeViewChanged = QtCore.pyqtSignal(object)

    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    win = SignalWindow()
    mdi = QtWidgets.QMdiArea()
    win.setCentralWidget(mdi)
    sub = QtWidgets.QMdiSubWindow()
    scroll = QtWidgets.QTextEdit()
    sub.setWidget(scroll)
    mdi.addSubWindow(sub)
    mdi.setActiveSubWindow(sub)
    win.show()
    viewport = scroll.viewport()
    canvas = FakeCanvas()
    view = FakeView(canvas, FakeDocument())
    context = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert context.bind(win, view, canvas) is True
        first = created[0]
        win.activeViewChanged.emit(None)
        assert len(created) == 2, "activeViewChanged must rebind a fresh overlay"
        assert first.disposed is True
        assert created[1].parent is viewport  # re-attached to the canvas widget
        before = created[1].repaints
        canvas.rotationChanged.emit(20.0)
        assert context.view_state.rotation_deg == 20.0
        assert created[1].repaints == before + 1, "new overlay gets repaints"
    finally:
        _close(win)


# --- the document-size source -------------------------------------------------


def test_document_size_is_the_view_document_in_document_px():
    """The pin that would have caught the defect: a
    4000x3000 document shown in an 800x600 viewport. The canvas fake is shaped
    like the real libkis ``Canvas`` (no ``width()``/``height()`` at all, a
    widget-px document rect), so the only document-px source is the bound
    view's ``Document``. A fresh bind must yield a usable ``view_state`` whose
    ``doc_width`` is 4000 (NOT the 800 viewport number the old canvas getter /
    ``sizeChanged`` path handed out), the same for ``doc_size``, and the widget
    rect must stay viewport-space."""
    context, canvas, win, viewport, created = _bound_context(
        doc_w=4000.0, doc_h=3000.0, widget_w=800.0, widget_h=600.0
    )
    try:
        assert context.doc_size == (4000.0, 3000.0)
        state = context.view_state
        assert state is not None, "a fresh bind must be usable without any signal"
        assert state.doc_width == 4000.0
        assert state.doc_height == 3000.0
        assert state.document_rect_widget == Bounds(0.0, 0.0, 800.0, 600.0)
    finally:
        _close(win)


def test_size_changed_is_repaint_only_and_the_document_is_only_read():
    """``sizeChanged(QSize)`` (widget px) may never become the document extent:
    after a viewport-sized emission the doc fields and ``doc_size`` still
    describe the 4000x3000 document (the repaint count is pinned in
    :func:`test_view_signals_rebuild_state_and_request_paint`).

    In the same breath: the size route must stay a READ. The document
    doubles as a tripwire — every attribute access is recorded, and any name the
    fake does not define (i.e. any mutation API) raises — so a context that
    reached for one would be caught here instead of inside Krita."""
    _ensure_qapp()

    class TripwireDocument(FakeDocument):
        def __init__(self, doc_w=800.0, doc_h=600.0):
            super().__init__(doc_w, doc_h)
            self.accessed = []

        def width(self):
            self.accessed.append("width")
            return super().width()

        def height(self):
            self.accessed.append("height")
            return super().height()

        def __getattr__(self, item):
            # only reached for names the fake does NOT define
            self.accessed.append(item)
            raise AssertionError(f"non-read Document API touched: {item}")

    document = TripwireDocument(4000.0, 3000.0)
    canvas = FakeCanvas(800.0, 600.0)
    win, viewport = _mdi_window()
    context = CanvasContext(GridStateService())
    try:
        context.bind(win, FakeView(canvas, document), canvas)
        assert context.doc_size == (4000.0, 3000.0)
        canvas.sizeChanged.emit(QtCore.QSize(800, 600))
        assert context.doc_size == (4000.0, 3000.0), (
            "sizeChanged is the viewport: it must not feed the document extent"
        )
        assert context.view_state.doc_width == 4000.0
        canvas.documentRectInWidgetPixelsChanged.emit(QtCore.QRectF(0, 0, 800.0, 600.0))
        assert [name for name in document.accessed if name not in ("width", "height")] == []
    finally:
        _close(win)


def test_document_size_falls_back_to_the_windows_active_view(caplog):
    """The second branch: the context was bound with NO view
    (``bind(window, None, canvas)``, e.g. a docker ``canvasChanged`` that arrived
    while the active view was unreadable). The document size must still be
    reachable through ``window.activeView().document()``, and the branch that
    worked is logged at DEBUG."""
    _ensure_qapp()
    win, viewport = _mdi_window()
    canvas = FakeCanvas(500.0, 400.0)
    wrapper = FakeWindowWrapper(
        win, view=FakeView(canvas, FakeDocument(1600.0, 1200.0))
    )
    context = CanvasContext(GridStateService())
    try:
        with caplog.at_level("DEBUG", logger="composition_grid"):
            # a bare wrapper exposes no qwindow widget: bind degrades, tracking
            # still runs — and so does the document-size read
            context.bind(wrapper, None, canvas)
        assert context.doc_size == (1600.0, 1200.0), (
            "the window's own activeView() is the documented second source"
        )
        debug_messages = [r.getMessage() for r in caplog.records if r.levelno == 10]
        assert any("window-activeView" in m for m in debug_messages), debug_messages
    finally:
        _close(win)


def test_refresh_document_size_follows_a_document_switch():
    """A different document is routed onto an ALREADY bound context (no rebind:
    the docker's ``canvasChanged`` for the canvas the context already has). The
    extent stored at bind describes the PREVIOUS document, so the extension's
    refresh point must re-read it through the window's current active view."""
    _ensure_qapp()
    win, viewport = _mdi_window()
    canvas = FakeCanvas(500.0, 400.0)
    wrapper = FakeWindowWrapper(win, view=FakeView(canvas, FakeDocument(1600.0, 1200.0)))
    context = CanvasContext(GridStateService())
    try:
        context.bind(wrapper, wrapper.activeView(), canvas)
        assert context.doc_size == (1600.0, 1200.0)
        wrapper._view = FakeView(canvas, FakeDocument(900.0, 700.0))  # new document
        assert context.refresh_document_size() is True
        assert context.doc_size == (900.0, 700.0)
    finally:
        _close(win)


def test_document_switch_to_missing_then_other_never_keeps_the_stale_document(
    ownership_cleanup
):
    """Own-document contract on every refresh transition (see _read_doc_size)."""
    tracked = ownership_cleanup
    service = GridStateService()
    doc_a = FakeDocument(800.0, 600.0, name="doc-a")
    canvas = FakeCanvas(800.0, 600.0)
    view = FakeView(canvas, doc_a)
    win, _viewport = _mdi_window(tracked)
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    context = CanvasContext(service, overlay_factory=factory)
    tracked.append(context)
    assert context.bind(win, view, canvas) is True
    model_a = _one_grid(service, "doc-a", 800.0, 600.0)
    model_b = _one_grid(service, "doc-b", 800.0, 600.0)
    service.start_edit_session()
    service.set_active("doc-a")
    service.start_edit_session()
    assert context.model_provider() is model_a

    view._document = None
    # doc_size stays the documented last-known cache; ownership must still reset:
    context.refresh_document_size()
    assert context.model_provider() is None
    assert context.view_state is None
    assert context.doc_size == (800.0, 600.0)
    created[0].end_edit_session()
    assert model_a.edit_session is True, (
        "a keyless context must not end the active session"
    )
    assert model_b.edit_session is True, (
        "a keyless context must not end the foreign session either"
    )
    doc_b = FakeDocument(400.0, 300.0, name="doc-b")
    doc_b._doc_w = None
    doc_b._doc_h = None
    view._document = doc_b
    context.refresh_document_size()
    assert context.model_provider() is model_b
    assert context.view_state is None
    assert context.doc_size == (800.0, 600.0)

    created[0].end_edit_session()
    assert model_b.edit_session is False, (
        "B was routed even unreadable: its session belongs to this context"
    )
    assert model_a.edit_session is True, (
        "the active foreign session stays untouched"
    )

    doc_b._doc_w = 400.0
    doc_b._doc_h = 300.0
    assert context.refresh_document_size() is True
    assert context.doc_size == (400.0, 300.0)
    assert context.view_state is not None
    assert context.view_state.doc_width == 400.0


def test_unreadable_document_getter_degrades_to_none_size(caplog):
    """A ``Document`` whose getters raise (host mid-teardown) is a DEBUG note and
    an unusable size — never an exception out of bind, and never the previous
    number silently reused as if it were fresh."""

    class BrokenDocument:
        def width(self):
            raise RuntimeError("wrapper is gone")

        def height(self):
            raise RuntimeError("wrapper is gone")

    _ensure_qapp()
    win, viewport = _mdi_window()
    canvas = FakeCanvas()
    context = CanvasContext(GridStateService())
    try:
        with caplog.at_level("DEBUG", logger="composition_grid"):
            context.bind(win, FakeView(canvas, BrokenDocument()), canvas)
        assert context.doc_size is None
        assert context.view_state is None
        assert any("width()/height()" in m for m in caplog.messages)
    finally:
        _close(win)


# --- bind-time plausibility --------------------------------------------------


def test_bind_rejects_dock_impostor_and_logs_reason(caplog):
    """The lenient locator fallback on a window WITHOUT an MDI chain resolves
    the QDockWidget's scroll-area viewport (genuine impostor geometry, above
    the size floor): ``bind`` must degrade it exactly like the widget-is-None
    path — INFO with the ``QDockWidget`` reason, ``False``, NO overlay
    created — while the Canvas signal subscriptions keep tracking view state
    for a later rebind (degradation)."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(QtWidgets.QWidget())  # no QMdiArea, no central scroll
    dock = QtWidgets.QDockWidget()
    scroll = QtWidgets.QTextEdit()
    scroll.resize(300, 300)
    dock.setWidget(scroll)
    win.addDockWidget(
        _enum_member("DockWidgetArea", "RightDockWidgetArea"), dock
    )
    win.resize(900, 600)
    win.show()
    canvas = FakeCanvas()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    context = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert type(win.findChild(QtWidgets.QAbstractScrollArea)) is QtWidgets.QTextEdit
        with caplog.at_level("INFO", logger="composition_grid"):
            assert context.bind(win, FakeView(canvas, FakeDocument()), canvas) is False
        assert created == [], "no overlay may be parented to a dock impostor"
        assert context.overlay is None
        assert context.widget is None
        info_messages = [r.getMessage() for r in caplog.records if r.levelno == 20]
        assert any("QDockWidget" in m for m in info_messages), info_messages
        assert any("hidden" in m for m in info_messages), info_messages
        # degradation parity with the None path: tracking continues, no crash
        canvas.rotationChanged.emit(30.0)
        assert context.view_state.rotation_deg == 30.0
    finally:
        _close(win)


# --- fresh-object rebind on activeViewChanged ---------------------------------


class FakeWindowWrapper:
    """Duck-typed libkis ``Window`` wrapper: ``qwindow()`` + ``activeView()``."""

    def __init__(self, qwindow, view=None):
        self._qwindow = qwindow
        self._view = view

    def qwindow(self):
        return self._qwindow

    def activeView(self):
        return self._view


def _signal_mdi_window():
    """MDI window shaped like Krita that EXPOSES ``activeViewChanged`` (as
    libkis ``Window`` does); returns (window, canvas viewport)."""
    _ensure_qapp()

    class SignalWindow(QtWidgets.QMainWindow):
        activeViewChanged = QtCore.pyqtSignal(object)

    win = SignalWindow()
    mdi = QtWidgets.QMdiArea()
    win.setCentralWidget(mdi)
    sub = QtWidgets.QMdiSubWindow()
    scroll = QtWidgets.QTextEdit()
    sub.setWidget(scroll)
    mdi.addSubWindow(sub)
    mdi.setActiveSubWindow(sub)
    win.show()
    return win, scroll.viewport()


def test_active_view_changed_view_payload_reconnects_new_canvas(caplog):
    """Via the event path, honestly: the payload duck-types as the newly
    activated View, so the rebind takes the NEW canvas from it — the OLD
    canvas's signals no longer rebuild state or drive repaints, the new
    canvas's do, and the DEBUG log names the branch taken."""
    win, viewport = _signal_mdi_window()
    canvas_a = FakeCanvas()
    canvas_b = FakeCanvas(400.0, 300.0)
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    context = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert context.bind(win, FakeView(canvas_a, FakeDocument()), canvas_a) is True
        with caplog.at_level("DEBUG", logger="composition_grid"):
            win.activeViewChanged.emit(FakeView(canvas_b, FakeDocument(400.0, 300.0)))
        assert len(created) == 2, "view switch must rebind a fresh overlay"
        assert created[0].disposed is True
        assert created[1].parent is viewport
        debug_messages = [r.getMessage() for r in caplog.records if r.levelno == 10]
        assert any("payload-view" in m for m in debug_messages), debug_messages

        # stale canvas must be fully disconnected after the switch:
        canvas_a.rotationChanged.emit(99.0)
        assert context.view_state.rotation_deg == 0.0
        assert created[1].repaints == 0, "old canvas must not drive the new overlay"

        # the fresh canvas drives state + repaints:
        canvas_b.rotationChanged.emit(30.0)
        assert context.view_state.rotation_deg == 30.0
        assert created[1].repaints == 1
        # and the view state was re-seeded from ITS getters at the rebind:
        assert context.view_state.doc_width == 400.0
        assert context.view_state.document_rect_widget == Bounds(
            0.0, 0.0, 400.0, 300.0
        )
    finally:
        _close(win)


def test_active_view_changed_falls_back_to_window_active_view(caplog):
    """No view-like payload: the fresh canvas comes from the window's OWN
    ``activeView()`` (branch ``window-activeView``), not from the stale
    canvas stored at bind."""
    win, viewport = _signal_mdi_window()
    canvas_a = FakeCanvas()
    canvas_b = FakeCanvas(400.0, 300.0)
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    wrapper = FakeWindowWrapper(win, view=FakeView(canvas_b, FakeDocument(400.0, 300.0)))
    context = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert context.bind(wrapper, FakeView(canvas_a, FakeDocument()), canvas_a) is True
        with caplog.at_level("DEBUG", logger="composition_grid"):
            win.activeViewChanged.emit(None)
        assert len(created) == 2
        assert created[1].parent is viewport
        debug_messages = [r.getMessage() for r in caplog.records if r.levelno == 10]
        assert any("window-activeView" in m for m in debug_messages), debug_messages

        canvas_a.rotationChanged.emit(99.0)
        assert context.view_state.rotation_deg == 0.0
        assert created[1].repaints == 0
        canvas_b.rotationChanged.emit(20.0)
        assert context.view_state.rotation_deg == 20.0
        assert created[1].repaints == 1
    finally:
        _close(win)


def test_active_view_changed_window_payload_rebinds_new_window():
    """A window-like payload (exposes ``qwindow()``, no ``canvas()``)
    re-resolves the WINDOW: the overlay re-attaches to the NEW window's
    canvas widget while the stored canvas subscription is kept — the current
    behaviour survives only as this last resort for view/canvas."""
    win1, viewport1 = _signal_mdi_window()
    win2, viewport2 = _signal_mdi_window()
    canvas = FakeCanvas()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    context = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert context.bind(win1, FakeView(canvas, FakeDocument()), canvas) is True
        assert created[0].parent is viewport1
        win1.activeViewChanged.emit(FakeWindowWrapper(win2))  # activeView -> None
        assert len(created) == 2
        assert created[0].disposed is True
        assert created[1].parent is viewport2, "window-like payload re-resolves the window"

        before = created[1].repaints
        canvas.rotationChanged.emit(10.0)
        assert created[1].repaints == before + 1, "stored canvas kept as last resort"
    finally:
        _close(win1)
        _close(win2)


def test_default_factory_parents_a_real_grid_overlay():
    """Without an injected factory the context builds a REAL GridOverlay:
    parented to the located canvas viewport, sized to its rect, and inert to
    the mouse before any edit session (transparent attribute set).

    The user's live report said
    ``overlay.isVisible=False`` for an attached, gate-accepted overlay, and the
    code had NO show() call anywhere — a child created under an already-visible
    parent is not shown by itself, so the grid could never receive a paintEvent.
    Asserted strictly (not "did not crash"): the attached overlay is on screen."""
    _ensure_qapp()
    context = CanvasContext(GridStateService())
    win, viewport = _mdi_window()
    canvas = FakeCanvas()
    try:
        assert context.bind(win, None, canvas) is True
        overlay = context.overlay
        assert isinstance(overlay, GridOverlay)
        assert overlay.parentWidget() is viewport
        assert overlay.geometry() == viewport.rect()
        assert overlay.testAttribute(qt_compat.WA_TransparentForMouseEvents) is True
        assert not overlay.isHidden(), "attaching must show the overlay"
        assert overlay.isVisible(), "a bound overlay under a shown window is visible"
    finally:
        _close(win)


# --- one-time starvation INFO --------------------------------------------------


def test_first_unresolved_document_size_logs_one_info_then_debug(caplog):
    """While no document-size source answers,
    ``view_state`` stays ``None`` for the rest of the session until a re-read and
    the grid stays hidden — a state that was previously only a
    DEBUG note, i.e. effectively silent to an operator. The FIRST unresolved size
    must surface exactly ONE INFO naming that consequence; every later failed
    re-read stays DEBUG-only so a Reset/refresh storm cannot flood the log."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()  # no central widget/MDI, no activeView -> no Document
    win.show()
    context = CanvasContext(GridStateService())
    try:
        with caplog.at_level("DEBUG", logger="composition_grid"):
            context.bind(win, None, None)  # first starvation: 1 INFO + its DEBUG note
            context.refresh_document_size()  # re-read, still starved: DEBUG only
            context.refresh_document_size()
        infos = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == 20 and "document size" in r.getMessage().lower()
        ]
        debugs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == 10 and "document size" in r.getMessage().lower()
        ]
    finally:
        _close(win)
    assert len(infos) == 1, f"exactly one starvation INFO expected, got {infos}"
    assert "hidden" in infos[0].lower(), "the INFO names the hidden-grid consequence"
    assert len(debugs) >= 2, f"later starvation stays DEBUG-only: {debugs}"
    assert context.view_state is None, "no document size -> no view state (degradation)"


# ================== the Krita 5.3.x path — COMPUTED view state =================
#
# Evidence (live host 5.3.4.1/PyQt5, checked against
# ``libs/libkis/Canvas.h`` at tag v5.3.0-prealpha vs master): the Canvas the
# user runs has NO signals at all — not
# ``documentRectInWidgetPixelsChanged``, not ``rotationChanged``,
# ``mirrorChanged``, ``sizeChanged``, ``removed`` — and no
# ``documentRectInWidgetPixels()`` either. Those are master/6.x. So a
# ViewState that can only be RECEIVED can never be built there: the overlay
# exists but never paints. The tests below pin the replacement contract —
# ViewState COMPUTED from the getters the 5.3 host does answer, invalidated by
# genuine Qt viewport events (scrollbar value/range changes, wheel, resize),
# with NO timers and no "if 5.3 else 6.0" dual path.


_CANVAS_53_CLS = None


def _canvas_53_cls():
    """Build the 5.3.4.1-shaped Canvas class on first use (the same deferred
    definition rule as :func:`_fake_canvas_cls`: a module-level ``class`` would
    dereference ``QtCore`` at import even when the whole module is skipped)."""
    global _CANVAS_53_CLS
    if _CANVAS_53_CLS is not None:
        return _CANVAS_53_CLS

    class Canvas53(QtCore.QObject):
        """EXACTLY the live 5.3.4.1 Canvas surface and nothing more: a QObject
        with NO signal members (``stateChanged``/``documentOffsetChanged``/
        ``effectiveZoomChanged``/``rotationChanged``/``mirrorChanged``/
        ``sizeChanged``/``documentRectInWidgetPixelsChanged``/``removed`` are
        all absent) and NO ``documentRectInWidgetPixels()`` and no
        ``width()``/``height()``.

        What the host does answer (the user's own diagnostics dump) is the
        getter surface below, and these are the only inputs the computed
        ViewState may read: ``zoomLevel()``, ``rotation()``, ``mirror()``,
        ``preferredCenter()``, plus the present-but-unused ``pan()``/``view()``/
        ``wrapAroundMode()``/``levelOfDetailMode()``.

        HOST-CONSISTENCY (the fake was the one that lied about the host):
        ``mirror()`` answers a plain BOOL here, because that is what the live
        5.3.4.1 returned (``Canvas.mirror() = False``) and what libkis documents
        for this API generation (``mirror()`` / ``setMirror(bool)``,
        one horizontal flip). The dict shape stays exercised by the 6.x
        :func:`FakeCanvas` above and by ``tests/canvas/test_host_view_probe.py``.
        ``zoomLevel()`` is likewise host-shaped: on this host it is ALREADY
        widget px per document px (the two scrollbar values prove it)."""

        def __init__(
            self,
            zoom=1.0,
            rotation=0.0,
            mirror=False,
            center_x=0.0,
            center_y=0.0,
        ):
            super().__init__()
            self._zoom = float(zoom)
            self._rotation = float(rotation)
            self._mirror = bool(mirror)
            self._center = QtCore.QPointF(float(center_x), float(center_y))

        def zoomLevel(self):
            return self._zoom

        def rotation(self):
            return self._rotation

        def mirror(self):
            return self._mirror

        def preferredCenter(self):
            return self._center

        # Present on the host, never called by the plugin — part of the shape,
        # so the fake is neither more nor less capable than the machine.
        def pan(self, point):  # noqa: ARG002 — host signature
            return None

        def view(self):
            return None

        def wrapAroundMode(self):
            return False

        def levelOfDetailMode(self):
            return False

    _CANVAS_53_CLS = Canvas53
    return Canvas53


def Canvas53(**kwargs):  # noqa: N802 — call sites keep class-style construction
    return _canvas_53_cls()(**kwargs)


class Document53:
    """``Document`` as the live 5.3.4.1 host answers it: ``width()``,
    ``height()`` and ``resolution()`` are present, ``name()`` is EMPTY for a
    document opened from a file while ``fileName()`` is populated, and
    ``uniqueId``/``title`` do not exist on the host at all — so the fake has
    neither (the measured host surface that ruled out document-key routing).

    ``resolution`` defaults to the live document's 600 dpi. It is carried
    because the HOST carries it, NOT because the computed scale reads it: the
    two-axis scrollbar agreement established that the scale is
    ``zoomLevel()`` directly, and the probe must not read the dpi at all."""

    def __init__(
        self,
        doc_w=7000.0,
        doc_h=4000.0,
        resolution=600.0,
        file_name="/tmp/live_document.kra",
    ):
        self._doc_w = float(doc_w)
        self._doc_h = float(doc_h)
        self._resolution = float(resolution)
        self._file_name = file_name

    def name(self):
        return ""

    def fileName(self):
        return self._file_name

    def width(self):
        return self._doc_w

    def height(self):
        return self._doc_h

    def resolution(self):
        return self._resolution


def _scroll_area_window(scrollbars=True):
    """A Krita-shaped window whose canvas holder is a REAL
    ``QAbstractScrollArea`` (a ``QScrollArea``) with REAL ``QScrollBar`` s,
    inside an MDI subwindow. The locator resolves its ``viewport()`` — the very
    object the context parents the overlay to — and the scroll area above it is
    what owns the scrollbars the 5.3 repaint triggers are wired on (the
    locator's own DEBUG line "canvas viewport … inside <QAbstractScrollArea>"
    is the proof that this holder exists on the live host).

    ``scrollbars=False`` parks both scrollbar policies on AlwaysOff: a wheel
    then provably cannot scroll, so a repaint observed after a wheel event can
    only have come from the viewport event filter."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    mdi = QtWidgets.QMdiArea()
    win.setCentralWidget(mdi)
    sub = QtWidgets.QMdiSubWindow()
    scroll = QtWidgets.QScrollArea()
    inner = QtWidgets.QWidget()
    inner.setFixedSize(3000, 3000)  # a big canvas: the scrollbars have a real range
    scroll.setWidget(inner)
    scroll.setWidgetResizable(False)
    if not scrollbars:
        off = QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff if hasattr(
            QtCore.Qt, "ScrollBarPolicy"
        ) else QtCore.Qt.ScrollBarAlwaysOff
        scroll.setHorizontalScrollBarPolicy(off)
        scroll.setVerticalScrollBarPolicy(off)
    sub.setWidget(scroll)
    mdi.addSubWindow(sub)
    mdi.setActiveSubWindow(sub)
    win.resize(1400, 900)
    sub.resize(1000, 700)
    win.show()
    QtWidgets.QApplication.processEvents()
    return win, scroll, inner


def _bound_context_53(canvas=None, document=None, scrollbars=True):
    """Bind the 5.3-shaped triplet (Canvas53 + Document53 + the bound ``View``)
    with a recording overlay; returns ``(context, canvas, document, win,
    scroll, viewport, created)``."""
    _ensure_qapp()
    canvas = canvas if canvas is not None else Canvas53()
    document = document if document is not None else Document53()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    win, scroll, inner = _scroll_area_window(scrollbars=scrollbars)
    viewport = scroll.viewport()
    context = CanvasContext(GridStateService(), overlay_factory=factory)
    assert context.bind(win, FakeView(canvas, document), canvas) is True
    return context, canvas, document, win, scroll, viewport, created


def _wheel_event(angle_delta=120):
    """A widget-local ``QWheelEvent``. The Qt5 and Qt6 constructors differ
    (Qt6: 8 args with pixel/angle deltas and a scroll phase; Qt5: 6 args with a
    single delta), so the Qt6 form is tried first, same scoped-first rule as
    everywhere else in this file."""
    buttons = _enum_member("MouseButton", "NoButton")
    modifiers = _enum_member("KeyboardModifier", "NoModifier")
    try:
        phase = _enum_member("ScrollPhase", "NoScrollPhase")
    except AttributeError:  # pragma: no cover - very old bindings
        phase = None
    point = QtCore.QPointF(20.0, 20.0)
    if phase is not None:
        try:
            return QtGui.QWheelEvent(
                point,
                point,
                QtCore.QPoint(0, 0),
                QtCore.QPoint(0, angle_delta),
                buttons,
                modifiers,
                phase,
                False,
            )
        except (TypeError, AttributeError):
            pass
    return QtGui.QWheelEvent(
        QtCore.QPoint(20, 20), QtCore.QPoint(20, 20), angle_delta, buttons, modifiers
    )


def test_canvas_53_shape_has_no_signals_and_no_rect_getter():
    """THE verified fact the tests below are built on, pinned on the fake
    itself: a 5.3.4.1 Canvas has none of the signals the earlier context
    required and no ``documentRectInWidgetPixels()``. If a future fake grew one
    of these back, every computed-path test below would silently stop testing
    the 5.3 host."""
    canvas = Canvas53()
    for signal_name in (
        "stateChanged",
        "documentOffsetChanged",
        "effectiveZoomChanged",
        "rotationChanged",
        "mirrorChanged",
        "sizeChanged",
        "documentRectInWidgetPixelsChanged",
        "removed",
        "documentRectInWidgetPixels",
    ):
        assert not hasattr(canvas, signal_name), f"fake grew host-absent {signal_name}"


def test_view_state_is_computed_on_a_fresh_bind_with_no_signal_ever_emitted():
    """(a) The defect fixed here: on a signalless 5.3 Canvas the OLD
    context had no ViewState source at all, so ``view_state`` stayed ``None``
    and the overlay never painted. A fresh bind must now answer a ViewState
    computed from the getters + the located viewport's own size, and the rect
    must be exactly the corrected placement applied to THOSE inputs:

        s = zoomLevel()                  (the direct rule — see below)
        w = doc_w * s        x = viewport_w/2 - center_x*s
        h = doc_h * s        y = viewport_h/2 - center_y*s

    (libkis documents ``preferredCenter()`` as "the position
    of the image pixel that is placed in the center of the current view", so
    the centred doc point is ``center`` itself — the earlier +doc/2 term was a
    guess, now refuted by the getter's own documentation).

    with the fake at the live host's own zoom 1.0 on a 600 dpi document (the
    value the user's report printed) and a 7000x4000 document with the canvas
    centre on the document centre (3500, 2000), so the written arithmetic below
    evaluates to w 7000, h 4000, x = vw/2 - 3500, y = vh/2 - 2000 — the document
    centred in the viewport. The dpi is part of the fixture ON PURPOSE: under
    the refuted ``zoom*resolution/72`` rule the scale here would have been 8.33,
    so this test also discriminates the two rules (the same numbers at 72 dpi
    would not).
    """
    document = Document53(7000.0, 4000.0, resolution=600.0)
    canvas = Canvas53(zoom=1.0, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, _scroll, viewport, created = _bound_context_53(canvas, document)
    try:
        vw, vh = float(viewport.width()), float(viewport.height())
        assert vw > 0.0 and vh > 0.0, "the located viewport must have a real size"
        state = context.view_state
        assert state is not None, "a signalless 5.3 bind must still compute a ViewState"
        scale = 1.0  # s = zoomLevel(): the host zoom IS widget px per doc px
        assert state.document_rect_widget.w == 7000.0 * scale
        assert state.document_rect_widget.h == 4000.0 * scale
        assert state.document_rect_widget.x == vw / 2.0 - 3500.0 * scale
        assert state.document_rect_widget.y == vh / 2.0 - 2000.0 * scale
        assert state.doc_width == 7000.0
        assert state.doc_height == 4000.0
        assert state.rotation_deg == 0.0
        assert state.mirror_x is False and state.mirror_y is False
        # bind() refreshes once and NOTHING else may fire before an event:
        assert created[0].refreshes == 1
        assert created[0].repaints == 0
    finally:
        _close(win)


def test_zoom_drives_the_computed_scale_and_resolution_does_not():
    """CORRECTED SCALE RULE, verified end-to-end through the real context
    (the expectations used to assert ``zoom * resolution / 72``,
    which the live host refutes — a correction justified by host evidence, not a
    weakening):

    * zoom 0.5 at 600 dpi halves the rect (the refuted rule would have given
      ``0.5 * 600 / 72 = 4.17``, i.e. a document 8x too large);
    * the SAME geometry at 96 dpi gives the IDENTICAL rect — dpi simply is not an
      input any more."""
    document = Document53(7000.0, 4000.0, resolution=600.0)
    canvas = Canvas53(zoom=0.5, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, _scroll, viewport, _created = _bound_context_53(canvas, document)
    try:
        state = context.view_state
        assert state.document_rect_widget.w == 3500.0
        assert state.document_rect_widget.h == 2000.0
        # x = vw/2 - center_x*s = vw/2 - 3500*0.5 (the centred doc point
        # scales with s too).
        assert state.document_rect_widget.x == viewport.width() / 2.0 - 1750.0
    finally:
        _close(win)

    same_zoom_other_dpi = Document53(100.0, 100.0, resolution=96.0)
    canvas = Canvas53(zoom=0.24, center_x=50.0, center_y=50.0)
    context, _c, _d, win, _scroll, viewport, _created = _bound_context_53(
        canvas, same_zoom_other_dpi
    )
    try:
        state = context.view_state
        assert state.document_rect_widget.w == pytest.approx(24.0, abs=1e-9)
        assert state.document_rect_widget.h == pytest.approx(24.0, abs=1e-9)
        assert state.document_rect_widget.x == pytest.approx(
            viewport.width() / 2.0 - 12.0, abs=1e-9
        )
    finally:
        _close(win)

    other_dpi = Document53(100.0, 100.0, resolution=600.0)
    context, _c, _d, win, _scroll, viewport, _created = _bound_context_53(
        Canvas53(zoom=0.24, center_x=50.0, center_y=50.0), other_dpi
    )
    try:
        # The identical numbers at a different dpi: nothing in the arithmetic
        # reads the document resolution.
        state600 = context.view_state
        assert state600.document_rect_widget.w == pytest.approx(24.0, abs=1e-9)
        assert state600.document_rect_widget.h == pytest.approx(24.0, abs=1e-9)
        assert state600.document_rect_widget.x == pytest.approx(
            viewport.width() / 2.0 - 12.0, abs=1e-9
        )
    finally:
        _close(win)


def test_scrollbar_value_change_repaints_and_the_rect_follows_a_zoom_change():
    """(b) The 5.3 invalidation source: a scroll of the canvas viewport is a
    real ``QScrollBar.valueChanged``. The handler must ask for a repaint, and
    because the ViewState is COMPUTED the rect reads the zoom the host now
    answers — a zoom change that arrived with no signal at all."""
    canvas = Canvas53(zoom=1.0, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, scroll, _viewport, created = _bound_context_53(canvas)
    overlay = created[0]
    try:
        bar = scroll.horizontalScrollBar()
        assert bar.maximum() > 0, "the fixture needs a scrollable range"
        repaints_after_range = overlay.repaints

        canvas._zoom = 0.25  # a zoom change the fake received with NO signal
        before = overlay.repaints
        bar.setValue(100)
        assert overlay.repaints == before + 1, (
            "scrollbar valueChanged must request a repaint (no timers)"
        )
        state = context.view_state
        assert state.document_rect_widget.w == 7000.0 * 0.25
        assert state.document_rect_widget.h == 4000.0 * 0.25
        assert overlay.repaints == repaints_after_range + 1
    finally:
        _close(win)


def test_scrollbar_range_change_repaints():
    """(c) ``rangeChanged`` is the other half: growing the canvas (Krita
    emits it when the document or the viewport extent changes) repaints, and
    the repaint must be provably from the range signal — the value is still
    inside the new range, so no ``valueChanged`` accompanies it."""
    canvas = Canvas53(zoom=1.0, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, scroll, _viewport, created = _bound_context_53(canvas)
    overlay = created[0]
    try:
        bar = scroll.horizontalScrollBar()
        bar.setValue(100)
        before = overlay.repaints
        before_value = bar.value()
        bar.setRange(0, 6000)
        assert bar.value() == before_value, "the value must not move (only a range change)"
        assert overlay.repaints == before + 1, (
            "scrollbar rangeChanged must request a repaint"
        )
    finally:
        _close(win)


def test_wheel_event_on_the_viewport_repaints():
    """(d) The wheel is the common zoom gesture on 5.3 and it arrives on the
    canvas VIEWPORT, not on the libkis Canvas. With both scrollbars parked
    AlwaysOff the wheel provably cannot scroll, so the single repaint can only
    have come from the viewport event filter — and the filter still returns
    False, so Krita keeps the event."""
    context, _c, _d, win, _scroll, viewport, created = _bound_context_53(scrollbars=False)
    overlay = created[0]
    try:
        before = overlay.repaints
        wheel = _wheel_event()
        assert QtCore.QCoreApplication.sendEvent(viewport, wheel) is not None
        assert overlay.repaints == before + 1, "a wheel on the viewport must repaint"
    finally:
        _close(win)


class _TypedEvent:
    """An event object carrying ONLY what the repaint filter reads: ``type()``.

    NOT a stand-in for a real Qt event in general — it exists because PyQt
    wraps neither ``QScrollPrepareEvent`` nor ``QScrollEvent`` (so a Python test
    cannot build a genuine one), and handing a plain ``QEvent`` of those types
    to a ``QAbstractScrollArea`` viewport makes Qt static_cast it to
    ``QScrollPrepareEvent`` and WRITE through that cast (observed as
    STATUS_HEAP_CORRUPTION). The real delivery path is pinned separately with
    genuine ``QWheelEvent``/``QResizeEvent`` objects below; this class pins only
    the two remaining type comparisons of the trigger set."""

    def __init__(self, event_type):
        self._type = event_type

    def type(self):
        return self._type


def test_resize_event_on_the_viewport_repaints():
    """``Resize`` of the canvas viewport changes the viewport size the ViewState
    is computed against, so it repaints — delivered as a genuine ``QResizeEvent``
    through Qt's own dispatch, and still passed through to the widget (the
    filter never consumes)."""
    context, _c, _d, win, _scroll, viewport, created = _bound_context_53(scrollbars=False)
    overlay = created[0]
    try:
        before = overlay.repaints
        event = QtGui.QResizeEvent(QtCore.QSize(900, 600), QtCore.QSize(800, 500))
        QtCore.QCoreApplication.sendEvent(viewport, event)
        assert overlay.repaints == before + 1, "a viewport Resize must repaint"
    finally:
        _close(win)


def test_scroll_and_scroll_prepare_types_trigger_the_filter():
    """The last two members of the trigger set (Qt's kinetic-scroll
    notifications). The pin is exact: the two types repaint, an unrelated type
    does not, and the filter answers ``False`` for all of them."""
    context, _c, _d, win, _scroll, viewport, created = _bound_context_53(scrollbars=False)
    overlay = created[0]
    try:
        _watched, viewport_filter, _remove = context._event_filters[0]
        for event_name in ("Scroll", "ScrollPrepare"):
            assert (
                viewport_filter.eventFilter(
                    viewport, _TypedEvent(getattr(qt_compat, event_name))
                )
                is False
            ), f"{event_name} must not be consumed"
        assert overlay.repaints == 2, "Scroll and ScrollPrepare must each repaint"

        before = overlay.repaints
        assert viewport_filter.eventFilter(
            viewport, _TypedEvent(qt_compat.Close)
        ) is False
        assert overlay.repaints == before, "a non-trigger event type must not repaint"
    finally:
        _close(win)


def test_viewport_paint_and_holder_layout_request_repaint():
    """Triggers for changes that move NEITHER scrollbar
    value NOR range (mirror toggle, menu/N-key rotation, slider zoom while the
    document fits the viewport, zoom-to-cursor). Those changes are only
    visible because the viewport must REPAINT, so ``QEvent.Paint`` on the
    viewport invalidates — and the ``resetCanvas`` widget swap re-lays-out the
    holder, so ``QEvent.LayoutRequest`` on the located scroll area does too.
    The overlay is a CHILD of the viewport, so invalidating it from the
    parent's Paint cannot re-enter the parent's paint (loop-free proof lives in
    ``tests/canvas/test_viewport_triggers_qt.py`` with a real child widget);
    ``unbind`` removes BOTH filters."""
    context, _c, _d, win, scroll, viewport, created = _bound_context_53(scrollbars=False)
    overlay = created[0]
    try:
        assert len(context._event_filters) == 2, (
            "viewport AND scroll-area holder filters must both be installed"
        )
        before = overlay.repaints
        QtCore.QCoreApplication.sendEvent(
            viewport, QtGui.QPaintEvent(QtCore.QRect(0, 0, 10, 10))
        )
        assert overlay.repaints == before + 1, "a viewport Paint must request a repaint"
        QtCore.QCoreApplication.sendEvent(
            scroll, QtCore.QEvent(getattr(qt_compat, "LayoutRequest"))
        )
        assert overlay.repaints == before + 2, "a holder LayoutRequest must repaint"

        context.unbind()
        after = overlay.repaints
        QtCore.QCoreApplication.sendEvent(
            viewport, QtGui.QPaintEvent(QtCore.QRect(0, 0, 10, 10))
        )
        QtCore.QCoreApplication.sendEvent(
            scroll, QtCore.QEvent(getattr(qt_compat, "LayoutRequest"))
        )
        assert overlay.repaints == after, "unbound filters must not repaint anything"
    finally:
        _close(win)


def test_viewport_resize_is_reflected_in_the_computed_rect():
    """The viewport size is read LIVE, not seeded once at bind: after the
    canvas holder is really resized, the rect follows the new viewport
    dimensions (the x offset moves by half the width delta) — the
    document-side scale is untouched."""
    canvas = Canvas53(zoom=1.0, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, scroll, viewport, _created = _bound_context_53(canvas)
    try:
        before_x = context.view_state.document_rect_widget.x
        before_w = viewport.width()
        scroll.resize(600, 400)
        QtWidgets.QApplication.processEvents()
        after_w = viewport.width()
        assert after_w != before_w, "the fixture must actually change the viewport size"
        state = context.view_state
        assert state.document_rect_widget.x == pytest.approx(
            before_x + (after_w - before_w) / 2.0
        )
        assert state.document_rect_widget.w == 7000.0
    finally:
        _close(win)


def test_rotation_and_mirror_getters_reach_the_computed_state():
    """Rotation and mirror are getter reads on BOTH hosts (``rotation()``, and
    ``mirror()`` — which on this API generation answers the plain bool libkis
    documents) and must land on the ViewState unchanged, with the
    rect staying axis-aligned — the rotation stays in the mapper, not the
    rect. A horizontal flip is ONE axis: ``mirror_y`` stays False."""
    canvas = Canvas53(
        zoom=1.0, rotation=30.0, mirror=True, center_x=3500.0, center_y=2000.0
    )
    context, _c, _d, win, _scroll, _viewport, _created = _bound_context_53(canvas)
    try:
        state = context.view_state
        assert state.rotation_deg == 30.0
        assert state.mirror_x is True
        assert state.mirror_y is False
        assert state.document_rect_widget.h == 4000.0  # rotation not folded in
    finally:
        _close(win)


def test_absent_required_getter_degrades_to_none_and_logs_debug(caplog):
    """A getter the computed path needs but the host does not answer is a
    DEBUG note and an unusable ViewState — never an exception, never a silent
    default that would paint a wrong grid. Here: a 5.3-shaped Canvas with a
    viewport and a document but NO ``zoomLevel()``."""

    class NoZoomCanvas(QtCore.QObject):
        """The shape of a host that cannot answer the scale: the other 5.3
        getters are there, ``zoomLevel()`` is not."""

        def rotation(self):
            return 0.0

        def mirror(self):
            return False

        def preferredCenter(self):
            return QtCore.QPointF(3500.0, 2000.0)

    context, _c, _d, win, _scroll, _viewport, _created = _bound_context_53(
        NoZoomCanvas()
    )
    try:
        with caplog.at_level(logging.DEBUG, logger="composition_grid"):
            assert context.view_state is None
        assert any("zoomLevel" in m for m in caplog.messages), caplog.messages
    finally:
        _close(win)


# --- starvation must be VISIBLE, not DEBUG-only --------------------------------


def test_first_computed_view_state_starvation_logs_one_info_then_debug(caplog):
    """A missing computed-path getter used to degrade to
    ``view_state=None`` with a DEBUG-only note — invisible to the operator
    (the DOCUMENT-size starvation had a one-time INFO, which is how the live
    host was diagnosed at all; the pattern now covers the whole
    computed path). Exactly ONE INFO on the first starvation naming the hidden
    grid consequence, DEBUG on every later attempt so a paint storm cannot
    flood the log."""
    from composition_grid.canvas import view_metrics

    class NoCenterCanvas(QtCore.QObject):
        """5.3-shaped Canvas with every getter EXCEPT ``preferredCenter()``
        — the computed path cannot be fed without it."""

        def zoomLevel(self):
            return 1.0

        def rotation(self):
            return 0.0

        def mirror(self):
            return False

    view_metrics.reset_starvation_latch()
    context, _c, _d, win, _scroll, _viewport, _created = _bound_context_53(NoCenterCanvas())
    try:
        with caplog.at_level(logging.DEBUG, logger="composition_grid"):
            assert context.view_state is None
            assert context.view_state is None
            assert context.view_state is None
        infos = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO and "view_metrics" in r.getMessage()
        ]
        debugs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG and "view_metrics" in r.getMessage()
        ]
        assert len(infos) == 1, f"exactly one starvation INFO expected, got {infos}"
        assert "hidden" in infos[0].lower(), "the INFO names the hidden-grid consequence"
        assert len(debugs) >= 2, f"later starvations stay DEBUG-only: {debugs}"
    finally:
        _close(win)
        view_metrics.reset_starvation_latch()


def test_orientation_starvation_also_surfaces_the_one_info(caplog):
    """Every None-out of the COMPUTED path routes through the latched reporter
    — here the canvas answers no ``rotation()``/``mirror()`` at all (the
    context used to return ``None`` silently on that branch)."""
    from composition_grid.canvas import view_metrics

    class OrientationlessCanvas(QtCore.QObject):
        def zoomLevel(self):
            return 1.0

    view_metrics.reset_starvation_latch()
    context, _c, _d, win, _scroll, _viewport, _created = _bound_context_53(
        OrientationlessCanvas()
    )
    try:
        with caplog.at_level(logging.DEBUG, logger="composition_grid"):
            assert context.view_state is None
            assert context.view_state is None
        infos = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO and "view_metrics" in r.getMessage()
        ]
        assert len(infos) == 1, infos
        assert "orientation" in infos[0].lower(), infos[0]
    finally:
        _close(win)
        view_metrics.reset_starvation_latch()


def test_canvas_size_change_repaints_with_the_LIVE_extent_not_the_cached_one():
    """``Image ▸ Canvas Size`` changes the document extent
    and the host announces it via the scrollbar ``rangeChanged`` — but the old
    context read ``Document.width()/height()`` only at bind/refresh, so the
    repaint still used the STALE extent (7000 while the document is 8000: the
    scale denominator off by 12.5%). Every input of one frame must come from
    the same live read: the rect, the ViewState's doc_width and the scale
    denominator must all follow the document's CURRENT answer.
    ``doc_size`` (the docker's ensure/Reset source) is deliberately the
    LAST-KNOWN stored pair until :meth:`refresh_document_size` — that
    distinction is the contract, not an oversight."""
    document = Document53(7000.0, 4000.0, resolution=600.0)
    canvas = Canvas53(zoom=1.0, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, scroll, _viewport, created = _bound_context_53(canvas, document)
    overlay = created[0]
    try:
        state = context.view_state
        assert state.document_rect_widget.w == 7000.0
        assert state.doc_width == 7000.0

        document._doc_w = 8000.0  # Image ▸ Canvas Size on the same Document
        before = overlay.repaints
        scroll.horizontalScrollBar().setRange(0, 9000)  # the host's own announce
        assert overlay.repaints == before + 1, "rangeChanged must repaint"

        state = context.view_state
        assert state.document_rect_widget.w == 8000.0, (
            "the repaint must use the LIVE document extent, "
            "not the extent cached at bind"
        )
        assert state.doc_width == 8000.0
        assert state.document_rect_widget.h == 4000.0
        # The last-known pair is what the docker's ensure/Reset reads, and it
        # only moves on an explicit refresh — the pinned distinction:
        assert context.doc_size == (7000.0, 4000.0), "stored pair stays last-known"
        assert context.refresh_document_size() is True
        assert context.doc_size == (8000.0, 4000.0), "refresh re-reads it"
    finally:
        _close(win)


def test_live_extent_read_failure_degrades_to_no_view_state():
    """Same-live-read rule, other half: when the live
    extent read itself fails (a wrapper mid-teardown), the frame is STARVED —
    ``view_state`` answers ``None`` — rather than mixing a precise 6.x rect
    override with a stale cached extent. Mixed sources in one frame are
    exactly what the live-read rule forbids."""
    document = Document53(7000.0, 4000.0, resolution=600.0)
    canvas = Canvas53(zoom=1.0, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, _scroll, _viewport, _created = _bound_context_53(canvas, document)
    try:
        assert context.view_state is not None

        def _raising():
            raise RuntimeError("Document wrapper mid-teardown")

        document.width = _raising
        assert context.view_state is None, (
            "a failed live extent read must starve the frame, not fall back "
            "to the cached extent"
        )
    finally:
        _close(win)


def test_precise_rect_path_also_uses_the_live_extent():
    """Krita 6 override half of the same rule: even when the HOST rect wins,
    the ViewState's doc_width/doc_height (the mapper's scale denominator) come
    from the live ``Document.width()/height()`` read, so the precise rect can
    never be paired with the bind-cached extent."""
    context, canvas, win, _viewport, _created = _bound_context(doc_w=4000.0, doc_h=3000.0)
    try:
        assert context.view_state.doc_width == 4000.0
        context._document._doc_w = 5000.0  # live Canvas Size on the same doc
        state = context.view_state
        assert state.document_rect_widget == Bounds(0.0, 0.0, 800.0, 600.0), (
            "the host rect override stays untouched"
        )
        assert state.doc_width == 5000.0, "the extent is read live on BOTH paths"
    finally:
        _close(win)


def test_unbind_disconnects_the_scrollbar_triggers():
    """The 5.3 triggers are connections like any other: after ``unbind`` a
    scrollbar change must not repaint the disposed overlay, and a later
    ``view_state`` read stays exception-free."""
    canvas = Canvas53(zoom=1.0, center_x=3500.0, center_y=2000.0)
    context, _c, _d, win, scroll, _viewport, created = _bound_context_53(canvas)
    overlay = created[0]
    try:
        bar = scroll.horizontalScrollBar()
        bar.setRange(0, 5000)
        context.unbind()
        assert overlay.disposed is True
        before = overlay.repaints
        bar.setValue(250)
        assert overlay.repaints == before, "a disconnected scrollbar must not repaint"
        assert context.view_state is None
    finally:
        _close(win)


def test_krita_6_precise_rect_still_wins_over_the_computed_one():
    """The sanctioned override (on 6.x
    ``documentRectInWidgetPixels()`` is the exact rect, so it beats the
    computed approximation and the fallback stays the tested 5.3 path above):
    the 6.x fake canvas shape — precise rect getter + signals, NO
    zoom/preferredCenter — must still yield exactly its rect."""
    _ensure_qapp()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    win, viewport = _mdi_window()
    canvas = FakeCanvas(800.0, 600.0)
    context = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert context.bind(win, FakeView(canvas, FakeDocument(4000.0, 3000.0)), canvas)
        assert context.view_state.document_rect_widget == Bounds(0.0, 0.0, 800.0, 600.0)
        canvas.documentRectInWidgetPixelsChanged.emit(QtCore.QRectF(12.0, 15.0, 500.0, 400.0))
        assert context.view_state.document_rect_widget == Bounds(12.0, 15.0, 500.0, 400.0)
    finally:
        _close(win)


# ============ the host's OWN doc->widget affine beats the reconstruction ======


class _HostMatrix:
    """``QTransform`` duck, attribute-shaped like the live binding. The
    ``inverted()`` below exists so the fake looks like the real class — the
    probe must never need it (it inverts in ``Affine`` arithmetic)."""

    def __init__(self, m11, m12, m21, m22, dx, dy):
        self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = (
            m11,
            m12,
            m21,
            m22,
            dx,
            dy,
        )

    def inverted(self):  # pragma: no cover - proves nothing if it is never called
        return self, True


class AffineView(FakeView):
    """``View`` answering the two libkis transform getters on top of the
    ``canvas()``/``document()`` surface."""

    def __init__(self, canvas, document, image_to_flake, flake_to_canvas):
        super().__init__(canvas, document)
        self._image_to_flake = image_to_flake
        self._flake_to_canvas = flake_to_canvas

    def flakeToImageTransform(self):
        return self._image_to_flake

    def flakeToCanvasTransform(self):
        return self._flake_to_canvas


# zoom .5, pan (900, 600), rotation 30, doc 3000x2000 — the measured host answer
_ROT_30 = (0.4330, 0.25, -0.25, 0.4330, 987.0, 318.5)
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def test_view_state_prefers_the_host_affine_over_the_precise_rect():
    """The live-host measurement: the reconstructed rotation is wrong (Krita 5.3
    rotation is CLOCKWISE-positive and pivots away from the doc centre), so the
    ``View``'s own matrices win even over the Krita 6 precise rect — an
    axis-aligned rect can never place a rotated document. doc(700, 200) must
    land on the measured widget point, and the rect the state still carries is
    the affine's own footprint (this 6.x-shaped canvas answers no
    zoomLevel/preferredCenter, so the computed rect is not available)."""
    _ensure_qapp()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    win, _viewport = _mdi_window()
    canvas = FakeCanvas(800.0, 600.0)
    view = AffineView(
        canvas,
        FakeDocument(3000.0, 2000.0),
        _HostMatrix(*_IDENTITY),
        _HostMatrix(*_ROT_30),
    )
    context = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert context.bind(win, view, canvas) is True
        state = context.view_state
        assert state.host_affine is not None, "the affine source is the point of this path"
        assert doc_to_widget_affine(state).apply((700.0, 200.0)) == pytest.approx(
            (1240.1, 580.1), abs=0.2
        )
        assert widget_to_doc_affine(state).apply((1240.1, 580.1)) == pytest.approx(
            (700.0, 200.0), abs=0.2
        )
        assert view_scale(state) == pytest.approx(0.5, abs=1e-4)
        rect = state.document_rect_widget
        assert (rect.x, rect.y) == pytest.approx((487.0, 318.5), abs=0.2)
        assert (rect.w, rect.h) == pytest.approx((1799.0, 1616.0), abs=0.5)
        # The orientation fields stay filled (reporting only — the mapper uses
        # the affine), read from the very same live getters as before.
        assert state.rotation_deg == canvas.rotation()
        assert state.doc_width == 3000.0 and state.doc_height == 2000.0
    finally:
        _close(win)


def test_host_affine_state_keeps_the_computed_rect_when_it_is_available():
    """The 5.3 shape plus an answering ``View``: the mapping is the host's
    matrix while the rect the state carries is the one the computed getters
    describe (zoom .5 of a 3000x2000 doc = 1500x1000 widget px, centred on
    ``preferredCenter``). A ``FakeView`` that answers no transform getter keeps
    the old computed state untouched — the fallback stays what it was."""
    canvas = Canvas53(zoom=0.5, rotation=30.0, center_x=1800.0, center_y=1000.0)
    document = Document53(3000.0, 2000.0)
    plain, _c, _d, win, _scroll, _viewport, _created = _bound_context_53(canvas, document)
    try:
        computed = plain.view_state
        assert computed.host_affine is None, "FakeView answers no transform getter"
        rect = computed.document_rect_widget
        assert (rect.w, rect.h) == (1500.0, 1000.0)
        assert rect.x == pytest.approx(plain.widget.width() / 2.0 - 900.0, abs=1e-6)
    finally:
        _close(win)

    _ensure_qapp()
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    win2, _v2 = _mdi_window()
    view2 = AffineView(
        Canvas53(zoom=0.5, rotation=30.0, center_x=1800.0, center_y=1000.0),
        Document53(3000.0, 2000.0),
        _HostMatrix(*_IDENTITY),
        _HostMatrix(*_ROT_30),
    )
    context2 = CanvasContext(GridStateService(), overlay_factory=factory)
    try:
        assert context2.bind(win2, view2, view2.canvas()) is True
        state = context2.view_state
        assert state.host_affine is not None
        assert doc_to_widget_affine(state).apply((700.0, 200.0)) == pytest.approx(
            (1240.1, 580.1), abs=0.2
        )
        assert (
            state.document_rect_widget.w,
            state.document_rect_widget.h,
            state.document_rect_widget.x,
            state.document_rect_widget.y,
        ) == pytest.approx(
            (
                1500.0,
                1000.0,
                context2.widget.width() / 2.0 - 900.0,
                context2.widget.height() / 2.0 - 500.0,
            )
        )
    finally:
        _close(win2)


# ============ the whole path must actually PAINT ==============================


def _window_with_viewport_of(target_w: int, target_h: int):
    """A Krita-shaped MDI window whose canvas viewport is EXACTLY
    ``target_w x target_h`` — the user's live 1234x877 — built from real Qt
    widgets and converged by resizing the subwindow until
    ``QScrollArea.viewport()`` reports the target. The caller asserts the size,
    so a fixture that cannot reach it fails loudly instead of quietly measuring
    a different viewport.

    Deliberately NOT ``_scroll_area_window``: that helper's viewport is whatever
    the offscreen layout gives (≈966x666), and this test's whole point is that
    the rect numbers agree with the host's own scrollbar readings IN PIXELS."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    mdi = QtWidgets.QMdiArea()
    win.setCentralWidget(mdi)
    sub = QtWidgets.QMdiSubWindow()
    scroll = QtWidgets.QScrollArea()
    inner = QtWidgets.QWidget()
    inner.setFixedSize(3000, 3000)
    scroll.setWidget(inner)
    scroll.setWidgetResizable(False)
    sub.setWidget(scroll)
    mdi.addSubWindow(sub)
    mdi.setActiveSubWindow(sub)
    win.resize(target_w + 320, target_h + 320)
    sub.resize(target_w + 60, target_h + 60)
    win.show()
    for _ in range(25):
        QtWidgets.QApplication.processEvents()
        viewport = scroll.viewport()
        dw = target_w - viewport.width()
        dh = target_h - viewport.height()
        if dw == 0 and dh == 0:
            break
        sub.resize(sub.width() + dw, sub.height() + dh)
    QtWidgets.QApplication.processEvents()
    return win, scroll, scroll.viewport()


def test_live_host_shape_paints_the_grid_end_to_end():
    """THE regression for the user's actual symptom (``context.view_state:
    None``, ``overlay.isVisible=False``, nothing painted) on the EXACT host
    shape, with nothing mocked out but the host objects themselves: a 5.3.4.1
    Canvas with NO signals answering ``zoomLevel() = 0.0833333333333357``,
    ``mirror() = False`` (a plain bool), ``rotation() = 0.0`` and
    ``preferredCenter() = (4860, 1854)``; a 7000x4000 / 600 dpi Document; a real
    parented viewport of 1234x877.

    Asserted, in chain order:
    * ``context.view_state`` is not None (the computed path is unblocked);
    * the computed rect is exactly ``(212.0, 284.0, 583.33…, 333.33…)`` — the
      two host scrollbar values -212/-284 in magnitude, both axes (host
      evidence, so a wrong scale rule cannot pass this);
    * the overlay reports itself visible;
    * ``paint_grid()`` through the REAL ``QPainter`` path puts ink where that
      rect says the rule-of-thirds lines are (first vertical third at
      ``212 + 583.33/3 = 406.4``, first horizontal at ``284 + 333.33/3 =
      395.1``) and leaves the area left of the document rect untouched — a
      pixel-band assertion, not "did not crash"."""
    _ensure_qapp()
    doc_id = "/tmp/live_document.kra"
    service = GridStateService(
        # the element = the whole document, so the widget-space thirds are
        # computable from the rect alone:
        first_geometry_factory=lambda w, h: TransformState(
            cx=w / 2.0, cy=h / 2.0, width=w, height=h
        )
    )
    _one_grid(service, doc_id, 7000.0, 4000.0)

    canvas = Canvas53(
        zoom=0.0833333333333357, center_x=4860.0, center_y=1854.0, mirror=False
    )
    document = Document53(7000.0, 4000.0, resolution=600.0)
    win, _scroll, viewport = _window_with_viewport_of(1234, 877)
    context = CanvasContext(service)
    try:
        assert (viewport.width(), viewport.height()) == (1234, 877), (
            "the fixture must reproduce the live viewport size, or the numbers "
            "below are not the live numbers"
        )
        assert context.bind(win, FakeView(canvas, document), canvas) is True

        state = context.view_state
        assert state is not None, (
            "the host shape that used to starve the grid must now compute a ViewState"
        )
        rect = state.document_rect_widget
        assert rect.x == pytest.approx(212.0, abs=1e-6), rect
        assert rect.y == pytest.approx(284.0, abs=1e-6), rect
        assert rect.w == pytest.approx(7000.0 * 0.0833333333333357, abs=1e-6)
        assert rect.h == pytest.approx(4000.0 * 0.0833333333333357, abs=1e-6)
        assert state.mirror_x is False and state.mirror_y is False
        assert state.doc_width == 7000.0 and state.doc_height == 4000.0

        overlay = context.overlay
        assert isinstance(overlay, GridOverlay), "the production factory is in play"
        assert overlay.parentWidget() is viewport
        assert not overlay.isHidden(), "the overlay was created explicitly hidden"
        assert viewport.isVisible(), "the fixture viewport must be visible"
        assert overlay.isVisible(), "a bound overlay on a shown window is visible"

        img = QtGui.QImage(1234, 877, QtGui.QImage.Format.Format_ARGB32)
        img.fill(QtGui.QColor(*_BG))
        qp = QtGui.QPainter(img)
        try:
            overlay.paint_grid(qp)
        finally:
            qp.end()

        scale = 0.0833333333333357
        first_vertical = 212.0 + 7000.0 * scale / 3.0
        first_horizontal = 284.0 + 4000.0 * scale / 3.0
        vertical = _changed_points(
            img,
            int(first_vertical) - 5,
            int(first_vertical) + 6,
            290,
            610,
        )
        assert len(vertical) >= 50, (
            f"the first vertical third is missing near x={first_vertical:.1f}"
        )
        horizontal = _changed_points(
            img,
            218,
            790,
            int(first_horizontal) - 5,
            int(first_horizontal) + 6,
        )
        assert len(horizontal) >= 100, (
            f"the first horizontal third is missing near y={first_horizontal:.1f}"
        )
        # nothing may be painted left/above the document rect the host says:
        stray = _changed_points(img, 0, 211, 0, 877)
        assert not stray, f"grid leaked outside the document rect: {stray[:5]}"
    finally:
        context.unbind()
        _close(win)


# ============ overlay ownership is the context's own document ===============


def test_two_windows_providers_each_keep_their_own_model(ownership_cleanup):
    """Two contexts on two documents: each provider, view state and style stays its own."""
    tracked = ownership_cleanup
    service = GridStateService()
    a_ctx, a_canvas, a_win, a_vp, a_overlays = _bound_context(
        service, doc_w=200.0, doc_h=200.0, widget_w=400.0, widget_h=400.0,
        doc_name="doc-a", track=tracked,
    )
    b_ctx, b_canvas, b_win, b_vp, b_overlays = _bound_context(
        service, doc_w=400.0, doc_h=100.0, widget_w=200.0, widget_h=50.0,
        doc_name="doc-b", track=tracked,
    )
    model_a = _one_grid(service, "doc-a", 200.0, 200.0)
    model_b = _one_grid(service, "doc-b", 400.0, 100.0)
    service.set_active("doc-a")
    style_a = service.set_style(Style(color_rgba=(0.1, 0.2, 0.3, 1.0), width_px=2.0))
    service.set_active("doc-b")
    style_b = service.set_style(Style(color_rgba=(0.9, 0.5, 0.1, 1.0), width_px=4.0))
    assert a_overlays[0].model_provider() is model_a
    assert b_overlays[0].model_provider() is model_b
    assert a_ctx.view_state.doc_width == 200.0 and a_ctx.view_state.doc_height == 200.0
    assert a_ctx.view_state.document_rect_widget == Bounds(0.0, 0.0, 400.0, 400.0)
    assert b_ctx.view_state.doc_width == 400.0 and b_ctx.view_state.doc_height == 100.0
    assert b_ctx.view_state.document_rect_widget == Bounds(0.0, 0.0, 200.0, 50.0)
    assert a_overlays[0].model_provider().style == style_a
    assert b_overlays[0].model_provider().style == style_b
    service.set_active("doc-a")
    assert b_overlays[0].model_provider() is model_b, (
        "B's overlay must never show the globally active A model"
    )
    assert a_overlays[0].model_provider() is model_a


def test_drag_via_window_a_controller_mutates_only_a(ownership_cleanup):
    """A real centre-handle drag through window A mutates A's geometry only, with B active."""
    tracked = ownership_cleanup
    service = GridStateService()
    a_ctx, a_win, a_vp = _bound_real_window(
        service, "doc-a", 200.0, 200.0, 400.0, 400.0, tracked
    )
    b_ctx, b_win, b_vp = _bound_real_window(
        service, "doc-b", 400.0, 100.0, 200.0, 50.0, tracked
    )
    model_a = _one_grid(service, "doc-a", 200.0, 200.0)
    model_b = _one_grid(service, "doc-b", 400.0, 100.0)
    service.set_active("doc-a")
    service.start_edit_session()
    style_a = service.set_style(Style(color_rgba=(0.1, 0.2, 0.3, 1.0), width_px=2.0))
    service.set_active("doc-b")
    service.start_edit_session()
    style_b = service.set_style(Style(color_rgba=(0.9, 0.5, 0.1, 1.0), width_px=4.0))
    assert a_ctx.model_provider() is model_a
    assert b_ctx.model_provider() is model_b
    before_a = model_a.transform
    before_b = model_b.transform

    overlay_a = a_ctx.overlay
    anchor = handle_positions(a_ctx.view_state, before_a)["move"]
    press = _mouse_event(qt_compat.MousePress, anchor[0], anchor[1], left=True, holding=True)
    overlay_a.mousePressEvent(press)
    assert press.isAccepted(), "A's own model drives A's overlay hit-testing"
    overlay_a.mouseMoveEvent(
        _mouse_event(qt_compat.MouseMove, anchor[0] + 40.0, anchor[1] + 20.0, holding=True)
    )
    overlay_a.mouseReleaseEvent(
        _mouse_event(qt_compat.MouseRelease, anchor[0] + 40.0, anchor[1] + 20.0, left=True)
    )
    # widget 400x400 over doc 200x200: +40/+20 px is exactly +20/+10 doc px.
    assert model_a.transform == TransformState(
        cx=120.0, cy=110.0, width=160.0, height=160.0,
        rotation_deg=0.0, ratio_locked=before_a.ratio_locked,
    )
    assert model_b.transform == before_b, "B's transform is untouched"
    assert model_b.style == style_b, "B's style is untouched"
    assert model_b.edit_session is True, "B's session is untouched"
    assert model_a.edit_session is True, "a handle hit never ends the session"
    assert model_a.style == style_a


def test_miss_on_window_a_ends_only_a_session(ownership_cleanup):
    """A miss on A's overlay ends A's session while globally active B keeps editing."""
    tracked = ownership_cleanup
    service = GridStateService()
    a_ctx, a_win, a_vp = _bound_real_window(
        service, "doc-a", 200.0, 200.0, 400.0, 400.0, tracked
    )
    b_ctx, b_win, b_vp = _bound_real_window(
        service, "doc-b", 400.0, 100.0, 200.0, 50.0, tracked
    )
    model_a = _one_grid(service, "doc-a", 200.0, 200.0)
    model_b = _one_grid(service, "doc-b", 400.0, 100.0)
    service.set_active("doc-a")
    service.start_edit_session()
    service.set_active("doc-b")
    service.start_edit_session()
    assert model_b.edit_session and model_a.edit_session

    press = _mouse_event(qt_compat.MousePress, 1000.0, 1000.0, left=True, holding=True)
    a_ctx.overlay.mousePressEvent(press)
    assert not press.isAccepted(), "the miss passes through"
    assert model_a.edit_session is False
    assert model_b.edit_session is True, (
        "window A's miss must not end globally active B's session"
    )


def test_document_switch_rebind_follows_the_new_document(ownership_cleanup):
    """Rebind re-keys the provider and the session hook onto the NEW document; unbind answers None."""
    tracked = ownership_cleanup
    service = GridStateService()
    context, canvas, win, viewport, created = _bound_context(
        service, doc_w=200.0, doc_h=200.0, widget_w=200.0, widget_h=200.0,
        doc_name="doc-a", track=tracked,
    )
    overlay = created[0]
    model_a = _one_grid(service, "doc-a", 200.0, 200.0)
    model_b = _one_grid(service, "doc-b", 200.0, 200.0)
    assert overlay.model_provider() is model_a

    fresh = FakeCanvas(200.0, 200.0)
    context.rebind(win, FakeView(fresh, FakeDocument(200.0, 200.0, name="doc-b")), fresh)
    assert created[-1].model_provider() is model_b
    assert context.model_provider() is model_b

    service.set_active("doc-a")
    service.start_edit_session()
    service.set_active("doc-b")
    service.start_edit_session()
    service.set_active("doc-a")
    notifications: list[int] = []
    service.on_change(lambda: notifications.append(1))
    session_end = created[-1].end_edit_session
    session_end()
    assert model_b.edit_session is False, (
        "the context's own document ends, not the active one"
    )
    assert model_a.edit_session is True, (
        "the globally active foreign session stays untouched"
    )

    context.unbind()
    assert context.model_provider() is None
    before = len(notifications)
    session_end()
    assert len(notifications) == before, (
        "an unbound context's captured hook must not notify"
    )
    assert model_a.edit_session is True
    assert model_b.edit_session is False


def test_missing_document_never_exposes_the_active_foreign_model(ownership_cleanup):
    """A document-less context answers None and its session hook changes nothing."""
    tracked = ownership_cleanup
    service = GridStateService()
    model_b = _one_grid(service, "doc-b", 200.0, 200.0)
    win, viewport = _mdi_window(tracked)
    created: list[RecordingOverlay] = []

    def factory(*args):
        overlay = RecordingOverlay(*args)
        created.append(overlay)
        return overlay

    context = CanvasContext(service, overlay_factory=factory)
    tracked.append(context)
    assert context.bind(win, None, None) is True
    service.start_edit_session()
    assert context.model_provider() is None
    created[0].end_edit_session()
    assert model_b.edit_session is True, (
        "a document-less context must not end the globally active session"
    )
    assert created[0].model_provider() is None


def test_two_windows_on_one_document_share_one_model(ownership_cleanup):
    """Two windows bound to the SAME Document object legitimately share one model."""
    tracked = ownership_cleanup
    shared = FakeDocument(200.0, 200.0, name="shared.png")
    service = GridStateService()
    a_ctx, a_win, a_vp = _bound_real_window(
        service, "shared.png", 200.0, 200.0, 400.0, 400.0, tracked, document=shared
    )
    b_ctx, b_win, b_vp = _bound_real_window(
        service, "shared.png", 200.0, 200.0, 200.0, 200.0, tracked, document=shared
    )
    model = _one_grid(service, "shared.png", 200.0, 200.0)
    service.start_edit_session()
    assert a_ctx.model_provider() is model
    assert b_ctx.model_provider() is model

    before = model.transform
    anchor = handle_positions(a_ctx.view_state, before)["move"]
    a_ctx.overlay.mousePressEvent(
        _mouse_event(qt_compat.MousePress, anchor[0], anchor[1], left=True, holding=True)
    )
    a_ctx.overlay.mouseMoveEvent(
        _mouse_event(qt_compat.MouseMove, anchor[0] + 40.0, anchor[1], holding=True)
    )
    a_ctx.overlay.mouseReleaseEvent(
        _mouse_event(qt_compat.MouseRelease, anchor[0] + 40.0, anchor[1], left=True)
    )
    assert model.transform == TransformState(
        cx=120.0, cy=100.0, width=160.0, height=160.0,
        rotation_deg=0.0, ratio_locked=before.ratio_locked,
    )
    assert b_ctx.model_provider().transform is model.transform
    assert b_ctx.model_provider().edit_session is True
