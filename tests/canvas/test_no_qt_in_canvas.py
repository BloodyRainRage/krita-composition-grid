"""Guard test: ``composition_grid.canvas`` Qt-import policy.

Two scopes with two rules (narrowed: the Qt canvas locator legally
lives in this same package, so a former blanket "no qt_compat anywhere"
scan would contradict the layout — see the matching narrowing in
``tests/presentation/test_no_qt_in_presentation.py``):

* PURE allowlist (``__init__.py``, ``doc_view_mapper.py``) — the pure
  arithmetic layer (doc↔widget mapping) consumed headlessly: no PyQt, no
  ``composition_grid.qt_compat``, no krita imports at all. This keeps the
  mapper importable without any Qt presence and keeps the guard MEANINGFUL:
  any PyQt import attempt in the pure modules still fails this test.
* Every other module under ``canvas`` (``canvas_locator.py`` and later Qt
  overlay/context modules) may import Qt ONLY through
  ``composition_grid.qt_compat`` — raw ``PyQt5`` / ``PyQt6`` / ``krita``
  imports stay banned everywhere.
"""

import re
from pathlib import Path

# Pure roster: anything here must not even mention Qt bindings / qt_compat.
# ``view_metrics.py`` is on it because the computed-metrics layer is pure
# arithmetic over duck-typed numbers, so it stays level-1 testable with no Qt
# present at all (the repo rule for the pure core).
_PURE_MODULES = ("__init__.py", "doc_view_mapper.py", "view_metrics.py")

_QT_OR_COMPAT_RE = re.compile(
    r"^\s*(?:import|from)\b(?:(?!PyQt5|PyQt6|qt_compat|\bkrita\b).)*(PyQt5|PyQt6|qt_compat|\bkrita\b)",
    re.IGNORECASE,
)
# Qt modules: qt_compat is the ONLY legal Qt route; raw bindings and krita banned.
_RAW_QT_RE = re.compile(
    r"^\s*(?:import|from)\b(?:(?!PyQt5|PyQt6|\bkrita\b).)*(PyQt5|PyQt6|\bkrita\b)",
    re.IGNORECASE,
)


def _canvas_dir() -> Path:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "composition_grid" / "canvas"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not locate composition_grid/canvas under the repo root")


def _violations(directory: Path, names, pattern) -> list[str]:
    violations: list[str] = []
    for name in sorted(names):
        path = directory / name
        assert path.is_file(), f"expected file missing under canvas/: {name}"
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{name}:{lineno}: {line.strip()}")
    return violations


def test_pure_modules_stay_qt_free():
    """The pure roster must not import PyQt, qt_compat, or krita at all."""
    directory = _canvas_dir()
    violations = _violations(directory, _PURE_MODULES, _QT_OR_COMPAT_RE)
    assert not violations, (
        "Qt/krita/qt_compat imports inside the pure canvas modules:\n"
        + "\n".join(violations)
    )


def test_other_canvas_modules_use_qt_compat_only():
    """Every non-allowlisted module under ``canvas/`` — INCLUDING any future
    subpackages (``rglob`` walk) — may import Qt only via
    composition_grid.qt_compat; raw PyQt5/PyQt6/krita imports stay banned.
    (Only ``canvas_locator.py`` exists today; the recursive walk
    future-proofs the guard for further overlay/context modules.)"""
    directory = _canvas_dir()
    pure_paths = {directory / name for name in _PURE_MODULES}
    others = [
        path
        for path in directory.rglob("*.py")
        if path.is_file() and path not in pure_paths
    ]
    assert others, "expected the Qt canvas modules to exist under canvas/"
    violations = _violations(
        directory, (path.relative_to(directory) for path in others), _RAW_QT_RE
    )
    assert not violations, (
        "raw PyQt/krita imports (bypassing composition_grid.qt_compat):\n"
        + "\n".join(violations)
    )
