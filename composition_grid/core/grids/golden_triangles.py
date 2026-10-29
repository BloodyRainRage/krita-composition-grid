"""Golden Triangles grid type: diagonal plus the two corner perpendiculars.

Pure-math core: no Qt, no krita — only stdlib. Geometry is normalized and
element-local (0..1 on both axes); the construction is measured in DOC
space (``bounds.w`` x ``bounds.h`` pixels) because perpendicularity is a
real-space notion, then normalized back — so the geometry depends on the
element aspect, unlike a plain phi cut.

Pinned semantics: orientation ``XY`` (``tl_br`` = main diagonal, ``tr_bl`` =
anti-diagonal) draws the diagonal from corner ``X`` to corner ``Y`` plus, from
each of the two corners NOT on that diagonal, the segment PERPENDICULAR to
the diagonal in doc space, ending at its foot on the diagonal; a ``Dot`` sits
at each foot.  For a square frame both feet coincide at the center — that is
the classic construction, not a degeneracy.

Output order: diagonal, then the two perpendiculars (their start corners in
canonical order ``tl`` < ``tr`` < ``br`` < ``bl``), then the two dots in the
same order.

Degenerate bounds (``w <= 0`` or ``h <= 0``) build to the empty geometry.
Stored settings may still name the retired orientations ``br_tl``/``bl_tr``:
they alias to ``tl_br``/``tr_bl`` (both pairs draw the same diagonal and,
since the feet are symmetric about the center, the same geometry).
"""

from __future__ import annotations

from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import Dot, GridGeometry, Segment
from composition_grid.core.grids.base import GridType, Param, resolve_choice
from composition_grid.core.grids.grid_registry import register

_CORNER_POS = {
    "tl": (0.0, 0.0),
    "tr": (1.0, 0.0),
    "br": (1.0, 1.0),
    "bl": (0.0, 1.0),
}
_FACE_ORDER = ("tl", "tr", "br", "bl")

# Retired choice values: same diagonal as their replacement, kept as aliases
# so old settings keep building instead of falling back to the default.
_LEGACY_DIAGONALS = {"br_tl": "tl_br", "bl_tr": "tr_bl"}


def _foot_on_diagonal(
    corner: tuple[float, float], start: tuple[float, float], diag: tuple[float, float]
) -> tuple[float, float]:
    """Doc-space foot of the perpendicular from ``corner`` onto ``start + t*diag``."""
    length_sq = diag[0] ** 2 + diag[1] ** 2
    t = (
        (corner[0] - start[0]) * diag[0] + (corner[1] - start[1]) * diag[1]
    ) / length_sq
    return (start[0] + t * diag[0], start[1] + t * diag[1])


class GoldenTriangles(GridType):
    """Golden triangle construction: diagonal, two corner perpendiculars, feet."""

    id = "golden_triangles"
    name = "Golden Triangles"
    params = (
        Param(
            name="diagonal",
            kind="choice",
            default="tl_br",
            choices=("tl_br", "tr_bl"),
        ),
    )

    def build(self, bounds: Bounds, params: Mapping[str, Any]) -> GridGeometry:
        w, h = bounds.w, bounds.h
        if w <= 0.0 or h <= 0.0:
            return ()

        stored = params.get("diagonal")
        diagonal = resolve_choice(
            self, {**params, "diagonal": _LEGACY_DIAGONALS.get(stored, stored)}, "diagonal"
        )
        start_name, end_name = diagonal.split("_")

        a = _CORNER_POS[start_name]
        b = _CORNER_POS[end_name]
        start_doc = (a[0] * w, a[1] * h)
        diag_doc = ((b[0] - a[0]) * w, (b[1] - a[1]) * h)

        geometry: list[Segment | Dot] = [
            Segment(a, b),
        ]
        feet: list[tuple[float, float]] = []
        for name in _FACE_ORDER:
            if name in (start_name, end_name):
                continue
            corner_doc = (_CORNER_POS[name][0] * w, _CORNER_POS[name][1] * h)
            foot_doc = _foot_on_diagonal(corner_doc, start_doc, diag_doc)
            foot = (foot_doc[0] / w, foot_doc[1] / h)
            feet.append(foot)
            geometry.append(Segment(_CORNER_POS[name], foot))

        return tuple(geometry) + tuple(Dot(foot) for foot in feet)


register(GoldenTriangles)
