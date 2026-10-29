"""Tests for the Quadrant Grid grid type."""

import pytest

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, Segment
from composition_grid.core.grids import Param, QuadrantGrid, get

BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)

# Pinned marker order: nw, ne, sw, se.
QUADRANT_CENTERS = [
    (0.25, 0.25),  # nw
    (0.75, 0.25),  # ne
    (0.25, 0.75),  # sw
    (0.75, 0.75),  # se
]


def cross_segments(geom):
    """All segments of ``geom`` (the always-present central cross)."""
    return [s for s in geom if isinstance(s, Segment)]


def dots(geom):
    """All Dot primitives of ``geom``."""
    return [d for d in geom if isinstance(d, Dot)]


class TestSchema:
    def test_identity(self):
        assert QuadrantGrid.id == "quadrant_grid"
        assert QuadrantGrid.name == "Quadrant Grid"
        assert QuadrantGrid().escapes_bounds is False
        assert get("quadrant_grid") is QuadrantGrid

    def test_params_schema(self):
        assert QuadrantGrid.params == (
            Param(name="quadrant_markers", kind="bool", default=False),
        )


class TestBuild:
    def test_defaults_cross_without_markers(self):
        geom = QuadrantGrid().build(BOUNDS, {})
        assert geom == (
            Segment((0.5, 0.0), (0.5, 1.0)),
            Segment((0.0, 0.5), (1.0, 0.5)),
        )
        assert dots(geom) == []

    def test_quadrant_markers_order(self):
        geom = QuadrantGrid().build(BOUNDS, {"quadrant_markers": True})
        positions = [d.position for d in dots(geom)]
        assert len(positions) == 4
        for got, want in zip(positions, QUADRANT_CENTERS):
            assert got == pytest.approx(want)

    def test_markers_after_cross_lines(self):
        geom = QuadrantGrid().build(BOUNDS, {"quadrant_markers": True})
        assert [type(p) for p in geom] == [Segment, Segment, Dot, Dot, Dot, Dot]

    def test_markers_truthiness(self):
        geom = QuadrantGrid().build(BOUNDS, {"quadrant_markers": 0})
        assert dots(geom) == []

    def test_params_defaults_resolvable_when_missing(self):
        """build({}) falls back to default quadrant_markers=False: cross only."""
        geom = QuadrantGrid().build(BOUNDS, {})
        assert len(cross_segments(geom)) == 2
        assert dots(geom) == []
