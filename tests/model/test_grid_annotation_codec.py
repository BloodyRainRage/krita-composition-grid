"""Tests for the model layer: the per-document grid-container JSON codec.

The codec is the ONE serialization of a document's grids, shared by the in-file
``composition_grid`` annotation and the kritarc "last used settings" store
(which keeps a SINGLE grid document). Pure model layer: no Qt, no krita.

Pinned semantics:

* ``encode_state`` writes a version-2 container — ``version``, ``selected``,
  ``grids``, each grid ``grid_id``, ``label``, ``grid_type_id``, ``params``,
  ``style``, ``transform``, ``visible`` (no ``present``: removal deletes);
* ``decode`` round-trips what it encoded, MIGRATES a version-1 payload
  (``present: true`` -> one grid labelled ``"1"``, ``present: false`` -> no
  grids) and answers ``None`` for anything it cannot trust;
* the output is byte-stable for equal states (``sort_keys``).
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from composition_grid.core.styles import Style
from composition_grid.core.transform_state import TransformState
from composition_grid.model.grid_annotation_codec import (
    CODEC_VERSION,
    decode,
    decode_seed,
    encode_model,
    encode_state,
    grid_entry_of,
)
from composition_grid.model.grid_model import GridModel, GridStateService

DOC_W, DOC_H = 800.0, 600.0


def _service_with(grids: int = 1) -> GridStateService:
    """A routed, activated document with ``grids`` added grids."""
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    for _ in range(grids):
        service.add_grid()
    return service


def _model() -> GridModel:
    """A real service-created default grid."""
    return _service_with().selected()


def _v1_payload(**patch) -> bytes:
    """A version-1 (single-grid, with ``present``) annotation payload."""
    payload = {
        "version": 1,
        "present": True,
        "visible": True,
        "grid_type_id": "rule_of_thirds",
        "params": {},
        "style": {},
        "transform": {},
    }
    return json.dumps({**payload, **patch}, sort_keys=True).encode("utf-8")


# ------------------------------------------------------------------ encode


def test_encode_entry_has_the_pinned_grid_fields():
    payload = json.loads(encode_model(_model()).decode("utf-8"))
    assert set(payload) == {
        "version",
        "grid_id",
        "label",
        "grid_type_id",
        "params",
        "style",
        "transform",
        "visible",
    }
    assert payload["version"] == CODEC_VERSION == 2
    assert payload["grid_id"] == "g1"
    assert payload["label"] == "1"
    assert payload["visible"] is True
    assert payload["grid_type_id"] == "rule_of_thirds"
    assert "present" not in payload, "removal is a delete, not a flag"


def test_encode_state_is_a_container_of_every_grid_in_order():
    service = _service_with(3)
    service.grids()[0].label = "sky"
    payload = json.loads(
        encode_state(service.grids(), service.selected_grid_id()).decode("utf-8")
    )
    assert payload["version"] == CODEC_VERSION
    assert payload["selected"] == service.selected_grid_id()
    assert [entry["grid_id"] for entry in payload["grids"]] == ["g1", "g2", "g3"]
    assert [entry["label"] for entry in payload["grids"]] == ["sky", "2", "3"]


def test_encode_state_selected_none_for_an_empty_document():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    payload = json.loads(encode_state(service.grids(), None).decode("utf-8"))
    assert payload == {"version": CODEC_VERSION, "selected": None, "grids": []}


def test_encode_is_utf8_bytes_and_byte_stable():
    service = _service_with(2)
    first = encode_state(service.grids(), service.selected_grid_id())
    assert isinstance(first, bytes)
    assert first == encode_state(service.grids(), service.selected_grid_id())


def test_encode_covers_style_and_transform_of_a_mutated_grid():
    service = _service_with()
    service.set_type("uniform_grid")
    service.set_param("columns", 5)
    service.set_style(width_px=2.0)
    service.set_rotation(30.0)
    entry = grid_entry_of(service.selected())
    assert entry["grid_type_id"] == "uniform_grid"
    assert entry["params"]["columns"] == 5
    assert entry["style"]["width_px"] == 2.0
    assert entry["transform"]["rotation_deg"] == 30.0
    assert entry["transform"] == dataclasses.asdict(service.selected().transform)


# ------------------------------------------------------------------ decode


def test_decode_round_trips_the_whole_container():
    service = _service_with(2)
    service.select_grid(service.grids()[0].grid_id)
    service.set_type("uniform_grid")
    service.set_param("rows", 7)
    service.set_style(width_px=2.0)
    service.toggle_visible()
    original = service.grids()
    state = decode(encode_state(original, service.selected_grid_id()))
    as_json = lambda value: json.loads(json.dumps(value))  # noqa: E731
    assert state["version"] == CODEC_VERSION
    assert state["selected"] == original[0].grid_id
    assert len(state["grids"]) == 2
    entry = state["grids"][0]
    assert entry["grid_id"] == original[0].grid_id
    assert entry["label"] == original[0].label
    assert entry["grid_type_id"] == "uniform_grid"
    assert entry["params"] == original[0].params
    # the round trip normalizes tuples to JSON lists (Style.__post_init__
    # re-normalizes on the way back in):
    assert entry["style"] == as_json(dataclasses.asdict(original[0].style))
    assert entry["transform"] == as_json(dataclasses.asdict(original[0].transform))
    assert entry["visible"] is False


def test_decode_migrates_a_version_1_present_payload_to_one_grid():
    state = decode(_v1_payload(grid_type_id="phi_grid", params={"x": 1}))
    assert state is not None
    assert state["version"] == CODEC_VERSION
    assert state["selected"] is None, "a migrated grid has no stored id to select"
    assert len(state["grids"]) == 1
    entry = state["grids"][0]
    assert entry["grid_id"] == "", "fresh ids are the service's job"
    assert entry["label"] == "1"
    assert entry["grid_type_id"] == "phi_grid"
    assert entry["params"] == {"x": 1}
    assert entry["visible"] is True


def test_decode_migrates_a_version_1_removed_payload_to_no_grids():
    state = decode(_v1_payload(present=False))
    assert state == {"version": CODEC_VERSION, "selected": None, "grids": []}


def test_decode_of_a_migrated_container_installs_through_the_service():
    """The end-to-end migration rule: an old file opens with the one grid it had."""
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    state = decode(_v1_payload(grid_type_id="uniform_grid", visible=False))
    assert service.install_grids("doc", state["grids"], state["selected"]) == 1
    model = service.grids()[0]
    assert model.grid_id == "g1", "the service minted the id the payload lacked"
    assert model.label == "1"
    assert model.grid_type_id == "uniform_grid"
    assert model.visible is False
    assert service.selected_grid_id() == "g1"


def test_decode_accepts_str_bytes_and_bytearray_inputs():
    service = _service_with()
    raw = encode_state(service.grids(), service.selected_grid_id())
    for variant in (raw, raw.decode("utf-8"), bytearray(raw)):
        assert decode(variant)["grids"][0]["grid_type_id"] == "rule_of_thirds"


def test_decode_accepts_a_single_grid_document():
    """The kritarc shape: one grid document, read as a one-grid container."""
    state = decode(encode_model(_model()))
    assert state["selected"] == "g1"
    assert len(state["grids"]) == 1


@pytest.mark.parametrize(
    "bad",
    [
        None,
        b"",
        "",
        b"{not json",
        b"[1, 2]",
        b'"a string"',
        b'{"grid_type_id": "rule_of_thirds"}',  # no version
        b'{"version": 3, "selected": null, "grids": []}',  # unknown version
        b'{"version": 0, "selected": null, "grids": []}',  # below the supported range
        b'{"version": 2, "selected": null}',  # container without a grids list
        b'{"version": 2, "selected": null, "grids": {}}',  # grids not a list
        b'{"version": 2, "selected": null, "grids": ["x"]}',  # entry not a mapping
        b'{"version": 2, "selected": 5, "grids": []}',  # selected not a string
        _v1_payload(grid_type_id=None),  # v1 without a type id
        _v1_payload(present="yes"),  # v1 present not a bool
        _v1_payload(visible=None),  # v1 visible not a bool
        _v1_payload(params=[]),  # params not an object
        _v1_payload(style="red"),  # style not an object
        _v1_payload(transform=None),  # transform not an object
        json.dumps(
            {
                "version": 2,
                "selected": None,
                "grids": [
                    {
                        "grid_id": "g1",
                        "label": "1",
                        "grid_type_id": "rule_of_thirds",
                        "params": {},
                        "style": {},
                        "transform": {},
                        "visible": "yes",
                    }
                ],
            }
        ).encode("utf-8"),  # grid visible not a bool
        json.dumps(
            {
                "version": 2,
                "selected": None,
                "grids": [{"grid_id": 7, "grid_type_id": "rule_of_thirds",
                            "params": {}, "style": {}, "transform": {}, "visible": True}],
            }
        ).encode("utf-8"),  # grid id not a string
    ],
)
def test_decode_answers_none_for_untrusted_payloads(bad):
    assert decode(bad) is None


def test_decode_reports_none_for_a_mapping_that_is_not_a_real_payload():
    """A dict input (the shape the transport layer already decoded) goes
    through the same validation as bytes."""
    assert decode({"grid_type_id": "rule_of_thirds"}) is None
    assert decode({"version": 2, "selected": None, "grids": []}) is not None


# ---------------------------------------------------------------- seed view


def test_decode_seed_answers_only_the_seed_fields():
    service = _service_with()
    service.set_type("uniform_grid")
    service.set_param("columns", 6)
    service.set_style(width_px=2.5)
    selected = service.selected()
    seed = decode_seed(encode_model(selected))
    as_json = lambda value: json.loads(json.dumps(value))  # noqa: E731
    assert seed == {
        "grid_type_id": "uniform_grid",
        "params": dict(selected.params),
        "style": as_json(dataclasses.asdict(selected.style)),
    }
    assert seed["params"]["columns"] == 6
    assert "grid_id" not in seed and "transform" not in seed, (
        "a seed describes values, not a grid identity"
    )


def test_decode_seed_prefers_the_selected_grid_of_a_container():
    service = _service_with(2)
    service.select_grid(service.grids()[0].grid_id)
    service.set_type("center_cross")
    seed = decode_seed(encode_state(service.grids(), service.selected_grid_id()))
    assert seed["grid_type_id"] == "center_cross"


def test_decode_seed_is_none_for_an_empty_container_or_garbage():
    assert decode_seed(encode_state([], None)) is None
    assert decode_seed(b"{not json") is None
    assert decode_seed(_v1_payload(present=False)) is None


# ---------------------------------------------------------------- style shape


def test_grid_entry_of_keeps_the_style_and_transform_as_plain_data():
    model = GridModel(
        grid_type_id="uniform_grid",
        params={"columns": 4, "margin": 0.1},
        style=Style(dash=(4.0, 2.0)),
        transform=TransformState(cx=1.0, cy=2.0, width=3.0, height=4.0),
        visible=True,
        edit_session=True,
        grid_id="g9",
        label="9",
    )
    entry = grid_entry_of(model)
    assert entry["style"]["dash"] == (4.0, 2.0), "asdict: JSON turns it into a list"
    assert entry["transform"]["cx"] == 1.0
    assert "edit_session" not in entry, "the edit session is transient everywhere"
    payload = json.loads(json.dumps(entry, sort_keys=True))
    assert payload["style"]["dash"] == [4.0, 2.0], (
        "Style.__post_init__ re-normalizes the list on the way back in"
    )
