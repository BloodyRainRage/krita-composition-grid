import pytest

from composition_grid.core.geometry import Affine, Bounds


def test_affine_apply_and_compose():
    t = Affine.translation(1, 2)
    s = Affine.scaling(2, 2)
    m = t.composed(s)              # сначала translate, потом scale
    assert m.apply((10, 3)) == (22, 10)

def test_rotation_90_about_origin():
    r = Affine.rotation_deg(90)
    assert r.apply((1, 0)) == pytest.approx((0, 1), abs=1e-9)

def test_affine_inverted_roundtrip():
    translate_scale = Affine.translation(1, 2).composed(Affine.scaling(2, 2))
    rotation_with_placement = Affine.rotation_deg(37).composed(
        Affine.translation(1, 2).composed(Affine.scaling(2, 2))
    )
    for m in (translate_scale, rotation_with_placement):
        back = m.inverted()
        for p in ((10, 3), (0, 0), (-4.5, 7.25)):
            assert back.apply(m.apply(p)) == pytest.approx(p, abs=1e-9)

def test_bounds_normalized_and_center():
    b = Bounds(100, 200, 400, 300)
    assert b.center() == (300, 350)
    assert b.normalized((100, 200)) == (0.0, 0.0)
    assert b.normalized((500, 500)) == (1.0, 1.0)
