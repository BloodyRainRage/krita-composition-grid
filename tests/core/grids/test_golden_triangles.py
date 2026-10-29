"""Tests for the Golden Triangles grid type.

Pinned construction: the frame diagonal plus, from each of the two corners
off the diagonal, the segment perpendicular to it IN DOC SPACE, ending at its
foot on the diagonal, and one Dot per foot.  The geometry therefore follows
the element aspect (a normalized-space perpendicular would not be one in
pixels), which is why the tests re-derive the feet from ``bounds.w``/``bounds.h``.
"""

import math

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment, primitive_within_bounds
from composition_grid.core.grids import (
    GoldenTriangles,
    Param,
    all_types,
    default_params,
    get,
)

BOUNDS_3_2 = Bounds(x=0.0, y=0.0, w=3.0, h=2.0)
BOUNDS_2_3 = Bounds(x=0.0, y=0.0, w=2.0, h=3.0)
BOUNDS_SQUARE = Bounds(x=0.0, y=0.0, w=5.0, h=5.0)

# Main-diagonal feet for a 3:2 frame: t = w^2 / (w^2 + h^2) == 9/13.
T_3_2 = 9.0 / 13.0
T_2_3 = 4.0 / 13.0


def segments_of(geom):
    return [p for p in geom if isinstance(p, Segment)]


def dots_of(geom):
    return [p for p in geom if isinstance(p, Dot)]


def corner_names(diagonal):
    return diagonal.split("_")


_CORNER = {"tl": (0.0, 0.0), "tr": (1.0, 0.0), "br": (1.0, 1.0), "bl": (0.0, 1.0)}


def doc_point(bounds, p):
    """Normalized point -> element-local doc point."""
    return (p[0] * bounds.w, p[1] * bounds.h)


def assert_point(actual, expected):
    assert actual[0] == pytest.approx(expected[0], abs=1e-12)
    assert actual[1] == pytest.approx(expected[1], abs=1e-12)


class TestSchema:
    def test_identity(self):
        assert GoldenTriangles.id == "golden_triangles"
        assert GoldenTriangles.name == "Golden Triangles"
        assert GoldenTriangles().escapes_bounds is False
        assert get("golden_triangles") is GoldenTriangles

    def test_params_schema(self):
        """Only two orientations remain: the reversed pairs draw the same
        geometry (``br_tl``/``bl_tr`` are legacy aliases, not choices)."""
        assert GoldenTriangles.params == (
            Param(
                name="diagonal",
                kind="choice",
                default="tl_br",
                choices=("tl_br", "tr_bl"),
            ),
        )

    def test_default_params(self):
        assert default_params(GoldenTriangles) == {"diagonal": "tl_br"}

    def test_registration_order(self):
        ids = [t.id for t in all_types()]
        assert ids[7] == "golden_spiral"
        assert ids[8] == "golden_triangles"
        assert ids[9] == "diagonal_method"


class TestBuild:
    def test_default_output_shape(self):
        """diagonal, 2 perpendiculars (canonical start-corner order), 2 feet."""
        geom = GoldenTriangles().build(BOUNDS_3_2, {})
        assert segments_of(geom)[0] == Segment((0.0, 0.0), (1.0, 1.0))
        assert segments_of(geom)[1].p1 == (1.0, 0.0)
        assert_point(segments_of(geom)[1].p2, (T_3_2, T_3_2))
        assert segments_of(geom)[2].p1 == (0.0, 1.0)
        assert_point(segments_of(geom)[2].p2, (1.0 - T_3_2, 1.0 - T_3_2))
        assert_point(dots_of(geom)[0].position, (T_3_2, T_3_2))
        assert_point(dots_of(geom)[1].position, (1.0 - T_3_2, 1.0 - T_3_2))
        # the rays end exactly at their dots
        for seg, dot in zip(segments_of(geom)[1:], dots_of(geom)):
            assert seg.p2 == dot.position

    def test_anti_diagonal_is_the_x_mirror(self):
        geom = GoldenTriangles().build(BOUNDS_3_2, {"diagonal": "tr_bl"})
        assert geom[0] == Segment((1.0, 0.0), (0.0, 1.0))
        # Start corners off the anti-diagonal, canonical order: tl, br.
        assert geom[1].p1 == (0.0, 0.0)
        assert_point(geom[1].p2, (1.0 - T_3_2, T_3_2))
        assert geom[2].p1 == (1.0, 1.0)
        assert_point(geom[2].p2, (T_3_2, 1.0 - T_3_2))
        assert_point(dots_of(geom)[0].position, (1.0 - T_3_2, T_3_2))
        assert_point(dots_of(geom)[1].position, (T_3_2, 1.0 - T_3_2))

    def test_feet_are_doc_space_perpendiculars(self):
        """3:2 and 2:3: the dot is the foot of the perpendicular and the ray
        is orthogonal to the diagonal in DOC pixels (not in normalized space)."""
        for bounds, t in ((BOUNDS_3_2, T_3_2), (BOUNDS_2_3, T_2_3)):
            for diagonal in ("tl_br", "tr_bl"):
                geom = GoldenTriangles().build(bounds, {"diagonal": diagonal})
                start, end = corner_names(diagonal)
                diag_doc = (
                    doc_point(bounds, _CORNER[start]),
                    doc_point(bounds, _CORNER[end]),
                )
                for seg in segments_of(geom)[1:]:
                    ray = (
                        doc_point(bounds, seg.p2)[0] - doc_point(bounds, seg.p1)[0],
                        doc_point(bounds, seg.p2)[1] - doc_point(bounds, seg.p1)[1],
                    )
                    line = (diag_doc[1][0] - diag_doc[0][0], diag_doc[1][1] - diag_doc[0][1])
                    cos = (ray[0] * line[0] + ray[1] * line[1]) / (
                        math.hypot(*ray) * math.hypot(*line)
                    )
                    assert abs(cos) < 1e-12, (bounds, diagonal, seg)

    def test_feet_lie_on_the_diagonal(self):
        for bounds in (BOUNDS_3_2, BOUNDS_2_3, BOUNDS_SQUARE):
            for diagonal in ("tl_br", "tr_bl"):
                geom = GoldenTriangles().build(bounds, {"diagonal": diagonal})
                diagonal_seg = segments_of(geom)[0]
                for dot in dots_of(geom):
                    cross = (
                        (dot.position[0] - diagonal_seg.p1[0])
                        * (diagonal_seg.p2[1] - diagonal_seg.p1[1])
                        - (dot.position[1] - diagonal_seg.p1[1])
                        * (diagonal_seg.p2[0] - diagonal_seg.p1[0])
                    )
                    assert abs(cross) < 1e-12

    def test_square_frame_feet_coincide_at_the_center(self):
        """Classic construction: for a square both perpendiculars meet at the
        diagonal intersection, so the two feet are the center."""
        geom = GoldenTriangles().build(BOUNDS_SQUARE, {})
        assert dots_of(geom) == [Dot((0.5, 0.5)), Dot((0.5, 0.5))]
        assert segments_of(geom)[1] == Segment((1.0, 0.0), (0.5, 0.5))
        assert segments_of(geom)[2] == Segment((0.0, 1.0), (0.5, 0.5))

    def test_geometry_follows_the_element_aspect(self):
        """A normalized-space perpendicular would be the same for 3:2 and
        2:3; the doc-space one is not."""
        assert (
            GoldenTriangles().build(BOUNDS_3_2, {})
            != GoldenTriangles().build(BOUNDS_2_3, {})
        )

    def test_two_dots_and_three_segments(self):
        for bounds in (BOUNDS_3_2, BOUNDS_2_3, BOUNDS_SQUARE, Bounds(0, 0, 1000, 600)):
            for diagonal in ("tl_br", "tr_bl"):
                geom = GoldenTriangles().build(bounds, {"diagonal": diagonal})
                assert len(segments_of(geom)) == 3
                assert len(dots_of(geom)) == 2

    def test_primitives_within_bounds_all_orientations(self):
        unit = Bounds(0, 0, 1, 1)
        for bounds in (BOUNDS_3_2, BOUNDS_2_3, BOUNDS_SQUARE):
            for diagonal in ("tl_br", "tr_bl"):
                geom = GoldenTriangles().build(bounds, {"diagonal": diagonal})
                for p in geom:
                    assert primitive_within_bounds(p, unit), (bounds, diagonal, p)


class TestLegacyChoices:
    def test_reversed_orientations_alias_to_the_kept_ones(self):
        """br_tl -> tl_br, bl_tr -> tr_bl: identical geometry, no crash."""
        for bounds in (BOUNDS_3_2, BOUNDS_2_3, BOUNDS_SQUARE):
            assert GoldenTriangles().build(
                bounds, {"diagonal": "br_tl"}
            ) == GoldenTriangles().build(bounds, {"diagonal": "tl_br"})
            assert GoldenTriangles().build(
                bounds, {"diagonal": "bl_tr"}
            ) == GoldenTriangles().build(bounds, {"diagonal": "tr_bl"})

    def test_unknown_diagonal_falls_back_to_tl_br(self):
        geom = GoldenTriangles().build(BOUNDS_3_2, {"diagonal": "nope"})
        assert geom == GoldenTriangles().build(BOUNDS_3_2, {"diagonal": "tl_br"})

    def test_missing_diagonal_uses_the_default(self):
        assert GoldenTriangles().build(BOUNDS_3_2, {}) == GoldenTriangles().build(
            BOUNDS_3_2, {"diagonal": "tl_br"}
        )


class TestDegenerateBounds:
    @pytest.mark.parametrize(
        "bounds",
        [
            Bounds(0, 0, 0.0, 5.0),
            Bounds(0, 0, 5.0, 0.0),
            Bounds(0, 0, -1.0, 5.0),
            Bounds(0, 0, 5.0, -1.0),
        ],
    )
    def test_returns_empty_geometry(self, bounds):
        assert GoldenTriangles().build(bounds, {}) == ()
        assert GoldenTriangles().build(bounds, {"diagonal": "tr_bl"}) == ()
