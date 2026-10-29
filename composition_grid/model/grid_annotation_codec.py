"""The grid-state JSON codec: ONE serialization of a document's grids.

Model layer: no Qt, no krita — stdlib plus the plugin's own pure core only.

The plugin persists grid state in two places, and both must agree byte-for-byte
on the schema or a document's saved state could not be read back:

* the per-document ``composition_grid`` annotation inside the ``.kra`` file
  (written by :mod:`composition_grid.plugin_document_state`, read back on the
  first activation) — a CONTAINER of every grid the document owns;
* the kritarc "last used settings" store
  (:class:`composition_grid.model.settings_store.JsonGridSettings`) — the state
  of the ONE selected grid, used only to seed a newly added grid.

Version 2 payload (the annotation)::

    {"version": 2, "selected": <grid id|null>,
     "grids": [{"grid_id": str, "label": str, "grid_type_id": str,
                "params": {}, "style": {}, "transform": {}, "visible": bool}, ...]}

``style``/``transform`` are frozen dataclasses serialized via
``dataclasses.asdict``; ``params`` pass through a JSON-safety coercion;
``sort_keys=True`` gives byte-stable output for equal states. ``edit_session``
is deliberately transient — it is never stored anywhere.

:func:`decode` is the trust gate AND the migration point: it answers a
version-2-shaped container for a version-1 payload too (``present: true`` ->
one grid labelled ``"1"`` with no stored id, ``present: false`` -> no grids),
because a document saved by an older build must open with the grid it had. A
v1 migration leaves ``grid_id`` empty — fresh ids are the service's job, this
layer only moves JSON.

``decode`` never raises and never logs: the callers own the policy — the store
falls back to defaults, the annotation reader falls back to the global restore
— and both log in their own words.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, List, Optional

from composition_grid.model.grid_model import GridModel

CODEC_VERSION = 2
"""The payload version this build writes; :func:`decode` also reads version 1."""

_MIN_SUPPORTED_VERSION = 1

_GRID_FIELDS = (
    "version",
    "grid_id",
    "label",
    "grid_type_id",
    "params",
    "style",
    "transform",
    "visible",
)

#: Defaults for the fields a payload shape may legitimately omit (the trust
#: gate has already rejected anything else).
_FIELD_DEFAULTS = {"grid_id": "", "label": "", "visible": True}


def _json_safe(value: Any) -> Any:
    """Return ``value`` unchanged when JSON-serializable, else its ``str()``.

    The parameter values of the registered grid types are booleans, numbers
    and strings (JSON-native); this is just a belt for any exotic value a
    future type might carry.
    """
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)
    return value


def grid_entry_of(model: GridModel) -> dict:
    """One grid's persistent fields as a plain JSON-ready dict."""
    return {
        "grid_id": model.grid_id,
        "label": model.label,
        "grid_type_id": model.grid_type_id,
        "params": {name: _json_safe(value) for name, value in model.params.items()},
        "style": asdict(model.style),
        "transform": asdict(model.transform),
        "visible": bool(model.visible),
    }


def payload_of(model: GridModel) -> dict:
    """The state document of a SINGLE grid (the kritarc "last used" shape).

    A version-2 entry carrying the version key, so one decoder reads both
    stores; the grid-level identity fields ride along unchanged.
    """
    entry = grid_entry_of(model)
    entry["version"] = CODEC_VERSION
    return entry


def encode_model(model: GridModel) -> bytes:
    """A single grid's state document as UTF-8 JSON bytes (settings-ready)."""
    return json.dumps(payload_of(model), sort_keys=True).encode("utf-8")


def encode_state(
    models: "list[GridModel] | tuple[GridModel, ...]", selected_grid_id: Optional[str]
) -> bytes:
    """A document's whole grid list as its version-2 annotation payload."""
    return json.dumps(
        {
            "version": CODEC_VERSION,
            "selected": selected_grid_id,
            "grids": [grid_entry_of(model) for model in models],
        },
        sort_keys=True,
    ).encode("utf-8")


def _parsed(raw: Any) -> Optional[Any]:
    """``raw`` (bytes/str/dict) as a parsed JSON value, or ``None``."""
    data = raw
    if isinstance(data, (bytes, bytearray)):
        if not data:
            return None
        try:
            data = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
    elif isinstance(data, str):
        if not data:
            return None
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return data


def _grid_fields_valid(entry: dict) -> bool:
    """The per-grid trust gate shared by both payload versions."""
    if not isinstance(entry.get("grid_type_id"), str):
        return False
    if not isinstance(entry.get("visible"), bool):
        return False
    if not isinstance(entry.get("params"), dict):
        return False
    if not isinstance(entry.get("style"), dict):
        return False
    if not isinstance(entry.get("transform"), dict):
        return False
    for field in ("grid_id", "label"):
        value = entry.get(field)
        if value is not None and not isinstance(value, str):
            return False
    return True


def _entry_of(entry: dict) -> dict:
    """The validated subset of one grid document, with identity defaults.

    The field list is :data:`_GRID_FIELDS` (minus the version key, which
    belongs to the document, not the grid), so the schema exists once."""
    return {
        field: (
            entry[field]
            if field in entry
            else _FIELD_DEFAULTS.get(field, "")
        )
        for field in _GRID_FIELDS
        if field != "version"
    }


def _migrate_v1(data: dict) -> Optional[dict]:
    """A version-1 single-grid document as a version-2 container.

    ``present: true`` -> one grid (label ``"1"``, no id: the service mints a
    fresh one); ``present: false`` -> the empty list, because "removed" is no
    longer a state a grid keeps. An unreadable v1 payload answers ``None``
    like any other foreign document.
    """
    present = data.get("present")
    visible = data.get("visible")
    if not isinstance(present, bool) or not isinstance(visible, bool):
        return None
    if not isinstance(data.get("grid_type_id"), str):
        return None
    if not isinstance(data.get("params"), dict):
        return None
    if not isinstance(data.get("style"), dict) or not isinstance(data.get("transform"), dict):
        return None
    if not present:
        return {"version": CODEC_VERSION, "selected": None, "grids": []}
    grid = {
        "grid_id": "",
        "label": "1",
        "grid_type_id": data["grid_type_id"],
        "params": data["params"],
        "style": data["style"],
        "transform": data["transform"],
        "visible": visible,
    }
    return {
        "version": CODEC_VERSION,
        "selected": None,
        "grids": [grid],
    }


def _single_grid_container(data: dict) -> dict:
    """A bare single-grid document (the kritarc payload) as a container.

    ``decode`` answers a container for EVERY readable shape, so the annotation
    reader and the settings store share one code path. A stored id/label rides
    along when the payload carries one; a seed written without identity fields
    describes values only, so the service mints them."""
    entry = _entry_of({"grid_id": "", "label": "", **data})
    return {
        "version": CODEC_VERSION,
        "selected": entry["grid_id"] or None,
        "grids": [entry],
    }


def decode(raw: Any) -> Optional[dict]:
    """Validate a stored state document; a container dict, or ``None``.

    Accepts ``bytes``/``bytearray`` (an annotation payload), ``str`` and an
    already-parsed ``dict`` (a transport string decoded elsewhere), in three
    shapes:

    * version 2 — ``{"version": 2, "selected": id|null, "grids": [...]}``;
    * version 1 — migrated by :func:`_migrate_v1`;
    * a bare single-grid document (the kritarc "last used" payload, version 1
      or 2 with no ``grids`` list) — wrapped as a one-grid container.

    Every rejectable shape answers ``None``: a host that cannot answer the
    getter, an empty annotation, corrupt JSON, a list or scalar, a version
    outside 1..2, a non-string type id, a non-bool ``visible``, missing or
    non-object ``style``/``transform``, a non-dict ``params``, a ``grids``
    value that is not a list, a grid entry that is not a mapping, and a
    non-string ``selected``."""
    data = _parsed(raw)
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    if version > CODEC_VERSION or version < _MIN_SUPPORTED_VERSION:
        return None
    if "grids" in data:
        if version != CODEC_VERSION:
            return _migrate_v1(data)
        return _decode_v2(data)
    if version == 1:
        return _migrate_v1(data)
    if not _grid_fields_valid(data):
        return None
    return _single_grid_container(data)


def _decode_v2(data: dict) -> Optional[dict]:
    """The version-2 container path of :func:`decode`."""
    grids = data.get("grids")
    if not isinstance(grids, list):
        return None
    entries: List[dict] = []
    for item in grids:
        if not isinstance(item, dict) or not _grid_fields_valid(item):
            return None
        entries.append(_entry_of(item))
    selected = data.get("selected")
    if selected is not None and not isinstance(selected, str):
        return None
    return {"version": CODEC_VERSION, "selected": selected, "grids": entries}


def decode_seed(raw: Any) -> Optional[dict]:
    """The seed values (``grid_type_id``/``params``/``style``) of a stored state.

    The settings store's read shape: the "last used" document describes the
    values a NEW grid starts from, so the container's first grid (the only one
    it stores) answers here. ``None`` for anything :func:`decode` rejects and
    for an empty container (a document stored with no grids seeds nothing).
    """
    container = decode(raw)
    if container is None:
        return None
    grids = container["grids"]
    if not grids:
        return None
    selected = container.get("selected")
    for entry in grids:
        if entry["grid_id"] == selected:
            return {field: entry[field] for field in ("grid_type_id", "params", "style")}
    entry = grids[0]
    return {field: entry[field] for field in ("grid_type_id", "params", "style")}
