"""Read the host's view-state GETTERS into a ``ViewMetrics``.

Why this is its own module: ``canvas_context`` owns the lifetime wiring
(bind/unbind/rebind, subscriptions, triggers) and the arithmetic lives in the
pure ``view_metrics``; what sits between them — probing foreign, duck-typed
libkis objects that may be mid-teardown — is a responsibility of its own, and
keeping it here is what stops one file from sprawling past ~520 lines.

Qt-free by construction: no ``qt_compat``, no ``PyQt*``, no ``krita`` import —
every object arrives as a parameter (a libkis wrapper or a test fake, and the
plugin must not be able to tell) and only documented READ getters are called:
``Canvas.zoomLevel/preferredCenter/rotation/mirror``,
``Document.width/height``, ``QWidget.width/height`` and — for the exact
doc->widget transform — ``View.flakeToImageTransform()`` /
``View.flakeToCanvasTransform()`` (:func:`read_host_affine`, which only reads
the ``m11/m12/m21/m22/dx/dy`` NUMBERS off whatever those return; no Qt type is
named here, so ``core`` and ``presentation`` stay Qt-free too). No Document
mutation API is ever touched. ``Document.resolution()`` is not on
the list any more: the live host shows the scale is the zoom itself
(:func:`build_metrics`).

Degradation contract: a getter that is absent or raises becomes a
DEBUG note naming it and the value ``None``; the caller then answers
``view_state=None`` (grid hidden). Nothing here ever raises outward, and nothing
here invents a default — a silently assumed zoom of 1.0 would paint a grid at
the wrong scale, which is worse than painting none. The DEBUG note names the
EXACT getter every attempt; the first starvation of the computed path surfaces
ONE INFO through ``view_metrics.to_view_state``, so the
operator sees the state without a paint storm flooding the log.
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Tuple

from composition_grid.canvas.view_metrics import ViewMetrics
from composition_grid.core.geometry import Affine, Bounds

logger = logging.getLogger("composition_grid")

__all__ = [
    "callable_attr",
    "guarded_call",
    "read_doc_extent",
    "read_host_affine",
    "read_orientation",
    "read_precise_rect",
    "read_viewport_size",
    "build_metrics",
]

_Orientation = Tuple[float, bool, bool]

# The six numbers of a 2x3 matrix as ``QTransform`` exposes them (attribute, not
# method, in both PyQt5 and PyQt6): (x, y) -> (m11*x + m21*y + dx,
# m12*x + m22*y + dy), i.e. exactly ``Affine(m11, m12, m21, m22, dx, dy)``.
_TRANSFORM_FIELDS = ("m11", "m12", "m21", "m22", "dx", "dy")


def callable_attr(obj, name: str) -> "Callable | None":
    """``obj.name`` when *obj* is not ``None`` and the attribute is callable.

    Public because reading foreign host objects safely is this module's job:
    :mod:`composition_grid.canvas.canvas_context` and
    :mod:`composition_grid.canvas.viewport_triggers` share exactly this probe
    instead of each inventing its own."""
    if obj is None:
        return None
    try:
        attr = getattr(obj, name, None)
    except RuntimeError as exc:
        logger.debug(
            "host_view_probe: %s.%s lookup failed: %r", type(obj).__name__, name, exc
        )
        return None
    return attr if callable(attr) else None


def guarded_call(obj, name: str):
    """``obj.name()`` through :func:`callable_attr`; a missing attribute is
    ``None`` and a raising foreign getter is a DEBUG log and ``None`` — the
    "never raise outward" pin, since a libkis wrapper can be mid-teardown."""
    fn = callable_attr(obj, name)
    if fn is None:
        return None
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — host wrapper may be mid-teardown
        logger.debug(
            "host_view_probe: %s.%s() failed: %r", type(obj).__name__, name, exc
        )
        return None


def _get(obj, name: str, purpose: str):
    """``obj.name()`` or ``None``; absent/erroring getters log the NAME, so a
    live report can tell "host has no such getter" from "getter refused"."""
    getter = callable_attr(obj, name)
    if getter is None:
        logger.debug(
            "host_view_probe: getter %r absent (%s) — view state cannot be "
            "computed from this host",
            name,
            purpose,
        )
        return None
    try:
        return getter()
    except Exception as exc:  # noqa: BLE001 — a host wrapper may be mid-teardown
        logger.debug("host_view_probe: getter %r failed (%s): %r", name, purpose, exc)
        return None


def _as_float(value, name: str):
    """Coerce a host number; anything uncoercible is a DEBUG note and ``None``."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        logger.debug("host_view_probe: %r is not a number: %r", name, exc)
        return None


def read_viewport_size(widget) -> "tuple[float, float] | None":
    """The canvas widget's own size in WIDGET px — the viewport ``preferredCenter``
    is measured against. ``None`` when no widget is attached (the degraded
    no-widget path)."""
    if widget is None:
        logger.debug("host_view_probe: no canvas widget attached; no viewport size")
        return None
    width = _get(widget, "width", "viewport width")
    height = _get(widget, "height", "viewport height")
    if width is None or height is None:
        return None
    w = _as_float(width, "viewport width")
    h = _as_float(height, "viewport height")
    if w is None or h is None:
        return None
    return (w, h)


def _mirror_axes(mirror):
    """Interpret a ``Canvas.mirror()`` payload as ``(mirror_x, mirror_y)``.

    The interpretation matrix, in this order, each shape with its own rule — and
    NOTHING in between them is guessed (the "unknown -> assume False" default is
    exactly the silent-assumption class that hid the live failure below):

    * ``bool`` — THE documented shape of this API generation
      (``mirror()`` / ``setMirror(bool)``): the live 5.3.4.1 host answers the
      plain ``False``. One axis of flipping, the horizontal one, so
      ``(value, False)``.
    * ``dict`` with BOTH ``"horizontal"`` and ``"vertical"`` keys — the Krita 6.x
      shape (``mirrorChanged(bool, bool)`` payload split); a dict missing either
      key is NOT interpreted: a missing axis is not a False axis.
    * a 2-sequence of bools — ``(horizontal, vertical)``, the same pair as a
      positional payload. Only ``True``/``False`` items count: ``(1, 0)`` is a
      number pair, not a bool pair, and gets rejected rather than coerced.
    * anything else (including ``None`` from a getter that answered nothing) —
      ``None``: unusable, reported by the caller as a missing input.

    Answers ``None`` when the payload is not interpretable; the caller keeps the
    orientation missing and the grid hidden."""
    if isinstance(mirror, bool):
        return (mirror, False)
    if isinstance(mirror, dict):
        if "horizontal" not in mirror or "vertical" not in mirror:
            return None
        horizontal, vertical = mirror["horizontal"], mirror["vertical"]
        if not isinstance(horizontal, bool) or not isinstance(vertical, bool):
            return None
        return (horizontal, vertical)
    if isinstance(mirror, (tuple, list)) and len(mirror) == 2:
        horizontal, vertical = mirror
        if isinstance(horizontal, bool) and isinstance(vertical, bool):
            return (horizontal, vertical)
    return None


def read_orientation(canvas) -> _Orientation | None:
    """``(rotation_deg, mirror_x, mirror_y)`` from ``Canvas.rotation()`` and
    ``Canvas.mirror()``.

    ``mirror()`` is read through :func:`_mirror_axes` — a plain bool is the
    documented and observed 5.3.4.1 answer (``setMirror(bool)``),
    which this probe used to reject as an "unusable mirror payload" and thereby
    starved a perfectly healthy host into ``view_state=None`` and no paint at
    all. Anything the matrix does not interpret keeps the orientation missing
    (never defaulted) and is logged naming the payload."""
    rotation = _get(canvas, "rotation", "canvas rotation")
    if rotation is None:
        return None
    rotation_deg = _as_float(rotation, "Canvas.rotation()")
    if rotation_deg is None:
        return None
    mirror = _get(canvas, "mirror", "canvas mirror")
    axes = _mirror_axes(mirror)
    if axes is None:
        if callable_attr(canvas, "mirror") is None:
            logger.debug(
                "host_view_probe: Canvas.mirror() absent — orientation is a "
                "missing input, not a default"
            )
        else:
            logger.debug(
                "host_view_probe: mirror() answered the uninterpretable payload "
                "%r (bool / {horizontal, vertical} / 2-sequence of bools are the "
                "accepted shapes) — orientation stays missing",
                mirror,
            )
        return None
    return (rotation_deg, axes[0], axes[1])


def read_precise_rect(canvas):
    """The KRITA 6 override: ``Canvas.documentRectInWidgetPixels()`` as
    ``Bounds``, or ``None`` when the host has no such getter (5.3.x) or answers
    garbage.

    When it exists it is the exact document rect in widget px and WINS over the
    computed one: the computed rect is only a reconstruction of the same
    quantity, so the host's own number is the better source."""
    rect = _get(canvas, "documentRectInWidgetPixels", "precise document rect")
    if rect is None:
        return None
    try:
        return Bounds(
            float(rect.x()),
            float(rect.y()),
            float(rect.width()),
            float(rect.height()),
        )
    except Exception as exc:  # noqa: BLE001 — never trust a foreign payload
        logger.warning("host_view_probe: unusable document rect %r: %r", rect, exc)
        return None


def _matrix_of(transform, name: str) -> "Affine | None":
    """``QTransform`` duck -> :class:`~composition_grid.core.geometry.Affine`.

    Reads the six affine numbers ``m11/m12/m21/m22/dx/dy`` (methods on a real
    ``QTransform``, plain attributes on test fakes); the
    projection row ``m13/m23/m33`` is not read, because both host getters are
    affine by construction. Anything unreadable, non-numeric or non-finite is a
    DEBUG note naming the field and ``None`` — a half-read matrix would paint a
    grid at a wrong place, which is worse than no grid."""
    if transform is None:
        logger.debug("host_view_probe: %s answered nothing — no host affine", name)
        return None
    values = []
    for field in _TRANSFORM_FIELDS:
        try:
            raw = getattr(transform, field)
            # a real QTransform exposes m11()..dy() as METHODS (live 5.3.1)
            if callable(raw):
                raw = raw()
        except Exception as exc:  # noqa: BLE001 — a foreign wrapper may be mid-teardown
            logger.debug("host_view_probe: %s.%s unreadable: %r", name, field, exc)
            return None
        value = _as_float(raw, f"{name}.{field}")
        if value is None or not math.isfinite(value):
            logger.debug("host_view_probe: %s.%s is not a finite number: %r", name, field, raw)
            return None
        values.append(value)
    return Affine(*values)


def read_host_affine(view) -> "Affine | None":
    """The host's EXACT doc px -> canvas-widget px transform, or ``None``.

    Krita 5.3's libkis ``View`` answers ``flakeToImageTransform()`` and
    ``flakeToCanvasTransform()`` (both ``QTransform``), and composing them is
    the only placement that is right under ROTATION: the reconstruction in
    :mod:`composition_grid.canvas.view_metrics` / ``doc_view_mapper`` rotates
    about the doc centre with the opposite sign and misses the real pivot by
    hundreds of widget px (see the measured table in the mapper's module
    docstring). The widget it maps into is the very viewport the overlay covers
    — the unrotated cases match the computed rect exactly, pixel for pixel.

    ``image -> flake -> canvas``, i.e. the inverse of ``flakeToImageTransform``
    applied first, then ``flakeToCanvasTransform``::

        i2w = flakeToImageTransform().inverted() * flakeToCanvasTransform()

    The inversion and the composition happen HERE, in ``Affine`` arithmetic,
    rather than through the foreign ``QTransform.inverted()``/``*`` (whose
    tuple-returning first form is a binding detail this probe must not depend
    on). Never raises: an absent getter, a getter that throws, a matrix with a
    non-finite or unreadable entry, or a singular (non-invertible /
    zero-area) matrix all answer ``None`` with a DEBUG note naming what was
    missing, and the caller falls back to the computed path."""
    if view is None:
        logger.debug(
            "host_view_probe: no View attached — no host affine, the computed "
            "fallback is the only source"
        )
        return None
    image_to_flake = _matrix_of(
        _get(view, "flakeToImageTransform", "host image->flake transform"),
        "View.flakeToImageTransform()",
    )
    if image_to_flake is None:
        return None
    flake_to_canvas = _matrix_of(
        _get(view, "flakeToCanvasTransform", "host flake->canvas transform"),
        "View.flakeToCanvasTransform()",
    )
    if flake_to_canvas is None:
        return None
    if image_to_flake.a * image_to_flake.d - image_to_flake.b * image_to_flake.c == 0.0:
        logger.debug(
            "host_view_probe: View.flakeToImageTransform() is singular — no host affine"
        )
        return None
    affine = image_to_flake.inverted().composed(flake_to_canvas)
    entries = (affine.a, affine.b, affine.c, affine.d, affine.e, affine.f)
    det = affine.a * affine.d - affine.b * affine.c
    if det == 0.0 or not all(math.isfinite(value) for value in entries):
        logger.debug(
            "host_view_probe: the composed host affine is degenerate (%r) — ignored",
            affine,
        )
        return None
    return affine


def _read_center(canvas) -> "tuple[float, float] | None":
    """``Canvas.preferredCenter()`` — a ``QPointF`` duck, read through ``.x()``
    and ``.y()`` (the 5.3 host has no rect getter, so this is what places the
    document in the viewport)."""
    point = _get(canvas, "preferredCenter", "canvas preferred centre")
    if point is None:
        return None
    x = _as_float(_get(point, "x", "preferredCenter x"), "preferredCenter().x()")
    y = _as_float(_get(point, "y", "preferredCenter y"), "preferredCenter().y()")
    if x is None or y is None:
        return None
    return (x, y)


def read_doc_extent(document) -> "tuple[float, float] | None":
    """``(Document.width(), Document.height())`` LIVE — the rule that EVERY
    input of one frame comes from the same read.

    The extent used to be captured at bind/refresh only, so an
    ``Image ▸ Canvas Size`` repaint (announced via ``rangeChanged``) recomputed
    the rect with the OLD extent and the mapper's scale denominator was wrong.
    ``None`` (with a DEBUG note naming the getter) when the document is absent,
    refuses either getter or answers non-positive garbage — the caller then
    starves the frame instead of mixing a live rect with a stale extent.
    Reads only, never a mutation API."""
    if document is None:
        logger.debug("host_view_probe: no document attached; live extent unreadable")
        return None
    width = _as_float(_get(document, "width", "document width"), "Document.width()")
    height = _as_float(_get(document, "height", "document height"), "Document.height()")
    if width is None or height is None:
        return None
    if width <= 0.0 or height <= 0.0:
        logger.debug(
            "host_view_probe: degenerate live document extent %gx%g ignored", width, height
        )
        return None
    return (width, height)


def build_metrics(
    widget,
    canvas,
    doc_w: float,
    doc_h: float,
    orientation: _Orientation | None,
) -> ViewMetrics | None:
    """The full metric set from the CURRENT host answers — every field read on
    demand, so a zoom/pan/resize that arrived with no signal is still reflected.

    ``None`` as soon as one required input is unanswerable; the caller then
    degrades to no ViewState (grid hidden) instead of painting a guess.

    NO ``document`` argument, on purpose: the document
    extent arrives as ``doc_w``/``doc_h``, read LIVE by
    :func:`read_doc_extent`, and nothing else about the document is an input of
    the scale. ``Document.resolution()`` in particular is NOT read — the live
    5.3.4.1 host proves the scale is ``Canvas.zoomLevel()`` directly, its two
    scrollbar values (-212/-284) agreeing with ``s = zoom`` on BOTH axes (see
    :func:`composition_grid.canvas.view_metrics.scale_per_doc_px` for the
    numbers). Reading the dpi anyway kept a needless starvation source on the
    critical path: a host that refused ``resolution()`` hid the grid for a value
    that never entered the arithmetic. ``xRes()``/``yRes()`` are not read either;
    :mod:`composition_grid.diagnostics` prints all three as informational data
    and cross-checks the resulting rect against the host scrollbars."""
    if orientation is None:
        return None
    viewport = read_viewport_size(widget)
    if viewport is None:
        return None
    zoom = _as_float(_get(canvas, "zoomLevel", "canvas zoom"), "Canvas.zoomLevel()")
    center = _read_center(canvas)
    if zoom is None or center is None:
        return None
    rotation_deg, mirror_x, mirror_y = orientation
    return ViewMetrics(
        viewport_w=viewport[0],
        viewport_h=viewport[1],
        doc_w=float(doc_w),
        doc_h=float(doc_h),
        zoom=zoom,
        center_x=center[0],
        center_y=center[1],
        rotation_deg=rotation_deg,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
    )
