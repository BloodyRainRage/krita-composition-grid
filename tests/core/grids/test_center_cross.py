"""Tests for the Center Cross grid type."""

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment
from composition_grid.core.grids import CenterCross, Param, get

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)


def vertical_count(geom):
    """Segments lying on the vertical center line (p1.y == p2.y == 0.5 case: x)."""
    return sum(
        1 for s in geom if isinstance(s, Segment) and s.p1[0] == s.p2[0] == 0.5
    )


def horizontal_count(geom):
    """Segments on the horizontal center line: p1.y == p2.y == 0.5."""
    return sum(
        1 for s in geom if isinstance(s, Segment) and s.p1[1] == s.p2[1] == 0.5
    )


def center_dots(geom):
    """Dots at the exact center (0.5, 0.5)."""
    return [d for d in geom if isinstance(d, Dot) and d.position == (0.5, 0.5)]


class TestSchema:
    def test_identity(self):
        assert CenterCross.id == "center_cross"
        assert CenterCross.name == "Center Cross"
        assert CenterCross().escapes_bounds is False
        assert get("center_cross") is CenterCross

    def test_params_schema(self):
        assert CenterCross.params == (
            Param(name="show_vertical", kind="bool", default=True),
            Param(name="show_horizontal", kind="bool", default=True),
            Param(name="show_center_dot", kind="bool", default=False),
        )


class TestBuild:
    def test_defaults_full_cross_no_dot(self):
        geom = CenterCross().build(BOUNDS, {})
        assert vertical_count(geom) == 1
        assert horizontal_count(geom) == 1
        assert center_dots(geom) == []
        assert len(geom) == 2

    def test_segment_endpoints(self):
        geom = CenterCross().build(BOUNDS, {})
        assert Segment((0.5, 0.0), (0.5, 1.0)) in geom
        assert Segment((0.0, 0.5), (1.0, 0.5)) in geom

    def test_center_cross_respects_flags(self):
        geom = CenterCross().build(BOUNDS, {"show_horizontal": False})
        assert horizontal_count(geom) == 0
        assert vertical_count(geom) == 1
        assert len(geom) == 1

    def test_show_vertical_false(self):
        geom = CenterCross().build(BOUNDS, {"show_vertical": False})
        assert vertical_count(geom) == 0
        assert horizontal_count(geom) == 1
        assert len(geom) == 1

    def test_both_false_yields_empty(self):
        geom = CenterCross().build(
            BOUNDS, {"show_vertical": False, "show_horizontal": False}
        )
        assert geom == ()

    def test_show_center_dot_true(self):
        geom = CenterCross().build(BOUNDS, {"show_center_dot": True})
        assert center_dots(geom) == [Dot((0.5, 0.5))]
        assert len(geom) == 3

    def test_geometry_order_vertical_horizontal_dot(self):
        geom = CenterCross().build(BOUNDS, {"show_center_dot": True})
        assert [type(p) for p in geom] == [Segment, Segment, Dot]

    def test_flags_are_boolean_coerced(self):
        """Pinned truthiness rule: bool(params.get(...))."""
        geom = CenterCross().build(
            BOUNDS, {"show_vertical": 0, "show_horizontal": "yes"}
        )
        assert vertical_count(geom) == 0
        assert horizontal_count(geom) == 1

    def test_params_defaults_resolvable_when_missing(self):
        """build({}) must fall back to schema defaults (True/True/False)."""
        geom = CenterCross().build(BOUNDS, {})
        assert vertical_count(geom) == 1
        assert horizontal_count(geom) == 1
        assert center_dots(geom) == []
