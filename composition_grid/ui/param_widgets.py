"""Factory generating one Qt control per grid ``Param`` of a schema.

Qt module — Qt is touched ONLY through :mod:`composition_grid.qt_compat`
(the single legal PyQt import route); no ``krita`` import anywhere. The
module is *functions-only* (no QWidget subclass at module level), so it
imports cleanly even without PyQt — consistent with the sibling Qt modules,
which raise ``RuntimeError`` at CONSTRUCTION without PyQt (``overlay.GridOverlay``
encodes that as a stub class; here the constructors are functions, so each
Qt-touching entry point raises through :func:`_require_qt` at call time; the
value-rule helpers :func:`param_needs_reset` and :func:`reset_button_name` are
pure and never touch Qt).

Pinned conventions (``core.grids.base.Param`` schema):

* ``kind == "number"``: an int schema default -> ``QSpinBox`` (whole-count
  parameters like columns/rows/ray step), a float default -> ``QDoubleSpinBox``
  (3 decimals, step 0.01 — fine for every shipped schema range, e.g. the
  uniform-grid margin 0..0.45). The schema ``minimum``/``maximum`` become the
  widget range; a ``None`` bound falls back to ``0``/``10000`` (every shipped
  schema is bounded — this is the documented open-range default, not a clamp).
* ``kind == "bool"``: ``QCheckBox``.
* ``kind == "choice"``: ``QComboBox`` filled with ``Param.choices`` in schema
  order (no sorting — declaration order is the UI order); the current value
  is preselected, an unknown current value selects the schema default.
* Every control gets ``setObjectName(param.name)`` — that is how the docker
  finds controls again for its refresh pass (and how tests address them).
* Every row carries a compact ``QToolButton`` "↺" right of its control: the
  per-parameter reset (``on_reset(name)``, docker-routed to
  ``GridStateService.reset_param``). It is enabled exactly while the current
  value differs from ``Param.default`` (the docker re-applies that rule on
  every refresh), its tooltip names the default it restores, and its object
  name is :func:`reset_button_name` — the address the refresh pass uses. A row
  built without ``on_reset`` has no button at all (the factory stays usable
  for a read-only section).
* The emitted callback is ``on_change(name, raw_widget_value)``: the RAW Qt
  value (int/float/bool/choice-str) is reported uncoerced; the service
  (``GridStateService.set_param``) owns schema coercion/clamping.
* Echo safety: signals are connected AFTER the initial value is set, so
  building a container never calls ``on_change``. The reset button rides on
  ``clicked`` (user-only), so a programmatic refresh of the row can never
  fire it.

The returned value is a plain ``QWidget`` holding a ``QFormLayout`` of
``label -> control row`` rows (English labels: param name with underscores
spaced and capitalized).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from composition_grid import qt_compat
from composition_grid.core.grids.base import Param

__all__ = ["build_param_widgets", "param_needs_reset", "reset_button_name"]

_RESET_GLYPH = "↺"

_RESET_BUTTON_WIDTH_PX = 22
"""One compact square per row: wide enough for the glyph, narrow enough that a
spin box keeps the line's value column (the docker is a narrow dock)."""

_RESET_BUTTON_SUFFIX = "__reset"


def reset_button_name(param_name: str) -> str:
    """The objectName of ``param_name``'s reset button (module docstring): the
    docker's refresh pass and the tests address it, never its position."""
    return f"{param_name}{_RESET_BUTTON_SUFFIX}"


def param_needs_reset(param: Param, value: Any) -> bool:
    """``True`` when ``value`` differs from the schema default of ``param``.

    Plain ``!=`` is the whole rule: an int/float pair like ``3`` vs ``3.0``
    compares equal (the widget and the service may disagree on the numeric
    type, not on the value)."""
    return bool(value != param.default)


def _default_text(param: Param) -> str:
    """The default as it reads in a tooltip: ``3``, ``0.25``, ``True``, ``tl``."""
    return str(param.default)


def _require_qt(what: str) -> None:
    """Functions-only Qt module contract (module docstring): raise loudly at
    call time without PyQt instead of failing at import."""
    if not qt_compat.QT_AVAILABLE:
        raise RuntimeError(
            f"composition_grid.ui.param_widgets.{what} requires PyQt, "
            "but composition_grid.qt_compat reports QT_AVAILABLE=False"
        )


def _display_label(name: str) -> str:
    """Human English form label for a param name: ``show_vertical`` ->
    ``Show vertical`` (all UI strings are English)."""
    return name.replace("_", " ").capitalize()


def _make_number_control(param: Param, value: Any):
    """QSpinBox for an int-default schema, QDoubleSpinBox for a float one."""
    widgets = qt_compat.QtWidgets
    bounded = (
        0.0 if param.minimum is None else float(param.minimum),
        10000.0 if param.maximum is None else float(param.maximum),
    )
    if isinstance(param.default, int) and not isinstance(param.default, bool):
        control = widgets.QSpinBox()
        control.setRange(int(bounded[0]), int(bounded[1]))
        control.setValue(int(value))
    else:
        control = widgets.QDoubleSpinBox()
        control.setDecimals(3)
        control.setSingleStep(0.01)
        control.setRange(bounded[0], bounded[1])
        control.setValue(float(value))
    return control


def _make_control(param: Param, value: Any):
    """One configured, initial-value-set control for ``param`` (not yet
    connected to ``on_change`` — the caller wires that last)."""
    widgets = qt_compat.QtWidgets
    if param.kind == "number":
        return _make_number_control(param, value)
    if param.kind == "bool":
        control = widgets.QCheckBox()
        control.setChecked(bool(value))
        return control
    if param.kind == "choice":
        choices = tuple(param.choices or ())
        control = widgets.QComboBox()
        for choice in choices:  # schema order is UI order (module docstring)
            control.addItem(choice)
        current = str(value)
        if current not in choices:
            current = str(param.default)
        control.setCurrentIndex(choices.index(current) if choices else 0)
        return control
    raise ValueError(f"param_widgets: unsupported Param kind {param.kind!r}")


def _connect(control, name: str, on_change: Callable[[str, Any], None]) -> None:
    """Wire the user-change signal of ``control`` to ``on_change(name, raw)``.

    Signal choice per control: ``valueChanged`` for spins (fires on
    user AND programmatic setValue — the docker's refresh wraps programmatic
    writes in ``blockSignals``), ``clicked`` for the check box (user-only;
    ``toggled`` would echo programmatic setChecked back), ``currentIndexChanged``
    for the combo (programmatic index changes echo through blockSignals too).
    """
    widgets = qt_compat.QtWidgets
    if isinstance(control, widgets.QDoubleSpinBox):
        control.valueChanged.connect(lambda v: on_change(name, float(v)))
    elif isinstance(control, widgets.QSpinBox):
        control.valueChanged.connect(lambda v: on_change(name, int(v)))
    elif isinstance(control, widgets.QCheckBox):
        control.clicked.connect(lambda: on_change(name, control.isChecked()))
    elif isinstance(control, widgets.QComboBox):
        control.currentIndexChanged.connect(
            lambda _index: on_change(name, control.currentText())
        )
    else:  # pragma: no cover — _make_control only ever returns the four above
        raise ValueError(f"param_widgets: unknown control type {type(control)!r}")


def _make_reset_button(param: Param, value: Any, on_reset: Callable[[str], None]):
    """One compact "↺" tool button restoring ``param`` to its default.

    ``autoRaise`` is what keeps it visually light in a narrow row, and the fixed
    width is the compactness half of the contract: the control keeps the rest of
    the line."""
    widgets = qt_compat.QtWidgets
    button = widgets.QToolButton()
    button.setObjectName(reset_button_name(param.name))
    button.setText(_RESET_GLYPH)
    button.setToolTip(f"Reset to default ({_default_text(param)})")
    button.setAutoRaise(True)
    button.setFixedWidth(_RESET_BUTTON_WIDTH_PX)
    button.setEnabled(param_needs_reset(param, value))
    button.clicked.connect(lambda _checked=False: on_reset(param.name))
    return button


def build_param_widgets(
    params_schema: Sequence[Param],
    current: Mapping[str, Any] | None,
    on_change: Callable[[str, Any], None],
    on_reset: Callable[[str], None] | None = None,
):
    """Build the container widget holding one control per schema ``Param``.

    ``current`` supplies stored values (missing names fall back to each
    ``Param.default``); ``on_change(name, raw_value)`` fires on every user
    change with the uncoerced widget value. ``on_reset(name)``, when given, is
    fired by that row's "↺" button (no argument means no reset buttons at all).
    Returns a ``QWidget`` with a ``QFormLayout``; raises ``RuntimeError``
    without PyQt (module docstring).
    """
    _require_qt("build_param_widgets")
    widgets = qt_compat.QtWidgets
    container = widgets.QWidget()
    form = widgets.QFormLayout(container)
    values = current or {}
    for param in params_schema:
        value = values.get(param.name, param.default)
        control = _make_control(param, value)
        control.setObjectName(param.name)
        if on_reset is None:
            form.addRow(_display_label(param.name), control)
        else:
            row = widgets.QWidget()
            row_layout = widgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)
            row_layout.addWidget(control, 1)
            row_layout.addWidget(_make_reset_button(param, value, on_reset), 0)
            form.addRow(_display_label(param.name), row)
        _connect(control, param.name, on_change)  # connect AFTER init: no echo
    return container
