"""Registry-wide bounds contract test.

The contract (DRY): iterate the REAL registry via
``all_types()`` — no grid-type module is imported by name here.  For
every type with ``escapes_bounds`` False (11 of the 12 types) building
with ``default_params`` inside any bounds must produce ONLY primitives
inside those bounds (tolerance 1e-6).  The one escaping type
(perspective_grid) must stay confined to the extended rect
``Bounds(-0.5, -0.5, 2.0, 2.0)`` instead.
"""

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import primitive_within_bounds
from composition_grid.core.grids import (
    DynamicSymmetry,
    PerspectiveGrid,
    all_types,
    default_params,
    get,
)

BOUNDS = Bounds(x=0.0, y=0.0, w=1000.0, h=600.0)
TOL = 1e-6
EXTENDED = Bounds(x=-0.5, y=-0.5, w=2.0, h=2.0)


def test_final_registry_count_and_tail_order():
    """12 grid types; the library ends with
    dynamic_symmetry then perspective_grid."""
    types = all_types()
    assert len(types) == 12
    assert types[-2:] == (DynamicSymmetry, PerspectiveGrid)


def test_contained_types_never_escape_bounds():
    contained = [t for t in all_types() if not t.escapes_bounds]
    assert len(contained) == 11  # every type except perspective_grid
    for grid_type in contained:
        geom = grid_type().build(BOUNDS, default_params(grid_type))
        for prim in geom:
            assert primitive_within_bounds(prim, BOUNDS, tol=TOL), (
                f"{grid_type.id}: {prim!r} escapes {BOUNDS!r}"
            )


def test_perspective_stays_within_extended_rect():
    perspective = get("perspective_grid")
    assert perspective.escapes_bounds is True
    geom = perspective().build(BOUNDS, default_params(perspective))
    assert geom, "perspective grid must produce geometry"
    for prim in geom:
        assert primitive_within_bounds(prim, EXTENDED, tol=TOL), (
            f"perspective_grid: {prim!r} escapes the extended rect {EXTENDED!r}"
        )
