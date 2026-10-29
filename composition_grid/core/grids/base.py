"""Grid type plug-in point: the Param schema and the abstract GridType.

Pure-math core: no Qt, no krita — only stdlib. Concrete grid types (later
tasks) subclass :class:`GridType`, declare their parameter schema in
``params``, and implement :meth:`GridType.build` to turn a :class:`Bounds`
plus resolved parameters into a :class:`GridGeometry`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import GridGeometry


@dataclass(frozen=True)
class Param:
    """Declaration of one user-facing grid parameter.

    ``kind`` is one of ``"number"``, ``"bool"``, ``"choice"``:
    - ``"number"``: a float; ``minimum``/``maximum`` are optional clamps.
    - ``"bool"``: a toggle; ``default`` is True/False.
    - ``"choice"``: one of ``choices``; ``default`` must be a member.
    """

    name: str
    kind: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None


class GridType(ABC):
    """Abstract base class for the plugin's grid types.

    Subclasses are declarative: set the class attributes ``id`` (unique,
    stable string key used by the registry), ``name`` (display name),
    ``params`` (tuple of :class:`Param`), and ``escapes_bounds`` (True for
    grids whose geometry may legitimately lie outside the target bounds).
    """

    id: str
    name: str
    params: tuple[Param, ...] = ()
    escapes_bounds: bool = False

    @abstractmethod
    def build(self, bounds: Bounds, params: Mapping[str, Any]) -> GridGeometry:
        """Build the grid geometry inside ``bounds`` from resolved ``params``."""
        raise NotImplementedError


def resolve_bool(
    grid_type: GridType, params: Mapping[str, Any], name: str
) -> bool:
    """Truthiness lookup of ``name`` with fallback to ``grid_type``'s schema default.

    Shared helper for concrete grid types (replaces per-class ``_flag``) so
    all grid types follow the same pinned coercion rule:
    ``bool(params.get(name, schema_default))``.
    """
    default = next(p.default for p in grid_type.params if p.name == name)
    return bool(params.get(name, default))


def _limits(param: Param) -> tuple[float | None, float | None]:
    return (param.minimum, param.maximum)


def _clamped(value: float, param: Param) -> float:
    """Clamp ``value`` to ``param``'s optional [minimum, maximum] range."""
    minimum, maximum = _limits(param)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def resolve_int(
    grid_type: GridType, params: Mapping[str, Any], name: str
) -> int:
    """Resolve an integer parameter: ``int(round(float(v)))`` clamped to the schema range.

    Shared helper for grid types whose parameters are whole counts (columns,
    rows, axes): incoming values are coerced via ``int(round(float(v)))``
    and clamped to the :class:`Param` ``minimum``/``maximum``.
    """
    param = next(p for p in grid_type.params if p.name == name)
    value = int(round(float(params.get(name, param.default))))
    value = _clamped(float(value), param)
    return int(value)


def resolve_choice(
    grid_type: GridType, params: Mapping[str, Any], name: str
) -> str:
    """Resolve a choice parameter with schema-default fallback.

    Shared helper for grid types with choice parameters: returns the schema
    :class:`Param` default when the incoming value is missing or not one of
    the declared choices (never raises for unknown values).
    """
    param = next(p for p in grid_type.params if p.name == name)
    value = params.get(name, param.default)
    if value not in (param.choices or ()):
        return str(param.default)
    return str(value)


def resolve_number(
    grid_type: GridType, params: Mapping[str, Any], name: str
) -> float:
    """Resolve a float parameter, clamped to the schema ``minimum``/``maximum`` range."""
    param = next(p for p in grid_type.params if p.name == name)
    value = float(params.get(name, param.default))
    return _clamped(value, param)
