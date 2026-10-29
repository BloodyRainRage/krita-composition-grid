"""The plugin-entry smoke test — wired against a FAKE ``krita`` module.

The plugin entry is the only module of the plugin that may reference libkis,
and only LAZILY inside a function (the sanctioned ``settings_store.
KritaTransport`` pattern): importing it must never require Krita. The seam this
smoke uses is therefore ``sys.modules`` injection — a fake ``krita`` module is
installed and the entry module RELOADED, because the ``krita.Extension`` base is
resolved at class-definition time (the docker solves the same problem with the
explicit ``docker_registration.make_panel_docker_class(base)`` parameter). The
fake is removed and the module restored on teardown, so no other test in the
suite ever sees a fake ``krita``.

Qt-gated the two-layer way of ``tests/canvas/test_overlay_qt.py`` (module-level
``skipif`` from a non-raising importlib probe + an autouse per-test
``try_import_qt`` gate; ``QT_QPA_PLATFORM=offscreen`` before any Qt import): the
entry itself is Qt-free, but it wires the Qt canvas layer and the
``TransformController`` fixture geometry of the sibling tests.

Pinned semantics:

* importing the entry does not raise and has NO top-level ``krita`` import;
* ``CompositionGridExtension`` subclasses the (fake) ``krita.Extension``,
  instantiates, and ``setup()`` is the documented no-op;
* ``createActions(window)`` calls ``window.createAction`` EXACTLY four times
  with the four pinned ids, English labels and the ``tools/scripts`` location;
* **Real Qt arity:** ``QAction.triggered`` is ``triggered(bool checked = false)``,
  so EVERY production emission delivers
  one positional argument. The fake hands back a REAL ``QAction`` and emits
  with that arity, so a handler that cannot absorb the checked flag is dead
  code — the actions are pinned to change real model state when triggered that
  way (and to survive a genuine ``QAction.trigger()``);
* **Document-size source:** libkis ``Canvas`` has NO
  ``width()``/``height()`` (its only size member is ``sizeChanged(QSize)``,
  documented as the size *in widget pixels*), so the fake canvas exposes none;
  the document extent comes from ``View.document().width()/.height()`` and is
  pinned to stay in DOCUMENT px even when the viewport is much smaller;
* **Fake honesty about the canvas widget:** ``Canvas`` has no widget getter
  either — ``KisCanvas2.canvasWidget()`` is a C++ internal from the
  widget hierarchy, not part of the Python API —
  so the fake canvas exposes none and the stable identity the rebind decision needs
  lives where the real locator looks: on the fake qwindow (``located_widget``),
  resolved through the extension's injectable ``widget_resolver`` seam;
* the four action callbacks do real work on a real ``GridStateService`` with a
  real ensured model: visibility flips, the edit session starts/ends, the
  transform resets to the default element FOR THE DOCUMENT SIZE TAKEN FROM THE
  CANVAS CONTEXT (no Document mutation API is touched at all — the size READ
  rides on ``View -> Document``), and the grid type cycles to the NEXT id in
  ``all_types()`` registry order and wraps;
* ``create_docker()`` registers EXACTLY ONE ``DockWidgetFactory`` with id
  ``composition_grid_docker``, the DockRight area and a docker class built over
  the (fake) ``krita.DockWidget`` base — and is idempotent, because a second
  factory means two panels per window while ``GridStateService.on_change`` has
  no unsubscribe;
* ONE notification path: a canvas drag writes the model
  and calls ``model.emit()``, which forwards to the owning
  :class:`GridStateService` (the single hub every model subscribes at creation) —
  there is NO per-model repaint bridge in the entry any more. ``CanvasContext``
  repaints through its own ``on_change`` subscription, so one ``drag_to`` step is
  EXACTLY one notification (one repaint, one docker sync, one save), never zero
  (the stale-field/no-save defect) and never two (the double notify).
* persistence: the saved type/params/style are restored onto a fresh model
  through the service (once per document); the saved ``transform`` is NOT
  (v1 scope is type+style; transform persistence is a non-goal), and a broken
  transport is logged, never raised;
* the extension keeps STRONG references to its actions/factory/contexts
  (a collected holder means collected signal callbacks), one
  context per window keyed by the stable ``qwindow()`` identity.
"""

from __future__ import annotations

import gc
import importlib
import importlib.machinery
import inspect
import json
import logging
import os
import re
import sys
import types
from pathlib import Path

# The offscreen platform is set before any Qt import in this module.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# --- GATE: no PyQt -> whole module skips at collection ----------------------
try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]

import composition_grid
import composition_grid.plugin_actions as plugin_actions
import composition_grid.plugin_composition_grid as plugin
import composition_grid.plugin_holders as plugin_holders
import composition_grid.plugin_settings as plugin_settings
import composition_grid.plugin_support as plugin_support
from composition_grid.canvas import canvas_locator
from composition_grid.canvas.canvas_context import CanvasContext
from composition_grid.canvas.doc_view_mapper import ViewState
from composition_grid.canvas.overlay import GridOverlay
from composition_grid.core.geometry import Bounds
from composition_grid.core.grids.grid_registry import all_types
from composition_grid.core.styles import Style
from composition_grid.core.transform_state import TransformState, def_element
from composition_grid.model.grid_model import GridStateService
from composition_grid.model.settings_store import JsonGridSettings
from composition_grid.ui import docker_registration
from composition_grid.ui.docker_panel import GridPanel
from composition_grid.ui.transform_controller import TransformController


def _qt_available() -> bool:
    """Non-raising probe mirroring ``try_import_qt`` order (PyQt6 first),
    probing the ``QtWidgets`` SUBMODULE (sibling Qt-test pattern)."""
    for major in (6, 5):
        try:
            importlib.import_module(f"PyQt{major}.QtWidgets")
        except ImportError:
            continue
        return True
    return False


@pytest.fixture(autouse=True)
def _qt_gate():
    """Per-test gate through the shared conftest helper."""
    try_import_qt()
    yield


pytestmark = pytest.mark.skipif(
    not _qt_available(), reason="PyQt not installed (uv install if needed)"
)

DOC_W, DOC_H = 800.0, 600.0
GRID_IDS = [cls.id for cls in all_types()]
ACTION_IDS = [
    "composition_grid_add",
    "composition_grid_toggle",
    "composition_grid_remove",
    "composition_grid_edit_session",
    "composition_grid_reset_transform",
    "composition_grid_cycle_type",
]


# ------------------------------------------------------------------ fake krita


class FakeSignal:
    """Minimal ``pyqtSignal`` look-alike: ``connect`` stores the slot (exactly
    the reference PyQt would have to keep alive) and ``emit`` calls them.

    NOT used for the action's ``triggered`` any more: that
    one is a genuine PyQt signal, because a hand-rolled ``emit()`` accepts zero
    arguments while ``QAction.triggered`` never does."""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


_KEEP_APP_REF = None
QtWidgets = None
QtCore = None


def _real_qaction(text):
    """A genuine ``QAction`` (offscreen QApplication kept alive), or ``None``
    when no binding is importable.

    WHY the fake uses the real thing: ``QAction.triggered`` is
    ``triggered(bool checked = false)``, and PyQt invokes the connected Python
    slot with that checked flag. A fake signal emitted with NO argument is more
    permissive than the host and hides arity bugs — this
    cannot. Resolved lazily (never at module import) so a PyQt-free collection
    stays error-free; the module-level gate skips these tests in that case
    anyway."""
    global _KEEP_APP_REF
    import importlib

    for major in (6, 5):
        try:
            widgets = importlib.import_module(f"PyQt{major}.QtWidgets")
            gui = importlib.import_module(f"PyQt{major}.QtGui")
            core = importlib.import_module(f"PyQt{major}.QtCore")
        except ImportError:
            continue
        if widgets.QApplication.instance() is None:
            _KEEP_APP_REF = widgets.QApplication([])
        # QAction lives in QtGui since Qt6; QtWidgets on Qt5.
        action_cls = getattr(gui, "QAction", None) or getattr(widgets, "QAction")
        return action_cls(text, None)
    return None


class FakeAction:
    """What ``Window.createAction`` answers in libkis: a QAction (here a REAL
    one, see :func:`_real_qaction`) with the documented ``triggered`` signal."""

    def __init__(self, action_id, text, menu_location):
        self.id = action_id
        self.text = text
        self.menu_location = menu_location
        self.qaction = _real_qaction(text)
        self.triggered = self.qaction.triggered if self.qaction is not None else FakeSignal()

    def trigger(self):
        """A real Qt activation — ``QAction.trigger()`` emits
        ``triggered(checked)`` through Qt's own invocation machinery, i.e. what
        a menu click in Krita does."""
        if self.qaction is not None:
            self.qaction.trigger()
        else:  # unreachable while the Qt gate is active; keeps the fake honest
            self.triggered.emit(True)


class _CheckedEmitter:
    """``action id -> trigger`` callable with the REAL Qt emission arity: every
    call emits ``triggered(checked)`` with one positional bool (alternating
    False/True like a checkable action) unless an explicit flag is given."""

    def __init__(self, action):
        self._action = action
        self._checked = False

    def __call__(self, checked=None):
        self._checked = (not self._checked) if checked is None else bool(checked)
        self._action.triggered.emit(self._checked)
        return self._checked


class _Rect:
    """QRectF duck (x/y/width/height)."""

    def __init__(self, x, y, w, h):
        self._values = (x, y, w, h)

    def x(self):
        return self._values[0]

    def y(self):
        return self._values[1]

    def width(self):
        return self._values[2]

    def height(self):
        return self._values[3]


class _Size:
    """QSize duck (widget-pixel size, the payload of ``Canvas.sizeChanged``)."""

    def __init__(self, w, h):
        self._values = (w, h)

    def width(self):
        return self._values[0]

    def height(self):
        return self._values[1]


class _CanvasWidget:
    """The canvas WIDGET of a fake window — the Qt object the overlay is parented
    to and the answer ``canvas_locator.find_canvas_widget`` gives for a qwindow.

    It belongs to the WINDOW side (the fake qwindow holds the one currently on
    screen), NEVER to a fake libkis ``Canvas``: real ``libs/libkis/Canvas.h`` has no
    widget getter at all, so a fake canvas offering one would be
    more permissive than the host — exactly how a canvas-widget helper on the fake
    would let the suite pass a rebind idempotence real Krita never had.

    The fresh-wrapper model still stands: a
    resolved ``view.canvas()`` is a NEW wrapper object every call, so the fake hands
    those out too — which is precisely why the identity that matters is the widget
    under the window, not anything on the wrapper."""

    def __init__(self, widget_w=DOC_W, widget_h=DOC_H):
        self.size = (float(widget_w), float(widget_h))


class FakeCanvas:
    """Duck-typed libkis Canvas — and, per the real ``libs/libkis/Canvas.h``,
    it exposes NO ``width()``/``height()``: the wrapper has no document-size
    getter at all. Its only size member is ``sizeChanged(QSize)``, documented
    as the size **in widget pixels** (the viewport), so the fake carries that
    signal plus the view-state seeding a real bind reads (document rect in
    widget px — VIEWPORT space: a 4000x3000 page may well sit in an 800x600
    viewport — rotation and mirror).

    The former ``width()``/``height()`` existed only because
    this fake invented them; they let a viewport-sized number masquerade as the
    document extent. ``stateChanged`` stays deliberately absent: an optional
    missing signal must be skipped, never raise.

    It exposes NO widget getter either (no ``canvasWidget()``, no
    ``widget``, no ``qwidget``) — the real header has none, so the stable identity of
    "the canvas on screen" comes from the window's Qt side
    (:attr:`FakeQWindow.located_widget`), which is exactly what the extension's
    ``widget_resolver`` reads. :meth:`twin` builds the fresh wrapper ``view.canvas()``
    hands out per call — a different Python object sharing the SAME signals (a real
    fresh wrapper shares the underlying QObject's signals).

    ``label`` is fixture bookkeeping for the assertions below (twins share it): the
    plugin never reads it, so unlike an invented widget getter it can never make
    production code pass a test real Krita would fail."""

    def __init__(self, widget_w=DOC_W, widget_h=DOC_H, label="", _shared=None):
        if _shared is not None:
            self._widget_w = _shared._widget_w
            self._widget_h = _shared._widget_h
            self.sizeChanged = _shared.sizeChanged
            self.documentRectInWidgetPixelsChanged = (
                _shared.documentRectInWidgetPixelsChanged
            )
            self.label = _shared.label
        else:
            self._widget_w = float(widget_w)
            self._widget_h = float(widget_h)
            self.sizeChanged = FakeSignal()
            self.documentRectInWidgetPixelsChanged = FakeSignal()
            self.label = label or "canvas"

    def twin(self):
        return FakeCanvas(_shared=self)

    def rotation(self):
        return 0.0

    def mirror(self):
        return {"horizontal": False, "vertical": False}

    def documentRectInWidgetPixels(self):
        return _Rect(0.0, 0.0, self._widget_w, self._widget_h)


class FakeDocument:
    """Duck-typed libkis ``Document``: ``name()`` (the per-document key the
    plugin reads) and the documented ``width()``/``height()`` — "the width and
    height of the image in pixels", i.e. the only true DOCUMENT-px source in
    libkis. Reads only for the grid geometry; the ONE
    documented exception is the ``composition_grid`` STATE ANNOTATION
    (``setAnnotation``/``annotation``/``annotationTypes``, live-verified on
    5.3.1) — the plugin writes it when the user changes the grid, which marks
    the document modified exactly like any other annotation. ``modified`` and
    the call logs let the tests pin that the RESTORE path never writes."""

    def __init__(self, name="untitled-1", doc_w=DOC_W, doc_h=DOC_H, file_name=None):
        self._name = name
        self._doc_w = float(doc_w)
        self._doc_h = float(doc_h)
        # The live host fills fileName() for a document opened from disk, where
        # name() answers '' — the fake carries both because the plugin may key
        # on either, and a fake that offers LESS than the host hides defects.
        self._file_name = file_name
        self.annotations = {}
        self.set_annotation_calls = []
        self.annotation_calls = []
        self.modified = False

    def name(self):
        return self._name

    def fileName(self):
        return self._file_name if self._file_name is not None else ""

    def width(self):
        return self._doc_w

    def height(self):
        return self._doc_h

    def setAnnotation(self, annotation_type, description, data):  # noqa: N802
        self.set_annotation_calls.append((annotation_type, description, bytes(data)))
        self.annotations[annotation_type] = bytes(data)
        self.modified = True

    def annotation(self, annotation_type):  # noqa: N802
        self.annotation_calls.append(annotation_type)
        return self.annotations.get(annotation_type, b"")

    def annotationTypes(self):  # noqa: N802
        return list(self.annotations)


class FakeView:
    """Duck-typed libkis ``View``: ``canvas()`` for the overlay wiring and
    ``document()`` — the route the document size is read through.

    Like the real pybind11 wrapper, ``canvas()`` hands back a FRESH wrapper per
    call — so the plugin can never identify a canvas by
    wrapper identity, and a Canvas offers no stable widget of its own
    either: the canvas widget the rebind decision keys on belongs to the window side
    (:attr:`FakeWindow.set_view`). The document object stays shared: the plugin only
    READS it (``name``/``width``/``height``) and never compares it. ``twin()`` is the
    fresh ``View`` wrapper ``window.activeView()`` returns per call."""

    def __init__(self, document, canvas):
        self._document = document
        self._canvas = canvas

    def twin(self):
        return FakeView(self._document, self._canvas)

    def document(self):
        return self._document

    def canvas(self):
        return self._canvas.twin()


class FakeQWindow:
    """The stable per-window identity (a real QMainWindow is one): the key the
    extension maps its contexts by — and, like a real QMainWindow, a QObject
    with a ``destroyed`` signal (the prune hook listens to it).

    ``located_widget`` is the canvas widget currently located under this window, i.e.
    the Qt object a locator would find by walking the widget tree and the object the
    overlay is parented to. That is where the fake stores the stable identity the
    rebind decision keys on — on the Qt side of the window, NOT on the libkis canvas,
    which offers no widget getter at all."""

    def __init__(self):
        self.destroyed = FakeSignal()
        self.located_widget = None


_OPEN_WINDOWS: list = []  # the fake libkis window registry, reset per fixture


class FakeWindow:
    """libkis ``Window`` wrapper look-alike: ``createAction`` records calls and
    answers a QAction-like object, ``qwindow()`` gives the stable identity,
    ``activeView()`` the current view.

    :meth:`set_view` is how a test switches what the window shows: a real view
    switch / ``resetCanvas`` replaces the canvas WIDGET too, so the fake moves both
    halves together — the fresh libkis view wrapper AND the widget located under the
    qwindow, which is the identity the extension's rebind decision compares.
    Constructing one registers it as an OPEN window (``_twin_of``: a wrapper is not
    a window)."""

    def __init__(self, document=None, canvas=None, _register=True):
        self.created = []
        self.actions = []
        self.activeViewChanged = FakeSignal()
        self._qwindow = FakeQWindow()
        self._view = None
        self.closed = False
        if document is not None or canvas is not None:
            self.set_view(
                FakeView(document or FakeDocument("untitled-1"), canvas or FakeCanvas())
            )
        if _register:
            _OPEN_WINDOWS.append(self)
            self._qwindow.destroyed.connect(self._mark_closed)

    def _mark_closed(self, *_):
        """A destroyed qwindow leaves the host's ``windows()`` registry."""
        self.closed = True

    def set_view(self, view, widget=None):
        """Install *view* as the active view and answer this window's newly located
        canvas widget (pass *widget* to switch the view while keeping the widget on
        screen — a document re-route onto the canvas already displayed)."""
        self._view = view
        if view is None:
            self._qwindow.located_widget = None
        else:
            self._qwindow.located_widget = (
                widget if widget is not None else _CanvasWidget()
            )
        return self._qwindow.located_widget

    def createAction(self, action_id, text="", menu_location=""):  # noqa: N802
        action = FakeAction(action_id, text, menu_location)
        self.actions.append(action)
        self.created.append((action_id, text, menu_location))
        return action

    def qwindow(self):
        return self._qwindow

    def twin(self):
        return _twin_of(self)

    def activeView(self):
        # a fresh ``View`` wrapper per call, pointing
        # at the same underlying view — exactly what ``Krita.activeWindow()`` /
        # ``Window.activeView()`` do under pybind11 by-value returns.
        return self._view.twin() if self._view is not None else None


def _twin_of(window):
    """A second ``Window`` wrapper around the SAME qwindow, view and view signals
    — what Krita hands out on every ``activeWindow()``/``windows()`` call."""
    twin = FakeWindow(_register=False)
    twin._qwindow = window.qwindow()
    twin._view = window._view
    twin.activeViewChanged = window.activeViewChanged
    return twin


def _active_view_of(window):
    """Guarded ``window.activeView()`` — the same duck-typed probe the real
    context uses for its document-size fallback branch."""
    getter = getattr(window, "activeView", None)
    return getter() if callable(getter) else None


def _qwindow_of(window):
    """The stable window identity — the object ``plugin_support.context_key`` keys a
    holder by and the object the canvas locator walks (a libkis ``Window`` answers
    ``qwindow()``, a raw QMainWindow IS it, as does a fake)."""
    getter = getattr(window, "qwindow", None)
    return getter() if callable(getter) else window


def _fake_widget_resolver(key):
    """Test stand-in for :func:`composition_grid.canvas.canvas_locator.
    find_canvas_widget`: the canvas widget designated under the fake window's
    qwindow.

    Injected through the extension's ``widget_resolver`` seam because the real
    locator needs a genuine Qt widget tree (it refuses a non-``QWidget``) and the
    fake windows are plain objects — the same reason ``context_factory`` is
    injectable. It reads the widget off the Qt side of the window, exactly where the
    real one looks, and NEVER off the canvas, which has no widget getter."""
    return getattr(key, "located_widget", None)


class FakeDockWidget:
    """``krita.DockWidget`` base stand-in (registration only BUILDS the docker
    class; Krita instantiates it later)."""


class FakeDockWidgetFactory:
    def __init__(self, factory_id, area, docker_class):
        self.id = factory_id
        self.area = area
        self.docker_class = docker_class


class FakeDockWidgetFactoryBase:
    DockRight = "DockRight"


class FakeExtension:
    def __init__(self, instance=None):
        self._instance = instance


class FakeKritaInstance:
    """``Krita.instance()``: the entry-side APIs the plugin uses (settings transport,
    docker factory, extension registry, active window, open-window registry)."""

    def __init__(self):
        self.settings = {}
        self.factories = []
        self.extensions = []
        self.active_window = None

    def readSetting(self, group, name, default=""):  # noqa: N802
        return self.settings.get((group, name), default)

    def writeSetting(self, group, name, value):  # noqa: N802
        self.settings[(group, name)] = value

    def addDockWidgetFactory(self, factory):  # noqa: N802
        self.factories.append(factory)

    def addExtension(self, extension):  # noqa: N802 — krita.pyi:364 takes one argument
        self.extensions.append(extension)

    def activeWindow(self):  # noqa: N802
        return self.active_window

    def windows(self):  # noqa: N802
        """Freshly owned wrappers of the OPEN main windows, closed qwindow excluded."""
        return [host.twin() for host in _OPEN_WINDOWS if not host.closed]


def _build_fake_krita():
    """A libkis-shaped fake module: exactly the five names the entry touches."""
    fake = types.ModuleType("krita")
    instance = FakeKritaInstance()
    fake.Extension = FakeExtension
    fake.DockWidget = FakeDockWidget
    fake.DockWidgetFactory = FakeDockWidgetFactory
    fake.DockWidgetFactoryBase = FakeDockWidgetFactoryBase

    class _Krita:
        @staticmethod
        def instance():
            return instance

    fake.Krita = _Krita
    return fake, instance


@pytest.fixture
def fake_krita():
    """Install a fake ``krita`` module in ``sys.modules`` and reload the entry
    over it; restore both afterwards (no residue for other test modules)."""
    fake, instance = _build_fake_krita()
    saved = sys.modules.get("krita")
    saved_windows = list(_OPEN_WINDOWS)
    _OPEN_WINDOWS.clear()
    sys.modules["krita"] = fake
    reloaded = importlib.reload(plugin)
    try:
        yield reloaded, fake, instance
    finally:
        try:
            if saved is None:
                del sys.modules["krita"]
            else:
                sys.modules["krita"] = saved
            importlib.reload(plugin)
        finally:
            _OPEN_WINDOWS.clear()
            _OPEN_WINDOWS.extend(saved_windows)


# ----------------------------------------------------------- test-local seams


class RecordingOverlay:
    def __init__(self):
        self.paints = 0

    def request_paint(self):
        self.paints += 1

    def refresh(self):
        # Mirrors the real GridOverlay.refresh(): a service-driven refresh is
        # exactly one repaint request (it ends in a single Qt update()).
        self.paints += 1


class RecordingContext:
    """Stands in for ``CanvasContext`` and exposes exactly the seams the
    extension uses (``doc_size``/``canvas``/``widget``/``bind``/``rebind``/
    ``refresh_sources``/``unbind``/``refresh_document_size``/``overlay``).

    Like the real ``CanvasContext`` it subscribes to the service's
    ``on_change`` at construction and repaints its overlay from that callback
    (``_on_state_change`` -> ``overlay.refresh``) — this is the surviving single
    notification path a canvas drag takes now that the per-model repaint bridge
    is gone, so one drag step (one ``model.emit`` -> one service ``_notify``) is
    countable headlessly as exactly one overlay repaint.

    Like the real context, the document size it reports is
    read through the BOUND VIEW's ``document()`` — never off the canvas, which
    in libkis carries no document size at all — so the fake cannot smuggle a
    viewport-sized number into the ``ensure``/Reset path.

    ``widget`` mirrors the real ``CanvasContext.widget``: the located canvas widget
    the overlay is attached to, stored at attach time from the SAME object the
    extension's resolver walks (that is the identity rebind idempotence
    compares), and cleared by ``unbind`` just like the real one clears it."""

    instances = []

    def __init__(self, service):
        self.service = service
        self.canvas = None
        self.widget = None
        self.window = None
        self.view = None
        self.binds = []
        self.rebinds = []
        self.refreshes = []
        self.unbindings = 0
        self.overlay = RecordingOverlay()
        self._doc_size = None
        # The surviving repaint path (mirrors the real CanvasContext): the
        # service is the single hub, so subscribe once and repaint on notify.
        service.on_change(self._on_state_change)
        RecordingContext.instances.append(self)

    def _on_state_change(self) -> None:
        if self.overlay is not None:
            self.overlay.refresh()

    @property
    def doc_size(self):
        return self._doc_size

    def bind(self, window, view, canvas):
        self.binds.append((window, view, canvas))
        self._attach(window, view, canvas)
        return True

    def rebind(self, window, view, canvas):
        self.rebinds.append((window, view, canvas))
        self._attach(window, view, canvas)
        return True

    def unbind(self):
        self.unbindings += 1
        self.canvas = None
        self.widget = None
        self._doc_size = None

    def refresh_sources(self, window, view, canvas):
        """Same-widget seam: fresh refs and extent, no overlay churn."""
        self.refreshes.append((window, view, canvas))
        self.window = window
        self.view = view
        self.canvas = canvas
        self._read_document(self.view)
        return self.overlay is not None

    def _attach(self, window, view, canvas):
        self.window = window
        self.view = view
        self.canvas = canvas
        self.widget = _fake_widget_resolver(_qwindow_of(window))
        self._read_document(self.view)

    def refresh_document_size(self):
        """The extension's refresh point (the real ``CanvasContext`` has the
        same seam): re-read the document extent through the window's CURRENT
        active view, e.g. when a different document is routed onto an already
        bound context."""
        self._read_document(_active_view_of(self.window))
        return self._doc_size is not None

    def _read_document(self, view):
        document = getattr(view, "document", None)
        document = document() if callable(document) else None
        width = getattr(document, "width", None)
        height = getattr(document, "height", None)
        if not callable(width) or not callable(height):
            self._doc_size = None
            return
        try:
            self._doc_size = (float(width()), float(height()))
        except (TypeError, ValueError):
            self._doc_size = None


def _context_factory():
    """Fresh ``context_factory`` + a reset instance log for one test."""
    RecordingContext.instances = []
    return lambda service: RecordingContext(service)


class RecordingSettings:
    """``JsonGridSettings`` look-alike: ``load`` answers a stored report dict."""

    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.loads = 0
        self.saved = []

    def load(self):
        self.loads += 1
        return dict(self.state)

    def save(self, model):
        self.saved.append(model)


def _extension(mod, **kwargs):
    """Extension over a fresh fake instance with a FRESH service by default
    (the shared singleton is only touched where a test pins that seam) and with the
    fake-window ``widget_resolver`` the rebind decision needs — production passes no
    resolver and gets the real locator, which cannot resolve anything from a fake
    qwindow (it insists on a genuine ``QWidget``)."""
    kwargs.setdefault("service", GridStateService())
    kwargs.setdefault("widget_resolver", _fake_widget_resolver)
    return mod.CompositionGridExtension(FakeKritaInstance(), **kwargs)


def _activated(mod, doc_id="untitled-1", grid=True, **kwargs):
    """Extension + window with an ACTIVATED document through the real
    createActions path; returns (ext, window, model).

    Routing alone creates NO grid — a document has no grid until the artist asks
    for one — so the tests that edit a grid ask for it here, through the service
    (the very call the "Add Composition Grid" action makes, notification and
    annotation write included). ``grid=False`` leaves the document routed and
    empty, which is what the no-grid pins need."""
    service = kwargs.setdefault("service", GridStateService())
    ext = _extension(mod, **kwargs)
    window = FakeWindow(FakeDocument(doc_id), FakeCanvas())
    ext.createActions(window)
    if grid:
        service.add_grid()
    return ext, window, service.selected(doc_id)


def _emitters(window):
    """``action id -> trigger``, called with the arity real Qt uses (see
    :class:`_CheckedEmitter`)."""
    return {action.id: _CheckedEmitter(action) for action in window.actions}


def _view_state(doc):
    """ViewState for the headless drag: doc square mapped 1:1 into widget px."""
    return ViewState(
        document_rect_widget=Bounds(0.0, 0.0, doc, doc),
        rotation_deg=0.0,
        mirror_x=False,
        mirror_y=False,
        doc_width=doc,
        doc_height=doc,
    )


# ------------------------------------------------------------------- contract


def test_entry_module_imports_without_krita_and_has_no_top_level_krita_import():
    """The entry imports in a bare environment (no libkis module)
    without raising, and the lazy-import contract holds: no top-level
    ``import krita`` line in the entry, in its helper modules (only indented,
    in-function ones — the sanctioned ``KritaTransport`` pattern), and that lazy
    seam really exists. The two helper modules
    (``plugin_actions`` for the action factory, ``plugin_settings`` for the
    restore path) are covered by the same rule."""
    assert "krita" not in sys.modules
    assert importlib.import_module("composition_grid.plugin_composition_grid") is plugin
    assert plugin.CompositionGridExtension is not None
    for module in (
        plugin,
        plugin_support,
        plugin_actions,
        plugin_settings,
        plugin_holders,
    ):
        top_level = [
            line
            for line in inspect.getsource(module).splitlines()
            if re.match(r"^(?:import|from)\s+krita\b", line)
        ]
        assert top_level == [], f"top-level krita import in {module.__name__}: {top_level}"
    assert "import krita" in inspect.getsource(plugin_support)
    assert "from krita import Extension" in inspect.getsource(plugin_support)


def test_package_import_without_krita_stays_side_effect_free():
    """``import composition_grid`` headlessly performs NO registration —
    the guard's predicate is what skips it (the pure test suite imports the
    package and its submodules without ever touching libkis)."""
    assert composition_grid.__version__
    assert composition_grid._krita_available() is False
    assert "krita" not in sys.modules


def test_package_import_with_krita_reachable_does_register():
    """The other half of the guard: once the ``krita`` module really is
    reachable (a proper spec, as inside Krita), importing the package performs
    the registration side effect the pykrita loader depends on — the entry
    import plus ``addExtension`` and the docker factory."""
    fake, instance = _build_fake_krita()
    fake.__spec__ = importlib.machinery.ModuleSpec("krita", None)
    saved = sys.modules.get("krita")
    saved_held = list(plugin.HELD_PLUGINS)
    sys.modules["krita"] = fake
    importlib.reload(plugin)
    try:
        importlib.reload(composition_grid)
        assert composition_grid._krita_available() is True
        assert len(plugin.HELD_PLUGINS) == len(saved_held) + 1
        registered = plugin.HELD_PLUGINS[-1]
        assert instance.extensions == [registered]
        assert [f.id for f in instance.factories] == [plugin.DOCKER_ID]
    finally:
        # leave no trace for the rest of the suite
        if saved is None:
            del sys.modules["krita"]
        else:
            sys.modules["krita"] = saved
        plugin.HELD_PLUGINS[:] = saved_held
        importlib.reload(plugin)
        importlib.reload(composition_grid)
    assert composition_grid._krita_available() is False


def test_extension_subclasses_krita_extension_and_setup_is_noop(fake_krita):
    """``CompositionGridExtension`` subclasses the (fake) ``krita.Extension``
    base resolved lazily at import; ``setup()`` is the documented no-op; the
    service is reachable for the docker seam."""
    mod, fake, _instance = fake_krita
    assert issubclass(mod.CompositionGridExtension, fake.Extension)
    ext = mod.CompositionGridExtension(FakeKritaInstance())
    assert ext.setup() is None
    assert ext.service is not None


def test_extension_ctor_falls_back_to_a_no_argument_base(fake_krita, caplog):
    """A genuine host-API uncertainty, not a crutch: the
    community templates construct ``krita.Extension`` with NO arguments, while
    this plugin's pybind11 wrapper MAY take the instance. If the real signature
    rejects the argument, ``register_plugin`` would raise during the package
    import and the plugin would silently never load — so the arg form is tried
    first and the no-arg form used on TypeError, with the fallback logged (never
    swallowed in silence). A manual check on real Krita 5.3 verifies which branch
    is taken."""

    class StrictBase:
        """A pybind11-shaped base: exactly one parameter, the self object."""

        def __init__(self):
            self.started = True

    mod, fake, _instance = fake_krita
    saved = fake.Extension
    fake.Extension = StrictBase
    try:
        importlib.reload(mod)
        with caplog.at_level(logging.DEBUG, logger="composition_grid"):
            ext = mod.CompositionGridExtension(FakeKritaInstance(), service=GridStateService())
        assert isinstance(ext, StrictBase)
        assert ext.started is True, "the no-arg base really got constructed"
        assert ext.setup() is None
        assert any(
            "rejected the instance argument" in r.getMessage() for r in caplog.records
        ), "the fallback is logged, not silent"
    finally:
        fake.Extension = saved
        importlib.reload(mod)


def test_default_service_is_the_shared_docker_seam(fake_krita):
    """Without an injected service the extension uses
    ``docker_registration.shared_state_service()`` — the designated wiring
    seam, so the docker panel and the actions drive the SAME models."""
    mod, _fake, _instance = fake_krita
    ext = mod.CompositionGridExtension(FakeKritaInstance())
    assert ext.service is docker_registration.shared_state_service()
    # honest teardown: the shared service has no unsubscribe, so drop the only
    # reference and let the subscriber go with it
    del ext
    gc.collect()


# -------------------------------------------------------------------- actions


def test_create_actions_makes_exactly_six_pinned_actions(fake_krita):
    """EXACTLY six ``window.createAction`` calls with the pinned ids, English
    labels and the ``tools/scripts`` menu location (the
    sixth is the multi-grid "Add Composition Grid")."""
    mod, _fake, _instance = fake_krita
    ext = _extension(mod)
    window = FakeWindow()
    ext.createActions(window)
    assert [created[0] for created in window.created] == ACTION_IDS
    assert len(window.actions) == 6
    assert all(a.menu_location == "tools/scripts" for a in window.actions)
    assert all(a.text.isascii() and a.text[0].isupper() for a in window.actions)
    # strong references to action + connected slot are kept on the holder
    assert [action.id for action, _slot in ext.held_actions] == ACTION_IDS
    assert len(ext.held_actions) == 6


def test_toggle_action_flips_visibility(fake_krita):
    mod, _fake, _instance = fake_krita
    _ext, window, model = _activated(mod)
    emit = _emitters(window)
    assert model.visible is True
    emit["composition_grid_toggle"]()
    assert model.visible is False
    emit["composition_grid_toggle"]()
    assert model.visible is True


def test_a_fresh_document_has_no_grid_until_the_add_action(fake_krita):
    """The first rule, end to end: activation ROUTES a document (its
    container exists) and creates nothing; the grid appears on the artist's
    explicit "Add Composition Grid" only."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service)
    window = FakeWindow(FakeDocument("untitled-1"), FakeCanvas())
    ext.createActions(window)

    assert service.is_routed("untitled-1"), "the document is known to the service"
    assert service.grids("untitled-1") == (), "and owns no grid yet"

    _emitters(window)["composition_grid_add"]()
    grids = service.grids("untitled-1")
    assert len(grids) == 1 and grids[0].label == "1"
    assert grids[0].visible is True and grids[0].edit_session is False
    assert service.selected_grid_id("untitled-1") == grids[0].grid_id


def test_remove_action_deletes_the_grid_and_add_creates_the_next_one(fake_krita):
    """"Remove Composition Grid" DELETES the selected grid — the document simply
    keeps what stays — and "Add Composition Grid" creates a fresh one whose label
    continues the numbering."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    _ext, window, model = _activated(mod, service=service)
    emit = _emitters(window)

    emit["composition_grid_remove"]()
    assert service.grids("untitled-1") == (), "the grid is deleted, not hidden"
    assert service.selected_grid_id("untitled-1") is None

    emit["composition_grid_add"]()
    added = service.selected("untitled-1")
    assert added is not None and added is not model, "a NEW grid, not the old one"
    assert added.label == "1", "the numbering restarts from the smallest free number"
    assert added.transform == def_element(None, DOC_W, DOC_H), "centered again"


def test_toggle_with_no_grid_never_creates_one(fake_krita, caplog):
    """"Toggle Composition Grid" only ever flips visibility: with no grid in the
    document every other action is a logged no-op and the document stays
    grid-less."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    _ext, window, _model = _activated(mod, service=service)
    emit = _emitters(window)
    emit["composition_grid_remove"]()

    with caplog.at_level(logging.INFO, logger="composition_grid"):
        emit["composition_grid_toggle"]()
        emit["composition_grid_cycle_type"]()
        emit["composition_grid_edit_session"]()

    assert service.grids("untitled-1") == (), "no action but Add creates a grid"
    assert "no selected grid" in caplog.text, "the no-op is logged, never silent"


def test_remove_action_without_an_active_document_is_a_no_op(fake_krita):
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service)
    window = FakeWindow()  # a window whose view has no document at all
    ext.createActions(window)
    _emitters(window)["composition_grid_remove"]()
    assert service.active_doc_id is None, "nothing was invented to remove"


def test_edit_session_action_starts_and_ends_the_session(fake_krita):
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    _ext, window, model = _activated(mod, service=service)
    emit = _emitters(window)
    assert service.active_doc_id == "untitled-1"
    assert model.edit_session is False
    emit["composition_grid_edit_session"]()
    assert model.edit_session is True
    emit["composition_grid_edit_session"]()
    assert model.edit_session is False


def test_reset_action_restores_the_default_element_for_the_context_doc_size(fake_krita):
    """Reset takes the document size from the CANVAS CONTEXT's view state (a
    REAL ``CanvasContext`` seeded through ``View.document()``, not a fake
    context): the model returns to ``def_element`` for 800x600, rotation 0 —
    and the document is only ever READ (no mutation API exists on the fake)."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    _ext, window, model = _activated(mod, service=service, context_factory=CanvasContext)
    assert model is not None, "the real context seeded the doc size at bind"
    assert model.transform == def_element(None, DOC_W, DOC_H)
    model.transform = TransformState(12.0, 34.0, 56.0, 78.0, 25.0, False)
    _emitters(window)["composition_grid_reset_transform"]()
    assert model.transform == def_element(None, DOC_W, DOC_H)
    assert model.transform.width == pytest.approx(0.8 * min(DOC_W, DOC_H))
    assert model.transform.rotation_deg == 0.0
    assert model.transform.ratio_locked is True


# ------------------------------------------------------------- real Qt arity


@pytest.mark.parametrize("check_state", [True, False])
def test_every_action_changes_state_when_qt_delivers_the_checked_bool(
    fake_krita, caplog, check_state
):
    """``QAction.triggered`` is ``triggered(bool checked =
    false)``, so every production emission carries ONE positional argument.
    The handlers receive the window through a partial, so a handler declared
    with exactly one parameter is called with two and raises ``TypeError`` —
    which ``never_raises_slot`` swallows into a log line, i.e. six menu
    entries that do nothing at all inside real Krita.

    Both checked states are exercised (a checkable action toggles), and the
    proof is the MODEL STATE, not the return value: visibility flips, the grid
    is deleted and a new one added, the edit session starts, the transform
    resets, the type cycles. A logged handler failure means the action died
    inside the swallow — asserted impossible."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    _ext, window, model = _activated(mod, service=service, context_factory=CanvasContext)
    emit = _emitters(window)
    model.transform = TransformState(12.0, 34.0, 56.0, 78.0, 25.0, False)
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        emit["composition_grid_toggle"](check_state)
        assert model.visible is False, "the visibility action must flip the model"
        emit["composition_grid_remove"](check_state)
        assert service.grids("untitled-1") == (), "the remove action deletes the grid"
        emit["composition_grid_add"](check_state)
        fresh = service.selected("untitled-1")
        assert fresh is not None and fresh is not model, "Add created the next grid"
        emit["composition_grid_edit_session"](check_state)
        assert fresh.edit_session is True, "the edit-session action must start it"
        emit["composition_grid_reset_transform"](check_state)
        assert fresh.transform == def_element(None, DOC_W, DOC_H)
        emit["composition_grid_cycle_type"](check_state)
        assert fresh.grid_type_id == GRID_IDS[1], "the cycle action must move the type"
    dead = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and "failed" in record.getMessage()
    ]
    assert not dead, f"an action died inside never_raises_slot: {[r.getMessage() for r in dead]}"


def test_actions_fire_through_a_real_qt_activation(fake_krita):
    """The same pin one level closer to production: ``QAction.trigger()`` (the
    genuine PyQt object the fake hands back) emits ``triggered(checked)``
    through Qt's own slot-invocation machinery — exactly what a click on
    Tools ▸ Scripts does. The bound handler must absorb the flag it is handed."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    _ext, window, model = _activated(mod, service=service)
    by_id = {action.id: action for action in window.actions}
    assert by_id[ACTION_IDS[0]].qaction is not None, "the fake hands back a real QAction"

    by_id["composition_grid_toggle"].trigger()
    assert model.visible is False
    by_id["composition_grid_edit_session"].trigger()
    assert model.edit_session is True
    model.transform = TransformState(12.0, 34.0, 56.0, 78.0, 25.0, False)
    by_id["composition_grid_reset_transform"].trigger()
    assert model.transform == def_element(None, DOC_W, DOC_H)
    by_id["composition_grid_cycle_type"].trigger()
    assert model.grid_type_id == GRID_IDS[1]


# ---------------------------------------------------- document px, not widget


def test_document_size_is_read_through_the_view_not_the_viewport(fake_krita):
    """THE unit test that pins the defect: a 4000x3000
    document shown in an 800x600 viewport. libkis ``Canvas`` has no size getter
    and its ``sizeChanged(QSize)`` is documented as the size *in widget pixels*,
    so feeding it to the document extent builds the default element at viewport
    scale and makes the mapper divide by the wrong denominator. The context must
    report 4000x3000 (``View.document().width()/.height()``) both for the default
    element of a grid added later and for Reset, and an emission of the viewport
    signal must not move it."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("big-doc", 4000.0, 3000.0)
    canvas = FakeCanvas(800.0, 600.0)  # the VIEWPORT rect, in widget px
    ext = _extension(mod, service=service, context_factory=CanvasContext)
    window = FakeWindow(document, canvas)
    ext.createActions(window)
    context = ext.contexts[0]
    service.add_grid()

    assert context.doc_size == (4000.0, 3000.0)
    state = context.view_state
    assert state is not None, "a fresh bind yields a usable view state"
    assert state.doc_width == 4000.0
    assert state.doc_height == 3000.0
    # the default element for the DOCUMENT, not the viewport:
    assert service.selected("big-doc").transform == def_element(None, 4000.0, 3000.0)

    canvas.sizeChanged.emit(_Size(800, 600))  # a viewport resize in widget px
    assert context.doc_size == (4000.0, 3000.0), "sizeChanged is widget px, not the doc"
    model = service.selected("big-doc")
    model.transform = TransformState(12.0, 34.0, 56.0, 78.0, 25.0, False)
    _emitters(window)["composition_grid_reset_transform"]()
    assert model.transform == def_element(None, 4000.0, 3000.0)
    assert context.view_state.document_rect_widget == Bounds(0.0, 0.0, 800.0, 600.0)


def test_cycle_action_follows_registry_order_and_wraps(fake_krita):
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    _ext, window, model = _activated(mod, service=service)
    emit_cycle = _emitters(window)["composition_grid_cycle_type"]
    assert model.grid_type_id == GRID_IDS[0]
    emit_cycle()
    assert model.grid_type_id == GRID_IDS[1]
    service.set_type(GRID_IDS[-1])
    emit_cycle()
    assert model.grid_type_id == GRID_IDS[0], "the cycle wraps after the last registry id"
    assert len(GRID_IDS) == 12, "v1 library: all 12 types cycle"


def test_actions_are_safe_noops_without_an_active_document(fake_krita):
    """No active view -> no document -> no model: every action is a logged
    no-op and nothing raises (degradation parity with ``GridStateService``)."""
    mod, _fake, _instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow()  # activeView() -> None
    ext.createActions(window)
    for emit in _emitters(window).values():
        emit()
    assert ext.service.active_doc_id is None
    assert RecordingContext.instances[0].binds, "the window's context still exists"


# -------------------------------------------------------------------- docker


def test_create_docker_registers_exactly_one_factory(fake_krita):
    """One ``DockWidgetFactory("composition_grid_docker", DockRight, class over
    krita.DockWidget)``, registered once — and idempotent: a second call must
    not add a factory, because two docker entries per window means two panels
    per window and ``GridStateService.on_change`` has no unsubscribe (a stale
    panel then raises inside ``_notify`` on every later mutation)."""
    mod, fake, instance = fake_krita
    ext = _extension(mod)
    factory = ext.create_docker()
    assert instance.factories == [factory]
    assert factory.id == "composition_grid_docker"
    assert factory.area is fake.DockWidgetFactoryBase.DockRight
    assert issubclass(factory.docker_class, fake.DockWidget)
    assert ext.docker_factory is factory
    assert ext.create_docker() is factory
    assert len(instance.factories) == 1, "the docker factory is registered exactly once"


def test_docker_providers_route_through_the_service_and_the_context(fake_krita):
    """The docker is built over the extension's own service, the context-derived
    doc-size provider, the injected settings store and ``canvasChanged`` routing
    — the three seams ``make_panel_docker_class`` expects."""
    mod, _fake, _instance = fake_krita
    settings = RecordingSettings()
    ext = _extension(mod, settings=settings, context_factory=_context_factory())
    assert ext.settings is settings
    assert ext.doc_size_provider() is None, "no bound context -> degraded provider"
    ext.createActions(FakeWindow(FakeDocument("docker-doc"), FakeCanvas()))
    assert ext.doc_size_provider() == (DOC_W, DOC_H)
    assert callable(ext.handle_canvas_changed)


def _located_widget(window):
    """The canvas widget currently located for *window* — the identity the rebind
    decision compares (resolved from the window's Qt side, never from the
    libkis canvas wrapper, which offers no widget getter)."""
    return _fake_widget_resolver(_qwindow_of(window))


def test_canvas_changed_rebinds_the_window_context_only_for_a_new_canvas(fake_krita):
    """``canvasChanged`` (the libkis virtual forwarded by docker_registration) is
    a payload-free WAKE-UP: it re-resolves the active window's OWN canvas and
    rebinds the window's context when the canvas WIDGET located for that window is
    genuinely new, and re-routes the document — but not again for a wake-up whose
    located widget is unchanged (the wrappers are fresh objects every call; a rebind
    there would only dispose and rebuild a perfectly good overlay).
    """
    mod, _fake, instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("doc-a"), FakeCanvas())
    instance.active_window = window  # what Krita.instance().activeWindow() answers
    ext.createActions(window)
    context = RecordingContext.instances[0]
    assert len(context.binds) == 1
    # a wake-up for the SAME widget (even handed the bound wrapper) must not rebind
    ext.handle_canvas_changed(context.canvas)
    assert context.rebinds == [], "same widget -> no redundant rebind"
    fresh = FakeCanvas(640.0, 480.0, "fresh")
    # the fixture stays SELF-CONSISTENT: the wake-up binds the
    # canvas its own window switched to — the window is the source of truth, the
    # delivered payload is ignored. ``set_view`` moves the canvas wrapper AND the
    # widget located under the window, as a real view switch does. doc-a's
    # document keeps its 800x600 extent.
    window.set_view(FakeView(FakeDocument("doc-a"), fresh))
    ext.handle_canvas_changed(fresh)
    assert [canvas.label for _w, _v, canvas in context.rebinds] == ["fresh"]
    assert context.widget is _located_widget(window), "rebound onto the new widget"
    # re-routing the SAME document never resets the user's element: a canvas size
    # only ever feeds the default element of a grid added afterwards
    service.add_grid()
    grid_a = service.selected("doc-a")
    assert grid_a.transform == def_element(None, DOC_W, DOC_H)
    ext.handle_canvas_changed(fresh)
    assert service.selected("doc-a").transform == grid_a.transform, (
        "a wake-up never rebuilds the element of a grid that exists"
    )
    # ... and a different document on the same window gets the NEW size — while the
    # unchanged canvas widget costs no second rebind (no overlay rebuild on a
    # pure document switch)
    window.set_view(
        FakeView(FakeDocument("doc-b", 640.0, 480.0), fresh),
        widget=_located_widget(window),
    )
    ext.handle_canvas_changed(fresh)
    assert [canvas.label for _w, _v, canvas in context.rebinds] == ["fresh"], (
        "a document switch on the unchanged widget must not rebuild the overlay"
    )
    service.add_grid()
    assert service.selected("doc-b").transform == def_element(None, 640.0, 480.0)


def test_unreadable_view_never_tears_a_working_overlay_down(fake_krita):
    """A repeat ``createActions`` / ``canvasChanged`` while ``activeView()`` is
    momentarily unreadable (mid view-switch) must NOT rebind: the real
    ``CanvasContext`` would unbind, dispose a perfectly good overlay and leave
    the grid hidden. Only a genuinely different canvas re-attaches."""
    mod, _fake, instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    instance.active_window = window
    ext.createActions(window)
    context = RecordingContext.instances[0]
    assert len(context.binds) == 1
    window._view = None  # Krita mid-switch: no active view to resolve
    ext.createActions(_twin_of(window))
    ext.handle_canvas_changed(None)
    assert context.rebinds == []
    assert context.canvas is not None, "the overlay stays on the canvas it had"
    assert context.widget is _located_widget(window), (
        "the widget the overlay sits on is untouched by the unreadable view"
    )


def test_one_context_per_window_keyed_by_qwindow_identity(fake_krita):
    """One docker/overlay per window: contexts are keyed by the STABLE
    ``qwindow()`` identity, so a fresh libkis ``Window`` wrapper for the SAME
    window never creates a second context (second overlay), while a real second
    window gets its own. The faithful context double repaints on every service
    notification (the surviving single hub path), so the twin re-activation —
    which notifies nothing (same doc already active + ensured) — must leave the
    first overlay's repaint count untouched (the no-extra-overlay this pin checks)."""
    mod, _fake, _instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    first = FakeWindow(FakeDocument("a"), FakeCanvas())
    ext.createActions(first)
    ctx0 = RecordingContext.instances[0]
    paints_after_first = ctx0.overlay.paints
    ext.createActions(_twin_of(first))
    assert len(RecordingContext.instances) == 1, (
        "a fresh wrapper for the same qwindow adds no second context/overlay"
    )
    assert ctx0.overlay.paints == paints_after_first, (
        "a twin re-activation of the same doc repaints no extra overlay"
    )
    second = FakeWindow(FakeDocument("b"), FakeCanvas())
    ext.createActions(second)
    assert len(RecordingContext.instances) == 2
    assert len(ext.contexts) == 2


# ------------------------------------------------------- per-window lifecycle


def test_destroyed_window_prunes_everything_held_for_it(fake_krita):
    """Strong references are kept — which is exactly the leak: nothing used to
    ever leave the per-window holders (contexts, action pairs, and the
    connection entries whose partials capture the dead libkis ``Window``). The
    qwindow's ``destroyed`` signal is the documented teardown point, so a closed
    window must unbind its context and drop out of every holder, leaving nothing
    bounded by the app session."""
    mod, _fake, _instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("gone"), FakeCanvas())
    ext.createActions(window)
    context = RecordingContext.instances[0]
    assert len(ext.contexts) == 1
    assert len(ext.held_actions) == 6
    assert ext.held_connections, "the activeViewChanged/destroyed refs are held"

    window.qwindow().destroyed.emit(None)

    assert ext.contexts == (), "the dead window's context leaves the map"
    assert context.unbindings == 1, "the overlay is disposed, not just unreferenced"
    assert ext.held_connections == (), (
        "no partial may keep the destroyed window's objects alive"
    )
    assert ext.held_actions == [], "the dead window's actions leave the holder"


def test_re_opened_window_gets_exactly_one_context_and_six_actions(fake_krita):
    """The prune must leave the extension usable: a window wired twice (same
    qwindow) never gets a second context, and after a prune a fresh window gets
    its own single context again — bounded by the app session, not by the number
    of windows Krita has had."""
    mod, _fake, _instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    ext.createActions(window)
    ext.createActions(_twin_of(window))
    assert len(RecordingContext.instances) == 1
    assert [action.id for action, _slot in ext.held_actions] == ACTION_IDS

    window.qwindow().destroyed.emit(None)
    other = FakeWindow(FakeDocument("b"), FakeCanvas())
    ext.createActions(other)
    ext.createActions(_twin_of(other))
    assert len(ext.contexts) == 1
    assert [action.id for action, _slot in ext.held_actions] == ACTION_IDS


# ------------------------------------------------------ canvas ownership gate


def test_a_foreign_canvas_changed_cannot_steal_the_window_context(fake_krita, caplog):
    """``DockWidget.canvasChanged`` is delivered by the docker, and a background
    window's docker can fire while another window is active. The route is
    payload-FREE: it re-resolves the ACTIVE window's own
    canvas and binds THAT, so a background docker's canvas can never land on the
    active window's overlay — no identity comparison of unsound fresh wrappers is
    even involved. The wake-up is logged so an operator can see it fired."""
    mod, _fake, instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    canvas_a, canvas_b = FakeCanvas(label="a"), FakeCanvas(640.0, 480.0, "b")
    window_a = FakeWindow(FakeDocument("doc-a"), canvas_a)
    window_b = FakeWindow(FakeDocument("doc-b"), canvas_b)
    ext.createActions(window_a)
    ext.createActions(window_b)
    context_a, context_b = RecordingContext.instances
    assert context_a.canvas.label == "a" and context_b.canvas.label == "b"
    assert context_a.widget is _located_widget(window_a), (
        "each context is attached to the widget of ITS OWN window"
    )
    assert context_b.widget is _located_widget(window_b)

    instance.active_window = window_a  # A is active while B's docker reports its canvas
    with caplog.at_level(logging.INFO, logger="composition_grid"):
        ext.handle_canvas_changed(canvas_b)
    assert context_a.rebinds == [], "window B's canvas must not be bound onto A"
    assert context_a.canvas.label == "a", "window A keeps its own canvas"
    assert context_a.widget is _located_widget(window_a)
    assert any("own" in r.getMessage().lower() for r in caplog.records), (
        "the wake-up is logged, never silent"
    )

    # the legitimate route still works: B's OWN new canvas, while B is active
    fresh_b = FakeCanvas(320.0, 240.0, "fresh-b")
    window_b.set_view(FakeView(FakeDocument("doc-b", 320.0, 240.0), fresh_b))
    instance.active_window = window_b
    ext.handle_canvas_changed(fresh_b)
    assert [canvas.label for _w, _v, canvas in context_b.rebinds] == ["fresh-b"], (
        "the owner's own canvas must still rebind"
    )
    assert context_b.widget is _located_widget(window_b)
    assert context_a.canvas.label == "a", "and window A's context stays on A's canvas"
    assert context_a.widget is _located_widget(window_a)


# ------------------- the payload-free wake-up channel re-resolves its own canvas


def test_canvas_changed_wake_up_rebinds_a_genuinely_new_own_canvas(fake_krita):
    """A wake-up gate keyed on the delivered payload is silent on EVERY real
    ``canvasChanged`` because a freshly-resolved wrapper is never ``is``-equal to
    the payload. With the wake-up resolving the window's OWN canvas, a switch of
    the canvas WIDGET located for that window (resetCanvas / external rebind)
    DOES rebind — the recovery channel is alive again.
    The delivered payload is deliberately a throwaway object to prove it is not
    consulted."""
    mod, _fake, instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    instance.active_window = window
    ext.createActions(window)
    context = RecordingContext.instances[0]
    original = context.widget
    switched = FakeCanvas(640.0, 480.0, "switched")
    # the window's OWN canvas moved: a fresh view wrapper AND a new widget under it
    new_widget = window.set_view(FakeView(FakeDocument("a"), switched))
    ext.handle_canvas_changed(object())  # payload irrelevant to the resolution
    assert [canvas.label for _w, _v, canvas in context.rebinds] == ["switched"]
    assert context.widget is not original, "the wake-up rebound to the new widget"
    assert context.widget is new_widget


def test_canvas_changed_wake_up_is_idempotent_across_fresh_wrappers(fake_krita):
    """The wake-up re-resolves a FRESH canvas wrapper on every
    call, so rebinding must key on the canvas widget LOCATED FOR THE WINDOW
    (a libkis ``Canvas`` has no widget getter, so nothing on the wrapper is
    stable) — otherwise the same live canvas would be torn down and rebuilt on every
    docker emission. Two wake-ups whose window still resolves the same widget rebind
    NOTHING (idempotence keyed on the fresh WRAPPER identity rebinds every time)."""
    mod, _fake, instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    instance.active_window = window
    ext.createActions(window)
    context = RecordingContext.instances[0]
    ext.handle_canvas_changed(None)  # fresh wrapper resolved internally, widget unchanged
    ext.handle_canvas_changed(canvas_twin := window._view.canvas())
    assert canvas_twin is not context.canvas, "the fixture really hands fresh wrappers"
    assert context.rebinds == [], "same widget across fresh wrappers -> no rebind churn"
    assert context.widget is _located_widget(window), "still on the widget it bound"


def test_canvas_changed_wake_up_on_unreadable_view_never_misbinds(fake_krita, caplog):
    """A wake-up for a window whose OWN ``activeView()`` is
    momentarily unreadable (mid view-switch) must degrade to a logged skip — the
    last-known working overlay is left intact, never rebound onto nothing and
    never torn down."""
    mod, _fake, instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    instance.active_window = window
    ext.createActions(window)
    context = RecordingContext.instances[0]
    bound = context.canvas
    window._view = None  # Krita mid-switch: the window resolves no canvas at all
    with caplog.at_level(logging.INFO, logger="composition_grid"):
        ext.handle_canvas_changed(FakeCanvas(320.0, 240.0))  # payload ignored
    assert context.rebinds == [], "no own canvas to resolve -> no rebind"
    assert context.canvas is bound, "the working overlay is not torn down"
    assert any("wake-up" in r.getMessage().lower() for r in caplog.records)


def test_repeated_create_actions_does_not_rebind_or_duplicate(fake_krita):
    """Krita may call ``createActions`` again for a live window (e.g. a
    re-opened docker). With fresh canvas wrappers a ``context.canvas is not
    canvas`` test is ALWAYS true, so every such call churns a rebind; the
    widget-keyed idempotence makes a repeat call neither create a second context
    NOR rebind the working overlay."""
    mod, _fake, _instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    ext.createActions(window)
    context = RecordingContext.instances[0]
    assert len(context.binds) == 1 and context.rebinds == []
    ext.createActions(_twin_of(window))
    assert len(RecordingContext.instances) == 1, "no second context for the same window"
    assert context.rebinds == [], "the live window's overlay is not rebuilt"


# ---------------- the fake canvas matches the real libkis ``Canvas.h`` exactly


def test_the_fake_canvas_exposes_no_widget_getter_just_like_libkis_canvas():
    """THE FAKE-SHAPE PIN (the honest tightening): ``libs/libkis/Canvas.h`` has no
    method returning a widget and no ``canvasWidget``/``widget``/``qwidget`` name —
    the ``KisCanvas2.canvasWidget()`` is a C++ internal named
    in the widget-HIERARCHY description, not part of the Python-visible Canvas API.

    A fake canvas that offered one would be MORE PERMISSIVE THAN THE HOST: a helper
    could read it, find a stable widget, and the suite would pass a rebind
    idempotence real Krita never had (there the lookup degrades to wrapper identity,
    so every wake-up rebounds). Asserting the absence here is what stops that class
    of mistake from coming back."""
    canvas = FakeCanvas()
    for name in ("canvasWidget", "widget", "qwidget"):
        assert not hasattr(canvas, name), f"the fake canvas invented {name}()"
    # and it still hands fresh wrappers per call — a fact the code must survive
    assert canvas.twin() is not canvas, "wrappers stay fresh objects"


def test_rebind_idempotence_is_keyed_on_the_located_widget_not_the_wrapper(
    fake_krita,
):
    """RED-PROOF (the regression pin): with a REAL-shaped canvas —
    no widget getter, a fresh wrapper per ``canvas()`` call — a ``canvasChanged``
    wake-up whose located widget is UNCHANGED must produce ZERO rebinds. Code that
    keyed on the wrapper identity fails exactly here, with one rebind per wake-up,
    because a helper that asks the libkis wrapper for a widget can only ever degrade
    to the fresh wrapper itself. The opposite case is pinned in the same
    test: the moment the located widget really differs, the very same channel rebinds
    EXACTLY once."""
    mod, _fake, instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory())
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    instance.active_window = window
    ext.createActions(window)
    context = RecordingContext.instances[0]
    widget = _located_widget(window)
    assert context.widget is widget, "the context is attached to the located widget"

    for _ in range(3):  # three docker emissions, one unchanged live canvas
        ext.handle_canvas_changed(None)
    assert window._view.canvas() is not window._view.canvas(), (
        "the host really hands a fresh wrapper per call"
    )
    assert context.rebinds == [], "an unchanged located widget is not a change"
    assert context.canvas is not None and context.widget is widget

    window.set_view(FakeView(FakeDocument("a"), FakeCanvas(640.0, 480.0, "new")))
    ext.handle_canvas_changed(None)
    assert len(context.rebinds) == 1, "a proven widget change still rebinds once"
    assert context.widget is _located_widget(window), "and onto the newly located widget"


def test_an_unlocatable_widget_is_never_a_proven_change(fake_krita):
    """The other half of the rule: when the resolver locates NO widget (a
    transient widget graph — the real locator's documented ``None``), the change is
    not PROVEN and the overlay is left alone; a wake-up must never tear a working
    overlay down on a guess."""
    mod, _fake, instance = fake_krita
    ext = _extension(
        mod, context_factory=_context_factory(), widget_resolver=lambda _key: None
    )
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    instance.active_window = window
    ext.createActions(window)
    context = RecordingContext.instances[0]
    bound = context.canvas
    window.set_view(FakeView(FakeDocument("a"), FakeCanvas(640.0, 480.0, "new")))
    ext.handle_canvas_changed(None)
    assert context.rebinds == [], "nothing located -> nothing proven -> no rebind"
    assert context.canvas is bound, "the working overlay survives the unreadable graph"


def test_a_raising_widget_resolver_degrades_to_a_logged_none(fake_krita, caplog):
    """The production locator never raises, but an injected resolver inherits no such
    contract: a failure there is a DEBUG record and a ``None`` answer (no rebind),
    never an exception travelling back out through the docker's signal emission — and
    never an empty ``except``."""

    def broken(_key):
        raise RuntimeError("widget graph torn down")

    mod, _fake, instance = fake_krita
    ext = _extension(mod, context_factory=_context_factory(), widget_resolver=broken)
    window = FakeWindow(FakeDocument("a"), FakeCanvas())
    instance.active_window = window
    ext.createActions(window)
    context = RecordingContext.instances[0]
    window.set_view(FakeView(FakeDocument("a"), FakeCanvas(640.0, 480.0, "new")))
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        ext.handle_canvas_changed(None)
    assert context.rebinds == [], "a raising resolver is not a proven change"
    assert any(
        "locating the canvas widget" in r.getMessage() for r in caplog.records
    ), "the swallow is logged, not silent"


def test_the_default_widget_resolver_is_the_real_locator(fake_krita):
    """The seam defaults to production: without an injected resolver the extension
    asks :func:`canvas_locator.find_canvas_widget`, i.e. the widget under the
    ``qwindow`` — the same object ``CanvasContext.bind`` parents the overlay to, so
    the two sides compare the very same Qt object."""
    mod, _fake, _instance = fake_krita
    ext = mod.CompositionGridExtension(FakeKritaInstance(), service=GridStateService())
    assert ext._widget_resolver is canvas_locator.find_canvas_widget


# ------------------- drag notification routes through the service hub


def test_drag_emit_routes_through_the_service_exactly_once(fake_krita):
    """ONE notification path: a drag writes ``model.transform``
    and calls ``model.emit()``, which forwards to the owning
    :class:`GridStateService` — the single hub every model subscribes at creation
    (there is no per-model repaint bridge in the entry any more). The model carries
    EXACTLY ONE observer (the service's ``_notify``): never zero (the stale-field /
    no-save defect) and never double-subscribed on re-activation or notify storms."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service, context_factory=_context_factory())
    ext.createActions(FakeWindow(FakeDocument("drag-doc"), FakeCanvas()))
    model = service.selected("drag-doc")
    assert model is None, "routing creates no grid; the drag tests ask for one"
    service.add_grid()
    model = service.selected("drag-doc")
    assert model.callbacks == [service._notify], (
        "the service is the single per-model subscriber; no double path"
    )
    # re-activation of the same document (new window) must not stack a second hub ...
    ext.createActions(FakeWindow(FakeDocument("drag-doc"), FakeCanvas()))
    assert model.callbacks == [service._notify]
    # ... and NEITHER may any later service mutation: ensure() subscribes at
    # creation only, so a notify storm can never grow into N repaints per emit.
    service.toggle_visible()
    service.set_type(GRID_IDS[1])
    service.set_style(width_px=2.0)
    service.reset_transform(DOC_W, DOC_H)
    assert model.callbacks == [service._notify], "notify storms must never stack the hub"
    context = RecordingContext.instances[0]
    context.overlay.paints = 0
    model.emit()  # the drag mutation's signal
    assert context.overlay.paints == 1, "one model emit -> exactly one repaint"
    model.emit()
    assert context.overlay.paints == 2


def test_each_drag_step_repaints_exactly_once(fake_krita):
    """The surviving single path through the real ``TransformController``: every
    ``drag_to`` -> ``model.emit`` -> service ``_notify`` repaints the context's
    overlay EXACTLY once — none missing (the old frozen-mid-drag defect) and no
    second notification per step."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service, context_factory=_context_factory())
    ext.createActions(FakeWindow(FakeDocument("drag-doc"), FakeCanvas(100.0, 100.0)))
    service.add_grid()
    model = service.selected("drag-doc")
    context = RecordingContext.instances[0]
    # a service-level mutation between activation and the drag (a notify the
    # single-hub wiring must survive without stacking) — see the test above
    service.set_style(width_px=2.0)
    view = _view_state(100.0)
    controller = TransformController(
        view_state_provider=lambda: view, model_provider=lambda: model
    )
    start_cx = model.transform.cx
    assert controller.begin_drag("move", (50.0, 50.0), False) is True
    paints = context.overlay.paints
    assert controller.drag_to((60.0, 50.0), False) is True
    assert context.overlay.paints == paints + 1
    assert controller.drag_to((70.0, 50.0), False) is True
    assert context.overlay.paints == paints + 2
    controller.end_drag()
    # recomputed from the BEGIN snapshot: the two steps total +20 doc px, not 30
    assert model.transform.cx == pytest.approx(start_cx + 20.0)


# --------------------------------------------------------------- persistence


def test_the_kritarc_store_seeds_the_added_grid_only(fake_krita):
    """PERSISTENCE changed shape: the global "last used" store no longer gives a
    document a grid on activation — a file without its own annotation opens
    WITHOUT one. The store is read exactly when the artist adds a grid, and
    seeds its type, params and style (the transform stays the doc-centered
    default). No tuple fix-ups: the JSON list of ``dash``/``color_rgba`` is
    normalized at the Style root, so a stored list already yields the pinned
    tuples."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    settings = RecordingSettings(
        {
            "grid_type_id": "uniform_grid",
            "params": {"columns": 5, "rows": 4, "margin": 0.1},
            "style": {
                "color_rgba": [0.2, 0.2, 0.2, 1.0],
                "width_px": 2.0,
                "opacity": 0.5,
                "dash": [6.0, 3.0],
            },
        }
    )
    ext = _extension(mod, service=service, settings=settings)
    ext.createActions(FakeWindow(FakeDocument("seeded"), FakeCanvas()))

    assert service.grids("seeded") == (), "a fresh document opens without a grid"
    assert settings.loads == 0, "activation no longer reads the store at all"

    service.add_grid()
    model = service.selected("seeded")
    assert settings.loads == 1, "the seed is read exactly when a grid is added"
    assert model.grid_type_id == "uniform_grid"
    assert model.params["columns"] == 5
    assert model.style.width_px == 2.0
    assert model.style.opacity == 0.5
    assert model.style.dash == (6.0, 3.0)
    assert model.style.color_rgba == (0.2, 0.2, 0.2, 1.0)
    assert model.transform == def_element(None, DOC_W, DOC_H)

    # a second activation of the same document adds nothing and reads nothing
    ext.createActions(FakeWindow(FakeDocument("seeded"), FakeCanvas()))
    assert len(service.grids("seeded")) == 1
    assert settings.loads == 1


def test_a_broken_seed_never_blocks_the_grid_the_artist_adds(fake_krita, caplog):
    """An unregistered stored type and an unknown stored parameter are skipped
    with a log instead of raising, and a stored ``transform`` is IGNORED: the
    seed describes values, the document's own geometry is the canvas's job."""
    mod, _fake, _instance = fake_krita
    settings = RecordingSettings(
        {
            "grid_type_id": "not_a_grid",
            "params": {"nonsense": 1},
            "style": None,
            "transform": {"cx": 1.0, "cy": 2.0, "width": 3.0, "height": 4.0},
        }
    )
    service = GridStateService()
    ext = _extension(mod, service=service, settings=settings)
    ext.createActions(FakeWindow(FakeDocument("garbage"), FakeCanvas()))
    with caplog.at_level(logging.WARNING, logger="composition_grid"):
        service.add_grid()
    model = service.selected("garbage")
    assert model.grid_type_id == GRID_IDS[0], "the unusable seed falls back"
    assert model.transform == def_element(None, DOC_W, DOC_H)
    assert model.params.get("nonsense") is None

    # a usable style still lands when the stored type is garbage
    other = GridStateService()
    settings.state = {"grid_type_id": GRID_IDS[0], "params": {}, "style": {"width_px": 3.0}}
    _extension(mod, service=other, settings=settings).createActions(
        FakeWindow(FakeDocument("second"), FakeCanvas())
    )
    other.add_grid()
    assert other.selected("second").style == Style(width_px=3.0)


def test_a_failing_store_never_breaks_activation_or_an_add(fake_krita, caplog):
    """A throwing transport must not break document activation (nothing is read
    there any more) nor the artist's Add (the seed falls back to the defaults) —
    and both degradations are logged, never silently eaten."""

    class Exploding:
        def load(self):
            raise RuntimeError("transport is gone")

        def save(self, model):
            raise RuntimeError("transport is gone")

    mod, _fake, _instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service, settings=Exploding())
    with caplog.at_level(logging.WARNING, logger="composition_grid"):
        ext.createActions(FakeWindow(FakeDocument("boom"), FakeCanvas()))
    assert service.is_routed("boom"), "activation survives a failed store"
    assert service.grids("boom") == ()

    with caplog.at_level(logging.WARNING, logger="composition_grid"):
        assert service.add_grid() is not None, "a broken seed provider still adds"
    assert service.selected("boom").grid_type_id == GRID_IDS[0]
    assert any("seed" in record.message.lower() for record in caplog.records)


class _CountingTransport:
    """In-memory SettingsTransport recording every read and write."""

    def __init__(self):
        self.data = {}
        self.reads = 0
        self.writes = []

    def read(self, group, key):
        self.reads += 1
        return self.data.get(f"{group}|{key}")

    def write(self, group, key, value):
        self.writes.append(value)
        self.data[f"{group}|{key}"] = value


_LAST_USED_UNIFORM = {
    "version": 2,
    "grid_type_id": "uniform_grid",
    "visible": True,
    "params": {"columns": 4, "rows": 5, "margin": 0.1},
    "style": {"color_rgba": [0.1, 0.2, 0.3, 1.0], "width_px": 2.0, "opacity": 1.0, "dash": [6.0, 3.0]},
    "transform": {"cx": 400.0, "cy": 300.0, "width": 480.0, "height": 480.0, "rotation_deg": 0.0, "ratio_locked": True},
}


def _panel_and_store_with_last_used_uniform():
    """A pre-existing real GridPanel plus a store whose last-used seed is
    uniform_grid with ``columns=4`` (the shape ``encode_model`` writes)."""
    transport = _CountingTransport()
    transport.data["composition_grid|active_grid"] = json.dumps(_LAST_USED_UNIFORM, sort_keys=True)
    service = GridStateService()
    _ensure_app()
    panel = GridPanel(service, settings=JsonGridSettings(transport))
    return service, panel, transport


def test_activation_neither_reads_the_seed_nor_overwrites_it(fake_krita):
    """A document opens WITHOUT a grid, so the panel has no selected model to
    save: the activation of a file neither consumes the seed nor lets the docker
    store its defaults over the artist's last-used values. The seed is spent by
    the ADD, and the added grid is what the store then describes."""
    mod, _fake, _instance = fake_krita
    service, _panel, transport = _panel_and_store_with_last_used_uniform()
    ext = _extension(mod, service=service, settings=JsonGridSettings(transport))
    ext.createActions(FakeWindow(FakeDocument("first"), FakeCanvas()))

    assert transport.reads == 0, "nothing to seed: the store is not consulted"
    assert transport.writes == [], "no selected grid: the panel stores nothing"

    assert service.add_grid() is not None
    assert transport.reads == 1, "the seed is read exactly once, by the add"
    assert len(transport.writes) == 1, "the grid the artist added becomes the last used one"
    stored = json.loads(transport.writes[0])
    assert stored["grid_type_id"] == "uniform_grid"
    assert stored["params"]["columns"] == 4


def test_a_later_new_document_opens_the_same_way(fake_krita):
    mod, _fake, _instance = fake_krita
    service, _panel, transport = _panel_and_store_with_last_used_uniform()
    ext = _extension(mod, service=service, settings=JsonGridSettings(transport))
    ext.createActions(FakeWindow(FakeDocument("first"), FakeCanvas()))
    ext.createActions(FakeWindow(FakeDocument("second"), FakeCanvas()))
    assert service.grids("second") == (), "a new document opens without a grid"
    assert transport.reads == 0 and transport.writes == []

    service.add_grid()
    model = service.selected("second")
    assert model.grid_type_id == "uniform_grid"
    assert model.params["columns"] == 4
    assert model.transform == def_element(None, DOC_W, DOC_H), (
        "a stored transform seeds nothing: a new grid is centered on ITS document"
    )


def test_reactivating_a_document_neither_reloads_nor_resaves(fake_krita):
    mod, _fake, _instance = fake_krita
    service, _panel, transport = _panel_and_store_with_last_used_uniform()
    ext = _extension(mod, service=service, settings=JsonGridSettings(transport))
    ext.createActions(FakeWindow(FakeDocument("first"), FakeCanvas()))
    service.add_grid()
    reads, writes = transport.reads, len(transport.writes)
    ext.createActions(FakeWindow(FakeDocument("first"), FakeCanvas()))
    assert transport.reads == reads, "a routed document does not consume the seed again"
    assert len(transport.writes) == writes, "a no-op activation saves nothing"


def test_user_mutation_after_activation_saves_exactly_once(fake_krita):
    mod, _fake, _instance = fake_krita
    service, _panel, transport = _panel_and_store_with_last_used_uniform()
    ext = _extension(mod, service=service, settings=JsonGridSettings(transport))
    ext.createActions(FakeWindow(FakeDocument("first"), FakeCanvas()))
    service.add_grid()
    transport.writes.clear()
    service.set_param("rows", 7)
    assert len(transport.writes) == 1
    assert json.loads(transport.writes[0])["params"]["rows"] == 7


# --------------------------------------------------- in-document (annotation) state

_GRID_ANNOTATION = "composition_grid"
_OTHER_ANNOTATION = "other_type"


def _entry(**overrides):
    """One valid version-2 grid entry: a hidden, rotated uniform_grid."""
    entry = {
        "grid_id": "g1",
        "label": "1",
        "grid_type_id": "uniform_grid",
        "params": {"columns": 6, "rows": 3, "margin": 0.2},
        "style": {
            "color_rgba": [0.5, 0.25, 0.75, 1.0],
            "width_px": 3.0,
            "opacity": 0.5,
            "dash": [6.0, 3.0],
        },
        "transform": {
            "cx": 111.0,
            "cy": 222.0,
            "width": 333.0,
            "height": 444.0,
            "rotation_deg": 30.0,
            "ratio_locked": False,
        },
        "visible": False,
    }
    entry.update(overrides)
    return entry


def _annotation_payload(*entries, selected="g1"):
    """A valid version-2 state CONTAINER as bytes (the host's annotation shape):
    the document's grids in list order plus the selected id. Called without
    arguments it describes the one hidden uniform_grid of :func:`_entry`;
    ``selected=None`` is a real stored state, and an empty list is
    :func:`_empty_container`."""
    payload = {
        "version": 2,
        "selected": selected,
        "grids": [dict(entry) for entry in (entries or (_entry(),))],
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _empty_container():
    """The version-2 container of a document that stores NO grids."""
    return json.dumps(
        {"version": 2, "selected": None, "grids": []}, sort_keys=True
    ).encode("utf-8")


def _v1_payload(**overrides):
    """A version-1 state document as bytes (what an older build stored)."""
    payload = {
        "version": 1,
        "present": True,
        "visible": False,
        "grid_type_id": "uniform_grid",
        "params": {"columns": 6, "rows": 3, "margin": 0.2},
        "style": {
            "color_rgba": [0.5, 0.25, 0.75, 1.0],
            "width_px": 3.0,
            "opacity": 0.5,
            "dash": [6.0, 3.0],
        },
        "transform": {
            "cx": 111.0,
            "cy": 222.0,
            "width": 333.0,
            "height": 444.0,
            "rotation_deg": 30.0,
            "ratio_locked": False,
        },
    }
    payload.update(overrides)
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def test_document_annotation_restores_the_whole_state_and_is_read_once(fake_krita):
    """A .kra that carries our annotation wins over the global last-used
    settings: the grid comes back with EVERY field (visible, transform, label,
    id) and the selection, the annotation is read exactly once for the document,
    and — the part that keeps an untouched file untouched — nothing is written
    and the document is not marked modified."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    settings = RecordingSettings({"grid_type_id": "phi_grid", "params": {}, "style": None})
    document = FakeDocument("with_state")
    document.annotations[_GRID_ANNOTATION] = _annotation_payload()
    ext = _extension(mod, service=service, settings=settings)
    ext.createActions(FakeWindow(document, FakeCanvas()))
    grids = service.grids("with_state")
    model = service.selected("with_state")

    assert document.set_annotation_calls == [], "restoring must not write"
    assert document.modified is False, "restoring must not mark the document modified"
    assert document.annotation_calls.count(_GRID_ANNOTATION) == 1
    assert settings.loads == 0, "a document with its own state never spends the seed"
    assert len(grids) == 1 and grids[0] is model
    assert service.selected_grid_id("with_state") == "g1"
    assert model.grid_id == "g1" and model.label == "1"
    assert model.grid_type_id == "uniform_grid", "the annotation beats the global store"
    assert model.params["columns"] == 6
    assert model.style.width_px == 3.0
    assert model.style.color_rgba == (0.5, 0.25, 0.75, 1.0)
    assert model.style.dash == (6.0, 3.0)
    assert model.transform == TransformState(
        cx=111.0, cy=222.0, width=333.0, height=444.0, rotation_deg=30.0, ratio_locked=False
    )
    assert model.visible is False
    assert model.edit_session is False, "the canvas session is transient"

    ext.createActions(FakeWindow(document, FakeCanvas()))
    assert document.annotation_calls.count(_GRID_ANNOTATION) == 1, "not re-read once restored"


def test_a_container_of_several_grids_comes_back_in_order_with_its_selection(fake_krita):
    """MULTIPLE grids are the point of version 2: the list order, every grid's
    own type/element and the stored selection all come back, and a mutation then
    touches only the selected one."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("several")
    document.annotations[_GRID_ANNOTATION] = _annotation_payload(
        _entry(grid_id="g1", label="1", grid_type_id="uniform_grid"),
        _entry(
            grid_id="g2",
            label="sky",
            grid_type_id="center_cross",
            params={},
            visible=True,
            transform={"cx": 20.0, "cy": 30.0, "width": 40.0, "height": 50.0,
                       "rotation_deg": 0.0, "ratio_locked": True},
        ),
        _entry(grid_id="g3", label="3", grid_type_id="rule_of_thirds", params={}),
        selected="g2",
    )
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(document, FakeCanvas())
    )
    grids = service.grids("several")

    assert [g.grid_id for g in grids] == ["g1", "g2", "g3"], "the stored order is the order"
    assert service.selected_grid_id("several") == "g2"
    assert grids[1].label == "sky" and grids[1].visible is True
    assert grids[1].transform.cx == pytest.approx(20.0)
    assert grids[2].visible is False, "the entry keeps its own visibility"

    service.set_style(width_px=7.0)
    assert grids[1].style.width_px == 7.0
    assert grids[0].style.width_px == 3.0, "an unselected grid keeps its own style"


def test_a_version_1_annotation_migrates_to_one_grid_labelled_1(fake_krita):
    """An older file is not a failure: ``present: true`` becomes the document's
    single grid, labelled ``"1"`` with a freshly minted id, every stored field
    intact, and it is selected — so the docker and the canvas behave as before."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("v1")
    document.annotations[_GRID_ANNOTATION] = _v1_payload()
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(document, FakeCanvas())
    )
    grids = service.grids("v1")

    assert len(grids) == 1
    model = grids[0]
    assert model.label == "1", "the version-1 grid has no label of its own"
    assert model.grid_id and service.selected_grid_id("v1") == model.grid_id
    assert model.grid_type_id == "uniform_grid"
    assert model.params["columns"] == 6
    assert model.transform.rotation_deg == 30.0
    assert model.visible is False
    assert document.set_annotation_calls == [], "a migration is still a restore"

    # the migrated grid is a normal grid: a second Add continues the numbering
    second = service.add_grid()
    assert service.get(second, "v1").label == "2"


def test_a_version_1_removed_grid_opens_as_no_grid_at_all(fake_krita):
    """``present: false`` meant "the artist removed the grid". Removal now
    DELETES, so the migration answers the empty list: the document opens without
    a grid, without a selection and without writing anything."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("removed_doc")
    document.annotations[_GRID_ANNOTATION] = _v1_payload(present=False)
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(document, FakeCanvas())
    )

    assert service.grids("removed_doc") == ()
    assert service.selected_grid_id("removed_doc") is None
    assert document.set_annotation_calls == []

    # and the artist can add one: the seed's grid, not the deleted one back
    grid_id = service.add_grid()
    assert grid_id is not None
    added = service.get(grid_id, "removed_doc")
    assert added.label == "1"
    assert added.grid_type_id == GRID_IDS[0], "no seed, no stored state: plain defaults"
    assert added.params.get("columns") is None, "the deleted grid does not resurface"


def test_a_stored_empty_container_opens_without_a_grid(fake_krita):
    """Version 2 stores the empty list explicitly; opening such a file restores
    nothing and writes nothing, and its first Add introduces the annotation."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("empty_container")
    document.annotations[_GRID_ANNOTATION] = _empty_container()
    ext = _extension(mod, service=service, settings=RecordingSettings())
    ext.createActions(FakeWindow(document, FakeCanvas()))

    assert service.grids("empty_container") == (), "no grids stored means no grids open"
    assert document.annotation_calls.count(_GRID_ANNOTATION) == 1, "read once"
    assert document.set_annotation_calls == []

    service.add_grid()
    assert len(document.set_annotation_calls) == 1, "the file owns its state, so a mutation writes it"


def test_a_removed_grid_is_written_back_as_an_empty_container(fake_krita):
    """A document that stored grids keeps its own state: removing the last one
    writes the empty list (the file now says "no grids"), and a later Add is a
    NEW grid from the seed, not the deleted one coming back."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("kept_away")
    document.annotations[_GRID_ANNOTATION] = _annotation_payload()
    ext = _extension(mod, service=service, settings=RecordingSettings())
    ext.createActions(FakeWindow(document, FakeCanvas()))
    model = service.selected("kept_away")

    service.remove_grid(model.grid_id)
    assert service.grids("kept_away") == ()
    payload = json.loads(document.set_annotation_calls[-1][2].decode("utf-8"))
    assert payload["grids"] == [] and payload["selected"] is None, "removal is a delete"

    document.set_annotation_calls.clear()
    grid_id = service.add_grid()
    fresh = service.get(grid_id, "kept_away")
    assert fresh is not model, "a removed grid is gone for good"
    assert fresh.label == "1"
    assert fresh.grid_type_id != model.grid_type_id or fresh.grid_id != model.grid_id
    assert len(document.set_annotation_calls) == 1, "the re-add is a mutation, so it is saved"


def test_a_broken_annotation_opens_the_document_without_a_grid(fake_krita, caplog):
    """Corrupt bytes, an unknown version and a foreign annotation type all leave
    the container empty (with a log and no exception out of activation): a file
    we cannot read is a file whose grids the artist adds again."""
    mod, _fake, _instance = fake_krita
    settings = RecordingSettings(
        {
            "grid_type_id": "uniform_grid",
            "params": {"columns": 4},
            "style": {"width_px": 2.0},
        }
    )
    for index, raw in enumerate(
        (b"{ not json", _v1_payload(version=99), None)
    ):
        service = GridStateService()
        document = FakeDocument(f"bad{index}")
        if raw is None:
            document.annotations[_OTHER_ANNOTATION] = b"{}"
        else:
            document.annotations[_GRID_ANNOTATION] = raw
        with caplog.at_level(logging.WARNING, logger="composition_grid"):
            _extension(mod, service=service, settings=settings).createActions(
                FakeWindow(document, FakeCanvas())
            )
        assert service.grids(f"bad{index}") == (), "an unreadable file opens without grids"
        assert document.set_annotation_calls == []
        # the seed is what the artist's next Add reads, defaults and all
        loads = settings.loads
        grid_id = service.add_grid()
        model = service.get(grid_id, f"bad{index}")
        assert settings.loads == loads + 1, "the add, not the activation, spends the seed"
        assert model.grid_type_id == "uniform_grid"
        assert model.params["columns"] == 4
        assert model.transform == def_element(None, DOC_W, DOC_H)
        assert model.visible is True
    assert any("annotation" in record.message.lower() for record in caplog.records)


def test_annotation_reader_never_raises_on_a_hostile_document(fake_krita, caplog):
    """A document whose annotation getter explodes is a logged, empty answer —
    document activation must not break."""
    mod, _fake, _instance = fake_krita

    class Hostile(FakeDocument):
        def annotation(self, _annotation_type):
            raise RuntimeError("wrapper is gone")

    service = GridStateService()
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(Hostile("hostile"), FakeCanvas())
    )
    assert service.is_routed("hostile"), "the document itself is still routed"
    assert service.grids("hostile") == (), "and unreadable state is simply no state"


def test_user_mutation_writes_the_annotation_of_the_active_document_only(fake_krita):
    """One write per change, into THAT document; the other document's
    annotation and modified flag stay untouched (per-document, not global)."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    first = FakeDocument("first")
    second = FakeDocument("second")
    ext = _extension(mod, service=service, settings=RecordingSettings())
    ext.createActions(FakeWindow(first, FakeCanvas()))
    ext.createActions(FakeWindow(second, FakeCanvas()))
    assert service.active_doc_id == "second", "the fresher window routed its own document"
    assert first.set_annotation_calls == [] and second.set_annotation_calls == []
    service.add_grid()
    # a mutation now belongs to the ACTIVE document only
    service.set_style(width_px=2.0)
    assert second.set_annotation_calls, "the routed document received its state"
    assert first.set_annotation_calls == [], "an unchanged document is never written"
    payload = json.loads(second.set_annotation_calls[-1][2].decode("utf-8"))
    assert payload["version"] == 2
    assert payload["selected"] == service.selected_grid_id("second")
    assert [entry["grid_id"] for entry in payload["grids"]] == [service.selected_grid_id("second")]
    assert payload["grids"][0]["style"]["width_px"] == 2.0
    assert second.modified is True
    assert first.modified is False


def test_annotation_write_describes_itself_with_the_pinned_type(fake_krita):
    from composition_grid import plugin_document_state

    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("labelled")
    ext = _extension(mod, service=service, settings=RecordingSettings())
    ext.createActions(FakeWindow(document, FakeCanvas()))
    service.add_grid()
    service.toggle_visible()
    annotation_type, description, _data = document.set_annotation_calls[-1]
    assert annotation_type == plugin_document_state.GRID_ANNOTATION_TYPE
    assert annotation_type == _GRID_ANNOTATION
    assert description == plugin_document_state.GRID_ANNOTATION_DESCRIPTION


def test_every_mutation_kind_writes_the_annotation_exactly_once(fake_krita):
    """type, param, style, transform, rotation, ratio lock, visibility, remove
    and re-add: each user mutation lands the document's whole list in the
    annotation once."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("every_mutation")
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(document, FakeCanvas())
    )
    service.add_grid()
    cases = (
        ("type", lambda: service.set_type("uniform_grid")),
        ("param", lambda: service.set_param("columns", 5)),
        ("style", lambda: service.set_style(width_px=4.0)),
        ("rotation", lambda: service.set_rotation(15.0)),
        ("ratio", lambda: service.set_ratio_lock(
            not service.selected().transform.ratio_locked)),
        ("reset", lambda: service.reset_transform(DOC_W, DOC_H)),
        ("visible", lambda: service.toggle_visible()),
        ("remove", lambda: service.remove_grid()),
        ("add", lambda: service.add_grid()),
    )
    for name, mutate in cases:
        document.set_annotation_calls.clear()
        mutate()
        assert len(document.set_annotation_calls) == 1, f"{name}: one annotation write"
        assert [call[0] for call in document.set_annotation_calls] == [_GRID_ANNOTATION]


def test_a_selection_change_is_a_mutation_too(fake_krita):
    """Selection is stored in the container, so switching grids is written like
    any other change (a second grid is added, selected and the write names it)."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("selection")
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(document, FakeCanvas())
    )
    first = service.add_grid()
    second = service.add_grid()
    service.select_grid(first)
    document.set_annotation_calls.clear()
    service.select_grid(second)
    assert len(document.set_annotation_calls) == 1
    payload = json.loads(document.set_annotation_calls[-1][2].decode("utf-8"))
    assert payload["selected"] == second
    assert len(payload["grids"]) == 2


def test_drag_step_writes_the_annotation_like_any_other_mutation(fake_krita):
    """The canvas drag path (``model.emit``) is a mutation too: one drag step
    is one notification and therefore one annotation write."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    document = FakeDocument("dragged")
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(document, FakeCanvas())
    )
    service.add_grid()
    model = service.selected("dragged")
    model.transform = TransformState(cx=10.0, cy=10.0, width=100.0, height=100.0)
    document.set_annotation_calls.clear()
    model.emit()
    assert len(document.set_annotation_calls) == 1
    payload = json.loads(document.set_annotation_calls[-1][2].decode("utf-8"))
    assert payload["grids"][0]["transform"]["cx"] == 10.0


def test_annotation_write_survives_a_hostile_document(fake_krita, caplog):
    """``setAnnotation`` raising is logged, never delivered back into the
    notification chain (a docker control click must not break)."""
    mod, _fake, _instance = fake_krita

    class Refusing(FakeDocument):
        def setAnnotation(self, *_a):  # noqa: N802
            raise RuntimeError("document is read-only")

    service = GridStateService()
    document = Refusing("refusing")
    _extension(mod, service=service, settings=RecordingSettings()).createActions(
        FakeWindow(document, FakeCanvas())
    )
    service.add_grid()
    with caplog.at_level(logging.WARNING, logger="composition_grid"):
        assert service.set_type("uniform_grid") is True
    assert any("annotation" in record.message.lower() for record in caplog.records)


# ------------------------------------------------------------- registration


def test_register_plugin_adds_extension_and_docker_and_holds_them(fake_krita):
    """The pykrita entry ritual: ``addExtension`` plus the
    docker factory, with the extension kept alive module-locally (Krita's own
    reference may be too weak to keep signal callbacks alive)."""
    mod, _fake, instance = fake_krita
    before = len(mod.HELD_PLUGINS)
    ext = mod.register_plugin(instance)
    assert instance.extensions == [ext]
    assert len(instance.factories) == 1
    assert mod.HELD_PLUGINS[before] is ext
    assert ext.service is docker_registration.shared_state_service()


def test_register_plugin_reaches_host_single_argument_api(fake_krita, caplog):
    """Host-shaped one-arg addExtension: registration must not land in the except."""
    mod, _fake, instance = fake_krita
    with caplog.at_level(logging.ERROR, logger="composition_grid"):
        ext = mod.register_plugin(instance)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert instance.extensions == [ext]


def test_register_plugin_keeps_docker_and_hold_when_add_extension_refuses(fake_krita):
    """A refused extension registration is logged, not fatal: docker and hold still land."""
    mod, _fake, instance = fake_krita

    def refuse(extension):  # noqa: N802
        raise RuntimeError("host refused")

    instance.addExtension = refuse
    before = len(mod.HELD_PLUGINS)
    ext = mod.register_plugin(instance)
    assert instance.extensions == []
    assert [f.id for f in instance.factories] == [mod.DOCKER_ID]
    assert mod.HELD_PLUGINS[before] is ext


def test_krita_access_is_a_lazy_module_seam(fake_krita):
    """``plugin_support.krita_module()`` is the single lazy seam (mirrors
    ``KritaTransport``) and answers whatever libkis-shaped module is installed —
    the fake here, the real one inside Krita. The entry itself imports no Qt
    binding and not even ``qt_compat``: it is pure wiring."""
    mod, fake, _instance = fake_krita
    assert plugin_support.krita_module() is fake
    source = inspect.getsource(mod)
    assert not re.search(r"^\s*(?:from|import)\s+PyQt", source, re.MULTILINE)
    assert not re.search(r"^\s*(?:from|import)\s+composition_grid\.qt_compat", source, re.MULTILINE)


def test_icon_krita_answers_none_when_the_host_offers_no_icons(fake_krita):
    """The docker's eye needs a host icon lookup, and the ui layer may not import
    libkis: ``plugin_support.icon_krita`` is that seam. ``FakeKritaInstance`` has
    NO ``icon()`` — exactly the shape of an older/sparser host — and the adapter
    must answer ``None`` (logged) rather than raise, so the panel paints its own
    glyph. The same guard holds when the host method itself explodes."""
    assert plugin_support.icon_krita("visible") is None, "the fake offers no icons"

    class _Exploding:
        @staticmethod
        def instance():
            raise RuntimeError("wrapper is gone")

    saved = sys.modules["krita"].Krita
    sys.modules["krita"].Krita = _Exploding
    try:
        assert plugin_support.icon_krita("novisible") is None
    finally:
        sys.modules["krita"].Krita = saved


def test_the_docker_registration_passes_the_host_icon_provider(fake_krita):
    """Production wiring (stage C): ``create_docker`` hands the entry's icon seam
    to ``make_panel_docker_class``, so the panel in a real Krita shows Krita's own
    eyes instead of painting its fallback. The docker CLASS is only inspected (the
    fake base is a bare object: libkis' ``DockWidget`` API is not faked here, and
    instantiating it is the docker-side tests' job)."""
    mod, _fake, instance = fake_krita
    registration = importlib.import_module("composition_grid.ui.docker_registration")
    seen = {}
    original = registration.make_panel_docker_class

    def spy(base, **kwargs):
        seen.update(kwargs)
        return original(base, **kwargs)

    saved = mod.make_panel_docker_class
    mod.make_panel_docker_class = spy
    try:
        ext = _extension(mod)
        factory = ext.create_docker()
    finally:
        mod.make_panel_docker_class = saved

    assert factory is not None
    provider = seen.get("icon_provider")
    assert callable(provider), "the icon seam is passed to the docker class factory"
    assert provider("visible") is None, "the fake host offers no icons -> honest None"
    assert provider("novisible") is None


def test_desktop_entry_and_manual_ship_with_the_package():
    """The install surface: ``composition_grid.desktop``
    (repo root, next to the package folder — the pykrita layout) with
    the pinned fields, and ``Manual.html`` inside the package describing only
    what v1 really does."""
    root = None
    for ancestor in Path(plugin.__file__).resolve().parents:
        if (ancestor / "composition_grid.desktop").is_file():
            root = ancestor
            break
    assert root is not None, "composition_grid.desktop is missing from the repo root"
    desktop = (root / "composition_grid.desktop").read_text(encoding="utf-8")
    for line in (
        "[Desktop Entry]",
        "Type=Service",
        "ServiceTypes=Krita/PythonPlugin",
        "X-KDE-Library=composition_grid",
        "X-Python-2-Compatible=false",
        "X-Krita-Manual=composition_grid/Manual.html",
        "Name=Composition Grid",
        "Comment=Non-destructive transformable composition grid overlay (12 grid types)",
    ):
        assert line in desktop, f"desktop entry line missing: {line}"
    manual = Path(plugin.__file__).resolve().parent / "Manual.html"
    assert manual.is_file()
    text = manual.read_text(encoding="utf-8")
    assert "Edit Grid on Canvas" in text
    for action_id, _text, _name in plugin.ACTIONS:
        label = dict((a, t) for a, t, _h in plugin.ACTIONS)[action_id]
        assert label in text, f"manual must document the {label!r} action"
    # honesty: the limitations section names the deferred features instead of
    # advertising them
    for deferred in ("No reordering of the grid list", "no user presets", "no snapping"):
        assert deferred in text, f"manual must state the deferred limit: {deferred!r}"
    assert "composition_grid" in text, "the kritarc group is documented"


# ---------------- startup order: route the document on the first interaction


def test_create_actions_before_a_view_exists_still_works_afterwards(fake_krita):
    """The first live Krita run: ``createActions(window)`` fires while Krita is
    still starting up, when ``Window.activeView()`` is ``None``. Nothing was
    routable then, and a document that already exists does NOT emit
    ``activeViewChanged`` afterwards — so without on-demand routing the docker
    stayed display-only and every action was a no-op for the whole session.

    The assertion is the user-visible one: the menu item flips visibility once
    a view exists, even though the window was wired while it did not."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service, context_factory=_context_factory())

    window = FakeWindow()  # NO view yet: the Krita startup state
    ext.createActions(window)
    assert service.active_doc_id is None, "nothing is routable without a view"
    assert tuple(ext.contexts), "the window itself is still wired"

    window.set_view(FakeView(FakeDocument("late.png"), FakeCanvas()))
    triggers = _emitters(window)

    triggers["composition_grid_add"](checked=False)

    assert service.is_routed("late.png"), "the interaction routed the document startup missed"
    grids = service.grids("late.png")
    assert len(grids) == 1, "and the action did its job on it, not a silent no-op"
    assert grids[0].visible is True


def test_routing_failure_is_logged_not_silent(fake_krita, caplog):
    """A no-op must be explainable: with no window at all the plugin says so at
    INFO instead of doing nothing invisibly."""
    mod, _fake, instance = fake_krita
    ext = _extension(mod, service=GridStateService())
    instance.active_window = None

    with caplog.at_level(logging.INFO, logger="composition_grid"):
        assert ext.ensure_routed(None) is False
    assert "no window to route" in caplog.text


def test_an_unnamed_document_is_routed_by_its_file_name(fake_krita):
    """Live Krita answered ``Document.name() == ''`` for a document opened from
    a PNG, while ``fileName()`` held the path. ``name()`` alone was the routing
    key, so the document was never activated: no model, display-only docker,
    every action a logged no-op — the symptom the user reported.

    The fake has NO ``uniqueId``/``title`` because the host surface dump showed
    the host has none either: the fallback chain is exactly what this Krita
    offers, not what a newer libkis might."""
    mod, _fake, _instance = fake_krita
    service = GridStateService()
    ext = _extension(mod, service=service, context_factory=_context_factory())
    document = FakeDocument("", DOC_W, DOC_H, file_name="C:/x/ava.png")

    window = FakeWindow(document, FakeCanvas())
    ext.createActions(window)

    assert service.active_doc_id == "C:/x/ava.png", (
        "the document is keyed by its file name when name() is empty"
    )
    assert service.is_routed("C:/x/ava.png"), (
        "and its container exists, so the docker is interactive"
    )
    assert service.selected_grid_id("C:/x/ava.png") is None, "routing invents no grid"


# ---------------- startup recovery: a failed initial bind must be retryable


def _ensure_app():
    """The offscreen QApplication (and QtCore) for the real-widget tests."""
    global _KEEP_APP_REF, QtWidgets, QtCore
    if QtWidgets is None:
        for major in (6, 5):
            try:
                QtWidgets = importlib.import_module(f"PyQt{major}.QtWidgets")
                QtCore = importlib.import_module(f"PyQt{major}.QtCore")
                break
            except ImportError:
                continue
    app = QtWidgets.QApplication.instance()
    if app is None:
        _KEEP_APP_REF = QtWidgets.QApplication([])
        app = QtWidgets.QApplication.instance()
    return app


@pytest.fixture
def qt_window():
    """A shown real QMainWindow, torn down via the extension's own prune."""
    _ensure_app()
    holder = {}
    destroyed = []

    def on_destroyed(*_args):
        destroyed.append(True)

    holder["keep_slot"] = on_destroyed

    def make():
        win = QtWidgets.QMainWindow()
        win.resize(800, 600)
        holder["win"] = win
        win.destroyed.connect(on_destroyed)
        win.show()
        return win

    yield make
    win = holder.get("win")
    if win is not None:
        win.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(
            None, getattr(QtCore.QEvent, "Type", QtCore.QEvent).DeferredDelete
        )
        QtCore.QCoreApplication.processEvents()
        assert destroyed, "the real window survived the fixture teardown"


class _QtWindowWrapper:
    """libkis ``Window`` look-alike around a real ``QMainWindow``, registered as an
    OPEN window so ``windows()`` answers fresh twins of the same host."""

    def __init__(self, qwindow, state=None, _register=True):
        self._qwindow = qwindow
        self._state = state if state is not None else {}
        self.closed = False
        if _register:
            _OPEN_WINDOWS.append(self)
            qwindow.destroyed.connect(self._mark_closed)

    def _mark_closed(self, *_):
        """A destroyed real QMainWindow leaves the host's ``windows()`` registry."""
        self.closed = True

    def twin(self):
        return _QtWindowWrapper(self._qwindow, self._state, _register=False)

    @property
    def _view(self):
        return self._state.get("view")

    @_view.setter
    def _view(self, value):
        self._state["view"] = value

    def qwindow(self):
        return self._qwindow

    def activeView(self):
        return self._view

    def createAction(self, action_id, text="", menu_location=""):  # noqa: N802
        action = FakeAction(action_id, text, menu_location)
        return action


def _mdi_viewport(win):
    """Show the Krita-shaped MDI area (subwindow + scroll area) later in life."""
    mdi = QtWidgets.QMdiArea()
    win.setCentralWidget(mdi)
    sub = QtWidgets.QMdiSubWindow()
    scroll = QtWidgets.QTextEdit()
    sub.setWidget(scroll)
    mdi.addSubWindow(sub)
    mdi.setActiveSubWindow(sub)
    return scroll.viewport()


def _real_qt_extension(mod, service, created, providers):
    """Real CanvasContext+GridOverlay extension counting overlays and providers."""

    def factory(parent, model_provider, view_state_provider, end_edit_session=None,
                grids_provider=None):
        seen = {}

        def model():
            seen["model"] = model_provider()
            return seen["model"]

        def view_state():
            seen["view_state"] = view_state_provider()
            return seen["view_state"]

        def grids():
            seen["grids"] = grids_provider() if grids_provider is not None else ()
            return seen["grids"]

        overlay = GridOverlay(
            parent,
            model,
            view_state,
            end_edit_session=end_edit_session,
            grids_provider=grids,
        )
        created.append(overlay)
        providers.append(seen)
        return overlay

    return _extension(
        mod,
        service=service,
        context_factory=lambda svc: CanvasContext(svc, overlay_factory=factory),
        widget_resolver=canvas_locator.find_canvas_widget,
    )


def _prune(ext, win):
    """The real teardown hook: prune the holder, then dispose the Qt window."""
    ext._prune_window(plugin_support.context_key(win))


def test_startup_bind_without_a_view_recovers_through_ensure_routed(fake_krita, qt_window):
    """Startup leaves the context unattached; a later routed wake-up must attach it."""
    mod, _fake, instance = fake_krita
    win = qt_window()
    created = []
    providers = []
    service = GridStateService()
    ext = _real_qt_extension(mod, service, created, providers)
    try:
        window = _QtWindowWrapper(win)
        ext.createActions(window)
        context = ext.contexts[0]
        assert context.widget is None and context.overlay is None, (
            "startup cannot attach an overlay without a locatable widget"
        )

        viewport = _mdi_viewport(win)
        canvas = FakeCanvas(640.0, 480.0, "own")
        window._view = FakeView(FakeDocument("late.png"), canvas)
        instance.active_window = window
        assert ext.ensure_routed() is True

        assert context.widget is viewport, "recovery attached to the located widget"
        assert len(created) == 1 and context.overlay is created[0]
        assert isinstance(created[0], GridOverlay)
        assert created[0].parentWidget() is viewport
        assert context.doc_size == (DOC_W, DOC_H)
        assert context.view_state.document_rect_widget == Bounds(0.0, 0.0, 640.0, 480.0)
        assert context.view_state.doc_width == DOC_W
        assert context.view_state.doc_height == DOC_H
    finally:
        _prune(ext, win)


def test_canvas_changed_wake_up_recovers_an_unattached_context(fake_krita, qt_window):
    """The payload-free canvasChanged wake-up must bind and expose live state."""
    mod, _fake, instance = fake_krita
    win = qt_window()
    created = []
    providers = []
    service = GridStateService()
    ext = _real_qt_extension(mod, service, created, providers)
    try:
        window = _QtWindowWrapper(win)
        ext.createActions(window)
        context = ext.contexts[0]
        assert context.overlay is None

        viewport = _mdi_viewport(win)
        canvas = FakeCanvas(640.0, 480.0, "own")
        window._view = FakeView(FakeDocument("wake.png"), canvas)
        instance.active_window = window
        ext.handle_canvas_changed(None)

        assert context.widget is viewport, "bound to THIS window's located widget"
        assert len(created) == 1 and context.overlay is created[0]
        assert created[0].parentWidget() is viewport
        assert context.canvas is not None, "the context holds this window's canvas"
        assert context.view_state is not None, "a usable view state exists"
        assert context.view_state.doc_width == DOC_W
        assert context.view_state.doc_height == DOC_H
        assert context.view_state.document_rect_widget == Bounds(0.0, 0.0, 640.0, 480.0)
        assert providers[-1].get("model") is service.selected("wake.png"), (
            "the routed document has no grid, so no selected model reaches the overlay"
        )
        assert created[0]._grids_provider() == (), "and no grids to paint"
        grid_id = service.add_grid()
        assert created[0]._model_provider() is service.get(grid_id, "wake.png")
        assert created[0]._grids_provider() == service.grids("wake.png"), (
            "the providers follow the service, not the bind-time snapshot"
        )
        assert created[0]._view_state_provider() == context.view_state
        assert context.doc_size == (DOC_W, DOC_H)
    finally:
        _prune(ext, win)


@pytest.mark.parametrize("case", ["canvas-absent", "widget-unlocatable"])
@pytest.mark.parametrize("wake", ["ensure_routed", "canvasChanged"])
def test_half_valid_startup_never_creates_an_overlay(fake_krita, qt_window, case, wake):
    """One valid half is not a proven attach: no overlay, then real recovery."""
    mod, _fake, instance = fake_krita
    win = qt_window()
    created = []
    providers = []
    service = GridStateService()
    ext = _real_qt_extension(mod, service, created, providers)
    try:
        window = _QtWindowWrapper(win)
        ext.createActions(window)
        context = ext.contexts[0]
        assert context.widget is None and created == []

        def fire():
            if wake == "ensure_routed":
                ext.ensure_routed()
            else:
                ext.handle_canvas_changed(None)

        viewport = None
        if case == "canvas-absent":
            viewport = _mdi_viewport(win)
            assert canvas_locator.find_canvas_widget(win) is viewport
            assert window.activeView() is None
            instance.active_window = window
            fire()
            assert created == [] and context.overlay is None, (
                "a locatable widget without a canvas must not pre-create an overlay"
            )
        else:
            win.setCentralWidget(QtWidgets.QWidget())
            window._view = FakeView(FakeDocument("nowhere.png"), FakeCanvas())
            instance.active_window = window
            fire()
            assert created == [] and context.overlay is None, (
                "a canvas without a locatable widget must not attach anywhere"
            )
            assert service.is_routed("nowhere.png") and service.grids("nowhere.png") == (), (
                "routing still works while the overlay degrades (and invents no grid)"
            )

        if viewport is None:
            viewport = _mdi_viewport(win)
        canvas = FakeCanvas(640.0, 480.0, "own")
        window._view = FakeView(FakeDocument("late.png"), canvas)
        instance.active_window = window
        fire()
        assert len(created) == 1 and context.overlay is created[0], (
            "the SAME context recovers once both halves are valid"
        )
        assert context.widget is viewport and context.canvas is not None
        assert context.view_state is not None
    finally:
        _prune(ext, win)


def test_recovery_never_rebuilds_a_live_overlay_on_later_wake_ups(fake_krita, qt_window):
    """Recovered overlays survive repeated wake-ups and transient view loss."""
    mod, _fake, instance = fake_krita
    win = qt_window()
    created = []
    providers = []
    service = GridStateService()
    ext = _real_qt_extension(mod, service, created, providers)
    try:
        window = _QtWindowWrapper(win)
        ext.createActions(window)
        viewport = _mdi_viewport(win)
        canvas = FakeCanvas(640.0, 480.0, "own")
        window._view = FakeView(FakeDocument("doc-a"), canvas)
        instance.active_window = window
        ext.handle_canvas_changed(None)
        context = ext.contexts[0]
        assert len(created) == 1

        ext.handle_canvas_changed(None)
        ext.createActions(window)
        ext.ensure_routed()
        assert len(created) == 1, "no overlay churn on unchanged wake-ups"

        window._view = None
        ext.handle_canvas_changed(None)
        ext.ensure_routed()
        assert context.widget is viewport and context.overlay is created[0], (
            "a transient unreadable view never tears the recovered overlay down"
        )
    finally:
        _prune(ext, win)
