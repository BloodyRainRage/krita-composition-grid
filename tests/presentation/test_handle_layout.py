"""Pinned tests for ``composition_grid.presentation.handle_layout``.

Pure arithmetic only — the layout layer must stay Qt-free (guard test:
``test_no_qt_in_presentation.py``). Coordinate conventions (burned into the
docstrings): screen-style y-down doc space and widget pixels; the element
rotation reaches positions only via ``TransformState.element_matrix``; the
view rotation (positive = CCW as seen on screen) is pre-composed inside
``doc_to_widget_affine``.

Shared fixture numbers: doc 100x50, widget rect (5, 5, 200, 100) →
view scale = 2.0 widget px per doc px.
"""

import math

import pytest

from composition_grid.canvas.doc_view_mapper import (
    ViewState,
    doc_to_widget_affine,
    view_scale,
)
from composition_grid.core.geometry import Bounds
from composition_grid.core.grids.grid_registry import default_params
from composition_grid.core.grids.perspective_grid import PerspectiveGrid
from composition_grid.core.transform_state import TransformState, element_matrix
from composition_grid.presentation import (
    DEFAULT_VP_X,
    DEFAULT_VP_Y,
    cursor_shape,
    effective_tolerance,
    handle_ids,
    handle_positions,
    hit_test,
)

_RECT = Bounds(x=5.0, y=5.0, w=200.0, h=100.0)  # widget px
_DOC = (100.0, 50.0)  # doc px
_SCALE = 200.0 / 100.0  # widget px per doc px


def _state(rotation_deg: float = 0.0) -> ViewState:
    return ViewState(
        document_rect_widget=_RECT,
        rotation_deg=rotation_deg,
        doc_width=_DOC[0],
        doc_height=_DOC[1],
    )


def test_handle_ids_order_and_vp():
    assert handle_ids() == ("nw", "ne", "se", "sw", "n", "e", "s", "w", "move", "rotate")
    assert handle_ids(include_vp=1) == (
        "nw",
        "ne",
        "se",
        "sw",
        "n",
        "e",
        "s",
        "w",
        "move",
        "rotate",
        "vp0",
    )


def test_corner_handles_at_element_corners():
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    positions = handle_positions(state, ts)

    view = doc_to_widget_affine(state)
    matrix = element_matrix(ts)
    for handle, local in (
        ("nw", (0.0, 0.0)),
        ("ne", (1.0, 0.0)),
        ("se", (1.0, 1.0)),
        ("sw", (0.0, 1.0)),
    ):
        assert positions[handle] == pytest.approx(view.apply(matrix.apply(local)))

    # Hand-computed: element doc rect (30..70, 15..35) at zoom 2 from (5, 5).
    assert positions["nw"] == pytest.approx((65.0, 35.0))
    assert positions["ne"] == pytest.approx((145.0, 35.0))
    assert positions["se"] == pytest.approx((145.0, 75.0))
    assert positions["sw"] == pytest.approx((65.0, 75.0))
    # Edge midpoints and the move anchor (element center).
    assert positions["n"] == pytest.approx((105.0, 35.0))
    assert positions["e"] == pytest.approx((145.0, 55.0))
    assert positions["s"] == pytest.approx((105.0, 75.0))
    assert positions["w"] == pytest.approx((65.0, 55.0))
    assert positions["move"] == pytest.approx((105.0, 55.0))


def test_rotate_handle_above_top_edge_24px():
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    positions = handle_positions(state, ts)

    assert view_scale(state) == pytest.approx(_SCALE)
    top_mid = doc_to_widget_affine(state).apply(element_matrix(ts).apply((0.5, 0.0)))
    rotate = positions["rotate"]
    # exact 24 widget px beyond the top-edge midpoint, straight up on screen
    assert math.hypot(rotate[0] - top_mid[0], rotate[1] - top_mid[1]) == pytest.approx(24.0)
    assert rotate == pytest.approx((top_mid[0], top_mid[1] - 24.0))


def test_hit_priority_rotate_before_corners():
    state = _state()
    # 5x5 doc px element -> ~10x10 widget px: the rotate anchor is close to
    # the top corners.
    tiny = TransformState(cx=50.0, cy=25.0, width=5.0, height=5.0)
    positions = handle_positions(state, tiny)
    rotate = positions["rotate"]

    # the nw corner is WELL within the raw grab radius of the rotate anchor,
    # yet the rotate handle must win by priority:
    assert math.hypot(rotate[0] - positions["nw"][0], rotate[1] - positions["nw"][1]) < 30.0
    assert hit_test(positions, rotate, tol_px=30.0, view_scale=_SCALE) == "rotate"
    # same XY logic at the exact corner XY: the corner itself is probed there
    assert hit_test(positions, positions["nw"], tol_px=30.0, view_scale=_SCALE) == "nw"


def test_tiny_element_hit_priority_rotate_most():
    state = _state()
    tiny = TransformState(cx=50.0, cy=25.0, width=5.0, height=5.0)  # ~10x10 widget px
    positions = handle_positions(state, tiny)

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    # Direct clamp math: effective radius = min(tol_px, diag / 6), and the
    # diagonal term must actually bind for this raw radius.
    assert diag / 6.0 < 100.0
    assert effective_tolerance(positions, 100.0) == pytest.approx(min(100.0, diag / 6.0))
    # a raw radius smaller than the clamp bound passes through unchanged
    assert effective_tolerance(positions, 1.0) == pytest.approx(1.0)

    # Probe 6 widget px left of the nw corner: inside the RAW radius (100)
    # but outside the clamped one -> nothing must be grabbed.
    probe = (positions["nw"][0] - 6.0, positions["nw"][1])
    assert 6.0 > diag / 6.0
    assert hit_test(positions, probe, tol_px=100.0, view_scale=_SCALE) is None

    # Inside the clamped radius the priority order holds as usual.
    assert hit_test(positions, positions["rotate"], tol_px=100.0, view_scale=_SCALE) == "rotate"
    assert hit_test(positions, positions["se"], tol_px=100.0, view_scale=_SCALE) == "se"


def test_vp_defaults_match_perspective_grid_params():
    """Drift-guard: pure layer doesn't import grid types at runtime, so the
    ``vp_x``/``vp_y`` defaults are re-exposed as module constants in
    ``handle_layout`` — this test (importing the grid types only here, at
    test time) pins them to the ``perspective_grid`` Param defaults."""
    params = default_params(PerspectiveGrid)
    assert DEFAULT_VP_X == params["vp_x"]
    assert DEFAULT_VP_Y == params["vp_y"]


def test_vp_positions_at_vp_points():
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    vp_local = (0.25, 0.75)  # perspective vp_x / vp_y, normalized to the element

    positions = handle_positions(state, ts, vp_count=1, vp_x=vp_local[0], vp_y=vp_local[1])
    expected = doc_to_widget_affine(state).apply(element_matrix(ts).apply(vp_local))
    assert positions["vp0"] == pytest.approx(expected)
    # hand-computed: (0.25, 0.75) -> doc (40, 30) -> widget (5 + 80, 5 + 60)
    assert positions["vp0"] == pytest.approx((85.0, 65.0))

    # No vanishing point requested -> no vp handle at all.
    assert "vp0" not in handle_positions(state, ts)


def test_cursor_shape_names():
    assert cursor_shape("rotate") == "cross"
    assert cursor_shape("move") == "size_all"
    assert cursor_shape("nw") == "size_fdiag"
    assert cursor_shape("se") == "size_fdiag"
    assert cursor_shape("ne") == "size_bdiag"
    assert cursor_shape("sw") == "size_bdiag"
    assert cursor_shape("n") == "size_ver"
    assert cursor_shape("s") == "size_ver"
    assert cursor_shape("e") == "size_hor"
    assert cursor_shape("w") == "size_hor"
    assert cursor_shape("vp0") == "pointing_hand"
    assert cursor_shape("not-a-handle") == "arrow"


def test_rotated_element_handles_follow_rotation():
    angle = 30.0
    state = _state()  # the view itself stays unrotated: doc -> widget is pure zoom
    ts = TransformState(
        cx=50.0, cy=25.0, width=40.0, height=20.0, rotation_deg=angle
    )
    positions = handle_positions(state, ts)

    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    def rotate_rel(rel: tuple[float, float]) -> tuple[float, float]:
        """Rotate a center-relative doc vector the way element_matrix does."""
        dx, dy = rel
        return (cos_a * dx - sin_a * dy, sin_a * dx + cos_a * dy)

    def widget_of(rel: tuple[float, float]) -> tuple[float, float]:
        rx, ry = rotate_rel(rel)
        return (5.0 + 2.0 * (50.0 + rx), 5.0 + 2.0 * (25.0 + ry))

    landmarks = {
        "nw": (-20.0, -10.0),
        "ne": (20.0, -10.0),
        "se": (20.0, 10.0),
        "sw": (-20.0, 10.0),
        "n": (0.0, -10.0),
        "e": (20.0, 0.0),
        "s": (0.0, 10.0),
        "w": (-20.0, 0.0),
        "move": (0.0, 0.0),
    }
    for handle, rel in landmarks.items():
        assert positions[handle] == pytest.approx(widget_of(rel), abs=1e-9)

    # Rotate handle: 24 widget px = 12 doc px beyond the top-edge midpoint,
    # along the element's own (rotated) local up axis — "above the top edge
    # in doc terms".
    offset_doc = 24.0 / view_scale(state)
    top_mid = rotate_rel((0.0, -10.0))
    up_dir = rotate_rel((0.0, -1.0))
    anchor_rel = (top_mid[0] + up_dir[0] * offset_doc, top_mid[1] + up_dir[1] * offset_doc)
    # anchor_rel is already rotated -> place it manually (do NOT rotate again)
    expected_anchor = (5.0 + 2.0 * (50.0 + anchor_rel[0]), 5.0 + 2.0 * (25.0 + anchor_rel[1]))
    assert positions["rotate"] == pytest.approx(expected_anchor, abs=1e-9)

    # And the hit test still finds the rotate anchor on the rotated layout.
    assert hit_test(positions, positions["rotate"], tol_px=8.0, view_scale=_SCALE) == "rotate"


# --- interior move hit (drag-anywhere-inside-the-element) --------------------


def _mirrored_state() -> ViewState:
    """Same fixture numbers as :func:`_state`, but the view mirrors x about the
    doc center line: the corner labels keep their names while the quad's widget
    winding is reversed — the interior test must not care."""
    return ViewState(
        document_rect_widget=_RECT,
        rotation_deg=0.0,
        mirror_x=True,
        mirror_y=False,
        doc_width=_DOC[0],
        doc_height=_DOC[1],
    )


def test_hit_interior_of_axis_aligned_quad_is_move():
    """A probe inside the corner quad but beyond every point handle's grab
    radius resolves to ``"move"`` (previously ``None``)."""
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    positions = handle_positions(state, ts)
    center = positions["move"]
    # interior, pulled toward the nw corner but far from all handles
    probe = (center[0] + 0.4 * (positions["nw"][0] - center[0]),
             center[1] + 0.4 * (positions["nw"][1] - center[1]))
    assert hit_test(positions, probe, tol_px=8.0, view_scale=_SCALE) == "move"


def test_hit_outside_axis_aligned_quad_is_none():
    """A probe outside the corner quad stays ``None``: the interior region is
    exactly the element, not the whole widget."""
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    positions = handle_positions(state, ts)
    # widget rect of the element is (65,35)..(145,75); far outside it:
    assert hit_test(positions, (30.0, 20.0), tol_px=8.0, view_scale=_SCALE) is None
    assert hit_test(positions, (160.0, 90.0), tol_px=8.0, view_scale=_SCALE) is None


def test_hit_interior_of_rotated_quad_is_move():
    """The same interior resolution works on a 30°-rotated element quad."""
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0, rotation_deg=30.0)
    positions = handle_positions(state, ts)
    center = positions["move"]
    probe = (center[0] + 0.4 * (positions["nw"][0] - center[0]),
             center[1] + 0.4 * (positions["nw"][1] - center[1]))
    assert hit_test(positions, probe, tol_px=8.0, view_scale=_SCALE) == "move"
    # a point beyond the rotated sw corner is outside:
    outside = (positions["sw"][0] - 40.0, positions["sw"][1] + 40.0)
    assert hit_test(positions, outside, tol_px=8.0, view_scale=_SCALE) is None


def test_hit_interior_of_mirrored_quad_is_move():
    """Mirrored view reverses the quad winding; the winding-independent test
    still reports ``"move"`` inside and ``None`` outside."""
    state = _mirrored_state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    positions = handle_positions(state, ts)
    center = positions["move"]
    probe = (center[0] + 0.4 * (positions["nw"][0] - center[0]),
             center[1] + 0.4 * (positions["nw"][1] - center[1]))
    assert hit_test(positions, probe, tol_px=8.0, view_scale=_SCALE) == "move"
    assert hit_test(positions, (30.0, 20.0), tol_px=8.0, view_scale=_SCALE) is None


def test_point_handle_wins_over_interior():
    """A probe both inside the quad AND within a corner's grab radius returns
    the corner, never ``"move"`` — the point-handle priority is unchanged."""
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    positions = handle_positions(state, ts)
    nw = positions["nw"]
    # 5,5 widget px inside the nw corner: within the 8px radius and interior.
    probe = (nw[0] + 5.0, nw[1] + 5.0)
    assert hit_test(positions, probe, tol_px=8.0, view_scale=_SCALE) == "nw"


def test_interior_needs_all_four_corners():
    """Without a complete corner quad the interior region is undefined, so only
    the old point-handle behaviour applies (centre → move, else None)."""
    state = _state()
    ts = TransformState(cx=50.0, cy=25.0, width=40.0, height=20.0)
    positions = handle_positions(state, ts)
    del positions["sw"]  # incomplete quad
    center = positions["move"]
    probe = (center[0] + 0.4 * (positions["nw"][0] - center[0]),
             center[1] + 0.4 * (positions["nw"][1] - center[1]))
    assert hit_test(positions, probe, tol_px=8.0, view_scale=_SCALE) is None
    # the centre point handle is still reachable by the old behaviour
    assert hit_test(positions, center, tol_px=8.0, view_scale=_SCALE) == "move"
