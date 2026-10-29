import importlib

import pytest


def try_import_qt():
    """Import PyQt (6 first, then 5). pytest.skip when neither is available."""
    for major in (6, 5):
        try:
            # The submodules too: an installed binding whose native libraries
            # cannot load (e.g. an old libstdc++) must skip, not error.
            qt_modules = tuple(
                importlib.import_module(f"PyQt{major}.{name}")
                for name in ("QtWidgets", "QtCore", "QtGui")
            )
        except ImportError:
            continue
        return qt_modules
    pytest.skip("PyQt not installed (uv install if needed)", allow_module_level=False)
