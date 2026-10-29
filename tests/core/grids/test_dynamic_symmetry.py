"""Tests for the Dynamic Symmetry grid type.

Pinned geometry: a root-n rectangle — short:long DOC
ratio ``1:root_n`` (the classic Hambidge rectangle) — inscribed in the
element, centered, fitted by the smaller side and oriented like the
element (landscape root rect for wide/square frames, portrait root rect
for tall frames; pinned by the 300x900 root-3 test).  Emitted
coordinates are normalized element-local; the insets are ANISOTROPIC by
construction (``Bounds(px/w, py/h, w_r/w, h_r/h)``): the doc-space
1:sqrt(n) ratio is real, and the later element_matrix mapping
reproduces it from these normalized coordinates.

The reciprocals are the classic Hambidge construction, measured in DOC
space (perpendicularity is a real-space notion): FOUR of them — from ne and
from sw perpendicular to the nw->se diagonal, from nw and from se
perpendicular to the sw->ne diagonal — each starting at its own corner,
passing through the foot and produced until it leaves the inner rect.
"""

import math

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import (
    Segment,
    primitive_within_bounds,
)
from composition_grid.core.grids import (
    DynamicSymmetry,
    Param,
    all_types,
    default_params,
    get,
)

BOUNDS_500 = Bounds(x=0.0, y=0.0, w=500.0, h=500.0)
BOUNDS_TALL = Bounds(x=0.0, y=0.0, w=300.0, h=900.0)

# Pinned constants (exact literals).
ROOT2_INNER_H = 0.7071067811865476  # 500/√2 / 500 == 1/sqrt(2)
ROOT2_INNER_PY = (1.0 - ROOT2_INNER_H) / 2.0  # == 0.14644660940672627
ROOT3_INNER_H = 0.5773502691896257  # 300*sqrt(3) / 900 == 1/sqrt(3)
ROOT3_INNER_PY = (1.0 - ROOT3_INNER_H) / 2.0  # == 0.21132486540518715


def segments(geom):
    return [p for p in geom if isinstance(p, Segment)]


def is_side(seg):
    """Axis-aligned (horizontal or vertical) segment."""
    return seg.p1[0] == seg.p2[0] or seg.p1[1] == seg.p2[1]


def _point_close(p, q):
    return all(
        math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9) for a, b in zip(p, q)
    )


def _ends_match(seg, a, b):
    """Whether the segment connects points ``a`` and ``b`` (either order)."""
    return (_point_close(seg.p1, a) and _point_close(seg.p2, b)) or (
        _point_close(seg.p1, b) and _point_close(seg.p2, a)
    )


class TestSchema:
    def test_identity(self):
        assert DynamicSymmetry.id == "dynamic_symmetry"
        assert DynamicSymmetry.name == "Dynamic Symmetry"
        assert DynamicSymmetry().escapes_bounds is False
        assert get("dynamic_symmetry") is DynamicSymmetry

    def test_params_schema(self):
        assert DynamicSymmetry.params == (
            Param(
                name="root",
                kind="choice",
                default="2",
                choices=("2", "3", "5"),
            ),
            Param(name="show_reciprocal", kind="bool", default=True),
        )

    def test_default_params(self):
        assert default_params(DynamicSymmetry) == {
            "root": "2",
            "show_reciprocal": True,
        }

    def test_registration_order_tail(self):
        """Dynamic_symmetry and perspective_grid complete the library."""
        assert [t.id for t in all_types()][-2:] == [
            "dynamic_symmetry",
            "perspective_grid",
        ]


class TestBuild:
    def test_default_output_shape(self):
        """4 inner-rect sides + 2 inner diagonals + 1 reciprocal."""
        geom = DynamicSymmetry().build(BOUNDS_500, {})
        assert all(isinstance(p, Segment) for p in geom)
        assert len(geom) == 7

    def test_root2_fits(self):
        """500x500, root=2: inner rect (0, 0.1464466..., 1, 0.7071067...)."""
        geom = DynamicSymmetry().build(BOUNDS_500, {})
        y0 = ROOT2_INNER_PY
        y1 = y0 + ROOT2_INNER_H
        sides = [s for s in segments(geom) if is_side(s)]
        assert len(sides) == 4
        assert _ends_match(sides[0], (0.0, y0), (1.0, y0))  # top
        assert _ends_match(sides[1], (0.0, y1), (1.0, y1))  # bottom
        assert _ends_match(sides[2], (0.0, y0), (0.0, y1))  # left
        assert _ends_match(sides[3], (1.0, y0), (1.0, y1))  # right

    def test_root2_pinned_py_literal(self):
        y0 = ROOT2_INNER_PY
        assert y0 == pytest.approx(0.14644660940672627, abs=1e-15)

    def test_root3_fits(self):
        """300x900 portrait tall, root=3: inner rect (0, 0.2113248..., 1, 0.5773502...)."""
        geom = DynamicSymmetry().build(BOUNDS_TALL, {"root": "3"})
        y0 = ROOT3_INNER_PY
        y1 = y0 + ROOT3_INNER_H
        sides = [s for s in segments(geom) if is_side(s)]
        assert len(sides) == 4
        assert _ends_match(sides[0], (0.0, y0), (1.0, y0))  # top
        assert _ends_match(sides[1], (0.0, y1), (1.0, y1))  # bottom
        assert _ends_match(sides[2], (0.0, y0), (0.0, y1))  # left
        assert _ends_match(sides[3], (1.0, y0), (1.0, y1))  # right

    def test_two_corner_to_corner_inner_diagonals(self):
        geom = DynamicSymmetry().build(BOUNDS_500, {})
        y0 = ROOT2_INNER_PY
        y1 = y0 + ROOT2_INNER_H
        assert _ends_match(segments(geom)[4], (0.0, y0), (1.0, y1))  # nw->se
        assert _ends_match(segments(geom)[5], (0.0, y1), (1.0, y0))  # sw->ne


def inner_rect_of(bounds: Bounds, root_n: float) -> Bounds:
    """The inscribed root-n rect in NORMALIZED coords (the grid's own rule)."""
    w, h = bounds.w, bounds.h
    if w >= h:
        rect_h = min(h, w / root_n)
        rect_w = root_n * rect_h
    else:
        rect_w = min(w, h / root_n)
        rect_h = root_n * rect_w
    return Bounds(
        (w - rect_w) / 2.0 / w,
        (h - rect_h) / 2.0 / h,
        rect_w / w,
        rect_h / h,
    )


def reciprocals(geom, inner: Bounds):
    """The four Hambidge reciprocals: every segment that is neither an
    inner-rect side nor one of the two corner-to-corner diagonals."""
    nw, ne, se, sw = inner.corners()
    return [
        s
        for s in segments(geom)
        if not is_side(s) and not _ends_match(s, nw, se) and not _ends_match(s, sw, ne)
    ]


def corner_index(point, inner: Bounds) -> int:
    """Index into ``inner.corners()`` (nw, ne, se, sw) of a corner point."""
    for index, corner in enumerate(inner.corners()):
        if _point_close(point, corner):
            return index
    raise AssertionError(f"{point!r} is not an inner-rect corner")


def to_doc(bounds: Bounds, point) -> tuple[float, float]:
    """Normalized point -> element-local doc point (the anisotropic inverse)."""
    return (point[0] * bounds.w, point[1] * bounds.h)


def foot_of(point, line_from, direction) -> tuple[tuple[float, float], float]:
    """Doc-space projection of ``point`` onto ``line_from + t * direction``."""
    length_sq = direction[0] ** 2 + direction[1] ** 2
    t = (
        (point[0] - line_from[0]) * direction[0]
        + (point[1] - line_from[1]) * direction[1]
    ) / length_sq
    return (line_from[0] + t * direction[0], line_from[1] + t * direction[1]), t


# (bounds, root choice) pairs: a square, a wide and a tall element.
CASES = (
    pytest.param(BOUNDS_500, "2", id="square-root2"),
    pytest.param(Bounds(0.0, 0.0, 1000.0, 500.0), "2", id="wide-root2"),
    pytest.param(BOUNDS_TALL, "3", id="tall-root3"),
)


class TestBuild:
    def test_default_output_shape(self):
        """4 inner-rect sides + 2 inner diagonals + 4 reciprocals."""
        geom = DynamicSymmetry().build(BOUNDS_500, {})
        assert all(isinstance(p, Segment) for p in geom)
        assert len(geom) == 10

    def test_root2_fits(self):
        """500x500, root=2: inner rect (0, 0.1464466..., 1, 0.7071067...)."""
        geom = DynamicSymmetry().build(BOUNDS_500, {})
        y0 = ROOT2_INNER_PY
        y1 = y0 + ROOT2_INNER_H
        sides = [s for s in segments(geom) if is_side(s)]
        assert len(sides) == 4
        assert _ends_match(sides[0], (0.0, y0), (1.0, y0))  # top
        assert _ends_match(sides[1], (0.0, y1), (1.0, y1))  # bottom
        assert _ends_match(sides[2], (0.0, y0), (0.0, y1))  # left
        assert _ends_match(sides[3], (1.0, y0), (1.0, y1))  # right

    def test_root2_pinned_py_literal(self):
        y0 = ROOT2_INNER_PY
        assert y0 == pytest.approx(0.14644660940672627, abs=1e-15)

    def test_root3_fits(self):
        """300x900 portrait tall, root=3: inner rect (0, 0.2113248..., 1, 0.5773502...)."""
        geom = DynamicSymmetry().build(BOUNDS_TALL, {"root": "3"})
        y0 = ROOT3_INNER_PY
        y1 = y0 + ROOT3_INNER_H
        sides = [s for s in segments(geom) if is_side(s)]
        assert len(sides) == 4
        assert _ends_match(sides[0], (0.0, y0), (1.0, y0))  # top
        assert _ends_match(sides[1], (0.0, y1), (1.0, y1))  # bottom
        assert _ends_match(sides[2], (0.0, y0), (0.0, y1))  # left
        assert _ends_match(sides[3], (1.0, y0), (1.0, y1))  # right

    def test_two_corner_to_corner_inner_diagonals(self):
        geom = DynamicSymmetry().build(BOUNDS_500, {})
        y0 = ROOT2_INNER_PY
        y1 = y0 + ROOT2_INNER_H
        assert _ends_match(segments(geom)[4], (0.0, y0), (1.0, y1))  # nw->se
        assert _ends_match(segments(geom)[5], (0.0, y1), (1.0, y0))  # sw->ne


class TestReciprocals:
    @pytest.mark.parametrize("bounds,root", CASES)
    def test_four_reciprocals_one_per_corner(self, bounds, root):
        """ne and sw drop onto nw->se, nw and se onto sw->ne: one full
        reciprocal per inner-rect corner, emitted after the 6 base lines."""
        inner = inner_rect_of(bounds, math.sqrt(float(root)))
        geom = DynamicSymmetry().build(bounds, {"root": root})
        recip = reciprocals(geom, inner)
        assert len(recip) == 4
        assert {corner_index(s.p1, inner) for s in recip} == {0, 1, 2, 3}
        assert geom[6:] == tuple(recip)

    @pytest.mark.parametrize("bounds,root", CASES)
    def test_reciprocals_are_perpendicular_in_doc_space(self, bounds, root):
        """Perpendicularity is a DOC-space notion: on a non-square element
        the same construction in normalized space would not be orthogonal."""
        inner = inner_rect_of(bounds, math.sqrt(float(root)))
        rect_w, rect_h = inner.w * bounds.w, inner.h * bounds.h
        geom = DynamicSymmetry().build(bounds, {"root": root})
        for seg in reciprocals(geom, inner):
            index = corner_index(seg.p1, inner)
            # ne (1) and sw (3) drop onto the nw->se diagonal; the others
            # onto the sw->ne one.
            diag = (rect_w, rect_h) if index in (1, 3) else (rect_w, -rect_h)
            direction = (
                to_doc(bounds, seg.p2)[0] - to_doc(bounds, seg.p1)[0],
                to_doc(bounds, seg.p2)[1] - to_doc(bounds, seg.p1)[1],
            )
            cos = (direction[0] * diag[0] + direction[1] * diag[1]) / (
                math.hypot(*direction) * math.hypot(*diag)
            )
            assert abs(cos) < 1e-12

    @pytest.mark.parametrize("bounds,root", CASES)
    def test_reciprocal_passes_through_the_perpendicular_foot(self, bounds, root):
        """The analytic doc-space foot lies on the emitted reciprocal,
        strictly between the diagonal's ends and between the ray's ends."""
        inner = inner_rect_of(bounds, math.sqrt(float(root)))
        corners_doc = [to_doc(bounds, c) for c in inner.corners()]
        rect_w, rect_h = inner.w * bounds.w, inner.h * bounds.h
        geom = DynamicSymmetry().build(bounds, {"root": root})
        for seg in reciprocals(geom, inner):
            index = corner_index(seg.p1, inner)
            line_from, diag = (
                (corners_doc[0], (rect_w, rect_h))
                if index in (1, 3)
                else (corners_doc[3], (rect_w, -rect_h))
            )
            foot, t = foot_of(corners_doc[index], line_from, diag)
            assert 0.0 < t < 1.0  # the foot is on the diagonal's interior

            p1, p2 = to_doc(bounds, seg.p1), to_doc(bounds, seg.p2)
            span = (p2[0] - p1[0], p2[1] - p1[1])
            cross = (foot[0] - p1[0]) * span[1] - (foot[1] - p1[1]) * span[0]
            assert abs(cross) < 1e-6 * math.hypot(*span)  # collinear
            along = (
                (foot[0] - p1[0]) * span[0] + (foot[1] - p1[1]) * span[1]
            ) / (span[0] ** 2 + span[1] ** 2)
            assert 0.0 < along < 1.0  # the ray runs THROUGH the foot

    @pytest.mark.parametrize("bounds,root", CASES)
    def test_reciprocals_are_cut_at_the_inner_rect_boundary(self, bounds, root):
        """Extended past the foot: the reciprocal starts at its corner and
        stops where it leaves the inner rect, never further."""
        inner = inner_rect_of(bounds, math.sqrt(float(root)))
        geom = DynamicSymmetry().build(bounds, {"root": root})
        left, right = inner.x, inner.x + inner.w
        top, bottom = inner.y, inner.y + inner.h
        for seg in reciprocals(geom, inner):
            assert corner_index(seg.p1, inner) in (0, 1, 2, 3)
            on_border = (
                math.isclose(seg.p2[0], left, abs_tol=1e-9)
                or math.isclose(seg.p2[0], right, abs_tol=1e-9)
                or math.isclose(seg.p2[1], top, abs_tol=1e-9)
                or math.isclose(seg.p2[1], bottom, abs_tol=1e-9)
            )
            assert on_border, (bounds, root, seg)
            assert primitive_within_bounds(seg, inner, tol=1e-9)

    def test_reciprocal_from_ne_hits_bottom_side_midpoint(self):
        """Sanity check on the classic root-2 rect: the perpendicular from
        ne, produced past the foot, meets the bottom side at its midpoint."""
        for bounds in (BOUNDS_500, Bounds(0.0, 0.0, 1000.0, 500.0)):
            inner = inner_rect_of(bounds, math.sqrt(2.0))
            geom = DynamicSymmetry().build(bounds, {"root": "2"})
            from_ne = [
                s for s in reciprocals(geom, inner) if corner_index(s.p1, inner) == 1
            ]
            assert len(from_ne) == 1
            mid_bottom = (inner.x + inner.w / 2.0, inner.y + inner.h)
            assert from_ne[0].p2 == pytest.approx(mid_bottom, abs=1e-12)

    def test_show_reciprocal_false_drops_them(self):
        inner = inner_rect_of(BOUNDS_500, math.sqrt(2.0))
        geom = DynamicSymmetry().build(BOUNDS_500, {"show_reciprocal": False})
        assert len(geom) == 6  # 4 sides + 2 diagonals only
        assert reciprocals(geom, inner) == []

    def test_no_two_lines_coincide(self):
        """10 distinct lines: sides, diagonals and reciprocals never overlap."""
        inner = inner_rect_of(BOUNDS_500, math.sqrt(2.0))
        geom = DynamicSymmetry().build(BOUNDS_500, {})
        keys = {frozenset((tuple(s.p1), tuple(s.p2))) for s in segments(geom)}
        assert len(keys) == len(geom)
