"""Host-adapter helpers for the plugin entrypoint.

Why this module exists: :mod:`composition_grid.plugin_composition_grid` must
stay lean and single-responsibility (global constraint: short files, one
responsibility each). Everything in here is a *host adapter*: the plumbing that
talks to a foreign object graph (libkis + Qt) defensively, with no knowledge of
grids, models or view state.

Contract of every adapter here: **never raise outward** — Krita's Python API has
no stability guarantees for a plugin (wrappers appear and disappear between
calls, optional signals differ between versions), so a failure becomes a logged
record on the ``composition_grid`` logger and an honest ``None``/``False``
value. No empty ``except`` anywhere.

The lazy-import rule lives here on purpose: ``plugin_composition_grid`` imports
``krita`` only through :func:`krita_module` / :func:`extension_base`, mirroring
the sanctioned ``model.settings_store.KritaTransport`` pattern, so the whole
package stays importable (and testable) without libkis. Qt is never imported by
either module: the plugin's only legal Qt route is
:mod:`composition_grid.qt_compat`, used by the canvas/ui layers below.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("composition_grid")

__all__ = [
    "HeadlessExtensionBase",
    "context_key",
    "document_key",
    "extension_base",
    "guarded_call",
    "icon_krita",
    "krita_module",
    "never_raises_slot",
]


def document_key(document):
    """The stable per-document identity the grid state is keyed by, or ``None``.

    ``Document.name()`` is NOT it: a live Krita answered ``''`` for a document
    opened from a file, and requiring a non-empty name meant the document was
    never routed — the docker stayed display-only and every action a no-op.
    ``fileName()`` is what the same host actually fills in, so the key is
    ``name() or fileName()``, in that order, and the first non-empty string
    wins.

    Both empty is left as ``None`` rather than invented: a synthesized key
    (size, pointer id) would silently share or lose a document's grid, and an
    unhidden "no key" is the honest degradation.
    """
    for getter in ("name", "fileName"):
        value = guarded_call(document, getter)
        if isinstance(value, str) and value:
            return value
    return None


class HeadlessExtensionBase:
    """Stand-in base for ``CompositionGridExtension`` when libkis is absent.

    Mirrors only ``krita.Extension``'s constructor contract (it is created with
    the Krita instance). Inside a running Krita :func:`extension_base` resolves
    the real base instead — the headless one exists so that importing the entry
    (and the whole pure test suite) never needs libkis."""

    def __init__(self, instance=None) -> None:
        self._instance = instance


def krita_module():
    """The libkis ``krita`` module, imported LAZILY (module docstring).

    Inside a running Krita this is the real API; in
    ``tests/test_plugin_smoke_qt.py`` it is the fake module the test injects
    into ``sys.modules`` — that injection is the sanctioned seam, so the entry
    never needs a live Krita to be importable or testable."""
    import krita

    return krita


def icon_krita(name: str):
    """``Krita.instance().icon(name)`` — a host theme icon, guarded (module
    contract: never raise outward).

    Answered as-is (a libkis ``QIcon``), because the only consumer is the docker's
    injected ``icon_provider``: the ui layer receives a value and never imports
    krita to build it. ``None`` means "no such icon here" — no libkis, no such
    method, a raising wrapper — and the caller falls back to its own glyph."""
    try:
        icon = krita_module().Krita.instance().icon(name)
    except Exception as exc:  # noqa: BLE001 — a host lookup is never worth a crash
        logger.debug("plugin_support: krita icon %r unavailable: %r", name, exc)
        return None
    return icon


def extension_base():
    """``krita.Extension`` when it is reachable, else :class:`HeadlessExtensionBase`.

    Resolved at class-definition time of the entry module (a Python base list is
    evaluated there and libkis offers no later hook). The headless fallback is
    logged at INFO: it is the normal state for a test run, and a mistake would
    be registering a plugin outside Krita, which :func:`krita_module` would
    already have made loudly impossible."""
    try:
        from krita import Extension

        return Extension
    except Exception as exc:  # noqa: BLE001 — "no libkis here" is the normal case
        logger.info(
            "plugin_support: krita.Extension unavailable (%r); the entry is "
            "imported headlessly, registration needs a running Krita",
            exc,
        )
        return HeadlessExtensionBase


def guarded_call(obj, name: str):
    """``obj.name()`` duck-typed and guarded: a missing attribute, a non-callable
    attribute, a raising wrapper or a deleted one all answer ``None`` — the lookup
    is guarded too, because a deleted sip wrapper raises on ATTRIBUTE access."""
    if obj is None:
        return None
    try:
        getter = getattr(obj, name, None)
    except RuntimeError as exc:
        logger.debug(
            "plugin_support: %s.%s lookup failed: %r", type(obj).__name__, name, exc
        )
        return None
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception as exc:  # noqa: BLE001 — host objects may be mid-teardown
        logger.debug(
            "plugin_support: %s.%s() failed: %r", type(obj).__name__, name, exc
        )
        return None


def never_raises_slot(handler: Callable) -> Callable:
    """Wrap *handler* so a failure is a logged record, never an exception
    travelling back out through the Qt/libkis emission (PyQt re-raises out of
    ``emit`` by default, so an uncaught action error would surface inside
    Krita's own event handling)."""

    def _slot(*args):
        try:
            handler(*args)
        except Exception:  # noqa: BLE001 — a broken action must not take the UI down
            logger.exception(
                "plugin_support: handler %s failed (swallowed by design)",
                getattr(handler, "__name__", repr(handler)),
            )

    return _slot


def context_key(window):
    """Stable identity of a main window: the libkis ``Window`` wrapper is a
    fresh Python object per ``Krita.activeWindow()`` call, its ``qwindow()`` (the
    QMainWindow) is not — so the per-window holders must be keyed by the qwindow,
    falling back to *window* itself when it exposes no ``qwindow()`` (a raw
    QMainWindow, or a test fixture). Unhashable answers degrade to the window
    itself rather than raising out of a dict lookup."""
    qwindow = guarded_call(window, "qwindow")
    if qwindow is None:
        return window
    try:
        hash(qwindow)
    except TypeError:
        logger.debug("plugin_support: qwindow %r is unhashable; keying by window", qwindow)
        return window
    return qwindow
