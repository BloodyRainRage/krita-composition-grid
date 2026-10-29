"""Pure tests for the view-metrics math (no Qt, no krita).

WHY THIS MODULE EXISTS AT ALL (live host evidence): the live Krita
5.3.4.1 host exposes NO Canvas signals and no ``documentRectInWidgetPixels()``
(``libs/libkis/Canvas.h`` at tag v5.3.0-prealpha has no signals at all; they
only exist on master = Krita 6.x), so the ViewState cannot be *received* — it
has to be *computed* from the getters the 5.3 host really answers
(``zoomLevel()``, ``preferredCenter()``, ``rotation()``, ``mirror()``,
``Document.width()/height()`` and the canvas widget's own viewport
size). These tests pin that arithmetic with exact numbers, because a
"not None" assertion would not catch a wrong sign or a wrong scale basis.

CORRECTED SCALE RULE (live evidence from the user's Krita 5.3.4.1 /
PyQt5 report — the host readings outrank the older formula):

    scale_per_doc_px = Canvas.zoomLevel()          (widget px per doc px)

The formula this file used to pin, ``zoom * Document.resolution() / 72``
(the community "manual variant"), is the PHYSICAL-SIZE zoom
mode and is NOT what this host runs: the live numbers refute it through TWO
INDEPENDENT host readings. With ``zoomLevel() = 0.0833333333333357``,
``resolution() = 600``, ``preferredCenter() = (4860, 1854)`` and a 1234x877
viewport, the placement ``doc_left = viewport_w/2 - center_x*s`` gives

    s = zoom*600/72 = 0.6944 -> x -2758, y -849   (matches neither axis)
    s = zoom        = 0.08333 -> x +212.0, y +284.0

and the host's own scrollbars read ``value = -212`` / ``value = -284``. Only
``s = zoom`` agrees with BOTH bars at once, so on this host ``zoomLevel()`` is
already widget px per document px ("display pixel size at 100%" mode) and the
DPI normalisation is simply wrong. The numeric expectations below were pinned to
the old formula; correcting them is justified by that host evidence, not a
weakening — and :func:`test_live_host_rect_agrees_with_both_scrollbar_values`
is the regression test only the corrected rule passes.

What the arithmetic pins:

* ``scale_per_doc_px`` = ``zoom`` — widget px per document px.
* ``document_rect_widget`` places the document so that the canvas
  ``preferredCenter`` sits at the viewport center; the document rect is
  axis-aligned and rotation is deliberately NOT folded into it (the mapper
  applies view rotation about the document center — the exact
  ``documentRectInWidgetPixels`` contract, so the pinned
  ``doc_view_mapper`` tests stay the single source of the mapping contract).

Placement convention (corrected by the host's own API evidence):
libkis ``Canvas.h`` documents ``preferredCenter()`` as "the position of the
image pixel that is placed in the center of the current view", implemented as
``documentToImage(flakeToDocument(controller->preferredCenter()))`` — absolute
DOCUMENT px of the point sitting at the viewport centre, with NO half-document
offset. The rect top-left is therefore ``viewport/2 - preferredCenter * scale``
(the previous ``+(doc/2)`` term was a guess, now refuted by the getter's
own documentation; the live host confirms it against the screen).
"""

from __future__ import annotations

import dataclasses
import logging
import math

import pytest

from composition_grid.canvas.doc_view_mapper import ViewState, view_scale
from composition_grid.canvas.view_metrics import (
    ViewMetrics,
    document_rect_widget,
    is_usable,
    scale_per_doc_px,
    to_view_state,
)


def _metrics(**overrides) -> ViewMetrics:
    """The reference geometry of the live diagnostic run: a 7000x4000 document
    in a 1234x877 viewport at zoom 1.0 with the canvas centre on the document
    centre. NO ``resolution`` field: the live host proves the scale IS the
    zoom, so the document dpi is not an input of this arithmetic at all."""
    base = dict(
        viewport_w=1234.0,
        viewport_h=877.0,
        doc_w=7000.0,
        doc_h=4000.0,
        zoom=1.0,
        center_x=3500.0,
        center_y=2000.0,
        rotation_deg=0.0,
        mirror_x=False,
        mirror_y=False,
    )
    base.update(overrides)
    return ViewMetrics(**base)


# --- the scale rule ---------------------------------------------------------


def test_scale_per_doc_px_is_the_host_zoom_directly():
    """CORRECTED by host evidence (module docstring): the 5.3.4.1
    ``zoomLevel()`` is ALREADY widget px per doc px, so no dpi normalisation
    enters. zoom 1.0 -> 1.0, 2.5 -> 2.5, and the host's own printed value is
    returned unchanged."""
    assert scale_per_doc_px(_metrics()) == 1.0
    assert scale_per_doc_px(_metrics(zoom=2.5)) == 2.5
    assert scale_per_doc_px(_metrics(zoom=0.5)) == 0.5
    assert scale_per_doc_px(_metrics(zoom=0.0833333333333357)) == 0.0833333333333357


def test_view_metrics_has_no_resolution_field():
    """The 72-basis left the code with it: a field no
    consumer reads is a claim about the arithmetic that the host refuted, so
    ``ViewMetrics`` must not carry ``resolution`` and ``is_usable`` must not
    guard a value that never enters the computation."""
    names = {f.name for f in dataclasses.fields(ViewMetrics)}
    assert "resolution" not in names
    assert "zoom" in names
    with pytest.raises(TypeError):
        _metrics(resolution=600.0)


# --- the document rect in widget pixels -------------------------------------


def test_document_rect_widget_reference_case_exact_numbers():
    """THE formula pin (exact numbers, not "is not None"), corrected to the
    documented ``preferredCenter`` semantics (host API evidence):
    ``preferredCenter = (3500, 2000)`` is the image pixel that the view puts AT
    THE VIEWPORT CENTRE, so

    s = zoom = 1.0; w = 7000, h = 4000;
    x = 1234/2 - 3500*1 = 617 - 3500 = -2883;
    y =  877/2 - 2000*1 = 438.5 - 2000 = -1561.5.

    (The earlier convention added half the document extent to the centre and
    pinned x -6383 / y -3561.5; that arithmetic was a guess refuted by
    the getter's own documentation, not a host observation. The scale is the
    direct-zoom rule of the module docstring: at zoom 1.0 the old and the new
    rule happen to agree, so THIS case keeps its numbers while
    ``test_live_host_rect_agrees_with_both_scrollbar_values`` pins the case
    where they do not.)
    """
    rect = document_rect_widget(_metrics())
    assert rect.x == -2883.0
    assert rect.y == -1561.5
    assert rect.w == 7000.0
    assert rect.h == 4000.0


def test_document_rect_widget_halves_everything_at_zoom_half():
    """s = 0.5: the size halves and the placement scales with it — a document
    point is viewport-centre minus ITS OWN doc coords times s:
    x = 617 - 3500*0.5 = 617 - 1750 = -1133;
    y = 438.5 - 2000*0.5 = 438.5 - 1000 = -561.5."""
    rect = document_rect_widget(_metrics(zoom=0.5))
    assert rect.x == -1133.0
    assert rect.y == -561.5
    assert rect.w == 3500.0
    assert rect.h == 2000.0


def test_live_host_rect_agrees_with_both_scrollbar_values():
    """THE REGRESSION PIN (only the corrected formula passes). Verbatim live
    numbers from the user's Krita 5.3.4.1 / PyQt5 self-diagnostics report:

        Canvas.zoomLevel()      = 0.0833333333333357
        Document.resolution()   = 600 (xRes/yRes = 599.9977611989281)
        Canvas.preferredCenter()= (4860.0, 1854.0)
        viewport                = 1234 x 877, document 7000 x 4000
        host scrollbars         : horizontal value -212, vertical value -284

    The placement of the corrected rule is ``doc_left = vw/2 - center_x*s``:

        s = zoom * 600 / 72 = 0.6944 -> x -2758.3, y -849.2  (matches NEITHER
                                          bar, and the two axes disagree)
        s = zoom          = 0.08333  -> x   +212.0, y +284.0  (matches -212
                                          and -284 in magnitude)

    Agreement on BOTH axes at once from two independent readings is what makes
    this evidence rather than a coincidence, so both numbers are pinned here —
    with the resolution-normalised variant kept as a NEGATIVE CONTROL, because
    that is exactly the formula this round removes."""
    live = _metrics(
        zoom=0.0833333333333357,
        center_x=4860.0,
        center_y=1854.0,
    )
    rect = document_rect_widget(live)
    assert rect.x == pytest.approx(212.0, abs=1e-6), rect
    assert rect.y == pytest.approx(284.0, abs=1e-6), rect
    assert rect.w == pytest.approx(7000.0 * 0.0833333333333357, abs=1e-6)
    assert rect.h == pytest.approx(4000.0 * 0.0833333333333357, abs=1e-6)

    # negative control: the refuted physical-size rule lands ~2970 px away on
    # the horizontal axis, i.e. it could never produce the host's -212/-284.
    wrong_scale = 0.0833333333333357 * 600.0 / 72.0
    wrong_x = 1234.0 / 2.0 - 4860.0 * wrong_scale
    wrong_y = 877.0 / 2.0 - 1854.0 * wrong_scale
    assert abs(rect.x - wrong_x) > 1000.0
    assert abs(rect.y - wrong_y) > 1000.0


def test_preferred_center_at_doc_origin_places_doc_origin_at_viewport_center():
    """SEMANTICS PIN (not arithmetic): ``preferredCenter() = (0, 0)`` means the
    image/document ORIGIN is the pixel at the centre of the view, so the
    document's top-left corner must land exactly on the viewport centre
    (x = vw/2, y = vh/2). This is what makes the placement convention
    verifiable instead of assumed: the old +doc/2 formula would place the
    document a whole half-extent further up-left here.
    """
    rect = document_rect_widget(
        _metrics(
            viewport_w=1000.0,
            viewport_h=600.0,
            doc_w=400.0,
            doc_h=200.0,
            center_x=0.0,
            center_y=0.0,
        )
    )
    assert rect.x == 500.0
    assert rect.y == 300.0
    assert rect.w == 400.0
    assert rect.h == 200.0


def test_preferred_center_at_doc_centre_centres_the_document():
    """SEMANTICS PIN: the view centred on the document's centre point
    (doc_w/2, doc_h/2) must show the document centred in the viewport:
    x = (vw - doc_w*s)/2, y = (vh - doc_h*s)/2 — at zoom 1.0 AND at zoom 2.0,
    where only the corrected formula satisfies both at once."""
    centred = document_rect_widget(
        _metrics(
            viewport_w=1000.0,
            viewport_h=600.0,
            doc_w=400.0,
            doc_h=200.0,
            center_x=200.0,
            center_y=100.0,
        )
    )
    assert centred.x == (1000.0 - 400.0) / 2.0
    assert centred.y == (600.0 - 200.0) / 2.0

    zoomed = document_rect_widget(
        _metrics(
            viewport_w=1000.0,
            viewport_h=600.0,
            doc_w=400.0,
            doc_h=200.0,
            zoom=2.0,
            center_x=200.0,
            center_y=100.0,
        )
    )
    assert zoomed.x == (1000.0 - 400.0 * 2.0) / 2.0
    assert zoomed.y == (600.0 - 200.0 * 2.0) / 2.0
    assert zoomed.w == 800.0
    assert zoomed.h == 400.0


def test_rotation_is_not_folded_into_the_rect():
    """``documentRectInWidgetPixels`` contract: the rect stays axis-aligned
    and rotation-free — the mapper applies rotation about the document
    centre. Two metrics differing only in rotation must give the SAME rect."""
    plain = document_rect_widget(_metrics())
    turned = document_rect_widget(_metrics(rotation_deg=90.0, mirror_x=True))
    assert plain == turned


# --- usability + ViewState construction -------------------------------------


def test_is_usable_accepts_the_reference_metrics():
    assert is_usable(_metrics()) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"viewport_w": 0.0},
        {"viewport_h": 0.0},
        {"doc_w": 0.0},
        {"doc_h": 0.0},
        {"viewport_w": -1.0},
        {"doc_h": -100.0},
        {"zoom": float("nan")},
        {"zoom": float("inf")},
        {"center_x": float("nan")},
        {"center_y": float("inf")},
        {"rotation_deg": float("nan")},
    ],
)
def test_is_usable_rejects_degenerate_or_non_finite_inputs(overrides):
    """A zero viewport/doc size or a non-finite number cannot produce a usable
    rect; ``is_usable`` is the single predicate every consumer shares, so the
    mapper can never be handed a division by zero."""
    assert is_usable(_metrics(**overrides)) is False


@pytest.mark.parametrize("overrides", [{"viewport_w": 0.0}, {"doc_w": 0.0}])
def test_to_view_state_returns_none_and_logs_debug_on_degenerate_inputs(
    overrides, caplog
):
    """Degenerate input degrades: ``None`` + a DEBUG note, NEVER an exception
    and never a divide-by-zero."""
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        assert to_view_state(_metrics(**overrides)) is None
    assert any(
        "view_metrics" in record.getMessage() for record in caplog.records
    ), caplog.messages


def test_to_view_state_builds_the_mapper_view_state():
    """The ViewState handed to ``doc_view_mapper`` carries the computed rect,
    the document extent and the pass-through rotation/mirror fields."""
    state = to_view_state(
        _metrics(rotation_deg=30.0, mirror_x=True, mirror_y=False)
    )
    assert state == ViewState(
        document_rect_widget=document_rect_widget(
            _metrics(rotation_deg=30.0, mirror_x=True, mirror_y=False)
        ),
        rotation_deg=30.0,
        mirror_x=True,
        mirror_y=False,
        doc_width=7000.0,
        doc_height=4000.0,
    )


def test_view_scale_of_a_computed_state_matches_the_scale_formula():
    """Round trip through the pinned mapper: ``view_scale(state)`` ==
    ``rect.w / doc_w`` == ``zoom`` (the direct rule, no dpi normalisation)."""
    metrics = _metrics(zoom=0.25)
    state = to_view_state(metrics)
    assert state is not None
    assert view_scale(state) == scale_per_doc_px(metrics) == 0.25


def test_view_scale_of_the_live_host_zoom_is_that_zoom():
    """The same round trip on the user's actual number: a computed state at
    ``zoomLevel() = 0.0833333333333357`` reports exactly that widget px per doc
    px, which is what the on-screen grid width depends on."""
    metrics = _metrics(zoom=0.0833333333333357, center_x=4860.0, center_y=1854.0)
    state = to_view_state(metrics)
    assert state is not None
    assert view_scale(state) == pytest.approx(0.0833333333333357, abs=1e-12)


def test_non_finite_inputs_are_rejected_before_the_rect_is_computed():
    """No NaN/inf may ever reach a ViewState (it would poison the affine and
    the paint), so the predicate covers every field, not only the sizes."""
    assert math.isfinite(document_rect_widget(_metrics()).x)
    assert not is_usable(_metrics(zoom=float("nan")))
    assert to_view_state(_metrics(center_x=float("inf"))) is None
