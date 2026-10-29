"""Tests for the plugin archive builder (``tools/build_plugin_zip.py``).

The archive is the artifact a user installs, so these tests pin the properties
that make it installable and trustworthy rather than re-testing ZIP mechanics:
the pykrita root layout, build noise kept out, the ``X-Krita-Manual`` path
actually present in the archive, byte-for-byte fidelity with the working tree,
and determinism (a rebuilt archive must be distinguishable by hash, not by
luck). The builder is loaded from its file because ``tools/`` is not a package.
"""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "build_plugin_zip", REPO_ROOT / "tools" / "build_plugin_zip.py"
)
assert _spec is not None and _spec.loader is not None
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)


def _make_fake_repo(root: Path) -> Path:
    """A minimal but realistic repo tree: registration file, package with a
    nested subpackage, a manual, plus the build noise that must never ship."""
    package = root / "composition_grid"
    (package / "core").mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (package / "Manual.html").write_text("<h1>manual</h1>\n", encoding="utf-8")
    (package / "core" / "geometry.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "geometry.cpython-310.pyc").write_bytes(b"stale")
    (package / "core" / "old.pyo").write_bytes(b"stale")
    (root / ".venv").mkdir()
    (root / ".venv" / "leak.py").write_text("import os\n", encoding="utf-8")
    (root / "composition_grid.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Service\n"
        "ServiceTypes=Krita/PythonPlugin\n"
        "X-KDE-Library=composition_grid\n"
        "X-Krita-Manual=composition_grid/Manual.html\n"
        "Name=Composition Grid\n",
        encoding="utf-8",
    )
    return root


def test_payload_is_the_pykrita_layout_without_build_noise(tmp_path):
    """Root holds the desktop file and the package; caches, compiled artifacts
    and the virtualenv never enter the archive."""
    names = [name for _source, name in builder.collect_payload(_make_fake_repo(tmp_path))]

    assert names == sorted(names), "entries must be sorted for a reproducible archive"
    assert "composition_grid.desktop" in names
    assert "composition_grid/__init__.py" in names
    assert "composition_grid/core/geometry.py" in names
    assert not any("pycache" in name or name.endswith((".pyc", ".pyo")) for name in names)
    assert not any(".venv" in name for name in names)


def test_built_archive_validates_and_preserves_bytes(tmp_path):
    """A good archive passes validation, and every member equals its source —
    the guarantee that shipping is not shipping a stale copy."""
    repo = _make_fake_repo(tmp_path)
    output = builder.build_archive(tmp_path / "dist" / "plugin.zip", payload=builder.collect_payload(repo))

    assert builder.validate_archive(output) == []
    with zipfile.ZipFile(output) as archive:
        for source, name in builder.collect_payload(repo):
            assert archive.read(name) == source.read_bytes(), name


def test_archive_is_byte_identical_across_rebuilds(tmp_path):
    """Determinism: same tree → same bytes, so a rebuild can be verified by hash."""
    repo = _make_fake_repo(tmp_path)
    payload = builder.collect_payload(repo)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    builder.build_archive(first, payload=payload)
    builder.build_archive(second, payload=payload)

    assert first.read_bytes() == second.read_bytes()


def test_validation_names_the_failure_a_user_would_hit(tmp_path):
    """Each broken shape is reported, not silently accepted: the installer
    would otherwise 'succeed' with an unloadable plugin."""
    repo = _make_fake_repo(tmp_path)

    def _built_without(name):
        payload = [pair for pair in builder.collect_payload(repo) if pair[1] != name]
        target = tmp_path / f"broken-{name.replace('/', '_')}.zip"
        builder.build_archive(target, payload=payload)
        return builder.validate_archive(target)

    assert any("root" in problem for problem in _built_without("composition_grid.desktop"))
    assert any("__init__.py" in problem for problem in _built_without("composition_grid/__init__.py"))
    assert any("Manual" in problem for problem in _built_without("composition_grid/Manual.html"))


def test_validation_rejects_a_stale_compiled_member(tmp_path):
    """A ``.pyc`` in the archive ships code that was never tested — the builder
    excludes it, and validation flags it if a payload bypassed the filter."""
    repo = _make_fake_repo(tmp_path)
    stale = repo / "composition_grid" / "__pycache__" / "geometry.cpython-310.pyc"
    payload = builder.collect_payload(repo) + [(stale, "composition_grid/__pycache__/g.pyc")]
    output = tmp_path / "stale.zip"
    builder.build_archive(output, payload=payload)

    assert any("build noise" in problem for problem in builder.validate_archive(output))


def test_read_version_matches_the_package_it_ships(tmp_path):
    """The archive name cannot drift from the shipped code's own version: the
    builder parses ``__version__`` as text (it must run in any interpreter,
    without PyQt), so it is pinned against the attribute of the imported
    package."""
    repo = _make_fake_repo(tmp_path)
    from composition_grid import __version__ as shipped_version

    assert builder.read_version(repo) == "9.9.9"
    assert builder.read_version(REPO_ROOT) == shipped_version
