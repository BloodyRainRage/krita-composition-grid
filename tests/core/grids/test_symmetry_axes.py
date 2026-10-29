"""Tests for the Symmetry Axes grid type and its registration order."""

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment
from composition_grid.core.grids import Param, SymmetryAxes, all_types, default_params, get

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)


def vertical_xs(geom):
    """X positions of full-height segments, ascending."""
    return sorted(s.p1[0] for s in geom if isinstance(s, Segment) and s.p1[0] == s.p2[0])


def horizontal_ys(geom):
    """Y positions of full-width segments, ascending."""
    return sorted(s.p1[1] for s in geom if isinstance(s, Segment) and s.p1[1] == s.p2[1])


class TestRegistrationOrder:
    def test_seven_types_with_symmetry_axes_last(self):
        """12 types; symmetry_axes is the last of the first seven."""
        assert len(all_types()) == 12
        assert all_types()[6] is SymmetryAxes

    def test_full_id_order(self):
        assert [t.id for t in all_types()] == [
            "rule_of_thirds",
            "center_cross",
            "quadrant_grid",
            "phi_grid",
            "rule_of_fifths",
            "uniform_grid",
            "symmetry_axes",
            "golden_spiral",
            "golden_triangles",
            "diagonal_method",
            "dynamic_symmetry",
            "perspective_grid",
        ]


class TestSchema:
    def test_identity(self):
        assert SymmetryAxes.id == "symmetry_axes"
        assert SymmetryAxes.name == "Symmetry Axes"
        assert SymmetryAxes().escapes_bounds is False
        assert get("symmetry_axes") is SymmetryAxes

    def test_params_schema(self):
        assert SymmetryAxes.params == (
            Param(name="vertical_axes", kind="number", default=1, minimum=1, maximum=4),
            Param(name="horizontal_axes", kind="number", default=1, minimum=1, maximum=4),
        )

    def test_default_params(self):
        assert default_params(SymmetryAxes) == {
            "vertical_axes": 1,
            "horizontal_axes": 1,
        }


class TestBuild:
    def test_default_single_cross(self):
        geom = SymmetryAxes().build(BOUNDS, {})
        assert vertical_xs(geom) == pytest.approx([0.5])
        assert horizontal_ys(geom) == pytest.approx([0.5])
        assert len(geom) == 2

    def test_two_axes_symmetric_about_center(self):
        """Pinned: N=2 -> x = 1/3 and 2/3 (symmetric about x=0.5)."""
        geom = SymmetryAxes().build(BOUNDS, {"vertical_axes": 2, "horizontal_axes": 2})
        xs = vertical_xs(geom)
        assert xs == pytest.approx([1.0 / 3.0, 2.0 / 3.0])
        assert horizontal_ys(geom) == pytest.approx([1.0 / 3.0, 2.0 / 3.0])
        assert pytest.approx(xs[0] + xs[1]) == 1.0

    def test_four_axes_at_fifths(self):
        geom = SymmetryAxes().build(BOUNDS, {"vertical_axes": 4, "horizontal_axes": 0})
        ys = horizontal_ys(geom)
        assert vertical_xs(geom) == pytest.approx([0.2, 0.4, 0.6, 0.8])
        assert ys == pytest.approx([0.5])  # horizontal_axes=0 clamps to 1 -> x=0.5

    def test_build_order_verticals_then_horizontals_ascending(self):
        geom = SymmetryAxes().build(BOUNDS, {"vertical_axes": 3, "horizontal_axes": 2})
        assert [type(p) for p in geom] == [Segment] * 5
        xs = [s.p1[0] for s in geom[:3]]
        ys = [s.p1[1] for s in geom[3:]]
        assert xs == pytest.approx([0.25, 0.5, 0.75])
        assert ys == pytest.approx([1.0 / 3.0, 2.0 / 3.0])

    def test_no_dots_ever(self):
        geom = SymmetryAxes().build(BOUNDS, {"vertical_axes": 2, "horizontal_axes": 2})
        assert not any(isinstance(p, Dot) for p in geom)

    def test_int_coercion_and_clamp(self):
        geom = SymmetryAxes().build(BOUNDS, {"vertical_axes": 2.4})
        assert len(vertical_xs(geom)) == 2  # round(2.4) = 2

        geom = SymmetryAxes().build(BOUNDS, {"vertical_axes": 99})
        assert len(vertical_xs(geom)) == 4  # clamped to max 4

        geom = SymmetryAxes().build(BOUNDS, {"vertical_axes": 0})
        assert vertical_xs(geom) == pytest.approx([0.5])  # clamped to min 1

    def test_geometry_independent_of_target_bounds(self):
        other = Bounds(x=10.0, y=-5.0, w=3.0, h=7.0)
        assert SymmetryAxes().build(other, {}) == SymmetryAxes().build(BOUNDS, {})
