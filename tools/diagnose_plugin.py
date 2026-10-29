"""Console entry point for the plugin's self-diagnostics (thin wrapper).

The report logic lives in ONE place — :mod:`composition_grid.diagnostics`,
inside the plugin — because the plugin is the thing that has to describe
itself, and its state is reachable only through the objects it owns. This file
just resolves the running libkis ``Krita`` instance and prints/saves the
report.

Krita 5.3.1 ships no Python console, so the normal route is the companion dev
plugin in ``devtools/grid_diagnostics`` (see README):
it adds
Tools ▸ Scripts ▸ «Composition Grid Diagnostics», which runs the same report
into a dialog and into the same temp file.
"""

from __future__ import annotations

import sys


def _safe_console() -> None:
    """The report is Cyrillic: a legacy-code-page console (cp1251/cp866) would
    raise UnicodeEncodeError mid-print and hide the line that names the defect."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # noqa: BLE001 — cosmetic only
                pass


def main() -> int:
    _safe_console()
    try:
        from krita import Krita
    except ImportError:
        print(
            "Этот скрипт запускается ВНУТРИ Krita: нужен модуль `krita`, а\n"
            "проверяемое состояние существует только в работающем приложении.\n"
            "Как запустить внутри Krita:\n"
            "  1) Tools > Scripts > Composition Grid Diagnostics — dev-плагин\n"
            "     из devtools/grid_diagnostics (тот же отчёт в диалоге), или\n"
            "  2) exec(open(r'"
            + __file__
            + "', encoding='utf-8').read())\n"
            "     в Python-консоли, если она у вас есть.",
            file=sys.stderr,
        )
        return 2

    from composition_grid.diagnostics import collect_report, write_report

    report = collect_report(Krita.instance())
    print(report)
    path = write_report(report)
    print(f"\nОтчёт сохранён: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
