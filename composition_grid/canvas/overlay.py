"""Transparent grid overlay widget parented to the canvas widget.

Qt module — ALL Qt naming goes through :mod:`composition_grid.qt_compat`
(the single legal PyQt import route); no ``krita`` import anywhere (the
model/view/canvas objects arrive duck-typed via injected providers). When
PyQt is absent the module still imports: ``GridOverlay`` is then a stub
whose construction raises ``RuntimeError`` (a QWidget subclass cannot be
declared without a Qt base class — the stub keeps the import contract of
the sibling Qt modules honest instead of crashing at import).

Responsibility split: this widget only RENDERS and
ROUTES MOUSE INPUT; all geometry math lives in the pure modules and all
view-state computation in ``canvas_context``. Conventions:

* Pure-data plumbing (same DI style as ``TransformController``): the
  overlay owns no state besides hover; the current ``GridModel``s and the
  ``ViewState`` come from injected zero-arg providers that may answer
  ``None``/empty (no active document / no grids / no active view) — every
  entry point then degrades to painting nothing / ignoring input, never an
  exception. TWO model providers, because a document now holds several grids:
  ``grids_provider`` answers the active document's grids in LIST ORDER and is
  what :meth:`paint_grid` iterates, ``model_provider`` answers the SELECTED
  grid and is what the handles, the hover state and the drag controller act
  on (one grid is edited at a time).
* Presence: a document with NO grids paints NOTHING and stays mouse
  transparent — there is no "removed but remembered" state any more, removing
  a grid deletes it, so the only gate left is ``visible`` per grid plus the
  edit session of the selected one.
* Input transparency: ``WA_TransparentForMouseEvents`` is set
  from ``model.edit_session`` in :meth:`refresh` — True outside a session
  (clicks reach Krita untouched), False inside. A press that hits a handle
  starts a controller drag and is ACCEPTED; a press with no hit ENDS the
  session (through the injected ``end_edit_session`` hook — normally
  ``GridStateService.end_edit_session``) and is left NOT accepted, per
  Qt's documented routing letting the same press continue toward the
  canvas widget: a click past the markers completes the session
  and the event continues into Krita.
* Documented limitation — MANUAL-CHECK item, not automated: the Qt tests
  invoke the mouse handlers directly with synthetic ``QMouseEvent``s, so
  they prove only the HANDLER contract (accept on a handle hit, ignore +
  end session on a miss, never consume outside a session). That an ignored
  press actually propagates from this child up to the parent canvas widget,
  and that Qt's implicit mouse grab keeps delivering mid-drag moves to the
  overlay after the pointer leaves it, are verified exclusively on the
  manual acceptance check in Krita.
* Paint path: ``paintEvent`` delegates to the public ``paint_grid(qp)``
  seam so headless tests drive the exact production code. Grid geometry is
  rebuilt per paint per grid from the registry type (``cls().build(bounds,
  params)`` with ``bounds`` = the element rect in doc coords per
  ``element_matrix``); primitives go through ``paint_primitives``
  (widget-px pen width, doc-rect clip) with THAT grid's own style, handles
  through ``paint_handles`` with the same vp-params logic as
  ``TransformController._positions`` (perspective type -> ``vp_count=1``,
  params ``vp_x``/``vp_y``). A failed geometry build is logged and paints
  nothing — never a crash inside Krita's paint.
* Render hints (the overlay IS the painter caller ``overlay_painter``
  defers to): ``paint_grid`` enables antialiasing. Reason (demonstrated by
  ``test_grid_lines_appear_for_simple_model``): the widget-px pen width is
  divided by the geometric-mean element scale ``s_e``, so on anisotropic
  elements the device width along the short axis drops below 1 px (e.g.
  ``60 / 77.5 * 1 px = 0.77 px``); with AA off the raster engine discards
  such sub-coverage lines entirely and the horizontals vanish. AA spreads
  them over the neighbouring pixel row/column instead of losing them.
* Mouse tolerance: ``MOUSE_TOLERANCE_PX`` widget px fed to
  ``handle_layout.hit_test`` (the clamp for tiny elements applies there).
* Hover/cursor: ``cursor_shape`` names map onto the qt_compat cursor scalars
  it exports; no keyboard hooks are installed.
* Parent sync: the constructor syncs ``setGeometry(parent.
  rect())``, installs itself as event filter on the parent and connects the
  parent's ``destroyed`` signal (the Qt6-safe replacement for the removed
  ``QEvent.Destroy`` type — ``qt_compat.Destroy`` is ``None`` there, the
  filter guards it). The filter only ever returns False (never consumes).
"""

from __future__ import annotations

import logging
from typing import Callable

from composition_grid import qt_compat
from composition_grid.canvas.doc_view_mapper import ViewState, doc_to_widget_affine, view_scale
from composition_grid.core.geometry import Bounds, Point
from composition_grid.core.grids.grid_registry import get
from composition_grid.core.transform_state import element_matrix
from composition_grid.model.grid_model import GridModel
from composition_grid.presentation.handle_layout import (
    DEFAULT_VP_X,
    DEFAULT_VP_Y,
    HandleId,
    cursor_shape,
    handle_positions,
    hit_test,
)
from composition_grid.presentation.handle_painter import paint_handles
from composition_grid.presentation.overlay_painter import paint_primitives
from composition_grid.ui.transform_controller import TransformController

logger = logging.getLogger("composition_grid")

__all__ = ["GridOverlay", "MOUSE_TOLERANCE_PX"]

MOUSE_TOLERANCE_PX = 8.0
"""Widget-px grab radius fed to ``hit_test`` for press/hover resolution."""

# ``cursor_shape`` name -> qt_compat cursor scalar (built lazily: the names
# only exist once PyQt is importable). None until first use.
_CURSOR_BY_SHAPE: "dict[str, object] | None" = None


def _cursor_for_shape(shape: str):
    """Map a cursor NAME (``handle_layout.cursor_shape``) onto the matching
    qt_compat scalar; unknown names fall back to the arrow (same rule as
    ``cursor_shape`` itself)."""
    global _CURSOR_BY_SHAPE
    if _CURSOR_BY_SHAPE is None:
        _CURSOR_BY_SHAPE = {
            "cross": qt_compat.CrossCursor,
            "size_all": qt_compat.SizeAllCursor,
            "size_hor": qt_compat.SizeHorCursor,
            "size_ver": qt_compat.SizeVerCursor,
            "size_bdiag": qt_compat.SizeBDiagCursor,
            "size_fdiag": qt_compat.SizeFDiagCursor,
            "pointing_hand": qt_compat.PointingHandCursor,
            "arrow": qt_compat.ArrowCursor,
        }
    return _CURSOR_BY_SHAPE.get(shape, qt_compat.ArrowCursor)


def _enum_member(scope: str, name: str):
    """Qt enum member, scoped (PyQt6) path first, flat (Qt5) fallback —
    the same resolution convention ``qt_compat._pick`` uses; kept local
    because qt_compat only freezes widget constants, not modifiers."""
    base = qt_compat.Qt
    scoped = getattr(base, scope, None)
    member = getattr(scoped, name, None) if scoped is not None else None
    if member is None:
        member = getattr(base, name)
    return member


def _mouse_point(event) -> Point:
    """Widget-local fractional mouse point (Qt6 ``position()``, Qt5 ``localPos()``)."""
    getter = getattr(event, "position", None)
    if getter is None:
        getter = event.localPos  # Qt5 binding
    point = getter()
    return (float(point.x()), float(point.y()))


def _aa_hint():
    """``QPainter`` antialiasing render hint, scoped (PyQt6) path first,
    flat (Qt5) fallback — same local enum-shim convention as
    :func:`_enum_member`."""
    painter = qt_compat.QtGui.QPainter
    scoped = getattr(painter, "RenderHint", None)
    hint = getattr(scoped, "Antialiasing", None) if scoped is not None else None
    if hint is None:
        hint = painter.Antialiasing
    return hint


if qt_compat.QT_AVAILABLE:

    class GridOverlay(qt_compat.QtWidgets.QWidget):
        """Transparent child of the canvas widget that paints every grid of
        the document and (inside an edit session only) routes the selected
        grid's handle dragging.

        Constructor contract (all state injected, see module docstring):
        ``parent`` (canvas widget or ``None`` for headless painting),
        ``model_provider``/``view_state_provider`` (zero-arg callables that
        may answer ``None``), ``grids_provider`` (zero-arg callable answering
        the active document's grids in list order; without it the single
        ``model_provider`` grid is what gets painted), optional ``controller``
        (a default ``TransformController`` is built from the providers) and
        optional ``end_edit_session`` callable (normally
        ``GridStateService.end_edit_session``).
        """

        def __init__(
            self,
            parent,
            model_provider: Callable[[], GridModel | None],
            view_state_provider: Callable[[], ViewState | None],
            controller: TransformController | None = None,
            end_edit_session: Callable[[], None] | None = None,
            grids_provider: Callable[[], "tuple[GridModel, ...]"] | None = None,
        ) -> None:
            super().__init__(parent)
            self._model_provider = model_provider
            self._grids_provider = grids_provider
            self._view_state_provider = view_state_provider
            self._controller = controller or TransformController(
                view_state_provider, model_provider
            )
            self._end_edit_session = end_edit_session
            self._hover: HandleId | None = None
            self._dragging = False

            # A child widget with no system background leaves the canvas
            # visible under everything we do not explicitly paint.
            self.setAttribute(qt_compat.WA_NoSystemBackground, True)
            self.setAttribute(qt_compat.WA_TransparentForMouseEvents, True)
            self.setMouseTracking(True)  # hover without a held button

            if parent is not None:
                self.sync_geometry()
                parent.installEventFilter(self)
                # The live host's other symptom:
                # ``overlay.isVisible=False`` with the widget attached, the
                # plausibility gate passed and a healthy model). Qt shows the
                # children that EXIST when a parent is shown; a child created
                # under an ALREADY-visible parent is not shown by itself. Krita's
                # canvas viewport is long since visible when the plugin attaches,
                # so without this explicit ``show()`` the overlay never receives
                # a paintEvent and nothing is painted however correct the view
                # state is. It is a no-op while the parent is hidden (Qt keeps the
                # widget off-screen until the parent shows), so the degradation
                # paths lose nothing.
                self.show()
                # Qt6-safe teardown hook (qt_compat.Destroy is None there):
                # the destroyed() signal is the documented replacement.
                destroyed = getattr(parent, "destroyed", None)
                if destroyed is not None:
                    destroyed.connect(self._on_parent_destroyed)

        # ------------------------------------------------------ public API

        def request_paint(self) -> None:
            """Ask Qt for a repaint of the whole overlay (no timers)."""
            self.update()

        def refresh(self) -> None:
            """Re-read the model and sync input transparency + repaint.

            Called by ``CanvasContext`` on every ``GridStateService``
            change; outside a session the stale hover/drag state is also
            cleared defensively (a session that ended underneath us)."""
            model = self._model_provider()
            editing = bool(model is not None and model.edit_session)
            self.setAttribute(qt_compat.WA_TransparentForMouseEvents, not editing)
            if not editing:
                self._hover = None
                self._dragging = False
                self._controller.end_drag()
                self.unsetCursor()
            self.request_paint()

        def sync_geometry(self) -> None:
            """Resize the overlay onto the full canvas-widget rect."""
            parent = self.parentWidget()
            if parent is not None:
                self.setGeometry(parent.rect())

        def dispose(self) -> None:
            """Detach from the canvas widget and drop out of the tree
            (called by ``CanvasContext.unbind``/``rebind``)."""
            parent = self.parentWidget()
            if parent is not None:
                try:
                    parent.removeEventFilter(self)
                    destroyed = getattr(parent, "destroyed", None)
                    if destroyed is not None:
                        destroyed.disconnect(self._on_parent_destroyed)
                except (RuntimeError, TypeError) as exc:
                    logger.debug("overlay: parent detach skipped: %r", exc)
            self.hide()
            self.setParent(None)
            self.deleteLater()

        def paint_grid(self, qp) -> None:
            """Paint EVERY visible grid of the document (list order, each with
            its own style) plus the SELECTED grid's edit-session handles onto
            ANY QPainter through the widget-space matrices.

            Public seam used by ``paintEvent`` and by the headless Qt tests;
            paints NOTHING without view state or without a visible grid, and
            never raises outward."""
            models = self._paintable_grids()
            view = self._view_state_provider()
            if not models or view is None:
                return
            selected = self._model_provider()
            qp.save()
            try:
                qp.setRenderHint(_aa_hint(), True)  # module docstring: AA rule
                for model in models:
                    paint_primitives(
                        qp,
                        self._build_geometry(model),
                        element_matrix(model.transform),
                        doc_to_widget_affine(view),
                        view_scale(view),
                        model.style,
                        doc_size=(view.doc_width, view.doc_height),
                    )
                if selected is not None and selected.edit_session:
                    paint_handles(
                        qp, self._positions(selected, view), self._hover, True
                    )
            except Exception:  # noqa: BLE001 — never crash Krita from a paint
                logger.exception("overlay: grid paint failed")
            finally:
                qp.restore()

        def _paintable_grids(self) -> "tuple[GridModel, ...]":
            """The document's visible grids in list order: the injected
            container when there is one, else the single selected grid (the
            provider shape a caller with one grid still passes)."""
            if self._grids_provider is not None:
                models = self._grids_provider() or ()
            else:
                model = self._model_provider()
                models = () if model is None else (model,)
            return tuple(model for model in models if model is not None and model.visible)

        # ------------------------------------------------------- internals

        def _on_parent_destroyed(self, *args) -> None:
            """Canvas widget swapped out from under us:
            hide and forget — touching our own C++ object may already be
            illegal mid-teardown, so any failure is logged, never raised."""
            try:
                self.hide()
            except RuntimeError as exc:
                logger.debug("overlay: hide on parent destroy skipped: %r", exc)

        def _build_geometry(self, model: GridModel):
            """Registry geometry for the current element: bounds = the
            element rect in doc coords (matches what ``element_matrix``
            maps the normalized unit square onto), params from the model."""
            try:
                cls = get(model.grid_type_id)
            except KeyError as exc:
                logger.warning("overlay: unknown grid type %r: %s", model.grid_type_id, exc)
                return ()
            ts = model.transform
            bounds = Bounds(ts.cx - ts.width / 2.0, ts.cy - ts.height / 2.0, ts.width, ts.height)
            try:
                return cls().build(bounds, model.params)
            except Exception as exc:  # noqa: BLE001 — degradation, not crash
                logger.warning(
                    "overlay: geometry build failed for %r: %r", model.grid_type_id, exc
                )
                return ()

        def _positions(
            self, model: GridModel, view: ViewState
        ) -> "dict[HandleId, Point]":
            """Widget-px handle positions, same vp-params logic as
            ``TransformController._positions`` (perspective -> one vp0)."""
            has_vp = self._grid_has_vp(model)
            if has_vp:
                return handle_positions(
                    view,
                    model.transform,
                    vp_count=1,
                    vp_x=model.params.get("vp_x", DEFAULT_VP_X),
                    vp_y=model.params.get("vp_y", DEFAULT_VP_Y),
                )
            return handle_positions(view, model.transform)

        @staticmethod
        def _grid_has_vp(model: GridModel) -> bool:
            """``True`` when the active grid type's schema defines ``vp_x``
            (mirrors the controller's private check, registry-based)."""
            try:
                cls = get(model.grid_type_id)
            except KeyError:
                return False
            return any(param.name == "vp_x" for param in cls.params)

        @staticmethod
        def _shift_held(event) -> bool:
            """Live Shift-modifier state of a mouse event."""
            return bool(event.modifiers() & _enum_member("KeyboardModifier", "ShiftModifier"))

        # ------------------------------------------------------ Qt events

        def paintEvent(self, event) -> None:  # noqa: ARG002 — Qt signature
            qp = qt_compat.QtGui.QPainter(self)
            try:
                self.paint_grid(qp)
            finally:
                qp.end()

        def mousePressEvent(self, event) -> None:
            """Edit-session protocol only (module docstring): hit -> drag,
            miss -> end session + pass the event through; anything outside a
            session is ignored outright. A document with NO grids (so no
            selected grid) ignores it too: the service cannot start a session
            on nothing, and a stale flag must not keep stealing clicks from the
            canvas."""
            model = self._model_provider()
            view = self._view_state_provider()
            if (
                model is None
                or view is None
                or not model.edit_session
                or event.button() != _enum_member("MouseButton", "LeftButton")
            ):
                event.ignore()
                return
            point = _mouse_point(event)
            hit = hit_test(
                self._positions(model, view), point, MOUSE_TOLERANCE_PX, view_scale(view)
            )
            if hit is None:
                # A click past the markers completes the session;
                # ignore() lets the same press continue toward Krita.
                if self._end_edit_session is not None:
                    self._end_edit_session()
                else:
                    logger.debug("overlay: no end_edit_session hook wired; miss ignored")
                event.ignore()
                return
            if self._controller.begin_drag(hit, point, self._shift_held(event)):
                self._dragging = True
                self._hover = hit
                event.accept()
                self.request_paint()
            else:
                logger.debug("overlay: begin_drag refused for handle %r", hit)
                event.ignore()

        def mouseMoveEvent(self, event) -> None:
            """Mid-drag: feed the controller. ``drag_to`` writes the model and
            calls ``model.emit()``, which forwards to the owning
            :class:`GridStateService` — the single notification hub — and the
            service re-enters this overlay through
            ``CanvasContext._on_state_change`` -> ``refresh()`` for the repaint,
            so the drag step needs NO second repaint request from here.
            Otherwise: hover + cursor only."""
            model = self._model_provider()
            view = self._view_state_provider()
            if model is None or view is None or not model.edit_session:
                event.ignore()
                return
            point = _mouse_point(event)
            if self._dragging:
                # The repaint is the service's job (module docstring above); a
                # refused step (no active drag/model) still changes nothing.
                self._controller.drag_to(point, self._shift_held(event))
                event.accept()
                return
            hit = hit_test(
                self._positions(model, view), point, MOUSE_TOLERANCE_PX, view_scale(view)
            )
            if hit != self._hover:
                self._hover = hit
                self.setCursor(_cursor_for_shape(cursor_shape(hit) if hit else "arrow"))
                self.request_paint()
            event.accept()

        def mouseReleaseEvent(self, event) -> None:
            """End the in-flight drag on a LeftButton release only; other releases pass through."""
            if (
                self._dragging
                and event.button() == _enum_member("MouseButton", "LeftButton")
            ):
                self._dragging = False
                self._controller.end_drag()
                self.request_paint()
                event.accept()
            else:
                event.ignore()

        def eventFilter(self, watched, event) -> bool:
            """Canvas-widget lifecycle sync ONLY: Resize -> re-geometry,
            Show -> re-geometry + repaint, Destroy (Qt5 only; guarded, the
            Qt6 path is the ``destroyed()`` signal) -> hide. Always returns
            False — the filter never consumes Krita's events."""
            et = event.type()
            if et == qt_compat.Resize:
                self.sync_geometry()
            elif et == qt_compat.Show:
                self.sync_geometry()
                self.request_paint()
            elif qt_compat.Destroy is not None and et == qt_compat.Destroy:
                self.hide()
            return False

else:  # pragma: no cover — pure (no PyQt) environment

    class GridOverlay:  # type: ignore[no-redef]
        """Import-safe stub without PyQt: construction raises RuntimeError
        (no QWidget base class exists to subclass). Tests of this module
        skip via ``tests.conftest.try_import_qt``."""

        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError(
                "composition_grid.canvas.overlay.GridOverlay requires PyQt, "
                "but composition_grid.qt_compat reports QT_AVAILABLE=False"
            )
