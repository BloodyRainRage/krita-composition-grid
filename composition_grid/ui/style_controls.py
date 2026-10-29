"""Shared style-control data for the docker (Qt-safe, no krita).

A small single-responsibility module extracted from :mod:`.docker_panel`: the
pinned dash presets and the ``Style.color_rgba`` <-> ``QColor`` conversions.
It imports no Qt binding directly (Qt only ever arrives through
``composition_grid.qt_compat`` at the call site), so the ui Qt guard stays
satisfied, and it holds no logic the panel owns.

* :data:`DASH_PATTERNS` — the combo labels -> :class:`Style.dash` patterns the
  docker lists exactly in this order (test-pinned). ``Style.dash`` patterns are
  widget px; ``overlay_painter`` normalizes them by the view scale. "Solid" ->
  ``None``.
* :func:`rgba_to_qcolor` / :func:`qcolor_to_rgba` — the color round-trip.
  v1 caveat (documented, not corrected): each dialog accept re-quantizes the
  float ``0..1`` components through 8-bit ints (``0.9 -> 230 -> 0.90196...``),
  so the stored color drifts by ~1/255 per accept; acceptable for v1.
"""

from __future__ import annotations

from composition_grid.core.styles import DashPattern

__all__ = [
    "DASH_PATTERNS",
    "color_swatch_icon",
    "qcolor_to_rgba",
    "rgba_to_qcolor",
]

# Pinned dash presets: combo label -> Style.dash widget-px run lengths (Solid -> None).
DASH_PATTERNS: "dict[str, DashPattern | None]" = {
    "Solid": None,
    "Dash": (6.0, 3.0),
    "Dot": (1.0, 2.0),
    "DashDot": (8.0, 3.0, 1.0, 3.0),
}


def rgba_to_qcolor(color_rgba, QtGui):
    """``Style.color_rgba`` floats -> ``QColor`` (0..255 ints, round).

    ``QtGui`` is passed in (the caller supplies ``qt_compat.QtGui``) so this
    module never dereferences a Qt name at import — it stays importable in a
    pure environment (the sibling Qt-module convention)."""
    return QtGui.QColor(
        *(round(component * 255) for component in color_rgba)  # RGBA order
    )


def qcolor_to_rgba(color) -> "tuple[float, float, float, float]":
    """``QColor`` -> the float RGBA tuple ``Style`` stores.

    Re-quantizes through 8-bit ints (see the module-docstring v1 caveat)."""
    return (
        color.red() / 255.0,
        color.green() / 255.0,
        color.blue() / 255.0,
        color.alpha() / 255.0,
    )


def color_swatch_icon(QtGui, QtCore, color_rgba, size_px: int = 16):
    """A preview chip for the CURRENT line color, for the Color button.

    Why an icon and not just the hex text: the docker sets the grid line color,
    and a hex string is not something an artist checks — they need to see the
    color next to the control they press. The chip paints the color OVER A
    CHECKERBOARD, so a translucent line color reads as translucent instead of
    looking like an opaque swatch (the same reason pickers show alpha this way).

    ``QtGui``/``QtCore`` are passed in by the caller (``qt_compat`` modules), so
    this module keeps no Qt name at import time — the pure-import contract of
    its siblings.
    """
    pixmap = QtGui.QPixmap(size_px, size_px)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))  # enum-free transparent (Qt5/Qt6 alike)
    painter = QtGui.QPainter(pixmap)
    try:
        cell = max(size_px // 4, 1)
        dark = QtGui.QColor(90, 90, 90)
        light = QtGui.QColor(200, 200, 200)
        for row in range(0, size_px, cell):
            for column in range(0, size_px, cell):
                painter.fillRect(
                    QtCore.QRect(column, row, cell, cell),
                    dark if (row // cell + column // cell) % 2 else light,
                )
        painter.fillRect(
            QtCore.QRect(0, 0, size_px, size_px), rgba_to_qcolor(color_rgba, QtGui)
        )
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawRect(QtCore.QRect(0, 0, size_px - 1, size_px - 1))
    finally:
        painter.end()
    return QtGui.QIcon(pixmap)
