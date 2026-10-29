"""Pinned tests for the pure-logic TransformController (no Qt).

Fixture conventions:
* Documents are 100x100 document px; the fake ``ViewState`` maps the doc
  rect ``Bounds(0, 0, 100, 100)`` onto the widget rect 0..100, so
  ``view_scale == 1`` and widget pixels == document pixels.
* The element is the default geometry: doc-centered 80x80 square,
  ``TransformState(50, 50, 80, 80)``, rotation 0.
* Rotate-drag sign convention pinned HERE: a CLOCKWISE screen drag of the
  rotate handle increases ``rotation_deg`` — consistent with
  ``TransformState``/``element_matrix`` applying ``Affine.rotation_deg``
  directly in y-down doc space, where a positive ``rotation_deg`` already
  reads as clockwise on screen.
"""

import math

import pytest

from composition_grid.canvas.doc_view_mapper import ViewState
from composition_grid.core.geometry import Bounds as GeometryBounds
from composition_grid.core.grids.grid_registry import default_params, get
from composition_grid.core.styles import Style
from composition_grid.core.transform_state import (
    TransformState,
    def_element,
    element_matrix,
)
from composition_grid.model.grid_model import GridModel
from composition_grid.presentation.handle_layout import handle_positions
from composition_grid.ui.transform_controller import TransformController

DOC = 100.0


def make_view() -> ViewState:
    """Fake view: doc 100x100 mapped 1:1 onto the widget rect (scale == 1)."""
    rect = GeometryBounds(x=0.0, y=0.0, w=DOC, h=DOC)
    return ViewState(
        document_rect_widget=rect,
        rotation_deg=0.0,
        mirror_x=False,
        mirror_y=False,
        doc_width=DOC,
        doc_height=DOC,
    )


def make_model(
    grid_type_id: str = "rule_of_thirds",
    transform=None,
    params: dict | None = None,
) -> GridModel:
    cls = get(grid_type_id)
    return GridModel(
        grid_type_id=grid_type_id,
        params=dict(params) if params else default_params(cls),
        style=Style(),
        transform=transform if transform is not None else def_element(None, DOC, DOC),
        visible=True,
        edit_session=True,
    )


def make_controller(model: GridModel | None, view: ViewState | None = None):
    return TransformController(
        view_state_provider=lambda: view if view is not None else make_view(),
        model_provider=lambda: model,
    )


def positions_for(model: GridModel, view: ViewState | None = None):
    """Canonical widget-px handle positions for the current model + view."""
    vp_count = 1 if any(p.name == "vp_x" for p in get(model.grid_type_id).params) else 0
    return handle_positions(
        view if view is not None else make_view(),
        model.transform,
        vp_count=vp_count,
        vp_x=model.params.get("vp_x"),
        vp_y=model.params.get("vp_y"),
    )


def vp_params(vp_x: float, vp_y: float) -> dict:
    """Perspective schema defaults with the vp pair overridden."""
    params = default_params(get("perspective_grid"))
    params.update({"vp_x": vp_x, "vp_y": vp_y})
    return params


# ------------------------------------------------------------------ probe


def test_probe_finds_corner_and_rotate():
    model = make_model()
    controller = make_controller(model)
    positions = positions_for(model)

    assert controller.probe("ne", positions["ne"], 5.0) is True
    assert controller.probe("rotate", positions["rotate"], 5.0) is True
    assert controller.probe("move", positions["move"], 5.0) is True

    # A point far from every handle hits nothing, for every handle id.
    far = (-1000.0, -1000.0)
    for handle in positions:
        assert controller.probe(handle, far, 5.0) is False, handle

    #vp0 is not offered on a non-perspective grid (never in the layout).
    assert controller.probe("vp0", far, 5.0) is False


# ------------------------------------------------------- begin_drag guards


def test_begin_drag_requires_active_model():
    controller = make_controller(None)
    assert controller.begin_drag("move", (50.0, 50.0), False) is False
    # And a failed begin leaves no active drag.
    assert controller.drag_to((60.0, 50.0), False) is False


def test_begin_drag_requires_view_state():
    model = make_model()
    controller = TransformController(
        view_state_provider=lambda: None,
        model_provider=lambda: model,
    )
    assert controller.begin_drag("move", (50.0, 50.0), False) is False
    assert controller.drag_to((60.0, 50.0), False) is False


# ------------------------------------------------------------------- move


def test_move_drag_translates_center_by_widget_delta():
    model = make_model()
    controller = make_controller(model)
    positions = positions_for(model)

    assert controller.begin_drag("move", positions["move"], False) is True
    assert controller.drag_to((positions["move"][0] + 10.0, positions["move"][1]), False) is True

    transform = model.transform
    assert transform.cx == pytest.approx(60.0)
    assert transform.cy == pytest.approx(50.0)
    assert (transform.width, transform.height, transform.rotation_deg) == pytest.approx(
        (80.0, 80.0, 0.0)
    )


# -------------------------------------------------------- corner + ratio


def test_corner_drag_respects_ratio_lock():
    """Shift temporarily INVERTS the ratio lock: XOR with shift."""
    base = def_element(None, DOC, DOC)  # 80x80, ratio_locked=True

    # Locked, no shift: ratio stays square; the opposite corner stays fixed.
    model = make_model(transform=base)
    controller = make_controller(model)
    controller.begin_drag("se", positions_for(model)["se"], False)
    controller.drag_to((positions_for(model)["se"][0] + 10.0,
                        positions_for(model)["se"][1] + 5.0), False)
    assert model.transform.width == pytest.approx(90.0)
    assert model.transform.height == pytest.approx(90.0)
    # Rendered nw corner anchored (rotation 0 -> plain coordinates).
    assert element_matrix(model.transform).apply((0.0, 0.0)) == pytest.approx((10.0, 10.0))

    # Locked, shift held: lock temporarily inverted -> free extents.
    model = make_model(transform=base)
    controller = make_controller(model)
    controller.begin_drag("se", positions_for(model)["se"], False)
    controller.drag_to((positions_for(model)["se"][0] + 10.0,
                        positions_for(model)["se"][1] + 5.0), True)
    assert model.transform.width == pytest.approx(90.0)
    assert model.transform.height == pytest.approx(85.0)

    # Unlocked, shift held: lock temporarily inverted -> ratio preserved.
    unlocked = type(base)(50.0, 50.0, 80.0, 80.0, 0.0, ratio_locked=False)
    model = make_model(transform=unlocked)
    controller = make_controller(model)
    controller.begin_drag("se", positions_for(model)["se"], False)
    controller.drag_to((positions_for(model)["se"][0] + 10.0,
                        positions_for(model)["se"][1] + 5.0), True)
    assert model.transform.width == pytest.approx(90.0)
    assert model.transform.height == pytest.approx(90.0)


# ------------------------------------------------------------------ edge


def test_edge_drag_moves_only_that_edge():
    model = make_model()
    controller = make_controller(model)
    start = positions_for(model)["n"]  # (50, 10)

    assert controller.begin_drag("n", start, False) is True
    # Downward drag shrinks the height; the x component must be ignored.
    assert controller.drag_to((start[0] + 5.0, start[1] + 10.0), False) is True

    transform = model.transform
    assert transform.height == pytest.approx(70.0)
    assert transform.width == pytest.approx(80.0)
    assert transform.cx == pytest.approx(50.0)
    # The opposite (south) face keeps its doc position.
    south = element_matrix(transform).apply((0.5, 1.0))
    assert south == pytest.approx((50.0, 90.0))


# ---------------------------------------------------------------- rotate


def test_rotate_drag_delta_angle_around_center():
    """Pivot = element center; CW screen drag -> positive rotation_deg."""
    model = make_model()
    controller = make_controller(model)
    handle = positions_for(model)["rotate"]
    center = (model.transform.cx, model.transform.cy)
    radius = math.hypot(handle[0] - center[0], handle[1] - center[1])
    start_angle = math.atan2(handle[1] - center[1], handle[0] - center[0])

    assert controller.begin_drag("rotate", handle, False) is True

    # Drag 90 degrees CLOCKWISE on screen (y-down: increasing atan2 angle).
    cw = start_angle + math.pi / 2.0
    cw_point = (
        center[0] + radius * math.cos(cw),
        center[1] + radius * math.sin(cw),
    )
    assert controller.drag_to(cw_point, False) is True
    assert model.transform.rotation_deg == pytest.approx(90.0, abs=1e-9)
    assert model.transform.cx == pytest.approx(50.0)
    assert model.transform.cy == pytest.approx(50.0)

    # CCW back over the discontinuity: crosses atan2's +-180 boundary.
    ccw = start_angle - math.pi / 2.0
    ccw_point = (
        center[0] + radius * math.cos(ccw),
        center[1] + radius * math.sin(ccw),
    )
    assert controller.drag_to(ccw_point, False) is True
    assert model.transform.rotation_deg == pytest.approx(-90.0, abs=1e-9)


# ------------------------------------------------------------------- vp0


def test_vp0_drag_updates_perspective_params():
    model = make_model(grid_type_id="perspective_grid")
    controller = make_controller(model)
    assert model.params["vp_x"] == 0.5 and model.params["vp_y"] == 0.5

    start = positions_for(model)["vp0"]  # (50, 50): vp_x/vp_y = 0.5/0.5
    assert controller.begin_drag("vp0", start, False) is True
    assert controller.drag_to((75.0, 75.0), False) is True

    # Doc point (75, 75) under the snapshot element matrix (origin (10,10),
    # side 80): normalized ((75-10)/80, (75-10)/80).
    assert model.params["vp_x"] == pytest.approx(0.8125)
    assert model.params["vp_y"] == pytest.approx(0.8125)
    # vp drag must not disturb the element transform.
    assert model.transform == def_element(None, DOC, DOC)

    # Drag beyond the element: the schema clamps the params to 0..1.
    assert controller.drag_to((250.0, 250.0), False) is True
    assert model.params["vp_x"] == 1.0
    assert model.params["vp_y"] == 1.0


# ------------------------------------------------- vp0 grab offset (regression)


def test_vp0_off_center_grab_does_not_jump_the_vp():
    """Releasing on the grab point must leave the VP exactly where it was."""
    model = make_model(grid_type_id="perspective_grid")
    controller = make_controller(model)
    # The handle is drawn at (50, 50); 4 px away is still inside the grab radius.
    assert controller.begin_drag("vp0", (54.0, 50.0), False) is True
    assert controller.drag_to((54.0, 50.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.5)
    assert model.params["vp_y"] == pytest.approx(0.5)


def test_vp0_drag_applies_the_cursor_delta_to_the_begin_vp():
    """Both directions move the VP by the CURSOR delta, not the absolute point."""
    model = make_model(grid_type_id="perspective_grid")
    controller = make_controller(model)
    grab = (54.0, 50.0)
    assert controller.begin_drag("vp0", grab, False) is True

    assert controller.drag_to((62.0, 50.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.6)  # 0.5 + 8 / 80
    assert model.params["vp_y"] == pytest.approx(0.5)

    assert controller.drag_to((46.0, 50.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.4)  # 0.5 - 8 / 80
    assert model.params["vp_y"] == pytest.approx(0.5)


def test_vp0_repeated_steps_recompute_from_the_begin_snapshot():
    """Steps are derived from the BEGIN vp pair, so nothing accumulates."""
    model = make_model(grid_type_id="perspective_grid")
    controller = make_controller(model)
    assert controller.begin_drag("vp0", (54.0, 50.0), False) is True

    assert controller.drag_to((60.0, 50.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.575)  # 0.5 + 6 / 80

    assert controller.drag_to((60.0, 50.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.575), "same point, same value"

    assert controller.drag_to((54.0, 50.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.5)

    assert controller.drag_to((54.0, 42.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.5)
    assert model.params["vp_y"] == pytest.approx(0.4)  # 0.5 - 8 / 80


def test_vp0_grab_offset_survives_rotated_and_asymmetric_element():
    ts = TransformState(50.0, 50.0, 80.0, 40.0, 30.0, ratio_locked=False)
    model = make_model(
        grid_type_id="perspective_grid", transform=ts, params=vp_params(0.3, 0.7)
    )
    controller = make_controller(model)
    handle = positions_for(model)["vp0"]
    grab = (handle[0] + 6.0, handle[1] - 4.0)

    assert controller.begin_drag("vp0", grab, False) is True
    assert controller.drag_to(grab, False) is True
    assert model.params["vp_x"] == pytest.approx(0.3)
    assert model.params["vp_y"] == pytest.approx(0.7)

    assert controller.drag_to((grab[0] + 12.0, grab[1] + 8.0), False) is True
    assert (model.params["vp_x"], model.params["vp_y"]) != (0.3, 0.7)
    # The rendered handle follows the cursor delta, keeping the grab offset.
    assert positions_for(model)["vp0"] == pytest.approx(
        (handle[0] + 12.0, handle[1] + 8.0)
    )
    assert model.transform == ts


def test_vp0_grab_offset_survives_zoom_and_mirror():
    """A rotated/mirrored/zoomed view maps the cursor delta through the frozen affine."""
    view = ViewState(
        document_rect_widget=GeometryBounds(x=0.0, y=0.0, w=200.0, h=200.0),
        mirror_x=True,
        doc_width=DOC,
        doc_height=DOC,
    )  # zoom 2x, x mirrored: widget delta (+16, 0) == doc delta (-8, 0)
    model = make_model(
        grid_type_id="perspective_grid", params=vp_params(0.3, 0.5)
    )
    controller = TransformController(
        view_state_provider=lambda: view, model_provider=lambda: model
    )
    handle = positions_for(model, view)["vp0"]
    assert handle == pytest.approx((132.0, 100.0))
    grab = (handle[0] + 8.0, handle[1] + 6.0)

    assert controller.begin_drag("vp0", grab, False) is True
    assert controller.drag_to(grab, False) is True
    assert (model.params["vp_x"], model.params["vp_y"]) == (0.3, 0.5)

    assert controller.drag_to((grab[0] + 16.0, grab[1]), False) is True
    assert model.params["vp_x"] == pytest.approx(0.2)
    assert model.params["vp_y"] == pytest.approx(0.5)
    assert positions_for(model, view)["vp0"] == pytest.approx(
        (handle[0] + 16.0, handle[1])
    )


def test_vp0_offset_drag_clamps_to_the_schema_range():
    model = make_model(grid_type_id="perspective_grid")
    controller = make_controller(model)
    assert controller.begin_drag("vp0", (54.0, 54.0), False) is True

    assert controller.drag_to((54.0, 200.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.5)
    assert model.params["vp_y"] == 1.0

    assert controller.drag_to((54.0, -100.0), False) is True
    assert model.params["vp_x"] == pytest.approx(0.5)
    assert model.params["vp_y"] == 0.0


def test_vp0_on_non_perspective_grid_is_a_safe_noop():
    model = make_model(grid_type_id="rule_of_thirds")
    controller = make_controller(model)

    assert controller.begin_drag("vp0", (50.0, 50.0), False) is False
    assert controller.drag_to((60.0, 60.0), False) is False
    assert model.transform == def_element(None, DOC, DOC)


def test_vp_gate_requires_both_vp_params_of_the_schema():
    """The defect this closes: the gate looked at
    ``vp_x`` ONLY, while a vp drag writes ``vp_x`` AND ``vp_y`` — so a schema
    with ``vp_x`` but no ``vp_y`` passed the gate and then died on
    ``next(...)`` with StopIteration inside ``_apply_vp_param`` (a crash inside
    a live drag, the opposite of the module's degrade-to-False contract). The
    gate now requires BOTH names: such an asymmetric schema is refused like any
    non-perspective grid, with no exception."""
    import composition_grid.core.grids.grid_registry as grid_registry
    from composition_grid.core.grids.base import GridType, Param

    class AsymmetricVpGrid(GridType):
        id = "asym_vp_grid"
        name = "Asymmetric VP Grid"
        params = (Param(name="vp_x", kind="number", default=0.5, minimum=0.0, maximum=1.0),)

        def build(self, bounds, params):  # noqa: ARG002 - unused in this probe
            return ()

    snapshot = list(grid_registry._REGISTRY)
    grid_registry._REGISTRY.append(AsymmetricVpGrid)
    try:
        model = make_model(grid_type_id="asym_vp_grid", params={"vp_x": 0.5})
        controller = make_controller(model)
        # begin refuses (no vp_y in the schema) ...
        assert controller.begin_drag("vp0", (50.0, 50.0), False) is False
        assert controller.drag_to((60.0, 60.0), False) is False
        # ... and even a controller that reached the write path degrades to a
        # False no-op instead of raising StopIteration.
        assert controller.probe("vp0", (50.0, 50.0), 5.0) is False
        assert model.params == {"vp_x": 0.5}
    finally:
        grid_registry._REGISTRY[:] = snapshot


# ----------------------------------------------------- view freeze / noop


def test_view_is_frozen_at_begin_drag():
    """Mid-drag zoom changes must not warp the in-flight drag."""
    model = make_model()
    current_view = make_view()
    controller = TransformController(
        view_state_provider=lambda: current_view,
        model_provider=lambda: model,
    )
    start = positions_for(model)["move"]
    assert controller.begin_drag("move", start, False) is True

    # The view zooms 2x mid-drag (widget 200x200 over the same doc).
    current_view = ViewState(
        document_rect_widget=GeometryBounds(x=0.0, y=0.0, w=200.0, h=200.0),
        doc_width=DOC,
        doc_height=DOC,
    )
    # A +10 widget px drag still moves the center +10 DOC px (snapshot view).
    assert controller.drag_to((start[0] + 10.0, start[1]), False) is True
    assert model.transform.cx == pytest.approx(60.0)
    assert model.transform.cy == pytest.approx(50.0)


def test_no_active_drag_is_safe_noop():
    controller = make_controller(make_model())
    assert controller.drag_to((60.0, 60.0), False) is False
    assert controller.end_drag() is None  # idempotent, must not raise

    # end_drag after a real drag clears the state.
    model = make_model()
    controller = make_controller(model)
    controller.begin_drag("move", positions_for(model)["move"], False)
    controller.end_drag()
    assert controller.drag_to((70.0, 70.0), False) is False


def test_refused_begin_drag_leaves_no_stale_drag_active():
    """The defect this closes: a REFUSED ``begin_drag`` used to leave the
    previous drag's snapshot in place, so the next ``drag_to`` kept mutating the
    model from a stale frozen affine/handle the overlay had already released.
    Every refusal path (no view, no model, vp gate) must clear the state, and
    the refused handle must not move anything once the provider recovers."""
    model = make_model()
    view = make_view()
    controller = TransformController(
        view_state_provider=lambda: view, model_provider=lambda: model
    )
    assert controller.begin_drag("move", positions_for(model)["move"], False) is True

    view = None  # the view went away: this begin must be refused
    assert controller.begin_drag("se", positions_for(model)["se"], False) is False
    view = make_view()  # ... and it is back before the next move arrives

    before = model.transform
    assert controller.drag_to((10.0, 10.0), False) is False, (
        "a refused begin must not leave a drag active"
    )
    assert model.transform == before

    # the same for the vp gate: a refused vp0 begin cannot resurrect the move drag
    assert controller.begin_drag("move", positions_for(model)["move"], False) is True
    assert controller.begin_drag("vp0", (50.0, 50.0), False) is False
    assert controller.drag_to((80.0, 80.0), False) is False
    assert model.transform == before

    # a successful begin still works afterwards (the clear is not a shutdown)
    assert controller.begin_drag("move", positions_for(model)["move"], False) is True
    assert controller.drag_to((positions_for(model)["move"][0] + 10.0, 50.0), False) is True
    assert model.transform.cx == pytest.approx(before.cx + 10.0)


def test_end_drag_clears_state():
    model = make_model()
    controller = make_controller(model)
    assert controller.begin_drag("move", positions_for(model)["move"], False) is True
    controller.end_drag()
    assert controller.drag_to((70.0, 70.0), False) is False
