"""Stage B/C: the docker GRID LIST — rows, click-to-select-and-edit, the per-row
eye toggle, in-place rename.

Gating and the live-panel teardown follow the sibling docker modules
(``tests.ui.test_param_widgets_qt``, ``tests.ui.test_color_dialog_qt``): a
two-layer PyQt gate (module-level ``skipif`` from a non-raising probe plus the
shared ``try_import_qt`` in an autouse fixture), ``QT_QPA_PLATFORM=offscreen``
before any Qt import, and a panel registry that closes and destroys every panel
so a later test never touches a dead C++ wrapper.

The gestures are REAL Qt gestures where the behaviour is about gesture handling
(``QTest.mouseClick`` on a row or on the eye, ``QTest.mouseDClick`` + a key click
for the rename editor, ``QTest.keyClick`` for F2 and for the arrow keys), which is
the only way to pin the three things the list actually depends on:

* a press on the row TEXT selects that grid AND starts its canvas edit session
  (so the artist can drag it immediately), except on a HIDDEN grid, which is
  only selected — an invisible grid cannot be edited on the canvas;
* a press on the row EYE changes that grid's visibility ONLY — it must not move
  the selection or the current row, because ``select_grid`` ends the previously
  selected grid's canvas edit session;
* a keyboard row change selects without starting a session.

Since stage C the toggle is a painted glyph with a geometry hit test, not a Qt
check state (``itemPressed`` can no longer be attributed by the delegate), so the
EYE ZONE is read from the view's own ``eye_zone(item)`` rather than guessed —
that single source is what keeps the drawn glyph and the clickable area the same
pixels.

Pinned here as well: rows in list order as ``"<label> — <type name>"``; no Qt
check state on any row; a hidden row renders differently from a visible one
(dimmed text, viewport rasterisation); the rename editor seeded with the LABEL
alone; an empty commit keeps the old label and re-renders it; add/remove (from
the buttons or from the service) update the rows, the selection and the enabled
state; the list scrolls instead of growing and keeps its scroll position; one
gesture is one selection (plus at most the one session start it asked for).
"""

from __future__ import annotations

import importlib
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

# The row role the delegate paints from (role == Qt.UserRole + 64) is read
# lazily through the module, so a rename of the constant is a failing test, not
# a silent green — and so this file still COLLECTS in a PyQt-free environment,
# where docker_panel defines no role constants at all.
def _visible_role():
    """The visibility role the panel writes into each row."""
    return importlib.import_module("composition_grid.ui.docker_panel")._VISIBLE_ROLE


def _qt_available() -> bool:
    """Non-raising probe of the binding the panel itself resolves."""
    for major in (6, 5):
        try:
            importlib.import_module(f"PyQt{major}.QtWidgets")
        except ImportError:
            continue
        return True
    return False


def _import_qt_modules():
    for major in (6, 5):
        try:
            return tuple(
                importlib.import_module(f"PyQt{major}.{name}")
                for name in ("QtWidgets", "QtCore", "QtGui", "QtTest")
            )
        except ImportError:
            continue
    return (None, None, None, None)


QtWidgets, QtCore, QtGui, QtTest = _import_qt_modules()
# ``mouseClick``/``qWaitForWindowExposed`` are static members of the QTest CLASS
# in both bindings (the module attribute set differs), so bind the class.
QTest = getattr(QtTest, "QTest", QtTest)

pytestmark = pytest.mark.skipif(
    not _qt_available() or QTest is None, reason="PyQt (with QtTest) not installed"
)

_KEEP_APP_REF = None


def _ensure_qapp():
    """The shared QApplication, kept referenced for the whole session (the
    sibling Qt modules pin this gotcha: a collected wrapper tears the C++ app
    down mid-suite)."""
    global _KEEP_APP_REF
    inst = QtWidgets.QApplication.instance()
    if inst is None:
        _KEEP_APP_REF = QtWidgets.QApplication([])
    elif _KEEP_APP_REF is None:
        _KEEP_APP_REF = inst


def _enum(owner, *dotted):
    """Binding-safe enum access: the Qt6-scoped path first, the Qt5 flat name as
    the fallback (same helper shape as ``tests.ui.test_color_dialog_qt``)."""
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


_LIVE_PANELS: list = []


@pytest.fixture(autouse=True)
def _panel_teardown():
    _LIVE_PANELS.clear()
    yield
    for panel in _LIVE_PANELS:
        panel.close()
        panel.setParent(None)
        panel.deleteLater()
    _process_deferred_deletes()


class RecordingTransport:
    """``SettingsTransport`` double that records every write."""

    def __init__(self):
        self.data = {}
        self.writes = 0

    def read(self, group, key):
        return self.data.get(f"{group}|{key}")

    def write(self, group, key, value):
        self.writes += 1
        self.data[f"{group}|{key}"] = value


# --------------------------------------------------------------- fixtures


def _service_with_grid_ids(count: int = 1) -> GridStateService:
    """A real service: one active 800x600 document with ``count`` grids (ids
    g1..gN, selection on the last one, as ``add_grid`` leaves it)."""
    service = GridStateService()
    service.ensure("doc1", 800.0, 600.0)
    service.set_active("doc1")
    for _ in range(count):
        service.add_grid()
    return service


def _visible_panel(service: GridStateService, **kwargs) -> GridPanel:
    """A panel that is REALLY on screen: the gestures below need laid-out rows
    (``visualItemRect`` is null for an unmapped view, and Qt would deliver the
    click to nothing)."""
    panel = GridPanel(service, **kwargs)
    _LIVE_PANELS.append(panel)
    panel.destroyed.connect(lambda *_: panel in _LIVE_PANELS and _LIVE_PANELS.remove(panel))
    panel.resize(240, 600)
    panel.show()
    QTest.qWaitForWindowExposed(panel)
    QtWidgets.QApplication.processEvents()
    return panel


def _list(panel: GridPanel):
    return panel.findChild(QtWidgets.QListWidget, "grid_list")


def _rows(lst) -> list:
    return [lst.item(index).text() for index in range(lst.count())]


def _ids(lst) -> list:
    return [lst.item(index).data(qt_compat.UserRole) for index in range(lst.count())]


def _eye_states(lst) -> list:
    """What the ROW says about visibility — the role the delegate paints from, so
    this is the eye a user sees, not a model read through the service."""
    role = _visible_role()
    return [bool(lst.item(index).data(role)) for index in range(lst.count())]


def _left_click(lst, point) -> None:
    QTest.mouseClick(
        lst.viewport(),
        _enum(QtCore.Qt, "MouseButton.LeftButton", "LeftButton"),
        _enum(QtCore.Qt, "KeyboardModifier.NoModifier", "NoModifier"),
        point,
    )
    QtWidgets.QApplication.processEvents()


def _click_body(lst, row: int) -> None:
    """A single real click on the row TEXT, clear of the eye toggle."""
    rect = lst.visualItemRect(lst.item(row))
    assert rect.height() > 0, "the list must be laid out to be clickable"
    _left_click(lst, QtCore.QPoint(rect.right() - 40, rect.center().y()))


def _click_eye(lst, row: int) -> None:
    """A single real click in the row's EYE zone, as the view itself computes it."""
    zone = lst.eye_zone(lst.item(row))
    assert zone.width() >= 12, "the toggle keeps a usable hit area"
    _left_click(lst, QtCore.QPoint(zone.center().x(), zone.center().y()))


def _open_rename_editor(lst, row: int):
    """Double-click a row into its rename editor (the first press also selects,
    which is what Qt needs before it accepts the double click)."""
    _click_body(lst, row)
    rect = lst.visualItemRect(lst.item(row))
    QTest.mouseDClick(
        lst.viewport(),
        _enum(QtCore.Qt, "MouseButton.LeftButton", "LeftButton"),
        _enum(QtCore.Qt, "KeyboardModifier.NoModifier", "NoModifier"),
        QtCore.QPoint(rect.right() - 40, rect.center().y()),
    )
    QtWidgets.QApplication.processEvents()
    editor = lst.findChild(QtWidgets.QLineEdit)
    assert editor is not None, "double-clicking a row opens the label editor"
    return editor


def _type_combo(panel):
    return panel.findChild(QtWidgets.QComboBox, "grid_type")


def _viewport_rgb(panel, step: int = 7):
    """The panel rasterised — the honest way to ask what a row LOOKS like when the
    difference is a text colour."""
    image = panel.grab().toImage()
    return [
        image.pixel(x, y)
        for y in range(0, image.height(), step)
        for x in range(0, image.width(), step)
    ]


def _eye_rgb(panel, visible: bool):
    """The pixels of the eye a row would show: the icon the panel resolves, painted
    into a flat image (a provider icon and a fallback glyph are then directly
    comparable)."""
    icon = panel._eye_icon(visible=visible)
    image = QtGui.QImage(24, 24, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor("#ffffff"))
    painter = QtGui.QPainter(image)
    try:
        icon.paint(painter, QtCore.QRect(0, 0, 24, 24))
    finally:
        painter.end()
    return [image.pixel(x, y) for y in range(24) for x in range(24)]


def _theme_icon(rgb):
    """A provider answer the tests can recognise in a rasterisation."""
    icon = QtGui.QIcon()
    pixmap = QtGui.QPixmap(18, 18)
    pixmap.fill(QtGui.QColor(*rgb))
    icon.addPixmap(pixmap)
    return icon


# ------------------------------------------------------------------ rows


def test_the_list_is_at_the_top_with_the_grid_buttons_under_it():
    """Requirement 1: rows first, then Add/Remove, then the controls that edit
    the selected grid (pinned on the layout, not on appearance)."""
    panel = _visible_panel(_service_with_grid_ids(2))
    lst = _list(panel)
    layout = panel.layout()
    names = [layout.itemAt(i).widget().objectName() for i in range(layout.count())]
    assert names[:4] == ["grid_list", "add_grid", "remove_grid", "grid_type"]
    assert lst.minimumHeight() > 0 and lst.maximumHeight() > lst.minimumHeight()


def test_one_row_per_grid_in_list_order_with_label_and_type_name():
    service = _service_with_grid_ids(3)
    service.set_label(service.grids()[1].grid_id, "sky")
    service.set_type("uniform_grid")  # the SELECTED grid is the third one
    panel = _visible_panel(service)
    lst = _list(panel)

    assert _rows(lst) == [
        "1 — Rule of Thirds",
        "sky — Rule of Thirds",
        "3 — Uniform Grid",
    ]
    assert _ids(lst) == [model.grid_id for model in service.grids()]
    assert lst.count() == 3


def test_a_grid_without_a_label_shows_only_its_type_name():
    """A row must never read "— Type" with a dangling dash."""
    service = _service_with_grid_ids(1)
    service.grids()[0].label = ""  # state that predates labels
    panel = _visible_panel(service)

    assert _rows(_list(panel)) == ["Rule of Thirds"]


def test_each_row_shows_that_grids_visibility_and_no_row_has_a_check_state():
    """Stage C replaced the checkbox: the eye state rides on an int role and the
    rows carry NO Qt check state at all (no flag, so the view cannot toggle it)."""
    service = _service_with_grid_ids(3)
    service.set_visible(service.grids()[0].grid_id, False)
    service.set_visible(service.grids()[2].grid_id, False)
    panel = _visible_panel(service)
    lst = _list(panel)

    assert _eye_states(lst) == [False, True, False]
    checkable = _enum(QtCore.Qt, "ItemFlag.ItemIsUserCheckable", "ItemIsUserCheckable")
    unchecked = _enum(QtCore.Qt, "CheckState.Unchecked", "Unchecked")
    for index in range(lst.count()):
        item = lst.item(index)
        assert not (item.flags() & checkable), "no checkbox column any more"
        assert item.checkState() == unchecked


# ---------------------------------------------------- click = edit on canvas


def test_pressing_a_visible_row_selects_it_and_starts_its_edit_session():
    """Stage C requirement 1: no "Edit Grid on Canvas" press needed — the row
    press both selects the grid and makes it live, and the button mirrors it."""
    service = _service_with_grid_ids(2)
    service.set_type("uniform_grid")  # row 2 is a Uniform Grid
    service.select_grid(service.grids()[0].grid_id)
    panel = _visible_panel(service)
    lst = _list(panel)

    _click_body(lst, 1)

    assert service.selected_grid_id() == service.grids()[1].grid_id
    assert service.grids()[1].edit_session is True, "the grid is interactive at once"
    assert service.grids()[0].edit_session is False, "one grid is edited at a time"
    assert panel.findChild(QtWidgets.QPushButton, "edit").isChecked()
    assert _type_combo(panel).currentData() == "uniform_grid"
    assert lst.currentRow() == 1
    assert lst.item(1).isSelected()


def test_pressing_the_already_selected_row_keeps_its_session_on():
    """No toggle-off from the list: pressing the selected row with its
    session on leaves everything as it was."""
    service = _service_with_grid_ids(1)
    service.start_edit_session()
    panel = _visible_panel(service)

    _click_body(_list(panel), 0)

    assert service.selected_grid_id() == service.grids()[0].grid_id
    assert service.grids()[0].edit_session is True


def test_pressing_a_hidden_row_selects_it_without_an_edit_session():
    """The exception: a grid that is not drawn cannot be dragged on the canvas, so
    its row only selects."""
    service = _service_with_grid_ids(2)
    hidden_id = service.grids()[1].grid_id
    service.set_visible(hidden_id, False)
    panel = _visible_panel(service)

    _click_body(_list(panel), 1)

    assert service.selected_grid_id() == hidden_id, "the panel follows the hidden grid"
    assert service.get(hidden_id).edit_session is False


def test_the_edit_button_still_ends_a_session_started_from_a_row():
    """The route OUT of a session is untouched by stage C: the checkable button (and
    the canvas, and the action) still end it."""
    service = _service_with_grid_ids(1)
    panel = _visible_panel(service)
    _click_body(_list(panel), 0)
    button = panel.findChild(QtWidgets.QPushButton, "edit")
    assert button.isChecked()

    button.click()

    assert service.grids()[0].edit_session is False
    assert not button.isChecked()


def test_a_keyboard_row_change_selects_without_starting_a_session():
    """Arrow keys move the selection but never put a grid on the canvas."""
    service = _service_with_grid_ids(3)
    selected_id = service.selected_grid_id()
    panel = _visible_panel(service)
    lst = _list(panel)
    lst.setCurrentRow(0)
    lst.setFocus()
    QtWidgets.QApplication.processEvents()
    target = service.grids()[1].grid_id

    QTest.keyClick(
        lst.viewport(), _enum(QtCore.Qt, "Key.Key_Down", "Key_Down")
    )
    QtWidgets.QApplication.processEvents()

    assert lst.currentRow() == 1
    assert service.selected_grid_id() == target, "the arrow selected the grid"
    assert service.get(target).edit_session is False
    assert service.get(selected_id).edit_session is False


# ---------------------------------------------------------------- the eye


def test_pressing_a_row_eye_hides_that_grid_only_and_keeps_selection_and_session():
    """The reason the eye is a geometry hit test: hiding another grid must neither
    switch the panel nor end the selected grid's canvas edit session."""
    service = _service_with_grid_ids(2)
    other_id = service.grids()[0].grid_id
    selected_id = service.grids()[1].grid_id
    service.start_edit_session()  # the SELECTED grid is being edited on canvas
    panel = _visible_panel(service)
    lst = _list(panel)

    _click_eye(lst, 0)

    assert service.get(other_id).visible is False, "the eye hid that grid"
    assert service.get(selected_id).visible is True, "no other grid changed"
    assert service.selected_grid_id() == selected_id, "the panel still edits row 2"
    assert service.get(selected_id).edit_session is True, "its edit session survived"
    assert lst.currentRow() == 1, "the eye press did not even move the current row"
    assert _eye_states(lst) == [False, True]


def test_pressing_the_eye_of_the_selected_row_toggles_it_without_killing_the_session():
    """Hiding the grid the artist is editing keeps it selected and its session
    where it is; the overlay simply stops drawing it."""
    service = _service_with_grid_ids(1)
    grid_id = service.grids()[0].grid_id
    service.start_edit_session()
    panel = _visible_panel(service)

    _click_eye(_list(panel), 0)

    assert service.get(grid_id).visible is False
    assert service.get(grid_id).edit_session is True
    assert not panel.findChild(QtWidgets.QCheckBox, "visible").isChecked(), (
        "the panel's own Visible checkbox follows the model"
    )


def test_a_hidden_row_is_rendered_differently_from_its_visible_self():
    """The dim is a LOOK (the row stays enabled so it can still be selected and
    renamed), so it is pinned on the rasterisation rather than on a colour value."""
    service = _service_with_grid_ids(1)
    grid_id = service.grids()[0].grid_id
    panel = _visible_panel(service)
    shown = _viewport_rgb(panel)

    service.set_visible(grid_id, False)
    QtWidgets.QApplication.processEvents()
    hidden = _viewport_rgb(panel)

    assert _eye_states(_list(panel)) == [False]
    assert shown != hidden, "hiding a grid changes how its row is drawn"


def test_the_panel_paints_its_own_eye_when_there_is_no_icon_provider():
    """No Krita (tests, headless docker): the fallback glyph must exist and must
    paint — a null icon here would leave the toggle invisible while still eating
    clicks."""
    panel = _visible_panel(_service_with_grid_ids(2))
    for visible in (True, False):
        icon = panel._eye_icon(visible=visible)
        assert icon is not None and not icon.isNull()
        pixmap = icon.pixmap(QtCore.QSize(18, 18))
        assert not pixmap.isNull() and pixmap.hasAlpha()
    assert panel.grab().toImage().height() > 0, "the panel still rasterises"


def test_the_injected_provider_icons_are_used_and_cached_per_state():
    """With a provider (Krita's theme icons in production) its answer is what the
    row gets: the fallback is never painted and each state resolves once."""
    asked = []

    def provider(name):
        asked.append(name)
        return _theme_icon((0, 128, 255) if name == "visible" else (128, 0, 0))

    panel = _visible_panel(_service_with_grid_ids(2), icon_provider=provider)
    visible_icon = panel._eye_icon(visible=True)
    hidden_icon = panel._eye_icon(visible=False)
    panel._eye_icon(visible=True)

    assert asked == ["visible", "novisible"], "one resolve per state, then cached"
    assert visible_icon is panel._eye_icons[True]
    assert hidden_icon is not visible_icon

    plain = _visible_panel(_service_with_grid_ids(2))
    assert _eye_rgb(panel, True) != _eye_rgb(plain, True), "the host icon is what gets drawn"
    assert _eye_rgb(panel, True) != _eye_rgb(panel, False)


def test_a_provider_that_answers_nothing_falls_back_to_the_painted_eye():
    """Krita without the icon, a wrapper that raises, an icon with no pixmap: all
    three degrade to the panel's own glyph without breaking the row."""
    answers = {
        "raising": lambda name: (_ for _ in ()).throw(RuntimeError("no host")),
        "none": lambda name: None,
        "null icon": lambda name: QtGui.QIcon(),
    }
    for kind, provider in answers.items():
        panel = _visible_panel(_service_with_grid_ids(1), icon_provider=provider)
        icon = panel._eye_icon(visible=True)
        assert not icon.isNull(), "%s: the fallback glyph must be shown" % kind


# ------------------------------------------------------------- selection echo


def test_the_selected_row_follows_a_selection_made_elsewhere():
    """Actions, buttons and the canvas all move the selection; the list follows
    without a feedback loop (rebuild writes with the signals blocked)."""
    service = _service_with_grid_ids(3)
    panel = _visible_panel(service)
    lst = _list(panel)
    notifies = []
    service.on_change(lambda: notifies.append(1))

    service.select_grid(service.grids()[0].grid_id)

    assert lst.currentRow() == 0
    assert lst.item(0).isSelected()
    assert notifies == [1], "one selection change is one notification"

    notifies.clear()
    _click_body(lst, 2)
    assert service.selected_grid_id() == service.grids()[2].grid_id
    assert service.grids()[2].edit_session is True
    assert notifies == [1, 1], "select, then the session the press asked for"


# ----------------------------------------------------------------- rename


def test_double_click_renames_the_label_in_place():
    service = _service_with_grid_ids(1)
    grid_id = service.grids()[0].grid_id
    panel = _visible_panel(service)
    lst = _list(panel)

    editor = _open_rename_editor(lst, 0)
    assert editor.text() == "1", "the editor shows the LABEL, not the row text"
    editor.setText("bridge")
    QTest.keyClick(
        editor, _enum(QtCore.Qt, "Key.Key_Return", "Key_Return")
    )
    QtWidgets.QApplication.processEvents()

    assert service.get(grid_id).label == "bridge"
    assert _rows(lst) == ["bridge — Rule of Thirds"]


def test_f2_opens_the_label_editor_for_the_current_row():
    service = _service_with_grid_ids(2)
    service.set_label(service.grids()[1].grid_id, "sky")
    panel = _visible_panel(service)
    lst = _list(panel)
    lst.setCurrentRow(0)
    lst.setFocus()
    QtWidgets.QApplication.processEvents()

    QTest.keyClick(lst.viewport(), _enum(QtCore.Qt, "Key.Key_F2", "Key_F2"))
    QtWidgets.QApplication.processEvents()

    editor = lst.findChild(QtWidgets.QLineEdit)
    assert editor is not None, "F2 starts the rename like a double click"
    assert editor.text() == "1"


def test_an_empty_rename_keeps_the_old_label_and_re_renders_it():
    service = _service_with_grid_ids(1)
    grid_id = service.grids()[0].grid_id
    panel = _visible_panel(service)
    lst = _list(panel)

    editor = _open_rename_editor(lst, 0)
    editor.setText("   ")
    QTest.keyClick(editor, _enum(QtCore.Qt, "Key.Key_Return", "Key_Return"))
    QtWidgets.QApplication.processEvents()

    assert service.get(grid_id).label == "1", "a label cannot be blanked"
    assert _rows(lst) == ["1 — Rule of Thirds"], "the row shows the kept label"


def test_cancelled_rename_writes_nothing():
    """Escape leaves the editor text uncommitted (the model is never written by
    the delegate, so a cancelled edit cannot leak into the row). The notifications
    counted are the ones taken AFTER the row press that opened the editor."""
    service = _service_with_grid_ids(1)
    grid_id = service.grids()[0].grid_id
    notifies = []
    service.on_change(lambda: notifies.append(1))
    panel = _visible_panel(service)
    lst = _list(panel)

    editor = _open_rename_editor(lst, 0)
    notifies.clear()  # the press that opened the editor may have selected/started
    editor.setText("nope")
    QTest.keyClick(editor, qt_compat.Key_Escape)
    QtWidgets.QApplication.processEvents()

    assert service.get(grid_id).label == "1"
    assert _rows(lst) == ["1 — Rule of Thirds"]
    assert notifies == [], "a cancelled edit mutates nothing"


def test_a_rename_persists_through_the_injected_store():
    transport = RecordingTransport()
    service = _service_with_grid_ids(1)
    panel = _visible_panel(service, settings=JsonGridSettings(transport))

    editor = _open_rename_editor(_list(panel), 0)
    editor.setText("horizon")
    QTest.keyClick(editor, _enum(QtCore.Qt, "Key.Key_Return", "Key_Return"))
    QtWidgets.QApplication.processEvents()

    payload = json.loads(transport.data["composition_grid|active_grid"])
    assert payload["label"] == "horizon"


# ----------------------------------------------------------- add / remove


def test_add_button_appends_a_row_and_selects_it():
    service = _service_with_grid_ids(1)
    panel = _visible_panel(service)
    lst = _list(panel)

    panel.findChild(QtWidgets.QPushButton, "add_grid").click()

    assert _rows(lst) == ["1 — Rule of Thirds", "2 — Rule of Thirds"]
    assert service.grids()[1].grid_id == service.selected_grid_id()
    assert lst.currentRow() == 1
    assert service.grids()[1].edit_session is False, (
        "the BUTTON only adds; a session starts when the artist presses the row"
    )


def test_remove_button_deletes_the_row_and_moves_the_selection():
    service = _service_with_grid_ids(3)
    panel = _visible_panel(service)
    lst = _list(panel)

    panel.findChild(QtWidgets.QPushButton, "remove_grid").click()

    assert _rows(lst) == ["1 — Rule of Thirds", "2 — Rule of Thirds"]
    assert lst.currentRow() == 1, "the neighbour of the removed grid is selected"


def test_removing_through_the_service_while_the_panel_is_open_updates_the_list():
    """Any other entry point (the Remove action, another document's edit) is
    answered by the same notification the list rebuilds from."""
    service = _service_with_grid_ids(3)
    doomed = service.grids()[0].grid_id
    selected = service.grids()[2].grid_id
    panel = _visible_panel(service)
    lst = _list(panel)

    service.remove_grid(doomed)

    assert _ids(lst) == [service.grids()[0].grid_id, service.grids()[1].grid_id]
    assert _rows(lst) == ["2 — Rule of Thirds", "3 — Rule of Thirds"]
    assert service.selected_grid_id() == selected, "removing another grid keeps it"
    assert lst.currentRow() == 1


def test_removing_the_last_grid_empties_the_list_and_inerts_the_controls():
    service = _service_with_grid_ids(1)
    panel = _visible_panel(service)
    lst = _list(panel)

    panel.findChild(QtWidgets.QPushButton, "remove_grid").click()

    assert _rows(lst) == []
    assert lst.currentRow() == -1
    assert not panel.findChild(QtWidgets.QPushButton, "remove_grid").isEnabled()
    assert not _type_combo(panel).isEnabled()
    assert panel.findChild(QtWidgets.QPushButton, "add_grid").isEnabled()


def test_a_document_that_has_never_had_a_grid_shows_an_empty_list():
    """``ensure`` creates an EMPTY container: nothing to select, so the list is
    empty, Remove is inert and every grid control is too."""
    service = GridStateService()
    service.ensure("doc1", 800.0, 600.0)
    service.set_active("doc1")
    panel = _visible_panel(service)

    assert _list(panel).count() == 0
    assert not panel.findChild(QtWidgets.QPushButton, "remove_grid").isEnabled()
    assert not _type_combo(panel).isEnabled()
    assert not panel.findChild(QtWidgets.QCheckBox, "visible").isEnabled()


def test_switching_documents_replaces_the_rows():
    service = _service_with_grid_ids(2)
    service.set_label(service.grids()[1].grid_id, "sky")
    service.ensure("doc2", 800.0, 600.0)
    panel = _visible_panel(service)
    lst = _list(panel)

    service.set_active("doc2")
    assert _rows(lst) == [], "the new document has no grid yet"
    service.add_grid()
    assert _rows(lst) == ["1 — Rule of Thirds"]
    service.set_active("doc1")
    assert _rows(lst) == ["1 — Rule of Thirds", "sky — Rule of Thirds"]


# ------------------------------------------------------------- the scroll


def test_many_grids_scroll_instead_of_growing_the_panel():
    service = _service_with_grid_ids(12)
    panel = _visible_panel(service)
    lst = _list(panel)

    assert lst.count() == 12
    assert lst.height() <= lst.maximumHeight()
    assert lst.verticalScrollBar().maximum() > 0, "the rows overflow the fixed height"


def test_a_rebuild_keeps_the_scroll_position():
    service = _service_with_grid_ids(12)
    panel = _visible_panel(service)
    lst = _list(panel)
    bar = lst.verticalScrollBar()
    bar.setValue(bar.maximum())
    QtWidgets.QApplication.processEvents()
    scrolled = bar.value()
    assert scrolled > 0, "the list was scrolled to the bottom"

    service.set_visible(service.grids()[0].grid_id, False)

    assert bar.value() == scrolled, "a rebuild never jumps the list"
