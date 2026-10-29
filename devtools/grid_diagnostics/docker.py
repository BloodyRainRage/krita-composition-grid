"""The diagnostics DOCKER: a button, a report, no console needed.

Why a docker and not only a menu item: on the first live Krita 5.3.1 run the
user enabled the plugin and looked in Settings ▸ Dockers — an Extension's
action lives in Tools ▸ Scripts and is invisible there, so a menu-only tool
asked them to hunt through a channel we cannot verify on their machine. Dockers
are the channel they demonstrably reach, and the libkis lesson from the main
plugin applies unchanged: ``krita.DockWidget`` IS a ``QDockWidget``, so the
content goes through ``setWidget()`` and the Dockers-list label comes from
``setWindowTitle()``.

Base classes are INJECTED (``krita.DockWidget`` at runtime, ``QDockWidget`` in
tests): this file must not import ``krita``, and a fake must never be more
permissive than the host — the mistake that hid three earlier defects.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("composition_grid")

__all__ = ["DOCKER_ID", "DOCKER_WINDOW_TITLE", "make_diagnostics_docker_class"]

DOCKER_ID = "grid_diagnostics_docker"
DOCKER_WINDOW_TITLE = "Composition Grid Diagnostics"
"""The label in Settings ▸ Dockers (``windowTitle``, per the Krita howto)."""

_REPORT_PLACEHOLDER = (
    "Нажмите «Run Diagnostics» — отчёт проходит по цепи плагина (регистрация →\n"
    "активный документ → view -> Document -> Canvas-сигналы → виджет холста →\n"
    "plausibility-гейт → view_state → модель) и печатает первое оборванное звено.\n"
    "Только чтение: ни одного изменения документа, вью или состояния."
)


def make_diagnostics_docker_class(base, report_runner=None):
    """Build the docker class over an injected ``base`` (``krita.DockWidget``).

    ``report_runner`` is the seam tests use; production calls
    :func:`composition_grid.diagnostics.collect_report` on ``Krita.instance()``.
    """

    class DiagnosticsDocker(base):
        def __init__(self, *args):
            from composition_grid import qt_compat

            super().__init__(*args)
            widgets = qt_compat.QtWidgets
            self.setWindowTitle(DOCKER_WINDOW_TITLE)

            content = widgets.QWidget(self)
            layout = widgets.QVBoxLayout(content)
            layout.setContentsMargins(6, 6, 6, 6)

            self.run_button = widgets.QPushButton("Run Diagnostics")
            self.run_button.setObjectName("run_diagnostics")
            self.run_button.clicked.connect(self._on_run)
            layout.addWidget(self.run_button)

            self.report_view = widgets.QPlainTextEdit(content)
            self.report_view.setObjectName("report")
            self.report_view.setReadOnly(True)
            self.report_view.setPlainText(_REPORT_PLACEHOLDER)
            layout.addWidget(self.report_view)

            # QDockWidget shows ONLY what setWidget() installs (the lesson that
            # made the main panel render empty).
            self.setWidget(content)

        def canvasChanged(self, canvas) -> None:  # noqa: N802 — libkis virtual
            """MUST exist: libkis declares ``DockWidget.canvasChanged()`` abstract,
            and PyQt5 enforces it — a subclass without it raises
            ``NotImplementedError: DockWidget.canvasChanged() is abstract and must
            be overridden`` when Krita creates the docker. Observed on the live
            host: the diagnostics docker appeared, Krita logged the error, and
            the panel kept working — but the traceback is exactly the kind of
            noise that makes a user doubt the whole plugin.

            This docker has no canvas to track, so the override ignores its
            argument and says so at DEBUG."""
            logger.debug("grid_diagnostics: canvasChanged ignored (no canvas state)")

        def _on_run(self, *args) -> None:
            """Produce the report. ``*args`` swallows Qt's always-delivered
            ``clicked(bool)`` payload."""
            self.report_view.setPlainText(self._collect())

        def _collect(self) -> str:
            try:
                if report_runner is not None:
                    return report_runner()
                from composition_grid.diagnostics import collect_report, write_report
                from composition_grid.plugin_support import krita_module

                report = collect_report(krita_module().Krita.instance())
                try:
                    path = write_report(report)
                    report += f"\n\nОтчёт сохранён: {path}"
                except OSError as exc:
                    logger.warning("grid_diagnostics: report file failed: %r", exc)
                    report += "\n\n(в файл не записан)"
                return report
            except Exception as exc:  # noqa: BLE001 — a debug panel must not raise into Qt
                logger.exception("grid_diagnostics: report failed")
                return f"Диагностика не удалась: {exc!r}"

    DiagnosticsDocker.__name__ = "DiagnosticsDocker"
    return DiagnosticsDocker
