"""The action factory/binding seam.

The entry module owns ONE responsibility — the ``krita.Extension`` lifecycle and
the window wiring. Everything needed to turn a ``(action id, English text,
handler name)`` spec into a connected action on a libkis window is host
plumbing, and it lives here.

No Qt binding and no ``krita`` import: the window and its actions are duck-typed
host objects (a real Krita ``Window``/``QAction`` or a test fake — the code
cannot tell and must not care), and every failure is logged and degraded into
"this action was not created" instead of raising out of
``createActions``, which Krita calls during window setup.

THE ARITY CONTRACT (the reason the binding looks like this):
libkis' ``QAction.triggered`` is ``triggered(bool checked = false)``, so Qt
invokes the connected slot with the checked flag as ONE positional argument,
NEVER with none. The slot is therefore the handler *partially bound* to its
window, and the handler itself must still absorb the flag (``def
_action_toggle(self, window=None, *args)``) — a one-parameter handler would be
called with two arguments, raise ``TypeError`` inside
:func:`~composition_grid.plugin_support.never_raises_slot` and leave a menu
entry that silently does nothing inside Krita.
"""

from __future__ import annotations

import logging
from typing import Callable, Mapping, Sequence

from composition_grid.plugin_support import guarded_call, never_raises_slot

logger = logging.getLogger("composition_grid")

__all__ = ["create_window_actions"]


def create_window_actions(
    window,
    specs: Sequence[tuple[str, str, str]],
    handlers: Mapping[str, Callable],
    menu_location: str,
) -> list:
    """Create and connect every action of *specs* on *window*.

    ``specs`` is the ``(action_id, English text, handler name)`` sequence,
    ``handlers`` maps a handler name to the callable to connect (the entry hands
    in the window-bound partials), ``menu_location`` is the libkis menu path.
    Answers the list of ``(action, slot)`` pairs that really exist, in spec
    order — the caller must keep them referenced for as long as the window
    lives (Krita's own references may not keep a Python slot
    alive, and a collected slot is a dead connection).

    A single failing action is logged and skipped: one broken menu entry must
    not cost the other three, and must not break window setup."""
    created = []
    for action_id, text, handler_name in specs:
        handler = handlers.get(handler_name)
        if handler is None:
            logger.warning(
                "plugin_actions: no handler %r for action %r; not created",
                handler_name,
                action_id,
            )
            continue
        pair = _create_one(window, action_id, text, handler, menu_location)
        if pair is not None:
            created.append(pair)
    return created


def _create_one(window, action_id: str, text: str, handler: Callable, menu_location: str):
    """One ``window.createAction(id, text, menuLocation)`` plus its trigger
    connection; answers ``(action, slot)`` or ``None`` when either step failed."""
    try:
        action = window.createAction(action_id, text, menu_location)
    except Exception:  # noqa: BLE001 — a hostile/absent window must not break setup
        logger.exception("plugin_actions: createAction(%r) failed", action_id)
        return None
    if action is None:
        logger.warning("plugin_actions: window.createAction(%r) answered None", action_id)
        return None
    # The window is bound into the handler; Qt still adds the checked flag, so
    # the handler must tolerate one more positional argument (module docstring).
    slot = never_raises_slot(handler)
    signal = getattr(action, "triggered", None)
    if signal is None or not hasattr(signal, "connect"):
        # some libkis shapes hand back an Action wrapper, not the QAction
        signal = getattr(guarded_call(action, "qaction"), "triggered", None)
    if signal is None or not hasattr(signal, "connect"):
        logger.warning(
            "plugin_actions: action %r exposes no triggerable signal; it cannot work",
            action_id,
        )
        return None
    try:
        signal.connect(slot)
    except Exception:  # noqa: BLE001 — the host object may reject it
        logger.exception("plugin_actions: connecting action %r failed", action_id)
        return None
    return action, slot
