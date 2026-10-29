"""Tests for the Perspective grid type: a one-vanishing-point ray fan.

Pinned semantics: rays leave the VP at DOC-space angles
``alpha = k * ray_step_deg`` (degrees are document-pixel angles, so a doc
direction ``(cos a, sin a)`` maps to the normalized delta
``(cos a * L_doc / w, sin a * L_doc / h)`` with ``L_doc = 2.5*max(w,h)``)
over a HALF turn: ``k = 0 .. ceil(180/step) - 1``.  Each ray is drawn
through the VP in BOTH directions, so a full turn would emit every line
twice.  Each ray is clipped to the extended rect
``Bounds(-0.5, -0.5, 2.0, 2.0)``.  A Dot marks the VP; ``show_horizon``
adds the horizontal segment at ``y = vp_y`` across the extended rect's x
range.
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
BOUNDS_WIDE = Bounds(x=0.0, y=0.0, w=1000.0, h=500.0)
EXTENDED = Bounds(x=-0.5, y=-0.5, w=2.0, h=2.0)
VP = (0.5, 0.5)


def segments(geom):
    return [p for p in geom if isinstance(p, Segment)]


def rays_before_dot(geom):
    """The ray fan: every Segment emitted before the single VP Dot."""
    dot_index = next(i for i, p in enumerate(geom) if isinstance(p, Dot))
    return [p for p in geom[:dot_index] if isinstance(p, Segment)]


def collinear_with(seg, vp, tol=1e-9):
    """Cross product of (p1 - vp) x (p2 - vp) ~ 0."""
    cross = (seg.p1[0] - vp[0]) * (seg.p2[1] - vp[1]) - (seg.p1[1] - vp[1]) * (
        seg.p2[0] - vp[0]
    )
    return abs(cross) < tol


def endpoint_outside_unit(p, tol=1e-9):
    x, y = p
    return (x < -tol or x > 1.0 + tol or y < -tol or y > 1.0 + tol)


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
            Param(
                name="ray_step_deg",
                kind="number",
                default=10,
                minimum=1.0,
                maximum=30.0,
            ),
            Param(name="show_horizon", kind="bool", default=True),
        )

    def test_default_params(self):
        assert default_params(PerspectiveGrid) == {
            "vp_x": 0.5,
            "vp_y": 0.5,
            "ray_step_deg": 10,
            "show_horizon": True,
        }

    def test_registration_is_last(self):
        assert all_types()[-1] is PerspectiveGrid


class TestRays:
    def test_ray_count_matches_step(self):
        """Half turn: one line per direction, so ceil(180/step) rays."""
        for step, expected in ((10, 18), (30, 6)):
            geom = PerspectiveGrid().build(
                BOUNDS, {"ray_step_deg": step, "show_horizon": False}
            )
            assert len(segments(geom)) == expected

    def test_ray_count_clamped_step(self):
        geom = PerspectiveGrid().build(
            BOUNDS, {"ray_step_deg": 45.0, "show_horizon": False}
        )
        assert len(segments(geom)) == 6  # 45 clamps to 30 -> 180/30
        geom = PerspectiveGrid().build(
            BOUNDS, {"ray_step_deg": 0.0, "show_horizon": False}
        )
        assert len(segments(geom)) == 180  # 0 clamps to 1 -> 180/1

    def test_no_rays_are_emitted_twice(self):
        """A ray through the VP covers both sides, so alpha and alpha+180
        must not both be emitted (endpoint sets are unique)."""
        geom = PerspectiveGrid().build(BOUNDS, {"show_horizon": False})
        rays = rays_before_dot(geom)
        keys = {frozenset((s.p1, s.p2)) for s in rays}
        assert len(keys) == len(rays)
        assert len(rays) == 18

    def test_default_output_shape(self):
        """18 rays + the VP dot + the horizon line."""
        geom = PerspectiveGrid().build(BOUNDS, {})
        assert len(geom) == 20
        assert isinstance(geom[-2], Dot)
        assert isinstance(geom[-1], Segment)

    def test_rays_start_at_vp(self):
        """Every kept ray passes through the VP (cross product ~ 0)."""
        geom = PerspectiveGrid().build(BOUNDS, {})
        rays = rays_before_dot(geom)
        assert len(rays) == 18
        assert Dot(VP) in geom
        for seg in rays:
            assert collinear_with(seg, VP)

    def test_single_vp_dot(self):
        dots = [p for p in PerspectiveGrid().build(BOUNDS, {}) if isinstance(p, Dot)]
        assert dots == [Dot(VP)]

    def test_rays_escape_bounds_per_contract(self):
        """At least one ray leaves the [0,1] element — yet nothing leaves
        the extended rect Bounds(-0.5, -0.5, 2.0, 2.0)."""
        geom = PerspectiveGrid().build(BOUNDS, {})
        rays = rays_before_dot(geom)
        escapees = [s for s in rays if endpoint_outside_unit(s.p1) or endpoint_outside_unit(s.p2)]
        assert escapees  # at least one ray leaves the element bounds
        for prim in geom:
            assert primitive_within_bounds(prim, EXTENDED, tol=1e-6)

    def test_ray_directions_respect_doc_aspect(self):
        """Doc-space angles: at alpha=0 the normalized direction is purely
        +x (1, 0); at alpha=90 it is (0, 1) — pinned on w=1000 x h=500,
        where the two axes normalize differently (2:1)."""
        geom = PerspectiveGrid().build(BOUNDS_WIDE, {"show_horizon": False})
        rays = rays_before_dot(geom)
        horiz = [s for s in rays if s.p1[1] == s.p2[1]]  # alpha 0 only
        vert = [s for s in rays if abs(s.p1[0] - s.p2[0]) < 1e-12]  # alpha 90
        assert len(horiz) == 1
        for s in horiz:
            assert s.p1[1] == pytest.approx(VP[1], abs=1e-12)
            assert min(s.p1[0], s.p2[0]) == pytest.approx(-0.5, abs=1e-9)
            assert max(s.p1[0], s.p2[0]) == pytest.approx(1.5, abs=1e-9)
        assert len(vert) == 1
        for s in vert:
            assert s.p1[0] == pytest.approx(VP[0], abs=1e-12)
            assert min(s.p1[1], s.p2[1]) == pytest.approx(-0.5, abs=1e-9)
            assert max(s.p1[1], s.p2[1]) == pytest.approx(1.5, abs=1e-9)
        # Doc degrees survive the anisotropic mapping: with step 10 the
        # alpha=30 ray (k=3) has normalized slope (w/h) * tan(30).
        slanted = [
            s for s in rays
            if s.p1[0] != s.p2[0] and abs(s.p1[1] - s.p2[1]) > 1e-12
        ]
        slope30 = (BOUNDS_WIDE.w / BOUNDS_WIDE.h) * math.tan(math.radians(30.0))
        ray30 = [
            s for s in slanted
            if math.isclose(
                (s.p2[1] - s.p1[1]) / (s.p2[0] - s.p1[0]), slope30, abs_tol=1e-9
            )
        ]
        assert len(ray30) == 1  # alpha 30 (alpha 210 is the same line)


class TestHorizon:
    def test_horizon_at_vp_y(self):
        geom = PerspectiveGrid().build(BOUNDS, {})
        assert geom[-1] == Segment((-0.5, VP[1]), (1.5, VP[1]))

    def test_horizon_disabled(self):
        geom = PerspectiveGrid().build(BOUNDS, {"show_horizon": False})
        assert len(geom) == 19  # 18 rays + VP dot; no trailing horizon
        assert isinstance(geom[-1], Dot)
