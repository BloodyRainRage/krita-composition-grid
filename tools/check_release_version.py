"""Fail unless a release tag matches the version the plugin actually ships.

An archive named ``composition_grid-0.2.0.zip`` published under tag ``v0.1.0``
is the kind of mistake a user only discovers after installing it, so the
release workflow asks this script to compare three sources of truth — the tag,
``composition_grid.__version__`` and ``pyproject.toml`` — and stops on the
first difference. stdout carries ``version=<version>`` for the caller to reuse
(``>> $GITHUB_OUTPUT``); the human-readable report goes to stderr.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_plugin_zip import PLUGIN_NAME, REPO_ROOT, read_version  # noqa: E402


def read_pyproject_version(repo_root: Path = REPO_ROOT) -> str:
    """``version`` from the ``[project]`` table, read as text."""
    text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise ValueError(f"no version found in {repo_root / 'pyproject.toml'}")
    return match.group(1)


def main(argv: "list[str] | None" = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1 or not arguments[0]:
        print("usage: python tools/check_release_version.py vX.Y.Z", file=sys.stderr)
        return 2
    tag = arguments[0]

    try:
        package_version = read_version()
        project_version = read_pyproject_version()
    except (FileNotFoundError, ValueError) as exc:
        print(f"::error::version is not readable: {exc}", file=sys.stderr)
        return 1

    if project_version != package_version:
        print(
            f"::error::pyproject.toml says {project_version!r} but "
            f"{PLUGIN_NAME}/__init__.py says {package_version!r} — bump both.",
            file=sys.stderr,
        )
        return 1

    expected_tag = f"v{package_version}"
    if tag != expected_tag:
        print(
            f"::error::tag {tag!r} does not match the plugin version "
            f"{package_version!r}: bump {PLUGIN_NAME}/__init__.py and "
            f"pyproject.toml to {tag.lstrip('v')!r} and commit, or tag "
            f"{expected_tag!r} instead.",
            file=sys.stderr,
        )
        return 1

    print(
        f"tag {tag} matches {PLUGIN_NAME} {package_version} "
        f"(pyproject agrees)",
        file=sys.stderr,
    )
    print(f"version={package_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
