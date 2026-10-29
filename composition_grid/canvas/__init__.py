"""Canvas package. The pure arithmetic layer — ``doc_view_mapper`` (the
doc<->widget matrices) and ``view_metrics`` (the Krita 5.3
computed metrics) — stays pure Python (no Qt, no krita); the Qt canvas modules
(``canvas_locator``, the overlay/context, ``host_view_probe``,
``viewport_triggers``) import Qt only through ``composition_grid.qt_compat``
(guard: tests/canvas)."""

from composition_grid.canvas.doc_view_mapper import (
    ViewState,
    doc_to_widget_affine,
    view_scale,
    widget_to_doc_affine,
)

__all__ = [
    "ViewState",
    "doc_to_widget_affine",
    "widget_to_doc_affine",
    "view_scale",
]
