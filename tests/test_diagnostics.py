"""Tests for the plugin's self-diagnostics (``composition_grid/diagnostics.py``).

The report is the answer to "nothing happens on the canvas", which from the
outside has at least six different causes — so the tests pin that the report
NAMES THE FIRST BROKEN LINK, not that it looks nice. Each test stops the chain
at one object (no extension, no view, no canvas widget, vetoed widget, no
document size) and asserts the report says that thing failed, in order.

No Qt and no real Krita: the host objects are duck-typed fakes shaped like
libkis (getters return fresh wrappers, getters can raise during teardown), and
the report must survive both.
"""

from __future__ import annotations

import logging
import re

import pytest

from composition_grid import diagnostics


class _Raising:
    """A host object whose getters raise — a half-torn-down libkis wrapper."""

    def activeDocument(self):
        raise RuntimeError("wrapper of a deleted document")

    def activeWindow(self):
        raise RuntimeError("wrapper of a deleted window")

    def version(self):
        raise RuntimeError("version() of a dying app")


class _FakeApp:
    """The parts of ``krita.Krita.instance()`` the report reads."""

    def __init__(self, document=None, window=None):
        self._document = document
        self._window = window

    def version(self):
        return "5.3.1 (fake)"

    def activeDocument(self):
        return self._document

    def activeWindow(self):
        return self._window


class _StrictVersionApp:
    """The host's exact surface for this probe: an instance ``version()``."""

    def version(self):
        return "5.3.1 (git 9069dbc)"


class _EmptyVersionApp:
    def version(self):
        return ""


class _BlankVersionApp:
    def version(self):
        return "   "


class _NonStringVersionApp:
    def version(self):
        return None


def test_probe_reads_the_documented_instance_version():
    version, routes = diagnostics._krita_version_probe(_StrictVersionApp())

    assert version == "5.3.1 (git 9069dbc)"
    assert routes == [("app.version()", "5.3.1 (git 9069dbc)")]


def test_report_prints_the_version_from_app_version():
    report = diagnostics.collect_report(_StrictVersionApp())

    assert "OK   Krita: 5.3.1 (git 9069dbc)" in report
    assert "app.version() = 5.3.1 (git 9069dbc)" in report
    assert "versionString" not in report
    assert "readSetting" not in report


@pytest.mark.parametrize(
    "app",
    [object(), _Raising(), _EmptyVersionApp(), _BlankVersionApp(), _NonStringVersionApp()],
    ids=["absent", "raising", "empty", "blank", "non-string"],
)
def test_probe_answers_no_version_without_crashing(app):
    version, routes = diagnostics._krita_version_probe(app)

    assert version is None
    assert routes, "the report must say what the host answered"
    assert routes[0][0] == "app.version()"
    assert "<" in routes[0][1], routes


def test_report_degrades_to_a_fail_line_when_the_host_has_no_version():
    report = diagnostics.collect_report(_EmptyVersionApp())

    assert "FAIL Krita: версия не определилась" in report
    assert "app.version()" in report


class _FakeDocument:
    def __init__(self, name="doc.png", width=1200, height=800):
        self._name, self._width, self._height = name, width, height

    def name(self):
        return self._name

    def width(self):
        return self._width

    def height(self):
        return self._height


class _FakeCanvas:
    """Shaped like the REAL libkis Canvas: view-state getters, and NO
    width()/height() — the fact the report explicitly documents."""

    def __init__(self, rect=None):
        self._rect = rect if rect is not None else _Rect()

    def documentRectInWidgetPixels(self):
        return self._rect

    def documentRectInWidgetPixelsChanged(self):  # present as a signal stand-in
        return None

    def rotationChanged(self):
        return None

    def mirrorChanged(self):
        return None

    def sizeChanged(self):
        return None

    def removed(self):
        return None


class _Rect:
    def x(self):
        return 0.0

    def y(self):
        return 0.0

    def width(self):
        return 600.0

    def height(self):
        return 400.0


class _Point:
    """QPointF duck — the shape of ``Canvas.preferredCenter()`` (x()/y())."""

    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _SignallessCanvas:
    """Canvas как на живом хосте пользователя: никаких сигналов 5.3+ и нет
    ``documentRectInWidgetPixels()`` — источник синхронизации с видом отсутствует."""

    def zoomLevel(self):
        return 1.0

    def rotation(self):
        return 0.0


class _FakeView:
    """Hands back a FRESH wrapper per call, like pybind11 does."""

    def __init__(self, document=None, canvas=None):
        self._document = document
        self._canvas = canvas

    def canvas(self):
        return _FakeCanvas() if self._canvas is None else self._canvas

    def document(self):
        return self._document


class _FakeWindow:
    def __init__(self, view=None, qwindow=None):
        self._view = view
        self._qwindow = qwindow

    def activeView(self):
        return self._view

    def qwindow(self):
        return self._qwindow


class _FakeService:
    """The read-only slice of the multi-grid service the report asks about."""

    def __init__(self, active_doc_id=None, grids=(), selected=None):
        self.active_doc_id = active_doc_id
        self._grids = tuple(grids)
        self._selected = selected

    def grids(self, doc_id=None):
        return self._grids

    def selected(self, doc_id=None):
        return self._selected


class _FakeExtension:
    def __init__(self, service=None, contexts=()):
        self.service = service if service is not None else _FakeService()
        self._contexts = tuple(contexts)

    @property
    def contexts(self):
        return self._contexts

    def _context_doc_size(self, context):
        return None


@pytest.fixture()
def no_registered_plugin(monkeypatch):
    """HELD_PLUGINS emptied: the "plugin never created" branch."""
    import composition_grid.plugin_composition_grid as entry

    monkeypatch.setattr(entry, "HELD_PLUGINS", [], raising=False)
    return entry


def test_report_names_the_first_broken_link_when_nothing_is_registered(no_registered_plugin):
    report = diagnostics.collect_report(_FakeApp())

    assert "зарегистрированных расширений (HELD_PLUGINS): 0" in report
    assert "плагин не создан" in report
    # A truncated report is still a report: the log section is always present.
    assert "лог composition_grid" in report


def test_report_survives_a_host_object_that_raises_from_every_getter():
    """A diagnostic that dies on a teardown race hides the answer; every probe
    must degrade into a line instead.

    The extension is passed explicitly: with nothing registered the report
    correctly STOPS at that first gate and would never reach the hostile host
    getters this test is about."""
    report = diagnostics.collect_report(_Raising(), extension=_FakeExtension())

    assert "FAIL" in report
    assert "диагностика оборвалась" not in report
    assert "activeDocument" in report and "deleted document" in report
    assert "activeWindow" in report and "deleted window" in report
    # The chain kept going past the hostile getters, down to the plugin state.
    assert "контекстов окна" in report


def test_report_walks_a_healthy_chain_and_prints_the_state():
    document = _FakeDocument()
    canvas = _FakeCanvas()
    view = _FakeView(document=document, canvas=canvas)
    window = _FakeWindow(view=view)
    app = _FakeApp(document=document, window=window)

    report = diagnostics.collect_report(app, extension=_FakeExtension())

    assert "OK   Krita: 5.3.1 (fake)" in report
    assert "документа: «doc.png» 1200x800" in report
    assert "View.document() — источник размера документа: _FakeDocument" in report
    assert "Canvas.documentRectInWidgetPixelsChanged присутствует: True" in report
    # The pinned libkis fact: Canvas has no document size of its own.
    assert "(справка) getattr(Canvas, 'width') = None" in report
    assert "сеток в активном документе: 0" in report
    assert "Add Composition Grid" in report, "no grids: the report names the command"
    assert "контекстов окна: 0" in report


def test_report_counts_the_grids_and_names_the_selected_one():
    """"My grid disappeared" is usually "two grids, this one is the unselected
    one": the report answers it without a debugger — the count, every
    ``id(label)`` of the document and which grid the canvas edits."""
    from composition_grid.model.grid_model import GridStateService

    service = GridStateService()
    service.ensure("doc.png", 800.0, 600.0)
    service.set_active("doc.png")
    service.add_grid()
    first = service.selected_grid_id()
    service.add_grid()
    second = service.selected_grid_id()
    service.set_label(second, "sky")

    view = _FakeView(document=_FakeDocument(), canvas=_FakeCanvas())
    report = diagnostics.collect_report(
        _FakeApp(document=_FakeDocument(), window=_FakeWindow(view=view)),
        extension=_FakeExtension(service=service),
    )

    assert "сеток в активном документе: 2" in report
    assert f"{first}(" in report and f"{second}(sky)" in report
    assert f"выбранная сетка: {second} label='sky'" in report


def test_report_flags_a_context_without_an_overlay(monkeypatch):
    """The report must not claim a grid can be painted when there is nothing to
    paint it on."""
    calls = []

    class _WidgetlessContext:
        widget = None
        overlay = None
        view_state = None

    def _fake_find_canvas_widget(_qwindow):
        calls.append(1)
        return None

    import composition_grid.canvas.canvas_locator as locator

    monkeypatch.setattr(locator, "find_canvas_widget", _fake_find_canvas_widget, raising=True)
    extension = _FakeExtension(contexts=[_WidgetlessContext()])

    report = diagnostics.collect_report(
        _FakeApp(window=_FakeWindow(qwindow=object())), extension=extension
    )

    assert calls, "the report re-asks the locator, it does not trust the context"
    assert "context.widget (привязанный виджет): None" in report
    assert "FAIL find_canvas_widget(qwindow): None" in report


def test_report_dumps_the_host_surface_it_has_to_reason_about():
    """The live Krita answered NO Canvas signals and an EMPTY Document.name().
    A yes/no line cannot be acted on, so the report must show the API the host
    actually has — the method lists and every identity candidate — otherwise
    the next fix is a guess about a machine nobody is testing on."""

    class _EmptyNamedDocument(_FakeDocument):
        def name(self):
            return ""

        def fileName(self):
            return "/tmp/untitled.kra"

    app = _FakeApp(
        document=_EmptyNamedDocument(),
        # The user's host shape: a Canvas with NO 5.3+ signals and no rect getter.
        window=_FakeWindow(view=_FakeView(canvas=_SignallessCanvas())),
    )
    report = diagnostics.collect_report(app, extension=_FakeExtension())

    assert "Document.name() = ''" in report
    assert "Document.fileName() = '/tmp/untitled.kra'" in report
    assert "поверхность Document:" in report
    assert "поверхность Canvas:" in report
    assert "поверхность View:" in report
    assert "Сигналов Canvas 5.3+ на этом хосте НЕТ" in report
    assert "первое FAIL" in report
    # ...and the surface dump shows the methods the host DOES have, which is
    # what the next fix has to be chosen against.
    assert "zoomLevel" in report.split("поверхность Canvas:")[1].splitlines()[0]


# --- the live run must CONFIRM the computed path in one
#     shot
#
# The scale rule (``scale = Canvas.zoomLevel()``, ``preferredCenter`` placed at
# the viewport centre) is the one thing no headless test can validate — the
# report therefore prints every input the computed ViewState uses, the resulting
# rect and scale, AND the host scrollbar values with an explicit cross-check
# computed inside the report, so a single live run either confirms or refutes
# it. These lines are pure data lines in the existing OK/FAIL style; the order
# of the report and its conclusion block are unchanged.


class _ViewportWidget:
    """The attached canvas widget: the viewport size of the user's own live
    diagnostic run (1234x877), read as ``width()``/``height()``."""

    def __init__(self, w=1234, h=877):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class _ContextWithWidget:
    """A bound context: it carries the widget the viewport size comes from."""

    def __init__(self, widget):
        self.widget = widget
        self.overlay = None
        self.view_state = None


class _ScrollBar:
    """QScrollBar duck with the method names a REAL binding exposes.

    FINDING 3 (live report: ``min '<у QScrollBar нет метода minimumValue>'``):
    ``minimumValue``/``maximumValue`` are the Qt *properties*; the methods on
    ``QAbstractSlider`` are ``minimum()``/``maximum()``/``value()``/``pageStep()``,
    and PyQt5 exposes only the methods. A fake that carried the property names
    as methods was more permissive than the host and hid the bug — this one is
    shaped like the binding the user runs."""

    def __init__(self, mn=0, mx=2900, val=120, page=100):
        self._values = {
            "minimum": mn,
            "maximum": mx,
            "value": val,
            "pageStep": page,
        }

    def minimum(self):
        return self._values["minimum"]

    def maximum(self):
        return self._values["maximum"]

    def value(self):
        return self._values["value"]

    def pageStep(self):
        return self._values["pageStep"]


class _PropertyOnlyScrollBar:
    """A binding that exposes the Qt *properties* and no methods (the fallback
    path the probe must also answer, so both bindings report numbers)."""

    def __init__(self, mn=0, mx=2900, val=120, page=100):
        self.minimum = mn
        self.maximum = mx
        self.value = val
        self.pageStep = page


class _ScrollArea:
    """The located holder object — only its identity is printed."""


class _BoundContext:
    """A PRODUCTION-shaped bound context (the fake must not be weaker than the
    host): widget + attached canvas + the trigger-status
    snapshot the real CanvasContext records at bind."""

    def __init__(self, widget, canvas=None, bars=None):
        self.widget = widget
        self.canvas = canvas if canvas is not None else _SignallessCanvas()
        self.overlay = None
        self.view_state = None
        self.trigger_status = {
            "scroll_area": _ScrollArea(),
            "bars": bars if bars is not None else [_ScrollBar(), _ScrollBar(mx=1900, val=80)],
            "viewport_filter_installed": True,
            "holder_filter_installed": True,
            "viewport_event_names": ("Paint", "Resize", "Scroll", "ScrollPrepare", "Wheel"),
            "holder_event_names": ("LayoutRequest",),
        }


def _metrics_report(context=None, view_factory=_FakeView):
    """A full 5.3-shaped chain: a signalless Canvas that answers the getters, a
    document with a resolution, and a context with the located viewport.
    ``view_factory`` builds the ``View`` (the host-affine case adds the two
    ``flakeTo*Transform`` getters to it)."""

    class Canvas53Like(_SignallessCanvas):
        def preferredCenter(self):
            return _Point(3500.0, 2000.0)

        def mirror(self):
            return {"horizontal": False, "vertical": False}

        def rotation(self):
            return 0.0

    class DocumentWithResolution(_FakeDocument):
        def resolution(self):
            return 72.0

    document = DocumentWithResolution("photo.kra", 7000, 4000)
    window = _FakeWindow(
        view=view_factory(document=document, canvas=Canvas53Like()),
        qwindow=object(),
    )
    extension = _FakeExtension(
        contexts=[context if context is not None else _ContextWithWidget(_ViewportWidget())]
    )
    return diagnostics.collect_report(
        _FakeApp(document=document, window=window), extension=extension
    )


def _line(report, fragment):
    return next(line for line in report.splitlines() if fragment in line)


def test_report_prints_the_scale_formula_inputs():
    """Every getter the computed path reads is printed as a data line, so a
    wrong scale basis is visible on the live host immediately. ``resolution``
    stays in the report as INFORMATION ONLY: the live host proved the scale is
    ``zoomLevel()`` itself, so the line must say the dpi does not enter the
    computation instead of implying the refuted ``zoom*resolution/72`` rule."""
    report = _metrics_report()

    assert _line(report, "Canvas.zoomLevel() =").endswith("1.0")
    assert "Canvas.preferredCenter()" in report
    assert "x 3500.0" in report and "y 2000.0" in report
    assert "Document.resolution() = 72.0" in report
    assert "в вычислении масштаба не участвует" in _line(
        report, "Document.resolution()"
    )
    assert _line(report, "Canvas.rotation() =").endswith("0.0")
    assert "horizontal" in _line(report, "Canvas.mirror() =")
    assert _line(report, "viewport (привязанный виджет)").endswith("1234x877 px")


def test_report_prints_the_computed_rect_and_the_resulting_view_scale():
    """The confirmation itself: with the pinned inputs above the corrected
    placement (``preferredCenter`` IS the doc point at the
    viewport centre, no +doc/2) and the corrected scale
    (``s = zoomLevel()``), here zoom 1.0, so this case cannot distinguish the two
    rules; :func:`test_report_prints_the_live_host_rect_and_scrollbar_cross_check`
    does) give s = 1.0 and the rect
    ``x = 1234/2 - 3500 = -2883``, ``y = 877/2 - 2000 = -1561.5``, ``w 7000``,
    ``h 4000`` — asserted verbatim, because the whole point of the live run is
    to compare these numbers with the screen."""
    report = _metrics_report()

    scale_line = _line(report, "view_scale")
    assert scale_line.strip().endswith("= 1.0"), scale_line
    rect_line = _line(report, "вычисленный rect документа")
    assert rect_line.split("=", 1)[1].strip() == (
        "x -2883.0, y -1561.5, w 7000.0, h 4000.0"
    ), rect_line


def test_report_prints_the_host_affine_and_names_the_source_it_wins():
    """The rotation finding made the host's own matrices the primary source, so
    the report must print them (or why they are absent) NEXT TO the computed
    rect, and the effective-source line must say the affine — not the rect — is
    what feeds ``view_state``."""

    class _Matrix:
        """``QTransform`` duck: the six affine numbers as attributes."""

        def __init__(self, m11, m12, m21, m22, dx, dy):
            self.m11, self.m12, self.m21, self.m22, self.dx, self.dy = (
                m11,
                m12,
                m21,
                m22,
                dx,
                dy,
            )

    class ViewWithAffine(_FakeView):
        def flakeToImageTransform(self):
            return _Matrix(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

        def flakeToCanvasTransform(self):
            # the measured zoom .5 / rotation 30 answer of the live 5.3.1 host
            return _Matrix(0.4330, 0.25, -0.25, 0.4330, 987.0, 318.5)

    affine_report = _metrics_report(view_factory=ViewWithAffine)
    line = _line(affine_report, "аффинное преобразование хоста doc->widget =")
    assert line.startswith("OK"), line
    assert "a 0.433" in line and "b 0.25" in line and "c -0.25" in line, line
    assert "doc(0, 0) -> widget (987.0, 318.5)" in line, line
    assert "источник view_state: аффинное преобразование хоста" in affine_report
    assert "источник view_state: вычисление по геттерам" not in affine_report
    # the computed rect is still printed — the affine line sits right after it
    assert affine_report.index("вычисленный rect документа") < affine_report.index(
        "аффинное преобразование хоста doc->widget ="
    )

    without = _metrics_report()
    fail_line = _line(without, "аффинное преобразование хоста doc->widget")
    assert fail_line.startswith("FAIL"), fail_line


def test_report_marks_absent_metric_getters_as_fail_lines(monkeypatch):
    """A getter the host does not answer is a FAIL data line, not a crash and
    not a silent gap: the fake Canvas here has zoomLevel/preferredCenter/
    resolution nowhere, so every input line says FAIL and the computed rect is
    reported as not computable."""

    class _NoMetricsCanvas(_FakeCanvas):
        pass

    document = _FakeDocument("doc.png", 1200, 800)
    window = _FakeWindow(
        view=_FakeView(document=document, canvas=_NoMetricsCanvas()),
        qwindow=object(),
    )
    extension = _FakeExtension(contexts=[_ContextWithWidget(_ViewportWidget())])
    report = diagnostics.collect_report(
        _FakeApp(document=document, window=window), extension=extension
    )

    assert "FAIL Canvas.zoomLevel()" in report
    assert "FAIL Canvas.preferredCenter()" in report
    assert "FAIL Document.resolution()" in report
    assert "вычисленный rect документа" in report
    assert "не вычисляется" in report


def test_metrics_lines_do_not_reorder_the_existing_chain():
    """The report's order is its contract (the FIRST FAIL names the broken
    link): the new block sits inside the Canvas section and moves nothing —
    activeView, the signal-presence lines, the surfaces and the conclusion all
    keep their places."""
    report = _metrics_report()

    order = [
        "activeWindow:",
        "Window.activeView():",
        "View.canvas():",
        "поверхность Canvas:",
        "service.active_doc_id:",
        "контекстов окна:",
        "--- вывод по первому оборванному звену ---",
    ]
    positions = [report.index(fragment) for fragment in order]
    assert positions == sorted(positions), "the chain order changed"
    # the metrics block lives inside the Canvas section: after the surface dump
    # of Canvas and before the service lines.
    assert report.index("view_scale") > report.index("поверхность Canvas:")
    assert report.index("view_scale") < report.index("service.active_doc_id:")


# --- the report must state the REAL state and carry the
#     numbers that settle the scale rule in ONE live run ----------------------


def test_report_states_the_effective_view_state_source_computed_or_host_override():
    """The old conclusion block still claimed «источника нет,
    view_state не может собраться» — a leftover from before the computed path
    existed that contradicts the computed path two lines below and would
    mislead a live run. The report must instead NAME the effective source: the
    host rect
    override when ``documentRectInWidgetPixels()`` answers, the computed path
    when it does not, and never the stale dead-end sentence."""
    computed = _metrics_report()
    assert "не может собраться" not in computed
    assert "источник view_state: вычисление по геттерам" in computed

    document = _FakeDocument()
    canvas = _FakeCanvas()  # the 6.x shape: precise rect getter present
    window = _FakeWindow(
        view=_FakeView(document=document, canvas=canvas), qwindow=object()
    )
    host_rect = diagnostics.collect_report(
        _FakeApp(document=document, window=window),
        extension=_FakeExtension(contexts=[_ContextWithWidget(_ViewportWidget())]),
    )
    assert "источник view_state: точный прямоугольник хоста" in host_rect


def test_report_names_the_computed_inputs_the_host_does_not_answer():
    """The starvation half: with the user's host shape (``_Signalless
    Canvas`` has zoomLevel/rotation but NO preferredCenter/mirror) the
    conclusion must say WHICH computed-path getters are missing — the block
    previously declared the whole synchronisation dead instead."""
    document = _FakeDocument("photo.kra", 7000, 4000)
    window = _FakeWindow(
        view=_FakeView(document=document, canvas=_SignallessCanvas()), qwindow=object()
    )
    report = diagnostics.collect_report(
        _FakeApp(document=document, window=window),
        extension=_FakeExtension(contexts=[_ContextWithWidget(_ViewportWidget())]),
    )
    assert "нет геттеров: preferredCenter, mirror" in report
    # The pinned 5.3 fact keeps its original wording (existing test):
    assert "Сигналов Canvas 5.3+ на этом хосте НЕТ" in report


def test_report_prints_scrollbar_ranges_and_trigger_wiring():
    """The ONLY host-side numbers that independently pin the
    effective zoom and the document's widget-space left edge are the owning
    scroll area's bar values — min/max/value/pageStep — and a live run also
    has to SEE the trigger wiring: scroll area found? bars wired? which filters
    with which event types? All of it from the context's bind-time snapshot."""
    report = _metrics_report(context=_BoundContext(_ViewportWidget()))

    hbar = _line(report, "scrollbar horizontal")
    assert "min 0" in hbar and "max 2900" in hbar, hbar
    assert "value 120" in hbar and "pageStep 100" in hbar, hbar
    vbar = _line(report, "scrollbar vertical")
    assert "max 1900" in vbar and "value 80" in vbar, vbar

    wiring = _line(report, "viewport event filter")
    assert "Paint" in wiring and "Wheel" in wiring, wiring
    holder = _line(report, "holder event filter")
    assert "LayoutRequest" in holder, holder
    assert "scroll area: _ScrollArea" in report
    assert "проводка триггеров" in report
    assert "scrollbars подключено: 2" in report


# --- the report must settle the SCALE RULE from the live numbers ------------


def _live_host_report(bars=None):
    """A report for the user's EXACT 5.3.4.1 answers: ``zoomLevel()``
    0.0833333333333357, ``preferredCenter()`` (4860, 1854), a 1234x877 viewport,
    a 7000x4000 / 600 dpi document, ``mirror()`` = the plain bool False the host
    really answers, and the two host scrollbars at value -212 / -284."""

    class CanvasLive53:
        def zoomLevel(self):
            return 0.0833333333333357

        def rotation(self):
            return 0.0

        def mirror(self):
            return False  # the live answer: a bool, not a dict

        def preferredCenter(self):
            return _Point(4860.0, 1854.0)

    class Document600dpi(_FakeDocument):
        def resolution(self):
            return 600.0

        def xRes(self):
            return 599.9977611989281

        def yRes(self):
            return 599.9977611989281

    document = Document600dpi("photo.kra", 7000, 4000)
    window = _FakeWindow(
        view=_FakeView(document=document, canvas=CanvasLive53()), qwindow=object()
    )
    context = _BoundContext(
        _ViewportWidget(),
        canvas=CanvasLive53(),
        bars=bars
        if bars is not None
        else [_ScrollBar(val=-212, page=308), _ScrollBar(val=-284, page=219)],
    )
    return diagnostics.collect_report(
        _FakeApp(document=document, window=window),
        extension=_FakeExtension(contexts=[context]),
    )


def _rect_numbers(report):
    """The ``(x, y, w, h)`` of the printed computed rect, as floats — parsed
    rather than string-matched, because the live host's own values are not
    round numbers (``0.0833333333333357 * 4860`` is not exactly 405)."""
    line = _line(report, "вычисленный rect документа")
    body = line.split("=", 1)[1]
    parts = {}
    for chunk in body.split(","):
        key, _, value = chunk.strip().partition(" ")
        parts[key] = float(value)
    return (parts["x"], parts["y"], parts["w"], parts["h"])


def test_report_prints_the_live_host_rect_and_scrollbar_cross_check():
    """FINDINGS 1+2 TOGETHER, as one falsifiable report. With the user's verbatim
    inputs the corrected scale (``s = zoomLevel()``) places the document at
    ``x = 1234/2 - 4860*s = 212.0``, ``y = 877/2 - 1854*s = 284.0`` — and those
    are exactly the MAGNITUDES of the host's own scrollbar values (-212 / -284),
    on BOTH axes. The refuted ``zoom*resolution/72`` rule gives -2758/-849 and
    agrees with neither. The report therefore recomputes that agreement itself
    and prints the deltas, so a future host in physical-size mode shows up as a
    NON-ZERO delta instead of a silently wrong grid. ``mirror() = False`` must be
    read as a valid answer (no 'unusable mirror payload' in the log)."""
    report = _live_host_report()

    x, y, w, h = _rect_numbers(report)
    assert x == pytest.approx(212.0, abs=1e-6), report
    assert y == pytest.approx(284.0, abs=1e-6), report
    assert w == pytest.approx(7000.0 * 0.0833333333333357, abs=1e-6)
    assert h == pytest.approx(4000.0 * 0.0833333333333357, abs=1e-6)
    # view_scale is rect.w/doc_w, so it carries the float rounding of the
    # multiplication: compare as a number, not as a repr string.
    scale_value = float(_line(report, "view_scale").split("=", 1)[1].strip())
    assert scale_value == pytest.approx(0.0833333333333357, abs=1e-12), report

    # The bool is the host's normal answer, printed and interpreted.
    assert "Canvas.mirror() = False" in report
    assert "unusable mirror" not in report

    # The cross-check the report computes ITSELF (data line, no behaviour):
    check = _line(report, "cross-check")
    assert "Dx = 0.000" in check and "Dy = 0.000" in check, check
    assert "-212" in report and "-284" in report


def test_report_cross_check_would_expose_a_physical_size_mode_host():
    """The cross-check is only worth printing if it can actually refute a wrong
    rule. Feed it the bar values a physical-size host WOULD have (the
    ``zoom*resolution/72`` rect of -2758/-849, i.e. what the refuted formula
    produced on this very document) and both deltas must come out large, not
    zero — anything that quietly re-adjusted the numbers would hide exactly the
    failure this line exists to show."""
    report = _live_host_report(
        bars=[_ScrollBar(val=-2758, page=308), _ScrollBar(val=-849, page=219)]
    )
    check = _line(report, "cross-check")
    deltas = [float(v) for v in re.findall(r"D[xy] = (-?\d+(?:\.\d+)?)", check)]
    assert len(deltas) == 2, check
    assert all(abs(value) > 100.0 for value in deltas), (check, deltas)


def test_report_reads_scrollbar_property_only_bindings():
    """FINDING 3: the live report printed ``min '<у QScrollBar нет метода
    minimumValue>'`` — ``minimumValue``/``maximumValue`` are Qt *properties*, the
    ``QAbstractSlider`` METHODS are ``minimum()``/``maximum()``/``value()``/
    ``pageStep()``, and PyQt5 (the user's binding) exposes only the methods. The
    probe tries the method names first and keeps the property read as a fallback,
    so a binding of either shape reports real numbers and no error text."""
    report = _live_host_report(
        bars=[_PropertyOnlyScrollBar(val=-212), _PropertyOnlyScrollBar(val=-284)]
    )
    for label in ("horizontal", "vertical"):
        line = _line(report, f"scrollbar {label}")
        assert "нет метода" not in line and "<" not in line, line
    assert "value -212" in _line(report, "scrollbar horizontal")
    assert "value -284" in _line(report, "scrollbar vertical")
    check = _line(report, "cross-check")
    assert "Dx = 0.000" in check and "Dy = 0.000" in check, check


def test_report_prints_the_attached_canvas_and_located_widget_identity():
    """Rebind visibility: a tab switch that did NOT rebind is
    the freshly resolved ``View.canvas()`` and the located widget's identity.
    A context that predates the snapshot (fake/unbound) is reported honestly,
    without inventing a FAIL for a capability it never claimed."""
    report = _metrics_report(context=_BoundContext(_ViewportWidget()))
    assert "context.canvas (привязанный Canvas): _SignallessCanvas" in report

    plain = _metrics_report()  # _ContextWithWidget: no canvas attribute at all
    assert "context.canvas: контекст не предоставляет" in plain


def test_report_prints_xres_yres_to_make_the_isotropic_assumption_testable():
    """The computed scale used to
    fold a SINGLE ``Document.resolution()`` into ``zoom*resolution/72``, so a
    document with ``xRes != yRes`` would have been off vertically by
    ``yRes/xRes`` (v1 deliberately has no anisotropic zoom). The live host
    removed that dependency — the scale is ``zoomLevel()`` and the dpi does not
    enter at all — but xRes/yRes stay printed, now as pure reference data: an
    anisotropic host cannot hurt THIS scale, and the scrollbar cross-check is
    what would reveal a host whose zoom semantics differ."""

    class DocumentXYRes(_FakeDocument):
        def resolution(self):
            return 72.0

        def xRes(self):
            return 96.0

        def yRes(self):
            return 300.0

    document = DocumentXYRes("photo.kra", 7000, 4000)
    window = _FakeWindow(
        view=_FakeView(document=document, canvas=_SignallessCanvas()), qwindow=object()
    )
    report = diagnostics.collect_report(
        _FakeApp(document=document, window=window),
        extension=_FakeExtension(contexts=[_ContextWithWidget(_ViewportWidget())]),
    )
    assert "Document.xRes() = 96.0" in report
    assert "Document.yRes() = 300.0" in report
    assert "xRes != yRes" in report

    # The 72-only fake has no xRes/yRes: the line still prints, honestly
    # naming the absent getters instead of silently omitting the check.
    absent = _metrics_report()
    assert "Document.xRes()" in absent and "Document.yRes()" in absent


def test_conclusion_is_printed_at_every_exit_not_only_at_the_end():
    """The chain walk returns early on purpose, and the SHORT reports (nothing
    registered, no context) are exactly the ones a user pastes — they must
    carry the same 'first broken link' summary as a full report."""
    no_plugin = diagnostics.collect_report(_FakeApp())
    no_context = diagnostics.collect_report(
        _FakeApp(), extension=_FakeExtension(contexts=())
    )
    walked = diagnostics.collect_report(_FakeApp(window=_FakeWindow()), extension=_FakeExtension())

    for report in (no_plugin, no_context, walked):
        assert "первое FAIL" in report, report
    # The summary must name the FIRST broken link of THIS environment, whatever
    # it is: without PyQt the Qt binding genuinely is that link, with PyQt the
    # missing extension registration is. Asserting one fixed answer here would
    # only pin the machine the test was written on.
    summary = no_plugin.split("--- вывод по первому оборванному звену ---")[1]
    first_fail = next(line for line in no_plugin.splitlines() if line.startswith("FAIL"))
    assert first_fail.removeprefix("FAIL").strip() in summary
