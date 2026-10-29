"""Symmetry Axes grid type: N vertical and M horizontal symmetry axes.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.

Pinned: with ``N`` vertical axes the element is divided into ``N+1`` equal
strips — lines at ``x = k/(N+1)`` for ``k = 1..N`` (symmetric about the
center 0.5 for any N); ``M`` horizontal axes analogously at ``y = k/(M+1)``.
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import GridGeometry, Segment
from composition_grid.core.grids.base import GridType, Param, resolve_int
from composition_grid.core.grids.grid_registry import register


class SymmetryAxes(GridType):
    """Vertical/horizontal mirror axes dividing the element into equal strips."""

    id = "symmetry_axes"
    name = "Symmetry Axes"
    params = (
        Param(name="vertical_axes", kind="number", default=1, minimum=1, maximum=4),
        Param(name="horizontal_axes", kind="number", default=1, minimum=1, maximum=4),
    )

    def build(
        self, bounds: Bounds, params: Mapping[str, Any]
    ) -> GridGeometry:  # noqa: ARG002 - bounds unused: geometry is normalized
        vertical_axes = resolve_int(self, params, "vertical_axes")
        horizontal_axes = resolve_int(self, params, "horizontal_axes")
        geometry = []
        for k in range(1, vertical_axes + 1):
            x = k / (vertical_axes + 1)
            geometry.append(Segment((x, 0.0), (x, 1.0)))
        for k in range(1, horizontal_axes + 1):
            y = k / (horizontal_axes + 1)
            geometry.append(Segment((0.0, y), (1.0, y)))
        return tuple(geometry)


register(SymmetryAxes)
