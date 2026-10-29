"""Tests for the Phi (Golden Ratio) grid type."""

import math

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment
from composition_grid.core.grids import PhiGrid, Param, all_types, default_params, get

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)

# Pinned constants (the exact phi values).
PHI = (math.sqrt(5) - 1) / 2  # == 0.6180339887498949
LOW = 1.0 - PHI               # == 0.3819660112501051


class TestSchema:
    def test_identity(self):
        assert PhiGrid.id == "phi_grid"
        assert PhiGrid.name == "Golden Ratio (Phi)"
        assert PhiGrid().escapes_bounds is False
        assert get("phi_grid") is PhiGrid

    def test_params_schema(self):
        assert PhiGrid.params == (
            Param(name="show_horizontals", kind="bool", default=True),
            Param(name="show_verticals", kind="bool", default=True),
        )

    def test_default_params(self):
        assert default_params(PhiGrid) == {
            "show_horizontals": True,
            "show_verticals": True,
        }

    def test_registered_after_task7_prefix(self):
        """Prefix order is protected by an earlier test; phi lands at index 3."""
        assert all_types()[3] is PhiGrid


class TestPinnedConstants:
    def test_phi_value(self):
        """PHI = (sqrt(5)-1)/2 == 0.6180339887... (pinned ±1e-9)."""
        assert PHI == 0.6180339887498949

    def test_low_value(self):
        """1-PHI == 0.3819660112501051 (pinned ±1e-9)."""
        assert LOW == 0.3819660112501051


class TestBuild:
    def test_defaults_full_geometry_in_pinned_order(self):
        """Verticals (ascending x), horizontals (ascending y), then 4 power dots."""
        geom = PhiGrid().build(BOUNDS, {})
        assert geom == (
            Segment((LOW, 0.0), (LOW, 1.0)),
            Segment((PHI, 0.0), (PHI, 1.0)),
            Segment((0.0, LOW), (1.0, LOW)),
            Segment((0.0, PHI), (1.0, PHI)),
            Dot((LOW, LOW)),
            Dot((PHI, LOW)),
            Dot((LOW, PHI)),
            Dot((PHI, PHI)),
        )

    def test_positions_approx_pinned(self):
        geom = PhiGrid().build(BOUNDS, {})
        vertical_xs = sorted(
            s.p1[0] for s in geom if isinstance(s, Segment) and s.p1[0] == s.p2[0]
        )
        assert vertical_xs[0] == 0.3819660112501051
        assert vertical_xs[1] == 0.6180339887498949

    def test_show_verticals_false_keeps_horizontals_and_dots(self):
        geom = PhiGrid().build(BOUNDS, {"show_verticals": False})
        verticals = [s for s in geom if isinstance(s, Segment) and s.p1[0] == s.p2[0]]
        assert verticals == []
        horizontals = [s for s in geom if isinstance(s, Segment)]
        assert len(horizontals) == 2
        dots = [d for d in geom if isinstance(d, Dot)]
        assert len(dots) == 4  # dots are always part of build (PIN)

    def test_show_horizontals_false_keeps_verticals_and_dots(self):
        geom = PhiGrid().build(BOUNDS, {"show_horizontals": False})
        horizontals = [s for s in geom if isinstance(s, Segment) and s.p1[1] == s.p2[1]]
        assert horizontals == []
        verticals = [s for s in geom if isinstance(s, Segment)]
        assert len(verticals) == 2
        assert len([d for d in geom if isinstance(d, Dot)]) == 4  # PIN: always built

    def test_both_flags_false_leaves_only_power_dots(self):
        """Flags control only the segment groups; dots are unconditional."""
        geom = PhiGrid().build(
            BOUNDS, {"show_verticals": False, "show_horizontals": False}
        )
        assert geom == (
            Dot((LOW, LOW)),
            Dot((PHI, LOW)),
            Dot((LOW, PHI)),
            Dot((PHI, PHI)),
        )

    def test_flags_are_boolean_coerced(self):
        """Pinned truthiness rule via resolve_bool: bool(params.get(...))."""
        geom = PhiGrid().build(
            BOUNDS, {"show_verticals": 0, "show_horizontals": "yes"}
        )
        verticals = [s for s in geom if isinstance(s, Segment) and s.p1[0] == s.p2[0]]
        horizontals = [s for s in geom if isinstance(s, Segment) and s.p1[1] == s.p2[1]]
        assert verticals == []
        assert len(horizontals) == 2

    def test_geometry_independent_of_target_bounds(self):
        other = Bounds(x=10.0, y=-5.0, w=3.0, h=7.0)
        assert PhiGrid().build(other, {}) == PhiGrid().build(BOUNDS, {})
