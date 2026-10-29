"""Tests for the pure-arithmetic doc->widget view mapper.

Conventions pinned here and documented in
``composition_grid/canvas/doc_view_mapper.py``:

* Coordinate spaces are screen-style: y grows **down** in both doc space
  and widget space.
* ``ViewState.host_affine``, when the caller supplies it, IS the mapping:
  the three functions return it (or its analytic inverse) verbatim. It comes
  from the live host's ``View.flakeToImageTransform()`` /
  ``flakeToCanvasTransform()``, so the measured matrices below are the ones the
  real Krita 5.3.1 answers — they are the reference the mapper must reproduce.
* The fallback fields are a RECONSTRUCTION, exact only while the view is
  unrotated. ``rotation_deg`` is positive = **counterclockwise on screen** in
  THIS model's own terms; the live host's ``Canvas.rotation()`` is
  clockwise-positive and pivots away from the doc centre, which is precisely
  why the measured rotated cases below only agree with the host through
  ``host_affine`` (the fallback lands hundreds of px off — see
  ``test_host_affine_beats_the_fallback_under_rotation``).
* Mirroring flips the document about its own center line, in doc space,
  *before* zoom and placement into the widget rect.
"""

import pytest

from composition_grid.core.geometry import Affine, Bounds
from composition_grid.canvas import (
    ViewState,
    doc_to_widget_affine,
    view_scale,
    widget_to_doc_affine,
)


def _view(
    rect: Bounds | None = None,
    doc: tuple[float, float] = (100.0, 50.0),
    rotation_deg: float = 0.0,
    mirror_x: bool = False,
    mirror_y: bool = False,
    host_affine: Affine | None = None,
) -> ViewState:
    return ViewState(
        document_rect_widget=rect if rect is not None else Bounds(5.0, 5.0, 100.0, 50.0),
        rotation_deg=rotation_deg,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
        doc_width=doc[0],
        doc_height=doc[1],
        host_affine=host_affine,
    )


def test_identity_mapping_corners():
    # No rotation, no mirror, rect size == doc size: pure translation.
    state = _view()
    m = doc_to_widget_affine(state)
    assert m.apply((0.0, 0.0)) == (5.0, 5.0)
    assert m.apply((100.0, 50.0)) == (105.0, 55.0)


def test_zoom_two_x():
    # rect is exactly twice the doc size: exact corner mapping.
    state = _view(rect=Bounds(5.0, 5.0, 200.0, 100.0))
    m = doc_to_widget_affine(state)
    assert m.apply((100.0, 50.0)) == (205.0, 105.0)
    assert m.apply((0.0, 0.0)) == (5.0, 5.0)


def test_mirror_x_swaps_horizontal_edges():
    state = _view(mirror_x=True)
    m = doc_to_widget_affine(state)
    # doc LEFT edge lands on the rect RIGHT edge at the same widget y ...
    assert m.apply((0.0, 10.0)) == (105.0, 15.0)
    # ... and doc RIGHT edge lands on the rect LEFT edge.
    assert m.apply((100.0, 10.0)) == (5.0, 15.0)
    # The doc center line is fixed by the flip (map through rect offset).
    assert m.apply((50.0, 25.0)) == (55.0, 30.0)


def test_mirror_y_vertical_edges():
    state = _view(mirror_y=True)
    m = doc_to_widget_affine(state)
    # doc TOP edge lands on the rect BOTTOM edge at the same widget x ...
    assert m.apply((10.0, 0.0)) == (15.0, 55.0)
    # ... and doc BOTTOM edge lands on the rect TOP edge.
    assert m.apply((10.0, 50.0)) == (15.0, 5.0)
    # The doc center line is fixed by the flip (map through rect offset).
    assert m.apply((50.0, 25.0)) == (55.0, 30.0)


def test_rotation_90_center_and_edge_point():
    # doc 100x50 about its center (50, 25); rect (5, 5, 100, 50).
    # Positive rotation_deg = CCW on screen: the doc vector pointing DOWN
    # (visually) rotates to point RIGHT on screen.
    state = _view(rotation_deg=90.0)
    m = doc_to_widget_affine(state)

    # Center is fixed by the rotation about the doc center.
    assert m.apply((50.0, 25.0)) == pytest.approx((55.0, 30.0), abs=1e-9)

    # Doc point directly BELOW the center: (50, 50) (bottom-edge midpoint).
    # CCW-on-screen 90deg sends the down-vector (0, +25) to the
    # right-vector (+25, 0): rotated doc point (75, 25), then placed into
    # the rect at (5 + 75, 5 + 25) = the rect RIGHT-edge midpoint.
    assert m.apply((50.0, 50.0)) == pytest.approx((80.0, 30.0), abs=1e-9)

    # Correspondingly, the doc point ABOVE the center lands on the rect
    # LEFT-edge midpoint.
    assert m.apply((50.0, 0.0)) == pytest.approx((30.0, 30.0), abs=1e-9)


def test_view_scale_plain_ratio():
    # Widget px per doc px, ignoring rotation: rect.w / doc_width.
    assert view_scale(_view()) == 1.0
    assert view_scale(_view(rect=Bounds(5.0, 5.0, 200.0, 100.0))) == 2.0


def test_roundtrip_with_rotation_30():
    state = _view(rect=Bounds(5.0, 5.0, 200.0, 100.0), rotation_deg=30.0)
    forward = doc_to_widget_affine(state)
    back = widget_to_doc_affine(state)

    points = (
        (50.0, 25.0),          # doc center
        (0.0, 0.0),            # nw corner
        (100.0, 0.0),          # ne corner
        (100.0, 50.0),         # se corner
        (0.0, 50.0),           # sw corner
        (50.0, 0.0),           # n midpoint
        (37.5, 12.25),         # arbitrary interior point
    )
    for p in points:
        assert back.apply(forward.apply(p)) == pytest.approx(p, abs=1e-9)


def test_roundtrip_with_rotation_and_mirrors():
    # Analytic inverse must also invert the mirror stages exactly.
    state = _view(
        rect=Bounds(5.0, 5.0, 200.0, 100.0),
        rotation_deg=30.0,
        mirror_x=True,
        mirror_y=True,
    )
    forward = doc_to_widget_affine(state)
    back = widget_to_doc_affine(state)
    for p in ((50.0, 25.0), (0.0, 0.0), (100.0, 50.0), (12.5, 41.0)):
        assert back.apply(forward.apply(p)) == pytest.approx(p, abs=1e-9)


def test_widget_to_doc_is_analytic_inverse_of_full_product():
    # Composing the inverted stages must equal inverting the whole product.
    state = _view(rect=Bounds(5.0, 5.0, 200.0, 100.0), rotation_deg=30.0)
    staged = widget_to_doc_affine(state)
    whole = doc_to_widget_affine(state).inverted()
    for p in ((0.0, 0.0), (100.0, 50.0), (23.0, 17.5)):
        assert staged.apply(p) == pytest.approx(whole.apply(p), abs=1e-9)


# --------------------------------------------------------------------------
# ViewState.host_affine — the live Krita 5.3.1 measurement (doc 3000x2000).
#
# Every matrix below is what the host itself answers, read as
# ``flakeToImageTransform().inverted()[0] * flakeToCanvasTransform()`` and
# written in QTransform order (m11, m12, m21, m22, dx, dy); QTransform maps
# (x, y) -> (m11*x + m21*y + dx, m12*x + m22*y + dy). The widget points are the
# screen measurement of doc(700, 200), 0.2 px tolerance.
# --------------------------------------------------------------------------

_DOC = (3000.0, 2000.0)
_PROBE = (700.0, 200.0)

# zoom .5, pan (900, 600), rotation 0 — the host agrees with the plugin exactly.
_ZOOM_PAN = Affine(0.5, 0.0, 0.0, 0.5, 487.0, 318.0)
# zoom .25 centred, rotation 30.
_ROT_CENTRED = Affine(0.2165, 0.125, -0.125, 0.2165, 812.0, 368.5)
# zoom .5, pan (900, 600), rotation 30.
_ROT_PANNED = Affine(0.4330, 0.25, -0.25, 0.4330, 987.0, 318.5)
# zoom .5, pan (900, 600), mirror, rotation 0 — again identical to the plugin.
_MIRROR = Affine(-0.5, 0.0, 0.0, 0.5, 1987.0, 318.0)
# zoom .25 centred, rotation 30 + mirror (host Canvas.rotation() reads -30).
# The live report gives no screen point for this one, so it is pinned as the
# verbatim host matrix instead of a re-derived number.
_MIRROR_ROT = Affine(-0.2165, 0.125, 0.125, 0.2165, 1211.52, 368.5)


def _host_state(affine: Affine, **kwargs) -> ViewState:
    """A state that carries the host matrix; the rect it also holds is the
    unrotated placement (1500x1000 widget px for the 3000x2000 doc at .5) —
    nothing in these tests may read it."""
    return ViewState(
        document_rect_widget=kwargs.pop("rect", Bounds(487.0, 318.0, 1500.0, 1000.0)),
        doc_width=_DOC[0],
        doc_height=_DOC[1],
        host_affine=affine,
        **kwargs,
    )


@pytest.mark.parametrize(
    "affine, expected",
    [
        (_ROT_CENTRED, (938.6, 499.3)),
        (_ROT_PANNED, (1240.1, 580.1)),
        (_ZOOM_PAN, (837.0, 418.0)),  # the unrotated control the host also answers
    ],
)
def test_host_affine_maps_the_measured_points(affine, expected):
    """The mapper must reproduce the host's own placement, sign and pivot
    included — which is only possible by returning the host matrix: the
    fallback model puts doc(700, 200) at (663.8, 545.3) and (690.6, 672.1)
    respectively for the two rotated cases."""
    mapped = doc_to_widget_affine(_host_state(affine)).apply(_PROBE)
    assert mapped == pytest.approx(expected, abs=0.2)


def test_host_affine_is_returned_verbatim_and_inverted_for_the_mirrored_view():
    """The two sources the live report measured without giving a screen point:
    forward mapping == the host matrix applied directly, and the widget->doc
    direction is its analytic inverse (round trip to 0.2 px)."""
    for affine in (_MIRROR, _MIRROR_ROT):
        state = _host_state(affine)
        forward = doc_to_widget_affine(state)
        assert (forward.a, forward.b, forward.c, forward.d, forward.e, forward.f) == (
            affine.a,
            affine.b,
            affine.c,
            affine.d,
            affine.e,
            affine.f,
        )
        back = widget_to_doc_affine(state)
        assert back.apply(forward.apply(_PROBE)) == pytest.approx(_PROBE, abs=0.2)
    # The mirrored rotation is the unrotated mirror's reflection of rot 30:
    # doc(700, 200) sits at (938.6, 499.3) unmirrored and at the same widget y
    # on the other side of the view's vertical axis here.
    assert doc_to_widget_affine(_host_state(_MIRROR_ROT)).apply(_PROBE) == pytest.approx(
        (1085.0, 499.3), abs=0.2
    )


@pytest.mark.parametrize("affine, expected", [(_ROT_CENTRED, 0.25), (_ROT_PANNED, 0.5)])
def test_view_scale_of_host_affine_is_sqrt_abs_det(affine, expected):
    """Rotation and mirror are isometries: ``sqrt(|det|)`` is the host zoom, and
    that is what pen widths and the rotate-handle offset divide by."""
    assert view_scale(_host_state(affine)) == pytest.approx(expected, abs=1e-4)


def test_host_affine_beats_the_fallback_under_rotation():
    """The regression the live round found: with the SAME orientation fields the
    fallback computes a different (wrong) place, so a state that carries the
    host affine must not re-derive anything from them. On the host itself the
    fallback missed doc(700, 200) by (938.6-663.8, ...) = 275 px in x at zoom
    .25 and by 550 px at zoom .5 — here the same mismatch, against this state's
    own rect, is over 100 px."""
    state = _host_state(_ROT_CENTRED, rotation_deg=30.0)
    plain = ViewState(
        document_rect_widget=state.document_rect_widget,
        rotation_deg=30.0,
        doc_width=_DOC[0],
        doc_height=_DOC[1],
    )
    with_host = doc_to_widget_affine(state).apply(_PROBE)
    without_host = doc_to_widget_affine(plain).apply(_PROBE)
    assert with_host == pytest.approx((938.6, 499.3), abs=0.2)
    assert abs(without_host[0] - with_host[0]) > 100.0, (
        "the fallback is expected to miss by hundreds of px under rotation"
    )


def test_host_affine_agrees_with_the_fallback_without_rotation():
    """The other half of the evidence: at rotation 0 the two sources are the
    same numbers for zoom, pan AND mirror, so preferring the affine cannot
    regress an unrotated view."""
    zoom_pan = Bounds(487.0, 318.0, _DOC[0] * 0.5, _DOC[1] * 0.5)
    for affine, mirror_x in ((_ZOOM_PAN, False), (_MIRROR, True)):
        fallback = ViewState(
            document_rect_widget=zoom_pan,
            mirror_x=mirror_x,
            doc_width=_DOC[0],
            doc_height=_DOC[1],
        )
        host = ViewState(
            document_rect_widget=zoom_pan,
            mirror_x=mirror_x,
            doc_width=_DOC[0],
            doc_height=_DOC[1],
            host_affine=affine,
        )
        forward_host = doc_to_widget_affine(host)
        forward_fallback = doc_to_widget_affine(fallback)
        for point in ((0.0, 0.0), _PROBE, (_DOC[0], _DOC[1])):
            assert forward_host.apply(point) == pytest.approx(
                forward_fallback.apply(point), abs=1e-9
            )
        assert view_scale(host) == pytest.approx(view_scale(fallback), abs=1e-9)


def test_no_host_affine_keeps_the_computed_path_unchanged():
    """``host_affine=None`` is the documented fallback for hosts without the
    ``View`` getters: the same state must map exactly as before."""
    with_none = _view(host_affine=None)
    assert doc_to_widget_affine(with_none) == doc_to_widget_affine(_view())
