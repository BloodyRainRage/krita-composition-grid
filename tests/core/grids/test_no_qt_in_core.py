"""Guard test: ``composition_grid.core`` must stay free of Qt imports.

The pure-math core is imported by the plugin UI and must remain stdlib-only
(no PyQt5 / PyQt6 / qt_compat anywhere under ``composition_grid/core``).

Note on partial builds: ``composition_grid/core/grids/`` did not exist when
this test was written (later grid-type tasks create it and may add more
modules). The scan therefore globs whatever ``*.py`` files exist under
``composition_grid/core`` at run time and asserts simply that at least the
core package itself was found — absent subdirectories are skipped silently.
"""

import re
from pathlib import Path

def _resolve_core_dir() -> Path:
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "composition_grid" / "core"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not locate composition_grid/core under the repo root")


# The regex targets import/from statements and rejects Qt-related names.
# `from composition_grid.core.styles import ...` is fine; both sides are
# matched anyway because any import line containing PyQt5/PyQt6/qt_compat
# fails, and plain stdlib/own-package lines contain none of those names.
_QT_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\b(?:(?!PyQt5|PyQt6|qt_compat).)*(PyQt5|PyQt6|qt_compat)",
    re.IGNORECASE,
)


def test_no_qt_imports_in_core():
    core_dir = _resolve_core_dir()
    sources = sorted(p for p in core_dir.rglob("*.py") if p.is_file())

    # sanity: the core package itself exists (guards path drift)
    assert sources, f"no sources found under {core_dir}"

    offenders = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _QT_IMPORT_RE.search(line):
                offenders.append(f"{path.relative_to(core_dir)}:{lineno}: {line.strip()}")

    assert not offenders, "Qt/krita imports inside composition_grid/core:\n" + "\n".join(
        offenders
    )
