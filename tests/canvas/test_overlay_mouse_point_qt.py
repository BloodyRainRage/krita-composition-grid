"""Binding-surface regression tests for the overlay's ``_mouse_point`` adapter."""

from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]

import composition_grid.qt_compat as qt_compat
from composition_grid.canvas.doc_view_mapper import ViewState
from composition_grid.canvas.overlay import GridOverlay, _mouse_point
from composition_grid.core.geometry import Bounds
from composition_grid.core.grids import default_params, get
from composition_grid.core.styles import Style
from composition_grid.core.transform_state import TransformState
from composition_grid.model.grid_model import GridModel
from composition_grid.presentation.handle_layout import handle_positions

_VIEW = ViewState(
    document_rect_widget=Bounds(0.0, 0.0, 200.0, 200.0),
    rotation_deg=0.0,
    mirror_x=False,
    mirror_y=False,
    doc_width=200.0,
    doc_height=200.0,
)

# Fractional and negative: an integer getter cannot reproduce these numbers.
_X, _Y = -12.75, 34.25


class _PointF:
    """What a Qt ``QPointF`` exposes to Python: ``x()``/``y()`` accessors."""

    def __init__(self, x: float, y: float) -> None:
        self._x, self._y = x, y

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y


class _EventBase:
    """Members the handlers read, minus the position getters — those live only
    on the per-binding subclasses below, so ``getattr(event, "position", None)``
    genuinely answers ``None`` on the Qt5 shape, as it does on Qt 5.15."""

    def __init__(self, x: float, y: float) -> None:
        self._x, self._y = x, y
        self._accepted = False

    def pos(self) -> _PointF:
        return _PointF(
            float(math.floor(self._x + 0.5)), float(math.floor(self._y + 0.5))
        )

    def button(self):
        return _enum_member("MouseButton", "LeftButton")

    def buttons(self):
        return _enum_member("MouseButton", "LeftButton")

    def modifiers(self):
        return _enum_member("KeyboardModifier", "NoModifier")

    def accept(self) -> None:
        self._accepted = True

    def ignore(self) -> None:
        self._accepted = False

    def isAccepted(self) -> bool:
        return self._accepted


class Qt5MouseEvent(_EventBase):
    """Qt 5.15 surface: ``localPos()`` fractional, ``pos()``, no ``position()``."""

    def localPos(self) -> _PointF:
        return _PointF(self._x, self._y)


class Qt6MouseEvent(_EventBase):
    """Qt 6 surface: ``position()`` fractional, ``pos()``, no ``localPos()``."""

    def position(self) -> _PointF:
        return _PointF(self._x, self._y)


def _enum_member(scope: str, name: str, base=None):
    """Scoped (Qt6) path first, flat (Qt5) fallback — the repo convention."""
    base = qt_compat.Qt if base is None else base
    scoped = getattr(base, scope, None)
    member = getattr(scoped, name, None) if scoped is not None else None
    if member is None:
        member = getattr(base, name)
    return member


_APP_REF = None


def _ensure_qapp():
    """Offscreen QApplication, kept referenced so Qt does not tear it down
    mid-suite."""
    global _APP_REF
    QtWidgets = try_import_qt()[0]
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        _APP_REF = QtWidgets.QApplication([])
    elif _APP_REF is None:
        _APP_REF = instance


def test_mouse_point_reads_the_qt5_local_pos_not_the_integer_pos():
    """A Qt5 event must yield its fractional, negative point, not ``pos()``."""
    event = Qt5MouseEvent(_X, _Y)
    assert not hasattr(event, "position"), "the Qt5 shape must not fake a Qt6 name"
    assert not hasattr(event, "posF"), "posF() is not a QMouseEvent API on either side"
    assert (event.pos().x(), event.pos().y()) != (_X, _Y), (
        "fixture: pos() must be distinguishable from localPos()"
    )

    assert _mouse_point(event) == (_X, _Y)


def test_mouse_point_reads_the_qt6_position_surface():
    """The Qt6 surface must answer the identical fractional point."""
    event = Qt6MouseEvent(_X, _Y)
    assert not hasattr(event, "localPos"), "the Qt6 shape must not fake a Qt5 name"
    assert not hasattr(event, "posF")

    assert _mouse_point(event) == (_X, _Y)


def _real_mouse_event(x: float, y: float):
    """A GENUINE ``QMouseEvent`` from the installed binding; skips without PyQt."""
    _QtWidgets, QtCore, QtGui = try_import_qt()
    kind = _enum_member("Type", "MouseMove", QtCore.QEvent)
    button = _enum_member("MouseButton", "LeftButton", QtCore.Qt)
    return QtGui.QMouseEvent(
        kind,
        QtCore.QPointF(x, y),
        button,
        button,
        _enum_member("KeyboardModifier", "NoModifier", QtCore.Qt),
    )


def test_real_mouse_event_exposes_the_documented_surface_for_its_major():
    """The API facts the adapter is written against, on the binding in use."""
    _QtWidgets, _QtCore, QtGui = try_import_qt()
    cls = QtGui.QMouseEvent
    assert not hasattr(cls, "posF"), "posF() is the Qt4 name; neither binding has it"

    if qt_compat.qt_major >= 6:
        assert hasattr(cls, "position")
    else:
        assert hasattr(cls, "localPos")
        assert not hasattr(cls, "position"), (
            "Qt5 offers no position(); localPos() is the only fractional getter"
        )


@pytest.mark.parametrize(
    "coords", [(-12.75, 34.25), (0.25, 0.75), (116.666666, 83.333333)]
)
def test_mouse_point_returns_the_fractional_point_of_a_real_mouse_event(coords):
    """The live-host crash: a real Qt5 event has no ``posF()``, only localPos()."""
    x, y = coords
    event = _real_mouse_event(x, y)

    assert _mouse_point(event) == pytest.approx((x, y), abs=1e-6)


def test_press_with_a_qt5_shaped_event_drags_the_real_overlay():
    """A Qt5-shaped press/move pair must reach the drag with its fraction."""
    _ensure_qapp()
    model = GridModel(
        grid_type_id="rule_of_thirds",
        params=default_params(get("rule_of_thirds")),
        style=Style(),
        transform=TransformState(cx=100.0, cy=100.0, width=100.0, height=60.0),
        visible=True,
        edit_session=True,
    )
    overlay = GridOverlay(None, lambda: model, lambda: _VIEW)
    nw = handle_positions(_VIEW, model.transform)["nw"]
    assert (nw[0], nw[1]) == (50.0, 70.0), "fixture anchor, same as test_overlay_qt"

    press = Qt5MouseEvent(nw[0], nw[1])
    overlay.mousePressEvent(press)
    assert press.isAccepted(), "a Qt5-shaped press on a handle must be accepted"

    overlay.mouseMoveEvent(Qt5MouseEvent(nw[0] - 10.25, nw[1] - 10.25))
    assert model.transform.width == pytest.approx(110.25, abs=0.01), (
        "the drag must follow the fractional localPos, not the rounded pos()"
    )
