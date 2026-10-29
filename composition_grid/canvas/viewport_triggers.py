"""The Krita 5.3 repaint triggers — genuine Qt viewport events.

WHY THIS EXISTS (observed on live host Krita 5.3.4.1 / PyQt5):
libkis ``Canvas`` on 5.3.x emits NOTHING (``libs/libkis/Canvas.h`` at tag
v5.3.0-prealpha declares no signals; they are master/6.x), so an overlay that
only repainted on Canvas signals would never repaint at all. What Qt does emit
by itself, on the canvas widget, is the geometry change: the viewport belongs to
a ``QAbstractScrollArea`` (the locator's own DEBUG line "canvas viewport …
inside <QAbstractScrollArea>" proves the holder is reachable), so scrolling and
zooming the canvas arrive as scrollbar signals and as Wheel/Resize/Scroll/
ScrollPrepare events. What those four MISSED:
changes that move neither a scrollbar value nor a scrollbar range — the mirror
toggle, canvas rotation via the menu/N-key, a slider/keyboard zoom while the
document fits the viewport (range stays 0..0), zoom-to-cursor that keeps the
value — are announced by exactly one thing: the canvas viewport must PAINT to
show them, so ``QEvent.Paint`` on the viewport invalidates. The ``resetCanvas``
widget swap re-lays-out the holder, so ``QEvent.LayoutRequest`` on the located
scroll area does too. This module owns WHICH objects those are;
:mod:`composition_grid.canvas.canvas_context` owns WHEN they are wired and
released (its bind/unbind lifecycle).

NO FEEDBACK LOOP FROM THE PAINT TRIGGER: our overlay is a CHILD of the canvas
viewport, and the trigger only calls ``child.update()`` (through the context's
repaint callback) — scheduling a paint for the CHILD widget. Qt never re-sends
a Paint event to the parent because a child repaints, and the filter is
installed on the viewport, not on the overlay, so the overlay's own Paint
events are not even watched. A repaint therefore cannot re-enter the code that
requested it (verified with a real child widget in
``tests/canvas/test_viewport_triggers_qt.py``).

NO TIMERS, NO POLLING: every source here is an event
Qt emits on its own. Every Qt name comes through
:mod:`composition_grid.qt_compat` — including the event types, which is why
they were added there rather than referenced as ``QtCore.QEvent.Wheel`` here.
The filter returns ``False`` unconditionally: it invalidates, it never consumes
Krita's input. And it reads ONLY ``event.type()``: PyQt mis-wraps
``QScrollEvent``/``QScrollPrepareEvent`` (a prior probe observed heap corruption
when one was dereferenced), so no payload is ever touched.
"""

from __future__ import annotations

import logging
from typing import Callable, FrozenSet, Tuple

from composition_grid import qt_compat
from composition_grid.canvas.host_view_probe import callable_attr, guarded_call

logger = logging.getLogger("composition_grid")

__all__ = [
    "HOLDER_EVENT_NAMES",
    "HOLDER_EVENT_TYPES",
    "TRIGGER_EVENT_NAMES",
    "TRIGGER_EVENT_TYPES",
    "find_scroll_area",
    "install",
    "remove",
]

#: How far up the parent chain the canvas holder is looked for. A Krita viewport
#: sits two or three levels under the main window; the bound only guards against
#: a cyclic foreign widget graph, it is not a tuning knob.
_MAX_ANCESTOR_STEPS = 12

_TriggerHandle = Tuple[object, object, Callable]

#: Name strings of the two watched event sets. Qt-free on purpose: the
#: diagnostics print what is wired even when this branch is the pure one, and
#: the drift test pins that the names resolve through ``qt_compat`` to exactly
#: the frozensets the filters compare against.
TRIGGER_EVENT_NAMES = ("Paint", "Resize", "Scroll", "ScrollPrepare", "Wheel")
HOLDER_EVENT_NAMES = ("LayoutRequest",)

if qt_compat.QT_AVAILABLE:

    TRIGGER_EVENT_TYPES = frozenset(
        {
            qt_compat.Wheel,
            qt_compat.Resize,
            qt_compat.Scroll,
            qt_compat.ScrollPrepare,
            # The ONLY event an overlay can observe for
            # mirror/rotation/zoom-to-cursor changes (see module docstring).
            qt_compat.Paint,
        }
    )

    #: Events watched on the SCROLL-AREA HOLDER rather than the viewport:
    #: ``resetCanvas`` swaps the canvas widget and re-lays-out the holder.
    HOLDER_EVENT_TYPES: FrozenSet = frozenset({qt_compat.LayoutRequest})

    class _ViewportEventFilter(qt_compat.QtCore.QObject):
        """Ask for a repaint when the watched object is told its geometry, its
        scroll position or its painted content just changed. Which event types
        count is the constructor's ``event_types`` set (the viewport set vs.
        the holder set). Never raises outward, never consumes."""

        def __init__(
            self, on_trigger: Callable[[], None], event_types: FrozenSet
        ) -> None:
            super().__init__()
            self._on_trigger = on_trigger
            self._event_types = event_types

        def eventFilter(self, watched, event) -> bool:  # noqa: ARG002 — Qt signature
            try:
                if event.type() in self._event_types:
                    self._on_trigger()
            except Exception:  # noqa: BLE001 — an event filter must not crash Krita
                logger.exception(
                    "viewport_triggers: repaint trigger failed (swallowed by design)"
                )
            return False

else:  # pragma: no cover — pure (no PyQt) environment: no QObject to build
    TRIGGER_EVENT_TYPES = frozenset()
    HOLDER_EVENT_TYPES = frozenset()
    _ViewportEventFilter = None


def find_scroll_area(widget):
    """The first ancestor of *widget* answering both scrollbar getters.

    Duck-typed on purpose: that pair IS the ``QAbstractScrollArea`` surface, so
    no Qt class is dereferenced and a fake that owns scrollbars behaves exactly
    like the host here. ``None`` when the chain runs out (or is unwalkable) —
    the caller then logs and simply wires fewer triggers."""
    ancestor = guarded_call(widget, "parentWidget")
    steps = 0
    while ancestor is not None and steps < _MAX_ANCESTOR_STEPS:
        if (
            callable_attr(ancestor, "horizontalScrollBar") is not None
            and callable_attr(ancestor, "verticalScrollBar") is not None
        ):
            return ancestor
        ancestor = guarded_call(ancestor, "parentWidget")
        steps += 1
    return None


def scrollbars(scroll_area) -> "list[object]":
    """The two scrollbars of a holder found by :func:`find_scroll_area`
    (``[]`` when the argument is ``None`` or answers nothing)."""
    if scroll_area is None:
        return []
    bars = []
    for getter in ("horizontalScrollBar", "verticalScrollBar"):
        bar = guarded_call(scroll_area, getter)
        if bar is not None:
            bars.append(bar)
    return bars


def install(widget, on_trigger: Callable[[], None], event_types=None) -> "_TriggerHandle | None":
    """Put the repaint filter on *widget* (the canvas viewport, or — with the
    ``event_types`` of :data:`HOLDER_EVENT_TYPES` — its scroll-area holder);
    answer the handle :func:`remove` needs, or ``None`` when it could not be
    installed (each reason is a DEBUG note, never an exception — the triggers
    are an optimisation of correctness that the computed ViewState already
    provides)."""
    if _ViewportEventFilter is None:
        logger.debug(
            "viewport_triggers: Qt unavailable; viewport repaint filter not installed"
        )
        return None
    install_filter = callable_attr(widget, "installEventFilter")
    remove_filter = callable_attr(widget, "removeEventFilter")
    if install_filter is None or remove_filter is None:
        logger.debug(
            "viewport_triggers: canvas widget %s accepts no event filter; wheel/"
            "resize/paint repaint triggers not wired",
            type(widget).__name__,
        )
        return None
    viewport_filter = _ViewportEventFilter(
        on_trigger, TRIGGER_EVENT_TYPES if event_types is None else event_types
    )
    try:
        install_filter(viewport_filter)
    except Exception as exc:  # noqa: BLE001 — a foreign widget may refuse it
        logger.debug(
            "viewport_triggers: installing the viewport event filter failed: %r", exc
        )
        return None
    # The filter must stay referenced while installed, which is
    # exactly what handing the handle back to the context achieves.
    return (widget, viewport_filter, remove_filter)


def remove(handle: "_TriggerHandle | None") -> None:
    """Undo one :func:`install` (idempotent, exception-free: the viewport may
    already be gone during a canvas teardown)."""
    if handle is None:
        return
    _widget, viewport_filter, remove_filter = handle
    try:
        remove_filter(viewport_filter)
    except Exception as exc:  # noqa: BLE001 — teardown must not escape
        logger.debug(
            "viewport_triggers: removing the viewport event filter skipped: %r", exc
        )
