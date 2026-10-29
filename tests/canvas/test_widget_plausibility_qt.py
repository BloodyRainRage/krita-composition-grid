"""Bind-time canvas-widget plausibility.

Qt-gated the same two-layer way as ``tests.canvas.test_overlay_qt`` /
``tests.canvas.test_canvas_locator_qt``: module-level ``skipif`` from a
non-raising importlib probe plus an autouse per-test ``try_import_qt`` gate;
``QT_QPA_PLATFORM=offscreen`` before any Qt import.

The check under test, :func:`is_plausible_canvas_widget`, is the sanity gate
the locator's lenient fallback needs: that fallback can resolve the
QMdiArea's own viewport or a DOCK's scroll-area viewport, and parenting the
overlay there paints the grid nowhere and — inside an edit session — steals
mouse input from dock chrome.
Pinned rules (each one a real Qt fact, documented in the module):

* the widget must be a ``QWidget`` AND a child (the overlay is a
  CHILD of the canvas widget — a childless top-level is rejected);
* no ``QDockWidget`` anywhere in the ``parentWidget()`` chain — a docker's
  scroll-area viewport is precisely the lenient-fallback impostor;
* no ``QMdiArea`` in the chain BEFORE a ``QMdiSubWindow`` was passed — the
  QMdiArea's own viewport is the MDI-background
  impostor of the same fallback: dock-free, above the size floor, and only
  reachable when no document subwindow is active;
* size floor ``MIN_CANVAS_WIDGET_SIZE_PX`` == 24 px, derived DRY-style from
  ``handle_layout.ROTATE_HANDLE_OFFSET_PX`` (the rotate handle rides 24 widget
  px beyond the top edge, so a widget that small cannot even display the
  handle layout).

Fixtures are built with REAL Qt widgets (QMainWindow / QDockWidget /
QAbstractScrollArea) exactly like ``test_canvas_locator_qt`` so the impostor
geometry is genuine, not mocked.
"""

from __future__ import annotations

import importlib
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
from composition_grid.canvas.widget_plausibility import (
    MIN_CANVAS_WIDGET_SIZE_PX,
    is_plausible_canvas_widget,
)
from composition_grid.presentation.handle_layout import ROTATE_HANDLE_OFFSET_PX


def _qt_available() -> bool:
    """Non-raising probe mirroring ``try_import_qt`` order (PyQt6 first);
    probes the ``QtWidgets`` SUBMODULE so a half-removed install reads as
    unavailable (same pattern as the sibling Qt test modules)."""
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

_KEEP_APP_REF = None


def _ensure_qapp():
    """Shared QApplication singleton, kept referenced for the whole test
    lifetime (the pinned PyQt6 teardown gotcha, as in the sibling modules)."""
    global _KEEP_APP_REF
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        _KEEP_APP_REF = QtWidgets.QApplication([])
    elif _KEEP_APP_REF is None:
        _KEEP_APP_REF = inst


@pytest.fixture(autouse=True)
def _qt_gate():
    """Per-test gate through the shared conftest helper."""
    try_import_qt()
    yield


def _main_window(size=(900, 600)):
    """Plain QMainWindow with a bare central widget (NO MDI chain — the
    geometry the lenient locator fallback is designed for)."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(QtWidgets.QWidget())
    win.resize(*size)
    win.show()
    return win


# --- pinned: the plausible case ----------------------------------------------


def test_scroll_viewport_in_central_area_is_plausible():
    """A real scroll-area viewport that IS a child inside the shown window's
    central area and big enough -> plausible, empty reason. This is the
    dominant-area outcome the check must never reject."""
    win = _main_window()
    try:
        scroll = QtWidgets.QTextEdit(win.centralWidget())
        scroll.resize(400, 300)
        scroll.show()
        plausible, reason = is_plausible_canvas_widget(scroll.viewport())
        assert plausible is True, reason
        assert reason == ""
    finally:
        win.close()


# --- pinned: the dock impostor -----------------------------------------------


def test_dock_nested_scroll_area_viewport_is_rejected():
    """A QDockWidget's scroll-area viewport — the lenient fallback's classic
    impostor (bigger than the size floor, so ONLY the dock rule can catch it)
    — is rejected with a reason naming QDockWidget."""
    win = _main_window()
    dock = QtWidgets.QDockWidget()
    try:
        scroll = QtWidgets.QTextEdit()
        scroll.resize(300, 300)
        dock.setWidget(scroll)
        win.addDockWidget(
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea if hasattr(
                QtCore.Qt, "DockWidgetArea"
            ) else QtCore.Qt.RightDockWidgetArea,
            dock,
        )
        dock.show()
        viewport = scroll.viewport()
        # genuine impostor geometry: above the size floor, under a QDockWidget
        assert viewport.width() >= MIN_CANVAS_WIDGET_SIZE_PX
        assert viewport.height() >= MIN_CANVAS_WIDGET_SIZE_PX
        plausible, reason = is_plausible_canvas_widget(viewport)
        assert plausible is False
        assert "QDockWidget" in reason, reason
    finally:
        win.close()


# --- pinned: the MDI-AREA impostor (the QMdiArea's own viewport) --------------


def _mdi_window(active_subwindow=False):
    """A ``QMainWindow`` shaped like Krita's MDI workspace: a ``QMdiArea`` as
    the central widget (900x600 class geometry). With ``active_subwindow`` a
    document scroll area is installed in a ``QMdiSubWindow`` and activated —
    the shape the primary locator chain resolves; without it the chain stalls
    and the lenient fallback answers the QMdiArea's OWN viewport (``QMdiArea``
    IS a ``QAbstractScrollArea``), the impostor this rule rejects."""
    win = _main_window()
    mdi = QtWidgets.QMdiArea(win.centralWidget())
    mdi.setGeometry(0, 0, 880, 580)
    mdi.show()
    scroll = None
    if active_subwindow:
        sub = QtWidgets.QMdiSubWindow(mdi)
        scroll = QtWidgets.QTextEdit()
        sub.setWidget(scroll)
        mdi.addSubWindow(sub)
        sub.setGeometry(10, 10, 500, 400)
        sub.show()
        mdi.setActiveSubWindow(sub)
    return win, mdi, scroll


def test_mdi_area_own_viewport_is_rejected():
    """The QMdiArea's OWN viewport (no ``QMdiSubWindow`` in its parent chain)
    is the floating/subwindow-mode impostor: above the size floor and outside
    any dock, so ONLY the MDI rule can catch it. Rejected with a reason naming
    QMdiArea, because parenting the overlay there paints the grid over the MDI
    background with the wrong doc->widget coordinates."""
    win, mdi, _scroll = _mdi_window()
    try:
        viewport = mdi.viewport()
        # genuine impostor geometry: big, dock-free, MDI-background child
        assert viewport.width() >= MIN_CANVAS_WIDGET_SIZE_PX
        assert viewport.height() >= MIN_CANVAS_WIDGET_SIZE_PX
        plausible, reason = is_plausible_canvas_widget(viewport)
        assert plausible is False
        assert "QMdiArea" in reason, reason
        assert "QMdiSubWindow" in reason, reason
    finally:
        win.close()


def test_canvas_viewport_inside_active_subwindow_stays_plausible():
    """Non-regression for the real Krita shape: a scroll-area viewport whose
    chain passes through a ``QMdiSubWindow`` BEFORE reaching the ``QMdiArea``
    IS the document canvas -> plausible with an empty reason (the MDI rule
    must not reject the primary chain it was written next to)."""
    win, _mdi, scroll = _mdi_window(active_subwindow=True)
    try:
        viewport = scroll.viewport()
        assert viewport.width() >= MIN_CANVAS_WIDGET_SIZE_PX
        assert viewport.height() >= MIN_CANVAS_WIDGET_SIZE_PX
        plausible, reason = is_plausible_canvas_widget(viewport)
        assert plausible is True, reason
        assert reason == ""
    finally:
        win.close()


# --- pinned: the size floor ----------------------------------------------------


def test_undersized_central_widget_is_rejected():
    """A 10x10 px child inside the central area cannot host the handle
    layout (rotate anchor alone rides 24 px off the top edge) -> rejected
    with the floor value in the reason."""
    win = _main_window()
    try:
        tiny = QtWidgets.QWidget(win.centralWidget())
        tiny.resize(10, 10)
        plausible, reason = is_plausible_canvas_widget(tiny)
        assert plausible is False
        assert "24" in reason, reason
    finally:
        win.close()


# --- pinned: top-level (the overlay must be a CHILD) -------------------------


def test_childless_top_level_widget_is_rejected():
    """A large parentless widget is a top-level window — rejected as
    top-level even though it clears the size floor (the overlay must be a
    child of the canvas widget)."""
    _ensure_qapp()
    top = QtWidgets.QWidget()
    top.resize(500, 400)
    plausible, reason = is_plausible_canvas_widget(top)
    assert plausible is False
    assert "top-level" in reason, reason


# --- pinned: degenerate inputs -------------------------------------------------


def test_non_widget_and_none_are_rejected():
    """The helper never raises on foreign objects: ``None`` and a plain
    Python object are rejected as not-a-QWidget."""
    plausible, reason = is_plausible_canvas_widget(None)
    assert plausible is False
    assert "not a QWidget" in reason, reason
    plausible, reason = is_plausible_canvas_widget(object())
    assert plausible is False
    assert "not a QWidget" in reason, reason


# --- pinned: the floor constant itself -----------------------------------------


def test_min_canvas_floor_matches_rotate_offset():
    """MIN_CANVAS_WIDGET_SIZE_PX is pinned to 24 px and derived from the
    single source of that figure — ``ROTATE_HANDLE_OFFSET_PX`` (the handle
    layout minimum) — so the two can never drift apart."""
    assert MIN_CANVAS_WIDGET_SIZE_PX == 24.0
    assert MIN_CANVAS_WIDGET_SIZE_PX == ROTATE_HANDLE_OFFSET_PX
