"""Grid types subpackage: interface (base), registry, concrete grid types.

Importing this package pulls in the pure-math stdlib core (no Qt, no
krita) and, as a side effect of the module imports below, registers all
concrete grid types. RuleOfThirds is registered FIRST (registration order
matters: the default grid resolves to ``all_types()[0]``, followed by the
remaining types in module-import order).
"""

from __future__ import annotations

from composition_grid.core.grids.base import GridType, Param
from composition_grid.core.grids.grid_registry import (
    all_types,
    default_params,
    get,
    register,
)

# Concrete grid types: module import performs the registration side effect.
from composition_grid.core.grids.rule_of_thirds import RuleOfThirds
from composition_grid.core.grids.center_cross import CenterCross
from composition_grid.core.grids.quadrant_grid import QuadrantGrid
from composition_grid.core.grids.phi_grid import PhiGrid
from composition_grid.core.grids.rule_of_fifths import RuleOfFifths
from composition_grid.core.grids.uniform_grid import UniformGrid
from composition_grid.core.grids.symmetry_axes import SymmetryAxes
from composition_grid.core.grids.golden_spiral import GoldenSpiral
from composition_grid.core.grids.golden_triangles import GoldenTriangles
from composition_grid.core.grids.diagonal_method import DiagonalMethod
from composition_grid.core.grids.dynamic_symmetry import DynamicSymmetry
from composition_grid.core.grids.perspective_grid import PerspectiveGrid

__all__ = [
    "GridType",
    "Param",
    "all_types",
    "default_params",
    "get",
    "register",
    "RuleOfThirds",
    "CenterCross",
    "QuadrantGrid",
    "PhiGrid",
    "RuleOfFifths",
    "UniformGrid",
    "SymmetryAxes",
    "GoldenSpiral",
    "GoldenTriangles",
    "DiagonalMethod",
    "DynamicSymmetry",
    "PerspectiveGrid",
]
