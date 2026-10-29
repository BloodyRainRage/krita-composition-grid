"""Tests for the Rule of Fifths grid type."""

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment
from composition_grid.core.grids import RuleOfFifths, Param, all_types, get

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)

EXPECTED = (
    # verticals first, ascending k = 1..4
    Segment((1.0 / 5.0, 0.0), (1.0 / 5.0, 1.0)),
    Segment((2.0 / 5.0, 0.0), (2.0 / 5.0, 1.0)),
    Segment((3.0 / 5.0, 0.0), (3.0 / 5.0, 1.0)),
    Segment((4.0 / 5.0, 0.0), (4.0 / 5.0, 1.0)),
    # horizontals, ascending k = 1..4
    Segment((0.0, 1.0 / 5.0), (1.0, 1.0 / 5.0)),
    Segment((0.0, 2.0 / 5.0), (1.0, 2.0 / 5.0)),
    Segment((0.0, 3.0 / 5.0), (1.0, 3.0 / 5.0)),
    Segment((0.0, 4.0 / 5.0), (1.0, 4.0 / 5.0)),
)


class TestSchema:
    def test_identity(self):
        assert RuleOfFifths.id == "rule_of_fifths"
        assert RuleOfFifths.name == "Rule of Fifths"
        assert RuleOfFifths().escapes_bounds is False
        assert get("rule_of_fifths") is RuleOfFifths

    def test_params_schema(self):
        assert RuleOfFifths.params == (
            Param(name="show_labels", kind="bool", default=True),
        )

    def test_registered_fourth(self):
        assert all_types()[4] is RuleOfFifths


class TestBuild:
    def test_defaults_exactly_8_segments_in_pinned_order(self):
        geom = RuleOfFifths().build(BOUNDS, {})
        assert geom == EXPECTED
        assert len(geom) == 8

    def test_no_dots_and_no_labels_rendered_in_v1(self):
        """Labels are NOT rendered in v1 (documented non-goal)."""
        geom = RuleOfFifths().build(BOUNDS, {})
        assert not any(isinstance(p, Dot) for p in geom)

    def test_show_labels_false_is_ignored_by_build(self):
        """Schema-only parameter: build output identical with/without it."""
        assert RuleOfFifths().build(BOUNDS, {"show_labels": False}) == EXPECTED
        assert RuleOfFifths().build(BOUNDS, {}) == EXPECTED

    def test_fifths_step_positions(self):
        """Vertical/horizontal lines at k/5 for k = 1..4."""
        geom = RuleOfFifths().build(BOUNDS, {})
        vertical_xs = sorted(s.p1[0] for s in geom if s.p1[0] == s.p2[0])
        horizontal_ys = sorted(s.p1[1] for s in geom if s.p1[1] == s.p2[1])
        assert vertical_xs == [0.2, 0.4, 0.6, 0.8]
        assert horizontal_ys == [0.2, 0.4, 0.6, 0.8]

    def test_geometry_independent_of_target_bounds(self):
        other = Bounds(x=10.0, y=-5.0, w=3.0, h=7.0)
        assert RuleOfFifths().build(other, {}) == EXPECTED
