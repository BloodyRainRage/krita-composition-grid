"""Guard test: ``composition_grid.ui`` Qt-import policy.

Two scopes with two rules (the Qt docker panel and the
param-widget factory legally live in this same package, so a former
blanket "no Qt anywhere in ui" scan would contradict the layout — mirrors
the narrowings in ``tests/presentation/test_no_qt_in_presentation.py``
and ``tests/canvas/test_no_qt_in_canvas.py``):

* PURE allowlist (``__init__.py``, ``transform_controller.py``) — the
  pure-logic drag controller consumed headlessly: no PyQt, no
  ``composition_grid.qt_compat``, no krita imports at all. This keeps the
  controller importable without any Qt presence and keeps the guard
  MEANINGFUL: any PyQt import attempt in the pure modules still fails this
  test.
* Every other module under ``ui`` (``param_widgets.py``, ``docker_panel.py``
  and any later Qt widget) may import Qt ONLY through
  ``composition_grid.qt_compat`` — raw ``PyQt5`` / ``PyQt6`` / ``krita``
  imports stay banned everywhere. The krita ban is deliberate even for
  lazy in-function imports (the ``settings_store.KritaTransport`` pattern):
  the ui layer must stay testable with a *base-injected* docker
  (``docker_panel.make_panel_docker_class``), and the one sanctioned lazy
  ``krita`` import for the docker base lives in the plugin entry module
  (outside this package), not here.
"""

import re
from pathlib import Path

# Pure roster: anything here must not even mention Qt bindings / qt_compat.
_PURE_MODULES = ("__init__.py", "transform_controller.py")

_QT_OR_COMPAT_RE = re.compile(
    r"^\s*(?:import|from)\b(?:(?!PyQt5|PyQt6|qt_compat|\bkrita\b).)*(PyQt5|PyQt6|qt_compat|\bkrita\b)",
    re.IGNORECASE,
)
# Qt widgets: qt_compat is the ONLY legal Qt route; raw bindings and krita banned.
_RAW_QT_RE = re.compile(
    r"^\s*(?:import|from)\b(?:(?!PyQt5|PyQt6|\bkrita\b).)*(PyQt5|PyQt6|\bkrita\b)",
    re.IGNORECASE,
)


def _ui_dir() -> Path:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "composition_grid" / "ui"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not locate composition_grid/ui under the repo root")


def _violations(directory: Path, names, pattern) -> list[str]:
    violations: list[str] = []
    for name in sorted(names):
        path = directory / name
        assert path.is_file(), f"expected file missing under ui/: {name}"
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{name}:{lineno}: {line.strip()}")
    return violations


def test_pure_modules_stay_qt_free():
    """The pure roster must not import PyQt, qt_compat, or krita at all."""
    directory = _ui_dir()
    violations = _violations(directory, _PURE_MODULES, _QT_OR_COMPAT_RE)
    assert not violations, (
        "Qt/krita/qt_compat imports inside the pure ui modules:\n"
        + "\n".join(violations)
    )


def test_other_ui_modules_use_qt_compat_only():
    """Every non-allowlisted module under ``ui/`` — INCLUDING any future
    subpackages (``rglob`` walk) — may import Qt only via
    composition_grid.qt_compat; raw PyQt5/PyQt6/krita imports stay banned.
    (Only the two Qt widget modules exist today; the recursive walk
    future-proofs the guard.)"""
    directory = _ui_dir()
    pure_paths = {directory / name for name in _PURE_MODULES}
    others = [
        path
        for path in directory.rglob("*.py")
        if path.is_file() and path not in pure_paths
    ]
    assert others, "expected the Qt widget modules to exist under ui/"
    violations = _violations(
        directory, (path.relative_to(directory) for path in others), _RAW_QT_RE
    )
    assert not violations, (
        "raw PyQt/krita imports (bypassing composition_grid.qt_compat):\n"
        + "\n".join(violations)
    )
