"""The Krita docker registration seam for the composition-grid panel.

Extracted from :mod:`.docker_panel` so that module keeps a single
responsibility (the panel widget itself). This module owns the two wiring
pieces the plugin entry needs to attach the panel to Krita:

* :func:`shared_state_service` — the module-level :class:`GridStateService`
  singleton the docker resolves its service through. Krita's docker factory
  instantiates the docker with NO constructor arguments, so the service it
  must talk to lives here; the plugin ``Extension`` builds its
  ``CanvasContext`` over the SAME instance (one active grid per document
  project-wide).
* :func:`make_panel_docker_class` — builds the concrete ``PanelDocker`` over an
  INJECTED base class. Production passes ``krita.DockWidget`` (the lazy
  ``krita`` import lives in the plugin entry, OUTSIDE the ui package,
  mirroring the sanctioned ``KritaTransport`` lazy-import pattern); headless
  tests pass ``QWidget``. This module never imports ``krita`` — that keeps the
  strict no-krita ui guard satisfied (Qt only via
  :mod:`composition_grid.qt_compat`) and the docker 100% testable without
  Krita.

LIFECYCLE: ``GridStateService.on_change`` has no unsubscribe, so a
:class:`GridPanel` subscribes exactly ONCE per construction. The plugin
extension must construct exactly ONE docker per window — a second docker over
the shared singleton adds a second subscriber that never goes away, and once
its panel is torn down its stale ``_refresh`` raises inside ``_notify`` on
every later mutation (logged, never fatal). Exactly one panel per window is
therefore required.

All UI strings English.
"""

from __future__ import annotations

from typing import Callable

from composition_grid import qt_compat
from composition_grid.model.grid_model import GridStateService
from composition_grid.model.settings_store import JsonGridSettings
from composition_grid.ui.docker_panel import GridPanel

__all__ = ["DOCKER_WINDOW_TITLE", "make_panel_docker_class", "shared_state_service"]

DOCKER_WINDOW_TITLE = "Composition Grid"
"""The docker's window title — libkis shows THIS string in Settings ▸ Dockers
("the window title is how it will appear in the docker list", Krita python
plugin howto). Leaving it unset renders an unlabelled, unusable entry: that is
what the first live Krita run showed."""

_SHARED_SERVICE: GridStateService | None = None


def shared_state_service() -> GridStateService:
    """The module-level :class:`GridStateService` singleton.

    The docker Krita instantiates receives no constructor arguments, so the
    service it must talk to lives here; the plugin ``Extension`` builds its
    ``CanvasContext`` over the SAME instance (one active grid per document
    project-wide)."""
    global _SHARED_SERVICE
    if _SHARED_SERVICE is None:
        _SHARED_SERVICE = GridStateService()
    return _SHARED_SERVICE


def make_panel_docker_class(
    base,
    service: GridStateService | None = None,
    doc_size_provider: Callable[[], "tuple[float, float] | None"] | None = None,
    settings: JsonGridSettings | None = None,
    on_canvas_changed: Callable[[object], None] | None = None,
    on_route: Callable[[], object] | None = None,
    icon_provider: Callable[[str], object] | None = None,
):
    """Build the concrete ``PanelDocker`` class over an INJECTED ``base``.

    Production: ``make_panel_docker_class(krita.DockWidget, ...)`` —
    the lazy ``krita`` import lives THERE (this ui module keeps the strict
    no-krita guard). Tests: pass ``QWidget``. The returned class embeds a
    :class:`GridPanel` (dependencies resolved at instantiation: a ``None``
    service falls back to :func:`shared_state_service`, because the Krita
    docker factory instantiates with no args) and implements the libkis
    ``canvasChanged(canvas)`` virtual by forwarding the
    canvas object to the injected provider callback unchanged (the plugin entry
    routes it to the ``CanvasContext``). ``on_route`` is the same idea for the
    document: the panel asks the entry to route the CURRENT document when it is
    shown or used (see ``CompositionGridExtension.ensure_routed``).
    ``icon_provider`` is the same idea for pixels: the host's theme icons for the
    grid list's eye toggle, reached only from the plugin entry (``name ->
    QIcon | None``, see ``plugin_support.icon_krita``)."""

    class PanelDocker(base):
        """A libkis ``DockWidget`` IS a ``QDockWidget`` (Krita DockWidget class
        reference), so the content goes through ``setWidget()`` and the label in
        Settings ▸ Dockers comes from ``setWindowTitle()``.

        Giving the docker a layout instead leaves it EMPTY: a QDockWidget paints
        only the widget installed via ``setWidget()``; a layout set on the dock
        itself has nowhere to be shown. The first live Krita run showed exactly
        that — an unlabelled docker with nothing in it."""

        def __init__(self, *args):
            # Krita instantiates DockWidgets as ``PanelDocker(parent)``;
            # *args forwards whatever the factory provides to the base.
            super().__init__(*args)
            self.setWindowTitle(DOCKER_WINDOW_TITLE)
            widgets = qt_compat.QtWidgets
            content = widgets.QWidget(self)
            layout = widgets.QVBoxLayout(content)
            layout.setContentsMargins(0, 0, 0, 0)
            self.panel = GridPanel(
                service if service is not None else shared_state_service(),
                doc_size_provider=doc_size_provider,
                settings=settings,
                on_canvas_changed=on_canvas_changed,
                on_route=on_route,
                icon_provider=icon_provider,
                parent=content,
            )
            layout.addWidget(self.panel)
            self.setWidget(content)

        def canvasChanged(self, canvas) -> None:  # noqa: N802 — libkis virtual
            """DockWidget virtual: forward the canvas to the provider wired
            by the registration (module docstring)."""
            self.panel.handle_canvas_changed(canvas)

    PanelDocker.__name__ = "PanelDocker"
    PanelDocker.__qualname__ = "PanelDocker"
    PanelDocker.__doc__ = (
        "Composition Grid docker: GridPanel content over an injected "
        "DockWidget base (see make_panel_docker_class)."
    )
    return PanelDocker
