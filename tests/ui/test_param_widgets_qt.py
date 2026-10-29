"""Qt tests for the docker panel and the parameter widgets.

ALL tests are gated on PyQt availability with the same two-layer gate as
``tests.canvas.test_overlay_qt``: a ``pytest.mark.skipif`` evaluated from a
non-raising importlib probe (a clean whole-module SKIP without PyQt — no Qt
value is dereferenced at module import), plus an autouse fixture calling the
shared ``tests.conftest.try_import_qt()`` inside test scope.
``QT_QPA_PLATFORM=offscreen`` is set before any Qt import runs.

The docker is driven headlessly against a REAL :class:`GridStateService`
(plus fake doc-size provider / fake settings transport): user-level widget
operations go through ``click()`` / ``setValue()`` / ``setCurrentIndex()``
and the assertions check the *service state* (and, for the settings test,
the transport payload) — never merely "the widget exists".

Pinned semantics implemented here:

* ``build_param_widgets``: one control per ``Param`` — ``number`` with an
  int default -> QSpinBox, float default -> QDoubleSpinBox, both honoring
  the schema minimum/maximum; ``bool`` -> QCheckBox; ``choice`` -> QComboBox
  with the schema choices in order and the current value selected. A user
  change calls ``on_change(name, raw_widget_value)`` (the service coerces).
* Docker: type combo routes ``service.set_type``; the params section is
  REBUILT on type change (no stale controls); width spin (range 0.25..12,
  step 0.05) and opacity slider (0..100 -> /100) route ``set_style``; the
  dash combo produces the pinned ``DASH_PATTERNS`` tuple; the visibility
  checkbox reflects and drives ``model.visible``; unchecking the Edit button
  calls ``end_edit_session``; Reset uses the injected doc-size provider;
  the ratio-lock checkbox writes ``transform.ratio_locked`` directly (the
  model-mutation convention of ``TransformController`` — a drag-mode flag
  that changes no geometry, so no service notify).
* Service -> widget echo: any external mutation (``set_type`` etc. called
  programmatically on the service) refreshes the controls (combo index,
  slider values) through a ``blockSignals`` refresh pass — standard Qt
  echo protection, not a hack.
* Settings: with a ``JsonGridSettings`` injected, every service mutation
  saves the active model (asserted as the JSON dict in the fake transport).
* ``make_panel_docker_class(base)`` builds the Krita ``PanelDocker`` from an
  INJECTED base class (``krita.DockWidget`` in production, QWidget here):
  the module never imports krita (ui guard: raw PyQt/krita banned; Qt only
  via qt_compat); ``canvasChanged(canvas)`` forwards to the injected
  provider callback (the plugin entry wires the CanvasContext).
"""

from __future__ import annotations

import json
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
from composition_grid.canvas.doc_view_mapper import ViewState
from composition_grid.core.geometry import Bounds
from composition_grid.core.grids import (
    CenterCross,
    GoldenSpiral,
    Param,
    UniformGrid,
    all_types,
    default_params,
)
from composition_grid.core.styles import Style
from composition_grid.model.grid_model import GridStateService
from composition_grid.model.settings_store import JsonGridSettings
from composition_grid.ui.docker_panel import GridPanel
from composition_grid.ui.docker_registration import (
    DOCKER_WINDOW_TITLE,
    make_panel_docker_class,
    shared_state_service,
)
from composition_grid.ui.param_widgets import build_param_widgets
from composition_grid.ui.style_controls import DASH_PATTERNS
from composition_grid.ui.transform_controller import TransformController


def _qt_available() -> bool:
    """Non-raising probe mirroring ``try_import_qt`` order (PyQt6 first);
    probes the ``QtWidgets`` SUBMODULE so a half-removed install reads as
    unavailable (same pattern as the sibling Qt test modules)."""
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
    submodules all import; ``None`` without PyQt."""
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

_KEEP_APP_REF = None


def _ensure_qapp():
    """Create the shared QApplication singleton once (offscreen platform).

    PyQt6 GOTCHA (pinned by the sibling Qt modules): the wrapper must be kept
    referenced for the whole test lifetime or Qt tears the C++ app down
    mid-suite."""
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
    _ensure_qapp()
    yield


class FakeTransport:
    """In-memory ``SettingsTransport`` double (same contract as the
    settings-store test): every write is recorded under ``"group|key"``."""

    def __init__(self):
        self.data = {}

    def read(self, group, key):
        return self.data.get(f"{group}|{key}")

    def write(self, group, key, value):
        self.data[f"{group}|{key}"] = value


def _service_with_doc() -> GridStateService:
    """A real service with one active 800x600 document holding ONE grid.

    ``ensure`` alone creates an EMPTY container now — the docker's controls edit
    the document's SELECTED grid, so the tests that drive them ask for a grid."""
    service = GridStateService()
    service.ensure("doc1", 800.0, 600.0)
    service.set_active("doc1")
    service.add_grid()
    return service


def _active_model(service: GridStateService):
    return service.selected(service.active_doc_id)


def _panel(service, **kwargs) -> GridPanel:
    return GridPanel(service, **kwargs)


def _recorder(calls):
    """on_change sink collecting ``(name, value)`` pairs in order."""

    def _on_change(name, value):
        calls.append((name, value))

    return _on_change


class _BogusType:
    """Minimal grid-type stand-in whose schema carries an unknown ``Param.kind``
    so ``build_param_widgets`` raises (drives the rebuild-survival test)."""

    id = "bogus_type"
    params = (Param(name="weird", kind="banana", default=1),)


# ---------------------------------------------------------------- param widgets


def test_param_widget_number_bounds():
    """number params get spin boxes bounded by the schema (uniform_grid
    columns 1..24 int -> QSpinBox; margin 0..0.45 float -> QDoubleSpinBox)."""
    container = build_param_widgets(
        UniformGrid.params, default_params(UniformGrid), lambda name, value: None
    )
    columns = container.findChild(QtWidgets.QSpinBox, "columns")
    assert columns is not None
    assert columns.minimum() == 1
    assert columns.maximum() == 24
    assert columns.value() == 3

    margin = container.findChild(QtWidgets.QDoubleSpinBox, "margin")
    assert margin is not None
    assert margin.minimum() == pytest.approx(0.0)
    assert margin.maximum() == pytest.approx(0.45)


def test_number_widget_emits_raw_value_on_change():
    """A programmatic spin change (valueChanged fires) reports the RAW widget
    value with the param name — the service performs the coercion."""
    calls = []
    container = build_param_widgets(
        UniformGrid.params, default_params(UniformGrid), _recorder(calls)
    )
    container.findChild(QtWidgets.QSpinBox, "columns").setValue(5)
    assert calls == [("columns", 5)]


def test_bool_widget_emits_on_change():
    """bool param -> QCheckBox; clicking it calls on_change with the new
    boolean (center_cross show_vertical default True -> False)."""
    calls = []
    container = build_param_widgets(
        CenterCross.params, default_params(CenterCross), _recorder(calls)
    )
    box = container.findChild(QtWidgets.QCheckBox, "show_vertical")
    assert box.isChecked()
    box.click()
    assert calls == [("show_vertical", False)]


def test_combo_choices_order():
    """choice param -> QComboBox: schema order, current value selected;
    picking another entry emits the choice STRING."""
    calls = []
    container = build_param_widgets(
        GoldenSpiral.params, default_params(GoldenSpiral), _recorder(calls)
    )
    combo = container.findChild(QtWidgets.QComboBox, "origin")
    entries = [combo.itemText(i) for i in range(combo.count())]
    assert entries == list(GoldenSpiral.params[0].choices) == ["tl", "tr", "br", "bl"]
    assert combo.currentText() == "tl"
    combo.setCurrentIndex(2)
    assert calls == [("origin", "br")]


def test_current_value_without_default_selects_combo():
    """``current`` overrides the schema default: origin="bl" is preselected."""
    container = build_param_widgets(
        GoldenSpiral.params, {"origin": "bl", "show_subdivision": True},
        lambda name, value: None,
    )
    assert container.findChild(QtWidgets.QComboBox, "origin").currentText() == "bl"


# --------------------------------------------------------------------- docker


def test_docker_lists_all_twelve_types_and_current():
    service = _service_with_doc()
    panel = _panel(service)
    combo = panel.findChild(QtWidgets.QComboBox, "grid_type")
    assert combo.count() == 12
    assert [combo.itemText(i) for i in range(combo.count())] == [
        cls.name for cls in all_types()
    ]
    assert combo.currentText() == "Rule of Thirds"  # registry-first active model


def test_docker_type_combo_routes_set_type_and_rebuilds_params():
    """Selecting a type in the combo routes ``service.set_type``; the params
    section rebuilds to the new schema (uniform controls appear, center-cross
    controls are gone afterwards)."""
    service = _service_with_doc()
    panel = _panel(service)
    combo = panel.findChild(QtWidgets.QComboBox, "grid_type")
    combo.setCurrentIndex([cls.id for cls in all_types()].index("uniform_grid"))
    model = _active_model(service)
    assert model.grid_type_id == "uniform_grid"
    assert panel.findChild(QtWidgets.QSpinBox, "columns") is not None

    combo.setCurrentIndex([cls.id for cls in all_types()].index("center_cross"))
    assert _active_model(service).grid_type_id == "center_cross"
    assert panel.findChild(QtWidgets.QSpinBox, "columns") is None  # stale gone
    assert panel.findChild(QtWidgets.QCheckBox, "show_vertical") is not None


def test_docker_style_controls_route_service():
    """Width spin (0.25..12, step 0.05), opacity slider (50 -> 0.5) and the
    dash combo (pinned pattern tuples) all write through to model.style."""
    service = _service_with_doc()
    panel = _panel(service)

    width = panel.findChild(QtWidgets.QDoubleSpinBox, "width_px")
    assert width.minimum() == pytest.approx(0.25)
    assert width.maximum() == pytest.approx(12.0)
    assert width.singleStep() == pytest.approx(0.05)
    width.setValue(2.5)
    assert _active_model(service).style.width_px == pytest.approx(2.5)

    panel.findChild(QtWidgets.QSlider, "opacity").setValue(50)
    assert _active_model(service).style.opacity == pytest.approx(0.5)

    dash = panel.findChild(QtWidgets.QComboBox, "dash")
    assert list(DASH_PATTERNS) == [
        dash.itemText(i) for i in range(dash.count())
    ] == ["Solid", "Dash", "Dot", "DashDot"]
    dash.setCurrentIndex(1)
    assert _active_model(service).style.dash == DASH_PATTERNS["Dash"]


def test_docker_visibility_and_edit_session():
    """The visibility checkbox drives ``model.visible`` (via the service);
    an externally started session is reflected in the Edit button and
    unchecking the button calls ``end_edit_session``."""
    service = _service_with_doc()
    panel = _panel(service)

    visible = panel.findChild(QtWidgets.QCheckBox, "visible")
    assert visible.isChecked()  # model starts visible
    visible.click()
    assert _active_model(service).visible is False

    service.start_edit_session()  # external mutation -> button follows
    edit = panel.findChild(QtWidgets.QPushButton, "edit")
    assert edit.isCheckable() and edit.isChecked()
    edit.click()  # user unchecks -> session ends through the service
    assert _active_model(service).edit_session is False
    assert not edit.isChecked()


def test_docker_remove_button_removes_the_grid_and_disables_its_controls():
    """TWO buttons (multi-grid): "Remove grid" DELETES the selected
    grid and disables every grid control; "Add grid" creates the next one with
    the controls live again."""
    service = _service_with_doc()
    panel = _panel(service)
    add_button = panel.findChild(QtWidgets.QPushButton, "add_grid")
    remove_button = panel.findChild(QtWidgets.QPushButton, "remove_grid")
    combo = panel.findChild(QtWidgets.QComboBox, "grid_type")
    visible = panel.findChild(QtWidgets.QCheckBox, "visible")

    assert add_button.isEnabled(), "adding is always offered"
    assert remove_button.isEnabled()
    assert combo.isEnabled()

    remove_button.click()
    assert service.selected() is None, "the grid is gone, not hidden"
    assert not remove_button.isEnabled(), "a document without a grid has nothing to remove"
    assert not combo.isEnabled(), "a removed grid has nothing to configure"
    assert not visible.isEnabled(), "and nothing to hide either"

    add_button.click()
    assert service.selected() is not None
    assert remove_button.isEnabled()
    assert combo.isEnabled() and visible.isEnabled()


def test_docker_remove_button_without_a_document_is_a_no_op():
    """No routed document: the Remove button is disabled, its click changes no
    service state (it never invents a grid to remove)."""
    service = GridStateService()
    panel = _panel(service)
    button = panel.findChild(QtWidgets.QPushButton, "remove_grid")
    notifications = []
    service.on_change(lambda: notifications.append(1))

    assert not button.isEnabled(), "nothing selected: the button is disabled"
    button.click()

    assert service.active_doc_id is None
    assert notifications == [], "nothing to remove is nothing to notify"


def test_docker_refresh_follows_external_mutation():
    """Programmatic service mutations (no widget involved) refresh every
    control: combo index, spin/slider values, visibility."""
    service = _service_with_doc()
    panel = _panel(service)
    service.set_type("uniform_grid")
    service.set_param("columns", 7)
    service.set_style(opacity=0.25)
    service.toggle_visible()

    assert panel.findChild(QtWidgets.QComboBox, "grid_type").currentText() == "Uniform Grid"
    assert panel.findChild(QtWidgets.QSpinBox, "columns").value() == 7
    assert panel.findChild(QtWidgets.QSlider, "opacity").value() == 25
    assert not panel.findChild(QtWidgets.QCheckBox, "visible").isChecked()


def test_docker_editing_targets_the_selected_grid_only():
    """Two grids in one document: every control edits the SELECTED one, and
    changing the selection swaps the whole panel — the other grid's values are
    neither shown nor touched."""
    service = _service_with_doc()
    panel = _panel(service)
    first_id = service.selected_grid_id()
    service.set_type("uniform_grid")
    service.set_param("columns", 7)
    service.set_style(opacity=0.25)
    first = service.selected()

    second_id = service.add_grid()
    assert second_id not in (None, first_id), "a second grid joins the document"
    assert service.selected_grid_id() == second_id
    second = service.selected()

    combo = panel.findChild(QtWidgets.QComboBox, "grid_type")
    assert combo.currentData() == second.grid_type_id
    assert panel.findChild(QtWidgets.QSpinBox, "columns") is None, (
        "the params section belongs to the selected grid, not to the last edited one"
    )
    assert panel.findChild(QtWidgets.QSlider, "opacity").value() == round(
        second.style.opacity * 100
    )

    service.select_grid(first_id)
    assert combo.currentData() == "uniform_grid"
    assert panel.findChild(QtWidgets.QSpinBox, "columns").value() == 7
    assert panel.findChild(QtWidgets.QSlider, "opacity").value() == 25
    assert first.style.opacity == pytest.approx(0.25), "editing B never restyles A"
    assert service.grids() == (first, second), "the list order is the document order"


def test_docker_refresh_does_not_echo_back():
    """The refresh pass must NOT re-trigger service mutations: one user width
    gesture is ONE ``set_style`` call (Qt's clamp keeps the value pinned at 12),
    and an external style write that pulls a stale spin forward does not echo a
    second mutation. The docstring's "stores the value once" is asserted here
    by counting, not assumed."""
    service = _service_with_doc()
    panel = _panel(service)
    width = panel.findChild(QtWidgets.QDoubleSpinBox, "width_px")

    # Spy every set_style call (the panel handler + refresh route through it).
    real_set_style = service.set_style
    style_calls = []

    def spy_set_style(**patch):
        style_calls.append(patch)
        return real_set_style(**patch)

    service.set_style = spy_set_style

    width.setValue(20.0)  # beyond the spin's 12.0 maximum: Qt clamps to 12
    assert _active_model(service).style.width_px == pytest.approx(12.0)
    assert width.value() == pytest.approx(12.0)
    # Exactly one mutation for the single user gesture (no re-entrant echo).
    assert style_calls == [{"width_px": 12.0}]

    # External write that changes the displayed spin value: the blockSignals
    # refresh must sync the widget WITHOUT firing _on_width_changed again.
    style_calls.clear()
    real_set_style(width_px=7.5)  # the widget shows 12.0 -> refresh writes 7.5
    assert width.value() == pytest.approx(7.5)
    assert style_calls == []  # refresh echoed nothing back into the handler


def test_docker_no_active_model_reverts_checkboxes():
    """A stray click with no active document must not leave a flipped control:
    the visibility and ratio-lock checkboxes revert to their pre-click state."""
    service = GridStateService()  # nothing ensured, nothing active
    panel = _panel(service)

    visible = panel.findChild(QtWidgets.QCheckBox, "visible")
    before = visible.isChecked()
    visible.click()
    assert visible.isChecked() is before  # reverted, no model written

    lock = panel.findChild(QtWidgets.QCheckBox, "ratio_lock")
    before = lock.isChecked()
    lock.click()
    assert lock.isChecked() is before


def test_docker_rebuild_survives_unknown_param_kind():
    """A bogus ``Param.kind`` makes ``build_param_widgets`` raise; the panel
    must keep the PREVIOUS controls (build-first-swap-on-success) rather than
    leaving the container pointing at a deleted widget."""
    service = _service_with_doc()
    service.set_type("uniform_grid")  # real controls first (columns spin)
    panel = _panel(service)
    columns = panel.findChild(QtWidgets.QSpinBox, "columns")
    assert columns is not None
    old_container = panel._params_container

    bogus = _BogusType()
    panel._rebuild_params(bogus, {})  # raises internally -> caught, logged

    assert panel._params_container is old_container  # untouched
    assert panel.findChild(QtWidgets.QSpinBox, "columns") is not None  # usable
    assert panel._params_type_id == "uniform_grid"  # not swapped to the bogus id


def test_docker_dash_label_survives_json_list():
    """A dash restored from JSON arrives as a LIST; ``Style.__post_init__``
    normalizes it back to a tuple so the preset label matches and no spurious
    "custom dash" WARNING is logged (the docker must not depend on
    tuple restoration elsewhere)."""
    service = _service_with_doc()
    # Simulate the settings_store round-trip: style.dash was a tuple, JSON
    # turned it into a list; restore the model style from that list.
    restored_dash = list(DASH_PATTERNS["Dash"])  # [6.0, 3.0]
    _active_model(service).style = Style(dash=restored_dash)
    panel = _panel(service)

    dash_combo = panel.findChild(QtWidgets.QComboBox, "dash")
    assert dash_combo.currentText() == "Dash"
    assert _active_model(service).style.dash == DASH_PATTERNS["Dash"]  # tuple


def test_docker_reset_uses_injected_doc_size_provider():
    """Reset routes ``service.reset_transform(w, h)`` with the CURRENT doc
    size from the provider: 1000x800 -> centered square 0.8*800 = 640."""
    service = _service_with_doc()
    panel = _panel(service, doc_size_provider=lambda: (1000.0, 800.0))
    panel.findChild(QtWidgets.QPushButton, "reset").click()
    ts = _active_model(service).transform
    assert (ts.cx, ts.cy) == (500.0, 400.0)
    assert ts.width == pytest.approx(640.0)
    assert ts.height == pytest.approx(640.0)
    assert ts.rotation_deg == pytest.approx(0.0)


def test_docker_ratio_lock_checkbox_routes_service_and_persists():
    """The lock flag is service-owned: a click writes through
    ``service.set_ratio_lock`` AND reaches the injected transport (the bug the
    old direct-model-write hid — ``GridModel.emit`` never fired the persistence
    subscriber, so the transport stayed empty)."""
    transport = FakeTransport()
    service = _service_with_doc()
    panel = _panel(service, settings=JsonGridSettings(transport))
    lock = panel.findChild(QtWidgets.QCheckBox, "ratio_lock")
    assert lock.isChecked() is True  # def_element starts locked
    lock.click()
    assert _active_model(service).transform.ratio_locked is False

    payload = json.loads(transport.data["composition_grid|active_grid"])
    assert payload["transform"]["ratio_locked"] is False


def test_docker_rotation_spin_routes_service_and_normalizes():
    """The numeric angle field writes the absolute rotation through
    ``service.set_rotation`` (normalize into (-180, 180], then persist)."""
    transport = FakeTransport()
    service = _service_with_doc()
    panel = _panel(service, settings=JsonGridSettings(transport))
    spin = panel.findChild(QtWidgets.QDoubleSpinBox, "rotation_deg")
    assert spin.minimum() == pytest.approx(-180.0)
    assert spin.maximum() == pytest.approx(180.0)
    assert spin.singleStep() == pytest.approx(1.0)

    spin.setValue(90.0)
    assert _active_model(service).transform.rotation_deg == pytest.approx(90.0)
    payload = json.loads(transport.data["composition_grid|active_grid"])
    assert payload["transform"]["rotation_deg"] == pytest.approx(90.0)

    # A beyond-range entry the spin itself allows is wrapped by the service.
    spin.setValue(180.0)
    assert _active_model(service).transform.rotation_deg == pytest.approx(180.0)


def test_docker_rotation_follows_external_mutation_without_echo():
    """An external ``set_rotation`` refreshes the spin; a user gesture maps to
    exactly ONE service mutation (the blockSignals pass never re-enters)."""
    service = _service_with_doc()
    notify_calls = [0]
    service.on_change(lambda: notify_calls.__setitem__(0, notify_calls[0] + 1))
    panel = _panel(service)
    spin = panel.findChild(QtWidgets.QDoubleSpinBox, "rotation_deg")

    service.set_rotation(45.0)  # external -> spin follows
    assert spin.value() == pytest.approx(45.0)
    assert notify_calls[0] == 1  # one notify for the external mutation

    notify_calls[0] = 0
    spin.setValue(-30.0)  # user gesture -> exactly one notify, no echo
    assert _active_model(service).transform.rotation_deg == pytest.approx(-30.0)
    assert notify_calls[0] == 1


def test_docker_color_button_applies_rgba():
    """apply_color is the dialog-free seam of the color button (the click
    handler opens the modal QColorDialog in production and calls this)."""
    service = _service_with_doc()
    panel = _panel(service)
    panel.apply_color(QtGui.QColor(255, 0, 0, 255))
    assert _active_model(service).style.color_rgba == pytest.approx((1.0, 0.0, 0.0, 1.0))
    button = panel.findChild(QtWidgets.QPushButton, "color")
    assert "#FF0000" in button.text()


def test_docker_saves_settings_on_mutation_when_store_injected():
    transport = FakeTransport()
    settings = JsonGridSettings(transport)
    service = _service_with_doc()
    panel = _panel(service, settings=settings)
    panel.findChild(QtWidgets.QDoubleSpinBox, "width_px").setValue(3.0)

    raw = transport.data["composition_grid|active_grid"]
    payload = json.loads(raw)
    assert payload["grid_type_id"] == "rule_of_thirds"
    assert payload["style"]["width_px"] == pytest.approx(3.0)


def test_docker_does_not_save_without_active_model():
    """Persistence only happens while an ACTIVE model exists: a notify that
    leaves no active document (removal) must write nothing."""
    transport = FakeTransport()
    service = _service_with_doc()
    _panel(service, settings=JsonGridSettings(transport))
    service.remove("doc1")  # notify -> active model gone
    assert transport.data == {}


def test_make_panel_docker_class_wraps_content_and_forwards_canvas():
    """PanelDocker is created dynamically over an INJECTED base (the ui layer
    never imports krita; production passes ``krita.DockWidget``): it embeds the
    GridPanel content and ``canvasChanged`` forwards to the provider.

    The base here is ``QDockWidget``, NOT ``QWidget``: libkis ``DockWidget``
    inherits QDockWidget, and a QDockWidget shows only the widget installed
    through ``setWidget()`` plus the label from ``setWindowTitle()``. Passing a
    plain QWidget was what let the first live Krita run show an unlabelled,
    empty docker while every test stayed green."""
    received = []
    service = _service_with_doc()
    docker_cls = make_panel_docker_class(
        QtWidgets.QDockWidget,
        service=service,
        on_canvas_changed=received.append,
    )
    assert docker_cls.__name__ == "PanelDocker"
    docker = docker_cls()
    assert isinstance(docker.panel, GridPanel)
    # The content must be INSIDE the dock's widget slot, reachable by the user:
    # a layout on the dock itself (the pre-fix shape) leaves widget() None.
    content = docker.widget()
    assert content is not None, "the dock shows nothing without setWidget()"
    assert docker.panel in content.findChildren(GridPanel)
    assert docker.windowTitle() == DOCKER_WINDOW_TITLE, "Dockers list label"
    sentinel = object()
    docker.canvasChanged(sentinel)
    assert received == [sentinel]


def test_make_panel_docker_class_falls_back_to_shared_service():
    """Without an injected service the docker resolves the module-level
    shared GridStateService (the Krita factory instantiates with no args)."""
    docker_cls = make_panel_docker_class(QtWidgets.QDockWidget)
    docker = docker_cls()
    assert docker.panel.service is shared_state_service()


# --------------------------------------------------- drag -> docker + persist
#
# The regression this pins: the canvas drag path
# wrote ``model.transform`` / ``model.params`` and called ``model.emit()``, but
# the docker (``GridPanel._refresh`` / ``_save_settings``) subscribes to
# ``GridStateService.on_change`` — which ``emit`` never fired. Result: after a
# drag the Rotation / vp_x / vp_y docker fields stayed STALE and the change was
# NEVER PERSISTED. The fix routes the service as the single hub (``model.emit``
# now forwards to ``GridStateService._notify``). These tests drive a REAL
# ``TransformController`` over a REAL ``GridStateService`` + ``GridPanel`` and a
# fake transport, so they exercise the exact production wiring of the drag path.


def _view_800x600() -> ViewState:
    """A doc 1:1 into widget px (rect == doc extent, no rotation/mirror), so a
    widget point and its doc point coincide — drag geometry is legible."""
    return ViewState(
        document_rect_widget=Bounds(0.0, 0.0, 800.0, 600.0),
        rotation_deg=0.0,
        mirror_x=False,
        mirror_y=False,
        doc_width=800.0,
        doc_height=600.0,
    )


def _rotate_drag(service: GridStateService) -> TransformController:
    """Start a rotate drag and take one step that yields a +90 deg rotation."""
    model = _active_model(service)
    ts = model.transform
    view = _view_800x600()
    controller = TransformController(lambda: view, lambda: model)
    # element center for an 800x600 doc is (400, 300); a start point straight
    # above it (-90 deg) dragged to straight right of it (0 deg) => +90 deg.
    assert controller.begin_drag("rotate", (ts.cx, ts.cy - 100.0), False) is True
    assert controller.drag_to((ts.cx + 100.0, ts.cy), False) is True
    controller.end_drag()
    return controller


def test_rotate_drag_updates_docker_rotation_field():
    """After a real rotate drag the docker's "Rotation (deg)"
    shows the new angle instead of the stale pre-drag value."""
    service = _service_with_doc()
    panel = _panel(service)
    spin = panel.findChild(QtWidgets.QDoubleSpinBox, "rotation_deg")
    assert spin.value() == pytest.approx(0.0)  # def_element starts unrotated

    _rotate_drag(service)
    model = _active_model(service)
    assert model.transform.rotation_deg == pytest.approx(90.0)
    # The drag must follow into the docker field (was STALE before the fix).
    assert spin.value() == pytest.approx(model.transform.rotation_deg)


def test_vp_drag_updates_docker_vp_param_controls():
    """The vp path: after a vp0 drag the vp_x / vp_y param
    controls show the new values instead of the schema defaults."""
    service = _service_with_doc()
    service.set_type("perspective_grid")
    panel = _panel(service)  # built after the type change: vp controls exist
    model = _active_model(service)
    vpx = panel.findChild(QtWidgets.QDoubleSpinBox, "vp_x")
    vpy = panel.findChild(QtWidgets.QDoubleSpinBox, "vp_y")
    assert vpx.value() == pytest.approx(0.5)  # schema default shown on build
    assert vpy.value() == pytest.approx(0.5)

    ts = model.transform
    view = _view_800x600()
    controller = TransformController(lambda: view, lambda: model)
    # vp0 grabbed at its (0.5, 0.5) spot (400, 300); (280, 180) is (0.25, 0.25)
    assert controller.begin_drag("vp0", (400.0, 300.0), False) is True
    assert controller.drag_to((280.0, 180.0), False) is True
    controller.end_drag()

    assert model.params["vp_x"] == pytest.approx(0.25)
    assert model.params["vp_y"] == pytest.approx(0.25)
    # The drag must follow into the docker param controls (were stale).
    assert vpx.value() == pytest.approx(model.params["vp_x"])
    assert vpy.value() == pytest.approx(model.params["vp_y"])


def test_rotate_drag_persists_the_rotation():
    """The save-last-settings regression for the drag
    path: a drag that changes rotation is PERSISTED to the transport."""
    transport = FakeTransport()
    service = _service_with_doc()
    panel = _panel(service, settings=JsonGridSettings(transport))
    assert transport.data == {}  # nothing saved yet (panel subscribed after ensure)

    _rotate_drag(service)

    payload = json.loads(transport.data["composition_grid|active_grid"])
    assert payload["transform"]["rotation_deg"] == pytest.approx(90.0)


def test_vp_drag_persists_the_vp_params():
    """The same persistence pin for a dragged vp param (params live in the
    payload's ``params`` map)."""
    transport = FakeTransport()
    service = _service_with_doc()
    service.set_type("perspective_grid")
    panel = _panel(service, settings=JsonGridSettings(transport))
    assert transport.data == {}

    model = _active_model(service)
    view = _view_800x600()
    controller = TransformController(lambda: view, lambda: model)
    # grab the vp0 handle where it is drawn (400, 300) and drag it to (0.25, 0.25)
    controller.begin_drag("vp0", (400.0, 300.0), False)
    controller.drag_to((280.0, 180.0), False)
    controller.end_drag()

    payload = json.loads(transport.data["composition_grid|active_grid"])
    assert payload["params"]["vp_x"] == pytest.approx(0.25)
    assert payload["params"]["vp_y"] == pytest.approx(0.25)


def test_one_drag_step_notifies_service_and_saves_exactly_once():
    """Notification accounting: pick ONE path, no
    double notify. One drag step produces EXACTLY one service notification and
    EXACTLY one transport write — neither zero (the defect) nor two."""

    class _CountingTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.writes = 0

        def write(self, group, key, value):
            self.writes += 1
            super().write(group, key, value)

    transport = _CountingTransport()
    service = _service_with_doc()
    notifies = [0]
    service.on_change(lambda: notifies.__setitem__(0, notifies[0] + 1))
    _panel(service, settings=JsonGridSettings(transport))

    model = _active_model(service)
    ts = model.transform
    view = _view_800x600()
    controller = TransformController(lambda: view, lambda: model)
    assert controller.begin_drag("move", (ts.cx, ts.cy), False) is True

    notifies[0] = 0
    writes_before = transport.writes
    assert controller.drag_to((ts.cx + 10.0, ts.cy), False) is True
    assert notifies[0] == 1, "one drag step -> exactly one service notification"
    assert transport.writes == writes_before + 1, "one drag step -> exactly one save"

    assert controller.drag_to((ts.cx + 20.0, ts.cy), False) is True
    assert notifies[0] == 2, "the second step adds exactly one more, no accumulation"
    assert transport.writes == writes_before + 2

    # A service-level mutation still notifies exactly once (no double hop back
    # through model.emit, which would inflate the count now that _notify rides
    # on the model).
    notifies[0] = 0
    service.toggle_visible()
    assert notifies[0] == 1, "service mutation -> single hub notification"


# ------------------------------------------------- color preview chip


def _pixel_color(pixmap, x: int, y: int):
    """``QPixmap`` pixel as ``(r, g, b, a)`` through a QImage copy."""
    image = pixmap.toImage()
    color = image.pixelColor(x, y)
    return (color.red(), color.green(), color.blue(), color.alpha())


def test_color_button_shows_the_current_color_as_a_chip():
    """The Color control must PREVIEW the color, not only name it in hex: an
    artist verifies a line color by looking at it. The chip is painted from the
    model style, so changing the style through the service updates the chip."""
    service = _service_with_doc()
    panel = _panel(service)
    button = panel.findChild(QtWidgets.QPushButton, "color")

    panel.apply_color(QtGui.QColor(220, 10, 10, 255))
    icon_pixmap = button.icon().pixmap(button.iconSize())
    assert not button.icon().isNull(), "the color button carries a preview chip"
    center = _pixel_color(icon_pixmap, icon_pixmap.width() // 2, icon_pixmap.height() // 2)
    assert center[:3] == (220, 10, 10), f"opaque chip pixel, got {center}"
    assert center[3] == 255

    # A translucent line color reads as translucent (checkerboard shows through),
    # which is the reason the chip is not a flat solid swatch.
    panel.apply_color(QtGui.QColor(220, 10, 10, 128))
    blended = _pixel_color(
        button.icon().pixmap(button.iconSize()),
        button.iconSize().width() // 2,
        button.iconSize().height() // 2,
    )
    assert blended[:3] != (220, 10, 10), f"alpha must blend with the checker, got {blended}"
    assert blended[3] == 255, "the chip itself is fully opaque (blend is precomputed)"


def test_color_chip_follows_an_external_style_change():
    """The chip is refreshed from the service, not only from the dialog: a color
    set anywhere else (a restored setting, another window) is shown."""
    service = _service_with_doc()
    panel = _panel(service)
    button = panel.findChild(QtWidgets.QPushButton, "color")

    service.set_style(color_rgba=(0.0, 1.0, 0.0, 1.0))
    pixmap = button.icon().pixmap(button.iconSize())
    center = _pixel_color(pixmap, pixmap.width() // 2, pixmap.height() // 2)
    assert center[:3] == (0, 255, 0), f"green line color preview, got {center}"


# ------------------------------------------- docker-side on-demand routing


def test_type_selection_routes_a_document_that_startup_missed():
    """The panel is display-only while no model exists — but "no model" can also
    mean *nothing has asked yet*: Krita runs ``createActions`` before a view
    exists, so the document of the session was never routed. Selecting a grid
    type must trigger the route and APPLY, not silently drop the choice."""
    service = GridStateService()  # nothing ensured, nothing active
    routes = []

    def _route():
        routes.append(1)
        service.ensure("late.png", 800.0, 600.0)
        service.set_active("late.png")

    panel = _panel(service, on_route=_route)
    combo = panel.findChild(QtWidgets.QComboBox, "grid_type")
    add_button = panel.findChild(QtWidgets.QPushButton, "add_grid")
    index = combo.findData("center_cross")
    assert index >= 0

    assert not combo.isEnabled(), "a document without a grid has nothing to configure"
    add_button.click()

    assert routes, "the interaction asked the plugin to route the document"
    model = service.selected("late.png")
    assert model is not None, (
        "the routed document is the one the user's new grid belongs to"
    )

    combo.setCurrentIndex(index)
    assert model.grid_type_id == "center_cross", (
        "the user's choice is applied by the same interaction that routed it"
    )


def test_showing_the_panel_routes_without_waiting_for_an_interaction():
    """The panel becoming visible is itself the event: the user opened
    Settings ▸ Dockers and expects to see their grid, not to perform a gesture
    first."""
    service = GridStateService()
    routes = []
    panel = _panel(service, on_route=lambda: routes.append(1))

    assert routes == [], "construction alone does not route"
    panel.show()
    assert routes, "showing the panel asks for the document"
