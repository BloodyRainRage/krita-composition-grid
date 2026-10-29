"""Tests for the Uniform Grid type and the base.py parameter resolvers."""

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment
from composition_grid.core.grids import (
    Param,
    UniformGrid,
    all_types,
    default_params,
    get,
)
from composition_grid.core.grids.base import GridType, resolve_bool, resolve_int, resolve_number

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)


def vertical_xs(geom):
    """X positions of full-height segments, ascending."""
    return sorted(s.p1[0] for s in geom if isinstance(s, Segment) and s.p1[0] == s.p2[0])


def horizontal_ys(geom):
    """Y positions of full-width segments, ascending."""
    return sorted(s.p1[1] for s in geom if isinstance(s, Segment) and s.p1[1] == s.p2[1])


class TestSchema:
    def test_identity(self):
        assert UniformGrid.id == "uniform_grid"
        assert UniformGrid.name == "Uniform Grid"
        assert UniformGrid().escapes_bounds is False
        assert get("uniform_grid") is UniformGrid

    def test_params_schema(self):
        assert UniformGrid.params == (
            Param(name="columns", kind="number", default=3, minimum=1, maximum=24),
            Param(name="rows", kind="number", default=3, minimum=1, maximum=24),
            Param(name="margin", kind="number", default=0.0, minimum=0.0, maximum=0.45),
        )

    def test_default_params(self):
        assert default_params(UniformGrid) == {
            "columns": 3,
            "rows": 3,
            "margin": 0.0,
        }

    def test_registered_fifth(self):
        assert all_types()[5] is UniformGrid


class TestBuildDefaults:
    def test_default_3x3_with_zero_margin(self):
        geom = UniformGrid().build(BOUNDS, {})
        # columns-1 = 2 verticals, rows-1 = 2 horizontals
        assert len(geom) == 4
        assert vertical_xs(geom) == pytest.approx([1.0 / 3.0, 2.0 / 3.0])
        assert horizontal_ys(geom) == pytest.approx([1.0 / 3.0, 2.0 / 3.0])
        assert not any(isinstance(p, Dot) for p in geom)

    def test_build_order_verticals_then_horizontals(self):
        geom = UniformGrid().build(BOUNDS, {})
        kinds = [type(p) for p in geom]
        assert kinds == [Segment, Segment, Segment, Segment]
        # ascending k within each axis: first two verticals, then horizontals
        assert geom[0].p1[0] < geom[1].p1[0]
        assert geom[2].p1[1] < geom[3].p1[1]

    def test_columns_2_gives_single_center_vertical(self):
        """Pinned: columns=2 -> exactly one vertical at x=0.5."""
        geom = UniformGrid().build(BOUNDS, {"columns": 2})
        assert vertical_xs(geom) == pytest.approx([0.5])
        assert horizontal_ys(geom) == pytest.approx([1.0 / 3.0, 2.0 / 3.0])

    def test_no_dots_ever(self):
        geom = UniformGrid().build(BOUNDS, {"columns": 2, "rows": 2})
        assert not any(isinstance(p, Dot) for p in geom)


class TestBuildMargin:
    def test_margin_0_1_positions(self):
        """Pinned: margin=0.1 -> margins performed in normalized space."""
        geom = UniformGrid().build(BOUNDS, {"columns": 3, "margin": 0.1})
        # inner width = 1 - 2*0.1 = 0.8; x = 0.1 + i*0.8/3 for i = 1, 2
        assert vertical_xs(geom) == pytest.approx([0.1 + 0.8 / 3.0, 0.1 + 2 * 0.8 / 3.0])
        # same on y
        assert horizontal_ys(geom) == pytest.approx([0.1 + 0.8 / 3.0, 0.1 + 2 * 0.8 / 3.0])
        assert pytest.approx(vertical_xs(geom)[0]) == 0.36666666666666664

    def test_uniform_2x2_with_margin(self):
        """Pinned: 2x2 with margin=0.1 -> one vertical + one horizontal at 0.5."""
        geom = UniformGrid().build(BOUNDS, {"columns": 2, "rows": 2, "margin": 0.1})
        assert vertical_xs(geom) == pytest.approx([0.5])
        assert horizontal_ys(geom) == pytest.approx([0.5])
        assert len(geom) == 2

    def test_full_span_endpoints(self):
        """Lines span the full element regardless of margin (v1 pin)."""
        geom = UniformGrid().build(BOUNDS, {"columns": 3, "rows": 3, "margin": 0.1})
        for s in geom:
            if s.p1[0] == s.p2[0]:
                assert (s.p1[1], s.p2[1]) == (0.0, 1.0)
            else:
                assert (s.p1[0], s.p2[0]) == (0.0, 1.0)

    def test_margin_is_clamped_to_schema_bounds(self):
        assert vertical_xs(
            UniformGrid().build(BOUNDS, {"columns": 3, "margin": -5.0})
        ) == pytest.approx(vertical_xs(
            UniformGrid().build(BOUNDS, {"columns": 3, "margin": 0.0})
        ))
        assert vertical_xs(
            UniformGrid().build(BOUNDS, {"columns": 3, "margin": 99.0})
        ) == pytest.approx(vertical_xs(
            UniformGrid().build(BOUNDS, {"columns": 3, "margin": 0.45})
        ))


class TestBuildIntCoercionAndClamps:
    @pytest.mark.parametrize("raw,expected", [
        (2.7, [1.0 / 3.0, 2.0 / 3.0]),                       # round(2.7) = 3
        (100, [i / 24.0 for i in range(1, 24)]),             # clamped to max 24
        (0, []),                                             # clamped to min 1 -> 0 lines
    ])
    def test_columns_coercion_and_clamp(self, raw, expected):
        geom = UniformGrid().build(BOUNDS, {"columns": raw})
        assert vertical_xs(geom) == pytest.approx(expected)

    def test_max_columns_24(self):
        geom = UniformGrid().build(BOUNDS, {"columns": 24, "rows": 1})
        assert len(vertical_xs(geom)) == 23

    def test_max_rows_24(self):
        geom = UniformGrid().build(BOUNDS, {"columns": 1, "rows": 24})
        assert len(horizontal_ys(geom)) == 23

    def test_degenerate_1x1_yields_empty(self):
        """Pinned safety: rows/columns of 1 produce no lines of that axis."""
        geom = UniformGrid().build(BOUNDS, {"columns": 1, "rows": 1})
        assert geom == ()


class TestGeometryIndependence:
    def test_geometry_independent_of_target_bounds(self):
        other = Bounds(x=10.0, y=-5.0, w=3.0, h=7.0)
        assert UniformGrid().build(other, {}) == UniformGrid().build(BOUNDS, {})


class TestBaseParameterResolvers:
    """Shared resolvers in base.py: truthiness + int coercion/clamp."""

    class ResolverGrid(GridType):
        id = "resolver_dummy"
        params = (
            Param(name="flag", kind="bool", default=True),
            Param(name="count", kind="number", default=3, minimum=1, maximum=24),
            Param(name="fraction", kind="number", default=0.0, minimum=0.0, maximum=0.45),
        )

        def build(self, bounds, params):  # noqa: ARG002 - signature fixed by base
            return ()

    def setup_method(self):
        self.grid = self.ResolverGrid()

    def test_resolve_bool_truthiness_and_default_fallback(self):
        assert resolve_bool(self.grid, {}, "flag") is True  # schema default
        assert resolve_bool(self.grid, {"flag": 0}, "flag") is False
        assert resolve_bool(self.grid, {"flag": ""}, "flag") is False
        assert resolve_bool(self.grid, {"flag": "yes"}, "flag") is True
        assert resolve_bool(self.grid, {"flag": True}, "flag") is True

    def test_resolve_int_coerces_rounds_and_clamps(self):
        assert resolve_int(self.grid, {}, "count") == 3
        assert resolve_int(self.grid, {"count": "2.7"}, "count") == 3
        assert resolve_int(self.grid, {"count": 100}, "count") == 24
        assert resolve_int(self.grid, {"count": 0}, "count") == 1

    def test_resolve_number_coerces_and_clamps(self):
        assert resolve_number(self.grid, {}, "fraction") == 0.0
        assert resolve_number(self.grid, {"fraction": "0.2"}, "fraction") == 0.2
        assert resolve_number(self.grid, {"fraction": 99.0}, "fraction") == 0.45
        assert resolve_number(self.grid, {"fraction": -1.0}, "fraction") == 0.0
