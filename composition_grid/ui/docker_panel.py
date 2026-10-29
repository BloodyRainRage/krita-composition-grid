"""The docker content panel — grid type, params, style, transform.

Qt module — Qt comes ONLY through :mod:`composition_grid.qt_compat` (the
single legal PyQt route); krita is never referenced here. That is a design
decision, not an accident: Krita's dockers must subclass ``krita.DockWidget``
*at class-definition time*, but the libkis class only exists inside a running
Krita. The concrete ``PanelDocker`` (the base-injected wrapper) and the shared
service singleton therefore live in :mod:`composition_grid.ui.docker_registration`
— this module is the panel widget and nothing else.

Responsibilities (docker scope, v1):

* Type combo: all registry types by display name, current model selected; a
  change routes ``GridStateService.set_type`` and the dynamic params section
  REBUILDS from the new schema via
  :func:`composition_grid.ui.param_widgets.build_param_widgets`.
* Style section: color button (opens a live-previewing modal ``QColorDialog``
  on click: the chosen sample shows on the button while the dialog is open and
  commits through the dialog-free seam :meth:`GridPanel.apply_color` only on OK
  while the opening model still owns the route; every other exit restores the
  authoritative swatch — the ``Style`` <-> ``QColor`` conversions and the
  pinned ``DASH_PATTERNS`` live in
  :mod:`composition_grid.ui.style_controls`), width ``QDoubleSpinBox``
  0.25..12 step 0.05 (``Style`` also clamps), opacity ``QSlider`` 0..100 mapped
  ``/100``, dash combo from the presets.
* Transform section: "Reset Transform" button (``reset_transform`` with the
  CURRENT doc size from the injected provider — without a provider Reset is a
  logged no-op), a numeric rotation field (a ``QDoubleSpinBox`` in degrees:
  (-180, 180], step 1, decimals 2) and a
  checkable "Lock Ratio" checkbox.
* Edit-session button (checkable "Edit Grid on Canvas",
  ``start/end_edit_session``) and the "Visible" checkbox of the SELECTED grid.
  Pressing a row in the grid list starts that grid's session too (see below), so
  the button mostly serves to END one; it always mirrors ``model.edit_session``.
* Two grid commands (``add_grid``/``remove_grid``): a document has NO grid
  until the artist adds one, so "Add grid" is always live, while "Remove grid"
  is enabled only while a grid is selected. Removing DELETES that grid (there is
  no "removed but remembered" state); every other grid control follows the
  selection and is inert when the document has none (see
  :meth:`GridPanel._grid_controls`).
* The grid LIST at the top of the panel (see :meth:`GridPanel._rebuild_grid_list`):
  one row per grid of the active document, in list order, reading
  ``"<label> — <type name>"`` with a compact EYE toggle at the left showing that
  grid's visibility (Krita's own ``visible``/``novisible`` theme icons through the
  injected ``icon_provider``, painted :class:`_GridListView` fallback glyphs when
  there is none). Pressing the eye calls ``set_visible`` for that grid and
  NOTHING else — no selection, no edit session; pressing anywhere else on the
  row selects that grid (``select_grid``), makes it live on the canvas
  (``start_edit_session``) when it is visible and starts no session when it is
  hidden. Arrow keys select without starting a session. A double click (or F2)
  renames the label in place (``set_label``, through :class:`_GridLabelDelegate`).
* EVERY mutation is service-owned: the service is the sole
  mutation+notify owner. The ratio-lock flag writes through
  :meth:`GridStateService.set_ratio_lock` and the rotation through
  :meth:`GridStateService.set_rotation` (which normalizes the angle with the
  SAME :func:`core.transform_state.normalize_angle_degrees` the canvas rotate
  handle uses). The panel never touches ``model.transform`` itself, so the
  persistence subscriber sees every flag/angle change.
* Echo protection (standard Qt): EVERY mutation goes through the
  service; the service notifies :meth:`GridPanel._refresh`, which writes the
  authoritative values back into the widgets inside a ``blockSignals`` pass —
  programmatic refresh never re-enters the handlers (asserted: one user gesture
  maps to exactly one service mutation). Controls are found again by their
  ``objectName`` (``param_widgets`` sets ``setObjectName(param.name)``).
* Widget/model desync guard: the visibility and ratio-lock handlers early-return
  when there is no selected grid and REVERT the checkbox to its pre-click state,
  so a stray click without a document never leaves a flipped control.
* Persistence (save last settings): with an optional injected
  :class:`~composition_grid.model.settings_store.JsonGridSettings`, a second
  ``on_change`` subscriber saves the SELECTED grid after every mutation (cheap
  write, no debounce; nothing is saved while no grid is selected;
  transport failures are logged, never raised into Qt). That stored copy is the
  "last used" seed of the NEXT grid :meth:`GridStateService.add_grid` builds.
* ``handle_canvas_changed(canvas)`` forwards the libkis ``canvasChanged``
  payload to the injected single-arg callback (the plugin entry wires the
  ``CanvasContext``); the ``canvasChanged`` virtual itself is on the
  ``PanelDocker`` wrapper in ``docker_registration``. No Krita API is called
  here.

All UI strings English.
"""

from __future__ import annotations

import logging
from typing import Callable

from composition_grid import qt_compat
from composition_grid.core.grids.grid_registry import all_types, get
from composition_grid.core.styles import Style
from composition_grid.model.grid_model import GridModel, GridStateService
from composition_grid.model.settings_store import JsonGridSettings
from composition_grid.ui.param_widgets import build_param_widgets
from composition_grid.ui.style_controls import (
    DASH_PATTERNS,
    color_swatch_icon,
    qcolor_to_rgba,
    rgba_to_qcolor,
)

logger = logging.getLogger("composition_grid")

__all__ = ["GridPanel"]


if qt_compat.QT_AVAILABLE:

    # A row is roughly 24 px: three rows minimum (the docker is narrow), eight
    # before the list itself starts scrolling instead of growing.
    _LIST_MIN_HEIGHT_PX = 72
    _LIST_MAX_HEIGHT_PX = 192

    _EYE_ICON_PX = 18
    """Side of the row's eye square: "compact" (Krita's layer docker is 16-18 px
    at the default font), and the eye is the ONLY thing in the row's left margin,
    so one number defines both the glyph and its hit zone."""

    _EYE_HIT_PAD_PX = 3
    """Extra clickable margin around the eye square — a 18 px target plus 3 px on
    each side is ~24 px, which is what makes the toggle usable without widening
    the glyph."""

    _EYE_TEXT_GAP_PX = 4
    """Gap between the eye and the row text, so the two hit zones cannot overlap."""

    _EYE_SLASH_COLOR = "#e05c5c"

    _HIDDEN_TEXT_DIM = 0.45
    """How far a hidden grid's row text is blended towards the base
    (window/alternate-base) colour: <1 keeps it readable, >0 keeps it clearly
    "off" — the same cue Krita's layer docker gives, applied to the text because
    the row stays enabled (it must remain clickable and renamable)."""

    _VISIBLE_ROLE = qt_compat.UserRole + 64
    """The row's visibility, read by the delegate when it paints. Deliberately
    NOT a Qt check state: an int role is written by the rebuild without emitting
    ``itemChanged``, so the "no Qt check state any more" rule of the eye toggle is
    structurally true and not just signal-blocked."""

    def _eye_rect(row_rect, left_inset: int):
        """The eye square of a row, in the coordinate system ``row_rect`` lives
        in. The delegate passes ``option.rect`` (viewport coordinates), the view
        passes the visual row rect shifted by its frame width — the SAME function
        on both sides is what keeps the drawn glyph and the hit zone in the same
        pixels (measured: the two rects differ only by ``frameWidth``, which is 0
        on the offscreen/Fusion list style and 1-2 px on others)."""
        QtCore = qt_compat.QtCore
        side = _EYE_ICON_PX
        top = row_rect.top() + max(0, (row_rect.height() - side) // 2)
        return QtCore.QRect(row_rect.left() + left_inset, top, side, side)

    def _eye_zone(row_rect, left_inset: int):
        """The CLICKABLE eye area: :func:`_eye_rect` grown by
        :data:`_EYE_HIT_PAD_PX`, clamped so it stops before the text starts."""
        eye = _eye_rect(row_rect, left_inset)
        QtCore = qt_compat.QtCore
        limit = row_rect.left() + left_inset + _EYE_ICON_PX + _EYE_TEXT_GAP_PX
        zone = QtCore.QRect(
            eye.left() - _EYE_HIT_PAD_PX,
            eye.top() - _EYE_HIT_PAD_PX,
            eye.width() + 2 * _EYE_HIT_PAD_PX,
            eye.height() + 2 * _EYE_HIT_PAD_PX,
        )
        if zone.right() >= limit:
            zone.setRight(limit - 1)
        return zone

    def _left_inset(view) -> int:
        """Rows in viewport coordinates start at the view's frame width; the item
        delegates report ``option.rect`` from 0 (the viewport IS the coordinate
        system there)."""
        return int(view.frameWidth())

    def _text_left_offset(view) -> int:
        """Where a row's text starts (delegate coordinates): after the eye and
        its gap, so the label never runs under the glyph."""
        return _left_inset(view) + _EYE_ICON_PX + _EYE_TEXT_GAP_PX

    _QStyle = qt_compat.QtWidgets.QStyle
    # PyQt5 exposes the member on QStyle, PyQt6 only on the scoped enum.
    _PE_ITEM_PANEL = getattr(
        _QStyle,
        "PE_PanelItemViewItem",
        getattr(getattr(_QStyle, "PrimitiveElement", _QStyle), "PE_PanelItemViewItem", None),
    )

    def _dimmed_on(base, blend, background):
        """``base`` blended ``blend`` of the way towards ``background`` (module
        docstring: the dim of a hidden row). Straight per-channel linear mix —
        no colour management, no clipping (both endpoints are in range)."""
        QtGui = qt_compat.QtGui
        return QtGui.QColor(
            int(base.red() * (1.0 - blend) + background.red() * blend),
            int(base.green() * (1.0 - blend) + background.green() * blend),
            int(base.blue() * (1.0 - blend) + background.blue() * blend),
            base.alpha(),
        )

    def _paint_fallback_eye(painter, rect, visible: bool, ink, paper) -> None:
        """The no-Krita eye glyph: an almond outline with a filled pupil. Hidden =
        the same almond dimmed and struck through, which reads as "off" without
        needing a second asset.

        Everything is scaled from the (fixed) icon side and drawn with integer
        coordinates only, so the glyph stays well proportioned and no float
        QPainter overload is relied upon. ``ink``/``paper`` come from the caller
        (the panel's palette), never from the painter — this runs once per icon,
        not once per paint."""
        QtGui = qt_compat.QtGui
        QtCore = qt_compat.QtCore
        side = rect.width()
        if side <= 0:
            return
        line = QtGui.QColor(ink)
        if not visible:
            line = _dimmed_on(line, _HIDDEN_TEXT_DIM, QtGui.QColor(paper))
        pen_width = max(1, side // 9)
        outline = QtGui.QPen(line, pen_width)
        outline.setJoinStyle(qt_compat.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(outline)
        painter.setBrush(qt_compat.Qt.BrushStyle.NoBrush)
        painter.drawPolygon(
            QtGui.QPolygon(
                [
                    QtCore.QPoint(rect.left(), rect.center().y()),
                    QtCore.QPoint(rect.left() + side // 4, rect.top() + pen_width),
                    QtCore.QPoint(rect.right() - side // 4, rect.top() + pen_width),
                    QtCore.QPoint(rect.right(), rect.center().y()),
                    QtCore.QPoint(rect.right() - side // 4, rect.bottom() - pen_width),
                    QtCore.QPoint(rect.left() + side // 4, rect.bottom() - pen_width),
                ]
            )
        )
        pupil = max(2, side // 3)
        painter.setPen(qt_compat.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(ink) if visible else QtGui.QColor(paper))
        painter.drawEllipse(
            QtCore.QPoint(rect.center().x() - pupil // 2, rect.center().y() - pupil // 2),
            pupil,
            pupil,
        )
        if not visible:
            slash = QtGui.QPen(QtGui.QColor(_EYE_SLASH_COLOR), pen_width)
            painter.setPen(slash)
            painter.drawLine(rect.topLeft(), rect.bottomRight())

    def _fallback_eye_pixmap(visible: bool, ink, paper):
        """The fallback eye as a ``QPixmap``: :func:`_paint_fallback_eye` needs a
        device and a font metrics need a ``QApplication``, so both are created
        here and a headless environment (or a paint that refuses to start) simply
        answers a NULL pixmap — the row still renders, the hit zone is still
        computed from geometry, nothing raises."""
        QtGui = qt_compat.QtGui
        QtCore = qt_compat.QtCore
        if qt_compat.QtWidgets.QApplication.instance() is None:
            return QtGui.QPixmap()
        pixmap = QtGui.QPixmap(_EYE_ICON_PX, _EYE_ICON_PX)
        pixmap.fill(QtGui.QColor(0, 0, 0, 0))
        painter = QtGui.QPainter(pixmap)
        try:
            _paint_fallback_eye(
                painter,
                QtCore.QRect(0, 0, _EYE_ICON_PX, _EYE_ICON_PX),
                visible,
                ink,
                paper,
            )
        finally:
            painter.end()
        return pixmap

    class _GridLabelDelegate(qt_compat.QtWidgets.QStyledItemDelegate):
        """In-place rename of a grid-list row (module docstring).

        The row DISPLAYS ``"<label> — <type name>"`` while the editor shows and
        commits ONLY the label, so the suffix can never be typed into or eaten by
        a rename. The commit is routed to the panel (-> ``service.set_label``) and
        deliberately writes nothing into the list model: the service notification
        re-renders the row from the authoritative label, which is also why an
        empty text leaves the old label visible instead of a blanked row.
        """

        def __init__(self, panel: "GridPanel", parent=None) -> None:
            super().__init__(parent)
            self._panel = panel

        def createEditor(self, parent, option, index):  # noqa: N802 — Qt naming
            return qt_compat.QtWidgets.QLineEdit(parent)

        def setEditorData(self, editor, index) -> None:  # noqa: N802 — Qt naming
            editor.setText(self._panel._label_at(index.row()))

        def setModelData(self, editor, model, index) -> None:  # noqa: N802 — Qt naming
            self._panel._commit_label(self._panel._grid_id_at(index.row()), editor.text())

        def sizeHint(self, option, index):  # noqa: N802 — Qt naming
            """Tall enough for the eye square to sit in the row with a pixel of
            breathing room above and below, and at least the normal text height."""
            hint = super().sizeHint(option, index)
            QtCore = qt_compat.QtCore
            needed = QtCore.QSize(
                hint.width() + _text_left_offset(self._panel._grid_list), _EYE_ICON_PX + 6
            )
            return QtCore.QSize(max(hint.width(), needed.width()), max(hint.height(), needed.height()))

        def paint(self, painter, option, index) -> None:  # noqa: N802 — Qt naming
            """The stock row with two changes: a hidden grid's text is dimmed and
            the eye occupies the left margin the check indicator used to.

            The eye is painted HERE rather than set as the item's decoration icon:
            only the painter decides where it lands, so the hit zone
            :class:`_GridListView` resolves is computed from the very same
            :func:`_eye_rect` instead of guessing the style's decoration rect
            (there is one column, so only that one gets the glyph)."""
            if index.column() != 0:
                super().paint(painter, option, index)
                return
            hidden = not bool(index.data(_VISIBLE_ROLE))
            if hidden:
                palette = option.palette
                window = palette.window().color()
                for role in (qt_compat.P_Text, qt_compat.P_WindowText):
                    palette.setColor(
                        role, _dimmed_on(palette.color(role), _HIDDEN_TEXT_DIM, window)
                    )
                option.palette = palette
            # Without a check indicator nothing reserves the eye's margin: paint
            # the row panel (selection/hover) across the FULL row, then the text
            # in a rect that starts after the eye, so the label never runs under it.
            widget = option.widget
            style = widget.style() if widget is not None else qt_compat.QtWidgets.QApplication.style()
            style.drawPrimitive(_PE_ITEM_PANEL, option, painter, widget)
            text_option = qt_compat.QtWidgets.QStyleOptionViewItem(option)
            text_option.rect = option.rect.adjusted(_text_left_offset(self._panel._grid_list), 0, 0, 0)
            super().paint(painter, text_option, index)
            self._panel._eye_icon(visible=not hidden).paint(
                painter,
                _eye_rect(option.rect, _left_inset(self._panel._grid_list)),
                qt_compat.AlignCenter,
            )

    class _GridListView(qt_compat.QtWidgets.QListWidget):
        """The grid list with the eye column handled by GEOMETRY (module docstring
        of :class:`GridPanel`).

        There is no Qt check state any more, so a press cannot be attributed to
        the toggle by the item delegate the way a checkbox was: this view answers
        the press itself — inside the eye zone it flips that grid and lets the
        event go no further (no selection, no current-row change, hence no edit
        session on the grid being hidden), everywhere else it forwards to Qt,
        whose own selection is then completed by ``GridPanel._on_list_pressed``.

        ``owner`` is the panel (pure DI, no back-reference cycle: the panel owns
        the view and the view only reads). ``_press_in_progress`` marks the window
        of an inherited press handling, so the panel can tell a mouse-driven
        current-row change from a keyboard one (a real gesture, not a heuristic
        about the event queue)."""

        def __init__(self, owner: "GridPanel", parent=None) -> None:
            super().__init__(parent)
            self._owner = owner
            self._press_in_progress = False

        def eye_zone(self, item):
            """The clickable eye area of a row, in viewport coordinates — the
            public form of the hit test, so a test (or a later tooltip) can ask
            where the toggle is without repainting the glyph itself."""
            return _eye_zone(self.visualItemRect(item), _left_inset(self))

        def _eye_item_at(self, event):
            """The item whose eye zone contains a left press, else ``None``."""
            if event.button() != qt_compat.LeftButton:
                return None
            position = event.pos()
            item = self.itemAt(position)
            if item is None:
                return None
            return item if self.eye_zone(item).contains(position) else None

        def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt naming
            item = self._eye_item_at(event)
            if item is not None:
                self._owner._on_eye_pressed(str(item.data(qt_compat.UserRole) or ""))
                event.accept()
                return
            self._press_in_progress = True
            try:
                super().mousePressEvent(event)
            finally:
                self._press_in_progress = False

    class GridPanel(qt_compat.QtWidgets.QWidget):
        """The docker content widget (plain QWidget — the DockWidget base is
        added around it by ``docker_registration.make_panel_docker_class``).

        Constructor contract (pure DI, same style as ``GridOverlay``):
        ``service`` (required), ``doc_size_provider`` (zero-arg
        ``() -> (w, h) | None`` feeding Reset), optional ``settings``
        (``JsonGridSettings``; injected store triggers a save on every
        change), optional ``on_canvas_changed`` (single-arg callback the
        ``handle_canvas_changed`` forwarding targets; the plugin entry wires the
        context) and optional ``icon_provider`` (``(name: str) -> QIcon | None``
        for the grid list's eye; the plugin injects Krita's theme icons and the
        panel paints its own glyphs when it answers ``None``).
        """

        def __init__(
            self,
            service: GridStateService,
            doc_size_provider: Callable[[], "tuple[float, float] | None"] | None = None,
            settings: JsonGridSettings | None = None,
            on_canvas_changed: Callable[[object], None] | None = None,
            on_route: Callable[[], object] | None = None,
            icon_provider: Callable[[str], object] | None = None,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._service = service
            self._doc_size_provider = doc_size_provider
            self._settings = settings
            self._on_canvas_changed = on_canvas_changed
            self._on_route = on_route
            self._icon_provider = icon_provider
            self._eye_icons: dict[bool, object] = {}
            self._params_type_id: str | None = None  # schema currently shown

            widgets = qt_compat.QtWidgets
            root = widgets.QVBoxLayout(self)

            # The document's grids, top of the panel: the artist picks WHICH grid
            # to edit here, everything below edits the one that is selected.
            self._grid_list = _GridListView(self)
            self._grid_list.setObjectName("grid_list")
            self._grid_list.setMinimumHeight(_LIST_MIN_HEIGHT_PX)
            self._grid_list.setMaximumHeight(_LIST_MAX_HEIGHT_PX)
            self._grid_list.setSelectionMode(qt_compat.SingleSelection)
            # Double-click or F2 only: a plain keypress must not start a rename.
            self._grid_list.setEditTriggers(
                qt_compat.DoubleClicked | qt_compat.EditKeyPressed
            )
            self._grid_list.setItemDelegate(_GridLabelDelegate(self, self._grid_list))
            self._grid_list.itemPressed.connect(self._on_list_pressed)
            self._grid_list.currentRowChanged.connect(self._on_current_row_changed)
            root.addWidget(self._grid_list)

            # The two grid commands sit directly under the list they operate on
            # and are NOT grid controls: "Add grid" is always live (it is how a
            # document gets its first grid) and "Remove grid" follows the
            # selection (see :meth:`_refresh`).
            self._add_button = widgets.QPushButton("Add grid")
            self._add_button.setObjectName("add_grid")
            self._add_button.clicked.connect(self._on_add_clicked)
            root.addWidget(self._add_button)

            self._remove_button = widgets.QPushButton("Remove grid")
            self._remove_button.setObjectName("remove_grid")
            self._remove_button.clicked.connect(self._on_remove_clicked)
            root.addWidget(self._remove_button)

            self._type_combo = widgets.QComboBox()
            self._type_combo.setObjectName("grid_type")
            for cls in all_types():  # registry order; id rides as itemData
                self._type_combo.addItem(cls.name, cls.id)
            root.addWidget(self._type_combo)

            params_box = widgets.QGroupBox("Parameters")
            self._params_layout = widgets.QVBoxLayout(params_box)
            self._params_container = None
            root.addWidget(params_box)

            style_box = widgets.QGroupBox("Style")
            style_form = widgets.QFormLayout(style_box)
            self._color_button = widgets.QPushButton()
            self._color_button.setObjectName("color")
            self._color_button.clicked.connect(self._on_color_clicked)
            style_form.addRow("Color:", self._color_button)

            self._width_spin = widgets.QDoubleSpinBox()
            self._width_spin.setObjectName("width_px")
            self._width_spin.setDecimals(2)
            self._width_spin.setRange(0.25, 12.0)  # Style.__post_init__ also clamps
            self._width_spin.setSingleStep(0.05)
            self._width_spin.valueChanged.connect(self._on_width_changed)
            style_form.addRow("Width (px):", self._width_spin)

            self._opacity_slider = widgets.QSlider(
                qt_compat.Qt.Orientation.Horizontal
            )
            self._opacity_slider.setObjectName("opacity")
            self._opacity_slider.setRange(0, 100)  # /100 -> Style.opacity
            self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
            style_form.addRow("Opacity:", self._opacity_slider)

            self._dash_combo = widgets.QComboBox()
            self._dash_combo.setObjectName("dash")
            self._dash_combo.addItems(list(DASH_PATTERNS))
            self._dash_combo.currentIndexChanged.connect(self._on_dash_changed)
            style_form.addRow("Dash:", self._dash_combo)
            root.addWidget(style_box)

            transform_box = widgets.QGroupBox("Transform")
            transform_form = widgets.QFormLayout(transform_box)
            self._rotation_spin = widgets.QDoubleSpinBox()
            self._rotation_spin.setObjectName("rotation_deg")
            self._rotation_spin.setDecimals(2)
            # The numeric angle field: the service wraps -180 -> +180
            # (core.transform_state.normalize_angle_degrees), matching the
            # (-180, 180] range the canvas rotate handle uses.
            self._rotation_spin.setRange(-180.0, 180.0)
            self._rotation_spin.setSingleStep(1.0)
            self._rotation_spin.valueChanged.connect(self._on_rotation_changed)
            transform_form.addRow("Rotation (deg):", self._rotation_spin)
            root.addWidget(transform_box)

            self._visible_check = widgets.QCheckBox("Visible")
            self._visible_check.setObjectName("visible")
            self._visible_check.clicked.connect(self._on_visibility_clicked)
            root.addWidget(self._visible_check)

            self._edit_button = widgets.QPushButton("Edit Grid on Canvas")
            self._edit_button.setObjectName("edit")
            self._edit_button.setCheckable(True)
            self._edit_button.clicked.connect(self._on_edit_clicked)
            root.addWidget(self._edit_button)

            self._reset_button = widgets.QPushButton("Reset Transform")
            self._reset_button.setObjectName("reset")
            self._reset_button.clicked.connect(self._on_reset_clicked)
            root.addWidget(self._reset_button)

            self._ratio_check = widgets.QCheckBox("Lock Ratio")
            self._ratio_check.setObjectName("ratio_lock")
            self._ratio_check.clicked.connect(self._on_ratio_lock_clicked)
            root.addWidget(self._ratio_check)

            # Connect the type combo LAST: building the initial state must
            # not route a spurious set_type through the handler.
            self._type_combo.currentIndexChanged.connect(self._on_type_changed)

            # Subscribe after the widgets exist: the first notify must find
            # a complete panel. on_change has no unsubscribe — subscribe ONCE.
            service.on_change(self._refresh)
            if settings is not None:
                service.on_change(self._save_settings)
            self._refresh()

        # ------------------------------------------------------ public API

        @property
        def service(self) -> GridStateService:
            """The injected state service (read-only; the docker tests and
            the plugin entry address the panel through this)."""
            return self._service

        def handle_canvas_changed(self, canvas) -> None:
            """Forward the libkis ``canvasChanged`` payload to the injected
            provider callback (module docstring); no-callback is a DEBUG
            note, never an error."""
            if self._on_canvas_changed is None:
                logger.debug("docker: canvasChanged received but no provider wired")
                return
            self._on_canvas_changed(canvas)

        def apply_color(self, color) -> None:
            """Dialog-free color seam (module docstring): a ``QColor`` ->
            ``service.set_style(color_rgba=...)``. The production click
            handler feeds it the accepted dialog result."""
            self._service.set_style(color_rgba=qcolor_to_rgba(color))

        # --------------------------------------------- widget -> service

        def showEvent(self, event) -> None:  # noqa: N802 — Qt naming
            """Ask the plugin to route the CURRENT document when the panel appears.

            Krita calls the extension's ``createActions`` while the app is still
            starting up, when ``activeView()`` can be ``None``; a document that
            already existed then never triggers ``activeViewChanged`` again, so
            without this the panel would stay display-only for the whole session.
            Event-driven (a show is an event), not polling — and idempotent:
            routing an already-routed document changes nothing."""
            super().showEvent(event)
            self._request_route()

        def _request_route(self) -> None:
            """One guarded call of the injected route seam; never raises outward."""
            if self._on_route is None:
                return
            try:
                self._on_route()
            except Exception:  # noqa: BLE001 — the panel must not break on a host probe
                logger.exception("docker: document re-route request failed")

        def _routed_model(self) -> GridModel | None:
            """The active model, asking for a route once when it is missing.

            Only USER interactions use this (a refresh pass must not route:
            routing mutates the service, which would re-enter ``_refresh``)."""
            model = self._active_model()
            if model is None and self._on_route is not None:
                self._request_route()
                model = self._active_model()
            return model

        def _active_model(self) -> GridModel | None:
            """The document's SELECTED grid — what every control here edits."""
            doc_id = self._service.active_doc_id
            return None if doc_id is None else self._service.selected(doc_id)

        # ------------------------------------------------------ grid list

        def _grid_id_at(self, row: int) -> "str | None":
            """The id of the grid a row shows, or ``None`` for a stale row (the
            list and the service can differ for one event loop pass only)."""
            grids = self._service.grids()
            return None if not 0 <= row < len(grids) else grids[row].grid_id

        def _label_at(self, row: int) -> str:
            """The label a rename editor starts from — never the displayed
            ``"<label> — type>"`` text (delegate docstring)."""
            grids = self._service.grids()
            return "" if not 0 <= row < len(grids) else grids[row].label

        def _on_list_pressed(self, item) -> None:
            """Pressing a row makes that grid live on the canvas (module
            docstring): it selects the grid (``select_grid``, which is what the
            whole panel below then shows) and, when that grid is VISIBLE, starts
            its edit session, so the artist can move and resize it without
            touching the "Edit Grid on Canvas" button.

            ``itemPressed``, not ``itemClicked``: the view resolves a press on the
            eye toggle itself (:class:`_GridListView`) and never emits this signal
            for it — hiding another grid neither switches the panel nor ends the
            selected grid's canvas edit session.

            Pressing the already selected row keeps its session on (``select_grid``
            answers ``False`` and only ``start_edit_session`` runs, which is the
            idempotent "on" the button shows); the ways OUT of a session stay the
            canvas (a press past the markers), the button and the action.
            """
            grid_id = item.data(qt_compat.UserRole)
            if not grid_id:
                return
            grid_id = str(grid_id)
            self._service.select_grid(grid_id)
            model = self._service.get(grid_id)
            if model is not None and model.visible:
                self._service.start_edit_session()

        def _on_current_row_changed(self, row: int) -> None:
            """Keyboard navigation selects too — WITHOUT starting an edit session:
            arrowing through a list must not put grids on the canvas, and an
            invisible grid can never be edited on it.

            A press-driven change is skipped because
            :meth:`_on_list_pressed` has already done the richer select-and-edit
            for it (Qt moves the current index before emitting either signal, so
            the guard suppresses a no-op, not a second route). The rebuild that
            mirrors an externally made selection blocks the view's signals, so
            this never echoes back into the service."""
            if self._grid_list._press_in_progress:
                return
            grid_id = self._grid_id_at(row)
            if grid_id:
                self._service.select_grid(str(grid_id))

        def _on_eye_pressed(self, grid_id: str) -> None:
            """The row's eye: ``set_visible`` for THAT grid and nothing else — the
            selection, the current row and the edit session are untouched, which is
            why the toggle is a hit test rather than a check state. A row the
            service no longer knows (a stale item) is a logged no-op."""
            if not grid_id:
                logger.debug("docker: eye pressed on a stale row")
                return
            model = self._service.get(grid_id)
            if model is None:
                logger.debug("docker: eye pressed on unknown grid %r", grid_id)
                return
            self._service.set_visible(grid_id, not model.visible)

        def _eye_icon(self, visible: bool):
            """The eye of a row: the host's theme icon when the injected provider
            answers one, else the panel's own glyph — both cached, because the
            fallback costs a ``QPixmap`` + ``QPainter`` and a rebuild runs on every
            service notification.

            A provider that raises, answers ``None`` or answers an icon QIcon itself
            calls null is indistinguishable from "no Krita here": the painted glyph
            takes over, so a docker always shows an eye. The cache is keyed by
            visibility only (the provider cannot change within a panel).
            ``isNull`` is looked up defensively because a host double may answer any
            object at all; an unknown shape is treated as "no icon"."""
            cached = self._eye_icons.get(visible)
            if cached is not None:
                return cached
            icon = None
            if self._icon_provider is not None:
                try:
                    icon = self._icon_provider("visible" if visible else "novisible")
                except Exception:  # noqa: BLE001 — a host lookup must not break a row
                    logger.exception("docker: grid-list icon provider failed")
                    icon = None
            QtGui = qt_compat.QtGui
            if icon is None or getattr(icon, "isNull", lambda: True)():
                pixmap = _fallback_eye_pixmap(
                    visible,
                    self.palette().color(qt_compat.P_Text),
                    self.palette().window().color(),
                )
                icon = QtGui.QIcon(pixmap) if not pixmap.isNull() else QtGui.QIcon()
            self._eye_icons[visible] = icon
            return icon

        def _commit_label(self, grid_id: "str | None", text: str) -> None:
            """Rename seam the delegate commits through: an empty or unchanged
            text is the service's ``False`` no-op (it keeps the old label), and a
            changed one notifies — which re-renders the row either way."""
            if not grid_id:
                return
            self._service.set_label(str(grid_id), "" if text is None else str(text))

        def _on_type_changed(self, _index: int) -> None:
            grid_id = self._type_combo.currentData()
            if self._routed_model() is None or grid_id is None:
                return  # no document yet: the combo is display-only
            self._service.set_type(str(grid_id))  # notify -> _refresh rebuilds

        def _on_param_changed(self, name: str, value: object) -> None:
            try:
                self._service.set_param(name, value)
            except ValueError as exc:  # stale control race: log, never raise
                logger.warning("docker: set_param(%r) rejected: %s", name, exc)

        def _on_width_changed(self, value: float) -> None:
            self._service.set_style(width_px=float(value))

        def _on_opacity_changed(self, value: int) -> None:
            self._service.set_style(opacity=int(value) / 100.0)

        def _on_dash_changed(self, _index: int) -> None:
            label = self._dash_combo.currentText()
            self._service.set_style(dash=DASH_PATTERNS.get(label))

        def _on_visibility_clicked(self) -> None:
            model = self._routed_model()
            if model is None:
                # No document: revert the checkbox so it never lies about state.
                self._visible_check.setChecked(not self._visible_check.isChecked())
                logger.debug("docker: visibility ignored — no active model")
                return
            want = self._visible_check.isChecked()
            if want != model.visible:
                self._service.toggle_visible()  # notify -> _refresh re-syncs

        def _on_edit_clicked(self, checked: bool) -> None:
            self._routed_model()  # route on demand; the session needs a document
            if checked:
                self._service.start_edit_session()
            else:
                self._service.end_edit_session()

        def _on_add_clicked(self) -> None:
            """Give the document a new grid (module docstring): the seed, the
            numbering, the selection and the end of any other grid's edit
            session are all the service's rules, and its notification is what
            re-enables and re-fills every control here.

            The route is asked for first, exactly like the canvas actions: the
            panel may be shown before the plugin ever routed a document, and
            adding onto nothing would silently do nothing."""
            self._request_route()
            if self._service.add_grid() is None:
                logger.debug("docker: add ignored — no active document")

        def _on_remove_clicked(self) -> None:
            """Delete the SELECTED grid; the selection moves to its neighbour
            (or nowhere) and one notification updates everything. Without a
            selection there is nothing to remove and no grid to invent."""
            self._request_route()
            if self._service.selected() is None:
                logger.debug("docker: remove ignored — no selected grid")
                return
            self._service.remove_grid()

        def _on_reset_clicked(self) -> None:
            if self._doc_size_provider is None:
                logger.debug("docker: Reset skipped — no doc-size provider wired")
                return
            size = self._doc_size_provider()
            if size is None:
                logger.debug("docker: Reset skipped — no document size known")
                return
            self._service.reset_transform(float(size[0]), float(size[1]))

        def _on_ratio_lock_clicked(self, checked: bool) -> None:
            """Service-owned: the flag rides through
            ``set_ratio_lock`` so the notify (and persistence) fires."""
            if self._routed_model() is None:
                # No document: revert the checkbox so it never lies about state.
                self._ratio_check.setChecked(not checked)
                logger.debug("docker: ratio lock ignored — no active model")
                return
            self._service.set_ratio_lock(checked)

        def _on_rotation_changed(self, value: float) -> None:
            """Numeric angle field: the absolute degrees route
            through ``set_rotation``, which normalizes and notifies (and thus
            persists) the change."""
            self._service.set_rotation(float(value))

        def _on_color_clicked(self) -> None:
            """Live-preview the choice; OK commits once, every other exit restores (module docstring)."""
            widgets = qt_compat.QtWidgets
            opening_model = self._routed_model()
            rgba = opening_model.style.color_rgba if opening_model else Style().color_rgba
            dialog = widgets.QColorDialog(self)
            dialog.setWindowTitle("Grid Color")
            dialog.setOption(qt_compat.ShowAlphaChannel, True)
            # Option before the seed: the seed path strips alpha while the channel is hidden.
            dialog.setCurrentColor(rgba_to_qcolor(rgba, qt_compat.QtGui))
            # Real QObject.destroyed liveness: the host can delete panel or dialog mid-exec.
            dialog_alive = [True]
            panel_alive = [True]

            def _dialog_died(*_args) -> None:
                dialog_alive[0] = False

            def _panel_died(*_args) -> None:
                panel_alive[0] = False

            def _preview(color) -> None:
                # Never show a sample the opening model's route would not store.
                if opening_model is None or self._active_model() is not opening_model:
                    self._restore_color_display()
                else:
                    self._show_color_on_button(qcolor_to_rgba(color))

            dialog.destroyed.connect(_dialog_died)
            self.destroyed.connect(_panel_died)
            dialog.currentColorChanged.connect(_preview)
            try:
                accepted = (
                    (getattr(dialog, "exec", None) or dialog.exec_)()
                    == qt_compat.DialogAccepted
                )
            finally:
                chosen = dialog.currentColor() if dialog_alive[0] else None
                if dialog_alive[0]:
                    dialog.destroyed.disconnect(_dialog_died)
                    dialog.currentColorChanged.disconnect(_preview)
                    dialog.setParent(None)
                    dialog.deleteLater()
                if panel_alive[0]:
                    self.destroyed.disconnect(_panel_died)
            if (
                accepted
                and dialog_alive[0]
                and panel_alive[0]
                and opening_model is not None
                and self._active_model() is opening_model
            ):
                self.apply_color(chosen)
            if panel_alive[0]:
                self._restore_color_display()

        def _restore_color_display(self) -> None:
            """Re-show the CURRENT authoritative color; a route with no model clears the button."""
            model = self._active_model()
            if model is None:
                self._color_button.setText("")
                self._color_button.setIcon(qt_compat.QtGui.QIcon())
                return
            self._show_color_on_button(model.style.color_rgba)

        def _show_color_on_button(self, color_rgba) -> None:
            """The single HEX + checkerboard-chip display path for refresh and preview."""
            self._color_button.setText(
                "#%02X%02X%02X"
                % tuple(round(component * 255) for component in color_rgba[:3])
            )
            # Preview chip next to the hex text: an artist checks a COLOR by
            # looking at it, not by reading six digits.
            self._color_button.setIcon(
                color_swatch_icon(qt_compat.QtGui, qt_compat.QtCore, color_rgba)
            )

        # --------------------------------------------- service -> widgets

        def _controls(self):
            """Every value-carrying control of the panel (fixed + current
            param controls, found by the objectNames ``param_widgets`` and
            this module set)."""
            widgets = qt_compat.QtWidgets
            yield from (
                self._type_combo,
                self._width_spin,
                self._opacity_slider,
                self._dash_combo,
                self._rotation_spin,
                self._visible_check,
                self._edit_button,
                self._ratio_check,
            )
            if self._params_container is not None:
                for cls in (
                    widgets.QSpinBox,
                    widgets.QDoubleSpinBox,
                    widgets.QCheckBox,
                    widgets.QComboBox,
                ):
                    yield from self._params_container.findChildren(cls)

        def _grid_controls(self):
            """Every control that describes the GRID itself (the value controls
            plus color and Reset): all of them are inert while the document has
            no selected grid — there is nothing to configure until the artist
            adds one."""
            yield from self._controls()
            yield self._color_button
            yield self._reset_button

        def _set_grid_controls_enabled(self, enabled: bool) -> None:
            """The one enabled-state pass of :meth:`_refresh`: the grid controls
            and "Remove grid" follow the selection, "Add grid" never does."""
            for control in self._grid_controls():
                control.setEnabled(enabled)
            self._remove_button.setEnabled(enabled)

        def _row_text(self, model: GridModel) -> str:
            """Row text: ``"<label> — <type name>"``; a grid that arrived with no
            label (state that predates labels) shows its type name alone rather
            than a row starting with a dangling dash."""
            try:
                name = get(model.grid_type_id).name
            except KeyError as exc:  # display-only fallback, logged like the params path
                logger.warning("docker: unknown grid type %r: %s", model.grid_type_id, exc)
                name = model.grid_type_id
            return f"{model.label} — {name}" if model.label else name

        def _rebuild_grid_list(self) -> None:
            """Sync the rows with the document's grids — text, eye state, id and
            the selected row — cheaply and without a feedback loop.

            Rows are REUSED, not destroyed: the item the user is pressing or
            renaming stays alive through the notification this rebuild answers to
            (a ``clear()`` would delete it under Qt's own event handling). The
            scroll offset is restored around the pass, so adding or hiding a grid
            never jumps the list. Signals are blocked for the whole pass — the
            same echo protection as the value controls — and the visibility rides
            on an int role rather than a Qt check state, so writing the
            authoritative state back has no signal to echo in the first place."""
            widgets = qt_compat.QtWidgets
            grids = self._service.grids()
            selected_id = self._service.selected_grid_id()
            scroll = self._grid_list.verticalScrollBar().value()
            self._grid_list.blockSignals(True)
            try:
                while self._grid_list.count() > len(grids):
                    self._grid_list.takeItem(self._grid_list.count() - 1)
                while self._grid_list.count() < len(grids):
                    self._grid_list.addItem(widgets.QListWidgetItem())
                row = None
                for index, model in enumerate(grids):
                    item = self._grid_list.item(index)
                    item.setText(self._row_text(model))
                    item.setData(qt_compat.UserRole, model.grid_id)
                    item.setData(_VISIBLE_ROLE, bool(model.visible))
                    item.setFlags(
                        qt_compat.ItemIsEnabled
                        | qt_compat.ItemIsSelectable
                        | qt_compat.ItemIsEditable
                    )
                    if model.grid_id == selected_id:
                        row = index
                if row is None:
                    self._grid_list.clearSelection()
                    self._grid_list.setCurrentItem(None)
                else:
                    self._grid_list.setCurrentRow(row)
                self._grid_list.verticalScrollBar().setValue(scroll)
            finally:
                self._grid_list.blockSignals(False)

        def _refresh(self) -> None:
            """Authoritative write-back of the selected model into ALL controls
            inside a ``blockSignals`` pass (echo protection, module
            docstring). Without a selected grid nothing is written (the widgets
            keep showing their build-time defaults) and every grid control
            including "Remove grid" goes inert — a debug note, not an error. The
            list is rebuilt either way: a document with no grids has an empty
            list, which is what makes "Add grid" the only live command."""
            model = self._active_model()
            self._rebuild_grid_list()
            if model is None:
                logger.debug("docker: refresh — no selected grid")
                self._set_grid_controls_enabled(False)
                return
            for control in self._controls():
                control.blockSignals(True)
            try:
                index = self._type_combo.findData(model.grid_type_id)
                if index >= 0:
                    self._type_combo.setCurrentIndex(index)
                self._width_spin.setValue(model.style.width_px)
                self._opacity_slider.setValue(round(model.style.opacity * 100))
                dash_index = self._dash_combo.findText(self._dash_label(model.style))
                self._dash_combo.setCurrentIndex(max(dash_index, 0))
                self._rotation_spin.setValue(model.transform.rotation_deg)
                self._visible_check.setChecked(model.visible)
                self._edit_button.setChecked(model.edit_session)
                self._ratio_check.setChecked(model.transform.ratio_locked)
                self._show_color_on_button(model.style.color_rgba)
                self._refresh_params(model)
                # Read AFTER the param rebuild so a freshly built section is
                # covered too (``_grid_controls`` walks the container lazily).
                self._set_grid_controls_enabled(True)
            finally:
                for control in self._controls():
                    control.blockSignals(False)

        @staticmethod
        def _dash_label(style: Style) -> str:
            """Combo label for the stored dash pattern; a custom pattern
            outside the presets displays as "Solid" (v1 UI can only produce
            the presets, so this is a display fallback, logged loudly
            enough to notice). ``Style.__post_init__`` normalizes ``dash`` to
            a tuple, so a JSON-restored list matches the tuple presets here."""
            for label, pattern in DASH_PATTERNS.items():
                if pattern == style.dash:
                    return label
            logger.warning("docker: custom dash pattern %r not in presets", style.dash)
            return "Solid"

        def _refresh_params(self, model: GridModel) -> None:
            """Rebuild the params section when the type changed, else sync
            the existing controls with the stored (coerced) values."""
            widgets = qt_compat.QtWidgets
            try:
                cls = get(model.grid_type_id)
            except KeyError as exc:
                logger.warning("docker: unknown grid type %r: %s", model.grid_type_id, exc)
                return
            if self._params_type_id != cls.id or self._params_container is None:
                self._rebuild_params(cls, model.params)
                return
            for param in cls.params:
                value = model.params.get(param.name, param.default)
                control = self._params_container.findChild(widgets.QWidget, param.name)
                if control is None:  # pragma: no cover — rebuild keeps parity
                    logger.warning("docker: no control for param %r", param.name)
                    continue
                if isinstance(control, widgets.QDoubleSpinBox):
                    control.setValue(float(value))
                elif isinstance(control, widgets.QSpinBox):
                    control.setValue(int(value))
                elif isinstance(control, widgets.QCheckBox):
                    control.setChecked(bool(value))
                elif isinstance(control, widgets.QComboBox):
                    index = control.findText(str(value))
                    if index >= 0:
                        control.setCurrentIndex(index)

        def _rebuild_params(self, cls, params: "dict[str, object]") -> None:
            """Swap the params container for a fresh build_param_widgets()
            result (module docstring: dynamic section on type change).

            The NEW container is built FIRST and the old one only detached on
            success: if ``build_param_widgets`` raises (an unknown
            ``Param.kind``), the old controls stay attached and usable rather
            than leaving ``_params_container`` pointing at a ``deleteLater``'d
            widget for every later refresh to run against."""
            try:
                new_container = build_param_widgets(
                    cls.params, params, self._on_param_changed
                )
            except Exception:  # noqa: BLE001 — a bad schema must not kill the panel
                logger.exception(
                    "docker: param rebuild failed for %r; keeping previous controls",
                    getattr(cls, "id", cls),
                )
                return
            widgets = qt_compat.QtWidgets
            if self._params_container is not None:
                self._params_layout.removeWidget(self._params_container)
                self._params_container.setParent(None)
                self._params_container.deleteLater()
            self._params_container = new_container
            self._params_layout.addWidget(self._params_container)
            self._params_type_id = cls.id

        # ------------------------------------------------------ persistence

        def _save_settings(self) -> None:
            """Second on_change subscriber (module docstring): save the
            SELECTED grid through the injected store; only when one exists."""
            model = self._active_model()
            if model is None or self._settings is None:
                return
            try:
                self._settings.save(model)
            except Exception:  # noqa: BLE001 — a failing transport must not kill the UI
                logger.exception("docker: settings save failed")

else:  # pragma: no cover — pure (no PyQt) environment

    class GridPanel:  # type: ignore[no-redef]
        """Import-safe stub without PyQt: construction raises ``RuntimeError``
        (no QWidget base class exists to subclass) — the sibling-Qt-module
        stub convention (``canvas.overlay``)."""

        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError(
                "composition_grid.ui.docker_panel.GridPanel requires PyQt, "
                "but composition_grid.qt_compat reports QT_AVAILABLE=False"
            )
