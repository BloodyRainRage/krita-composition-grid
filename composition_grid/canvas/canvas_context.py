"""Canvas context — lifetime wiring between Krita objects and the overlay.

Qt-adjacent module (it imports NO Qt binding itself: Qt is reached only through
the sibling modules that use :mod:`composition_grid.qt_compat` — ``overlay``,
``canvas_locator``, ``viewport_triggers``; NO ``krita`` import anywhere: the
window/view/canvas objects arrive as duck-typed parameters — libkis wrapper
or test fake, the context cannot tell and must not care).

One :class:`CanvasContext` per main window (holder lives on the plugin
``Extension`` instance). Responsibilities:

* LOCATE + PARENT: ``bind(window, view, canvas)`` resolves the canvas
  widget via ``canvas_locator.find_canvas_widget`` (accepting either a
  Krita ``Window`` wrapper exposing ``qwindow()`` or the qwindow itself),
  then gates it through ``is_plausible_canvas_widget`` BEFORE any overlay
  exists: the lenient locator fallback may have matched a QMdiArea/docker
  impostor viewport, and an impostor degrades exactly like a missing widget
  (INFO with the concrete reason, ``False``, no overlay — never parented,
  never eats dock chrome mouse input). The default ``overlay_factory``
  builds a real ``GridOverlay`` parented to the vetted widget. No widget
  -> bind returns ``False``, the grid stays hidden and NOTHING raises
  (degradation).
* VIEW STATE — COMPUTED, NOT RECEIVED.
  On Krita 5.3.x libkis ``Canvas`` exposes NO signals at all —
  ``documentRectInWidgetPixelsChanged``, ``rotationChanged``,
  ``mirrorChanged``, ``sizeChanged`` and ``removed`` are absent — and there
  is no ``documentRectInWidgetPixels()`` slot either
  (``libs/libkis/Canvas.h`` at tag v5.3.0-prealpha declares no signals; they
  are master/6.x only). The premise "5.3 has the Canvas signals" was
  wrong, so a ViewState that could only be REBUILT from signal payloads could
  never be built on the shipping host: the overlay existed but never painted.
  ``view_state`` is therefore a property that recomputes from the CURRENT
  host answers every time it is asked — viewport size from the attached
  canvas widget (``width()``/``height()``), document extent LIVE from the
  stored ``Document`` (a Canvas Size change repaints via
  ``rangeChanged`` and must not recompute with the bind-cached extent),
  ``Canvas.zoomLevel()``,
  ``Canvas.preferredCenter()``, ``Canvas.rotation()``, ``Canvas.mirror()``
  (the documented shape of this API generation: mirror is a plain bool) —
  through :mod:`composition_grid.canvas.
  host_view_probe` and the pure :mod:`composition_grid.canvas.view_metrics`.
  ``Document.resolution()`` is NOT an input: the live 5.3.4.1 host proves the
  scale is ``zoomLevel()`` itself (both of its scrollbar values agree with
  ``s = zoom`` on both axes, see
  :func:`composition_grid.canvas.view_metrics.scale_per_doc_px`).
  The Canvas SIGNALS are kept subscribed because where they exist (Krita 6)
  they are the best invalidation source available, but they are now ONLY
  accelerators: every view handler does nothing but ``_repaint()``, because
  the value it used to store is re-read from the getter that the host keeps
  in step with its own emissions. Storing payloads as a second source of the
  same quantity is exactly the half-state "crutch" this project forbids.
  TWO sanctioned overrides of the reconstructed rect, in precedence order:
  ``View.flakeToImageTransform()`` composed with
  ``View.flakeToCanvasTransform()`` IS the host's own doc->widget matrix
  (:func:`host_view_probe.read_host_affine`) and beats everything, because it
  is the only source that is exact under CANVAS ROTATION — the Krita 5.3
  rotation is clockwise-positive and pivots nowhere near the document centre,
  so the reconstructed model misses by hundreds of px (measured in
  :mod:`composition_grid.canvas.doc_view_mapper`). A state carrying it keeps
  ``doc_view_mapper`` untouched: the mapper returns the
  host matrix, and the rect it also carries is reporting data only. Then on
  Krita 6 ``Canvas.documentRectInWidgetPixels()`` answers the exact UNROTATED
  document rect, and when that getter exists it beats the reconstructed rect
  (the rect is axis-aligned and rotation is applied by the
  mapper about the document centre, exact only at 0°). The fallback branch
  (no affine, no precise rect -> computed) is what the 5.3 rotation-free tests
  exercise. ``view_state`` answers ``None`` while any required input is
  unusable (degradation, no division-by-zero math; the getter probing never
  raises and never invents a default).
* DOCUMENT SIZE: the ONLY libkis sources of document pixels are
  ``Document.width()``/``Document.height()`` ("the width/height of the image
  in pixels"), reached through the ``View`` stored at bind —
  ``view.document()``, falling back to ``window.activeView().document()``
  when the bound view answers no document (the branch that worked is logged
  at DEBUG, and :meth:`refresh_document_size` is the extension's re-read
  point). ``Canvas`` exposes no size getter at all, so probing one is not a
  fallback that ever succeeds. These are READS only: a Document mutation API
  is never called anywhere in this plugin. ``sizeChanged(QSize)``
  is deliberately NOT a size source — libkis ``Canvas.h`` documents that
  payload as "the size in widget pixels", i.e. the VIEWPORT, while the mapper
  and the default element need DOCUMENT pixels; it is one
  of the repaint-only signals above. The document object read for the extent
  is kept (``self._document``) because :attr:`view_state`
  re-reads ``width()``/``height()`` from it LIVE; the stored
  ``_doc_w``/``_doc_h`` pair survives ONLY as the docker's last-known size for
  ``ensure``/Reset — the deliberate split documented on :attr:`doc_size`.
* SIGNAL SUBSCRIPTION: every handler connects through a never-raises
  adapter whose connected slot object is kept in ``self._connections``
  together with its source, so callbacks are never garbage-collected while
  connected and a malformed payload can never raise outward; an optional
  signal MISSING on the given object is logged at DEBUG and skipped — Krita
  versions differ, and on 5.3.x that is every Canvas signal (all skipped, all
  harmless).
* 5.3 REPAINT TRIGGERS (all genuine Qt events, NO TIMERS — polling is
  forbidden and nothing here polls): the canvas viewport is the widget of
  the ``QAbstractScrollArea`` that holds it (the locator's own DEBUG line
  "canvas viewport … inside <QAbstractScrollArea>" proves the holder is
  reachable), and a scroll/zoom of the canvas is Qt-visible there.
  :mod:`composition_grid.canvas.viewport_triggers` decides WHICH objects and
  events those are — both scrollbars' ``valueChanged``/``rangeChanged``, plus a
  viewport ``QObject`` filter for ``Wheel``/``Resize``/``Scroll``/
  ``ScrollPrepare``/``Paint`` and a holder filter for ``LayoutRequest`` (the
  resetCanvas widget swap; Paint is the only observable
  event for mirror/rotation/slider-zoom changes that move neither scrollbar)
  (the event constants resolved through ``qt_compat``, the
  only legal place to add such constants) — and this class decides WHEN they
  are wired and released: :meth:`bind` installs, :meth:`unbind` disconnects.
  Every trigger only asks for a repaint; the filter always returns ``False``, so
  it invalidates without ever consuming Krita's input.
* TEARDOWN/REBIND: the overlay's own event filter tracks Resize/Show/Destroy
  of the canvas widget; the Canvas ``removed`` signal hides it; ``unbind``
  disconnects every signal (Canvas, window, scrollbars), removes the viewport
  filter and disposes the overlay; ``Window.activeViewChanged`` (connected on
  the window and/or qwindow when exposed) RE-RESOLVES fresh objects before
  rebinding — view-like payload -> its ``canvas()``, else the window's
  ``activeView()``, else a window-like payload re-resolves only the window,
  and the stored triplet survives as the last resort (DEBUG names the branch).
  Repaint coverage, honestly split: the event path recovers an ACTIVE-VIEW SWITCH
  at the same window (fresh widget re-lookup + Canvas/scrollbar subscriptions
  on the NEW canvas); a ``KisCanvas2::resetCanvas`` widget swap is covered
  only insofar as it emits ``activeViewChanged`` or triggers an external
  rebind — meanwhile the overlay's ``destroyed()`` hook merely hides it — and
  cross-window rediscovery / post-reset re-attach without an emission is
  deferred to the Extension wiring. No timers anywhere.
* MODEL ROUTING: the overlay gets pure providers, all keyed by THIS context's
  own document (``plugin_support.document_key`` of the bound ``Document``;
  ``None``/empty without a usable key) — :meth:`grids_provider` the whole list
  to PAINT, :meth:`model_provider` the SELECTED grid the handles and the drag
  belong to. The service's globally active document drives only docker/command
  routing, never the overlay
  (``GridStateService.end_edit_session(doc_id)`` keeps ``GridOverlay`` free of
  the service type).
"""

from __future__ import annotations

import logging
from typing import Callable

from composition_grid.canvas import host_view_probe, viewport_triggers
from composition_grid.canvas.canvas_locator import find_canvas_widget
from composition_grid.canvas.doc_view_mapper import ViewState
from composition_grid.canvas.host_view_probe import callable_attr, guarded_call
from composition_grid.canvas.overlay import GridOverlay
from composition_grid.canvas.view_metrics import (
    document_rect_widget,
    is_usable,
    to_view_state,
)
from composition_grid.canvas.widget_plausibility import is_plausible_canvas_widget
from composition_grid.core.geometry import Bounds
from composition_grid.model.grid_model import GridModel, GridStateService
from composition_grid.plugin_support import document_key

logger = logging.getLogger("composition_grid")

__all__ = ["CanvasContext"]

# Canvas signal -> handler. The ViewState is COMPUTED, so every
# view signal maps onto the same repaint-only handler; the mapping stays a list
# because the NAMES are the contract: each one is probed, and one missing on the
# host (all of them on 5.3.x) is a DEBUG note, never an error. ``sizeChanged``
# is here as a repaint source ONLY: its payload is the viewport in widget px and
# must never become the document extent.
_SIGNAL_HANDLERS: "tuple[tuple[str, str], ...]" = (
    ("stateChanged", "_on_view_changed"),
    ("documentOffsetChanged", "_on_view_changed"),
    ("effectiveZoomChanged", "_on_view_changed"),
    ("rotationChanged", "_on_view_changed"),
    ("mirrorChanged", "_on_view_changed"),
    ("sizeChanged", "_on_view_changed"),
    ("documentRectInWidgetPixelsChanged", "_on_view_changed"),
    ("removed", "_on_canvas_removed"),
)


def _default_overlay_factory(
    parent, model_provider, view_state_provider, end_edit_session, grids_provider
):
    """Production factory; positional contract shared with test doubles."""
    return GridOverlay(
        parent,
        model_provider,
        view_state_provider,
        end_edit_session=end_edit_session,
        grids_provider=grids_provider,
    )


def _never_raises(handler: Callable) -> Callable:
    """Adapter guaranteeing the "no exception outward" pin:
    a malformed foreign payload can never travel back through Qt's emission
    (PyQt re-raises out of ``emit`` by default) — it becomes a logged
    exception record instead."""

    def _slot(*args):
        try:
            handler(*args)
        except Exception as exc:  # noqa: BLE001 — plugin host must not crash on signal fan-out
            logger.exception(
                "canvas_context: signal handler %s failed (swallowed by design)",
                getattr(handler, "__name__", repr(handler)),
            )

    return _slot


def _disconnect_signal(source, signal_name: str, slot) -> None:
    """Disconnect one recorded connection; a deleted wrapper is a DEBUG note, never
    an exception, and never a reason to skip the sources that come after it."""
    try:
        signal = getattr(source, signal_name, None)
    except RuntimeError as exc:
        logger.debug(
            "canvas_context: %s.%s lookup failed (deleted source): %r",
            type(source).__name__,
            signal_name,
            exc,
        )
        return
    if signal is None or not hasattr(signal, "disconnect"):
        logger.debug("canvas_context: %r lost on its source", signal_name)
        return
    try:
        signal.disconnect(slot)
    except Exception as exc:  # noqa: BLE001 — source may be mid-teardown
        logger.debug("canvas_context: disconnect of %r skipped: %r", signal_name, exc)


class CanvasContext:
    """Owns the overlay + signal connections for one window/view/canvas."""

    def __init__(
        self,
        service: GridStateService,
        overlay_factory: Callable[..., object] | None = None,
    ) -> None:
        """``service`` routes the document's grids and the session end;
        ``overlay_factory(parent, model_provider, view_state_provider,
        end_edit_session, grids_provider)`` builds the overlay (default: real
        GridOverlay).

        The service subscription happens ONCE here (``on_change`` has no
        unsubscribe API, and bind/unbind/rebind must not stack
        duplicates); the callback tolerates a not-yet-bound context."""
        self._service = service
        self._overlay_factory = overlay_factory or _default_overlay_factory
        service.on_change(self._on_state_change)  # bound method: strong ref
        self._window = None
        self._view = None
        self._canvas = None
        self._qwindow = None
        self._widget = None
        self._overlay = None
        self._connections: list[tuple[object, str, Callable]] = []
        # The installed viewport-filter handles (see ``viewport_triggers``):
        # kept strongly for as long as they are installed and undone by
        # :meth:`unbind`.
        self._event_filters: list[object] = []
        # Read-only snapshot of what :meth:`_install_viewport_triggers` actually
        # wired (scroll area found? bars? which filters?), exposed through
        # :attr:`trigger_status` so the diagnostics report can show the operator
        # the live wiring instead of guessing.
        self._trigger_status: "dict | None" = None
        # The DOCUMENT object read at bind/refresh through ``View -> Document``
        # — the source of the LIVE extent (and nothing else: the computed scale
        # takes ``zoomLevel()`` directly, ``resolution()`` is not read) —
        # plus the LAST-KNOWN extent pair. The two roles are split
        # deliberately: :attr:`view_state` re-reads ``Document.width()/height()``
        # from ``self._document`` on every paint, while ``_doc_w``/``_doc_h``
        # stay the docker's last-known size for ``ensure``/Reset (the degraded
        # paths where no rect can be built but a size must still be offered),
        # with their documented refresh point
        # (:meth:`refresh_document_size`).
        self._document = None
        self._doc_w = 0.0
        self._doc_h = 0.0
        # Latched once the FIRST unresolved document size has been surfaced at
        # INFO: the per-branch DEBUG notes stay, but
        # the operator gets exactly one INFO that the grid is size-starved, so a
        # re-read storm cannot flood the log while ``view_state`` is ``None``.
        self._doc_size_starvation_reported = False

    # ------------------------------------------------------------ public

    @property
    def overlay(self):
        """The live overlay widget (``None`` while unbound / degraded)."""
        return self._overlay

    @property
    def widget(self):
        """The located canvas widget (``None`` while unbound / degraded)."""
        return self._widget

    @property
    def trigger_status(self) -> "dict | None":
        """What the 5.3 repaint-trigger wiring actually achieved at the last
        bind (``None`` before any bind): the located scroll area and bars plus
        which event filters were installed, with the watched event NAMES.
        Pure introspection — read by :mod:`composition_grid.diagnostics`,
        nothing in the render path consumes it."""
        return self._trigger_status

    @property
    def view_state(self) -> ViewState | None:
        """The ViewState COMPUTED from the current host answers, or ``None``
        while a required input is unusable (module docstring): EVERY input —
        including the document extent — is read live here, so one frame never
        mixes sources.

        Precedence, one rule and no version branches — the most exact source
        the host answers first:

        1. ``View.flakeToImageTransform()`` + ``View.flakeToCanvasTransform()``
           composed by :func:`host_view_probe.read_host_affine` into the host's
           own doc px -> widget px matrix. THIS WINS UNDER ROTATION: it is the
           host's real pivot and its real (clockwise-positive) angle, whereas
           every rect below is axis-aligned and has to be paired with the
           mapper's reconstructed rotation, which is exact only at 0°
           (measured, see :mod:`composition_grid.canvas.doc_view_mapper`).
           The rect the returned state still carries is informational.
        2. ``Canvas.documentRectInWidgetPixels()`` (Krita 6) — the host's exact
           UNROTATED document rect; the mapper still reconstructs rotation from
           ``Canvas.rotation()`` on top of it.
        3. the pure ``view_metrics`` formula from the getters a 5.3.x host does
           answer.

        Nothing is cached between calls, so a zoom/pan/resize/rotate that
        arrives with no signal at all is reflected on the next paint — an
        ``Image ▸ Canvas Size`` repaint announced by ``rangeChanged`` must not
        recompute with the bind-time extent (the scale denominator would be
        wrong)."""
        if self._canvas is None:
            return None
        extent = host_view_probe.read_doc_extent(self._document)
        if extent is None:
            # Route the silent None-outs of the computed path through the
            # latched reporter: first starvation INFO,
            # DEBUG afterwards; the probe note already named the getter.
            return to_view_state(None, reason="live Document.width()/height() unanswered")
        orientation = host_view_probe.read_orientation(self._canvas)
        if orientation is None:
            return to_view_state(
                None, reason="canvas orientation (rotation()/mirror()) unanswered"
            )
        host_affine = host_view_probe.read_host_affine(self._view)
        if host_affine is not None:
            return ViewState(
                document_rect_widget=self._rect_beside_host_affine(
                    host_affine, extent, orientation
                ),
                rotation_deg=orientation[0],
                mirror_x=orientation[1],
                mirror_y=orientation[2],
                doc_width=extent[0],
                doc_height=extent[1],
                host_affine=host_affine,
            )
        precise = host_view_probe.read_precise_rect(self._canvas)
        if precise is not None:
            return ViewState(
                document_rect_widget=precise,
                rotation_deg=orientation[0],
                mirror_x=orientation[1],
                mirror_y=orientation[2],
                doc_width=extent[0],
                doc_height=extent[1],
            )
        metrics = host_view_probe.build_metrics(
            self._widget,
            self._canvas,
            extent[0],
            extent[1],
            orientation,
        )
        return to_view_state(metrics)

    def _rect_beside_host_affine(self, affine, extent, orientation) -> Bounds:
        """The ``document_rect_widget`` a ``host_affine`` state still carries.

        The affine is the mapping the overlay uses, so this rect is reporting
        data only (:mod:`composition_grid.diagnostics` prints it as the
        computed rect): the computed rect while the getters that produce it
        answer, else the axis-aligned box the affine maps the document bounds
        into — which is the document's real widget footprint, rotation
        included. Never ``None``, so a host that answers the affine is never
        starved by a missing viewport/zoom/preferredCenter getter."""
        metrics = host_view_probe.build_metrics(
            self._widget, self._canvas, extent[0], extent[1], orientation
        )
        if metrics is not None and is_usable(metrics):
            return document_rect_widget(metrics)
        doc_w, doc_h = extent
        points = [
            affine.apply(corner)
            for corner in ((0.0, 0.0), (doc_w, 0.0), (doc_w, doc_h), (0.0, doc_h))
        ]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return Bounds(x=min(xs), y=min(ys), w=max(xs) - min(xs), h=max(ys) - min(ys))

    @property
    def window(self):
        """The Window object the subscriptions currently point at (``None`` unbound)."""
        return self._window

    @property
    def canvas(self):
        """The Canvas object the signals are currently connected to (``None``
        while unbound). Read by the plugin extension to make ``canvasChanged``
        routing idempotent — a rebind for the canvas it is already on would
        only churn a perfectly good overlay."""
        return self._canvas

    @property
    def doc_size(self) -> "tuple[float, float] | None":
        """The LAST-KNOWN document size in DOCUMENT px, or ``None`` while no
        ``View`` -> ``Document`` has answered a usable one.

        The roles are split deliberately: :attr:`view_state`
        reads ``Document.width()/.height()`` LIVE from the stored document
        object on every frame (the scale denominator must never be stale),
        while this pair stays the size captured at the last
        :meth:`_read_doc_size`/bind/refresh — exposed separately because the
        plugin entry needs a document size for the default element
        (Reset / ``ensure``) also while no widget or document rect is
        resolvable, and it must NOT gain live reads with side effects on that
        path. ``ensure``/Reset refresh it via :meth:`refresh_document_size`
        before use. The plugin's route to a document size, and only ever a
        READ."""
        if self._doc_w <= 0.0 or self._doc_h <= 0.0:
            return None
        return (float(self._doc_w), float(self._doc_h))

    def refresh_document_size(self) -> bool:
        """Re-read the document extent and answer whether a usable size is known.

        The extension's refresh point: a document can be routed onto an
        already-bound context without a rebind (the docker's
        ``canvasChanged`` for a canvas the context is already on), so the size
        has to be re-readable on demand instead of only at bind — otherwise a
        new document would be ensured with the previous document's extent. The
        window's CURRENT ``activeView()`` therefore wins here when it answers
        (it is the fresher source); the view stored at bind is kept when the
        window answers nothing (e.g. a raw qwindow, or a transient wrapper
        failure)."""
        current_view = guarded_call(self._window, "activeView")
        if current_view is not None and current_view is not self._view:
            self._view = current_view
        self._read_doc_size()
        return self.doc_size is not None

    def model_provider(self) -> GridModel | None:
        """This context's own document's SELECTED grid; None when it has no
        usable key or no selection.

        The handles, the hover state and the drag controller act on this one
        grid — the service guarantees at most one document is being edited on
        the canvas at a time."""
        doc_id = document_key(self._document)
        if doc_id is None:
            return None
        return self._service.selected(doc_id)

    def grids_provider(self) -> "tuple[GridModel, ...]":
        """This context's own document's grids in list order — everything the
        overlay PAINTS; empty without a usable key."""
        doc_id = document_key(self._document)
        if doc_id is None:
            return ()
        return self._service.grids(doc_id)

    def _end_edit_session(self) -> None:
        """End THIS context's document's edit session; no-op without its own key."""
        doc_id = document_key(self._document)
        if doc_id is not None:
            self._service.end_edit_session(doc_id)

    def view_state_provider(self) -> ViewState | None:
        """Zero-arg provider form of :attr:`view_state` for injection."""
        return self.view_state

    def bind(self, window, view, canvas) -> bool:
        """Attach the overlay to *window*'s canvas widget and subscribe the
        view signals on *canvas*; returns ``True`` when an overlay exists.

        The triplet is stored as given: *view* is the libkis ``View`` the
        document size is read through (``view.document()``), so a caller that
        has a view MUST pass it — passing ``None`` degrades the context to the
        ``window.activeView()`` branch of :meth:`_document_for_size` (and then
        to "no document size", which starves the plugin's ``ensure``/Reset).
        Signals are connected BEFORE the widget lookup fails or succeeds —
        view-state tracking keeps running even while the grid is hidden
        (degradation), so a later :meth:`rebind` can attach
        with fresh state. A located widget is only trusted after
        :func:`~composition_grid.canvas.widget_plausibility.
        is_plausible_canvas_widget` vetoes impostors (MDI-area/docker
        viewports of the lenient fallback, top-levels, undersized areas);
        an impostor degrades exactly like a missing widget. An existing
        binding is unbound first (idempotent re-bind of the same triplet
        is safe)."""
        self.unbind()
        self._window = window
        self._view = view
        self._canvas = canvas
        self._qwindow = self._resolve_qwindow(window)
        self._connect_signals(window, canvas)
        self._read_doc_size()

        widget = find_canvas_widget(self._qwindow)
        if widget is None:
            logger.info(
                "canvas_context: canvas widget not resolvable — grid stays "
                "hidden (degradation)"
            )
            return False
        plausible, reason = is_plausible_canvas_widget(widget)
        if not plausible:
            logger.info(
                "canvas_context: located widget rejected as canvas impostor "
                "(%s) — grid stays hidden (degradation)",
                reason,
            )
            return False
        self._widget = widget
        self._overlay = self._overlay_factory(
            widget,
            self.model_provider,
            self.view_state_provider,
            self._end_edit_session,
            self.grids_provider,
        )
        self._install_viewport_triggers()
        self._overlay.refresh()
        logger.debug("canvas_context: overlay attached to canvas widget %r", widget)
        return True

    def unbind(self) -> None:
        """Disconnect every signal (Canvas, window, scrollbars), drop the
        viewport event filter and dispose the overlay. Idempotent and
        exception-free outward (teardown races during canvas reset)."""
        self._disconnect_all()
        self._remove_viewport_filters()
        if self._overlay is not None:
            try:
                self._overlay.dispose()
            except Exception as exc:  # noqa: BLE001 — teardown must not escape
                logger.warning("canvas_context: overlay dispose failed: %r", exc)
        self._overlay = None
        self._widget = None
        self._qwindow = None
        self._window = None
        self._view = None
        self._canvas = None
        self._document = None
        self._trigger_status = None

    def rebind(self, window, view, canvas) -> bool:
        """Re-run the full attach for (possibly new) objects — the path for
        ``resetCanvas`` widget swaps and active-view changes."""
        return self.bind(window, view, canvas)

    def refresh_sources(self, window, view, canvas) -> bool:
        """Fresher Window/View/Canvas for the SAME widget: swap the triplet, move only
        the source subscriptions and re-read the extent; the overlay stays as it is."""
        swapped = window is not self._window or canvas is not self._canvas
        if swapped:
            stale = [
                s
                for s in (self._window, self._canvas, self._qwindow)
                if s is not None
            ]
            self._drop_connections(*stale)
        self._window, self._view, self._canvas = window, view, canvas
        resolved = self._resolve_qwindow(window)
        self._qwindow = resolved if resolved is not None else self._qwindow
        if swapped:
            self._connect_signals(window, canvas)
        self._read_doc_size()
        return self._overlay is not None

    # ------------------------------------------------------- subscriptions

    def _resolve_qwindow(self, window):
        """Krita ``Window`` wrapper exposes ``qwindow()``; a raw QMainWindow
        test fixture IS the qwindow. Neither -> ``None`` (locator degrades)."""
        if window is None:
            return None
        try:
            qwindow = getattr(window, "qwindow", None)
        except RuntimeError as exc:
            logger.debug("canvas_context: qwindow lookup failed (deleted source): %r", exc)
            return None
        if callable(qwindow):
            try:
                return qwindow()
            except Exception as exc:  # noqa: BLE001 — duck-typed host object
                logger.warning("canvas_context: qwindow() failed: %r", exc)
                return None
        return window

    def _connect_signals(self, window, canvas) -> None:
        for signal_name, handler_name in _SIGNAL_HANDLERS:
            self._connect(canvas, signal_name, getattr(self, handler_name))
        # Window-level re-sync source: connect on the wrapper
        # AND on the qwindow when both exist (deduped by identity).
        targets = [window]
        if self._qwindow is not None and self._qwindow is not window:
            targets.append(self._qwindow)
        for target in targets:
            self._connect(target, "activeViewChanged", self._on_active_view_changed)

    def _connect(self, source, signal_name: str, handler) -> bool:
        """getattr-based connect: an absent optional signal is a DEBUG log
        and ``False`` — NEVER an exception outward (version drift is
        data, not a crash). Connections are stored for disconnect and as
        strong references to the bound handlers."""
        if source is None:
            logger.debug("canvas_context: no source for optional signal %r", signal_name)
            return False
        try:
            signal = getattr(source, signal_name, None)
        except RuntimeError as exc:
            logger.debug("canvas_context: %s.%s lookup failed: %r",
                         type(source).__name__, signal_name, exc)
            return False
        if signal is None or not hasattr(signal, "connect"):
            logger.debug(
                "canvas_context: optional signal %r absent on %s (Krita version "
                "difference); skipped",
                signal_name,
                type(source).__name__,
            )
            return False
        slot = _never_raises(handler)
        try:
            signal.connect(slot)
        except Exception as exc:  # noqa: BLE001 — the host object may reject it
            logger.warning(
                "canvas_context: connecting %r failed: %r (treated as absent)",
                signal_name,
                exc,
            )
            return False
        # The stored slot keeps the bound handler alive (strong refs —
        # PyQt does not guarantee connected-method persistence on its own).
        self._connections.append((source, signal_name, slot))
        return True

    def _disconnect_all(self) -> None:
        """Disconnect every recorded connection and DRAIN the bookkeeping even when
        the first source is already deleted: one dead emitter may not strand the
        live slots, the references or the rest of the teardown."""
        entries, self._connections = self._connections, []
        for source, signal_name, handler in entries:
            _disconnect_signal(source, signal_name, handler)

    def _drop_connections(self, *sources) -> None:
        """Disconnect and forget only the connections whose emitter is one of *sources*."""
        kept = []
        for entry in self._connections:
            if any(entry[0] is source for source in sources):
                _disconnect_signal(*entry)
            else:
                kept.append(entry)
        self._connections[:] = kept

    # -------------------------------------------------- 5.3 repaint triggers

    def _install_viewport_triggers(self) -> None:
        """Wire the invalidation sources a Krita 5.3 Canvas cannot announce.

        :mod:`composition_grid.canvas.viewport_triggers` decides WHICH objects
        and events those are (the ``QAbstractScrollArea`` holding the viewport,
        its two scrollbars, Wheel/Resize/Scroll/ScrollPrepare/``Paint`` on the
        viewport — Paint is the only event for mirror /
        rotation / slider-zoom / zoom-to-cursor changes that move neither
        scrollbar value nor range — and ``LayoutRequest`` on the holder for the
        resetCanvas widget swap); this method only owns them for the lifetime
        of the binding: the scrollbar signals go through the same
        never-raises, disconnectable :meth:`_connect` path as every other
        connection, and the filter handles are kept so :meth:`unbind` can undo
        exactly this. Every trigger asks for a repaint only — the ViewState is
        recomputed when the paint asks for it. The Paint trigger cannot
        feedback-loop: the overlay is a CHILD of the watched viewport and the
        handler only schedules the child's repaint (see the viewport_triggers
        module docstring; verified with a real child widget in
        ``tests/canvas/test_viewport_triggers_qt.py``). NO TIMERS."""
        widget = self._widget
        if widget is None:
            return
        scroll_area = viewport_triggers.find_scroll_area(widget)
        bars = viewport_triggers.scrollbars(scroll_area)
        if not bars:
            logger.debug(
                "canvas_context: no scrollbar owner above the canvas viewport; "
                "the scrollbar repaint triggers are not wired"
            )
        for bar in bars:
            self._connect(bar, "valueChanged", self._on_view_changed)
            self._connect(bar, "rangeChanged", self._on_view_changed)
        handle = viewport_triggers.install(widget, self._repaint)
        if handle is not None:
            # The handle keeps the filter referenced while it is installed.
            self._event_filters.append(handle)
        holder_handle = None
        if scroll_area is not None:
            holder_handle = viewport_triggers.install(
                scroll_area,
                self._repaint,
                event_types=viewport_triggers.HOLDER_EVENT_TYPES,
            )
            if holder_handle is not None:
                self._event_filters.append(holder_handle)
        else:
            logger.debug(
                "canvas_context: no scroll-area holder above the canvas viewport; "
                "the LayoutRequest repaint trigger is not wired"
            )
        self._trigger_status = {
            "scroll_area": scroll_area,
            "bars": bars,
            "viewport_filter_installed": handle is not None,
            "holder_filter_installed": holder_handle is not None,
            "viewport_event_names": viewport_triggers.TRIGGER_EVENT_NAMES,
            "holder_event_names": viewport_triggers.HOLDER_EVENT_NAMES,
        }

    def _remove_viewport_filters(self) -> None:
        for handle in self._event_filters:
            viewport_triggers.remove(handle)
        self._event_filters.clear()

    # ------------------------------------------------------------- handlers

    def _on_view_changed(self, *args) -> None:
        """Invalidation ONLY: every Canvas view signal and both
        scrollbar signals land here. The payload is ignored on purpose — the
        ViewState is computed from the getters the host keeps in step with its
        own emissions, and storing the payload as well would leave two sources
        of one quantity that can drift apart."""
        self._repaint()

    def _on_canvas_removed(self, *args) -> None:
        """Canvas ``removed`` (5.3.x hosts do not have it, so treat it as an
        optional accelerator of the same hiding the widget-loss path already
        does): the overlay is hidden — degradation, never a crash; rebind
        attaches again when a new canvas arrives."""
        if self._overlay is not None:
            self._overlay.hide()

    def _on_active_view_changed(self, *args) -> None:
        """Window-level view switch: RE-RESOLVE fresh objects before
        rebinding — rebinding the stale stored triplet would re-attach the
        overlay to the NEW widget while keeping Canvas subscriptions on the
        OLD canvas (wrong-view doc rect/rotation/mirror). Branch order (the
        taken branch is logged at DEBUG):

        1. ``payload-view`` — per libkis the ``Window.activeViewChanged``
           payload is the newly activated ``View``; if it duck-types (a
           callable ``canvas()`` answering a Canvas), view/canvas come
           from it;
        2. ``window-payload`` — else a payload exposing ``qwindow()`` is
           window-like: the window is re-resolved from it (a missing/None
           ``activeView()`` then keeps the stored view/canvas);
        3. ``window-activeView`` — else the (possibly stored) window's own
           ``activeView()`` and its ``canvas()`` when it exposes them;
        4. ``stored-triplet`` — last resort: the objects stored at the
           previous bind (previous behaviour, unchanged).

        Failures never escape into Qt: the connected :func:`_never_raises`
        adapter swallows+logs, and every foreign getter here is guarded."""
        window, view, canvas, branch = self._resolve_fresh_triplet(args)
        logger.debug(
            "canvas_context: activeViewChanged -> rebinding via %s branch", branch
        )
        self.rebind(window, view, canvas)

    def _resolve_fresh_triplet(self, args):
        """Branch resolution for :meth:`_on_active_view_changed`; answers
        ``(window, view, canvas, branch)`` — pure duck-typing, no Krita
        class, no exception outward (guarded getter probes)."""
        payload = args[0] if args else None
        window, view, canvas, branch = self._window, self._view, self._canvas, "stored-triplet"
        if payload is not None:
            fresh_canvas = guarded_call(payload, "canvas")
            if fresh_canvas is not None:
                return window, payload, fresh_canvas, "payload-view"
            if callable_attr(payload, "qwindow") is not None:
                window, branch = payload, "window-payload"
        fresh_view = guarded_call(window, "activeView")
        fresh_canvas = guarded_call(fresh_view, "canvas")
        if fresh_view is not None and fresh_canvas is not None:
            return window, fresh_view, fresh_canvas, "window-activeView"
        return window, view, canvas, branch

    def _on_state_change(self) -> None:
        """GridStateService notification: sync overlay transparency/repaint
        (no-op before bind or while the grid is hidden)."""
        if self._overlay is not None:
            self._overlay.refresh()

    def _repaint(self) -> None:
        if self._overlay is not None:
            self._overlay.request_paint()

    # ------------------------------------------------------ document extent

    def _read_doc_size(self) -> None:
        """Refresh the current document now; cache the extent only when readable."""
        document, branch = self._document_for_size()
        self._document = document
        if document is None:
            logger.debug(
                "canvas_context: no Document through the view or the window's "
                "activeView(); document size unknown (degradation)"
            )
            self._report_doc_size_starvation()
            return
        width = guarded_call(document, "width")
        height = guarded_call(document, "height")
        if width is None or height is None:
            logger.debug(
                "canvas_context: Document from the %s branch answers no usable "
                "width()/height(); document size unknown",
                branch,
            )
            self._report_doc_size_starvation()
            return
        try:
            doc_w, doc_h = float(width), float(height)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "canvas_context: unusable document size (%r, %r): %r", width, height, exc
            )
            self._report_doc_size_starvation()
            return
        if doc_w <= 0.0 or doc_h <= 0.0:
            logger.debug(
                "canvas_context: degenerate document size %gx%g ignored", doc_w, doc_h
            )
            self._report_doc_size_starvation()
            return
        self._doc_w, self._doc_h = doc_w, doc_h
        logger.debug(
            "canvas_context: document size %gx%g (document px) read via the %s branch",
            doc_w,
            doc_h,
            branch,
        )

    def _report_doc_size_starvation(self) -> None:
        """One INFO the FIRST time a document size cannot be resolved: while
        no document-size source answers,
        :attr:`view_state` stays ``None`` and the grid stays hidden until one
        appears (degradation) — a state that was previously only a DEBUG note and
        therefore effectively silent. The latch is never re-armed, so any later
        failed re-read stays DEBUG-only and cannot flood the log."""
        if self._doc_size_starvation_reported:
            return
        self._doc_size_starvation_reported = True
        logger.info(
            "canvas_context: the document size cannot be resolved — the grid stays "
            "hidden until a document-size source appears (view_state "
            "is None meanwhile). Further failures log at DEBUG."
        )

    def _document_for_size(self):
        """``(Document, branch)`` for the document-size read; ``(None, None)``
        when nothing answered.

        Branch order (both are libkis-documented READS, no mutation API):
        1. ``view`` — the ``View`` stored at bind: ``view.document()``;
        2. ``window-activeView`` — the stored window's own ``activeView()``
           (bind may have been handed no view, or a wrapper that answers no
           ``document()``; the docker's ``canvasChanged`` route is exactly
           this case when the view was re-resolved in between)."""
        document = guarded_call(self._view, "document")
        if document is not None:
            return document, "view"
        window_view = guarded_call(self._window, "activeView")
        if window_view is not None and window_view is not self._view:
            document = guarded_call(window_view, "document")
            if document is not None:
                return document, "window-activeView"
        return None, None
