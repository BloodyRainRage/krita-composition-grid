"""``composition_grid.ui``: interaction controllers and panel widgets.

Mixed package (guard: ``tests/ui/test_no_qt_in_ui.py``): the pure-logic
layer (``transform_controller.py``, drag math, no Qt/krita at all) plus the
Qt widget layer (``param_widgets.py``, ``docker_panel.py``) whose
Qt names come ONLY from ``composition_grid.qt_compat`` and which never
reference krita (the docker takes its Krita base class as an injected
parameter).
"""

from composition_grid.ui.transform_controller import TransformController

__all__ = ["TransformController"]
