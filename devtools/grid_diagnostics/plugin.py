"""The dev plugin's single action: run the report, show it, save it.

Nothing here decides what the report SAYS — that is
:func:`composition_grid.diagnostics.collect_report`, which lives with the code
it inspects. This module only wires it to a menu item and to a dialog, so the
two implementations cannot drift.

Qt arrives through ``composition_grid.qt_compat`` (never a direct binding
import), ``krita`` only through the plugin's own lazy seam
:func:`composition_grid.plugin_support.krita_module`, and every host call is
kept inside a slot that cannot raise outward (the Krita host must never be
taken down by a debugging tool).
"""

from __future__ import annotations

import functools
import logging

from composition_grid.plugin_support import (
    extension_base,
    krita_module,
    never_raises_slot,
)

logger = logging.getLogger("composition_grid")

__all__ = [
    "ACTION_ID",
    "EXTENSION_ID",
    "GridDiagnosticsExtension",
    "create_docker_factory",
    "produce_report",
    "register_plugin",
]

EXTENSION_ID = "grid_diagnostics"
ACTION_ID = "grid_diagnostics_report"
MENU_LOCATION = "tools/scripts"

#: The extension instance must outlive ``createActions`` (a collected
#: extension is a dead action), and Krita keeps no Python-side
#: reference we can rely on.
HELD_EXTENSIONS: "list" = []


class GridDiagnosticsExtension(extension_base()):
    """One menu item that reports the state of the real plugin."""

    def __init__(self, *args):
        super().__init__(*args)
        self._held: "list" = []

    def setup(self) -> None:
        """No-op: this extension has nothing to configure (libkis contract)."""

    def createActions(self, window) -> None:  # noqa: N802 — libkis virtual
        """Add «Composition Grid Diagnostics» to Tools ▸ Scripts for *window*.

        The action AND the slot are held strongly: a collected slot is a dead
        menu item, which is the failure mode this whole tool exists to catch."""
        try:
            action = window.createAction(ACTION_ID, "Composition Grid Diagnostics", MENU_LOCATION)
        except Exception:  # noqa: BLE001 — a host window we cannot touch is not fatal
            logger.exception("grid_diagnostics: createAction(%r) failed", ACTION_ID)
            return
        if action is None:
            logger.warning("grid_diagnostics: window.createAction answered None")
            return
        slot = never_raises_slot(functools.partial(self._show_report, window))
        try:
            action.triggered.connect(slot)
        except Exception:  # noqa: BLE001
            logger.exception("grid_diagnostics: connecting the action failed")
            return
        self._held.append((action, slot))

    # ------------------------------------------------------------- the action

    def _show_report(self, window, *args) -> None:
        """Run the shared report and show it. ``*args`` swallows the
        ``triggered(bool)`` payload Qt always delivers."""
        try:
            self._inform(window, "Composition Grid — самодиагностика", produce_report())
        except Exception:  # noqa: BLE001 — a debug action must not raise into Qt
            logger.exception("grid_diagnostics: the report action failed")
            self._inform(window, "Диагностика недоступна", "Отчёт не собрался (см. лог).")

    def _inform(self, window, title: str, text: str) -> None:
        """Show *text* in a message box parented to the window's qwindow when
        one is reachable; without Qt or without a window the report still went
        to the log and to the temp file, so nothing is lost."""
        try:
            from composition_grid import qt_compat

            if not qt_compat.QT_AVAILABLE:
                raise RuntimeError("Qt binding unavailable")
            parent = None
            if window is not None:
                parent = getattr(window, "qwindow", lambda: None)()
            box = qt_compat.QtWidgets.QMessageBox(qt_compat.QtWidgets.QMessageBox.Information, title, text, parent)
            box.setWindowTitle(title)
            box.exec()
        except Exception:  # noqa: BLE001 — a debug tool must never take Krita down
            logger.exception("grid_diagnostics: could not show the report dialog")


def produce_report() -> str:
    """Run the plugin's read-only chain report and persist it (one
    implementation shared by the menu action and the docker).

    Raises nothing on purpose: the caller shows whatever comes back."""
    from composition_grid.diagnostics import collect_report, write_report

    report = collect_report(krita_module().Krita.instance())
    try:
        report += f"\n\nОтчёт сохранён: {write_report(report)}"
    except OSError as exc:  # noqa: BLE001 — a missing temp file must not lose the report
        logger.warning("grid_diagnostics: report file could not be written: %r", exc)
        report += "\n\n(в файл не записан)"
    return report


def register_plugin(instance=None):
    """Register the extension AND the docker on ``Krita.instance()``.

    The docker is the discoverable entry (Settings ▸ Dockers ▸ «Composition
    Grid Diagnostics»); the menu action stays as the second route. Registration
    of one failing must not prevent the other — both channels are held strongly
    and every failure is logged, never raised into the Krita loader."""
    if instance is None:
        instance = krita_module().Krita.instance()
    extension = GridDiagnosticsExtension(instance)
    HELD_EXTENSIONS.append(extension)
    try:
        instance.addExtension(extension)
    except Exception:  # noqa: BLE001 — registration failure is reported, not fatal
        logger.exception("grid_diagnostics: addExtension(%r) failed", EXTENSION_ID)
    try:
        instance.addDockWidgetFactory(create_docker_factory(instance))
    except Exception:  # noqa: BLE001 — the action route still works without the docker
        logger.exception("grid_diagnostics: docker factory registration failed")
    return extension


def create_docker_factory(instance=None):
    """The ``DockWidgetFactory`` for the diagnostics docker (registered once)."""
    from grid_diagnostics.docker import DOCKER_ID, make_diagnostics_docker_class

    krita = krita_module()
    docker_class = make_diagnostics_docker_class(
        krita.DockWidget, report_runner=produce_report
    )
    factory = krita.DockWidgetFactory(
        DOCKER_ID, krita.DockWidgetFactoryBase.DockRight, docker_class
    )
    HELD_DOCKER_FACTORIES.append(factory)
    return factory


#: Factories are held for the same reason as the extension: a collected factory
#: is a docker Krita can no longer create.
HELD_DOCKER_FACTORIES: "list" = []
