"""The thin plugin-side half of in-document grid persistence.

The STATE ITSELF is a JSON document produced and validated by the pure codec
:mod:`composition_grid.model.grid_annotation_codec`; this module only moves it
through libkis. It is the single place where the grid touches a Document
MUTATION API, and that exception is deliberate and bounded (README): the one call is
``Document.setAnnotation(GRID_ANNOTATION_TYPE, ...)`` —
an annotation is metadata Krita stores inside the ``.kra`` file, never a layer,
never a pixel, never part of an export.

Live-verified host facts (Krita 5.3.1) this module relies on:

* ``Document.setAnnotation(type, description, data)``,
  ``Document.annotation(type) -> QByteArray``, ``Document.annotationTypes()``;
* an annotation set before ``saveAs(".kra")`` comes back byte-identical when
  the file is reopened;
* ``Document.modified()`` is ``False`` right after opening and
  ``setAnnotation()`` flips it to ``True`` — which is why RESTORING must never
  write: reopening a file must not silently dirty it.

Two rules therefore shape this module:

1. **Never raise into the host.** Every getter/setter call is guarded
   (:func:`composition_grid.plugin_support.guarded_call` / a logged ``except``);
   a document that cannot answer is an empty read and a dropped write, each
   with a log line, never an exception reaching document activation or a
   mutation notification.
2. **Never write during restore.** :func:`writes_suppressed` is the window the
   entry opens while it creates and restores a document's model; the save
   subscriber consults it, so the first activation of a freshly opened file
   produces no annotation at all. The window nests (a counter, not a bool)
   because a batched restore can run inside another suppressed section.
"""

from __future__ import annotations

import contextlib
import logging

from composition_grid.model.grid_annotation_codec import (
    CODEC_VERSION,
    decode,
    encode_state,
)
from composition_grid.plugin_support import guarded_call, krita_module

logger = logging.getLogger("composition_grid")

__all__ = [
    "GRID_ANNOTATION_DESCRIPTION",
    "GRID_ANNOTATION_TYPE",
    "STATE_CODEC_VERSION",
    "read_grid_annotation",
    "suppressed_writes",
    "write_grid_annotation",
    "writes_suppressed",
]

GRID_ANNOTATION_TYPE = "composition_grid"
"""The annotation type key the state document lives under (pinned: renaming it
orphans the state of every file saved with an older build)."""

GRID_ANNOTATION_DESCRIPTION = "Composition Grid state"
"""The human-readable description shown with the annotation type."""

STATE_CODEC_VERSION = CODEC_VERSION
"""Re-exported for diagnostics: the payload version this build reads and writes."""

_pending_writes = [0]


def writes_suppressed() -> bool:
    """``True`` inside a :func:`suppressed_writes` window (restore in progress)."""
    return _pending_writes[0] > 0


@contextlib.contextmanager
def suppressed_writes():
    """Suppress annotation writes for everything nested inside (rule 2)."""
    _pending_writes[0] += 1
    try:
        yield
    finally:
        _pending_writes[0] -= 1


def read_grid_annotation(document):
    """The document's stored state as a validated dict, or ``None``.

    ``None`` covers every "no usable state here" case — no document, no such
    annotation, empty or corrupt bytes, an unknown version — and each is
    logged at the level its diagnosis needs (DEBUG for simply not having one,
    WARNING for a payload that is there but unreadable). A version-1 payload is
    not a failure: the codec migrates it to the current container shape. The
    caller treats ``None`` as "this document has no stored state, seed from the
    global last-used settings".
    """
    if document is None:
        return None
    try:
        raw = document.annotation(GRID_ANNOTATION_TYPE)
    except Exception as exc:  # noqa: BLE001 — a host wrapper may be mid-teardown
        logger.warning(
            "document_state: reading the %r annotation failed: %r; falling back",
            GRID_ANNOTATION_TYPE,
            exc,
        )
        return None
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw)
    else:  # a binding that answers something else (QByteArray variants)
        try:
            raw = bytes(raw)
        except Exception:  # noqa: BLE001
            raw = guarded_call(raw, "data")
            if raw is None:
                logger.warning(
                    "document_state: the %r annotation is an unreadable %r; falling back",
                    GRID_ANNOTATION_TYPE,
                    type(raw).__name__,
                )
                return None
    if not raw:
        logger.debug(
            "document_state: no %r annotation on this document; using the global defaults",
            GRID_ANNOTATION_TYPE,
        )
        return None
    state = decode(raw)
    if state is None:
        logger.warning(
            "document_state: the %r annotation is not a readable version-%d state "
            "document; falling back to the global defaults",
            GRID_ANNOTATION_TYPE,
            STATE_CODEC_VERSION,
        )
    return state


def write_grid_annotation(document, models, selected_grid_id=None) -> bool:
    """Store a document's whole grid list into its annotation; ``True`` when written.

    ``models`` is the ordered grid list (any iterable of
    :class:`~composition_grid.model.grid_model.GridModel`) and
    ``selected_grid_id`` the id the container marks as selected — an empty list
    is a real, storable state ("this document has no grids"), so it is written
    like any other. Called for a USER mutation of that document only (never
    during restore — rule 2 is enforced by the caller's window and re-checked
    here as the invariant of this module). ``setAnnotation`` marks the document
    modified: that is the intended, visible consequence of the artist having
    changed the grids. A raising host is logged and answered ``False``.
    """
    if document is None:
        logger.debug("document_state: no document to write the grid annotation into")
        return False
    if writes_suppressed():
        return False
    payload = encode_state(list(models), selected_grid_id)
    try:
        data = krita_module().QByteArray(payload)
    except Exception as exc:  # noqa: BLE001 — binding shape differences are normal
        logger.debug(
            "document_state: QByteArray(%d bytes) unavailable (%r); passing the raw bytes",
            len(payload),
            exc,
        )
        data = payload
    try:
        document.setAnnotation(GRID_ANNOTATION_TYPE, GRID_ANNOTATION_DESCRIPTION, data)
    except Exception as exc:  # noqa: BLE001 — a save must never break the UI
        logger.warning(
            "document_state: setAnnotation failed (%r); the grid state stays unsaved "
            "in this document",
            exc,
        )
        return False
    return True
