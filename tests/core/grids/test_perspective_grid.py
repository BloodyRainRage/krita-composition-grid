"""Tests for the Perspective grid type: a one-vanishing-point ray fan.

Pinned semantics: each of the four edges of the element's normalized [0,1]
square is split into ``divisions`` equal segments, and one ray is drawn from
the VP through each division point (corners included, deduplicated) out to
the clip rect ``Bounds(-0.5, -0.5, 2.0, 2.0)``.  A target coinciding with the
VP emits no ray, and two targets sharing a direction from the VP emit one
ray.  A Dot marks the VP; ``show_horizon`` adds the horizontal segment at
``y = vp_y`` across the extended rect's x range.
"""

import math

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import (
    Dot,
    Segment,
    primitive_within_bounds,
)
from composition_grid.core.grids import (
    PerspectiveGrid,
    Param,
    all_types,
    default_params,
    get,
)

BOUNDS = Bounds(x=0.0, y=0.0, w=1000.0, h=600.0)
EXTENDED = Bounds(x=-0.5, y=-0.5, w=2.0, h=2.0)
VP = (0.5, 0.5)
VP_TOL = 1e-9


def build(**params):
    return PerspectiveGrid().build(BOUNDS, params)


def rays_before_dot(geom):
    """The ray fan: every Segment emitted before the single VP Dot."""
    dot_index = next(i for i, p in enumerate(geom) if isinstance(p, Dot))
    return [p for p in geom[:dot_index] if isinstance(p, Segment)]


def rays(divisions=8, vp=VP):
    """The ray fan alone (horizon off), so counts skip that extra segment."""
    return rays_before_dot(
        build(
            vp_x=vp[0],
            vp_y=vp[1],
            divisions=divisions,
            show_horizon=False,
        )
    )


def collinear_with(seg, point, tol=VP_TOL):
    """Cross product of (p1 - point) x (p2 - point) ~ 0."""
    cross = (seg.p1[0] - point[0]) * (seg.p2[1] - point[1]) - (
        seg.p1[1] - point[1]
    ) * (seg.p2[0] - point[0])
    return abs(cross) < tol


def on_segment(seg, point, tol=1e-9):
    """Whether ``point`` lies on the segment (collinearity checked apart)."""
    return (
        min(seg.p1[0], seg.p2[0]) - tol
        <= point[0]
        <= max(seg.p1[0], seg.p2[0]) + tol
        and min(seg.p1[1], seg.p2[1]) - tol
        <= point[1]
        <= max(seg.p1[1], seg.p2[1]) + tol
    )


def edge_targets(divisions):
    """The distinct division points of the four frame edges of the unit square."""
    step = 1.0 / divisions
    return {
        point
        for i in range(divisions + 1)
        for point in (
            (i * step, 0.0),
            (i * step, 1.0),
            (0.0, i * step),
            (1.0, i * step),
        )
    }


def edge_crossings(fan, axis, value):
    """Coordinates along the other axis where a ray crosses the frame line
    ``axis=value`` WITHIN the drawn segment, restricted to the frame span: a
    ray aimed past a corner meets the extended image line outside the frame."""
    other = 1 - axis
    hits = []
    for seg in fan:
        if seg.p1[axis] == seg.p2[axis]:
            continue  # parallel to this image line: no crossing
        t = (value - seg.p1[axis]) / (seg.p2[axis] - seg.p1[axis])
        if not -1e-9 <= t <= 1.0 + 1e-9:
            continue  # the crossing point is not on the drawn ray
        coordinate = seg.p1[other] + t * (seg.p2[other] - seg.p1[other])
        if -1e-9 <= coordinate <= 1.0 + 1e-9:
            hits.append(round(coordinate, 9))
    return sorted(set(hits))


def fan_angles(fan):
    return sorted(
        round(
            math.degrees(math.atan2(s.p2[1] - s.p1[1], s.p2[0] - s.p1[0])),
            6,
        )
        for s in fan
    )


def frame_points(divisions):
    return [i / divisions for i in range(divisions + 1)]


class TestSchema:
    def test_identity(self):
        assert PerspectiveGrid.id == "perspective_grid"
        assert PerspectiveGrid.name == "Perspective"
        assert PerspectiveGrid().escapes_bounds is True
        assert get("perspective_grid") is PerspectiveGrid

    def test_params_schema(self):
        assert PerspectiveGrid.params == (
            Param(name="vp_x", kind="number", default=0.5, minimum=0.0, maximum=1.0),
            Param(name="vp_y", kind="number", default=0.5, minimum=0.0, maximum=1.0),
            Param(name="divisions", kind="number", default=8, minimum=2, maximum=32),
            Param(name="show_horizon", kind="bool", default=True),
        )

    def test_default_params(self):
        assert default_params(PerspectiveGrid) == {
            "vp_x": 0.5,
            "vp_y": 0.5,
            "divisions": 8,
            "show_horizon": True,
        }

    def test_registration_is_last(self):
        assert all_types()[-1] is PerspectiveGrid


class TestRays:
    def test_ray_count_is_one_per_edge_target(self):
        """divisions=4: 4 edges * 4 segments = 16 distinct targets, none
        coinciding with the centred VP, none sharing a direction."""
        assert len(rays(4)) == 16

    def test_ray_count_scales_with_divisions(self):
        for divisions in (2, 3, 4, 8, 32):
            assert len(rays(divisions)) == 4 * divisions, divisions

    def test_divisions_clamped_and_rounded(self):
        # 1 clamps up to 2, 99 clamps down to 32, 4.4 and 3.5 round to 4.
        for divisions, expected in ((1, 8), (99, 128), (4.4, 16), (3.5, 16)):
            assert len(rays(divisions)) == expected, divisions

    def test_every_ray_starts_at_and_passes_through_the_vp(self):
        fan = rays()
        assert len(fan) == 32
        for seg in fan:
            assert seg.p1 == VP
            assert collinear_with(seg, VP)

    def test_every_edge_target_lies_on_a_ray(self):
        fan = rays(4)
        for target in sorted(edge_targets(4)):
            assert any(
                collinear_with(seg, target) and on_segment(seg, target)
                for seg in fan
            ), target

    def test_frame_edges_are_crossed_at_equal_intervals(self):
        """The point of the construction: the rays crossing a frame edge hit
        it at EQUAL intervals, wherever the VP sits."""
        for divisions in (2, 4, 8):
            expected = frame_points(divisions)
            for vp in ((0.5, 0.5), (0.2, 0.8), (0.75, 0.35)):
                fan = rays(divisions, vp)
                for axis, value in ((0, 0.0), (0, 1.0), (1, 0.0), (1, 1.0)):
                    crossings = edge_crossings(fan, axis, value)
                    assert crossings == pytest.approx(expected), (vp, axis, value)
                    gaps = [b - a for a, b in zip(crossings, crossings[1:])]
                    assert all(
                        gap == pytest.approx(1.0 / divisions) for gap in gaps
                    ), (vp, axis, value)

    def test_moving_the_vp_refracts_the_fan_not_the_frame(self):
        """Ray angles change with the VP; the equal edge spacing does not."""
        centre = rays(4)
        off = rays(4, (0.3, 0.65))
        assert len(centre) == len(off) == 16
        assert fan_angles(centre) != fan_angles(off)
        assert {frozenset((s.p1, s.p2)) for s in centre} != {
            frozenset((s.p1, s.p2)) for s in off
        }
        expected = frame_points(4)
        for fan in (centre, off):
            assert edge_crossings(fan, 1, 1.0) == pytest.approx(expected)
            assert edge_crossings(fan, 0, 1.0) == pytest.approx(expected)

    def test_no_rays_are_emitted_twice(self):
        fan = rays(4)
        assert len({frozenset((s.p1, s.p2)) for s in fan}) == len(fan)

    def test_same_direction_targets_collapse_into_one_ray(self):
        """A VP on the left edge sees s=0 and s=0.25 in one direction."""
        fan = rays(4, (0.0, 0.5))
        assert len({frozenset((s.p1, s.p2)) for s in fan}) == len(fan)
        assert len(fan) == 13  # 16 targets - the VP - 2 collinear pairs
        assert [s for s in fan if s.p2 == (0.0, -0.5)] == [
            Segment((0.0, 0.5), (0.0, -0.5))
        ]

    def test_vp_on_an_edge_or_corner_emits_no_degenerate_ray(self):
        for vp in ((0.0, 0.5), (0.5, 0.0), (0.0, 0.0), (1.0, 1.0), (0.25, 1.0)):
            fan = rays(4, vp)
            assert fan, vp
            for seg in fan:
                assert seg.p1 != seg.p2, vp
                assert collinear_with(seg, vp), vp
            assert Dot(vp) in build(vp_x=vp[0], vp_y=vp[1], divisions=4)

    def test_single_vp_dot(self):
        assert [p for p in build() if isinstance(p, Dot)] == [Dot(VP)]

    def test_rays_escape_bounds_per_contract(self):
        """At least one ray leaves the [0,1] element — yet nothing leaves
        the extended rect Bounds(-0.5, -0.5, 2.0, 2.0)."""
        geom = build()
        fan = rays_before_dot(geom)
        assert any(
            min(s.p1[0], s.p2[0]) < 0.0
            or max(s.p1[0], s.p2[0]) > 1.0
            or min(s.p1[1], s.p2[1]) < 0.0
            or max(s.p1[1], s.p2[1]) > 1.0
            for s in fan
        )
        for prim in geom:
            assert primitive_within_bounds(prim, EXTENDED, tol=1e-6)

    def test_every_primitive_stays_in_the_extended_rect(self):
        for vp in ((0.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.1, 0.9)):
            geom = build(vp_x=vp[0], vp_y=vp[1], divisions=16)
            for prim in geom:
                assert primitive_within_bounds(prim, EXTENDED, tol=1e-9), (vp, prim)

    def test_empty_bounds_yield_nothing(self):
        assert PerspectiveGrid().build(Bounds(0.0, 0.0, 0.0, 600.0), {}) == ()


class TestLegacyParamIgnored:
    def test_ray_step_deg_is_ignored(self):
        """Stored settings may still carry the removed angle-fan parameter."""
        expected = build(divisions=5)
        for legacy in (10, 30.0, "15", None):
            assert build(divisions=5, ray_step_deg=legacy) == expected

    def test_ray_step_deg_does_not_reappear_in_the_defaults(self):
        assert "ray_step_deg" not in default_params(PerspectiveGrid)


class TestHorizon:
    def test_horizon_at_vp_y(self):
        geom = build(divisions=4)
        assert len(geom) == 18  # 16 rays + VP dot + horizon
        assert isinstance(geom[-2], Dot)
        assert geom[-1] == Segment((-0.5, VP[1]), (1.5, VP[1]))

    def test_horizon_disabled(self):
        geom = build(divisions=4, show_horizon=False)
        assert len(geom) == 17  # 16 rays + VP dot; no trailing horizon
        assert isinstance(geom[-1], Dot)
