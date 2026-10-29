"""Pure-arithmetic doc->widget view mapper for the canvas panel.

No Qt, no krita — only ``composition_grid.core.geometry``.

Conventions (the Qt widget layer of the canvas overlay depends on them):

* Coordinate spaces: both document space and widget space are screen-style,
  with **y growing down**.
* ``ViewState.host_affine``: when the host supplies it (see
  :func:`composition_grid.canvas.host_view_probe.read_host_affine`) it IS the
  doc->widget mapping and the three functions below simply return it. Every
  other field of the state is then carried for reporting only.
* ``ViewState.rotation_deg`` / ``mirror_x`` / ``mirror_y`` belong to the
  COMPUTED FALLBACK (hosts with no affine getters), and that model is exact
  **only while the view is unrotated** — verified against the live host: with
  zoom 0.5, pan (900, 600) and rotation 0 the fallback and the host agree
  exactly, mirror included.
* Krita 5.3 ``Canvas.rotation()`` is **clockwise-positive on screen**, not CCW,
  and it does NOT rotate about the document centre. Measured on the live 5.3.1
  host (doc 3000x2000) with ``View.flakeToImageTransform()`` /
  ``flakeToCanvasTransform()`` composed the way
  :mod:`composition_grid.canvas.host_view_probe` does, and where the widget px
  of doc(700, 200) is the check point:

  - zoom .25 centred, rot 30: host ``(938.6, 499.3)``, fallback
    ``(663.8, 545.3)``;
  - zoom .5, pan (900, 600), rot 30: host ``(1240.1, 580.1)``, fallback
    ``(690.6, 672.1)``.

  i.e. hundreds of widget px off: the sign is flipped and the pivot is not the
  doc centre. That is why the host affine wins whenever it is answerable, and
  why the fallback below keeps its own (unchanged, unrotated-exact) semantics
  instead of pretending to model a rotated view.
* ``ViewState.mirror_x`` / ``mirror_y`` (fallback): flip the *document* about
  its own center line (doc left edge swaps with doc right edge; top with
  bottom), in doc space, before zoom and placement into the widget rect. The
  doc center line stays fixed on screen.
* ``ViewState.document_rect_widget``: the canvas *document rect* in widget
  pixels (the widget-space axis-aligned rectangle over which the document
  is drawn). Zoom is expressed through this rect: the scale stage maps doc
  pixels to widget pixels by ``rect.w / doc_width`` per axis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from composition_grid.core.geometry import Affine, Bounds

__all__ = ["ViewState", "doc_to_widget_affine", "widget_to_doc_affine", "view_scale"]


@dataclass(frozen=True)
class ViewState:
    """Everything the view mapper needs about one canvas view.

    ``host_affine`` — when not ``None`` — is the host's own doc px -> widget px
    transform and the ONLY thing the mapper uses. Otherwise the state is
    described by ``document_rect_widget`` (the canvas document rect in widget
    pixels), ``doc_width`` / ``doc_height`` (document pixels) and the fallback
    orientation: ``rotation_deg`` / ``mirror_x`` / ``mirror_y``, which model
    the view exactly only while ``rotation_deg`` is 0 (module docstring).
    """

    document_rect_widget: Bounds
    rotation_deg: float = 0.0
    mirror_x: bool = False
    mirror_y: bool = False
    doc_width: float = 0.0
    doc_height: float = 0.0
    host_affine: Affine | None = None


def _rotation_about_doc_center(state: ViewState) -> Affine:
    """The fallback's rotation stage: ``rotation_deg`` about the doc center.

    Exact only at ``rotation_deg == 0`` — a real Krita 5.3 view rotates
    clockwise-positive and about a pivot that is not the doc centre (module
    docstring, measured). ``Affine.rotation_deg`` is CCW in standard
    math orientation, so a y-down screen-CCW angle is the negated one.
    ``Affine.composed`` applies ``self`` first, so the stages are written
    innermost-first: rebase to center, rotate, rebase back.
    """
    cx = state.doc_width / 2.0
    cy = state.doc_height / 2.0
    return (
        Affine.translation(-cx, -cy)
        .composed(Affine.rotation_deg(-state.rotation_deg))
        .composed(Affine.translation(cx, cy))
    )


def _mirror_about_doc_center(state: ViewState) -> Affine:
    """Flip the mirrored axis around the doc center: negate it in place."""
    cx = state.doc_width / 2.0
    cy = state.doc_height / 2.0
    return (
        Affine.translation(-cx, -cy)
        .composed(Affine.mirror(state.mirror_x, state.mirror_y))
        .composed(Affine.translation(cx, cy))
    )


def _scale_stage(state: ViewState) -> Affine:
    """Doc px -> widget px, about the doc origin (0, 0)."""
    rect = state.document_rect_widget
    return Affine.scaling(rect.w / state.doc_width, rect.h / state.doc_height)


def _translate_stage(state: ViewState) -> Affine:
    """Place the (already scaled) doc origin at the rect origin."""
    rect = state.document_rect_widget
    return Affine.translation(rect.x, rect.y)


def doc_to_widget_affine(state: ViewState) -> Affine:
    """Document point -> widget pixel.

    ``state.host_affine`` wins verbatim when present: it is the host's own
    transform and is exact under rotation, mirror and zoom alike.

    Otherwise the fallback composition (applied to a doc point in this order):

    1. rotate ``rotation_deg`` around the doc center,
    2. mirror the flipped axis around the doc center line,
    3. scale by ``(rect.w / doc_width, rect.h / doc_height)``,
    4. translate the rebased origin to ``document_rect_widget.x, .y``.

    So the ``Affine`` product is, in math order applied to a point,
    ``translate ∘ scale ∘ mirror(center) ∘ rotate(center)``; with
    ``Affine.composed`` (applies ``self`` first) that is written
    innermost-first:
    ``rotate.composed(mirror).composed(scale).composed(translate)``.
    """
    if state.host_affine is not None:
        return state.host_affine
    return (
        _rotation_about_doc_center(state)
        .composed(_mirror_about_doc_center(state))
        .composed(_scale_stage(state))
        .composed(_translate_stage(state))
    )


def widget_to_doc_affine(state: ViewState) -> Affine:
    """Widget pixel -> document point.

    The inverse of :func:`doc_to_widget_affine` for the same state: the
    analytic inverse of ``state.host_affine`` when the host supplied it, else
    the analytic inverse composition — undo the placement translate, undo the
    zoom scale, undo the mirror, undo the rotation. No numeric least-squares
    fitting involved.
    """
    if state.host_affine is not None:
        return state.host_affine.inverted()
    return (
        _translate_stage(state).inverted()
        .composed(_scale_stage(state).inverted())
        .composed(_mirror_about_doc_center(state).inverted())
        .composed(_rotation_about_doc_center(state).inverted())
    )


def view_scale(state: ViewState) -> float:
    """Widget px per doc px: ``sqrt(|det|)`` of the mapping in use.

    For ``host_affine`` that is the host's isotropic zoom (rotation and
    mirror leave ``|det|`` at ``s²`); for the fallback it reduces to the
    documented ``rect.w / doc_width``.
    """
    if state.host_affine is not None:
        affine = state.host_affine
        return math.sqrt(abs(affine.a * affine.d - affine.b * affine.c))
    return state.document_rect_widget.w / state.doc_width
