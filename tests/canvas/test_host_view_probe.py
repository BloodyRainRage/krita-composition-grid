"""``composition_grid.canvas.host_view_probe`` — reading foreign libkis getters.

The probe is Qt-free by construction, so these tests are level 1 (no binding, no
Krita): plain fakes shaped like what the LIVE host answers, and the interpretation
rules the probe must follow.

WHY THIS FILE EXISTS (live evidence from the user's Krita 5.3.4.1 /
PyQt5 report):

* ``Canvas.mirror()`` answered **``False``** — a plain bool — while the probe
  accepted ONLY a ``{"horizontal": …, "vertical": …}`` dict. The result was
  ``host_view_probe: unusable mirror payload False`` → no orientation →
  ``context.view_state: None`` and nothing painted, on a perfectly healthy host.
  libkis documents ``mirror()`` / ``setMirror(bool)``
  for this API generation (Krita 6's ``mirrorChanged(bool, bool)`` is the newer,
  different shape), so a bool IS the documented host answer and must be read as
  "horizontal flip", with the dict kept for the 6.x shape and a 2-sequence
  accepted as ``(horizontal, vertical)``. Anything else stays UNUSABLE: there is
  deliberately no "unknown -> assume False" default — a silently assumed mirror
  is the same class of bug as a silently assumed zoom, and it is what hid this
  one (degrade, never invent).
* The DEBUG note must not fire for a payload the probe understands: the user's
  log line called a normal host answer "unusable".
* ``Document.resolution()`` is NO LONGER an input: the live host proves the scale
  is ``zoomLevel()`` directly (see ``tests/canvas/test_view_metrics.py``), so the
  probe must not read — and must not require — the document dpi. A document that
  answers no ``resolution()`` at all still produces usable metrics.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

from composition_grid.canvas import host_view_probe
from composition_grid.canvas.view_metrics import ViewMetrics, to_view_state


class _Point:
    """QPointF duck — the shape of ``Canvas.preferredCenter()``."""

    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Canvas:
    """The live 5.3.4.1 Canvas surface: ``zoomLevel()``/``preferredCenter()``/
    ``rotation()``/``mirror()`` and NO 6.x rect getter, NO signals. ``mirror`` is
    whatever the test claims the host answers."""

    def __init__(self, mirror=False, rotation=0.0, zoom=0.0833333333333357,
                 center=(4860.0, 1854.0)):
        self._mirror = mirror
        self._rotation = rotation
        self._zoom = zoom
        self._center = center

    def zoomLevel(self):
        return self._zoom

    def rotation(self):
        return self._rotation

    def mirror(self):
        return self._mirror

    def preferredCenter(self):
        return _Point(*self._center)


class _Document:
    """``Document`` as the host answers it. ``resolution`` defaults to the
    host's real 600 dpi value; ``resolution=False`` builds a document WITHOUT
    the getter at all, which the probe must not care about."""

    def __init__(self, width=7000, height=4000, resolution=600.0):
        self._width = width
        self._height = height
        self._resolution = resolution

    def width(self):
        return self._width

    def height(self):
        return self._height

    def resolution(self):
        return self._resolution


class _CountingResolutionDocument(_Document):
    """Counts every ``resolution()`` read. The probe swallows foreign getter
    exceptions into ``None`` by contract, so "raising" is not a reliable tripwire
    here — a counter is: it proves the getter was never called."""

    def __init__(self, width=7000, height=4000, resolution=600.0):
        super().__init__(width, height, resolution)
        self.resolution_reads = 0

    def resolution(self):
        self.resolution_reads += 1
        return self._resolution


class _Widget:
    """The attached canvas viewport: 1234x877, the live report's own size."""

    def __init__(self, w=1234, h=877):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


def _metrics(canvas=None, document=None, widget=None):
    canvas = canvas if canvas is not None else _Canvas()
    document = document if document is not None else _Document()
    widget = widget if widget is not None else _Widget()
    orientation = host_view_probe.read_orientation(canvas)
    extent = host_view_probe.read_doc_extent(document)
    if orientation is None or extent is None:
        return None
    # The signature itself is part of the finding: the document is NOT an
    # argument of build_metrics any more, because nothing about it (dpi
    # included) enters the computed scale.
    return host_view_probe.build_metrics(
        widget, canvas, extent[0], extent[1], orientation
    )


# --- the mirror interpretation matrix ----------------------------------------


def test_bool_false_mirror_is_usable_and_unflipped():
    """THE live host answer, verbatim: ``mirror()`` -> ``False``. It is a valid
    single-axis horizontal reading, so orientation must come back usable
    (``mirror_x`` False, ``mirror_y`` False) and the whole computed path must
    produce a ViewState — this is the exact input that used to starve the grid."""
    orientation = host_view_probe.read_orientation(_Canvas(mirror=False))
    assert orientation == (0.0, False, False)
    metrics = _metrics(_Canvas(mirror=False))
    assert metrics is not None
    assert metrics.mirror_x is False
    assert metrics.mirror_y is False
    assert to_view_state(metrics) is not None


def test_bool_true_mirror_flips_only_the_horizontal_axis():
    """``setMirror(bool)`` toggles ONE axis, so ``True`` means the
    horizontal flip and vertical stays False — never both, never none."""
    assert host_view_probe.read_orientation(_Canvas(mirror=True)) == (0.0, True, False)


def test_dict_mirror_payload_fills_both_axes():
    """The 6.x shape stays accepted (the shape the earlier code assumed):
    ``{"horizontal": …, "vertical": …}`` maps to its own two axes."""
    canvas = _Canvas(mirror={"horizontal": True, "vertical": False})
    assert host_view_probe.read_orientation(canvas) == (0.0, True, False)
    canvas = _Canvas(mirror={"horizontal": True, "vertical": True})
    assert host_view_probe.read_orientation(canvas) == (0.0, True, True)


def test_two_sequence_of_bools_is_horizontal_then_vertical():
    """A ``(horizontal, vertical)`` pair of bools is the third documented-ish
    shape (the payload of ``mirrorChanged(bool, bool)``) and maps in order."""
    assert host_view_probe.read_orientation(_Canvas(mirror=(True, False))) == (
        0.0,
        True,
        False,
    )
    assert host_view_probe.read_orientation(_Canvas(mirror=[False, True])) == (
        0.0,
        False,
        True,
    )


@pytest.mark.parametrize(
    "payload",
    [
        "left-right",
        1,
        0.0,
        {"horizontal": True},
        {"horizontal": "yes", "vertical": False},
        (True, False, False),
        (1, 0),
        None,
    ],
)
def test_any_other_mirror_shape_is_unusable_and_logged(payload, caplog):
    """NO silent default: a payload the probe cannot interpret keeps the
    orientation missing (``None`` -> the caller answers ``view_state=None``,
    grid hidden) and is LOUD about it — asserted at DEBUG level, so this test
    would not pass on silence."""
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        assert host_view_probe.read_orientation(_Canvas(mirror=payload)) is None
    assert any("mirror" in r.getMessage() for r in caplog.records), caplog.messages


@pytest.mark.parametrize("payload", [False, True, {"horizontal": True, "vertical": False}])
def test_understood_mirror_is_never_reported_as_unusable(payload, caplog):
    """The misleading DEBUG line from the user's log (``unusable mirror payload
    False``) must not fire for a payload the probe actually interprets."""
    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        assert host_view_probe.read_orientation(_Canvas(mirror=payload)) is not None
    assert not any(
        "unusable mirror" in r.getMessage() for r in caplog.records
    ), caplog.messages


def test_absent_mirror_getter_is_reported_as_absent_not_guessed(caplog):
    """A host with no ``mirror()`` at all is the original starvation: the note
    names the getter, the orientation stays ``None``, nothing is assumed."""

    class _NoMirror:
        def zoomLevel(self):
            return 1.0

        def rotation(self):
            return 0.0

        def preferredCenter(self):
            return _Point(0.0, 0.0)

    with caplog.at_level(logging.DEBUG, logger="composition_grid"):
        assert host_view_probe.read_orientation(_NoMirror()) is None
    assert any("mirror" in r.getMessage() for r in caplog.records), caplog.messages


# --- resolution is not an input of the computed scale ------------------------


def test_metrics_are_identical_whatever_the_document_resolution_is():
    """600 dpi and 96 dpi documents at the same zoom must give byte-identical
    metrics: the scale is ``zoomLevel()`` (the host's two scrollbar values are
    the evidence), so the dpi cannot move anything."""
    from composition_grid.canvas.view_metrics import document_rect_widget

    high = _metrics(_Canvas(), _Document(resolution=600.0))
    low = _metrics(_Canvas(zoom=0.0833333333333357), _Document(resolution=96.0))
    assert high == low
    assert document_rect_widget(high).x == pytest.approx(212.0, abs=1e-6)
    assert document_rect_widget(high).y == pytest.approx(284.0, abs=1e-6)


def test_build_metrics_never_reads_document_resolution():
    """The dpi getter is not called AT ALL: an unused read on a foreign wrapper
    is a starvation source of its own (the old code returned ``None`` — grid
    hidden — whenever ``Document.resolution()`` was absent or raised, for a value
    the corrected scale never uses)."""
    document = _CountingResolutionDocument()
    metrics = _metrics(_Canvas(), document)
    assert metrics is not None
    assert metrics.zoom == pytest.approx(0.0833333333333357)
    assert document.resolution_reads == 0, "the computed scale must not read the dpi"


def test_view_metrics_fields_have_no_resolution():
    """The dataclass itself: no field, no guard (the same correction seen from
    the probe side — ``build_metrics`` must not fill what nothing consumes)."""
    assert "resolution" not in {f.name for f in dataclasses.fields(ViewMetrics)}


# --- the host's OWN doc->widget affine (View.flakeTo*Transform) ---------------


class _QTransform:
    """``QTransform`` duck: the six affine numbers as plain ATTRIBUTES (the
    real PyQt shape, methods, is ``_MethodQTransform`` below) and PyQt5's tuple-returning ``inverted()``.

    ``inverted_raises`` builds the shape that answers a BROKEN ``inverted()``:
    the probe inverts in ``Affine`` arithmetic instead, so a binding detail of
    the foreign object can never starve or corrupt the mapping."""

    def __init__(self, m11, m12, m21, m22, dx, dy, inverted_raises=False):
        self.m11 = m11
        self.m12 = m12
        self.m21 = m21
        self.m22 = m22
        self.dx = dx
        self.dy = dy
        self._inverted_raises = inverted_raises

    def inverted(self):  # pragma: no cover - the probe must not call this
        if self._inverted_raises:
            raise RuntimeError("QTransform.inverted() unusable through this binding")
        return self, True


class _View:
    """libkis ``View`` duck carrying only the two transform getters. ``image``
    / ``canvas`` of ``None`` means the host has no such getter at all."""

    def __init__(self, image=None, canvas=None):
        if image is not None:
            self.flakeToImageTransform = lambda: image
        if canvas is not None:
            self.flakeToCanvasTransform = lambda: canvas


def test_read_host_affine_composes_image_inverse_with_canvas_transform():
    """``i2w = flakeToImageTransform()⁻¹ · flakeToCanvasTransform()`` — flake
    space is Krita's internal one, so the image->flake inverse comes FIRST.
    Hand-computed for the two matrices: inverse of the doubling image
    transform, then the rotated/flipped canvas transform."""
    view = _View(
        image=_QTransform(2.0, 0.0, 0.0, 2.0, 0.0, 0.0),
        canvas=_QTransform(0.5, -0.5, 0.5, 0.5, 10.0, 20.0),
    )
    affine = host_view_probe.read_host_affine(view)
    assert (affine.a, affine.b, affine.c, affine.d, affine.e, affine.f) == (
        0.25,
        -0.25,
        0.25,
        0.25,
        10.0,
        20.0,
    )
    # (x, y) -> (a*x + c*y + e, b*x + d*y + f): the QTransform reading of the
    # same numbers, so the field ORDER (m12 into b, m21 into c) is pinned.
    assert affine.apply((4.0, 4.0)) == pytest.approx((12.0, 20.0))


def test_read_host_affine_of_the_measured_rotated_host_view():
    """The live 5.3.1 case (zoom .5, pan (900, 600), rotation 30, doc
    3000x2000): image space == flake space here, so the canvas transform IS the
    answer, and it must map doc(700, 200) to the measured widget point."""
    view = _View(
        image=_QTransform(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        canvas=_QTransform(0.4330, 0.25, -0.25, 0.4330, 987.0, 318.5),
    )
    affine = host_view_probe.read_host_affine(view)
    assert affine.apply((700.0, 200.0)) == pytest.approx((1240.1, 580.1), abs=0.2)


class _MethodQTransform:
    """The REAL PyQt5 shape (live 5.3.1): ``m11()``..``dy()`` are methods."""

    def __init__(self, *values):
        for name, value in zip(("m11", "m12", "m21", "m22", "dx", "dy"), values):
            setattr(self, name, (lambda v=value: v))


def test_read_host_affine_reads_qtransform_methods():
    """A real ``QTransform`` exposes its numbers as methods; reading them as
    attributes starved the host affine on the live host."""
    view = _View(
        image=_MethodQTransform(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        canvas=_MethodQTransform(0.4330, 0.25, -0.25, 0.4330, 987.0, 318.5),
    )
    affine = host_view_probe.read_host_affine(view)
    assert affine.apply((700.0, 200.0)) == pytest.approx((1240.1, 580.1), abs=0.2)


def test_read_host_affine_never_calls_the_foreign_inverted():
    """The inversion is the probe's own arithmetic: a host whose
    ``QTransform.inverted()`` raises still gives the exact mapping."""
    view = _View(
        image=_QTransform(2.0, 0.0, 0.0, 2.0, 0.0, 0.0, inverted_raises=True),
        canvas=_QTransform(0.5, 0.0, 0.0, 0.5, 7.0, 9.0),
    )
    affine = host_view_probe.read_host_affine(view)
    assert affine.apply((2.0, 2.0)) == pytest.approx((7.5, 9.5))


def test_read_host_affine_absent_getters_answer_none():
    """5.3.x hosts without the ``View`` transform getters (and a window that
    answered no view at all) degrade to ``None`` — the computed fallback path."""
    assert host_view_probe.read_host_affine(object()) is None
    assert host_view_probe.read_host_affine(_View(image=_QTransform(1, 0, 0, 1, 0, 0))) is None
    assert host_view_probe.read_host_affine(_View(canvas=_QTransform(1, 0, 0, 1, 0, 0))) is None
    assert host_view_probe.read_host_affine(None) is None


def test_read_host_affine_raising_getter_answers_none():
    """A libkis wrapper mid-teardown raises from any getter; the never-raises
    outward contract holds here too (no exception escapes, ``None`` answers)."""

    class _RaisingView:
        def flakeToImageTransform(self):
            raise RuntimeError("wrapper deleted")

        def flakeToCanvasTransform(self):
            return _QTransform(1, 0, 0, 1, 0, 0)

    assert host_view_probe.read_host_affine(_RaisingView()) is None


def test_read_host_affine_singular_or_non_finite_answers_none():
    """A zero-area matrix has no inverse (the widget->doc direction of the
    overlay would divide by zero) and a NaN/inf entry is garbage placement —
    both are refused, not normalised."""
    flat = _QTransform(0.0, 0.0, 0.0, 0.0, 5.0, 5.0)
    unit = _QTransform(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    assert host_view_probe.read_host_affine(_View(image=flat, canvas=unit)) is None
    assert host_view_probe.read_host_affine(_View(image=unit, canvas=flat)) is None
    nan = _QTransform(float("nan"), 0.0, 0.0, 1.0, 0.0, 0.0)
    assert host_view_probe.read_host_affine(_View(image=nan, canvas=unit)) is None
    inf = _QTransform(1.0, 0.0, 0.0, 1.0, float("inf"), 0.0)
    assert host_view_probe.read_host_affine(_View(image=unit, canvas=inf)) is None


def test_read_host_affine_unreadable_matrix_entry_answers_none():
    """``m11`` as a non-number (a foreign object that is not a QTransform at
    all) degrades like every other unusable input."""

    class _NotAMatrix:
        m11 = "wide"
        m12 = 0.0
        m21 = 0.0
        m22 = 1.0
        dx = 0.0
        dy = 0.0

    assert (
        host_view_probe.read_host_affine(
            _View(image=_NotAMatrix(), canvas=_QTransform(1, 0, 0, 1, 0, 0))
        )
        is None
    )
