"""The per-window holder registry.

This plugin must keep STRONG references to everything it
connects on Krita's side — a collected slot is a dead connection and a collected
context is a collected overlay, the community-proven crash. Left unmanaged, the
same ownership is the leak this registry prevents: references keyed by a window
Krita has already destroyed were never released, so the holders grew with the
number of windows the session had ever had.

This module owns exactly that data and its lifecycle: the per-window contexts,
the per-window ``(action, slot)`` pairs and the signal connections, all keyed by
the STABLE window identity (:func:`composition_grid.plugin_support.context_key`,
i.e. the ``qwindow``) plus the single release operation. It knows nothing about
grids, canvases, documents or libkis — the entry decides WHAT is worth holding,
this decides how it is kept and when it leaves. Keeping it separate honours
the single-responsibility split: the entry stays the Extension lifecycle.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Tuple

logger = logging.getLogger("composition_grid")

__all__ = ["WindowHolders"]


def _disconnect(source, signal_name: str, slot) -> None:
    """Disconnect one recorded connection; a deleted host object is a DEBUG note."""
    try:
        signal = getattr(source, signal_name, None)
    except RuntimeError as exc:
        logger.debug(
            "plugin_holders: %s.%s lookup failed (deleted source): %r",
            type(source).__name__,
            signal_name,
            exc,
        )
        return
    if signal is None or not hasattr(signal, "disconnect"):
        logger.debug(
            "plugin_holders: %r is not disconnectable on %s",
            signal_name,
            type(source).__name__,
        )
        return
    try:
        signal.disconnect(slot)
    except Exception as exc:  # noqa: BLE001 — the source may be mid-teardown
        logger.debug("plugin_holders: disconnect of %r skipped: %r", signal_name, exc)


class WindowHolders:
    """Strong references for the live windows of one extension instance."""

    def __init__(self) -> None:
        self._contexts: Dict[object, object] = {}
        self._actions: Dict[object, List[tuple]] = {}
        # ``(source, signal_name, slot, window_key)`` — the key is what makes a
        # per-window release possible without touching the other windows'.
        self._connections: List[Tuple[object, str, Callable, object]] = []

    # ---------------------------------------------------------------- contexts

    def has_window(self, key) -> bool:
        """Is this window already wired (one context per window)?"""
        return key in self._contexts

    def context(self, key):
        """The context of *key*, or ``None``."""
        return self._contexts.get(key)

    def add_context(self, key, context) -> None:
        self._contexts[key] = context

    def contexts(self) -> tuple:
        """Every live context, in insertion order."""
        return tuple(self._contexts.values())

    # ----------------------------------------------------------------- actions

    def hold_actions(self, key, pairs) -> None:
        """Record the ``(action, slot)`` pairs created for *key* — the entry must
        keep them referenced for as long as the window lives (a
        collected slot is a dead connection)."""
        self._actions.setdefault(key, []).extend(pairs)

    def has_actions(self, key) -> bool:
        """Are the actions of *key* already created (a deferred attach may repeat)?"""
        return bool(self._actions.get(key))

    def actions(self) -> list:
        """Every held ``(action, slot)`` pair, in creation order."""
        return [pair for pairs in self._actions.values() for pair in pairs]

    # ------------------------------------------------------------- connections

    def connect(self, source, signal_name: str, slot, key) -> bool:
        """getattr-based connect of *slot* to *source*'s ``signal_name``, keeping
        source, slot and the owning window key together.

        An absent optional signal is a DEBUG note and ``False``, never an
        exception (libkis versions differ), and a signal that rejects the
        connection is logged as a WARNING and treated as absent."""
        try:
            signal = getattr(source, signal_name, None)
        except RuntimeError as exc:
            logger.debug(
                "plugin_holders: %s.%s lookup failed (deleted source): %r",
                type(source).__name__,
                signal_name,
                exc,
            )
            return False
        if signal is None or not hasattr(signal, "connect"):
            logger.debug(
                "plugin_holders: optional signal %r absent on %s; skipped",
                signal_name,
                type(source).__name__,
            )
            return False
        try:
            signal.connect(slot)
        except Exception as exc:  # noqa: BLE001 — the host object may reject it
            logger.warning(
                "plugin_holders: connecting %r failed: %r (treated as absent)",
                signal_name,
                exc,
            )
            return False
        self._connections.append((source, signal_name, slot, key))
        return True

    def connections(self) -> tuple:
        """Introspection for the holder contract (tests, teardown diagnostics)."""
        return tuple(self._connections)

    # ----------------------------------------------------------------- release

    def release(self, key) -> object:
        """Drop everything held for ONE window identity and answer its context
        (or ``None`` when the window was already gone — the release must be
        idempotent, a Qt ``destroyed`` emission can arrive more than once).

        Releasing a reference is NOT releasing a subscription: an emitter outlives
        its Python wrapper, so the slots of this key are disconnected before the
        bookkeeping goes, or a still-live host object would keep calling a handler
        whose window is gone.

        The caller is responsible for unbinding the returned context: this
        registry owns references, not canvas semantics."""
        context = self._contexts.pop(key, None)
        owned, kept = [], []
        for entry in self._connections:
            (owned if entry[3] is key else kept).append(entry)
        for source, signal_name, slot, _key in owned:
            _disconnect(source, signal_name, slot)
        self._connections = kept
        self._actions.pop(key, None)
        return context

    def disconnect_source(self, source) -> int:
        """Disconnect and forget every connection whose emitter IS *source*."""
        owned, kept = [], []
        for entry in self._connections:
            (owned if entry[0] is source else kept).append(entry)
        for source_, signal_name, slot, _key in owned:
            _disconnect(source_, signal_name, slot)
        self._connections = kept
        return len(owned)

    def window_count(self) -> int:
        """How many windows are wired right now (bounded by the live windows)."""
        return len(self._contexts)
