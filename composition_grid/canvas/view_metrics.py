"""Pure view metrics — the Krita 5.3 way to OBTAIN a ViewState.

No Qt, no krita: this module only turns numbers into numbers, so it is testable
at level 1 (``tests/canvas/test_view_metrics.py``) with no binding present.

WHY IT EXISTS (observed on live host Krita 5.3.4.1 / PyQt5).
5.3 ``Canvas`` carries none of the view-state signals
(``documentRectInWidgetPixelsChanged``, ``rotationChanged``, ``mirrorChanged``,
``sizeChanged``, ``removed``): ``libs/libkis/Canvas.h`` at tag
v5.3.0-prealpha declares NO signals at all, and those signals plus
``documentRectInWidgetPixels()`` exist only on master (Krita 6.x). On 5.3 a
ViewState therefore can never be RECEIVED; it has to be COMPUTED from the
getters the host does answer — ``Canvas.zoomLevel()``,
``Canvas.preferredCenter()``, ``Canvas.rotation()``, ``Canvas.mirror()``,
``Document.width()/height()`` and the canvas widget's own viewport
size. This module is that arithmetic; :mod:`composition_grid.canvas.
canvas_context` prefers the host's OWN transforms whenever it can read them —
the composed ``View.flakeToImageTransform()``/``flakeToCanvasTransform()``
affine first (exact under rotation), ``documentRectInWidgetPixels()`` next —
and this reconstruction last.

Units (fixed by the constructor field names, no implicit conversions):
``viewport_*`` widget px, ``doc_*`` document px, ``zoom`` the host factor
(widget px per doc px — see :func:`scale_per_doc_px`), ``center_*`` document px,
``rotation_deg`` degrees.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from composition_grid.canvas.doc_view_mapper import ViewState
from composition_grid.core.geometry import Bounds

logger = logging.getLogger("composition_grid")

__all__ = [
    "ViewMetrics",
    "document_rect_widget",
    "is_usable",
    "reset_starvation_latch",
    "scale_per_doc_px",
    "to_view_state",
]


@dataclass(frozen=True)
class ViewMetrics:
    """Everything needed to place a document in a canvas viewport, as numbers.

    ``center_x``/``center_y`` are the canvas ``preferredCenter()`` — per libkis
    ``Canvas.h`` "the position of the image pixel that is placed in the center
    of the current view", implemented as
    ``documentToImage(flakeToDocument(controller->preferredCenter()))`` — i.e.
    absolute DOCUMENT px of the point that already sits at the viewport centre.
    There is NO half-document offset: the convention that added one was an
    assumption, corrected here by the getter's own API evidence (the live host
    confirms it against the screen).
    ``mirror_x``/``mirror_y``/``rotation_deg`` are pass-through: the rect stays
    axis-aligned and the mapper owns the orientation. It owns it as a
    RECONSTRUCTION that is exact only while ``rotation_deg`` is 0 — Krita 5.3
    rotates CLOCKWISE-positive about a pivot that is not the document centre,
    so a rotated view is placed wrong by this path and is instead taken from
    the host's own affine (:func:`composition_grid.canvas.host_view_probe.
    read_host_affine`), which :attr:`CanvasContext.view_state` asks first.
    Same-source mirror and zoom-free cases do agree with the host exactly.

    There is deliberately NO ``resolution`` field: the live host proved the
    document dpi is not an input of the scale (see :func:`scale_per_doc_px`),
    and a field nothing reads is both dead code and a claim about the arithmetic
    that the evidence refutes.
    """

    viewport_w: float
    viewport_h: float
    doc_w: float
    doc_h: float
    zoom: float
    center_x: float
    center_y: float
    rotation_deg: float
    mirror_x: bool
    mirror_y: bool


def scale_per_doc_px(metrics: ViewMetrics) -> float:
    """Widget px per document px — the host's ``Canvas.zoomLevel()``, as-is.

    WHY THERE IS NO DPI NORMALISATION (the live Krita 5.3.4.1 / PyQt5 report
    settles it). Krita has two zoom semantics —
    "display pixel size at 100%" (zoom 1.0 means one document px is one widget
    px) and physical size (100% means one document px is one *inch* at the
    document dpi, which is the ``zoom * resolution / 72`` variant). THIS HOST IS
    IN PIXEL MODE, proven by two independent readings it printed:

        Canvas.zoomLevel()       = 0.0833333333333357
        Document.resolution()    = 600  (xRes = yRes = 599.9977611989281)
        Canvas.preferredCenter() = (4860.0, 1854.0)
        attached viewport        = 1234 x 877,   document 7000 x 4000
        host scrollbars          : horizontal value -212, vertical value -284

    With the placement of :func:`document_rect_widget`
    (``doc_left = viewport_w/2 - center_x*s``):

        s = zoom*600/72 = 0.6944  ->  x -2758.3, y -849.2   (matches NEITHER
                                         bar; the two axes do not even agree)
        s = zoom        = 0.08333 ->  x   +212.0, y  +284.0  (agrees with the
                                         magnitudes -212 and -284 of BOTH bars)

    A two-axis cross-check against two independent host numbers is evidence, not
    a coincidence, so ``zoomLevel()`` is already widget px per doc px and the
    ``resolution``-normalised variant is NOT what this plugin assumes. The
    assumption stays falsifiable: :mod:`composition_grid.diagnostics` prints the
    zoom, the resolution (informational), the computed rect, ``view_scale`` and
    the host scrollbar values with a cross-check delta computed in the report
    itself, so a future host in physical-size mode shows up as a non-zero delta."""
    return metrics.zoom


def document_rect_widget(metrics: ViewMetrics) -> Bounds:
    """The document's rect in widget px, placing ``preferredCenter`` at the
    viewport centre.

    ``s = scale_per_doc_px``; libkis documents ``preferredCenter()`` as "the
    position of the image pixel that is placed in the center of the current
    view" (implemented as ``documentToImage(flakeToDocument(...))``, absolute
    document px), so the doc point ``(center_x, center_y)`` must land at
    ``(viewport_w/2, viewport_h/2)`` and the top-left is
    ``viewport/2 - center * s``. An earlier convention added half the document
    extent to the centre — an assumption about a wrap-around origin that the
    getter's own documentation refutes; the live host confirms the corrected
    convention. Rotation is deliberately NOT folded in (see the
    class docstring — the mapper owns it).
    """
    scale = scale_per_doc_px(metrics)
    return Bounds(
        x=metrics.viewport_w / 2.0 - metrics.center_x * scale,
        y=metrics.viewport_h / 2.0 - metrics.center_y * scale,
        w=metrics.doc_w * scale,
        h=metrics.doc_h * scale,
    )


def is_usable(metrics: ViewMetrics) -> bool:
    """The single predicate every consumer shares: sizes strictly positive and
    every number finite. ``doc_* > 0`` is what keeps the mapper's
    ``rect.w / doc_width`` out of a division by zero."""
    if metrics.viewport_w <= 0.0 or metrics.viewport_h <= 0.0:
        return False
    if metrics.doc_w <= 0.0 or metrics.doc_h <= 0.0:
        return False
    numbers = (
        metrics.zoom,
        metrics.center_x,
        metrics.center_y,
        metrics.rotation_deg,
    )
    return all(math.isfinite(value) for value in numbers)


_starvation_reported = False
"""Process-wide latch for the FIRST computed-ViewState starvation : the pattern already exists as
``CanvasContext._report_doc_size_starvation``; a starving host used to be a
DEBUG-only note the operator never sees. ``reset_starvation_latch`` is the
test seam — production latches exactly once per process, which is the point."""


def reset_starvation_latch() -> None:
    """Test seam: re-arm the one-time INFO latch (tests must observe the
    FIRST-starvation behaviour deterministically)."""
    global _starvation_reported
    _starvation_reported = False


def to_view_state(metrics: ViewMetrics | None, reason: str | None = None) -> ViewState | None:
    """Build the mapper's :class:`ViewState`, or ``None`` when the metrics are
    missing or degenerate.

    Degradation, never an exception: an unusable metric set
    means the grid has nowhere to paint, so the caller hides it. The FIRST
    starvation on the process latches ONE INFO naming the reason (the
    ``reason`` the caller knows plus the DEBUG notes of
    :mod:`host_view_probe` naming the exact getter); every later one logs at
    DEBUG, because this is asked on every paint and a starving host must not
    flood the log."""
    global _starvation_reported
    if metrics is None or not is_usable(metrics):
        detail = reason or (
            "no metrics from the host probe" if metrics is None else "degenerate metrics"
        )
        if _starvation_reported:
            logger.debug(
                "view_metrics: cannot compute a ViewState (%s) — degradation",
                detail,
            )
        else:
            _starvation_reported = True
            logger.info(
                "view_metrics: the computed ViewState cannot be built (%s) — the "
                "grid stays hidden until the host answers the inputs it is read "
                "from. The exact getter is named by the DEBUG "
                "notes; this INFO fires once.",
                detail,
            )
        return None
    return ViewState(
        document_rect_widget=document_rect_widget(metrics),
        rotation_deg=float(metrics.rotation_deg),
        mirror_x=bool(metrics.mirror_x),
        mirror_y=bool(metrics.mirror_y),
        doc_width=float(metrics.doc_w),
        doc_height=float(metrics.doc_h),
    )
