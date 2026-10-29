import math

import pytest

from composition_grid.core.geometry import Affine
from composition_grid.core.transform_state import (
    TransformState,
    _MIN_EXTENT,
    apply_corner_delta,
    apply_edge_delta,
    def_element,
    element_matrix,
    move,
    rotate,
)


def test_element_matrix_maps_center():
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0)
    m = element_matrix(ts)
    assert m.apply((0.5, 0.5)) == pytest.approx((300.0, 350.0))
    assert m.apply((0.0, 0.0)) == pytest.approx((100.0, 200.0))
    assert m.apply((1.0, 1.0)) == pytest.approx((500.0, 500.0))


def test_rotation_about_center_not_origin():
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0, rotation_deg=45.0)
    m = element_matrix(ts)
    assert m.apply((0.5, 0.5)) == pytest.approx((300.0, 350.0))
    # local (0, 0) rotates around the element center, not the origin:
    # offset (-200, -150) rotated by 45 degrees (y-down: CCW matrix on y-down
    # flips visual direction, but the length is preserved).
    off = m.apply((0.0, 0.0))
    dx, dy = off[0] - 300.0, off[1] - 350.0
    assert (dx * dx + dy * dy) == pytest.approx(200.0**2 + 150.0**2)
    assert Affine.rotation_deg(45.0).apply((-200.0, -150.0)) == pytest.approx((dx, dy))


def test_corner_resize_keeps_opposite_fixed():
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0)
    new = apply_corner_delta(ts, "nw", -100.0, -50.0, ratio_override=False)
    assert (new.cx, new.cy) == pytest.approx((250.0, 325.0))  # center += (dx/2, dy/2)
    assert (new.width, new.height) == pytest.approx((500.0, 350.0))
    assert new.rotation_deg == pytest.approx(0.0)
    # opposite (se) corner is unchanged in doc space
    old_se = element_matrix(ts).apply((1.0, 1.0))
    new_se = element_matrix(new).apply((1.0, 1.0))
    assert new_se == pytest.approx(old_se)


def test_ratio_lock_preserves_fraction():
    ts = TransformState(cx=400.0, cy=400.0, width=400.0, height=400.0)
    new = apply_corner_delta(ts, "nw", -100.0, -50.0, ratio_override=None)
    assert abs(new.width / new.height - 1.0) < 1e-9
    # grown by the larger delta projection: f = 1 + 100/400 = 1.25 -> 500/500
    assert (new.width, new.height) == pytest.approx((500.0, 500.0))
    # opposite (se) corner still fixed
    old_se = element_matrix(ts).apply((1.0, 1.0))
    new_se = element_matrix(new).apply((1.0, 1.0))
    assert new_se == pytest.approx(old_se)


def test_ratio_override_flips_lock():
    ts = TransformState(cx=400.0, cy=400.0, width=400.0, height=400.0, ratio_locked=False)
    new = apply_corner_delta(ts, "nw", -100.0, -50.0, ratio_override=True)
    assert abs(new.width / new.height - 1.0) < 1e-9
    assert (new.width, new.height) == pytest.approx((500.0, 500.0))


def test_edge_delta_moves_only_one_axis():
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0)
    east = apply_edge_delta(ts, "e", 100.0, 0.0)
    assert (east.width, east.height) == pytest.approx((500.0, 300.0))
    assert (east.cx, east.cy) == pytest.approx((350.0, 350.0))
    # west face stays put
    old_w = element_matrix(ts).apply((0.0, 0.5))
    new_w = element_matrix(east).apply((0.0, 0.5))
    assert new_w == pytest.approx(old_w)

    north = apply_edge_delta(ts, "n", 0.0, -20.0)
    assert (north.width, north.height) == pytest.approx((400.0, 320.0))
    assert (north.cx, north.cy) == pytest.approx((300.0, 340.0))
    # south face stays put
    old_s = element_matrix(ts).apply((0.5, 1.0))
    new_s = element_matrix(north).apply((0.5, 1.0))
    assert new_s == pytest.approx(old_s)


def test_rotate_normalizes():
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0)
    assert rotate(ts, 190.0).rotation_deg == pytest.approx(-170.0)
    assert rotate(ts, -190.0).rotation_deg == pytest.approx(170.0)
    assert rotate(ts, 180.0).rotation_deg == pytest.approx(180.0)
    assert rotate(ts, -180.0).rotation_deg == pytest.approx(180.0)
    assert rotate(TransformState(0, 0, 100, 100, rotation_deg=170.0), 20.0).rotation_deg == pytest.approx(-170.0)


def test_default_geometry_is_0p8_min_side_centered():
    assert def_element(None, 1920, 1080) == TransformState(
        cx=960.0, cy=540.0, width=864.0, height=864.0, rotation_deg=0.0, ratio_locked=True
    )
    assert def_element(None, 1000, 2000) == TransformState(
        cx=500.0, cy=1000.0, width=800.0, height=800.0, rotation_deg=0.0, ratio_locked=True
    )


def test_move_shifts_center_only():
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0, rotation_deg=15.0)
    new = move(ts, 10.0, -5.0)
    assert (new.cx, new.cy) == pytest.approx((310.0, 345.0))
    assert (new.width, new.height) == pytest.approx((400.0, 300.0))
    assert new.rotation_deg == pytest.approx(15.0)


# --- rotation-aware resize (rendered anchoring) -----------------------------


def test_rotated_corner_anchor_fixed():
    ts = TransformState(cx=300.0, cy=200.0, width=400.0, height=300.0, rotation_deg=45.0)
    after = apply_corner_delta(ts, "se", 100.0, 60.0, ratio_override=False)
    # rendered opposite corner (nw, local (0,0)) keeps its doc position exactly
    old_nw = element_matrix(ts).apply((0.0, 0.0))
    new_nw = element_matrix(after).apply((0.0, 0.0))
    assert new_nw == pytest.approx(old_nw, abs=1e-9)
    # and the rendered dragged corner follows the doc delta exactly (free mode)
    old_se = element_matrix(ts).apply((1.0, 1.0))
    new_se = element_matrix(after).apply((1.0, 1.0))
    assert new_se == pytest.approx((old_se[0] + 100.0, old_se[1] + 60.0), abs=1e-9)


def test_rotated_edge_anchor_fixed():
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0, rotation_deg=30.0)
    after = apply_edge_delta(ts, "e", 80.0, 0.0)
    # rendered west face midpoint (local (0, 0.5)) keeps its doc position
    old_w = element_matrix(ts).apply((0.0, 0.5))
    new_w = element_matrix(after).apply((0.0, 0.5))
    assert new_w == pytest.approx(old_w, abs=1e-9)
    # width grows by the local (rotation-projected) delta, not raw doc dx
    assert after.width == pytest.approx(400.0 + 80.0 * math.cos(math.radians(30.0)), abs=1e-9)


def test_rotated_drag_baggage_free():
    ts = TransformState(cx=300.0, cy=200.0, width=400.0, height=300.0, rotation_deg=45.0)
    after = apply_corner_delta(ts, "se", 100.0, 40.0, ratio_override=False)
    # R(-45 deg) . (100, 40):
    s = math.sqrt(0.5)
    dxl = (100.0 + 40.0) * s
    dyl = (40.0 - 100.0) * s
    # extents reflect the projected local delta (no doc-axis "baggage"):
    assert after.width == pytest.approx(400.0 + dxl, abs=1e-9)
    assert after.height == pytest.approx(300.0 + dyl, abs=1e-9)
    assert after.width != pytest.approx(500.0, abs=1e-6)


@pytest.mark.parametrize(
    "corner, dx, dy, expected_center, expected_size",
    [
        ("nw", -100.0, -50.0, (250.0, 325.0), (500.0, 350.0)),
        ("ne", 100.0, -50.0, (350.0, 325.0), (500.0, 350.0)),
        ("sw", -100.0, 50.0, (250.0, 375.0), (500.0, 350.0)),
        ("se", 100.0, 60.0, (350.0, 380.0), (500.0, 360.0)),
    ],
)
def test_corner_sign_table_pinned(corner, dx, dy, expected_center, expected_size):
    ts = TransformState(cx=300.0, cy=350.0, width=400.0, height=300.0)
    new = apply_corner_delta(ts, corner, dx, dy, ratio_override=False)
    assert (new.cx, new.cy) == pytest.approx(expected_center, abs=1e-9)
    assert (new.width, new.height) == pytest.approx(expected_size, abs=1e-9)
    opposite_local = {"nw": (1.0, 1.0), "ne": (0.0, 1.0), "sw": (1.0, 0.0), "se": (0.0, 0.0)}[corner]
    assert element_matrix(new).apply(opposite_local) == pytest.approx(
        element_matrix(ts).apply(opposite_local), abs=1e-9
    )


# --- edge drags: clamping keeps the rendered opposite face fixed ------------

# edge -> (dragged face local midpoint, opposite face local midpoint,
#          local delta direction that SHRINKS/crosses, extent axis)
_EDGES = {
    "n": ((0.5, 0.0), (0.5, 1.0), (0.0, 1.0), "height"),
    "s": ((0.5, 1.0), (0.5, 0.0), (0.0, -1.0), "height"),
    "w": ((0.0, 0.5), (1.0, 0.5), (1.0, 0.0), "width"),
    "e": ((1.0, 0.5), (0.0, 0.5), (-1.0, 0.0), "width"),
}

_EDGE_ELEMENTS = [
    TransformState(cx=50.0, cy=50.0, width=80.0, height=60.0),
    TransformState(cx=300.0, cy=200.0, width=400.0, height=300.0, rotation_deg=37.5),
    TransformState(cx=-120.0, cy=60.0, width=240.0, height=100.0, rotation_deg=-150.0),
    TransformState(cx=10.0, cy=10.0, width=100.0, height=100.0, rotation_deg=90.0),
]


def _crossing_delta(ts: TransformState, edge: str, extra: float = 50.0) -> tuple:
    _dragged, _opposite, local_dir, axis = _EDGES[edge]
    extent = ts.width if axis == "width" else ts.height
    magnitude = extent + extra
    rot = Affine.rotation_deg(ts.rotation_deg)
    return rot.apply((local_dir[0] * magnitude, local_dir[1] * magnitude))


@pytest.mark.parametrize("edge", sorted(_EDGES))
@pytest.mark.parametrize("ts", _EDGE_ELEMENTS, ids=lambda t: f"rot{t.rotation_deg:g}")
def test_edge_delta_crossed_keeps_opposite_face_fixed(edge, ts):
    dragged_local, opposite_local, _dir, axis = _EDGES[edge]
    dx, dy = _crossing_delta(ts, edge)
    new = apply_edge_delta(ts, edge, dx, dy)

    assert getattr(new, axis) == pytest.approx(_MIN_EXTENT, rel=1e-3)
    assert getattr(new, axis) > 0.0
    other_axis = "height" if axis == "width" else "width"
    assert getattr(new, other_axis) == pytest.approx(getattr(ts, other_axis), abs=1e-12)
    assert new.rotation_deg == ts.rotation_deg
    assert new.ratio_locked == ts.ratio_locked

    # the rendered opposite face midpoint keeps its document position
    old_opp = element_matrix(ts).apply(opposite_local)
    new_opp = element_matrix(new).apply(opposite_local)
    assert new_opp == pytest.approx(old_opp, abs=1e-6)

    # and the faces did not mirror: the rendered separation is still the local one
    sep = Affine.rotation_deg(-ts.rotation_deg).apply(
        (
            element_matrix(new).apply(dragged_local)[0] - new_opp[0],
            element_matrix(new).apply(dragged_local)[1] - new_opp[1],
        )
    )
    expected_sep = (
        (dragged_local[0] - opposite_local[0]) * new.width,
        (dragged_local[1] - opposite_local[1]) * new.height,
    )
    assert sep == pytest.approx(expected_sep, abs=1e-6)


@pytest.mark.parametrize("edge", sorted(_EDGES))
@pytest.mark.parametrize("ts", _EDGE_ELEMENTS, ids=lambda t: f"rot{t.rotation_deg:g}")
def test_edge_delta_just_before_crossing_is_exact(edge, ts):
    """Barely non-crossing drags stay on the unclamped path and are anchored exactly."""
    dragged_local, opposite_local, local_dir, axis = _EDGES[edge]
    extent = ts.width if axis == "width" else ts.height
    rot = Affine.rotation_deg(ts.rotation_deg)
    dx, dy = rot.apply((local_dir[0] * (extent - 1.0), local_dir[1] * (extent - 1.0)))
    new = apply_edge_delta(ts, edge, dx, dy)

    assert getattr(new, axis) == pytest.approx(1.0, abs=1e-9)
    old_opp = element_matrix(ts).apply(opposite_local)
    new_opp = element_matrix(new).apply(opposite_local)
    assert new_opp == pytest.approx(old_opp, abs=1e-9)
    old_drag = element_matrix(ts).apply(dragged_local)
    new_drag = element_matrix(new).apply(dragged_local)
    assert new_drag == pytest.approx((old_drag[0] + dx, old_drag[1] + dy), abs=1e-9)


@pytest.mark.parametrize("edge", sorted(_EDGES))
@pytest.mark.parametrize("ts", _EDGE_ELEMENTS, ids=lambda t: f"rot{t.rotation_deg:g}")
def test_edge_delta_ordinary_grows_and_anchors(edge, ts):
    dragged_local, opposite_local, local_dir, axis = _EDGES[edge]
    rot = Affine.rotation_deg(ts.rotation_deg)
    dx, dy = rot.apply((-local_dir[0] * 40.0, -local_dir[1] * 40.0))
    new = apply_edge_delta(ts, edge, dx, dy)

    extent = ts.width if axis == "width" else ts.height
    assert getattr(new, axis) == pytest.approx(extent + 40.0, abs=1e-9)
    assert element_matrix(new).apply(opposite_local) == pytest.approx(
        element_matrix(ts).apply(opposite_local), abs=1e-9
    )
    old_drag = element_matrix(ts).apply(dragged_local)
    new_drag = element_matrix(new).apply(dragged_local)
    assert new_drag == pytest.approx((old_drag[0] + dx, old_drag[1] + dy), abs=1e-9)


def test_edge_delta_unrotated_crossed_pinned_numbers():
    ts = TransformState(cx=50.0, cy=50.0, width=80.0, height=80.0)
    new = apply_edge_delta(ts, "n", 0.0, 100.0)
    assert new.height == pytest.approx(_MIN_EXTENT, rel=1e-3)
    assert new.width == pytest.approx(80.0, abs=1e-12)
    # south face midpoint must stay at (50, 90), not slide to (50, 100)
    assert element_matrix(new).apply((0.5, 1.0)) == pytest.approx((50.0, 90.0), abs=1e-6)
    assert (new.cx, new.cy) == pytest.approx((50.0, 90.0), abs=1e-6)


def test_unknown_edge_still_raises():
    ts = TransformState(cx=0.0, cy=0.0, width=10.0, height=10.0)
    with pytest.raises(ValueError):
        apply_edge_delta(ts, "north", 1.0, 1.0)
