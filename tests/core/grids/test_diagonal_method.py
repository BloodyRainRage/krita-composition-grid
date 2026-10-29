"""Tests for the Diagonal Method grid type and clip_segment_to_bounds.

The clip helper tests live here because this grid type consumes the
helper.  clip_segment_to_bounds itself is a
Liang–Barsky style parametric clip.
"""

import math

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import (
    Segment,
    clip_segment_to_bounds,
    primitive_within_bounds,
)
from composition_grid.core.grids import (
    DiagonalMethod,
    Param,
    all_types,
    default_params,
    get,
)

BOUNDS_2_1 = Bounds(x=0.0, y=0.0, w=1000.0, h=500.0)
BOUNDS_1_1 = Bounds(x=0.0, y=0.0, w=500.0, h=500.0)


def slope(seg: Segment) -> float:
    """Normalized slope dy/dx of a segment (dx==0 -> inf)."""
    dx = seg.p2[0] - seg.p1[0]
    dy = seg.p2[1] - seg.p1[1]
    if dx == 0:
        return math.inf
    return dy / dx


def segments(geom):
    return [p for p in geom if isinstance(p, Segment)]


def midlines(geom):
    return [s for s in segments(geom) if s.p1[1] == s.p2[1] or s.p1[0] == s.p2[0]]


def line_set(geom):
    """Order-independent identity of every drawn line (raw endpoints)."""
    return {frozenset((tuple(s.p1), tuple(s.p2))) for s in segments(geom)}


def rounded(line):
    return frozenset((round(p[0], 9), round(p[1], 9)) for p in line)


def lines(geom):
    return {rounded(line) for line in line_set(geom)}


def mirrored_x(geom):
    """Every drawn line mirrored about x=0.5 (rounded for comparison)."""
    return {rounded({(1.0 - p[0], p[1]) for p in line}) for line in line_set(geom)}


def mirrored_y(geom):
    """Every drawn line mirrored about y=0.5 (rounded for comparison)."""
    return {rounded({(p[0], 1.0 - p[1]) for p in line}) for line in line_set(geom)}


class TestClipHelper:
    """clip_segment_to_bounds: Liang–Barsky style clip of a segment."""

    UNIT = Bounds(x=0.0, y=0.0, w=1.0, h=1.0)

    def test_fully_inside_returned_as_is(self):
        seg = Segment((0.2, 0.3), (0.7, 0.6))
        assert clip_segment_to_bounds(seg, self.UNIT) == seg

    def test_fully_outside_returns_none(self):
        seg = Segment((2.0, 2.0), (3.0, 3.0))
        assert clip_segment_to_bounds(seg, self.UNIT) is None

    def test_crossing_two_sides_clipped(self):
        """Diagonal from outside top-right through center to bottom-left."""
        seg = Segment((2.0, 2.0), (-1.0, -1.0))
        out = clip_segment_to_bounds(seg, self.UNIT)
        assert out is not None
        pts = {out.p1, out.p2}
        assert (0.0, 0.0) in pts
        # exits at x=1 -> y=1 (corner) : the far endpoint clips to (1,1)
        assert (1.0, 1.0) in pts

    def test_one_endpoint_outside(self):
        seg = Segment((0.25, 0.25), (1.5, 0.25))
        out = clip_segment_to_bounds(seg, self.UNIT)
        assert out == Segment((0.25, 0.25), (1.0, 0.25))

    def test_degenerate_point_on_corner_inside(self):
        """Both endpoints == one corner (zero length) inside -> returned as is."""
        seg = Segment((0.0, 0.0), (0.0, 0.0))
        assert clip_segment_to_bounds(seg, self.UNIT) == seg

    def test_distance_to_interior_corner(self):
        """Segment exiting through the top edge mid-way."""
        seg = Segment((0.5, 0.5), (0.5, 2.0))
        out = clip_segment_to_bounds(seg, self.UNIT)
        assert out == Segment((0.5, 0.5), (0.5, 1.0))


class TestSchema:
    def test_identity(self):
        assert DiagonalMethod.id == "diagonal_method"
        assert DiagonalMethod.name == "Diagonal Method"
        assert DiagonalMethod().escapes_bounds is False
        assert get("diagonal_method") is DiagonalMethod

    def test_params_schema(self):
        assert DiagonalMethod.params == (
            Param(name="show_45_bisectors", kind="bool", default=True),
            Param(
                name="variant",
                kind="choice",
                default="grid_only",
                choices=("grid_only", "panels"),
            ),
        )

    def test_default_params(self):
        assert default_params(DiagonalMethod) == {
            "show_45_bisectors": True,
            "variant": "grid_only",
        }


class TestDiagonals45Deg:
    def test_diagonals_stay_45deg_in_doc_coords(self):
        """Pinned (ruled): one doc-45° line out of each frame corner
        -> normalized slope magnitude == w/h for every line.

        For Bounds w=1000, h=500: slope = +/- 2.0; after element_matrix
        scaling to doc (w, h) the doc angle becomes atan(w/h * h/w) = 45 deg.
        """
        geom = DiagonalMethod().build(
            BOUNDS_2_1, {"show_45_bisectors": False, "variant": "grid_only"}
        )
        segs = segments(geom)
        assert len(segs) == 4
        slopes = sorted(slope(s) for s in segs)
        assert slopes == pytest.approx([-2.0, -2.0, 2.0, 2.0])

    def test_square_canvas_slope_one(self):
        """Glanceable: 1:1 canvas -> corner-to-corner diagonals, slopes +/-1."""
        geom = DiagonalMethod().build(
            BOUNDS_1_1, {"show_45_bisectors": False, "variant": "grid_only"}
        )
        slopes = sorted(slope(s) for s in segments(geom))
        assert slopes == pytest.approx([-1.0, 1.0])

    def test_diagonals_start_at_all_four_corners(self):
        """Ruled pin: every frame corner, not only the top two."""
        geom = DiagonalMethod().build(
            BOUNDS_2_1, {"show_45_bisectors": False, "variant": "grid_only"}
        )
        starts = {tuple(s.p1) for s in segments(geom)} | {
            tuple(s.p2) for s in segments(geom)
        }
        for corner in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
            assert corner in starts


class TestSymmetryAndUniqueness:
    @pytest.mark.parametrize(
        "bounds",
        [
            Bounds(0, 0, 1000.0, 500.0),
            Bounds(0, 0, 500.0, 1000.0),
            Bounds(0, 0, 500.0, 500.0),
            Bounds(0, 0, 1000.0, 300.0),
            Bounds(0, 0, 300.0, 1000.0),
            Bounds(0, 0, 7.0, 5.0),
            Bounds(0, 0, 5.0, 7.0),
        ],
    )
    def test_mirror_symmetric_about_both_midlines(self, bounds):
        """The star is symmetric about x=0.5 and y=0.5 for any aspect."""
        geom = DiagonalMethod().build(bounds, {"variant": "grid_only"})
        assert lines(geom) == mirrored_x(geom)
        assert lines(geom) == mirrored_y(geom)

    @pytest.mark.parametrize("bounds", [
        Bounds(0, 0, 1000.0, 500.0),
        Bounds(0, 0, 500.0, 1000.0),
        Bounds(0, 0, 500.0, 500.0),
        Bounds(0, 0, 1000.0, 300.0),
        Bounds(0, 0, 300.0, 1000.0),
    ])
    def test_no_line_is_emitted_twice(self, bounds):
        """No duplicate segments (endpoints in either order), so an
        overlapping semi-transparent stroke never draws darker."""
        for params in (
            {"variant": "grid_only"},
            {"variant": "panels"},
            {"show_45_bisectors": False},
        ):
            geom = DiagonalMethod().build(bounds, params)
            assert len(line_set(geom)) == len(segments(geom))


class TestBuild:
    def test_grid_only_default_landscape(self):
        """2:1: the corner diagonals already cover both long-side midpoints,
        so the bisector rays are duplicates and only 4 lines remain."""
        geom = DiagonalMethod().build(BOUNDS_2_1, {"show_45_bisectors": True})
        assert all(isinstance(p, Segment) for p in geom)
        for s in segments(geom):
            assert primitive_within_bounds(s, Bounds(0, 0, 1, 1))
        assert len(segments(geom)) == 4

    def test_diagonal_endpoints_pinned_landscape(self):
        """Ruled pin: doc-45° lines from all four corners.

        From (0,0) with normalized slope +w/h = 2: y = 2x hits y=1 at x=0.5;
        mirrored from every other corner.
        """
        geom = DiagonalMethod().build(BOUNDS_2_1, {"show_45_bisectors": False})
        assert lines(geom) == {
            frozenset({(0.0, 0.0), (0.5, 1.0)}),
            frozenset({(1.0, 0.0), (0.5, 1.0)}),
            frozenset({(1.0, 1.0), (0.5, 0.0)}),
            frozenset({(0.0, 1.0), (0.5, 0.0)}),
        }

    def test_bisectors_kept_from_long_side_midpoints(self):
        """A wide frame (1000x300): 4 corner lines + 4 distinct bisector rays
        from the top/bottom midpoints, both horizontal directions each."""
        wide = Bounds(x=0.0, y=0.0, w=1000.0, h=300.0)
        geom = DiagonalMethod().build(wide, {"variant": "grid_only"})
        corners = [
            s for s in segments(geom)
            if tuple(s.p1) in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        ]
        kept = [s for s in segments(geom) if s not in corners]
        assert len(corners) == 4
        assert len(kept) == 4
        for s in kept:
            assert abs(slope(s)) == pytest.approx(wide.w / wide.h, abs=1e-9)
        endpoints = {tuple(s.p1) for s in kept} | {tuple(s.p2) for s in kept}
        assert (0.5, 0.0) in endpoints  # top midpoint
        assert (0.5, 1.0) in endpoints  # bottom midpoint
        off_midpoints = {p for p in endpoints if p not in ((0.5, 0.0), (0.5, 1.0))}
        assert sorted(p[0] for p in off_midpoints) == [0.2, 0.2, 0.8, 0.8]

    def test_diagonal_method_geometry_changes_with_aspect(self):
        """Glanceable: different bounds aspect -> different geometry."""
        g21 = DiagonalMethod().build(
            BOUNDS_2_1, {"show_45_bisectors": False, "variant": "grid_only"}
        )
        g11 = DiagonalMethod().build(
            BOUNDS_1_1, {"show_45_bisectors": False, "variant": "grid_only"}
        )
        assert g21 != g11

    def test_panels_adds_exactly_two_midlines(self):
        base = DiagonalMethod().build(BOUNDS_2_1, {"variant": "grid_only"})
        panels = DiagonalMethod().build(BOUNDS_2_1, {"variant": "panels"})
        extra = [s for s in segments(panels) if s not in segments(base)]
        assert len(extra) == 2
        assert sorted(s.p1[0] for s in extra if s.p1[0] == s.p2[0]) == [0.5]
        assert sorted(s.p1[1] for s in extra if s.p1[1] == s.p2[1]) == [0.5]
        assert len(segments(panels)) == len(segments(base)) + 2

    def test_panels_midline_positions(self):
        panels = DiagonalMethod().build(BOUNDS_2_1, {"variant": "panels"})
        mids = midlines(panels)
        assert len(mids) == 2
        assert Segment((0.5, 0.0), (0.5, 1.0)) in mids
        assert Segment((0.0, 0.5), (1.0, 0.5)) in mids

    def test_panels_variant_unchanged_by_symmetry_fix(self):
        """panels == grid_only plus exactly the two midlines, any aspect."""
        for bounds in (BOUNDS_2_1, Bounds(0, 0, 500.0, 1000.0)):
            grid = DiagonalMethod().build(bounds, {"variant": "grid_only"})
            panels = DiagonalMethod().build(bounds, {"variant": "panels"})
            assert set(panels) - set(grid) == {
                Segment((0.5, 0.0), (0.5, 1.0)),
                Segment((0.0, 0.5), (1.0, 0.5)),
            }

    def test_show_45_bisectors_false_leaves_only_diagonals(self):
        geom = DiagonalMethod().build(BOUNDS_2_1, {"show_45_bisectors": False})
        assert len(segments(geom)) == 4

    def test_all_primitives_within_bounds_panels(self):
        unit = Bounds(0, 0, 1, 1)
        panels = DiagonalMethod().build(BOUNDS_2_1, {"variant": "panels"})
        for s in segments(panels):
            assert primitive_within_bounds(s, unit)

    def test_tall_canvas_portrait_bisectors(self):
        """Portrait: corner-start lines span the short sides and the bisector
        rays duplicate them, so 4 lines remain — no exact repeats.

        For 500x1000 (w/h = 0.5) the corner lines run (0,0)->(1,0.5),
        (1,0)->(0,0.5), (0,1)->(1,0.5) and (1,1)->(0,0.5); every line has
        normalized slope magnitude w/h = 0.5, i.e. 45 deg in doc space.
        """
        tall = Bounds(x=0.0, y=0.0, w=500.0, h=1000.0)
        geom = DiagonalMethod().build(tall, {"variant": "grid_only"})
        for s in segments(geom):
            assert primitive_within_bounds(s, Bounds(0, 0, 1, 1))
        kept_slopes = sorted(abs(slope(s)) for s in segments(geom))
        assert kept_slopes == pytest.approx([0.5] * 4)
        assert lines(geom) == mirrored_x(geom)
        assert lines(geom) == mirrored_y(geom)
        assert (0.0, 0.5) in {tuple(s.p1) for s in segments(geom)} | {
            tuple(s.p2) for s in segments(geom)
        }

    def test_portrait_bisectors_shoot_both_ways(self):
        """Tall-but-not-2:1 frame: the left/right midpoints each add two rays."""
        tall = Bounds(x=0.0, y=0.0, w=300.0, h=1000.0)
        geom = DiagonalMethod().build(tall, {"variant": "grid_only"})
        assert len(segments(geom)) == 8
        endpoints = {tuple(s.p1) for s in segments(geom)} | {
            tuple(s.p2) for s in segments(geom)
        }
        assert (0.0, 0.5) in endpoints
        assert (1.0, 0.5) in endpoints
