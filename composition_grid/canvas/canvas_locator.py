"""Locate the Krita canvas widget from the main window (Qt only).

WHY this module exists
======================

The overlay must be parented to THE widget Krita renders the
document into — the viewport of the scroll area inside the active MDI
subwindow. The path from what the plugin receives (``Krita.instance().
activeWindow().qwindow()``, a ``QMainWindow``) to that widget is proven by
community practice (the
"Canvas Render — How to?" thread) and is, KISS:

    qwindow (QMainWindow)
      -> findChild(QMdiArea)                 # subwindow workspace
      -> QMdiArea.activeSubWindow()          # the focused document view
      -> subwindow.findChild(QAbstractScrollArea)   # KisCanvas2 canvas holder
      -> scrollArea.viewport()               # WA_TransparentForMouseEvents OK

Caveat: Krita can run subwindow/floating canvases where
``findChild(QMdiArea)`` answers ``None`` — and ``activeSubWindow()`` is
``None`` whenever no view is focused. Those situations are DEGRADATION, not
failure: the locator must never raise outward. It logs every fallback step
instead, and finally returns ``None`` so callers hide the grid ("сетка
скрыта"). A documented lenient fallback (window-wide
``findChild(QAbstractScrollArea)`` → ``viewport()``) is tried before giving
up, because a scroll area may still exist under the window even when the MDI
chain stalls (explored in ``tests/canvas/test_canvas_locator_qt.py``, which
documents which outcome each fixture geometry produces).

* Lenient fallback nuance: with no active subwindow (or an empty MDI area),
  the window-level ``findChild(QAbstractScrollArea)`` typically answers the
  MDI AREA's own ``viewport()`` — ``QMdiArea`` IS a ``QAbstractScrollArea``,
  so ``findChild`` (BFS) reaches it before any inner canvas content.

Krita-window caveats pinning the defensive style here:

* ``KisCanvas2::resetCanvas()`` can swap the canvas widget —
  therefore callers re-invoke :func:`find_canvas_widget` per bind/rebind
  instead of caching its result forever.
* Widget ``objectName``s are unreliable in Krita — the chain relies on Qt
  CLASS relationships only (``QMdiArea`` / ``QAbstractScrollArea``).
* ``findChild`` is recursive, so a single window-level ``findChild`` covers
  the proven ``centralWidget().findChild(QMdiArea)`` step while also tolerating
  any other (non-central) embedding Krita may introduce.
* All Qt access goes through :mod:`composition_grid.qt_compat` (the plugin's
  only legal Qt route; raw ``PyQt5``/``PyQt6``/``krita`` imports are banned).
* Logging only — the module never prints; every fallback reason goes to the
  ``composition_grid`` logger (DEBUG for chain steps, final outcomes on
  INFO).
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from composition_grid import qt_compat

logger = logging.getLogger("composition_grid")

_T = TypeVar("_T")


def _guarded(step: str, fn: Callable[[], _T]) -> _T | None:
    """Run one locator step, converting ANY failure into ``None`` + a WARNING
    log with the reason. Node of the "no exceptions outward" pin: widget
    graphs can be torn down between calls inside Krita, so a raising
    ``AttributeError``/``RuntimeError`` here is data, not a crash."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — plugin host must never crash via the locator
        logger.warning(
            "canvas_locator: step %r failed, treated as not found: %r", step, exc
        )
        return None


def find_canvas_widget(
    window: qt_compat.QtWidgets.QWidget | qt_compat.QtCore.QObject | None,
) -> qt_compat.QtWidgets.QWidget | None:
    """Return the canvas widget — the ``viewport()`` of the active document
    view under *window* (Krita's ``qwindow``) — or ``None`` when it cannot be
    resolved. NEVER raises outward (degradation, not crash).

    Log branch map (the module prints nothing, ever):

    * no Qt binding (pure env) — INFO one-liner, ``None``;
    * ``None`` / non-``QWidget`` *window* — INFO one-liner, ``None``;
    * primary-chain stall (no ``QMdiArea`` / no active subwindow / no scroll
      area in it / no viewport) — DEBUG per reason, then the lenient fallback
      runs;
    * ``_guarded`` step failures (any exception) — WARNING with the reason,
      the step counts as "not found";
    * lenient fallback success — INFO (flags that the primary chain didn't
      match the hierarchy it was built for);
    * primary-chain success — DEBUG (the normal Krita path, stay quiet);
    * nothing found at all — INFO "grid will stay hidden" outcome.
    """
    if not qt_compat.QT_AVAILABLE:
        logger.info(
            "canvas_locator: Qt binding unavailable; canvas widget not located"
        )
        return None

    QtWidgets = qt_compat.QtWidgets  # valid only under QT_AVAILABLE

    if window is None:
        logger.info(
            "canvas_locator: no main window provided; canvas widget not located"
        )
        return None

    is_widget = _guarded(
        f"isinstance(QWidget) check on {type(window).__name__}",
        lambda: isinstance(window, QtWidgets.QWidget),
    )
    if not is_widget:
        logger.info(
            "canvas_locator: provided window is not a QWidget (%s); canvas "
            "widget not located",
            type(window).__name__,
        )
        return None

    # --- primary chain (proven community path) ------------------------------
    mdi = _guarded(
        "findChild(QMdiArea)", lambda: window.findChild(QtWidgets.QMdiArea)
    )
    if mdi is None:
        logger.debug(
            "canvas_locator: no QMdiArea under the window (subwindow/floating "
            "canvas mode); falling back leniently"
        )
    else:
        sub = _guarded("QMdiArea.activeSubWindow()", lambda: mdi.activeSubWindow())
        if sub is None:
            logger.debug(
                "canvas_locator: QMdiArea present but no active subwindow "
                "(no focused view); falling back leniently"
            )
        else:
            scroll = _guarded(
                "subwindow.findChild(QAbstractScrollArea)",
                lambda: sub.findChild(QtWidgets.QAbstractScrollArea),
            )
            if scroll is None:
                logger.debug(
                    "canvas_locator: active subwindow holds no QAbstractScrollArea; "
                    "falling back leniently"
                )
            else:
                viewport = _guarded(
                    "scrollArea.viewport()", lambda: scroll.viewport()
                )
                if viewport is None:
                    logger.debug(
                        "canvas_locator: scroll area %r has no viewport; "
                        "falling back leniently",
                        scroll,
                    )
                else:
                    logger.debug(
                        "canvas_locator: primary chain resolved canvas "
                        "viewport %r inside %r",
                        viewport,
                        scroll,
                    )
                    return viewport

    # --- documented lenient fallback (degradation path) ----------------------
    scroll = _guarded(
        "lenient window.findChild(QAbstractScrollArea)",
        lambda: window.findChild(QtWidgets.QAbstractScrollArea),
    )
    if scroll is None:
        logger.info(
            "canvas_locator: no canvas widget resolvable under the window — "
            "grid will stay hidden (subwindow mode degradation)"
        )
        return None
    viewport = _guarded("lenient scrollArea.viewport()", lambda: scroll.viewport())
    if viewport is None:
        logger.info(
            "canvas_locator: leniently found scroll area %r has no viewport — "
            "grid will stay hidden (degradation)",
            scroll,
        )
        return None
    logger.info(
        "canvas_locator: primary MDI chain did not match; lenient fallback "
        "resolved canvas viewport %r inside %r",
        viewport,
        scroll,
    )
    return viewport
