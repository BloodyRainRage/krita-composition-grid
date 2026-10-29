"""Tests for the Golden Spiral grid type.

Pinned construction (see module docstring): the frame-fitted ("stretched")
golden spiral inscribed in the WHOLE unit frame — PHI-fraction cells cut
alternately along x and y, one quarter-ellipse arc per cell
(``rx = cell width``, ``ry = cell height``), subdivision lines = the arc radii.
The element aspect picks the first cut axis, so the geometry now follows
``bounds`` instead of ignoring it.
"""

import math

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import (
    Arc,
    Dot,
    Segment,
    arc_points,
    primitive_within_bounds,
)
from composition_grid.core.grids import (
    GoldenSpiral,
    Param,
    all_types,
    default_params,
    get,
)

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)  # landscape: first cut is W/E
BOUNDS_PORTRAIT = Bounds(x=0.0, y=0.0, w=1.0, h=2.0)  # portrait: first cut is N/S
BOUNDS_SQUARE = Bounds(x=0.0, y=0.0, w=5.0, h=5.0)  # w >= h -> landscape rule

PHI = (math.sqrt(5) - 1) / 2
ORIGINS = ("tl", "tr", "br", "bl")
CORNER_OF = {"tl": (0.0, 0.0), "tr": (1.0, 0.0), "br": (1.0, 1.0), "bl": (0.0, 1.0)}


def arcs_of(geom):
    return [p for p in geom if isinstance(p, Arc)]


def segments_of(geom):
    return [p for p in geom if isinstance(p, Segment)]


def xs_of(geom):
    """Set of all segment x coordinates, rounded (mirror-set comparisons)."""
    return {round(c, 9) for s in segments_of(geom) for c in (s.p1[0], s.p2[0])}


def arc_ends(arc):
    """The two endpoints of an arc, as sampled by the renderer."""
    points = arc_points(arc, 2)
    return points[0], points[1]


def point_close(a, b, tol=1e-12):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def all_points(geom):
    pts = []
    for prim in geom:
        if isinstance(prim, Segment):
            pts += [prim.p1, prim.p2]
        elif isinstance(prim, Arc):
            pts += arc_points(prim)
    return pts


class TestSchema:
    def test_identity(self):
        assert GoldenSpiral.id == "golden_spiral"
        assert GoldenSpiral.name == "Golden Spiral"
        assert GoldenSpiral().escapes_bounds is False
        assert get("golden_spiral") is GoldenSpiral

    def test_params_schema(self):
        assert GoldenSpiral.params == (
            Param(
                name="origin",
                kind="choice",
                default="tl",
                choices=("tl", "tr", "br", "bl"),
            ),
            Param(name="show_subdivision", kind="bool", default=True),
        )

    def test_default_params(self):
        assert default_params(GoldenSpiral) == {"origin": "tl", "show_subdivision": True}

    def test_registration_order_spiral_triangles_diagonal(self):
        """Pinned order: ... symmetry_axes -> golden_spiral."""
        ids = [t.id for t in all_types()]
        assert ids[7:8] == ["golden_spiral"]


class TestBuild:
    def test_spiral_fits_bounds(self):
        """architecture contract: arcs and segments stay inside the unit frame."""
        for origin in ORIGINS:
            geom = GoldenSpiral().build(BOUNDS, {"origin": origin})
            unit = Bounds(0, 0, 1, 1)
            for p in geom:
                assert primitive_within_bounds(p, unit, tol=1e-9), origin

    def test_covers_the_whole_frame(self):
        """Frame-fitted: the start rect is the frame itself, so the geometry
        reaches all four edges (the old phi band left 38% of the frame empty)."""
        for bounds in (BOUNDS, BOUNDS_PORTRAIT, BOUNDS_SQUARE):
            for origin in ORIGINS:
                pts = all_points(GoldenSpiral().build(bounds, {"origin": origin}))
                assert min(p[0] for p in pts) == pytest.approx(0.0, abs=1e-12)
                assert max(p[0] for p in pts) == pytest.approx(1.0, abs=1e-12)
                assert min(p[1] for p in pts) == pytest.approx(0.0, abs=1e-12)
                assert max(p[1] for p in pts) == pytest.approx(1.0, abs=1e-12)

    def test_quarter_arcs_count(self):
        """Pinned depth 6: exactly 6 arcs, each a 90-degree quarter arc."""
        for origin in ORIGINS:
            geom = GoldenSpiral().build(BOUNDS, {"origin": origin})
            arcs = arcs_of(geom)
            assert len(arcs) == 6
            for a in arcs:
                assert abs(a.a1 - a.a0) == pytest.approx(90.0)
                assert a.a0 % 90.0 == pytest.approx(0.0, abs=1e-9)
                assert a.rx > 0.0 and a.ry > 0.0

    def test_arcs_are_stretched_ellipses(self):
        """Stretched spiral: the cell is a golden rectangle, so rx != ry and
        the ratio between the two axes is the pinned PHI."""
        for origin in ORIGINS:
            for a in arcs_of(GoldenSpiral().build(BOUNDS, {"origin": origin})):
                assert a.rx == pytest.approx(PHI * a.ry, rel=1e-12)

    def test_arc_endpoints_are_cell_corners(self):
        """Each quarter arc spans exactly 90 degrees between two corners of
        its cell, i.e. its endpoints sit on the axes through the center."""
        for a in arcs_of(GoldenSpiral().build(BOUNDS, {"origin": "tl"})):
            start, end = arc_ends(a)
            for point in (start, end):
                on_x_axis = abs(point[1] - a.center[1]) < 1e-12
                on_y_axis = abs(point[0] - a.center[0]) < 1e-12
                assert on_x_axis or on_y_axis
            assert start != end

    def test_first_arc_starts_at_the_origin_corner(self):
        for origin in ORIGINS:
            for bounds in (BOUNDS, BOUNDS_PORTRAIT):
                arcs = arcs_of(GoldenSpiral().build(bounds, {"origin": origin}))
                assert arc_ends(arcs[0])[0] == pytest.approx(CORNER_OF[origin], abs=1e-12)

    def test_first_cut_axis_follows_the_element_aspect(self):
        """Landscape: the first cell is the vertical phi band, so the first
        arc ends on x = PHI (W cut) or x = 1 - PHI (E cut).  Portrait: the
        horizontal phi band, so it ends on y = PHI (N) / 1 - PHI (S)."""
        landscape = {"tl": PHI, "tr": 1.0 - PHI, "bl": PHI, "br": 1.0 - PHI}
        portrait = {"tl": PHI, "tr": PHI, "bl": 1.0 - PHI, "br": 1.0 - PHI}
        for origin in ORIGINS:
            arcs = arcs_of(GoldenSpiral().build(BOUNDS, {"origin": origin}))
            assert arc_ends(arcs[0])[1][0] == pytest.approx(landscape[origin], abs=1e-12)
            arcs = arcs_of(GoldenSpiral().build(BOUNDS_PORTRAIT, {"origin": origin}))
            assert arc_ends(arcs[0])[1][1] == pytest.approx(portrait[origin], abs=1e-12)

    def test_arcs_connect_end_to_end(self):
        """Consecutive arcs share the pivot corner (C0 continuity)."""
        for bounds in (BOUNDS, BOUNDS_PORTRAIT, BOUNDS_SQUARE):
            for origin in ORIGINS:
                arcs = arcs_of(GoldenSpiral().build(bounds, {"origin": origin}))
                for a, b in zip(arcs, arcs[1:]):
                    assert arc_ends(a)[1] == pytest.approx(arc_ends(b)[0], abs=1e-12)

    def test_spiral_flips_with_origin(self):
        """origin='tr' mirrors the 'tl' subdivision x-coordinates about x=0.5."""
        tl = GoldenSpiral().build(BOUNDS, {"origin": "tl"})
        tr = GoldenSpiral().build(BOUNDS, {"origin": "tr"})
        assert xs_of(tr) == {round(1.0 - x, 9) for x in xs_of(tl)}

    def test_br_mirrors_tl_in_both_axes(self):
        """'br' vs 'tl': mirrored in x (segment set) and arcs sweep opposite."""
        tl = GoldenSpiral().build(BOUNDS, {"origin": "tl"})
        br = GoldenSpiral().build(BOUNDS, {"origin": "br"})
        assert xs_of(br) == {round(1.0 - x, 9) for x in xs_of(tl)}
        tl_spans = sorted(a.a1 - a.a0 for a in arcs_of(tl))
        br_spans = sorted(a.a1 - a.a0 for a in arcs_of(br))
        assert tl_spans == br_spans
        assert tl_spans[0] == pytest.approx(-90.0)  # tl: clockwise
        assert br_spans[0] == pytest.approx(-90.0)  # br: clockwise

    def test_bl_spans_counterclockwise(self):
        """Pinned directions: tl/br -90 deg sweep, tr/bl +90 deg sweep."""
        bl_spans = sorted(
            a.a1 - a.a0 for a in arcs_of(GoldenSpiral().build(BOUNDS, {"origin": "bl"}))
        )
        tr_spans = sorted(
            a.a1 - a.a0 for a in arcs_of(GoldenSpiral().build(BOUNDS, {"origin": "tr"}))
        )
        assert bl_spans[0] == pytest.approx(90.0)
        assert tr_spans[0] == pytest.approx(90.0)

    def test_portrait_keeps_the_whirl_direction_per_origin(self):
        """The portrait cycle is derived so the pinned sweep per origin holds."""
        for origin in ORIGINS:
            spans = {
                round(a.a1 - a.a0, 9)
                for a in arcs_of(GoldenSpiral().build(BOUNDS_PORTRAIT, {"origin": origin}))
            }
            expected = -90.0 if origin in ("tl", "br") else 90.0
            assert spans == {expected}

    def test_arc_radii_are_phi_powers(self):
        """Cell axes shrink by PHI per cut: landscape rx == PHI^k along the
        cut axis, ry == PHI^(k-1); the portrait frame swaps the axes."""
        arcs = arcs_of(GoldenSpiral().build(BOUNDS, {}))
        assert [a.rx for a in arcs] == pytest.approx([PHI**k for k in range(1, 7)])
        assert [a.ry for a in arcs] == pytest.approx([PHI**k for k in range(0, 6)])

    def test_portrait_swaps_the_arc_axes(self):
        arcs = arcs_of(GoldenSpiral().build(BOUNDS_PORTRAIT, {}))
        assert [a.ry for a in arcs] == pytest.approx([PHI**k for k in range(1, 7)])
        assert [a.rx for a in arcs] == pytest.approx([PHI**k for k in range(0, 6)])

    def test_show_subdivision_emits_the_two_radii_per_cell(self):
        geom = GoldenSpiral().build(BOUNDS, {})
        arcs = arcs_of(geom)
        segs = segments_of(geom)
        assert len(segs) == 2 * len(arcs)
        for index, arc in enumerate(arcs):
            start, end = arc_ends(arc)
            radii = segs[2 * index : 2 * index + 2]
            for radius in radii:
                assert radius.p1 == arc.center
            assert point_close(radii[0].p2, start)
            assert point_close(radii[1].p2, end)

    def test_show_subdivision_false_arcs_only(self):
        geom = GoldenSpiral().build(BOUNDS, {"show_subdivision": False})
        assert len(arcs_of(geom)) == 6
        assert segments_of(geom) == []
        assert all(isinstance(p, Arc) for p in geom)

    def test_no_dots_ever(self):
        geom = GoldenSpiral().build(BOUNDS, {})
        assert not any(isinstance(p, Dot) for p in geom)

    def test_unknown_origin_falls_back_to_tl(self):
        geom = GoldenSpiral().build(BOUNDS, {"origin": "xx"})
        assert geom == GoldenSpiral().build(BOUNDS, {"origin": "tl"})

    def test_geometry_follows_the_element_aspect(self):
        """Same aspect -> same geometry; portrait vs landscape -> differs
        (the first cut axis is picked from bounds)."""
        same_aspect = Bounds(x=10.0, y=-5.0, w=600.0, h=300.0)
        assert GoldenSpiral().build(same_aspect, {}) == GoldenSpiral().build(BOUNDS, {})
        assert GoldenSpiral().build(BOUNDS_PORTRAIT, {}) != GoldenSpiral().build(
            BOUNDS, {}
        )
        # w >= h uses the landscape cycle, so a square frame matches too.
        assert GoldenSpiral().build(BOUNDS_SQUARE, {}) == GoldenSpiral().build(BOUNDS, {})

    def test_within_bounds_extreme_target_shapes(self):
        """90-quantile style contract across dramatically different frames."""
        for bounds in (
            Bounds(0, 0, 10, 10),
            Bounds(0, 0, 1, 3),
            Bounds(0, 0, 4, 0.25),
        ):
            geom = GoldenSpiral().build(bounds, {})
            for p in geom:
                assert primitive_within_bounds(p, Bounds(0, 0, 1, 1), tol=1e-9)

    def test_degenerate_bounds_still_build_the_square_spiral(self):
        """No division by the aspect: a zero-size bounds falls back to the
        landscape first axis (w >= h) and stays inside the frame."""
        geom = GoldenSpiral().build(Bounds(0, 0, 0.0, 0.0), {})
        assert len(arcs_of(geom)) == 6
        assert geom == GoldenSpiral().build(BOUNDS_SQUARE, {})
