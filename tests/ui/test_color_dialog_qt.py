"""Real-QModal-loop tests for the docker color dialog's live preview, ownership, restoration and mid-exec destruction."""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from tests.conftest import try_import_qt
except ImportError:  # pragma: no cover - depends on pytest import layout
    from conftest import try_import_qt  # type: ignore[no-redef]

import composition_grid.qt_compat as qt_compat  # noqa: F401  (pure-safe import)
from composition_grid.model.grid_model import GridStateService
from composition_grid.model.settings_store import JsonGridSettings
from composition_grid.ui.docker_panel import GridPanel


def _qt_available() -> bool:
    import importlib

    for major in (6, 5):
        try:
            importlib.import_module(f"PyQt{major}.QtWidgets")
        except ImportError:
            continue
        return True
    return False


def _import_qt_modules():
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
    global _KEEP_APP_REF
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        _KEEP_APP_REF = QtWidgets.QApplication([])
    elif _KEEP_APP_REF is None:
        _KEEP_APP_REF = inst


def _enum(owner, *dotted):
    for path in dotted:
        obj = owner
        for part in path.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if obj is not None:
            return obj
    raise AssertionError("binding exposes none of %r on %r" % (dotted, owner))


def _process_deferred_deletes():
    deferred = _enum(QtCore.QEvent, "Type.DeferredDelete", "DeferredDelete")
    QtCore.QCoreApplication.sendPostedEvents(None, deferred)
    QtWidgets.QApplication.processEvents()


@pytest.fixture(autouse=True)
def _qt_gate():
    try_import_qt()
    _ensure_qapp()
    yield


@pytest.fixture(autouse=True)
def _nonnative_dialog(monkeypatch):
    """Test-only policy: a fixture-time subclass keeps modal widgets drivable."""
    base = QtWidgets.QColorDialog
    option = _enum(base, "ColorDialogOption.DontUseNativeDialog", "DontUseNativeDialog")

    class _WidgetOnlyColorDialog(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.setOption(option, True)

    monkeypatch.setattr(qt_compat.QtWidgets, "QColorDialog", _WidgetOnlyColorDialog)
    yield


_LIVE_PANELS = []


@pytest.fixture(autouse=True)
def _panel_teardown():
    _LIVE_PANELS.clear()
    yield
    for panel in _LIVE_PANELS:
        panel.close()
        panel.setParent(None)
        panel.deleteLater()
    _process_deferred_deletes()


def _panel(service, **kwargs) -> GridPanel:
    panel = GridPanel(service, **kwargs)
    _LIVE_PANELS.append(panel)
    # A really destroyed panel leaves the registry, so teardown never touches a dead wrapper.
    panel.destroyed.connect(lambda *_: panel in _LIVE_PANELS and _LIVE_PANELS.remove(panel))
    return panel


class RecordingTransport:
    def __init__(self):
        self.data = {}
        self.writes = 0

    def read(self, group, key):
        return self.data.get(f"{group}|{key}")

    def write(self, group, key, value):
        self.writes += 1
        self.data[f"{group}|{key}"] = value


def _service_with_doc() -> GridStateService:
    """A service with one active document holding ONE grid (the dialog edits the
    SELECTED grid, so the tests that open it need a selection)."""
    service = GridStateService()
    service.ensure("doc1", 800.0, 600.0)
    service.set_active("doc1")
    service.add_grid()
    return service


def _model(service, doc_id):
    return service.selected(doc_id)


def _counter():
    box = [0]
    return box, lambda: box.__setitem__(0, box[0] + 1)


def _pixel_color(pixmap, x, y):
    color = pixmap.toImage().pixelColor(x, y)
    return (color.red(), color.green(), color.blue(), color.alpha())


def _chip_rgb(button):
    pixmap = button.icon().pixmap(button.iconSize())
    return _pixel_color(pixmap, pixmap.width() // 2, pixmap.height() // 2)


def _button(dialog, name):
    # QColorDialog.button() is not wrapped on every binding; the box child is stable.
    box = dialog.findChild(QtWidgets.QDialogButtonBox)
    assert box is not None, "the widget dialog carries a QDialogButtonBox"
    return box.button(_enum(QtWidgets.QDialogButtonBox, "StandardButton.Ok", "Ok")
                      if name == "Ok"
                      else _enum(QtWidgets.QDialogButtonBox, "StandardButton.Cancel", "Cancel"))


def _press_escape(dialog):
    key_press = _enum(QtCore.QEvent, "Type.KeyPress", "KeyPress")
    escape = _enum(QtCore.Qt, "Key.Key_Escape", "Key_Escape")
    no_modifier = _enum(QtCore.Qt, "KeyboardModifier.NoModifier", "NoModifier")
    QtWidgets.QApplication.sendEvent(
        dialog, QtGui.QKeyEvent(key_press, escape, no_modifier)
    )


def _click_color_button(panel, action, via=None):
    """Open the dialog (button click or direct handler call) and run action(dialog) inside the modal loop."""
    outcome = {"dialog": None, "error": None}
    color_button = panel.findChild(QtWidgets.QPushButton, "color")

    def _force_close():
        modal = QtWidgets.QApplication.activeModalWidget()
        if isinstance(modal, QtWidgets.QColorDialog):
            modal.reject()

    def _step():
        try:
            dialogs = panel.findChildren(QtWidgets.QColorDialog)
            if not dialogs:
                outcome["error"] = "no QColorDialog instance child"
                _force_close()
                return
            outcome["dialog"] = dialog = dialogs[0]
            action(dialog)
        except BaseException as exc:  # noqa: BLE001 — surfaced after the click
            outcome["error"] = repr(exc)
            _force_close()

    QtCore.QTimer.singleShot(0, _step)
    QtCore.QTimer.singleShot(10_000, _force_close)
    # A direct handler call lets post-exec exceptions reach the test instead of
    # being swallowed by sip's signal dispatch.
    (via if via is not None else color_button.click)()
    _process_deferred_deletes()
    if outcome["error"] is not None:
        pytest.fail(outcome["error"])
    if outcome["dialog"] is None:
        pytest.fail("the Color button click never opened a dialog")
    return outcome["dialog"]


# ---------------------------------------------------------------- open/seed


def test_click_opens_seeded_dialog_with_alpha_channel():
    service = _service_with_doc()
    panel = _panel(service)
    failures = []

    def _check(dialog):
        try:
            color = dialog.currentColor()
            assert (color.red(), color.green(), color.blue()) == (230, 230, 230)
            assert color.alpha() == 204, "seed must keep the model alpha"
            assert dialog.testOption(
                _enum(QtWidgets.QColorDialog,
                      "ColorDialogOption.ShowAlphaChannel", "ShowAlphaChannel")
            )
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))
        dialog.reject()

    _click_color_button(panel, _check)
    assert failures == []


def test_click_opens_even_without_an_active_model():
    """The handler is the guard, not the widget: with nothing to edit the colour
    button is disabled, but the handler reached directly still opens a dialog
    (seeded from the default style) that simply has nothing to commit to."""
    service = GridStateService()
    panel = _panel(service)
    seen = []

    def _check(dialog):
        seen.append(dialog)
        dialog.reject()

    assert not panel.findChild(QtWidgets.QPushButton, "color").isEnabled()
    _click_color_button(panel, _check, via=panel._on_color_clicked)
    assert seen


# ----------------------------------------------------------------- preview


def test_preview_updates_button_without_touching_model_service_or_store():
    transport = RecordingTransport()
    service = _service_with_doc()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service, settings=JsonGridSettings(transport))
    before_rgba = _model(service, "doc1").style.color_rgba
    button = panel.findChild(QtWidgets.QPushButton, "color")
    before_text = button.text()
    failures = []

    def _check(dialog):
        try:
            dialog.setCurrentColor(QtGui.QColor(0, 255, 0, 255))
            assert button.text() == "#00FF00", button.text()
            assert _chip_rgb(button)[:3] == (0, 255, 0)
            assert _model(service, "doc1").style.color_rgba == before_rgba
            assert box[0] == 0, "preview must not notify the service"
            assert transport.data == {}, "preview must not save settings"
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))
        dialog.reject()

    _click_color_button(panel, _check)
    assert failures == []
    assert transport.data == {} and transport.writes == 0
    assert button.text() == before_text, "cancel restores the pre-dialog hex"
    assert _chip_rgb(button)[:3] != (0, 255, 0), "the cancelled sample is gone"


def test_escape_restores_display_and_mutates_nothing():
    transport = RecordingTransport()
    service = _service_with_doc()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service, settings=JsonGridSettings(transport))
    button = panel.findChild(QtWidgets.QPushButton, "color")
    before_rgba = _model(service, "doc1").style.color_rgba
    failures = []

    def _reject(dialog):
        try:
            dialog.setCurrentColor(QtGui.QColor(10, 20, 200, 255))
            assert button.text() == "#0A14C8"
            _press_escape(dialog)
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))

    _click_color_button(panel, _reject)
    assert failures == []
    assert _model(service, "doc1").style.color_rgba == before_rgba
    assert box[0] == 0
    assert transport.data == {} and transport.writes == 0
    assert button.text() == "#E6E6E6"


# ------------------------------------------------------------------ commit


def test_accept_commits_once_with_alpha_preserved():
    transport = RecordingTransport()
    service = _service_with_doc()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service, settings=JsonGridSettings(transport))
    button = panel.findChild(QtWidgets.QPushButton, "color")

    def _accept(dialog):
        dialog.setCurrentColor(QtGui.QColor(220, 10, 10, 128))
        _button(dialog, "Ok").click()

    _click_color_button(panel, _accept)

    stored = _model(service, "doc1").style.color_rgba
    assert stored[0] == pytest.approx(220 / 255)
    assert stored[1] == pytest.approx(10 / 255)
    assert stored[3] == pytest.approx(128 / 255), f"alpha forced to {stored[3]}"
    assert box[0] == 1, "commit = exactly one service notification"
    assert transport.writes == 1, "commit = exactly one save"
    payload = json.loads(transport.data["composition_grid|active_grid"])
    assert payload["style"]["color_rgba"][3] == pytest.approx(128 / 255)
    assert button.text() == "#DC0A0A"


def test_reopen_seeds_from_the_committed_model_and_leaks_no_dialog():
    service = _service_with_doc()
    panel = _panel(service)
    failures = []

    def _accept(dialog):
        try:
            assert panel.findChild(QtWidgets.QPushButton, "color").text() == "#E6E6E6"
            dialog.setCurrentColor(QtGui.QColor(0, 255, 0, 255))
            _button(dialog, "Ok").click()
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))

    _click_color_button(panel, _accept)
    assert failures == []

    def _check_seed(dialog):
        try:
            color = dialog.currentColor()
            assert (color.red(), color.green(), color.blue(), color.alpha()) == (0, 255, 0, 255)
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))
        dialog.reject()

    _click_color_button(panel, _check_seed)
    assert failures == []
    assert panel.findChildren(QtWidgets.QColorDialog) == []


# ------------------------------------------------------------- ownership rules


def test_no_model_preview_is_inert_and_accept_is_a_noop():
    transport = RecordingTransport()
    service = GridStateService()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service, settings=JsonGridSettings(transport))
    button = panel.findChild(QtWidgets.QPushButton, "color")
    assert button.text() == ""
    failures = []

    def _accept(dialog):
        try:
            dialog.setCurrentColor(QtGui.QColor(0, 255, 0, 255))
            assert button.text() == "", "no-model preview must not repaint"
            assert button.icon().isNull()
            _button(dialog, "Ok").click()
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))

    _click_color_button(panel, _accept, via=panel._on_color_clicked)
    assert failures == []
    assert service.active_doc_id is None
    assert box[0] == 0
    assert transport.data == {}
    assert button.text() == ""


def test_model_recreated_under_the_same_doc_id_rejects_the_obsolete_choice():
    """OK may only write the grid object that was open, never a replacement of
    the same document (removed and added again mid-loop)."""
    transport = RecordingTransport()
    service = _service_with_doc()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service, settings=JsonGridSettings(transport))
    obsolete = _model(service, "doc1")
    state = {}

    def _cycle_and_accept(dialog):
        service.remove_grid(obsolete.grid_id)
        service.add_grid()
        replacement = _model(service, "doc1")
        assert replacement is not obsolete, "the cycle really replaced the grid"
        box[0] = 0  # baseline: only dialog-attributable effects count from here
        state["writes"] = transport.writes
        state["data"] = dict(transport.data)
        dialog.setCurrentColor(QtGui.QColor(200, 0, 200, 255))
        _button(dialog, "Ok").click()

    _click_color_button(panel, _cycle_and_accept)
    # Post-exec comparison: a forbidden save AFTER exec must fail here too.
    assert transport.writes == state["writes"]
    assert transport.data == state["data"]
    # Hand-derived: a fresh grid starts from the default Style color.
    assert _model(service, "doc1").style.color_rgba == pytest.approx((0.9, 0.9, 0.9, 0.8))
    assert box[0] == 0, "an obsolete choice must not notify"
    assert panel.findChild(QtWidgets.QPushButton, "color").text() == "#E6E6E6"


def test_absent_model_at_open_then_ensured_during_loop_is_not_committed():
    """A grid appearing mid-loop is not the opening model; OK must not commit to it."""
    service = GridStateService()
    transport = RecordingTransport()
    box, listener = _counter()
    service.on_change(listener)
    service.set_active("ghost")  # active id, no grid behind it
    panel = _panel(service, settings=JsonGridSettings(transport))
    state = {}
    failures = []

    def _ensure_and_accept(dialog):
        service.ensure("ghost", 800.0, 600.0)
        service.add_grid()
        box[0] = 0  # baseline past the legitimate ensure/add notifies
        state["writes"] = transport.writes
        state["data"] = dict(transport.data)
        try:
            dialog.setCurrentColor(QtGui.QColor(0, 255, 0, 255))
            # The new grid's OWN color is authoritative; the choice never lands.
            assert panel.findChild(QtWidgets.QPushButton, "color").text() == "#E6E6E6"
            _button(dialog, "Ok").click()
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))

    _click_color_button(panel, _ensure_and_accept, via=panel._on_color_clicked)
    assert failures == []
    assert transport.writes == state["writes"]
    assert transport.data == state["data"]
    assert _model(service, "ghost").style.color_rgba == pytest.approx((0.9, 0.9, 0.9, 0.8))
    assert box[0] == 0, "a model that appeared mid-loop must not receive the choice"


@pytest.mark.parametrize("exit_kind", ["cancel", "escape", "ok"])
def test_route_lost_after_preview_clears_the_button(exit_kind):
    """After the opening model vanishes, every exit clears: no phantom, no commit."""
    transport = RecordingTransport()
    service = _service_with_doc()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service, settings=JsonGridSettings(transport))
    button = panel.findChild(QtWidgets.QPushButton, "color")
    original_rgba = _model(service, "doc1").style.color_rgba
    state = {}
    failures = []

    def _lose_and_close(dialog):
        try:
            dialog.setCurrentColor(QtGui.QColor(200, 0, 200, 255))
            assert button.text() == "#C800C8"
            service.remove("doc1")  # legit notify; preview can no longer own the route
            box[0] = 0
            state["writes"] = transport.writes
            state["data"] = dict(transport.data)
            if exit_kind == "cancel":
                _button(dialog, "Cancel").click()
            elif exit_kind == "escape":
                _press_escape(dialog)
            else:
                _button(dialog, "Ok").click()
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))

    _click_color_button(panel, _lose_and_close)
    assert failures == []
    assert transport.writes == state["writes"]
    assert transport.data == state["data"]
    assert box[0] == 0, f"{exit_kind}: no dialog-attributable notify"
    assert button.text() == "", f"{exit_kind}: phantom preview survived"
    assert button.icon().isNull()
    assert _model(service, "doc1") is None
    assert original_rgba is not None


# --------------------------------------------------------- document switch


def test_document_switch_during_modal_loop_does_not_commit_to_the_new_doc():
    service = _service_with_doc()
    service.set_style(color_rgba=(0.0, 1.0, 0.0, 1.0))  # doc1 = green
    service.ensure("doc2", 800.0, 600.0)
    service.set_active("doc2")
    service.add_grid()
    service.set_active("doc1")
    panel = _panel(service)
    doc1_rgba = _model(service, "doc1").style.color_rgba
    doc2_rgba = _model(service, "doc2").style.color_rgba
    button = panel.findChild(QtWidgets.QPushButton, "color")

    def _switch_and_accept(dialog):
        service.set_active("doc2")
        dialog.setCurrentColor(QtGui.QColor(200, 0, 200, 255))
        assert button.text() != "#C800C8", "never preview onto another model's route"
        _button(dialog, "Ok").click()

    _click_color_button(panel, _switch_and_accept)

    assert _model(service, "doc1").style.color_rgba == doc1_rgba
    assert _model(service, "doc2").style.color_rgba == doc2_rgba
    assert button.text() == "#E6E6E6"  # the AUTHORITATIVE current model (doc2)
    assert _chip_rgb(button)[:3] != (200, 0, 200)


# ----------------------------------------------------- mid-exec destruction


def test_panel_destroyed_during_exec_does_not_commit_or_crash():
    """Host deleted mid-exec via real deleteLater+DeferredDelete: no raise, no stale write."""
    transport = RecordingTransport()
    service = _service_with_doc()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service, settings=JsonGridSettings(transport))
    original_rgba = _model(service, "doc1").style.color_rgba
    dead = []
    state = {}

    def _destroy_mid_exec(dialog):
        dialog.setCurrentColor(QtGui.QColor(200, 0, 200, 255))
        dialog.destroyed.connect(lambda *_: dead.append(True))
        box[0] = 0
        state["writes"] = transport.writes
        state["data"] = dict(transport.data)
        panel.deleteLater()
        QtCore.QTimer.singleShot(0, _process_deferred_deletes)  # destroy while exec runs

    _click_color_button(panel, _destroy_mid_exec, via=panel._on_color_clicked)
    assert dead, "the dialog must have really died with its parent"
    assert box[0] == 0, "a dead dialog must not commit"
    assert transport.writes == state["writes"]
    assert transport.data == state["data"]
    assert _model(service, "doc1").style.color_rgba == original_rgba


def test_dialog_destroyed_during_exec_abandons_choice_and_restores_the_panel():
    """The dialog dies but the panel lives: abandon the selection, restore the live display."""
    service = _service_with_doc()
    box, listener = _counter()
    service.on_change(listener)
    panel = _panel(service)
    button = panel.findChild(QtWidgets.QPushButton, "color")
    original_rgba = _model(service, "doc1").style.color_rgba
    dead = []
    failures = []

    def _destroy_dialog_mid_exec(dialog):
        try:
            dialog.setCurrentColor(QtGui.QColor(0, 255, 0, 255))
            assert button.text() == "#00FF00"
            dialog.destroyed.connect(lambda *_: dead.append(True))
            box[0] = 0
            dialog.deleteLater()
            QtCore.QTimer.singleShot(0, _process_deferred_deletes)
        except BaseException as exc:  # noqa: BLE001
            failures.append(repr(exc))

    _click_color_button(panel, _destroy_dialog_mid_exec, via=panel._on_color_clicked)
    assert failures == []
    assert dead, "the dialog must have really died"
    assert box[0] == 0, "a dead dialog must not commit"
    assert _model(service, "doc1").style.color_rgba == original_rgba
    assert button.text() == "#E6E6E6", "the live panel keeps no phantom sample"
    assert panel.findChildren(QtWidgets.QColorDialog) == []


# ---------------------------------------------------------- qt_compat surface


def test_qt_compat_exposes_the_normalized_constants():
    assert qt_compat.QT_AVAILABLE
    assert qt_compat.ShowAlphaChannel == _enum(
        QtWidgets.QColorDialog,
        "ColorDialogOption.ShowAlphaChannel",
        "ShowAlphaChannel",
    )
    assert qt_compat.DialogAccepted == _enum(
        QtWidgets.QDialog, "DialogCode.Accepted", "Accepted"
    )
