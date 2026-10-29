"""Tests for the Rule of Thirds grid type."""

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment
from composition_grid.core.grids import (
    CenterCross,
    QuadrantGrid,
    RuleOfThirds,
    all_types,
    get,
)

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)

ONE_THIRD = 1.0 / 3.0
TWO_THIRDS = 2.0 / 3.0


def verticals(geom):
    """Segments whose p1.x == p2.x (vertical lines)."""
    return [s for s in geom if isinstance(s, Segment) and s.p1[0] == s.p2[0]]


def horizontals(geom):
    """Segments whose p1.y == p2.y (horizontal lines)."""
    return [s for s in geom if isinstance(s, Segment) and s.p1[1] == s.p2[1]]


def dots(geom):
    """All Dot primitives of ``geom``."""
    return [d for d in geom if isinstance(d, Dot)]


class TestSchema:
    def test_identity_and_schema(self):
        assert RuleOfThirds.id == "rule_of_thirds"
        assert RuleOfThirds.name == "Rule of Thirds"
        assert RuleOfThirds.params == ()
        assert RuleOfThirds().escapes_bounds is False

    def test_registered_first_in_registry(self):
        """RuleOfThirds is the first registered type."""
        assert get("rule_of_thirds") is RuleOfThirds
        assert all_types()[:3] == (RuleOfThirds, CenterCross, QuadrantGrid)


class TestBuild:
    def test_thirds_lines_at_exact_thirds(self):
        geom = RuleOfThirds().build(BOUNDS, {})

        vlines = verticals(geom)
        assert len(vlines) == 2
        assert sorted(s.p1[0] for s in vlines) == pytest.approx(
            [ONE_THIRD, TWO_THIRDS]
        )
        for s in vlines:
            assert s.p1[1] == 0.0
            assert s.p2 == (s.p1[0], 1.0)

        hlines = horizontals(geom)
        assert len(hlines) == 2
        assert sorted(s.p1[1] for s in hlines) == pytest.approx(
            [ONE_THIRD, TWO_THIRDS]
        )
        for s in hlines:
            assert s.p1[0] == 0.0
            assert s.p2 == (1.0, s.p1[1])

    def test_geometry_shape_and_order_lines_then_dots(self):
        geom = RuleOfThirds().build(BOUNDS, {})
        assert [type(p) for p in geom] == [Segment] * 4 + [Dot] * 4

    def test_thirds_power_points(self):
        geom = RuleOfThirds().build(BOUNDS, {})
        positions = [d.position for d in dots(geom)]
        expected = [
            (ONE_THIRD, ONE_THIRD),
            (TWO_THIRDS, ONE_THIRD),
            (ONE_THIRD, TWO_THIRDS),
            (TWO_THIRDS, TWO_THIRDS),
        ]
        assert len(positions) == 4
        for got, want in zip(positions, expected):
            assert got == pytest.approx(want)

    def test_geometry_is_normalized_element_local(self):
        """Same 0..1 geometry regardless of the target bounds values."""
        other = Bounds(x=-50.0, y=7.5, w=300.0, h=90.0)
        assert RuleOfThirds().build(other, {}) == RuleOfThirds().build(BOUNDS, {})

    def test_params_defaults_resolvable_when_missing(self):
        """build({}) must yield the canonical geometry (no params to resolve)."""
        geom = RuleOfThirds().build(BOUNDS, {})
        assert len(verticals(geom)) == 2
        assert len(horizontals(geom)) == 2
        assert len(dots(geom)) == 4
