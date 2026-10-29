"""Dev-only companion plugin: the Composition Grid self-report as a menu item.

Krita 5.3.1 ships no Python console, so "run this snippet in the console" is
not an option for a user who is already stuck. This plugin adds ONE item,
Tools ▸ Scripts ▸ **Composition Grid Diagnostics**, which runs the plugin's own
read-only chain report (``composition_grid.diagnostics``) and shows it in a
dialog — and writes the same text to a temp file so it can be attached rather
than transcribed.

Deliberately NOT part of the shipped plugin: it is a developer tool, it lives
under ``devtools/`` so ``tools/build_plugin_zip.py`` never puts it in a user's
archive, and it does nothing except read. Install it the same way as the main
plugin (copy both ``grid_diagnostics.desktop`` and ``grid_diagnostics/`` into
``pykrita``, enable, restart), and it requires Composition Grid to be enabled
too — the code it reports on is the main plugin's.
"""

from __future__ import annotations

import importlib.util

__version__ = "0.1.0"


def _krita_available() -> bool:
    """``True`` inside Krita: resolves the libkis module's spec WITHOUT
    importing it. Outside Krita the package must import cleanly (the same
    headless contract as the main plugin)."""
    try:
        return importlib.util.find_spec("krita") is not None
    except (ImportError, ValueError):
        return False


if _krita_available():  # pragma: no cover - requires a running Krita
    from grid_diagnostics.plugin import *  # noqa: F401,F403

    register_plugin()  # noqa: F405
