from composition_grid.core.geometry import Bounds
from composition_grid.core.geometry_primitives import (
    Arc,
    Dot,
    Polygon,
    Segment,
    arc_points,
    primitive_within_bounds,
)


def test_arc_points_run_around_center():
    arc = Arc(center=(0.5, 0.5), rx=0.5, ry=0.5, a0=0, a1=90)
    pts = arc_points(arc, n=4)
    first = pts[0]
    assert first[0] >= 0.95 and first[1] <= 0.55    # старт на +x оси

def test_segment_within_bounds():
    assert primitive_within_bounds(Segment((0, 0), (1, 1)), Bounds(0, 0, 10, 10))
    # Brief had Bounds(0, 0, 10, 10) here, but Segment((0, 0), (2, 1)) lies fully
    # inside a 10x10 box; the evident intent (segment exits the box) requires a 1x1
    # box, matching the Dot/Polygon tests below.
    assert not primitive_within_bounds(Segment((0, 0), (2, 1)), Bounds(0, 0, 1, 1))

def test_dot_and_polygon_within():
    assert not primitive_within_bounds(Dot((1.5, 0.5)), Bounds(0, 0, 1, 1))
    assert primitive_within_bounds(Polygon(((0, 0), (1, 0), (1, 1))), Bounds(0, 0, 3, 3))
