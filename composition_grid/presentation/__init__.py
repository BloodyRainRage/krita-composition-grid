"""Presentation package: pure-arithmetic marker layout helpers.

No Qt, no krita in this package (guard test:
``tests/presentation/test_no_qt_in_presentation.py``) — the edit-session
handle layout and hit-testing are pure math shared by the Qt widget layer
(the Qt layers render and route through ``composition_grid.qt_compat``) and
headless tests.
"""

from composition_grid.presentation.handle_layout import (
    DEFAULT_VP_X,
    DEFAULT_VP_Y,
    HandleId,
    ROTATE_HANDLE_OFFSET_PX,
    cursor_shape,
    effective_tolerance,
    handle_ids,
    handle_positions,
    hit_test,
)

__all__ = [
    "HandleId",
    "DEFAULT_VP_X",
    "DEFAULT_VP_Y",
    "ROTATE_HANDLE_OFFSET_PX",
    "cursor_shape",
    "effective_tolerance",
    "handle_ids",
    "handle_positions",
    "hit_test",
]
