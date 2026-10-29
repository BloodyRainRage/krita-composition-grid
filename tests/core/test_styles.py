from composition_grid.core.styles import Style


def test_width_clamped():
    assert Style(width_px=0).width_px == 0.25
    assert Style(width_px=100).width_px == 12.0

def test_opacity_clamped_and_defaults():
    s = Style(opacity=2.0)
    assert s.opacity == 1.0
    assert s.color_rgba == (0.9, 0.9, 0.9, 0.8)
    assert s.dash is None


def test_dash_sequence_normalized_to_tuple():
    """JSON/settings round-trips turn tuples into lists; ``__post_init__``
    restores a tuple so every consumer (the docker dash combo, the painter)
    compares against the pinned tuple presets. ``None`` stays ``None``."""
    assert Style(dash=[6.0, 3.0]).dash == (6.0, 3.0)
    assert isinstance(Style(dash=[6.0, 3.0]).dash, tuple)
    assert Style(dash=(6.0, 3.0)).dash == (6.0, 3.0)
    assert Style(dash=[]).dash == ()
    assert Style(dash=None).dash is None
