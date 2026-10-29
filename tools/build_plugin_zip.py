"""Build the Krita-ready plugin archive in the pykrita directory layout.

Krita's «Tools ▸ Scripts ▸ Import Python Plugin from File…» consumes a ZIP that
unpacks into ``pykrita/`` AS-IS, so the archive root must contain exactly what
the resource folder must contain: ``composition_grid.desktop`` plus the
``composition_grid/`` package. Anything else in the repository (tests, docs,
the uv environment, ``__pycache__``) is build noise and must stay out — a
stale ``.pyc`` inside the archive is the classic way to ship code that is not
the code you tested.

Stdlib only, and deterministic: entries are sorted and timestamps are pinned,
so two builds of the same tree produce byte-identical archives and a rebuilt
plugin can be told apart by its hash rather than by luck.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

PLUGIN_NAME = "composition_grid"
DESKTOP_FILE = f"{PLUGIN_NAME}.desktop"
PACKAGE_DIR = PLUGIN_NAME

# Build noise never ships: caches, environments, VCS and development-only trees.
EXCLUDED_DIRECTORY_NAMES = {
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}
# A ZIP that cannot be validated is worse than no ZIP: it looks installable.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _is_excluded(relative: Path) -> bool:
    """True when any path SEGMENT is a build-noise directory or the leaf is a
    compiled artifact. Segments, not prefixes: ``composition_grid/__init__.py``
    must survive while ``composition_grid/__pycache__/x.pyc`` must not."""
    *directories, leaf = relative.parts
    return bool(EXCLUDED_DIRECTORY_NAMES.intersection(directories)) or (
        Path(leaf).suffix in EXCLUDED_FILE_SUFFIXES
    )


def collect_payload(
    repo_root: Path = REPO_ROOT,
    package_dir: str = PACKAGE_DIR,
    desktop_file: str = DESKTOP_FILE,
) -> list[tuple[Path, str]]:
    """Every ``(source file, archive name)`` pair to ship, sorted stably.

    The archive name uses forward slashes (the ZIP spec's separator) and sits
    at the archive root, mirroring the ``pykrita/`` target layout.
    """
    desktop = repo_root / desktop_file
    if not desktop.is_file():
        raise FileNotFoundError(f"plugin registration file is missing: {desktop}")
    package = repo_root / package_dir
    if not package.is_dir():
        raise FileNotFoundError(f"plugin package directory is missing: {package}")

    payload: list[tuple[Path, str]] = [(desktop, desktop_file)]
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root)
        if _is_excluded(relative):
            continue
        payload.append((path, relative.as_posix()))
    # Sorted by ARCHIVE NAME (not by discovery order): the archive is then
    # reproducible regardless of how the filesystem enumerates the tree.
    payload.sort(key=lambda pair: pair[1])
    return payload


def read_version(
    repo_root: Path = REPO_ROOT, package_dir: str = PACKAGE_DIR
) -> str:
    """``__version__`` from the package's own ``__init__.py``, read as text.

    Parsed rather than imported: this tool must run in any interpreter, with or
    without PyQt, while ``composition_grid/__init__.py`` guards a plugin-host
    side effect.
    """
    init = repo_root / package_dir / "__init__.py"
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise ValueError(f"no __version__ found in {init}")
    return match.group(1)


def build_archive(
    output: Path, repo_root: Path = REPO_ROOT, payload: Iterable[tuple[Path, str]] | None = None
) -> Path:
    """Write the ZIP and return its path (parents created, entries sorted)."""
    pairs = list(collect_payload(repo_root) if payload is None else payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, archive_name in pairs:
            info = zipfile.ZipInfo(archive_name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())
    return output


def validate_archive(
    path: Path, package_dir: str = PACKAGE_DIR, desktop_file: str = DESKTOP_FILE
) -> list[str]:
    """Return every reason this archive is NOT installable (empty = good).

    Checks the three things that silently produce a broken plugin: a missing
    registration file at the archive ROOT, a package without its entry module,
    and a manual path that does not match the shipped tree (``X-Krita-Manual``
    resolves against the unpacked layout, so a mismatch kills the Help link).
    """
    problems: list[str] = []
    if not path.is_file():
        return [f"archive does not exist: {path}"]
    with zipfile.ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist()]
        if desktop_file not in names:
            problems.append(f"{desktop_file} must sit at the archive root (pykrita layout)")
        if f"{package_dir}/__init__.py" not in names:
            problems.append(f"{package_dir}/__init__.py is missing: Krita imports the package")
        for name in names:
            if _is_excluded(Path(name)):
                problems.append(f"build noise shipped inside the archive: {name}")
            if name.startswith("/") or ".." in Path(name).parts:
                problems.append(f"unsafe archive member path: {name}")
        desktop_text = archive.read(desktop_file).decode("utf-8") if desktop_file in names else ""
        manual = re.search(r"^X-Krita-Manual=(.+)$", desktop_text, re.MULTILINE)
        if manual and manual.group(1).strip() not in names:
            problems.append(
                f"X-Krita-Manual points at {manual.group(1).strip()!r}, "
                "which is not in the archive (the Help link would be dead)"
            )
    return problems


def _make_console_output_safe() -> None:
    """Never let REPORTING the artifact kill the build.

    On a Windows console with a legacy code page (cp1251/cp866) printing the
    Cyrillic summary raises UnicodeEncodeError AFTER the archive is already
    written — a build that "fails" while having succeeded. The stream encoding
    is therefore pinned to UTF-8 with replacement; the message itself stays
    ASCII-only for the same reason (``▸`` and ``…`` are not encodable
    everywhere)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError) as exc:  # noqa: BLE001 — cosmetic only, build already done
                print(f"warning: console encoding not adjusted: {exc!r}", file=sys.stderr)


#: What can be packaged: the shipped plugin, and the dev-only diagnostics
#: companion. Both need the same pykrita root layout and the same validation,
#: so one builder serves both instead of a second copy of these rules.
TARGETS: "dict[str, dict[str, object]]" = {
    "composition_grid": {
        "root": REPO_ROOT,
        "package": PACKAGE_DIR,
        "desktop": DESKTOP_FILE,
    },
    "grid_diagnostics": {
        "root": REPO_ROOT / "devtools",
        "package": "grid_diagnostics",
        "desktop": "grid_diagnostics.desktop",
    },
}


def main(argv: "list[str] | None" = None) -> int:
    _make_console_output_safe()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--plugin",
        choices=sorted(TARGETS),
        default="composition_grid",
        help="which plugin to package (default: the shipped one)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="archive path; default dist/<plugin>-<version>.zip",
    )
    arguments = parser.parse_args(argv)
    target = TARGETS[arguments.plugin]
    root, package_dir, desktop_file = (
        target["root"],
        target["package"],
        target["desktop"],
    )
    version = read_version(root, package_dir)
    output = arguments.output or REPO_ROOT / "dist" / f"{package_dir}-{version}.zip"
    payload = collect_payload(root, package_dir, desktop_file)
    build_archive(output, payload=payload)
    problems = validate_archive(output, package_dir, desktop_file)
    if problems:
        print("Archive is NOT installable:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"{output}  (version {version}, {len(payload)} files: "
        f"{desktop_file} + {package_dir}/)"
    )
    print("Install: Tools > Scripts > Import Python Plugin from File..., then restart Krita.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
