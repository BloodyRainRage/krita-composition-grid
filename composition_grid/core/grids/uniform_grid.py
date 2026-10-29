"""Uniform Grid type: an even N x M lattice with an optional margin inset.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.

Pinned: the margin is performed in normalized space — ``x0 = margin``,
inner width ``= 1 - 2*margin`` (same on y); the ``columns-1`` vertical lines
sit at ``x0 + i * inner_width / columns`` for ``i = 1 .. columns-1``, the
``rows-1`` horizontal lines analogously. Lines still span the full element
on their orthogonal axis (margin only shifts positions in v1). Degenerate
safety: ``columns``/``rows`` of 1 produce no lines of that axis.
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import GridGeometry, Segment
from composition_grid.core.grids.base import GridType, Param, resolve_int, resolve_number
from composition_grid.core.grids.grid_registry import register


class UniformGrid(GridType):
    """Even lattice of ``columns x rows`` cells with a normalized margin inset."""

    id = "uniform_grid"
    name = "Uniform Grid"
    params = (
        Param(name="columns", kind="number", default=3, minimum=1, maximum=24),
        Param(name="rows", kind="number", default=3, minimum=1, maximum=24),
        Param(name="margin", kind="number", default=0.0, minimum=0.0, maximum=0.45),
    )

    def build(
        self, bounds: Bounds, params: Mapping[str, Any]
    ) -> GridGeometry:  # noqa: ARG002 - bounds unused: geometry is normalized
        columns = resolve_int(self, params, "columns")
        rows = resolve_int(self, params, "rows")
        margin = resolve_number(self, params, "margin")

        inner_width = 1.0 - 2.0 * margin
        inner_height = 1.0 - 2.0 * margin
        geometry = []
        for i in range(1, columns):  # columns-1 verticals; none when columns == 1
            x = margin + i * inner_width / columns
            geometry.append(Segment((x, 0.0), (x, 1.0)))
        for j in range(1, rows):  # rows-1 horizontals; none when rows == 1
            y = margin + j * inner_height / rows
            geometry.append(Segment((0.0, y), (1.0, y)))
        return tuple(geometry)


register(UniformGrid)
