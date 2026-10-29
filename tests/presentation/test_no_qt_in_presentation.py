"""Guard test: ``composition_grid.presentation`` Qt-import policy.

Two scopes with two rules (the Qt painters legally live
in this same package, so a blanket "no qt_compat anywhere" scan is gone):

* PURE allowlist (``__init__.py``, ``handle_layout.py``) — the pure arithmetic
  layer consumed headlessly: no PyQt, no ``composition_grid.qt_compat``, no
  krita imports at all. This is what keeps the marker layout / hit-testing
  layer importable without any Qt presence (and keeps the guard MEANINGFUL:
  any PyQt import attempt in ``handle_layout.py`` still fails this test).
* Every other module under ``presentation`` (the Qt painters) may import Qt
  ONLY through ``composition_grid.qt_compat`` — raw ``PyQt5`` / ``PyQt6`` /
  ``krita`` imports stay banned everywhere.
"""

import re
from pathlib import Path

# Pure roster: anything here must not even mention Qt bindings / qt_compat.
_PURE_MODULES = ("__init__.py", "handle_layout.py")

_QT_OR_COMPAT_RE = re.compile(
    r"^\s*(?:import|from)\b(?:(?!PyQt5|PyQt6|qt_compat|\bkrita\b).)*(PyQt5|PyQt6|qt_compat|\bkrita\b)",
    re.IGNORECASE,
)
# Painters: qt_compat is the ONLY legal Qt route; raw bindings and krita banned.
_RAW_QT_RE = re.compile(
    r"^\s*(?:import|from)\b(?:(?!PyQt5|PyQt6|\bkrita\b).)*(PyQt5|PyQt6|\bkrita\b)",
    re.IGNORECASE,
)


def _presentation_dir() -> Path:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "composition_grid" / "presentation"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not locate composition_grid/presentation under the repo root")


def _violations(directory: Path, names, pattern) -> list[str]:
    violations: list[str] = []
    for name in sorted(names):
        path = directory / name
        assert path.is_file(), f"expected file missing under presentation/: {name}"
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{name}:{lineno}: {line.strip()}")
    return violations


def test_pure_modules_stay_qt_free():
    """The pure roster must not import PyQt, qt_compat, or krita at all."""
    directory = _presentation_dir()
    violations = _violations(directory, _PURE_MODULES, _QT_OR_COMPAT_RE)
    assert not violations, (
        "Qt/krita/qt_compat imports inside the pure presentation modules:\n"
        + "\n".join(violations)
    )


def test_other_presentation_modules_use_qt_compat_only():
    """Every non-allowlisted module under ``presentation/`` — INCLUDING any
    future subpackages (``rglob`` walk) — may import Qt only via
    composition_grid.qt_compat; raw PyQt5/PyQt6/krita imports stay banned.
    (The package is flat today; the recursive walk future-proofs the guard.)"""
    directory = _presentation_dir()
    pure_paths = {directory / name for name in _PURE_MODULES}
    others = [
        path
        for path in directory.rglob("*.py")
        if path.is_file() and path not in pure_paths
    ]
    assert others, "expected the Qt painter modules to exist under presentation/"
    violations = _violations(
        directory, (path.relative_to(directory) for path in others), _RAW_QT_RE
    )
    assert not violations, (
        "raw PyQt/krita imports (bypassing composition_grid.qt_compat):\n"
        + "\n".join(violations)
    )
