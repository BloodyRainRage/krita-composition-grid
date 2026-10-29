"""Real-Qt regressions for the live Krita 5.3 borrowed-createActions-Window crash."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import types
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]

import composition_grid.plugin_composition_grid as plugin
from composition_grid import plugin_support
from composition_grid.canvas import viewport_triggers
from composition_grid.canvas.canvas_context import CanvasContext
from composition_grid.canvas.overlay import GridOverlay
from composition_grid.core.grids.grid_registry import all_types
from composition_grid.core.transform_state import TransformState, def_element
from composition_grid.model.grid_model import GridStateService

DOC_W, DOC_H = 800.0, 600.0
GRID_IDS = [cls.id for cls in all_types()]
ADD = "composition_grid_add"
TOGGLE = "composition_grid_toggle"
EDIT = "composition_grid_edit_session"
RESET = "composition_grid_reset_transform"
CYCLE = "composition_grid_cycle_type"


def _qt_available() -> bool:
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
_APP_REF = None


def _qt():
    """Non-raising lazy import (PyQt6 first, then PyQt5), offscreen app kept."""
    global _MODULES, _APP_REF
    if _MODULES is None:
        try_import_qt()
        for major in (6, 5):
            try:
                _MODULES = tuple(
                    importlib.import_module(f"PyQt{major}.{name}")
                    for name in ("QtWidgets", "QtCore", "QtGui")
                )
            except ImportError:
                continue
            break
        if _MODULES[0].QApplication.instance() is None:
            _APP_REF = _MODULES[0].QApplication([])
    return _MODULES


def _drain_deferred_deletes():
    _QtWidgets, QtCore, _QtGui = _qt()
    deferred = getattr(QtCore.QEvent, "Type", QtCore.QEvent).DeferredDelete
    QtCore.QCoreApplication.sendPostedEvents(None, deferred)
    QtCore.QCoreApplication.processEvents()


_HOST = None


def _host():
    """Lazily build the Qt-dependent host doubles (classes need QtCore)."""
    global _HOST
    if _HOST is None:
        QtWidgets, QtCore, QtGui = _qt()
        action_cls = getattr(QtGui, "QAction", None) or getattr(QtWidgets, "QAction")

        class _Point:
            def __init__(self, x, y):
                self._v = (x, y)

            def x(self):
                return self._v[0]

            def y(self):
                return self._v[1]

        class FakeDocument:
            def __init__(self, name, w=DOC_W, h=DOC_H):
                self._name = name
                self._w = float(w)
                self._h = float(h)

            def name(self):
                return self._name

            def fileName(self):
                return ""

            def width(self):
                return self._w

            def height(self):
                return self._h

        class KisCanvas:
            """Krita 5.3 Canvas: view GETTERS only, no signals at all."""

            def __init__(self, rect_w=640.0, rect_h=480.0):
                self._w = float(rect_w)
                self._h = float(rect_h)

            def rotation(self):
                return 0.0

            def mirror(self):
                return False

            def zoomLevel(self):
                return 1.0

            def preferredCenter(self):
                return _Point(self._w / 2.0, self._h / 2.0)

        class KisView:
            def __init__(self, document, canvas):
                self._document = document
                self._canvas = canvas

            def twin(self):
                return KisView(self._document, self._canvas)

            def document(self):
                return self._document

            def canvas(self):
                return self._canvas

        class KisWindow(QtCore.QObject):
            """libkis Window: a real QObject, so access raises once it is deleted."""

            activeViewChanged = QtCore.pyqtSignal()

            def __init__(self, entry):
                super().__init__()
                self._entry = entry
                entry.track_wrapper(self)

            def _check(self):
                # a Python method body survives the C++ deletion; objectName not
                self.objectName()

            def qwindow(self):
                self._check()
                return self._entry.qwindow

            def activeView(self):
                self._check()
                view = self._entry.view
                return view.twin() if view is not None else None

            def createAction(self, action_id, text="", menu_location=""):  # noqa: N802
                # libkis parents the QAction on the MAIN WINDOW, not on this wrapper
                self._check()
                action = action_cls(text, self._entry.qwindow)
                action.setObjectName(action_id)
                self._entry.created_actions.append(action)
                return action

            def kill(self):
                self.deleteLater()
                _drain_deferred_deletes()

        class KisMainWindow(QtWidgets.QMainWindow):
            """The real mainwindow, forwarding its view changes into live wrappers."""

            viewChanged = QtCore.pyqtSignal()

            def __init__(self):
                super().__init__()
                self._wrappers = []
                self.dead_skips = 0
                self.viewChanged.connect(self._forward)

            def track(self, wrapper):
                """Weakly, so tracking never keeps a Window wrapper alive."""
                self._wrappers.append(weakref.ref(wrapper))

            def _forward(self, *_args):
                for ref in list(self._wrappers):
                    wrapper = ref()
                    if wrapper is None:
                        continue
                    try:
                        wrapper.activeViewChanged.emit()
                    except RuntimeError:
                        self.dead_skips += 1

        class Entry:
            def __init__(self, doc_id, with_view=True, doc_w=DOC_W, doc_h=DOC_H):
                self.qwindow = KisMainWindow()
                self.qwindow.resize(800, 600)
                self.qwindow.show()
                self.created_actions = []
                self.document = FakeDocument(doc_id, doc_w, doc_h)
                self.canvas = KisCanvas()
                self.view = KisView(self.document, self.canvas) if with_view else None
                self._closed = False
                self.qwindow.destroyed.connect(self._mark_closed)

            def track_wrapper(self, wrapper):
                """The mainwindow notifies the Window objects of this window."""
                self.qwindow.track(wrapper)

            def notify_view_changed(self):
                """What Krita does on a view switch: a no-arg Window signal."""
                self.qwindow.viewChanged.emit()

            @property
            def closed(self):
                return self._closed

            def _mark_closed(self, *_args):
                self._closed = True

            def shutdown(self):
                if self._closed:
                    return
                self._closed = True
                self.qwindow.close()
                self.qwindow.deleteLater()
                _drain_deferred_deletes()

        _HOST = {
            "QtWidgets": QtWidgets,
            "QtCore": QtCore,
            "Entry": Entry,
            "KisWindow": KisWindow,
            "KisView": KisView,
            "KisCanvas": KisCanvas,
            "FakeDocument": FakeDocument,
        }
    return _HOST


def _viewport_of(entry):
    """The Krita-shaped MDI subwindow viewport under this window (built once)."""
    host = _host()
    QtWidgets = host["QtWidgets"]
    mdi = entry.qwindow.findChild(QtWidgets.QMdiArea)
    if mdi is None:
        mdi = QtWidgets.QMdiArea()
        entry.qwindow.setCentralWidget(mdi)
        sub = QtWidgets.QMdiSubWindow()
        scroll = QtWidgets.QTextEdit()
        # Python-created bars: PyQt only allows the protected receivers() that
        # _counts() needs on objects created from Python (Linux CI enforces it;
        # bars Qt creates internally raise RuntimeError there).
        qt = host["QtCore"].Qt
        orientation = getattr(qt, "Orientation", qt)
        scroll.setHorizontalScrollBar(QtWidgets.QScrollBar(orientation.Horizontal))
        scroll.setVerticalScrollBar(QtWidgets.QScrollBar(orientation.Vertical))
        sub.setWidget(scroll)
        mdi.addSubWindow(sub)
        mdi.setActiveSubWindow(sub)
        host["QtCore"].QCoreApplication.processEvents()
        return scroll.viewport()
    return mdi.currentSubWindow().widget().viewport()


def _overlays(viewport):
    return viewport.findChildren(GridOverlay)


def _bars_of(viewport):
    """The real scrollbars of the scroll area the PRODUCTION locator picks."""
    area = viewport_triggers.find_scroll_area(viewport)
    return list(viewport_triggers.scrollbars(area))


def _counts(bars):
    """QTextEdit wires its own scrollbar slots, so counts are only baseline deltas."""
    return {
        bar: (bar.receivers(bar.valueChanged), bar.receivers(bar.rangeChanged))
        for bar in bars
    }


def _errors(caplog, needle):
    """ERROR records mentioning *needle*: a swallowed handler failure logs one."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and needle in r.getMessage()
    ]


def _infos(caplog, needle):
    """INFO records mentioning *needle*: the logged no-op is the degradation."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and needle in r.getMessage()
    ]


def _by_id(entry):
    """``objectName -> QAction`` for the six real actions of one window."""
    return {action.objectName(): action for action in entry.created_actions}


class FakeKritaApp:
    """``Krita.instance()``: fresh independently owned wrappers, like libkis."""

    def __init__(self, host):
        self.entries = []
        self.active_entry = None
        self.exts = []
        self.settings = {}
        self._host = host

    def windows(self):
        """The wrappers of the OPEN windows only — a closed qwindow is gone."""
        return [
            self._host["KisWindow"](entry)
            for entry in self.entries
            if not entry.closed
        ]

    def activeWindow(self):
        if self.active_entry is None:
            return None
        return self._host["KisWindow"](self.active_entry)

    def readSetting(self, group, name, default=""):  # noqa: N802
        return self.settings.get((group, name), default)

    def writeSetting(self, group, name, value):  # noqa: N802
        self.settings[(group, name)] = value


def _extension(mod, app, service=None):
    ext = mod.CompositionGridExtension(app, service=service or GridStateService())
    app.exts.append(ext)
    return ext


@pytest.fixture
def fake_host():
    """Fake ``krita`` module + reloaded entry; teardown never swallows a failure."""
    host = _host()
    app = FakeKritaApp(host)

    fake = types.ModuleType("krita")
    fake.Extension = type(
        "Extension", (object,), {"__init__": lambda self, instance=None: None}
    )

    class _Krita:
        @staticmethod
        def instance():
            return app

    fake.Krita = _Krita
    saved = sys.modules.get("krita")
    sys.modules["krita"] = fake
    mod = importlib.reload(plugin)

    entries = []
    orig_entry = host["Entry"]

    def tracked_entry(*args, **kwargs):
        entry = orig_entry(*args, **kwargs)
        entries.append(entry)
        app.entries.append(entry)
        return entry

    host["Entry"] = tracked_entry
    try:
        yield mod, app, host
    finally:
        host["Entry"] = orig_entry
        try:
            for ext in app.exts:
                for context in ext.contexts:
                    context.unbind()
        finally:
            try:
                for entry in entries:
                    entry.shutdown()
                _drain_deferred_deletes()
            finally:
                if saved is None:
                    del sys.modules["krita"]
                else:
                    sys.modules["krita"] = saved
                importlib.reload(plugin)


def _wire_entry(app, host, ext, doc_id, **kwargs):
    """Viewport first, then createActions — Krita registers a window once its UI is up."""
    entry = host["Entry"](doc_id, **kwargs)
    _viewport_of(entry)
    wrapper = host["KisWindow"](entry)
    ext.createActions(wrapper)
    app.active_entry = entry
    return entry, wrapper


def test_actions_still_mutate_the_model_after_the_callback_wrapper_is_deleted(
    fake_host, caplog
):
    """(a) The six real QActions still drive THIS window's grid after the wrapper dies."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry, borrowed = _wire_entry(app, host, ext, "borrowed.png")
    viewport = _viewport_of(entry)
    context = ext.contexts[0]
    by_id = {action.objectName(): action for action in entry.created_actions}
    by_id[ADD].trigger()
    model = ext.service.selected("borrowed.png")
    assert model is not None and model.visible is True, "the Add action created it"
    assert context.overlay is not None and len(_overlays(viewport)) == 1
    assert context.view_state is not None, "usable state before the wrapper dies"

    borrowed.kill()
    model.transform = TransformState(12.0, 34.0, 56.0, 78.0, 25.0, False)
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        by_id[TOGGLE].trigger()
        assert model.visible is False, "toggle flips the model after the death"
        by_id[EDIT].trigger()
        assert model.edit_session is True, "edit session starts after the death"
        by_id[RESET].trigger()
        assert model.transform == def_element(None, DOC_W, DOC_H)
        by_id[CYCLE].trigger()
        assert model.grid_type_id == GRID_IDS[1], "cycle moves the type"
    dead = _errors(caplog, "failed")
    assert not dead, f"an action died inside the swallow: {dead}"
    assert len(ext.contexts) == 1, "no second context may spawn for the dead wrapper"
    assert context.overlay is not None and len(_overlays(viewport)) == 1
    assert context.view_state is not None, "the kept context stayed usable"


def test_two_window_actions_route_their_own_window_when_borrowed_wrapper_dead(
    fake_host,
):
    """(a2) A's action routes A by qwindow identity while B is the active window."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry_a, wrapper_a = _wire_entry(app, host, ext, "docA.png")
    entry_b, _wrapper_b = _wire_entry(app, host, ext, "docB.png")
    wrapper_a.kill()
    service = ext.service
    assert service.active_doc_id == "docB.png", "B was wired last"
    id_a = {action.objectName(): action for action in entry_a.created_actions}
    id_b = {action.objectName(): action for action in entry_b.created_actions}
    id_a[ADD].trigger()
    id_b[ADD].trigger()
    model_a = service.selected("docA.png")
    model_b = service.selected("docB.png")
    assert model_a is not None and model_b is not None, "each window added its own grid"

    id_a[TOGGLE].trigger()
    assert service.active_doc_id == "docA.png", "A's action routes A"
    assert model_a.visible is False
    assert model_b.visible is True, "B's model stays untouched"
    assert len(ext.contexts) == 2, "no third context may appear"


def test_wake_up_binds_after_deleted_callback_wrapper_and_late_view(fake_host):
    """(b) Late view plus dead wrapper: one wake-up binds exactly one usable overlay."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry = host["Entry"]("late.png", with_view=False, doc_w=1024.0, doc_h=768.0)
    borrowed = host["KisWindow"](entry)
    ext.createActions(borrowed)
    context = ext.contexts[0]
    assert context.widget is None and context.overlay is None
    borrowed.kill()

    viewport = _viewport_of(entry)
    entry.document = host["FakeDocument"]("late.png", 1024.0, 768.0)
    entry.canvas = host["KisCanvas"]()
    entry.view = host["KisView"](entry.document, entry.canvas)
    app.active_entry = entry
    ext.handle_canvas_changed(None)

    assert context.widget is viewport, "bound to the located viewport"
    assert len(_overlays(viewport)) == 1
    assert context.overlay is _overlays(viewport)[0]
    assert isinstance(context.overlay, GridOverlay)
    assert context.doc_size == (1024.0, 768.0)
    state = context.view_state
    assert state is not None, "computed ViewState from viewport + document metrics"
    assert state.doc_width == 1024.0 and state.doc_height == 768.0
    assert ext.service.is_routed("late.png"), "the wake-up routed the document"
    assert ext.service.grids("late.png") == (), "routing never invents a grid"


def test_wake_up_adopts_a_live_wrapper_without_churning_the_healthy_overlay(
    fake_host,
):
    """(b2) Dead stored wrapper plus a new document: a live wrapper is adopted, kept."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry, _borrowed = _wire_entry(app, host, ext, "first.png")
    viewport = _viewport_of(entry)
    context = ext.contexts[0]
    assert len(_overlays(viewport)) == 1
    overlay_before = context.overlay

    stored = getattr(context, "window", None) or getattr(context, "_window", None)
    assert stored is not None
    stored.kill()

    entry.document = host["FakeDocument"]("second.png", 1024.0, 768.0)
    entry.canvas = host["KisCanvas"]()
    entry.view = host["KisView"](entry.document, entry.canvas)
    app.active_entry = entry
    ext.handle_canvas_changed(None)

    assert context.overlay is overlay_before, "same widget -> same overlay"
    assert len(_overlays(viewport)) == 1, "no overlay churn"
    assert context.doc_size == (1024.0, 768.0), "the context state followed the new doc"
    state = context.view_state
    assert state is not None and state.doc_width == 1024.0
    assert ext.service.is_routed("second.png"), "the adopted view routed its document"
    assert ext.service.grids("second.png") == (), "routing never invents a grid"


def test_unbind_with_dead_and_live_sources_releases_everything(fake_host):
    """(c) Mixed teardown: drains bookkeeping, disconnects live bars, disposes overlay."""
    host = _host()
    entry = host["Entry"]("mixed.png")
    viewport = _viewport_of(entry)
    pre_bars = _bars_of(viewport)
    baseline = _counts(pre_bars)
    service = GridStateService()
    context = CanvasContext(service)
    wrapper = host["KisWindow"](entry)
    assert context.bind(wrapper, entry.view.twin(), entry.canvas) is True
    overlay = context.overlay
    assert overlay is not None and overlay.parentWidget() is viewport
    bars = context.trigger_status["bars"]
    assert bars and all(bar in baseline for bar in bars), (
        "the installed bars are the located scroll area's own"
    )
    for bar in bars:
        base = baseline[bar]
        assert bar.receivers(bar.valueChanged) == base[0] + 1
        assert bar.receivers(bar.rangeChanged) == base[1] + 1
    assert wrapper.receivers(wrapper.activeViewChanged) == 1

    wrapper.kill()
    context.unbind()  # must not raise: only one source is dead

    assert context._connections == [], "bookkeeping drained despite the dead source"
    for bar in bars:
        base = baseline[bar]
        assert bar.receivers(bar.valueChanged) == base[0], "live slots disconnected"
        assert bar.receivers(bar.rangeChanged) == base[1]
    assert context._event_filters == [], "filters released"
    assert overlay.parentWidget() is None and not overlay.isVisible(), (
        "the overlay was disposed, not abandoned"
    )
    context.unbind()  # repeat-safe

    fresh = host["KisWindow"](entry)
    assert context.bind(fresh, entry.view.twin(), entry.canvas) is True
    assert context.overlay is not None and context.overlay is not overlay
    for bar in _bars_of(viewport):
        base = baseline[bar]
        assert bar.receivers(bar.valueChanged) == base[0] + 1
    assert len(_overlays(viewport)) == 1
    context.unbind()
    assert _counts(_bars_of(viewport)) == baseline


def test_qwindow_destruction_prunes_without_exceptions(fake_host, caplog):
    """(d) The qwindow itself dies: the prune finishes quietly, B keeps working."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry_a, wrapper_a = _wire_entry(app, host, ext, "gone.png")
    entry_b, wrapper_b = _wire_entry(app, host, ext, "stays.png")
    viewport_a = _viewport_of(entry_a)
    context_a = next(c for c in ext.contexts if c.widget is viewport_a)
    assert context_a.overlay is not None

    # the live crash state: a dead startup wrapper over a still-open window
    wrapper_a.kill()
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        entry_a.qwindow.deleteLater()
        _drain_deferred_deletes()

    failed = _errors(caplog, "unbinding")
    assert not failed, f"the prune raised through the teardown: {failed}"
    assert len(ext.contexts) == 1, "exactly the dead window's holder entry left"
    assert context_a.overlay is None, "its context unbound completely"
    assert context_a._connections == [] and context_a._event_filters == []

    app.active_entry = entry_b
    service = ext.service
    by_id = {action.objectName(): action for action in entry_b.created_actions}
    by_id[ADD].trigger()
    model_b = service.selected("stays.png")
    assert model_b is not None, "the surviving window can still add a grid"
    by_id[TOGGLE].trigger()
    assert model_b.visible is False, "the surviving window's actions still work"


def test_repeated_same_widget_wake_ups_are_idempotent(fake_host):
    """(e) Fresh wrappers on every wake-up must not re-accumulate anything."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry, _wrapper = _wire_entry(app, host, ext, "steady.png")
    viewport = _viewport_of(entry)
    context = ext.contexts[0]
    assert len(_overlays(viewport)) == 1
    overlay = context.overlay
    n_connections = len(context._connections)

    for _ in range(3):
        ext.handle_canvas_changed(None)
        ext.ensure_routed()
        ext.createActions(host["KisWindow"](entry))

    assert context.overlay is overlay
    assert len(_overlays(viewport)) == 1
    assert len(context._connections) == n_connections, "no connection stacking"


def test_same_widget_wake_ups_follow_a_new_document_without_overlay_churn(fake_host):
    """(f) Same widget, new document: sources and extent refresh, overlay does not."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry, _wrapper = _wire_entry(app, host, ext, "steady.png")
    viewport = _viewport_of(entry)
    context = ext.contexts[0]
    overlay = context.overlay
    bars = _bars_of(viewport)
    baseline = _counts(bars)
    n_connections = len(context._connections)

    for doc_id, edge in (("first.png", 1024.0), ("second.png", 640.0)):
        entry.document = host["FakeDocument"](doc_id, edge, edge)
        entry.canvas = host["KisCanvas"]()
        entry.view = host["KisView"](entry.document, entry.canvas)
        ext.handle_canvas_changed(None)
        assert context.doc_size == (edge, edge), "the extent followed the new document"
        assert ext.service.active_doc_id == doc_id

    assert context.overlay is overlay and len(_overlays(viewport)) == 1
    assert len(context._connections) == n_connections, "no subscription stacking"
    assert _counts(bars) == baseline, "the scrollbar connections are the original ones"
    assert isinstance(context.window, host["KisWindow"]), "the stored window is a live one"
    context.window.qwindow()  # a retained dead wrapper would raise here


def test_actions_of_a_window_the_host_no_longer_lists_mutate_nothing(fake_host, caplog):
    """(g) No owned wrapper for the key: the action logs and leaves the active model alone."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry_a, _wrapper_a = _wire_entry(app, host, ext, "gone.png")
    entry_b, _wrapper_b = _wire_entry(app, host, ext, "other.png")
    app.entries.remove(entry_a)
    app.active_entry = entry_b
    _by_id(entry_b)[ADD].trigger()
    model_b = ext.service.selected("other.png")
    assert model_b is not None, "B routed and got its grid"
    with caplog.at_level(logging.INFO, logger="composition_grid"):
        _by_id(entry_a)[TOGGLE].trigger()
    assert model_b.visible is True, "the globally active document is not a fallback"
    assert ext.service.active_doc_id == "other.png"
    assert len(_infos(caplog, "no live window")) == 1, "the dead key logged one no-op"


def test_deferred_attach_repeats_create_actions_without_duplicating_them(fake_host):
    """(h) Window not yet listed: deferred attach, six actions once, late bind."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry = host["Entry"]("hidden.png")
    _viewport_of(entry)
    app.entries.remove(entry)  # the host has not published this window yet
    for _ in range(3):
        ext.createActions(host["KisWindow"](entry))
    assert len(entry.created_actions) == 6, "no duplicated menu actions while deferred"
    assert ext.contexts == (), "nothing to attach to while the window is unlisted"

    app.entries.append(entry)
    app.active_entry = entry
    ext.handle_canvas_changed(None)
    assert len(ext.contexts) == 1 and len(entry.created_actions) == 6
    assert ext.contexts[0].overlay is not None
    assert len(_overlays(_viewport_of(entry))) == 1


def test_view_change_routes_through_the_fresh_observer_after_the_old_wrapper_dies(
    fake_host, caplog
):
    """(i) A deleted observer wrapper emits nothing; the replacement still routes."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry, borrowed = _wire_entry(app, host, ext, "first.png")
    viewport = _viewport_of(entry)
    app.active_entry = entry
    ext.ensure_routed()  # replaces the subscribed wrapper with a fresher one
    observers = [c for c in ext.held_connections if c[1] == "activeViewChanged"]
    assert len(observers) == 1, "exactly one activeView observer per window"
    borrowed.kill()

    entry.document = host["FakeDocument"]("next.png", 1024.0, 768.0)
    entry.view = host["KisView"](entry.document, entry.canvas)
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        entry.notify_view_changed()
    assert entry.qwindow.dead_skips == 1, "the deleted wrapper emitted nothing"
    assert ext.service.active_doc_id == "next.png", "the fresh wrapper routed the switch"
    assert len(_overlays(viewport)) == 1, "the rebind left exactly one overlay behind"
    assert not _errors(caplog, "failed"), "no signal handler died inside the swallow"


def test_destroyed_qwindow_prunes_only_its_own_window(fake_host, caplog):
    """(j) A dies: A's context, actions, observer and hook leave; B keeps all six."""
    mod, app, host = fake_host
    ext = _extension(mod, app)
    entry_a, _wrapper_a = _wire_entry(app, host, ext, "gone.png")
    entry_b, _wrapper_b = _wire_entry(app, host, ext, "stays.png")
    key_a = plugin_support.context_key(entry_a.qwindow)
    key_b = plugin_support.context_key(entry_b.qwindow)
    viewport_b = _viewport_of(entry_b)
    context_b = ext._holders.context(key_b)
    entry_a.qwindow.deleteLater()
    _drain_deferred_deletes()

    assert ext.contexts == (context_b,), "B's context is the only one left"
    assert len([c for c in ext.held_connections if c[3] is key_b]) == 2, (
        "B's own observer and prune hook survived the prune of A"
    )
    assert key_a not in ext._view_observers and key_a not in ext._prune_hooked
    assert len(ext.held_actions) == 6 and len(entry_b.created_actions) == 6
    assert context_b.overlay is not None and len(_overlays(viewport_b)) == 1
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        _by_id(entry_b)[ADD].trigger()
        _by_id(entry_b)[CYCLE].trigger()
    model_b = ext.service.selected("stays.png")
    assert model_b is not None, "B's window still owns a grid"
    assert model_b.grid_type_id == GRID_IDS[1]
    assert not _errors(caplog, "failed")
