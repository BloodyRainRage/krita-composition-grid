"""Direct tests for the ``viewport_triggers`` module.

``tests/canvas/test_overlay_qt.py`` pins the triggers through the context
binding; this module pins the individual pieces plus the two new event
sources:

* ``QEvent.Paint`` on the canvas viewport — the only event-driven signal for
  changes that move NEITHER scrollbar (mirror toggle, canvas rotation via the
  menu/N-key, zoom by slider/keyboard when the document fits the viewport so
  the range stays 0..0, zoom-to-cursor that keeps the value). The viewport
  must repaint to show any of them, and our overlay is a CHILD of that
  viewport, so invalidating the child from the parent's Paint event cannot
  re-enter the parent's paint (proved below with a real child widget).
* ``QEvent.LayoutRequest`` on the located ``QAbstractScrollArea`` — the
  ``resetCanvas`` widget-swap case re-lays-out the holder.
* ``find_scroll_area`` / ``scrollbars`` / ``install`` / ``remove`` degradation
  branches: no scroll area, a wrong parent chain, an unwalkable chain, an
  absent signal, a refusing widget — each answers ``None``/``[]`` with a log
  and NEVER raises.
* The filter reads ONLY ``event.type()``: a payload-poisoned event object
  proves no scroll/wheel payload is ever dereferenced (PyQt mis-wraps
  ``QScrollEvent``; a prior probe observed heap corruption when one was
  touched).

Gated on PyQt like every Qt test module in this repo.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]

from composition_grid import qt_compat  # noqa: E402
from composition_grid.canvas import viewport_triggers  # noqa: E402


def _qt_available() -> bool:
    import importlib

    for major in (6, 5):
        try:
            importlib.import_module(f"PyQt{major}.QtWidgets")
        except ImportError:
            continue
        return True
    return False


pytestmark = pytest.mark.skipif(
    not _qt_available(), reason="PyQt not installed (uv install if needed)"
)

_MODULES = None


def _qt():
    global _MODULES
    if _MODULES is None:
        try_import_qt()
        import importlib

        _MODULES = tuple(
            importlib.import_module(f"PyQt{qt_compat.qt_major}.{name}")
            for name in ("QtWidgets", "QtCore", "QtGui")
        )
    return _MODULES


_KEEP_APP_REF = None


def _ensure_qapp():
    global _KEEP_APP_REF
    QtWidgets, _, _ = _qt()
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        _KEEP_APP_REF = QtWidgets.QApplication([])
    elif _KEEP_APP_REF is None:
        _KEEP_APP_REF = inst


def _scroll_area_pair():
    """A real ``QScrollArea`` (an actual ``QAbstractScrollArea``) with a real
    viewport, inside a shown QMainWindow — the minimal honest shape of the
    Krita canvas holder."""
    QtWidgets, _, _ = _qt()
    _ensure_qapp()
    win = QtWidgets.QMainWindow()
    scroll = QtWidgets.QScrollArea()
    inner = QtWidgets.QWidget()
    inner.setFixedSize(3000, 3000)
    scroll.setWidget(inner)
    win.setCentralWidget(scroll)
    win.resize(800, 600)
    win.show()
    QtWidgets.QApplication.processEvents()
    return win, scroll, inner


class _TypedEvent:
    """Event carrying ONLY ``type()`` — the exact surface the filter is allowed
    to touch."""

    def __init__(self, event_type):
        self._type = event_type

    def type(self):
        return self._type


class _PoisonEvent:
    """``type()`` answers, every OTHER attribute access raises — so any payload
    dereference inside the filter becomes a caught exception with a log, not a
    silent pass or a crash."""

    def __init__(self, event_type):
        self._type = event_type

    def type(self):
        return self._type

    def __getattr__(self, name):  # noqa: ANN401 — deliberate trap
        raise AssertionError(f"filter must not read payload attribute {name!r}")


# --- the trigger set ----------------------------------------------------------


def test_paint_and_layout_request_are_in_the_normalized_trigger_sets():
    """The two new event types are exported through ``qt_compat`` (never a raw
    binding path) and split across the two sets: Paint belongs to the VIEWPORT
    filter, LayoutRequest to the SCROLL-AREA holder filter."""
    assert qt_compat.Paint in viewport_triggers.TRIGGER_EVENT_TYPES
    assert qt_compat.LayoutRequest in viewport_triggers.HOLDER_EVENT_TYPES
    assert qt_compat.LayoutRequest not in viewport_triggers.TRIGGER_EVENT_TYPES
    assert qt_compat.Paint not in viewport_triggers.HOLDER_EVENT_TYPES


def test_trigger_event_names_match_the_exported_constants():
    """Name/constant drift pin: the names the diagnostics prints must resolve
    through ``qt_compat`` to exactly the sets the filters compare against."""
    viewport = frozenset(
        getattr(qt_compat, name) for name in viewport_triggers.TRIGGER_EVENT_NAMES
    )
    holder = frozenset(
        getattr(qt_compat, name) for name in viewport_triggers.HOLDER_EVENT_NAMES
    )
    assert viewport == viewport_triggers.TRIGGER_EVENT_TYPES
    assert holder == viewport_triggers.HOLDER_EVENT_TYPES
    assert {"Wheel", "Resize", "Scroll", "ScrollPrepare", "Paint"} == set(
        viewport_triggers.TRIGGER_EVENT_NAMES
    )
    assert {"LayoutRequest"} == set(viewport_triggers.HOLDER_EVENT_NAMES)


# --- the Paint trigger, with no feedback loop ---------------------------------


def test_paint_event_on_the_viewport_repaints_exactly_once_and_is_not_consumed():
    """(c) PROOF OF NO FEEDBACK LOOP, on a real QWidget scroll area: the filter
    is installed on the viewport, a genuine ``QPaintEvent`` is sent, and the
    trigger fires EXACTLY once; the filter itself returns ``False`` (never
    consumes). Then the child overlay invalidates itself and Qt runs the event
    loop: the parent viewport receives NO new Paint, so the repaint count does
    not move — invalidating a child cannot re-enter the parent's paint."""
    QtWidgets, QtCore, QtGui = _qt()
    win, scroll, _inner = _scroll_area_pair()
    viewport = scroll.viewport()
    hits = []
    try:
        handle = viewport_triggers.install(viewport, lambda: hits.append(1))
        assert handle is not None, "the filter must install on a real viewport"
        watched, flt, _remove = handle

        paint = QtGui.QPaintEvent(QtCore.QRect(0, 0, 10, 10))
        QtCore.QCoreApplication.sendEvent(viewport, paint)
        assert len(hits) == 1, f"a viewport Paint must repaint exactly once, got {hits}"

        # The same event through the filter directly: invalidates AND passes
        # the event through (the filter never consumes).
        assert flt.eventFilter(watched, QtGui.QPaintEvent(QtCore.QRect(0, 0, 10, 10))) is False
        assert len(hits) == 2, "the filter path repaints too, and returns False"

        # A real CHILD of the viewport (the overlay's position) invalidating
        # itself does NOT re-enter the parent's Paint -> no loop.
        child = QtWidgets.QWidget(viewport)
        child.setGeometry(0, 0, 50, 50)
        child.show()
        before = len(hits)
        child.update()
        QtWidgets.QApplication.processEvents()
        assert len(hits) == before, (
            "a child repaint scheduled a repaint back into the parent viewport "
            "(feedback loop) — the Paint trigger would spin forever"
        )

        # The filter must not consume: Qt still paints the viewport itself.
        assert viewport.isVisible()
    finally:
        viewport_triggers.remove(handle)
        win.close()


def test_unrelated_event_types_do_not_repaint():
    """The Paint addition must not turn the filter into a firehose: a type
    outside both sets invalidates nothing."""
    QtCore, = (_qt()[1],)
    win, scroll, _inner = _scroll_area_pair()
    try:
        hits = []
        handle = viewport_triggers.install(scroll.viewport(), lambda: hits.append(1))
        _w, flt, _r = handle
        assert flt.eventFilter(scroll.viewport(), _TypedEvent(qt_compat.Close)) is False
        assert flt.eventFilter(scroll.viewport(), _TypedEvent(qt_compat.Show)) is False
        assert hits == [], f"non-trigger types must not repaint: {hits}"
        viewport_triggers.remove(handle)
    finally:
        win.close()


def test_layout_request_on_the_scroll_area_repaints():
    """The ``resetCanvas`` widget swap re-lays-out the holder: ``LayoutRequest``
    on the scroll area goes through the holder filter and repaints once, with
    the event still passed through."""
    QtWidgets, QtCore = _qt()[0], _qt()[1]
    win, scroll, _inner = _scroll_area_pair()
    try:
        hits = []
        handle = viewport_triggers.install(
            scroll, lambda: hits.append(1), event_types=viewport_triggers.HOLDER_EVENT_TYPES
        )
        assert handle is not None
        _w, flt, _r = handle
        assert (
            flt.eventFilter(scroll, _TypedEvent(qt_compat.LayoutRequest)) is False
        ), "the holder filter must not consume LayoutRequest"
        assert hits == [1], hits
        # A viewport-only type must NOT repaint through the holder filter:
        # the two sets stay separate, or Paint would double-count.
        assert flt.eventFilter(scroll, _TypedEvent(qt_compat.Paint)) is False
        assert hits == [1], "Paint is not a holder trigger"
        # Genuine delivery through Qt's own dispatch works too (LayoutRequest
        # carries no payload for PyQt to mis-wrap).
        QtCore.QCoreApplication.sendEvent(
            scroll, QtCore.QEvent(qt_compat.LayoutRequest)
        )
        assert hits == [1, 1], "a real LayoutRequest on the holder must repaint"
        viewport_triggers.remove(handle)
    finally:
        win.close()


def test_filter_never_touches_event_payloads():
    """The pinned host hazard: PyQt mis-wraps ``QScrollEvent``/
    ``QScrollPrepareEvent`` and a prior probe observed heap corruption when a
    payload was dereferenced. A poison event proves the filter reads ONLY
    ``type()``; a raised payload access would escape as a caught-and-logged
    failure, so the count assertion below proves no attribute was touched."""
    win, scroll, _inner = _scroll_area_pair()
    try:
        hits = []
        handle = viewport_triggers.install(scroll.viewport(), lambda: hits.append(1))
        _w, flt, _r = handle
        for name in ("Wheel", "Resize", "Scroll", "ScrollPrepare", "Paint"):
            result = flt.eventFilter(scroll.viewport(), _PoisonEvent(getattr(qt_compat, name)))
            assert result is False, f"{name} must not be consumed"
        assert hits == [1] * 5, (
            f"every trigger type must invalidate without reading the payload: {hits}"
        )
        viewport_triggers.remove(handle)
    finally:
        win.close()


def test_filter_swallows_and_logs_an_exception_from_the_trigger_callback(caplog):
    """An event filter must never crash Krita: a raising callback becomes a
    logged record and the event still returns False (never consumed)."""
    import logging

    def boom():
        raise RuntimeError("overlay is gone")

    win, scroll, _inner = _scroll_area_pair()
    try:
        handle = viewport_triggers.install(scroll.viewport(), boom)
        _w, flt, _r = handle
        with caplog.at_level(logging.DEBUG, logger="composition_grid"):
            assert flt.eventFilter(scroll.viewport(), _TypedEvent(qt_compat.Paint)) is False
        assert any(
            "repaint trigger failed" in record.getMessage() for record in caplog.records
        ), caplog.messages
        viewport_triggers.remove(handle)
    finally:
        win.close()


# --- degradation branches -----------------------------------------------------


class _FakeChainWidget:
    """Duck widget whose ``parentWidget()`` walks a scripted ancestor list."""

    def __init__(self, chain):
        self._chain = list(chain)
        self._index = 0

    def parentWidget(self):
        if self._index >= len(self._chain):
            return None
        ancestor = self._chain[self._index]
        self._index += 1
        return ancestor


class _RaisingParent:
    def parentWidget(self):
        raise RuntimeError("wrapper mid-teardown")


class _ScrollAreaDuck:
    def horizontalScrollBar(self):
        return "hbar"

    def verticalScrollBar(self):
        return "vbar"


class _HalfScrollAreaDuck:
    def horizontalScrollBar(self):
        return "hbar"


class _RaisingScrollAreaDuck:
    def horizontalScrollBar(self):
        raise RuntimeError("teardown")

    def verticalScrollBar(self):
        return "vbar"


def test_find_scroll_area_walks_the_parent_chain_to_the_holder():
    holder = _ScrollAreaDuck()
    widget = _FakeChainWidget([holder])
    assert viewport_triggers.find_scroll_area(widget) is holder


def test_find_scroll_area_returns_none_for_a_wrong_parent_chain():
    """No ancestor answers the scrollbar pair (e.g. a plain layout chain):
    ``None``, no exception — the caller then wires fewer triggers."""
    widget = _FakeChainWidget([object(), object(), object()])
    assert viewport_triggers.find_scroll_area(widget) is None


def test_find_scroll_area_survives_an_unwalkable_chain_and_no_parent():
    assert viewport_triggers.find_scroll_area(_RaisingParent()) is None
    assert viewport_triggers.find_scroll_area(None) is None


def test_find_scroll_area_stops_at_the_ancestor_bound():
    """A cyclic foreign widget graph cannot spin the walk: after
    ``_MAX_ANCESTOR_STEPS`` the answer is ``None``."""
    endless = _FakeChainWidget([object()] * 50)
    assert viewport_triggers.find_scroll_area(endless) is None


def test_scrollbars_answers_both_bars_and_degrades_to_fewer():
    assert viewport_triggers.scrollbars(_ScrollAreaDuck()) == ["hbar", "vbar"]
    assert viewport_triggers.scrollbars(None) == []
    assert viewport_triggers.scrollbars(_HalfScrollAreaDuck()) == ["hbar"]
    assert viewport_triggers.scrollbars(_RaisingScrollAreaDuck()) == ["vbar"]


def test_install_degrades_with_a_log_when_the_widget_refuses_a_filter(caplog):
    """The two failure branches: a widget that has no ``installEventFilter``
    at all, and one whose call raises. Each answers ``None`` with a DEBUG note
    and never raises outward."""
    import logging

    class _NoFilterApi:
        pass

    class _RefusingFilter:
        def installEventFilter(self, flt):  # noqa: ARG002
            raise RuntimeError("foreign widget refuses the filter")

        def removeEventFilter(self, flt):  # noqa: ARG002
            return None

    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        assert viewport_triggers.install(_NoFilterApi(), lambda: None) is None
        assert viewport_triggers.install(_RefusingFilter(), lambda: None) is None
    notes = [r.getMessage() for r in caplog.records if "viewport_triggers" in r.getMessage()]
    assert len(notes) >= 2, caplog.messages


def test_remove_is_idempotent_and_exception_free():
    class _RefusingRemove:
        def removeEventFilter(self, flt):  # noqa: ARG002
            raise RuntimeError("already destroyed")

    viewport_triggers.remove(None)
    viewport_triggers.remove((_RefusingRemove(), object(), _RefusingRemove().removeEventFilter))
