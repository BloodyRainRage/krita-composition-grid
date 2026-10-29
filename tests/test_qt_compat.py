"""Tests for the ``composition_grid.qt_compat`` normalization layer.

Must hold when PyQt is present AND absent:

- ``test_qt_compat_importable`` always runs (pure-env import must succeed).
- The constant checks are gated: when ``QT_AVAILABLE`` is False they skip
  via ``pytest.skip(..., allow_module_level=False)`` at the top of their
  scope, per the pinned contract.
"""

import importlib

import pytest

import composition_grid.qt_compat as qt_compat

# --- pinned export lists (verbatim; do not edit without updating qt_compat docstring) ---

_MODULES = ("QtCore", "QtGui", "QtWidgets", "Qt", "Signal", "QT_AVAILABLE", "qt_major")

_EVENT_TYPES = (
    "MousePress",
    "MouseMove",
    "MouseRelease",
    "Enter",
    "Leave",
    "Resize",
    "Close",
    "Destroy",
    "Show",
    "Hide",
    # The Krita 5.3 repaint triggers are viewport Qt events (Wheel is
    # the zoom gesture, Scroll/ScrollPrepare the scroll-area's own
    # notifications). Paint covers changes that move no scrollbar at all
    # (mirror, rotation via menu/N-key, zoom-to-cursor); LayoutRequest
    # covers the resetCanvas widget swap re-laying-out the scroll area.
    "Wheel",
    "Scroll",
    "ScrollPrepare",
    "Paint",
    "LayoutRequest",
)

_WIDGET_ATTRIBUTES = (
    "WA_TransparentForMouseEvents",
    "WA_TranslucentBackground",
    "WA_NoSystemBackground",
)

_PEN_STYLES = (
    "SolidLine",
    "CustomDashLine",
)

_CURSOR_SHAPES = (
    "CrossCursor",
    "SizeAllCursor",
    "SizeHorCursor",
    "SizeVerCursor",
    "SizeBDiagCursor",
    "SizeFDiagCursor",
    "OpenHandCursor",
    "ClosedHandCursor",
    "ArrowCursor",
    "PointingHandCursor",
)

_SINGLETON_SCALARS = (
    "AlignCenter",
    "Key_Escape",
    # Color-dialog option + result code (live-preview path of the color button).
    "ShowAlphaChannel",
    "DialogAccepted",
    # Item-view constants of the docker grid list (rows, eye toggle, rename).
    "UserRole",
    "ItemIsEnabled",
    "ItemIsSelectable",
    "ItemIsEditable",
    "LeftButton",
    "SingleSelection",
    "DoubleClicked",
    "EditKeyPressed",
    "P_Text",
    "P_WindowText",
)

# The explicit list of scalars that must exist as module attributes.
_SCALARS = _EVENT_TYPES + _WIDGET_ATTRIBUTES + _PEN_STYLES + _CURSOR_SHAPES + _SINGLETON_SCALARS
_MISSING_NOTE = "wrong name would silently break every consumer; fail loudly here instead"


def test_qt_compat_importable():
    """Module must import cleanly in a pure environment (no PyQt at all)."""
    mod = importlib.import_module("composition_grid.qt_compat")
    assert mod.QT_AVAILABLE in (True, False)
    if not mod.QT_AVAILABLE:
        assert mod.qt_major == 0
        assert not hasattr(mod, "QtCore"), "QtCore must be absent when PyQt is unavailable"
        assert not hasattr(mod, "Signal"), "Signal must be absent when PyQt is unavailable"


def test_normalized_constants_present():
    if not qt_compat.QT_AVAILABLE:
        pytest.skip("PyQt not installed", allow_module_level=False)
    for name in _MODULES:
        assert hasattr(qt_compat, name), f"qt_compat missing module export {name!r}: {_MISSING_NOTE}"
    for name in _SCALARS:
        assert hasattr(qt_compat, name), f"qt_compat missing normalized scalar {name!r}: {_MISSING_NOTE}"


def test_compat_consts_values_resolvable():
    if not qt_compat.QT_AVAILABLE:
        pytest.skip("PyQt not installed", allow_module_level=False)
    assert qt_compat.QT_AVAILABLE is True
    for name in _SCALARS:
        value = getattr(qt_compat, name)
        f"{value}"  # formatting must not raise
        repr(value)  # repr must not raise
    assert qt_compat.Qt is qt_compat.QtCore.Qt, "Qt must alias QtCore.Qt"
    expected_signal = getattr(qt_compat.QtCore, "Signal", None)
    if expected_signal is None:
        expected_signal = getattr(qt_compat.QtCore, "pyqtSignal", None)
    # PyQt exposes `pyqtSignal` (the flat `Signal` name is PySide's); the
    # normalized export must alias whatever PyQt6/PyQt5 actually provide.
    assert expected_signal is not None, "PyQt must expose Signal/pyqtSignal on QtCore"
    assert qt_compat.Signal is expected_signal, "Signal must alias QtCore's real signal factory"
    assert qt_compat.qt_major in (5, 6)


# The repaint triggers, with the two enum paths _pick() resolves them
# through: Qt6-scoped first, Qt5-flat as the fallback.
_QEVENT_TRIGGER_PATHS = {
    "Wheel": ("QtCore.QEvent.Type.Wheel", "QtCore.QEvent.Wheel"),
    "Scroll": ("QtCore.QEvent.Type.Scroll", "QtCore.QEvent.Scroll"),
    "ScrollPrepare": (
        "QtCore.QEvent.Type.ScrollPrepare",
        "QtCore.QEvent.ScrollPrepare",
    ),
    "Paint": ("QtCore.QEvent.Type.Paint", "QtCore.QEvent.Paint"),
    "LayoutRequest": (
        "QtCore.QEvent.Type.LayoutRequest",
        "QtCore.QEvent.LayoutRequest",
    ),
}


def test_viewport_trigger_event_types_resolve_through_the_binding_own_enum():
    """The normalized export for each new trigger must be the enum member THIS
    binding actually has, resolved scoped-first (Qt6) with the flat Qt5 path as
    the fallback — a stand-in value here would silently kill every 5.3 repaint
    trigger, and the plugin could not tell: the filter compares on equality."""
    if not qt_compat.QT_AVAILABLE:
        pytest.skip("PyQt not installed", allow_module_level=False)
    for name, paths in _QEVENT_TRIGGER_PATHS.items():
        exported = getattr(qt_compat, name)
        resolved = None
        for path in paths:
            head, *attrs = path.split(".")
            obj = getattr(qt_compat, head, None)
            for attr in attrs:
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            if obj is not None:
                resolved = obj
                break
        assert resolved is not None, f"{name}: no binding exposes any of {paths}"
        assert exported == resolved, (
            f"{name}: exported {exported!r} is not the binding's own {resolved!r}"
        )
        assert exported != qt_compat.Resize, f"{name} must not alias another event type"
