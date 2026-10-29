"""Edit-session drag controller for the transformable grid element.

Pure logic: no Qt, no krita — stdlib plus the plugin's own pure modules
(``core``, ``canvas.doc_view_mapper``, ``presentation.handle_layout``,
``model.grid_model``).

The controller owns no state besides the in-flight drag snapshot. All
canvas state arrives through the two injected providers (KISS): the
current :class:`~composition_grid.canvas.doc_view_mapper.ViewState` and
the current :class:`~composition_grid.model.grid_model.GridModel`. When
either provider yields ``None`` (no active view / no active document)
every entry point degrades to a safe no-op (``False`), never an
exception.

Conventions (fixed; the rationale is given inline):

* Coordinate spaces: document space and widget pixels are screen-style,
  y growing down (:mod:`composition_grid.core.geometry`,
  :mod:`composition_grid.canvas.doc_view_mapper`).
* **View freeze**: the widget→doc affine is snapshotted at
  :meth:`TransformController.begin_drag` and every step works through it in doc
  space, so mid-drag zoom/pan/rotation changes cannot warp an in-flight drag; a
  refused begin leaves NO drag behind. The affine carries the whole mapping, so
  no separate view scale is snapshotted (``view_scale`` is only used live, by
  :meth:`probe` for the hit-test tolerance clamp).
* **Rotate pivot**: the rotation delta is measured about
  the ELEMENT CENTER — the rotate handle merely rides the local up axis
  beyond the top edge, but the pivot is ``(cx, cy)`` of the start
  snapshot. ``delta_deg = wrap(atan2(drag) − atan2(start))`` in y-down
  doc space, so a CLOCKWISE screen drag of the rotate handle yields a
  POSITIVE ``delta_deg``, aligned with ``TransformState`` /
  ``element_matrix`` (``Affine.rotation_deg`` is applied directly in
  y-down space: positive ``rotation_deg`` reads as clockwise on screen).
  The per-step delta is wrapped into ``(-180, 180]`` so crossing the
  atan2 discontinuity does not flip by 360°.
* **Ratio override**: the
  effective lock is ``snapshot.ratio_locked XOR shift_held`` (at
  ``drag_to`` time — the live Shift state wins), passed as
  ``ratio_override`` to :func:`apply_corner_delta`, whose ``None``
  fallback would otherwise use the state flag verbatim.
* **Mutations write through on every drag_to**: each step recomputes the
  new state from the BEGIN snapshot (not from the current model value),
  so jitter within one drag cannot accumulate. The update is assigned to
  ``model.transform`` / ``model.params`` and ``model.emit()`` notifies the
  model's subscribers. The owning :class:`GridStateService` subscribes its own
  ``_notify`` to every model it creates, so the emit forwards into the service —
  the single notification hub that drives the overlay repaint, the docker
  field-refresh and persistence together. The grid therefore live-updates
  during the drag with no extra buffering, and the docker/persistence follow the
  same one path (no per-model repaint bridge).
* **vp0** (V1: exactly one vanishing point; see handle_layout): the
  handle exists only when the active grid type's schema defines BOTH the
  ``vp_x`` and the ``vp_y`` parameter (i.e. ``perspective_grid``) — see
  :meth:`TransformController._grid_has_vp`. A vp drag on any other
  grid type is refused at ``begin_drag`` and re-checked at ``drag_to``:
  both return ``False`` (safe no-op-with-reason). The begin cursor point and the
  current cursor point are both mapped into element space by inverting the start
  snapshot's ``element_matrix``, and the resulting LOCAL delta is added to the
  ``vp_x`` / ``vp_y`` pair snapshotted at begin (never to the absolute cursor
  position, so an off-center grab keeps its offset instead of jumping the
  handle); the two params are then clamped to the schema's
  ``vp_x`` / ``vp_y`` minimum/maximum (0..1), matching what the docker's
  set-param coercion would store.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from composition_grid.canvas.doc_view_mapper import (
    ViewState,
    view_scale,
    widget_to_doc_affine,
)
from composition_grid.core.geometry import Affine, Point
from composition_grid.core.grids.grid_registry import get
from composition_grid.core.transform_state import (
    TransformState,
    apply_corner_delta,
    apply_edge_delta,
    element_matrix,
    move,
    rotate,
)
from composition_grid.model.grid_model import GridModel
from composition_grid.presentation.handle_layout import (
    DEFAULT_VP_X,
    DEFAULT_VP_Y,
    HandleId,
    handle_positions,
    hit_test,
)

__all__ = ["TransformController"]

_CORNERS = ("nw", "ne", "se", "sw")
_EDGES = ("n", "e", "s", "w")


def _wrap_deg(delta_deg: float) -> float:
    """Wrap an angle delta into ``(-180, 180]`` (mirrors ``rotate``)."""
    wrapped = (delta_deg + 180.0) % 360.0 - 180.0
    if wrapped <= -180.0:
        wrapped = 180.0
    return wrapped


@dataclass(frozen=True)
class _Drag:
    """Snapshot of one in-flight drag (controller-internal, never exported).

    ``TransformState`` is a frozen dataclass, so keeping the begin-snapshot
    reference is safe: the controller never mutates it, and the model's own
    field is only ever replaced wholesale.

    The frozen widget→doc affine is the ONLY view datum a drag needs: every step
    works in doc space through it, so no view-scale field exists here (the
    former ``px_per_doc`` was written at begin and read by nobody, so it was
    removed; the live ``view_scale`` belongs to
    :meth:`TransformController.probe`, which is a hit test and not
    part of a drag). ``start_vp`` is the begin ``vp_x``/``vp_y`` pair the vp0
    drag offsets itself against.
    """

    handle: HandleId
    start_transform: TransformState
    start_doc: Point
    start_angle: float  # radians; rotate handle only, 0.0 otherwise
    to_doc: Affine  # widget -> doc, frozen at begin_drag
    start_vp: Point  # begin (vp_x, vp_y) pair; vp0 drag only


class TransformController:
    """Drag controller for one transformable element in the edit session.

    The begin→drag→end protocol: :meth:`begin_drag` validates and
    snapshots; every :meth:`drag_to` writes the derived state through to
    the model (grid live-updates during the drag); :meth:`end_drag`
    clears the drag state and is idempotent.
    """

    def __init__(
        self,
        view_state_provider: Callable[[], ViewState | None],
        model_provider: Callable[[], GridModel | None],
    ) -> None:
        """Both providers are injected; the controller owns no canvas state.

        ``view_state_provider`` returns the current
        :class:`ViewState` (or ``None`` without an active view) and
        ``model_provider`` the current :class:`GridModel` (or ``None``
        without an active document).
        """
        self._view_state_provider = view_state_provider
        self._model_provider = model_provider
        self._drag: _Drag | None = None

    # ------------------------------------------------------------ look up

    def _grid_has_vp(self, model: GridModel) -> bool:
        """``True`` when the grid type's schema defines BOTH vp params.

        The gate is what makes a vp drag safe: the write path
        (:meth:`_apply_vp_param`) stores ``vp_x`` AND ``vp_y``, and each write
        looks its ``Param`` up in the schema — so a schema carrying only one of
        them would pass an ``vp_x``-only gate and then raise ``StopIteration``
        mid-drag (the opposite of this module's degrade-to-``False`` contract).
        The presence of BOTH names is required, never just one.
        """
        try:
            cls = get(model.grid_type_id)
        except KeyError:
            return False
        names = {param.name for param in cls.params}
        return "vp_x" in names and "vp_y" in names

    def _positions(
        self, model: GridModel, view: ViewState
    ) -> dict[HandleId, Point]:
        """Widget-px handle positions for the current model + view."""
        if self._grid_has_vp(model):
            return handle_positions(
                view,
                model.transform,
                vp_count=1,
                vp_x=model.params.get("vp_x", DEFAULT_VP_X),
                vp_y=model.params.get("vp_y", DEFAULT_VP_Y),
            )
        return handle_positions(view, model.transform)

    # -------------------------------------------------------------- probe

    def probe(self, handle: HandleId, probe: Point, tol_px: float) -> bool:
        """Is THIS handle within ``tol_px`` (widget px) of the widget point?

        Positions are recomputed for the current model + view and the check
        is delegated to
        :func:`composition_grid.presentation.handle_layout.hit_test`, which
        applies the tolerance clamp (positions-frame diagonal / 6) and the
        grab PRIORITY internally. Because of that priority, a handle hidden
        beneath a higher-priority overlapping handle (e.g. a corner under
        the rotate handle) probes ``False`` even when it is inside the raw
        radius — the controller adds no second, unclamped distance check.
        Without an active model or view (or with an unknown handle id) the
        answer is simply ``False`` — no exception.
        """
        model = self._model_provider()
        view = self._view_state_provider()
        if model is None or view is None:
            return False
        hit = hit_test(
            self._positions(model, view),
            probe,
            tol_px,
            view_scale(view),
        )
        return hit == handle

    # --------------------------------------------------------------- drag

    def begin_drag(
        self, handle: HandleId, widget_point: Point, shift_held: bool
    ) -> bool:
        """Start a drag of ``handle`` at the widget point; ``True`` on success.

        Requires an active model AND view state (else ``False``, no
        exception, and NO drag left behind — the previous snapshot is dropped
        first, so a refused begin can never make the next ``drag_to`` mutate the
        model from state the overlay has already released). Snapshots at begin:
        the start ``TransformState``, the widget point's doc position via the
        CURRENT ``widget_to_doc_affine`` (the view is FROZEN for the whole drag —
        see the module docstring — so mid-drag zoom does not warp it), and — for
        ``"rotate"`` — the start angle about the element center. ``"vp0"``
        additionally requires a grid type with vp parameters (perspective in v1)
        and snapshots the begin ``vp_x``/``vp_y`` pair the drag offsets itself
        against.
        ``shift_held`` is not snapshotted: the effective ratio lock uses the
        Shift state of each :meth:`drag_to` call (live inversion during the
        drag).
        """
        self._drag = None
        model = self._model_provider()
        view = self._view_state_provider()
        if model is None or view is None:
            return False
        if handle == "vp0" and not self._grid_has_vp(model):
            return False

        to_doc = widget_to_doc_affine(view)
        start_doc = to_doc.apply(widget_point)
        start_transform = model.transform
        start_angle = math.atan2(
            start_doc[1] - start_transform.cy, start_doc[0] - start_transform.cx
        ) if handle == "rotate" else 0.0
        start_vp = (
            model.params.get("vp_x", DEFAULT_VP_X),
            model.params.get("vp_y", DEFAULT_VP_Y),
        )

        self._drag = _Drag(
            handle=handle,
            start_transform=start_transform,
            start_doc=start_doc,
            start_angle=start_angle,
            to_doc=to_doc,
            start_vp=start_vp,
        )
        del shift_held
        return True

    def drag_to(self, widget_point: Point, shift_held: bool) -> bool:
        """One drag step; ``False`` when there is no active drag (no raise).

        The widget point is converted through the view frozen at
        :meth:`begin_drag`; the resulting doc delta is applied to the BEGIN
        snapshot (never to the live model value, so intra-drag jitter does
        not accumulate) and written through to the model, which then emits —
        forwarding to the owning :class:`GridStateService` (the single hub), so
        the grid live-updates and the docker + persistence follow on every step.

        Dispatch by handle kind:

        * ``move``: :func:`move` shifts the center by the doc delta.
        * corners: :func:`apply_corner_delta` with
          ``ratio_override`` = XOR of the snapshot's ``ratio_locked`` and
          the LIVE ``shift_held`` (Shift temporarily inverts the lock).
        * edges: :func:`apply_edge_delta` (perpendicular component ignored).
        * ``rotate``: delta angle about the element center from the frozen
          start angle, wrapped into ``(-180, 180]``, fed to :func:`rotate`.
        * ``vp0``: the element-normalized LOCAL delta between the begin cursor
          point and the dragged doc point (both through the INVERSE of the
          snapshot ``element_matrix``) is added to the ``vp_x`` / ``vp_y`` pair
          snapshotted at begin, clamped to the schema range; a vp drag on a grid
          without vp params is a ``False`` no-op.
        """
        drag = self._drag
        model = self._model_provider()
        if drag is None or model is None:
            return False

        doc_now = drag.to_doc.apply(widget_point)
        dx = doc_now[0] - drag.start_doc[0]
        dy = doc_now[1] - drag.start_doc[1]
        handle = drag.handle

        if handle == "move":
            model.transform = move(drag.start_transform, dx, dy)
        elif handle in _CORNERS:
            locked = bool(drag.start_transform.ratio_locked) != bool(shift_held)
            model.transform = apply_corner_delta(
                drag.start_transform, handle, dx, dy, locked
            )
        elif handle in _EDGES:
            model.transform = apply_edge_delta(drag.start_transform, handle, dx, dy)
        elif handle == "rotate":
            angle_now = math.atan2(
                doc_now[1] - drag.start_transform.cy,
                doc_now[0] - drag.start_transform.cx,
            )
            delta_deg = _wrap_deg(math.degrees(angle_now - drag.start_angle))
            model.transform = rotate(drag.start_transform, delta_deg)
        elif handle == "vp0":
            if not self._grid_has_vp(model):
                return False
            to_local = element_matrix(drag.start_transform).inverted()
            local_start = to_local.apply(drag.start_doc)
            local_now = to_local.apply(doc_now)
            self._apply_vp_param(
                model,
                "vp_x",
                drag.start_vp[0] + (local_now[0] - local_start[0]),
            )
            self._apply_vp_param(
                model,
                "vp_y",
                drag.start_vp[1] + (local_now[1] - local_start[1]),
            )
        else:
            # Unknown handle id: begin_drag would refuse it; nothing to do.
            return False

        model.emit()
        return True

    def end_drag(self) -> None:
        """Clear the drag state; idempotent and safe when not dragging."""
        self._drag = None

    # ----------------------------------------------------------- helpers

    def _apply_vp_param(
        self, model: GridModel, name: str, element_value: float
    ) -> None:
        """Write one vp param, clamped to the type schema's min/max.

        Mirrors what the docker's set-param coercion would store for the
        ``perspective_grid`` ``vp_x`` / ``vp_y`` number params (schema
        range 0..1). ``resolve_number`` — the docker path — is exactly
        ``float(value)`` + the same ``[minimum, maximum]`` clamp, so this raw
        write is already coercion-parity (no rounding or further normalization
        exists on either side); the call site is additionally gated by
        :meth:`_grid_has_vp`, which guarantees this param really is in the
        schema.
        """
        cls = get(model.grid_type_id)
        param = next(p for p in cls.params if p.name == name)
        value = float(element_value)
        if param.minimum is not None:
            value = max(param.minimum, value)
        if param.maximum is not None:
            value = min(param.maximum, value)
        model.params[name] = value
