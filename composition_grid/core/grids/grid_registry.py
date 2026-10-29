"""Registry of grid types, keyed by their ``GridType.id``.

Pure-math core: no Qt, no krita — only stdlib. The backing store is a
module-level private list (simple singleton): registration order is
preserved for :func:`all_types`, and grid ids are unique.
"""

from __future__ import annotations

from typing import Any, Dict, List

from composition_grid.core.grids.base import GridType

# KISS: one module-level list as the single backing store of the registry.
# ``Dict``/``List`` come from typing: Python 3.10 compatible but explicit.
_REGISTRY: List[type[GridType]] = []


def register(cls: type[GridType]) -> None:
    """Add ``cls`` to the registry.

    Raises:
        ValueError: if a grid type with the same ``id`` is already
            registered (double-defense: tasks that discover grid types
            never create duplicates anyway).
    """
    for existing in _REGISTRY:
        if existing.id == cls.id:
            raise ValueError(
                f"grid id already registered: {cls.id!r} "
                f"(existing={existing.__name__}, new={cls.__name__})"
            )
    _REGISTRY.append(cls)


def all_types() -> "tuple[type[GridType], ...]":
    """All registered grid types, in registration order."""
    return tuple(_REGISTRY)


def get(grid_id: str) -> "type[GridType]":
    """Look up a grid type by its id.

    Raises:
        KeyError: with a readable message that lists the registered ids.
    """
    for existing in _REGISTRY:
        if existing.id == grid_id:
            return existing
    known = ", ".join(existing.id for existing in _REGISTRY) or "<none registered>"
    raise KeyError(f"unknown grid id: {grid_id!r}; registered grid ids: {known}")


def default_params(cls: type[GridType]) -> "Dict[str, Any]":
    """Parameter dict for ``cls`` filled from each :class:`Param` default."""
    return {param.name: param.default for param in cls.params}
