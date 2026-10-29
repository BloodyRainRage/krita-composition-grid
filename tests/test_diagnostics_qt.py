"""Qt-gated diagnostics test: the scrollbar reads against a REAL ``QScrollBar``.

WHY THIS FILE EXISTS (from the user's live report):

    scrollbar horizontal: min '<у QScrollBar нет метода minimumValue>', ...

``diagnostics`` called ``QScrollBar.minimumValue()``/``maximumValue()``. Those are
the Qt *property* names; the ``QAbstractSlider`` METHODS are
``minimum()``/``maximum()``/``value()``/``pageStep()``, and PyQt5 — the binding on
the user's Krita 5.3.4.1 — exposes only the methods, so the report printed error
text where it needed the numbers that settle the scale rule. The Qt-free fakes in
``tests/test_diagnostics.py`` were shaped like the *property* names and could not
catch that (the L1 rule: a fake must not be more permissive than the host), hence
this file drives the real widget.

Gate: the same two-layer pattern as the other Qt modules — a ``skipif`` from a
non-raising importlib probe (clean SKIP without PyQt) plus an autouse fixture
calling ``try_import_qt()``; no Qt value is dereferenced at module import.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]

from composition_grid import diagnostics  # noqa: F401  (Qt-free by contract)


def _qt_available() -> bool:
    """Non-raising probe mirroring ``try_import_qt`` order (PyQt6 first),
    probing the ``QtWidgets`` SUBMODULE so a half-removed install reads as
    unavailable."""
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


@pytest.fixture(autouse=True)
def _qt_gate():
    try_import_qt()
    yield


def _live_chain(bars):
    """The user's exact 5.3.4.1 answers (the same fakes the Qt-free diagnostics
    tests use) with *bars* handed to the context's trigger snapshot."""
    from tests.test_diagnostics import (
        _BoundContext,
        _FakeApp,
        _FakeDocument,
        _FakeExtension,
        _FakeView,
        _FakeWindow,
        _Point,
        _ScrollArea,
        _ViewportWidget,
    )

    class CanvasLive53:
        def zoomLevel(self):
            return 0.0833333333333357

        def rotation(self):
            return 0.0

        def mirror(self):
            return False

        def preferredCenter(self):
            return _Point(4860.0, 1854.0)

    class Document600dpi(_FakeDocument):
        def resolution(self):
            return 600.0

    document = Document600dpi("photo.kra", 7000, 4000)
    window = _FakeWindow(
        view=_FakeView(document=document, canvas=CanvasLive53()), qwindow=object()
    )
    context = _BoundContext(
        _ViewportWidget(),
        canvas=CanvasLive53(),
        bars=bars,
    )
    context.trigger_status["scroll_area"] = _ScrollArea()
    return diagnostics.collect_report(
        _FakeApp(document=document, window=window),
        extension=_FakeExtension(contexts=[context]),
    )


def _line(report, fragment):
    return next(line for line in report.splitlines() if fragment in line)


def test_report_prints_a_real_qscrollbar_as_four_numbers():
    """The pin: with genuine ``QScrollBar`` objects (the user's
    binding) all four values print as NUMBERS, not as ``<у QScrollBar нет метода
    …>`` error text — and the cross-check the report computes from them comes
    out zero, because the corrected scale places the document exactly where the
    host's own bars say it is (rect 212/284 vs bar values -212/-284)."""
    QtWidgets, QtCore, _QtGui = try_import_qt()

    horizontal = QtWidgets.QScrollBar()
    horizontal.setRange(-4000, 4000)
    horizontal.setValue(-212)
    horizontal.setPageStep(308)
    vertical = QtWidgets.QScrollBar()
    vertical.setRange(-4000, 4000)
    vertical.setValue(-284)
    vertical.setPageStep(219)

    report = _live_chain([horizontal, vertical])

    h_line = _line(report, "scrollbar horizontal")
    v_line = _line(report, "scrollbar vertical")
    for line, value, page in ((h_line, -212, 308), (v_line, -284, 219)):
        assert "нет метода" not in line, line
        assert "<" not in line, line
        assert "min -4000" in line, line
        assert "max 4000" in line, line
        assert f"value {value}" in line, line
        assert f"pageStep {page}" in line, line
    assert h_line.startswith("OK"), h_line

    check = _line(report, "cross-check")
    assert "Dx = 0.000" in check and "Dy = 0.000" in check, check
    # and the real-widget path still interprets the bool mirror answer:
    assert "Canvas.mirror() = False" in report
    assert "unusable mirror" not in report


def test_report_prints_the_host_scrollbars_of_a_real_scroll_area():
    """Same read through a whole real holder: a ``QScrollArea``'s own
    ``horizontalScrollBar()``/``verticalScrollBar()`` (what the trigger wiring
    actually stores) must also report numbers, so the live round never again
    mistakes a binding difference for a missing capability."""
    QtWidgets, _QtCore, _QtGui = try_import_qt()
    area = QtWidgets.QScrollArea()
    inner = QtWidgets.QWidget()
    inner.setFixedSize(3000, 3000)
    area.setWidget(inner)
    area.resize(1234, 877)
    area.show()
    QtWidgets.QApplication.processEvents()
    try:
        report = _live_chain(
            [area.horizontalScrollBar(), area.verticalScrollBar()]
        )
    finally:
        area.hide()
        area.deleteLater()

    for label in ("horizontal", "vertical"):
        line = _line(report, f"scrollbar {label}")
        assert "нет метода" not in line, line
        assert "min 0" in line, line
        assert "value" in line, line
