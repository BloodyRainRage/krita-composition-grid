"""Rule of Fifths grid type: four verticals and four horizontals at k/5.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.

Pinned v1 non-goal: the ``show_labels`` parameter is schema-only — v1 does
NOT render any axis labels, and :meth:`build` ignores the parameter
entirely (kept in the schema for a future label-rendering level).
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import GridGeometry, Segment
from composition_grid.core.grids.base import GridType, Param
from composition_grid.core.grids.grid_registry import register

FIFTHS = (1, 2, 3, 4)


class RuleOfFifths(GridType):
    """Vertical/horizontal lines dividing the element into five equal strips."""

    id = "rule_of_fifths"
    name = "Rule of Fifths"
    params = (Param(name="show_labels", kind="bool", default=True),)

    def build(
        self, bounds: Bounds, params: Mapping[str, Any]
    ) -> GridGeometry:  # noqa: ARG002 - bounds and show_labels unused (see module note)
        return tuple(
            [Segment((k / 5.0, 0.0), (k / 5.0, 1.0)) for k in FIFTHS]
            + [Segment((0.0, k / 5.0), (1.0, k / 5.0)) for k in FIFTHS]
        )


register(RuleOfFifths)
