"""Model layer: per-document grid state (``GridModel`` + ``GridStateService``).

No Qt, no krita — stdlib plus the plugin's own pure core only. Importing this
package pulls in :mod:`composition_grid.core.grids`, whose module imports
register every concrete grid type (the service defaults to the first one).
"""

from composition_grid.core.grids import GridType, Param  # noqa: F401

from composition_grid.model.grid_model import GridModel, GridStateService

__all__ = [
    "GridModel",
    "GridStateService",
    "GridType",
    "Param",
]
