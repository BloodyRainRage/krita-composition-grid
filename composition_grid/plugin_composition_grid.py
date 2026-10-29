"""The plugin entrypoint — Extension, the six actions, the docker.

WHAT THIS MODULE IS
====================

The plugin's registration surface: the ``krita.Extension`` subclass Krita drives
per main window, the six actions, the docker registration seam and
the per-window holders. It references libkis ONLY through the lazy adapters in
:mod:`composition_grid.plugin_support` (``krita_module``/``extension_base``, the
sanctioned ``KritaTransport`` pattern), so there is no top-level ``import
krita``: the entry — and the whole package — imports in a bare environment, and
``tests/test_plugin_smoke_qt.py`` drives it with a fake ``krita`` module injected
into ``sys.modules``. No Qt binding is imported here at all.

HARD INVARIANT
==============
The grid never touches a document MUTATION API — no layers, no pixels, no
export. The Document access that exists in this plugin is READS only, and it is
exactly two of them: ``Document.name()`` (the per-document model key) and
``Document.width()``/``Document.height()`` (the document extent in DOCUMENT
pixels) — the latter reached through ``View.document()``, because libkis
``Canvas`` has no size getter and its ``sizeChanged(QSize)`` is documented as
the size *in widget pixels* (the viewport), which is the wrong unit for the
default element and for the doc->widget mapper.
Both reads are performed inside
:class:`~composition_grid.canvas.canvas_context.CanvasContext` (view state /
``doc_size``); this entry only asks the context for the result.

ONE documented exception, bounded and deliberate: the per-document grid state
travels INSIDE the ``.kra`` as a ``composition_grid`` annotation, so saving it
calls ``Document.setAnnotation`` (and reading it calls ``annotation()``). An
annotation is file metadata of exactly the kind Krita itself stores — never a
layer, never a pixel, never part of an export — and the whole call site lives
in :mod:`composition_grid.plugin_document_state`, guarded, and suppressed while
a file is merely being restored (so opening a file still changes nothing).

HOLDERS — strong-reference ownership (GC-kill crash prevention)
================================================================
Krita's references to Python objects are not guaranteed to keep them alive, and
a collected holder means collected signal callbacks (the community-proven failure
mode). This extension instance is the holder: the per-window contexts, the
``(action, slot)`` pairs and the signal connections live in a
:class:`~composition_grid.plugin_holders.WindowHolders` registry keyed by the
stable ``qwindow()`` identity, ``docker_factory`` and the module-level
:data:`HELD_PLUGINS` (the extension itself) are held here directly.

ONE DOCKER, ONE OVERLAY PER WINDOW
==================================
``GridStateService.on_change`` has NO unsubscribe and every panel subscribes
exactly once per construction, so a second panel per window would leave a stale
subscriber raising inside ``_notify``. Hence: :meth:`create_docker` registers its
factory EXACTLY ONCE, and contexts are keyed by the stable ``qwindow()`` identity
(:func:`plugin_support.context_key`) — never by the libkis ``Window`` wrapper
Krita re-creates per ``activeWindow()`` call, which would mean a second overlay.
Holding is only half of the ownership rule, though: the same ``qwindow`` also drives the
``destroyed`` PRUNE that releases one window's context,
connections and actions, so the holders are bounded by the windows Krita HAS,
not by the windows it has had. The documented residual (models of documents
closed inside a live window) is named in :meth:`_prune_window` and is a manual
checklist item.

WHAT LIVES ELSEWHERE (one responsibility per module)
=====================================================
Action factory/binding: :mod:`composition_grid.plugin_actions` — with the arity
contract it enforces (``QAction.triggered`` ALWAYS delivers the checked bool,
which is why every handler here ends in ``*args``). Settings restore:
:mod:`composition_grid.plugin_settings`. The per-window holder maps and their
release: :mod:`composition_grid.plugin_holders`. What is left here is the
Extension lifecycle: the six handlers, the window wiring and its ownership
rules, the docker seam and the document routing.

DRAG MUTATIONS RIDE THE SERVICE — ONE NOTIFICATION PATH
=======================================================
Canvas drags write ``model.transform``/``model.params`` and call ``model.emit()``.
That ``emit`` used to need a per-model repaint bridge here because the service
never saw it — but choosing ONE notification path explicitly was required,
and the chosen path is the service: every :class:`GridModel` subscribes the
owning :class:`GridStateService`'s ``_notify`` at creation
(:meth:`GridStateService.ensure`), so a drag emit IS a service notification.
There is therefore NO per-model repaint bridge in this entry any more. Each
:class:`~composition_grid.canvas.canvas_context.CanvasContext` already
subscribes to ``service.on_change`` and refreshes its overlay from it, and the
docker subscribes its own refresh + persistence there — so one drag step yields
EXACTLY ONE notification fanning out to repaint + docker sync + persistence,
never the double-notify that a surviving service subscription PLUS a per-model
bridge would have produced.

PERSISTENCE
===========
Two stores, one codec (:mod:`composition_grid.model.grid_annotation_codec`):

* ``JsonGridSettings`` over ``KritaTransport`` (kritarc group
  ``composition_grid``) holds the LAST USED values of one grid; the docker's
  own subscriber saves the SELECTED grid there on every change, and the service
  consults it through its injected seed provider whenever a grid is ADDED.
* The document's ``composition_grid`` ANNOTATION holds a CONTAINER — every
  grid of that document with its id, label, element and visibility, plus the
  selected id — and is the first thing the restore consults
  (:func:`composition_grid.plugin_settings.restore_document_state`). Writing
  it is the plugin's single deliberate Document mutation call (an annotation
  is file metadata: never a layer, never a pixel, never an export), it goes
  through the thin :mod:`composition_grid.plugin_document_state` adapter from
  this entry's OWN save subscriber (:meth:`_save_grid_annotation`), and it
  happens only for a MUTATION of a document — the activation of a freshly
  opened file runs with writes suppressed, and a document that never stored an
  annotation and has no grids gets none at all, so creating or opening a file
  changes nothing until the artist adds a grid to it.

All UI strings English.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, Tuple

from composition_grid.canvas import canvas_locator
from composition_grid.canvas.canvas_context import CanvasContext
from composition_grid.core.grids import all_types
from composition_grid.model.grid_model import GridStateService
from composition_grid.model.settings_store import JsonGridSettings, KritaTransport
from composition_grid.plugin_actions import create_window_actions
from composition_grid.plugin_document_state import suppressed_writes, write_grid_annotation
from composition_grid.plugin_holders import WindowHolders
from composition_grid.plugin_settings import restore_document_state
from composition_grid.plugin_support import (
    context_key,
    document_key,
    extension_base,
    guarded_call,
    icon_krita,
    krita_module,
    never_raises_slot,
)
from composition_grid.ui.docker_registration import (
    make_panel_docker_class,
    shared_state_service,
)

logger = logging.getLogger("composition_grid")

__all__ = [
    "ACTION_IDS",
    "ACTIONS",
    "DOCKER_ID",
    "EXTENSION_ID",
    "HELD_PLUGINS",
    "MENU_LOCATION",
    "CompositionGridExtension",
    "register_plugin",
]

EXTENSION_ID = "composition_grid"
DOCKER_ID = "composition_grid_docker"
MENU_LOCATION = "tools/scripts"  # libkis libs/libkis/Window.h

#: ``(action id, English label, handler attribute)`` — the six actions,
#: created in this order. The ids are stable/pinned;
#: ``composition_grid_add`` is the multi-grid addition — a document has no grid
#: until the artist asks for one, so creating one needs its own command.
ACTIONS: Tuple[Tuple[str, str, str], ...] = (
    ("composition_grid_add", "Add Composition Grid", "_action_add"),
    ("composition_grid_toggle", "Toggle Composition Grid", "_action_toggle"),
    ("composition_grid_remove", "Remove Composition Grid", "_action_remove"),
    ("composition_grid_edit_session", "Toggle Grid Edit Session", "_action_edit_session"),
    ("composition_grid_reset_transform", "Reset Grid Transform", "_action_reset_transform"),
    ("composition_grid_cycle_type", "Cycle Grid Type", "_action_cycle_type"),
)

ACTION_IDS: Tuple[str, ...] = tuple(action_id for action_id, _text, _name in ACTIONS)

#: Module-level holder for the registered extension(s).
HELD_PLUGINS: list = []

#: Sentinel: "no explicit canvas" for :meth:`_attach_window`.
_UNSET = object()


class CompositionGridExtension(extension_base()):
    """The plugin's ``krita.Extension``: six actions, one canvas context and
    one overlay per window, plus the docker registration.

    Constructor dependencies are injectable for the headless smoke test — the
    same DI style as ``CanvasContext``/``GridPanel``: ``service`` (defaults to
    the shared docker seam singleton), ``settings`` (defaults to
    ``JsonGridSettings(KritaTransport())``), ``context_factory`` (defaults to
    ``CanvasContext``) and ``widget_resolver`` (defaults to
    :func:`composition_grid.canvas.canvas_locator.find_canvas_widget`).

    WHY ``widget_resolver`` IS INJECTABLE: rebind idempotence keys on the located
    canvas WIDGET (that Qt object is the stable identity of a
    view — libkis ``Canvas`` offers no widget getter of its own), and it only
    exists inside a real Qt widget tree the locator walks. The plugin must stay
    testable without one, exactly like the ``context_factory`` seam above, so the
    smoke test injects a resolver over its fake windows and still drives the whole
    rebind decision. Production passes nothing and gets the real locator, whose
    own contract is "never raises outward, ``None`` when unlocatable"."""

    def __init__(
        self,
        instance=None,
        service: GridStateService | None = None,
        settings: JsonGridSettings | None = None,
        context_factory: Callable[[GridStateService], object] | None = None,
        widget_resolver: Callable[[object], object] | None = None,
    ) -> None:
        # A genuine host-API uncertainty, not a crutch: the
        # community templates construct ``krita.Extension`` with NO arguments,
        # while a pybind11 wrapper MAY take the instance. A rejected argument
        # here would raise inside ``register_plugin`` during the package import,
        # i.e. the plugin silently never loads, so both shapes are tried and the
        # fallback is logged. Which branch a real Krita 5.3 takes is a
        # manual check in Krita.
        try:
            super().__init__(instance)
        except TypeError as exc:
            logger.debug(
                "plugin: krita.Extension rejected the instance argument (%r); "
                "constructing it with no arguments instead",
                exc,
            )
            super().__init__()
        self._service = service if service is not None else shared_state_service()
        self.settings = (
            settings if settings is not None else JsonGridSettings(KritaTransport())
        )
        # The store a NEW grid is seeded from (the kritarc half). The
        # docker seam's service is a process-wide singleton built before any
        # store exists, so the provider is installed here; the first install
        # wins, so a second window's extension cannot re-point it.
        self._service.set_seed_provider(self.settings.load)
        self._context_factory = context_factory or (lambda service: CanvasContext(service))
        # The canvas-widget locator used for the rebind decision (see the class
        # the module attribute is read at construction, so a real Krita gets the
        # production locator while the smoke test injects its own resolver.
        self._widget_resolver = widget_resolver or canvas_locator.find_canvas_widget
        # ONE CanvasContext (+ overlay) per main window, keyed by the stable
        # qwindow identity and held strongly for as long as the window lives:
        # keep the reference so the signal callbacks (and the
        # overlay they fire) are never collected while Krita still uses them —
        # and released by the ``destroyed`` prune when it does not. The maps
        # themselves are :class:`WindowHolders'` business.
        self._holders = WindowHolders()
        # ONE activeView observer and ONE destroyed/prune hook per window key, so a
        # fresher wrapper replaces them instead of stacking a new subscription.
        self._view_observers: dict = {}
        self._prune_hooked: set = set()
        self.docker_factory = None
        self._restored_docs: set = set()
        # Documents whose FILE carries a ``composition_grid`` annotation — either
        # read at activation or written by a user mutation. An annotation-less
        # document with no grids is never written: creating or opening a file
        # must not mark it modified, so the first real mutation is
        # what introduces the annotation.
        self._annotation_docs: set = set()
        # The libkis ``Document`` of every key this session has routed, kept as
        # the holder of BOTH duties: strong ownership and the
        # route the per-document annotation is written through — the save
        # subscriber below knows the doc id, and this is the one place a
        # document object for it exists.
        self._documents: dict = {}
        # ONE annotation save subscriber per extension (``on_change`` has no
        # unsubscribe, and ``register_plugin`` builds exactly one extension for
        # the whole session, so the fan-out stays at one write per change):
        # every notification that is not suppressed writes the ACTIVE
        # document's own state into its own annotation. It lives HERE rather
        # than on the docker because a mutation is a mutation whether or not
        # the panel exists.
        self._service.on_change(self._save_grid_annotation)

    # ------------------------------------------------------------------ API

    @property
    def service(self) -> GridStateService:
        """The grid-state service the actions and the docker both drive."""
        return self._service

    @property
    def contexts(self) -> tuple:
        """The live per-window contexts (holder introspection)."""
        return self._holders.contexts()

    @property
    def held_actions(self) -> list:
        """Every live ``(action, slot)`` pair (a collected slot is
        a dead connection, so both halves are held — per window, so a closed
        window's actions leave with it)."""
        return self._holders.actions()

    @property
    def held_connections(self) -> tuple:
        """Every ``(source, signal name, slot, window key)`` connection this
        extension owns (introspection of the holder contract)."""
        return self._holders.connections()

    def setup(self) -> None:
        """Documented NO-OP: Krita calls ``setup()`` before any
        window exists and there is nothing per-window to prepare — every wire-up
        happens in :meth:`createActions` (once per main window) and
        :meth:`create_docker`."""

    def createActions(self, window) -> None:  # noqa: N802 — libkis virtual
        """Create the six pinned actions plus this window's canvas context; a repeat
        call neither duplicates the menu actions nor rebinds a good overlay.

        THE OWNERSHIP RULE (live Krita 5.3 crash): *window* is a STACK ``Window``
        whose C++ half dies when this callback returns, so it is used synchronously
        only — for the QActions and the stable ``qwindow()`` key everything else is
        keyed by, resolved later through :meth:`_owned_window` (deferred when none)."""
        if window is None:
            logger.warning("plugin: createActions without a window; nothing registered")
            return
        key = context_key(window)
        if key is window:
            logger.warning(
                "plugin: createActions window answered no qwindow; its actions cannot "
                "be keyed and will stay inert (degradation)"
            )
        if not self._holders.has_actions(key):
            created = create_window_actions(
                window,
                ACTIONS,
                {
                    name: functools.partial(self._dispatch, key, name)
                    for _id, _text, name in ACTIONS
                },
                MENU_LOCATION,
            )
            # keyed by the qwindow, so the `destroyed` prune releases exactly these
            self._holders.hold_actions(key, created)
            self._install_prune_hook(key)
        self._attach_owned(key)

    # ------------------------------------------------------- window ownership

    def _owned_windows(self) -> list:
        """``Krita.instance().windows()`` guarded: the owned wrappers of open windows."""
        try:
            return list(krita_module().Krita.instance().windows())
        except Exception as exc:  # noqa: BLE001 — headless/absent libkis is normal here
            logger.debug("plugin: windows() unavailable: %r", exc)
            return []

    def _owned_window(self, key):
        """The live owned ``Window`` whose qwindow IS *key*, never the borrowed one."""
        if key is None:
            return None
        for candidate in self._owned_windows():
            if context_key(candidate) is key:
                return candidate
        return None

    def _dispatch(self, key, handler_name: str, *args) -> None:
        """One action handler against the owned window of *key*: no owner, no mutation."""
        window = self._owned_window(key)
        if window is None:
            logger.info(
                "plugin: action %r has no live window for its identity — no document "
                "is routed and nothing is mutated (degradation)",
                handler_name,
            )
            return
        getattr(self, handler_name)(window, *args)

    def _attach_owned(self, key, canvas=_UNSET):
        """Attach through the owned wrapper of *key*; none yet defers to a wake-up."""
        window = self._owned_window(key)
        if window is None:
            logger.info(
                "plugin: no live window for this identity yet — the attach is deferred "
                "to the next canvasChanged wake-up"
            )
            return None
        return self._attach_window(key, window, canvas)

    def _install_prune_hook(self, key) -> None:
        """One ``destroyed`` hook per window identity, on the STABLE qwindow."""
        if key in self._prune_hooked:
            return
        slot = never_raises_slot(lambda *_: self._prune_window(key))
        if self._holders.connect(key, "destroyed", slot, key=key):
            self._prune_hooked.add(key)

    def _install_view_observer(self, key, window) -> None:
        """One ``activeViewChanged`` observer per key: a fresher wrapper replaces it."""
        current = self._view_observers.get(key)
        if current is window:
            return
        if current is not None:
            self._holders.disconnect_source(current)
        slot = never_raises_slot(lambda *_: self._on_active_view(key))
        if self._holders.connect(window, "activeViewChanged", slot, key=key):
            self._view_observers[key] = window

    def create_docker(self):
        """Register ``DockWidgetFactory("composition_grid_docker", DockRight,
        PanelDocker)`` on ``Krita.instance()``, with ``PanelDocker`` built over
        the lazily imported ``krita.DockWidget`` base and the seams of this
        extension: its service, its settings store, the context-derived doc-size
        provider, :meth:`handle_canvas_changed` as the ``canvasChanged`` route
        and :meth:`route_on_show` for the document. The annotation is NOT a
        docker seam: persisting it is this extension's own subscriber (see
        :meth:`_save_grid_annotation`), so a grid change is stored whether or
        not the panel was ever opened.

        Registered EXACTLY ONCE (the cached factory is returned afterwards): the
        panel subscribes to the shared service on construction and
        ``GridStateService.on_change`` has no unsubscribe, so a second factory —
        hence a second docker per window — would leave stale subscribers raising
        inside ``_notify``). The factory is
        kept on the instance as a strong reference."""
        if self.docker_factory is not None:
            logger.info(
                "plugin: docker factory %r is already registered; not adding a second one",
                DOCKER_ID,
            )
            return self.docker_factory
        try:
            krita = krita_module()
            panel_class = make_panel_docker_class(
                krita.DockWidget,
                service=self._service,
                doc_size_provider=self.doc_size_provider,
                settings=self.settings,
                on_canvas_changed=self.handle_canvas_changed,
                on_route=self.route_on_show,
                icon_provider=lambda name: icon_krita(name),
            )
            factory = krita.DockWidgetFactory(
                DOCKER_ID, krita.DockWidgetFactoryBase.DockRight, panel_class
            )
            krita.Krita.instance().addDockWidgetFactory(factory)
        except Exception:  # noqa: BLE001 — registration must not kill the extension
            logger.exception(
                "plugin: docker registration failed; the six actions still work"
            )
            return None
        self.docker_factory = factory
        logger.info("plugin: docker factory %r registered", DOCKER_ID)
        return factory

    # -------------------------------------------------------------- actions
    #
    # The factory/binding mechanics moved to
    # :mod:`composition_grid.plugin_actions` (one
    # responsibility per module); this class owns the handlers and their state.

    def _active_model(self):
        """The SELECTED grid of the active document (``None`` without one),
        through public service API only."""
        return self._service.selected()

    def ensure_routed(self, window=None) -> bool:
        """Route the window's current document NOW; ``True`` when it is routed.

        "Routed" means the document has a grid CONTAINER (see
        :meth:`GridStateService.is_routed`), not that it has a grid: a document
        without grids is routed perfectly well and its next action is
        ``Add Composition Grid``.

        WHY this exists (first live Krita 5.3 run): ``createActions(window)`` is
        called while Krita is still starting up, and at that moment
        ``window.activeView()`` is ``None`` — so nothing was routed, the service
        knows no document, the docker is display-only and every action is a
        no-op. Krita then sends no further ``activeViewChanged`` for a document
        that was already there, so the plugin would wait forever.

        Re-attaching on demand is the event-driven answer (no timers — a hard
        constraint): the first interaction — a click on an action or on
        the docker — resolves the same ``window -> activeView -> canvas`` chain
        again, and by then the view exists. Cost is one getter chain, and the
        whole method is idempotent because ``_attach_window`` reuses the
        window's context and only re-activates the document.
        """
        target = window if window is not None else self._active_window()
        if target is None:
            logger.info(
                "plugin: no window to route — the grid stays hidden (degradation)"
            )
            return False
        self._attach_window(context_key(target), target)
        doc_id = self._service.active_doc_id
        routed = doc_id is not None and self._service.is_routed(doc_id)
        if not routed:
            logger.info(
                "plugin: routing attempted but this window still has no routable "
                "document — see the log lines above for which link failed"
            )
        return routed

    def route_on_show(self) -> bool:
        """The docker's seam: route the document when the panel is shown or used.

        Same mechanism as :meth:`ensure_routed`, exposed as a zero-arg callback
        because the panel must not know about windows, contexts or libkis (it is
        the seam ``make_panel_docker_class`` injects)."""
        return self.ensure_routed()

    # ACTION HANDLER ARITY (the reason for every ``*args``)
    # ``QAction.triggered`` is ``triggered(bool checked = false)``: Qt ALWAYS
    # delivers the checked flag as one positional argument, and these handlers
    # are connected as ``functools.partial(handler, window)``. Without the
    # ``*args`` the partial would call e.g. ``_action_toggle(window, checked)``
    # -> TypeError -> swallowed and logged by ``never_raises_slot`` -> six menu
    # entries that do nothing at all inside real Krita (the smoke fake used to
    # emit with NO argument, which is more permissive than Qt and hid this).
    # The flag is ignored on purpose: these are commands, not checkable states.

    def _action_add(self, window=None, *args) -> None:
        """Add a NEW grid to the active document: the multi-grid
        entry point. The grid is seeded from the global "last used" values,
        gets the document-centered default element and becomes the selected
        one; without a routable document nothing is created."""
        self.ensure_routed(window)
        if self._service.add_grid() is None:
            logger.info("plugin: no active document — the add action is a no-op")

    def _action_toggle(self, window=None, *args) -> None:
        """Show/hide the SELECTED grid; the service owns mutation +
        notify. With no grid selected there is nothing to show or hide — the
        action never invents one (that is :meth:`_action_add`'s job)."""
        self.ensure_routed(window)
        if self._active_model() is None:
            logger.info(
                "plugin: this document has no selected grid — the visibility "
                "action is a no-op"
            )
            return
        self._service.toggle_visible()

    def _action_remove(self, window=None, *args) -> None:
        """Take the SELECTED grid away: it is DELETED from the
        document's list, and the selection moves to its neighbour. The
        document keeps remembering the list that stays (its own annotation), so
        the file opens with the grids the artist left. Nothing selected (or no
        document) is a logged no-op."""
        self.ensure_routed(window)
        if self._active_model() is None:
            logger.info(
                "plugin: no selected grid — the remove action is a no-op"
            )
            return
        self._service.remove_grid()

    def _action_edit_session(self, window=None, *args) -> None:
        """Enter/leave the edit session, service-owned. The
        overlay then stops passing the mouse through and the markers activate."""
        self.ensure_routed(window)
        model = self._active_model()
        if model is None:
            logger.info("plugin: no active document — the edit-session action is a no-op")
            return
        if model.edit_session:
            self._service.end_edit_session()
        else:
            self._service.start_edit_session()

    def _action_reset_transform(self, window=None, *args) -> None:
        """Reset the element to the default for THIS document.
        The document size is taken from the canvas context's view state —
        ``View.document()`` in document px, re-read first (see
        :meth:`_context_doc_size`) — and without it the action is a logged
        no-op, exactly like the docker's Reset button when it has no provider."""
        self.ensure_routed(window)
        doc_size = self._context_doc_size(self._resolved_context(window))
        if doc_size is None:
            logger.info(
                "plugin: no document size from the canvas context — the reset action "
                "is a no-op (degradation)"
            )
            return
        if self._service.reset_transform(doc_size[0], doc_size[1]) is None:
            logger.info("plugin: no active document — the reset action is a no-op")

    def _action_cycle_type(self, window=None, *args) -> None:
        """Switch to the NEXT grid type in ``all_types()`` registry order,
        wrapping (a type change keeps transform and style — the
        service's ``set_type`` owns that rule)."""
        self.ensure_routed(window)
        registered = [cls.id for cls in all_types()]
        if not registered:
            logger.warning("plugin: the grid registry is empty; nothing to cycle")
            return
        model = self._active_model()
        if model is None:
            logger.info("plugin: no active document — the cycle-type action is a no-op")
            return
        # A model whose type left the registry (a settings entry from an older
        # build) simply cycles onto the first registered type.
        current = (
            registered.index(model.grid_type_id)
            if model.grid_type_id in registered
            else -1
        )
        self._service.set_type(registered[(current + 1) % len(registered)])

    # ------------------------------------------------------- window plumbing

    def _attach_window(self, key, window, canvas=_UNSET):
        """Create-or-reuse the ONE context of this window, subscribe the
        document-routing observer on a fresh one, and make sure the context is
        attached to the window's current canvas and the document is routed.

        The window's ``activeView()`` is resolved here and handed to the context
        as the ``view`` argument — that is the ONLY route to a document size
        (``Canvas`` has no size getters), so it must reach the
        context on every attach. The docker's ``canvasChanged`` arrives here
        through the ``_UNSET`` canvas too: it is a payload-free WAKE-UP and the
        window's own ``activeView() -> canvas()`` is resolved below, so the bound
        canvas is by construction this window's own (:meth:`handle_canvas_changed`).
        For an already-created context the rebind decision is the proven-canvas-
        widget change of the block below, keyed by
        :meth:`_locate_widget` and the context's own attached ``widget``."""
        context = self._holders.context(key)
        view = guarded_call(window, "activeView")
        if canvas is _UNSET:
            canvas = guarded_call(view, "canvas")
        if context is None:
            context = self._context_factory(self._service)
            self._holders.add_context(key, context)
            self._install_prune_hook(key)
            self._install_view_observer(key, window)
            context.bind(window, view, canvas)
        elif canvas is not None:
            # Re-attach ONLY when the canvas WIDGET provably changed. The stable identity is the
            # widget located from the window (:meth:`_locate_widget`) — the
            # Qt object the overlay is parented to — NEVER anything read off the
            # libkis ``Canvas`` wrapper: ``view.canvas()`` is a fresh Python object
            # per call (:func:`plugin_support.context_key` documents exactly this
            # for the window wrapper and pybind11 repeats it for the canvas), and
            # real ``libs/libkis/Canvas.h`` exposes NO widget getter at all, so the
            # previous ``canvas.canvasWidget()`` key could never work on a real
            # host (it degraded to wrapper identity, i.e. always rebound).
            # The change has to be PROVEN: ``located`` non-None (an unlocatable /
            # transient widget graph means "cannot tell") AND ``located`` differs
            # from the attached widget — ``current`` None is the never-attached
            # startup context, which no earlier event could ever retry.
            # Every other combination leaves the overlay alone — including
            # ``canvas is None``, already excluded by the guard above, so a
            # transient unreadable view never tears a working overlay down
            # and a wake-up for the widget the context
            # already has is a no-op instead of overlay churn. Both identities are
            # logged so a field problem ("why did/did not it rebind?") is
            # diagnosable from the plugin log alone.
            located = self._locate_widget(key)
            current = getattr(context, "widget", None)
            self._install_view_observer(key, window)
            if located is None:
                logger.debug(
                    "plugin: no canvas widget locatable for window %r; neither the "
                    "overlay nor its sources are touched (cannot tell is not a change)",
                    key,
                )
            elif located is not current:
                logger.debug(
                    "plugin: the canvas widget of window %r changed (%r -> %r); "
                    "rebinding its overlay",
                    key,
                    current,
                    located,
                )
                context.rebind(window, view, canvas)
            else:
                logger.debug(
                    "plugin: the canvas widget of window %r is unchanged (%r); the "
                    "overlay stays as it is and only the source objects are refreshed",
                    key,
                    located,
                )
                context.refresh_sources(window, view, canvas)
        self._activate_document(key, window)
        return context

    def _locate_widget(self, key):
        """The canvas widget currently located for the window identity *key* (the
        ``qwindow`` — the object the locator walks), or ``None`` when it cannot be
        located.

        ``None`` is a DELIBERATE answer, not a failure: :meth:`_attach_window`
        reads it as "cannot tell, so do not touch a working overlay". The production resolver
        (:func:`composition_grid.canvas.canvas_locator.find_canvas_widget`) is
        itself exception-free, but an injected one inherits no such contract, so
        the call is guarded and the swallow logged — no empty ``except``.

        The fallback of :func:`plugin_support.context_key` (a window that answers
        no ``qwindow()`` is keyed by itself) simply means the locator is handed a
        non-``QWidget`` and answers ``None`` with its own log line: no proven
        change, no rebind. Honest, and visible in the log."""
        try:
            return self._widget_resolver(key)
        except Exception as exc:  # noqa: BLE001 — a widget graph can be mid-teardown
            logger.debug(
                "plugin: locating the canvas widget of %r failed: %r (treated as "
                "unlocatable; the overlay is left alone)",
                key,
                exc,
            )
            return None

    def _prune_window(self, key) -> None:
        """The lifecycle END of one main window.

        Strong-reference holding makes retention necessary; without a prune it becomes a leak: the
        context (with its overlay), every partial capturing the dead libkis
        ``Window`` in the connection holder and every action of that window would
        stay referenced for the whole application session, so the holders grew
        with the number of windows Krita had ever had instead of the number it
        has. Connected on the ``destroyed`` signal of the window's qwindow — the
        only teardown signal libkis/Qt documents for a main window — this
        unbinds the context (disconnecting its Canvas/Window subscriptions and
        disposing the overlay) and lets :class:`WindowHolders` drop that window's
        share of every holder. Idempotent: a repeat emission finds nothing.

        RESIDUAL, deliberately not handled here (a manual-check item): the
        per-document models of documents CLOSED inside a still-running window —
        ``GridStateService._grids`` (each grid holding one reference to the service's
        own ``_notify``) — and the contexts' ``on_change`` subscriptions, which the
        service can never revoke (there is no unsubscribe API; after ``unbind`` the callback is a no-op, which is the
        mitigation). libkis exposes NO document-close signal: libkis documents only
        ``Window.activeViewChanged`` and the Canvas signals, so nothing is
        invented here and :meth:`GridStateService.remove` keeps its test-pinned
        behaviour for callers that DO know a document is gone."""
        context = self._holders.release(key)
        self._prune_hooked.discard(key)
        self._view_observers.pop(key, None)
        if context is not None:
            unbind = getattr(context, "unbind", None)
            if callable(unbind):
                try:
                    unbind()
                except Exception:  # noqa: BLE001 — teardown must not escape a signal
                    logger.exception(
                        "plugin: unbinding the context of a destroyed window failed"
                    )
        logger.debug(
            "plugin: pruned one destroyed window — %d context(s) and %d action(s) left",
            self._holders.window_count(),
            len(self._holders.actions()),
        )

    def _on_active_view(self, key) -> None:
        """``activeViewChanged`` for this window: re-ROUTE the document onto the
        service, through the owned wrapper of the stable key (the observer holds
        no Window of its own). The context rebinds on the very same signal by
        itself (``canvas_context``), so rebinding here too would only churn a
        working overlay — the extension deliberately stays out of it."""
        window = self._owned_window(key)
        if window is None:
            logger.info(
                "plugin: activeViewChanged for a window that is no longer owned — "
                "nothing is routed (degradation)"
            )
            return
        self._activate_document(key, window)

    def _context_doc_size(self, context):
        """The context's document size in DOCUMENT px, re-read through its view
        first, or ``None`` when nothing is bound/answerable.

        The re-read is the point: a document can be routed
        onto an already-bound context without any rebind (the docker's
        ``canvasChanged`` for the canvas the context already has), so the stored
        extent may still describe the PREVIOUS document. Duck-typed and guarded:
        a context without the seam (a minimal double) simply reports what it
        holds, and a raising one degrades to ``None`` with a log."""
        if context is None:
            return None
        refresh = getattr(context, "refresh_document_size", None)
        if callable(refresh):
            try:
                refresh()
            except Exception as exc:  # noqa: BLE001 — a host getter never breaks an action
                logger.debug(
                    "plugin: refreshing the document size failed: %r (using the stored one)",
                    exc,
                )
        doc_size = getattr(context, "doc_size", None)
        if not isinstance(doc_size, tuple) or len(doc_size) != 2:
            return None
        return doc_size

    def _activate_document(self, key, window) -> None:
        """Ensure + activate this window's current document on the shared
        service, restoring a brand-new container from the document's own
        annotation.

        Routing creates NO grid of its own (:meth:`GridStateService.ensure`
        makes an empty container) and neither does an annotation-less file: a
        document ends up with grids only from its own annotation or from the
        artist pressing **Add grid**.

        Without an active view/document, or while no view -> Document has
        answered a size, nothing is activated: every action then degrades to a
        logged no-op and the grid stays hidden. The document is
        asked for its per-document key (:func:`plugin_support.document_key`:
        ``name()`` or ``fileName()``) and — inside the
        context, on the read-only ``View -> Document`` route — for its
        ``width()``/``height()``; the document object itself is remembered in
        :attr:`_documents` because that is the route the per-document
        annotation travels. No other Document mutation API is ever called
        (see the module docstring)."""
        document = guarded_call(guarded_call(window, "activeView"), "document")
        doc_id = document_key(document)
        if doc_id is None:
            logger.info(
                "plugin: this window has no identifiable document (both name() and "
                "fileName() answered empty); the grid stays hidden and the actions are "
                "no-ops (degradation)"
            )
            return
        if document is not None:
            self._documents[doc_id] = document
        context = self._holders.context(key)
        doc_size = self._context_doc_size(context)
        if doc_size is None:
            logger.info(
                "plugin: document %r not activated — no view -> Document size is "
                "readable, so the default element cannot be built",
                doc_id,
            )
            return
        is_new_container = not self._service.is_routed(doc_id)
        should_restore = is_new_container and doc_id not in self._restored_docs
        if should_restore:
            # Claimed BEFORE the work: a partially failed restore must not be
            # retried on every later wake-up (a retried read would also churn
            # the notification stream).
            self._restored_docs.add(doc_id)
        # ONE window, ONE batch, ONE notification — and the restore INSIDE the
        # batch. Both halves of that are load-bearing:
        #
        # * notifying with the freshly ensured DEFAULTS would let the persistence
        #   subscribers store those defaults over the very state the restore is
        #   about to read (the kritarc last-used entry especially: the document
        #   would then never come back as saved), and would write an annotation
        #   for a grid nobody changed. Deferring every notification to the batch
        #   exit makes the whole activation ONE change — and one save of the
        #   RESTORED state.
        # * the suppression window wraps the batch exit as well, which is what
        #   keeps an opened file un-dirtied: ``set_active`` switching
        #   documents notifies even for a document that already has grids, so an
        #   activation must never be a reason to write.
        with suppressed_writes():
            with self._service.batched_notifications():
                self._service.set_active(doc_id)
                self._service.ensure(doc_id, doc_size[0], doc_size[1])
                if should_restore:
                    self._restore_new_container(document, doc_id)

    def _restore_new_container(self, document, doc_id: str) -> None:
        """The one restore of one freshly routed container, inside the caller's
        activation batch with writes suppressed
        (:meth:`_activate_document` holds both).

        Reading is the cheap half and never touches the file: the document's own
        ``composition_grid`` annotation wins (every grid, in order, with the
        selection), else the container simply stays empty. A raising store,
        annotation or service call is logged and the document stays as it is."""
        try:
            if restore_document_state(self._service, document, doc_id):
                # The FILE owns this container's state, so from now on every
                # mutation of the document is worth writing back.
                self._annotation_docs.add(doc_id)
        except Exception:  # noqa: BLE001 — a broken restore never breaks routing
            logger.exception(
                "plugin: restoring the grid state of %r failed; the document keeps "
                "its defaults",
                doc_id,
            )

    def _save_grid_annotation(self) -> None:
        """Save subscriber: one notification, one write of the
        ACTIVE document's WHOLE grid container into THAT document's annotation.

        Four rules decide everything here. Only the ACTIVE document is written
        — an unchanged document keeps the annotation it already has, so a
        per-document state never leaks into a neighbour. Nothing is written
        while a restore suppression window is open
        (:func:`composition_grid.plugin_document_state.suppressed_writes`),
        which is what keeps an opened file un-dirtied. A document that has
        NEVER stored an annotation stays annotation-less while it has no grids:
        opening (or creating) a file writes nothing into it, and the first
        mutation that has something to store is what introduces the annotation.
        And the document object is taken from :attr:`_documents` rather than
        from ``Krita.instance().activeDocument()``: the extension routed that
        id, so it owns the only reference that proves which object the id meant.
        ``write_grid_annotation`` never raises outward; a raising subscriber
        would only be logged by the service's fan-out, so this method stays a
        plain guarded call."""
        doc_id = self._service.active_doc_id
        if doc_id is None:
            return
        if not self._service.is_routed(doc_id):
            return
        document = self._documents.get(doc_id)
        if document is None:
            logger.debug(
                "plugin: no document object routed for %r; its grid state is not "
                "written into any file",
                doc_id,
            )
            return
        if not self._service.grids(doc_id) and doc_id not in self._annotation_docs:
            return  # an empty document that never stored anything needs no annotation
        write_grid_annotation(
            document, self._service.grids(doc_id), self._service.selected_grid_id(doc_id)
        )
        self._annotation_docs.add(doc_id)

    # ------------------------------------------------------------ docker side

    def doc_size_provider(self):
        """Zero-arg provider handed to the docker (its Reset button): the
        document size (document px) of the resolved window's canvas context,
        ``None`` when nothing is bound — which ``GridPanel`` treats as a logged
        no-op."""
        context = self._resolved_context(None)
        doc_size = self._context_doc_size(context)
        if doc_size is None:
            # The panel asks for a size only when it needs one (Reset): a stale
            # startup answer must not be the last word.
            self.ensure_routed()
            doc_size = self._context_doc_size(self._resolved_context(None))
        return doc_size

    def handle_canvas_changed(self, canvas) -> None:
        """``DockWidget.canvasChanged`` route (wired through
        ``make_panel_docker_class``) — a PAYLOAD-FREE wake-up.

        The delivered *canvas* is deliberately NOT the thing we bind. The repo's
        own model says so: :func:`composition_grid.plugin_support.context_key`
        documents that the libkis ``Window`` wrapper is a fresh Python object per
        ``Krita.activeWindow()`` call, and pybind11 by-value returns repeat that
        for ``View`` and ``Canvas`` — so a freshly resolved ``view.canvas()`` is a
        DIFFERENT object every call and can NEVER be identity-equal to this
        payload. Comparing their wrapper identity (the previous ownership gate) is
        unsound: it would answer "not this window's own" on every real invocation
        and silently kill the resetCanvas / external-rebind recovery channel
        (the canvasChanged / external-rebind recovery channel).

        So the active window's OWN view/canvas is re-resolved here
        (``window.activeView()`` -> ``view.canvas()``) and routed through
        :meth:`_attach_window` with the ``_UNSET`` canvas: the canvas that gets
        bound is, BY CONSTRUCTION, the window's own — a background docker's
        foreign canvas cannot be attached without any identity comparison at all.
        :meth:`_attach_window` then rebinds only when the canvas WIDGET located
        for that window (the output of
        :func:`composition_grid.canvas.canvas_locator.find_canvas_widget`, the Qt
        object the overlay is parented to) is proven different from the widget the
        context is attached to — a libkis ``Canvas`` exposes no widget getter of
        its own, so nothing read off the canvas is stable across the fresh
        wrappers. A wake-up whose located widget is unchanged is therefore a
        logged no-op, and one for a window whose own
        active view is momentarily unreadable degrades to a logged skip with the
        working overlay left intact."""
        window = self._active_window()
        if window is None:
            logger.info("plugin: canvasChanged without a resolvable active window; ignored")
            return
        logger.info(
            "plugin: canvasChanged wake-up — re-resolving the active window's own "
            "active view/canvas (the delivered wrapper is ignored as a fresh object)"
        )
        self._attach_window(context_key(window), window, _UNSET)

    def _resolved_context(self, window):
        """Context lookup by stable window identity with the single-window
        fallback of a v1 session: *window*'s qwindow, else
        ``Krita.instance().activeWindow()``'s context, else the sole context when
        exactly one window is wired, else ``None``."""
        for candidate in (window, self._active_window()):
            if candidate is not None:
                found = self._holders.context(context_key(candidate))
                if found is not None:
                    return found
        contexts = self._holders.contexts()
        if len(contexts) == 1:
            return contexts[0]
        if contexts:
            logger.debug(
                "plugin: %d windows are wired and the active one did not resolve; "
                "no context route",
                len(contexts),
            )
        return None

    def _active_window(self):
        """``Krita.instance().activeWindow()`` duck-typed and guarded: outside a
        running Krita (or mid-teardown) it answers ``None``."""
        try:
            return krita_module().Krita.instance().activeWindow()
        except Exception as exc:  # noqa: BLE001 — headless/absent libkis is normal here
            logger.debug("plugin: active window unavailable: %r", exc)
            return None

    # ------------------------------------------------------------ persistence
    #
    # The restore path itself moved to
    # :mod:`composition_grid.plugin_settings`; the entry only
    # triggers it, once per document, from :meth:`_activate_document`.


def register_plugin(instance=None):
    """Create the extension, hand it to Krita and register the docker factory —
    the standard pykrita registration ritual. Called from
    ``composition_grid/__init__.py`` only when the ``krita`` module is reachable,
    i.e. inside a running Krita. The extension then goes into
    :data:`HELD_PLUGINS` because Krita's own reference may not be strong."""
    app = instance if instance is not None else krita_module().Krita.instance()
    extension = CompositionGridExtension(app, service=shared_state_service())
    try:
        app.addExtension(extension)
    except Exception:  # noqa: BLE001 — a refused registration stays logged, not fatal
        logger.exception("plugin: addExtension(%r) failed", EXTENSION_ID)
    extension.create_docker()
    HELD_PLUGINS.append(extension)
    logger.info(
        "plugin: registered extension %r with %d actions and docker %r",
        EXTENSION_ID,
        len(ACTION_IDS),
        DOCKER_ID,
    )
    return extension
