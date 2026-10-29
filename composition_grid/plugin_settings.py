"""The document-state restore seam: putting a stored container back.

Grid state lives in TWO places, and this module owns the policy of
reading the one that belongs to the document being routed:

* the document's own ``composition_grid`` annotation
  (:mod:`composition_grid.plugin_document_state`) — the full per-document
  container (every grid, in order, plus the selection), installed in one step
  through :meth:`GridStateService.install_grids`, and which WINS whenever it is
  readable;
* the kritarc group ``composition_grid``
  (:mod:`composition_grid.model.settings_store`) — the "last used settings"
  copy of type + parameters + style, which NEVER restores anything here. It is
  read by :meth:`GridStateService.add_grid` alone (the plugin injects it as the
  service's seed provider), because a document without a stored annotation opens
  WITHOUT grids.

Why the stored global ``transform`` plays no part even as a seed: the global copy
keeps the doc-centered default element for a grid with no state of its own (global
transform persistence is a non-goal), and a transform saved from a
differently sized document would otherwise land in the wrong place. A
document's OWN annotation, by contrast, describes THIS document — so every
grid's element comes back with it.

Per-type parameter memory is a non-goal too: ``GridStateService.set_type``
REPLACES the parameters with the new type's schema defaults (Manual.html says
exactly that), so a seed only ever contributes the parameters of the ONE stored
type.

It imports neither ``krita`` nor a Qt binding: the stores are injected, and the
restore is a service command (:meth:`install_grids`) for the same reason — this
module never writes a model's fields itself, and each field keeps its single
owner so the docker/repaint subscribers see every change.

Every failure is logged and skipped: a corrupt entry must never break document
activation, and no exception is allowed to leave this module —
there is no empty ``except`` here.
"""

from __future__ import annotations

import logging

from composition_grid.model.grid_model import GridStateService
from composition_grid.plugin_document_state import read_grid_annotation

logger = logging.getLogger("composition_grid")

__all__ = ["restore_document_state"]


def restore_document_state(
    service: GridStateService, document, doc_id: str | None = None
) -> bool:
    """Restore a freshly routed document from its own annotation.

    The document wins whenever ``composition_grid`` carries a readable state
    document: every grid comes back with its id, label, type, parameters,
    style, element and visibility, and the selection with them — the artist's
    grids are exactly where they left them (a version-1 payload arrives here as
    a one-grid container, migrated by the codec). Reading is a pure
    ``annotation()`` call, and the whole restore runs inside
    :func:`composition_grid.plugin_document_state.suppressed_writes`, so
    restoring can never mark the freshly opened file modified.

    Answers ``True`` when the file owned the container (a readable annotation,
    grids or not) — the caller remembers that as "this document has state of its
    own, so later mutations are worth writing back" — and ``False`` for a
    document with no annotation, which simply opens without grids."""
    state = read_grid_annotation(document)
    if state is None:
        logger.debug(
            "plugin_settings: this document stores no grid state; it opens without a grid"
        )
        return False
    target = doc_id if doc_id is not None else service.active_doc_id or ""
    if not state["grids"]:
        logger.info("plugin_settings: this document stores no grids; it opens without one")
        return True
    installed = service.install_grids(target, state["grids"], state.get("selected"))
    if not installed:
        logger.warning(
            "plugin_settings: the stored annotation carried grids but none could be "
            "installed; the document stays as it is"
        )
    return True
