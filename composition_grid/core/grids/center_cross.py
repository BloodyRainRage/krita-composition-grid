"""Center Cross grid type: toggling center axes with an optional center dot.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, GridGeometry, Segment
from composition_grid.core.grids.base import GridType, Param, resolve_bool
from composition_grid.core.grids.grid_registry import register


class CenterCross(GridType):
    """Vertical/horizontal center lines plus an optional center dot."""

    id = "center_cross"
    name = "Center Cross"
    params = (
        Param(name="show_vertical", kind="bool", default=True),
        Param(name="show_horizontal", kind="bool", default=True),
        Param(name="show_center_dot", kind="bool", default=False),
    )

    def build(
        self, bounds: Bounds, params: Mapping[str, Any]
    ) -> GridGeometry:
        geometry = []
        if resolve_bool(self, params, "show_vertical"):
            geometry.append(Segment((0.5, 0.0), (0.5, 1.0)))
        if resolve_bool(self, params, "show_horizontal"):
            geometry.append(Segment((0.0, 0.5), (1.0, 0.5)))
        if resolve_bool(self, params, "show_center_dot"):
            geometry.append(Dot((0.5, 0.5)))
        return tuple(geometry)


register(CenterCross)
