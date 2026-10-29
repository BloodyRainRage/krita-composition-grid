"""Rule of Thirds grid type: two verticals, two horizontals, power points.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, GridGeometry, Segment
from composition_grid.core.grids.base import GridType
from composition_grid.core.grids.grid_registry import register


class RuleOfThirds(GridType):
    """The classic thirds grid with its four power points."""

    id = "rule_of_thirds"
    name = "Rule of Thirds"
    params = ()

    def build(  # noqa: ARG002 - bounds unused: geometry is normalized
        self, bounds: Bounds, params: Mapping[str, Any]
    ) -> GridGeometry:
        third = 1.0 / 3.0
        two_thirds = 2.0 / 3.0
        return (
            Segment((third, 0.0), (third, 1.0)),
            Segment((two_thirds, 0.0), (two_thirds, 1.0)),
            Segment((0.0, third), (1.0, third)),
            Segment((0.0, two_thirds), (1.0, two_thirds)),
            Dot((third, third)),
            Dot((two_thirds, third)),
            Dot((third, two_thirds)),
            Dot((two_thirds, two_thirds)),
        )


register(RuleOfThirds)
