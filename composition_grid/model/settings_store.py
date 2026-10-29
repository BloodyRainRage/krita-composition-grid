"""Settings store: JSON persistence of the last used grid values.

Model layer: no Qt, no krita at module level — stdlib plus the plugin's own
model types only. The ``krita`` import is *lazy*: :class:`KritaTransport`
imports it inside :meth:`KritaTransport._app`, so importing this module (and
therefore running the whole test suite) never requires a running Krita.

This store keeps the "last used settings for NEW grids" copy: the state of the
ONE selected grid, as one JSON document under the single ``active_grid`` key of
the transport group, in EXACTLY the schema of
:mod:`composition_grid.model.grid_annotation_codec` — the same codec the
in-document annotation uses, so the field list exists once. It is consulted by
nothing but :meth:`GridStateService.add_grid`, which seeds a new grid from it.
``load()`` never raises: a missing key is the normal first run (silent), while
corrupt JSON or a wrong-shape document is a *handled* failure (logged WARNING,
``None`` reported) — the caller, not this module, applies the seed. The
identity/presence fields of the payload (``grid_id``, ``label``, ``visible``)
travel with it because the codec is shared, but a seed only ever contributes
type, parameters and style. ``edit_session`` is transient everywhere and never
stored.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Protocol

from composition_grid.model.grid_annotation_codec import decode_seed, encode_model
from composition_grid.model.grid_model import GridModel

logger = logging.getLogger("composition_grid")

# Pinned single key inside the transport group; documented, not configurable.
_ACTIVE_KEY = "active_grid"
_DEFAULT_GROUP = "composition_grid"

#: The fields a seed answers, and the shape an unreadable/absent store reports.
_SEED_FIELDS = ("grid_type_id", "params", "style")


def _empty_state() -> Dict[str, Any]:
    """A fresh copy of the "no usable seed stored" report."""
    return {"grid_type_id": None, "params": {}, "style": None}


class SettingsTransport(Protocol):
    """Minimal key/value string store the settings JSON moves through."""

    def read(self, group: str, key: str) -> str | None:
        """The stored string for ``(group, key)``, or ``None`` when unset."""
        ...

    def write(self, group: str, key: str, value: str) -> None:
        """Store ``value`` under ``(group, key)``, replacing any previous one."""
        ...


class JsonGridSettings:
    """Serialize/deserialize the last used grid values as one JSON document."""

    def __init__(
        self, transport: SettingsTransport, group: str = _DEFAULT_GROUP
    ) -> None:
        self._transport = transport
        self._group = group

    # -------------------------------------------------------------- save

    def save(self, model: GridModel) -> None:
        """Store ``model``'s persistent fields as one JSON string (overwrites).

        The payload is the shared codec's (``dataclasses.asdict`` for the
        frozen style/transform, JSON-safety coercion for ``params``,
        ``sort_keys=True`` for byte-stable output of equal states)."""
        self._transport.write(
            self._group, _ACTIVE_KEY, encode_model(model).decode("utf-8")
        )

    # -------------------------------------------------------------- load

    def load(self) -> Dict[str, Any]:
        """The stored seed (type + params + style), or the empty report.

        Missing key (first run): silent defaults. A payload the codec does not
        trust: logged WARNING, defaults — split into the "not even JSON" and
        the "valid JSON, wrong shape/version" wordings because the operator
        reading the log has to know which half of the store broke.
        """
        raw = self._transport.read(self._group, _ACTIVE_KEY)
        if raw is None:
            return _empty_state()
        seed = decode_seed(raw)
        if seed is None:
            try:
                json.loads(raw)
            except ValueError:
                logger.warning(
                    "corrupt JSON under %s=%r; recovering to per-field defaults",
                    _ACTIVE_KEY,
                    raw,
                )
            else:
                logger.warning(
                    "stored %s state has the wrong shape or version (%r); "
                    "recovering to per-field defaults",
                    _ACTIVE_KEY,
                    raw,
                )
            return _empty_state()
        return seed


class KritaTransport:
    """:class:`SettingsTransport` backed by the running Krita instance.

    Never exercised by the test suite (it would need a live Krita). The
    ``krita`` import is lazy — inside :meth:`_app` — so this module can be
    imported standalone; the class is only constructed inside Krita.
    """

    def _app(self):
        """The running ``Krita`` instance; deferred import (no krita at top)."""
        from krita import Krita

        return Krita.instance()

    def read(self, group: str, key: str) -> str | None:
        """``readSetting`` with the ``""`` sentinel: unset reports ``None``.

        Pinned: ``Krita.readSetting`` returns the given default string when
        the setting is unset — ``""`` is that sentinel here, and a ``""``
        payload is impossible (payloads are JSON documents).
        """
        value = self._app().readSetting(group, key, "")
        return None if value == "" else value

    def write(self, group: str, key: str, value: str) -> None:
        """Forward to ``Krita.writeSetting``; no caching."""
        self._app().writeSetting(group, key, value)
