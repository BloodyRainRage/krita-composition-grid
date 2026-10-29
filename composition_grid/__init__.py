"""``composition_grid`` — the pykrita plugin package (Krita 5.3+).

Entry semantics: Krita's Python-plugin loader
imports THIS package when ``composition_grid.desktop`` is installed, and the
package registers the extension plus the docker (the ritual lives in
:func:`composition_grid.plugin_composition_grid.register_plugin`).

Headless contract, why the import below is GUARDED and not unconditional: the
test suite (``uv run pytest``) and any plain ``import composition_grid`` run
outside Krita, where the libkis ``krita`` module does not exist. The rule of
this repository is that pure tests always run and Qt-dependent ones skip — so
package import must have NO side effects and must not raise without libkis.
:data:`_krita_available` is the honest predicate for that: it *looks up* the
``krita`` module (``importlib.util.find_spec`` never executes it) and the entry
import + registration only happen when Krita really provides it. Inside Krita the
``krita`` module is still imported only LAZILY by the entry itself
(:mod:`composition_grid.plugin_support`), never at the top of this file, so no
module of the package carries a top-level libkis import.
"""

from __future__ import annotations

import importlib.util

__version__ = "0.1.0"


def _krita_available() -> bool:
    """``True`` when the libkis ``krita`` module is reachable, i.e. this package
    is being imported BY Krita. Never imports it (only resolves its spec).

    A ``ValueError``/``ImportError`` from the lookup means a half-broken
    environment: treated as "not inside Krita" (degradation, logged, never a
    raise out of a plain ``import composition_grid``)."""
    try:
        return importlib.util.find_spec("krita") is not None
    except (ImportError, ValueError) as exc:
        import logging

        logging.getLogger("composition_grid").info(
            "composition_grid: krita module lookup failed (%r); assuming no "
            "Krita host, nothing is registered",
            exc,
        )
        return False


if _krita_available():  # pragma: no cover - requires a running Krita
    # Registration side effect: the entry module carries
    # the Extension class and the action/docker wiring; importing it resolves
    # the ``krita.Extension`` base lazily, then the ritual registers both.
    from composition_grid.plugin_composition_grid import *  # noqa: F401,F403

    register_plugin()  # noqa: F405
