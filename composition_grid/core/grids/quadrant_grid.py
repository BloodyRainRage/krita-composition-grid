"""Quadrant Grid grid type: central cross with optional quadrant markers.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, GridGeometry, Segment
from composition_grid.core.grids.base import GridType, Param, resolve_bool
from composition_grid.core.grids.grid_registry import register


class QuadrantGrid(GridType):
    """Central cross plus optional dots at the quadrant centers (nw, ne, sw, se)."""

    id = "quadrant_grid"
    name = "Quadrant Grid"
    params = (Param(name="quadrant_markers", kind="bool", default=False),)

    def build(
        self, bounds: Bounds, params: Mapping[str, Any]
    ) -> GridGeometry:
        geometry = [
            Segment((0.5, 0.0), (0.5, 1.0)),
            Segment((0.0, 0.5), (1.0, 0.5)),
        ]
        if resolve_bool(self, params, "quadrant_markers"):
            geometry.extend(
                (
                    Dot((0.25, 0.25)),  # nw
                    Dot((0.75, 0.25)),  # ne
                    Dot((0.25, 0.75)),  # sw
                    Dot((0.75, 0.75)),  # se
                )
            )
        return tuple(geometry)


register(QuadrantGrid)
