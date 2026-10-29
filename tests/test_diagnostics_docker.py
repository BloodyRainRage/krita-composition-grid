"""Tests for the diagnostics DOCKER (``devtools/grid_diagnostics/docker.py``).

The tool the user is told to click is itself the thing most likely to be broken
in the way that started this whole live-run investigation: ``krita.DockWidget``
IS a ``QDockWidget``, so content installed with a layout on the dock renders as
an empty docker. These tests therefore build the docker over a REAL
``QDockWidget`` — the base the injected-class design exists to allow — and
assert the user-visible facts: a labelled dock, a reachable button, and the
report appearing in the read-only view after the click.

Gated on PyQt like the sibling Qt test modules; the module is loaded from its
file because ``devtools/`` is deliberately not an installed package.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _qt_available() -> bool:
    import importlib

    for major in (6, 5):
        try:
            importlib.import_module(f"PyQt{major}.QtWidgets")
        except ImportError:
            continue
        return True
    return False


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.skipif(
    not _qt_available(), reason="PyQt not installed (uv install if needed)"
)


def _load_docker_module():
    spec = importlib.util.spec_from_file_location(
        "grid_diagnostics_docker_under_test",
        REPO_ROOT / "devtools" / "grid_diagnostics" / "docker.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ExtensionBase:
    def __init__(self, instance=None):
        self._instance = instance


class _DockerFactoryStub:
    def __init__(self, factory_id, area, docker_class):
        self.id = factory_id


class _FactoryBaseStub:
    DockRight = "DockRight"


class _HostShapedInstance:
    """Signatures taken from krita.pyi: addExtension gets ONE argument."""

    def __init__(self):
        self.extensions = []
        self.factories = []

    def addExtension(self, extension):  # noqa: N802 — krita.pyi:364
        self.extensions.append(extension)

    def addDockWidgetFactory(self, factory):  # noqa: N802
        self.factories.append(factory)


@pytest.fixture
def diagnostics_plugin(monkeypatch):
    """Load the devtools plugin over a host-shaped fake krita module."""
    from composition_grid import qt_compat

    fake = types.ModuleType("krita")
    fake.Extension = _ExtensionBase
    fake.DockWidget = qt_compat.QtWidgets.QDockWidget
    fake.DockWidgetFactory = _DockerFactoryStub
    fake.DockWidgetFactoryBase = _FactoryBaseStub
    monkeypatch.setitem(sys.modules, "krita", fake)
    monkeypatch.syspath_prepend(str(REPO_ROOT / "devtools"))
    spec = importlib.util.spec_from_file_location(
        "grid_diagnostics_plugin_under_test",
        REPO_ROOT / "devtools" / "grid_diagnostics" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, _HostShapedInstance()


@pytest.fixture()
def qapp():
    from composition_grid import qt_compat

    return qt_compat.QtWidgets.QApplication.instance() or qt_compat.QtWidgets.QApplication(
        ["test"]
    )


def test_diagnostics_docker_is_labelled_and_fills_its_widget(qapp):
    """A docker the user must FIND: a non-empty Dockers-list label (windowTitle)
    and content installed via ``setWidget`` — the exact two things that made the
    main panel invisible on the first live Krita run."""
    from composition_grid import qt_compat

    module = _load_docker_module()
    docker_cls = module.make_diagnostics_docker_class(
        qt_compat.QtWidgets.QDockWidget, report_runner=lambda: "OK   цепь цела"
    )
    docker = docker_cls()

    assert docker.windowTitle() == "Composition Grid Diagnostics"
    content = docker.widget()
    assert content is not None, "a QDockWidget shows nothing without setWidget()"

    widgets = qt_compat.QtWidgets
    button = content.findChild(widgets.QPushButton, "run_diagnostics")
    report_view = content.findChild(widgets.QPlainTextEdit, "report")
    assert button is not None and report_view is not None
    assert "Нажмите" in report_view.toPlainText(), "the idle view explains itself"

    button.click()  # the user's gesture, not a private call

    assert report_view.toPlainText() == "OK   цепь цела"


def test_report_failure_is_shown_instead_of_leaving_the_view_stale(qapp):
    """A debug tool must never raise into Qt and must never silently keep the
    previous text: a broken report says so in the view."""
    from composition_grid import qt_compat

    def _boom():
        raise RuntimeError("host object went away")

    module = _load_docker_module()
    docker = module.make_diagnostics_docker_class(
        qt_compat.QtWidgets.QDockWidget, report_runner=_boom
    )()
    report_view = docker.widget().findChild(qt_compat.QtWidgets.QPlainTextEdit, "report")
    docker.widget().findChild(qt_compat.QtWidgets.QPushButton, "run_diagnostics").click()

    assert "не удалась" in report_view.toPlainText()
    assert "host object went away" in report_view.toPlainText()


def test_report_view_is_read_only(qapp):
    """The report is evidence, not an editor: typing into it must be impossible."""
    from composition_grid import qt_compat

    module = _load_docker_module()
    docker = module.make_diagnostics_docker_class(
        qt_compat.QtWidgets.QDockWidget, report_runner=lambda: "x"
    )()
    report_view = docker.widget().findChild(qt_compat.QtWidgets.QPlainTextEdit, "report")

    assert report_view.isReadOnly()


def test_docker_overrides_the_abstract_canvas_changed(qapp):
    """libkis declares ``DockWidget.canvasChanged()`` ABSTRACT and PyQt5 enforces
    it at construction: the live host logged
    ``NotImplementedError: DockWidget.canvasChanged() is abstract and must be
    overridden`` for this docker. Any ``krita.DockWidget`` subclass must define
    it, so the requirement is pinned rather than discovered by a user's
    traceback."""
    from composition_grid import qt_compat

    module = _load_docker_module()
    docker = module.make_diagnostics_docker_class(
        qt_compat.QtWidgets.QDockWidget, report_runner=lambda: "x"
    )()

    assert hasattr(docker, "canvasChanged"), "abstract in libkis: must be overridden"
    docker.canvasChanged(object())  # must not raise


def test_register_plugin_adds_the_extension_instance_under_host_signatures(diagnostics_plugin):
    """Strict one-arg fake from krita.pyi:364: the instance must reach the host."""
    module, instance = diagnostics_plugin
    before = len(module.HELD_EXTENSIONS)
    ext = module.register_plugin(instance)
    assert instance.extensions == [ext]
    assert len(module.HELD_EXTENSIONS) == before + 1
    assert module.HELD_EXTENSIONS[before] is ext


def test_register_plugin_docker_lands_when_add_extension_refuses(diagnostics_plugin):
    """A refused extension registration must not lose the docker or the hold."""
    module, instance = diagnostics_plugin

    def refuse(extension):  # noqa: N802
        raise RuntimeError("host refused")

    instance.addExtension = refuse
    ext = module.register_plugin(instance)
    assert instance.extensions == []
    assert [f.id for f in instance.factories] == [_load_docker_module().DOCKER_ID]
    assert ext in module.HELD_EXTENSIONS
