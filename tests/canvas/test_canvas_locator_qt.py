"""Qt tests for the canvas widget locator.

ALL tests are gated on PyQt availability with the same two-layer gate as
``tests.presentation.test_overlay_painter_qt``: a ``pytest.mark.skipif``
evaluated from a non-raising importlib probe (a clean whole-module SKIP
without PyQt — ``pytest.skip`` cannot be called during module import), plus
an autouse fixture calling the shared ``tests.conftest.try_import_qt()``
inside test scope (legal ``skip``) so each test stays gated even if Qt
disappears between collection and run. ``QT_QPA_PLATFORM=offscreen`` is set
before any Qt import runs.

Semantics implemented here:

* ``find_canvas_widget`` on a real ``QMainWindow`` whose central widget is a
  ``QMdiArea`` with one ACTIVE subwindow holding a generic
  ``QAbstractScrollArea`` returns exactly that scroll area's ``viewport()``
  (identity ``is`` comparison on the live widget).
* ``find_canvas_widget(None)`` → ``None`` (no crash, INFO only).
* A plain ``QMainWindow`` WITHOUT any ``QMdiArea`` and WITHOUT any
  ``QAbstractScrollArea`` → ``None`` ENFORCED: the requirement is
  "не падает, возвращает None" on a window without Krita. (Note: when a
  ``QAbstractScrollArea`` DOES exist somewhere under the window, the
  documented lenient fallback deliberately returns its viewport instead —
  tested separately below.)
* ``QMdiArea`` present but NO active subwindow (or no subwindows at all) →
  the primary chain stalls and the lenient fallback (window-wide
  ``findChild(QAbstractScrollArea)``) answers ``mdi.viewport()`` — because
  ``QMdiArea`` is ITSELF a ``QAbstractScrollArea``, the window-level
  ``findChild`` reaches it before any inner content (BFS). True ``None``
  requires NO scroll area under the window at all (plain QMainWindow test
  below). Documented degradation, never a crash.
* Non-widget QObject → ``None`` gracefully.
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

from composition_grid.canvas.canvas_locator import find_canvas_widget


def _qt_available() -> bool:
    """Non-raising probe that mirrors ``tests.conftest.try_import_qt`` order
    (PyQt6 first, then PyQt5). Never calls ``pytest.skip``: safe at import.

    Probes the ``PyQt{major}.QtWidgets`` SUBMODULE, not the bare package: a
    partially-removed install can leave the bare ``PyQt6`` package importable
    while actual bindings are gone, which must read as "not available"
    (same pattern as ``tests.presentation.test_overlay_painter_qt``)."""
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


@pytest.fixture(autouse=True)
def _qt_gate():
    """Per-test gate through the shared conftest helper: inside test scope its
    ``pytest.skip`` is legal, so a disappearing PyQt still skips cleanly."""
    try_import_qt()
    yield


_KEEP_APP_REF = None


def _ensure_qapp():
    """Create the shared QApplication singleton once (offscreen platform).

    PyQt6 GOTCHA (root cause of the transient process aborts): the created
    ``QApplication`` wrapper MUST be kept referenced for the whole test
    lifetime — a helper-local wrapper dies at return, whereupon PyQt deletes
    the underlying C++ application and any later widget construction hits
    Qt's qFatal "QWidget: Must construct a QApplication before a QWidget"
    (fast-fail 0xC0000409 killing the whole pytest process). The module
    global holds the reference explicitly; ``instance()`` alone is NOT
    reliable when another module created the app without keeping its
    wrapper (its C++ object would already be destroyed)."""
    global _KEEP_APP_REF
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        # No live app: create one AND hold the wrapper at module level so
        # PyQt never deletes the C++ application mid-suite (see docstring).
        _KEEP_APP_REF = QtWidgets.QApplication([])
    elif _KEEP_APP_REF is None:
        # App already exists (created by another module that kept its own
        # wrapper): alias our reference to it.
        _KEEP_APP_REF = inst


def _cleanup(widget):
    """Schedule destruction of a created window/widget so tests stay isolated."""
    if widget is not None and hasattr(widget, "deleteLater"):
        widget.deleteLater()


@pytest.fixture
def mdi_window():
    """A ``QMainWindow`` shaped like Krita: ``QMdiArea`` central widget, one
    active subwindow whose content widget is a generic
    ``QAbstractScrollArea`` (``QTextEdit`` — never a Krita class)."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    mdi = QtWidgets.QMdiArea()
    win.setCentralWidget(mdi)
    sub = QtWidgets.QMdiSubWindow()
    scroll = QtWidgets.QTextEdit()  # QAbstractScrollArea subclass, generic Qt
    sub.setWidget(scroll)
    mdi.addSubWindow(sub)
    mdi.setActiveSubWindow(sub)
    win.show()
    yield win, mdi, sub, scroll
    win.close()
    _cleanup(win)


def test_locator_returns_viewport_of_active_subwindow(mdi_window):
    """Primary chain win→findChild(QMdiArea)→activeSubWindow→
    findChild(QAbstractScrollArea)→viewport() must return THE viewport of the
    scroll area inside the active subwindow (identity, not a copy)."""
    win, _mdi, _sub, scroll = mdi_window
    result = find_canvas_widget(win)
    assert result is scroll.viewport(), (
        "locator must return the live viewport widget of the active subwindow"
    )


def test_none_window_returns_none():
    """``find_canvas_widget(None)`` → ``None``. The one-liner INFO branch:
    no exception, nothing printed, merely not located."""
    assert find_canvas_widget(None) is None


def test_plain_qmainwindow_without_krita_returns_none():
    """The degradation rule: a plain ``QMainWindow`` without any
    ``QMdiArea`` anywhere and without any scroll area must NOT crash and must
    return ``None`` — degradation ("grid hidden"), not failure. A bare
    ``QWidget`` central widget guarantees neither chain step can succeed:
    no QMdiArea → primary chain stalls; no QAbstractScrollArea → the lenient
    fallback stalls too."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(QtWidgets.QWidget())
    win.show()
    try:
        assert find_canvas_widget(win) is None, (
            "plain QMainWindow must degrade to None (no crash)"
        )
    finally:
        win.close()
        _cleanup(win)


def test_no_active_subwindow_falls_back_leniently(mdi_window):
    """``QMdiArea`` present but NO active subwindow: the primary chain stalls
    at ``activeSubWindow()`` (documented DEBUG fallback), so the lenient
    window-wide ``findChild(QAbstractScrollArea)`` branch answers NON-None
    and non-crashingly.

    WHICH widget answers here is a documented nuance pinned by this test:
    ``QMdiArea`` is ITSELF a ``QAbstractScrollArea`` subclass, so the
    window-level ``findChild`` finds the MDI AREA first (BFS before any
    deeper subwindow content) and the lenient branch returns
    ``mdi.viewport()`` — NOT the inactive subwindow's QTextEdit viewport.
    (Only when nothing answerable exists at all does lenient yield None —
    see test_plain_qmainwindow_without_krita_returns_none.)"""
    win, mdi, _sub, _scroll = mdi_window
    win.centralWidget().setActiveSubWindow(None)  # stall the primary chain
    assert find_canvas_widget(win) is mdi.viewport(), (
        "lenient fallback must answer the first window-level scroll area "
        "(the QMdiArea itself) rather than crash or return None"
    )


def test_non_widget_object_returns_none():
    """A QObject that is NOT a QWidget must yield ``None`` gracefully (the
    locator type-guards before touching widget-only APIs)."""
    _ensure_qapp()
    dummy = QtCore.QObject()
    try:
        assert find_canvas_widget(dummy) is None
    finally:
        _cleanup(dummy)


def test_locator_prints_nothing_and_logs(caplog):
    """Contract: ``find_canvas_widget`` PRINTS nothing; it logs each step to
    the ``composition_grid`` logger. The None-window branch must produce an
    INFO record mentioning the locator, and stdout must stay empty."""
    import io
    import contextlib

    with caplog.at_level("DEBUG", logger="composition_grid"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = find_canvas_widget(None)
    assert result is None
    assert buf.getvalue() == "", "locator must print nothing to stdout"
    assert any(
        rec.levelno >= 10 and "canvas" in rec.getMessage().lower()
        for rec in caplog.records
    ), "None-window branch must log an INFO one-liner about not locating the canvas"


def test_mdi_without_subwindows_still_answers_via_lenient_branch():
    """Documented unproducibility-of-None nuance: even an MDI area with NO
    subwindows at all (nothing canvas-like anywhere) does NOT degrade to
    None, because the lenient window-wide ``findChild(QAbstractScrollArea)``
    finds ``QMdiArea`` ITSELF (a ``QAbstractScrollArea`` subclass) and
    returns ``mdi.viewport()``. A TRUE ``None`` outcome requires an ancestor
    window with neither an ``QMdiArea`` nor any other scroll area anywhere
    — guaranteed by test_plain_qmainwindow_without_krita_returns_none. The
    locator contract here is only "answers a sane QWidget or None, never a
    crash", which this asserts."""
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    mdi = QtWidgets.QMdiArea()  # no subwindows added at all
    win.setCentralWidget(mdi)
    win.show()
    try:
        assert find_canvas_widget(win) is mdi.viewport(), (
            "empty QMdiArea must answer its own viewport via the lenient "
            "fallback (it is a QAbstractScrollArea itself), never crash"
        )
    finally:
        win.close()
        _cleanup(win)
