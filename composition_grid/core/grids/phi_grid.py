"""Phi (Golden Ratio) grid type: phi lines plus fixed power dots.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes) regardless of the target bounds.

Pinned: the four power dots are ALWAYS part of the build (they mark the phi
intersections); the ``show_horizontals``/``show_verticals`` flags control
only the corresponding segment groups.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, GridGeometry, Segment
from composition_grid.core.grids.base import GridType, Param, resolve_bool
from composition_grid.core.grids.grid_registry import register

# PHI == 0.6180339887498949; 1-PHI == 0.3819660112501051.
PHI = (math.sqrt(5) - 1) / 2
LOW = 1.0 - PHI


class PhiGrid(GridType):
    """Vertical/horizontal phi lines with the four golden-ratio power dots."""

    id = "phi_grid"
    name = "Golden Ratio (Phi)"
    params = (
        Param(name="show_horizontals", kind="bool", default=True),
        Param(name="show_verticals", kind="bool", default=True),
    )

    def build(
        self, bounds: Bounds, params: Mapping[str, Any]
    ) -> GridGeometry:  # noqa: ARG002 - bounds unused: geometry is normalized
        geometry = []
        if resolve_bool(self, params, "show_verticals"):
            geometry.append(Segment((LOW, 0.0), (LOW, 1.0)))
            geometry.append(Segment((PHI, 0.0), (PHI, 1.0)))
        if resolve_bool(self, params, "show_horizontals"):
            geometry.append(Segment((0.0, LOW), (1.0, LOW)))
            geometry.append(Segment((0.0, PHI), (1.0, PHI)))
        # Power dots at the phi/phi intersections; always built (pinned).
        geometry.extend(
            (
                Dot((LOW, LOW)),
                Dot((PHI, LOW)),
                Dot((LOW, PHI)),
                Dot((PHI, PHI)),
            )
        )
        return tuple(geometry)


register(PhiGrid)
