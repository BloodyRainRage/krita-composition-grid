"""Bind-time plausibility check for the located canvas widget.

WHY this module exists
======================

``canvas_locator.find_canvas_widget`` degrades to a documented LENIENT
fallback (window-wide ``findChild(QAbstractScrollArea).viewport()``) whenever
the MDI primary chain stalls — and its own module docstring states that in that
mode the result is typically the QMdiArea's OWN viewport or, worse, some
DOCK's scroll-area viewport. Parenting the overlay to such an impostor paints
the grid nowhere (invisible/clipped) and, inside an edit session, clears
``WA_TransparentForMouseEvents`` on a child of a FOREIGN widget — the overlay
then consumes mouse input over dock chrome. A sanity check on the located
widget is therefore required at bind; this module is that check, kept separate
so ``canvas_context`` stays cohesive (it only decides to degrade on the verdict)
and the rules are individually testable.

Rules (all plain Qt facts, duck-typed, NO Krita class anywhere):

* the widget must be a ``QWidget`` AND a CHILD — the overlay must be parented
  to the canvas widget; a childless top-level is rejected;
* NO ``QDockWidget`` ancestor — the walk up ``parentWidget()`` to the
  top-level window rejects exactly the docker scroll-area impostor of the
  lenient fallback;
* NO ``QMdiArea`` ancestor *unless* a ``QMdiSubWindow`` was passed first —
  ``QMdiArea`` is itself a ``QAbstractScrollArea``, so with no active
  subwindow (floating/subwindow mode) the lenient fallback answers the MDI
  AREA's own viewport: dock-free and large enough to clear every other rule,
  yet it is the MDI background, not a document canvas, so the doc->widget
  matrix would be wrong there;
* a MINIMUM SIZE floor: ``MIN_CANVAS_WIDGET_SIZE_PX`` is fixed at 24 px,
  imported from ``presentation.handle_layout.ROTATE_HANDLE_OFFSET_PX`` — the
  smallest area that can even host the handle layout (the rotate anchor alone
  rides 24 widget px beyond the element's top edge; "under 24 px on screen" is
  the tiny/degenerate threshold the ``effective_tolerance`` clamp implements).
  A real Krita canvas viewport is hundreds of px in both axes — anything below
  the floor is definitively NOT one (verified fixture numbers: an MDI canvas
  viewport resolves at 254x190 px even in a minimal offscreen window).

Never raises outward: every foreign-object probe failure becomes a REJECTION
with the reason (``canvas_context`` guarantees "no exception from bind"), and
all Qt access goes through :mod:`composition_grid.qt_compat` (raw bindings and
``krita`` imports are banned plugin-wide).
"""

from __future__ import annotations

import logging

from composition_grid import qt_compat
from composition_grid.presentation.handle_layout import ROTATE_HANDLE_OFFSET_PX

logger = logging.getLogger("composition_grid")

__all__ = ["MIN_CANVAS_WIDGET_SIZE_PX", "is_plausible_canvas_widget"]

MIN_CANVAS_WIDGET_SIZE_PX = ROTATE_HANDLE_OFFSET_PX  # 24.0 px — see module docstring
"""Smallest plausible canvas-widget edge, in widget px: below it the handle
layout cannot fit, so the widget cannot be a Krita canvas viewport."""


def is_plausible_canvas_widget(widget) -> "tuple[bool, str]":
    """Decide whether *widget* can plausibly BE the canvas viewport the
    overlay may parent to; answer ``(plausible, reason)`` where ``reason`` is
    ``""`` on acceptance and a concrete, human-readable explanation on
    rejection (the context logs it at INFO so a user can tell WHY the grid
    is hidden). Never raises — probe failures reject."""
    if not qt_compat.QT_AVAILABLE:
        return False, "Qt binding unavailable (qt_compat.QT_AVAILABLE is False)"
    QtWidgets = qt_compat.QtWidgets

    try:
        if not isinstance(widget, QtWidgets.QWidget):
            return False, (
                f"located object is not a QWidget ({type(widget).__name__})"
            )
        parent = widget.parentWidget()
    except Exception as exc:  # noqa: BLE001 — foreign/mid-teardown C++ object
        logger.debug("widget_plausibility: QWidget probe failed: %r", exc)
        return False, f"widget probe failed: {exc!r}"

    if parent is None:
        return False, (
            "widget is a childless top-level window; the overlay must be a "
            "CHILD of the canvas widget"
        )

    try:
        ancestor = parent
        subwindow_seen = False
        while ancestor is not None:
            if isinstance(ancestor, QtWidgets.QDockWidget):
                return False, (
                    "widget lives inside a QDockWidget — the lenient locator "
                    "fallback matched a docker's scroll area, not the canvas "
                    "viewport"
                )
            # ``QMdiArea`` IS a ``QAbstractScrollArea``, so the lenient
            # fallback answers ITS OWN viewport whenever no document
            # subwindow is active (floating/subwindow mode). Parenting the
            # overlay there would paint the grid over the MDI background with
            # the wrong doc->widget coordinates: only a chain that reached a
            # ``QMdiSubWindow`` first is a document canvas.
            if isinstance(ancestor, QtWidgets.QMdiSubWindow):
                subwindow_seen = True
            elif (
                isinstance(ancestor, QtWidgets.QMdiArea)
                and not subwindow_seen
            ):
                return False, (
                    "widget is the QMdiArea's own viewport (no QMdiSubWindow "
                    "in its parent chain) — the MDI background is not a "
                    "document canvas and has no document->widget mapping"
                )
            ancestor = ancestor.parentWidget()
        width = int(widget.width())
        height = int(widget.height())
    except Exception as exc:  # noqa: BLE001 — ancestor/size probe on a foreign graph
        logger.debug("widget_plausibility: ancestor/size probe failed: %r", exc)
        return False, f"widget probe failed: {exc!r}"

    if width < MIN_CANVAS_WIDGET_SIZE_PX or height < MIN_CANVAS_WIDGET_SIZE_PX:
        return False, (
            f"widget is {width}x{height} px — below the "
            f"{MIN_CANVAS_WIDGET_SIZE_PX:.0f} px canvas floor (the handle "
            "layout cannot fit, so this is not a canvas viewport)"
        )
    return True, ""
