"""Tests for the model layer: JSON "last used" settings persistence.

``JsonGridSettings`` serializes the state of the ONE selected grid into one JSON
document stored through a pluggable transport, and answers it back as the seed a
NEW grid starts from (``grid_type_id`` + ``params`` + ``style`` — nothing else:
identity, element and visibility belong to a document's own annotation). The
transport here is a fake dict-backed stand-in (:class:`KritaTransport` itself
touches ``krita`` lazily and is never exercised by tests).
"""

from __future__ import annotations

import dataclasses
import json
import logging

import pytest

from composition_grid.model import GridStateService
from composition_grid.model.settings_store import JsonGridSettings

DOC_W, DOC_H = 800.0, 600.0
GROUP, KEY = "composition_grid", "active_grid"

EMPTY_SEED = {"grid_type_id": None, "params": {}, "style": None}


class FakeTransport:
    """Dict-backed transport; keys stored as ``group|key`` strings."""

    def __init__(self) -> None:
        self.data = {}

    def read(self, group, key):
        return self.data.get(f"{group}|{key}")

    def write(self, group, key, value):
        self.data[f"{group}|{key}"] = value


def _phi_service() -> GridStateService:
    """A routed document whose selected grid is the phi type, 2 px wide."""
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    service.add_grid()
    service.set_type("phi_grid")
    service.set_style(width_px=2.0)
    return service


def _phi_model():
    return _phi_service().selected()


# ---------------------------------------------------------------- roundtrip


def test_json_roundtrip_via_transport():
    transport = FakeTransport()
    settings = JsonGridSettings(transport)
    model = _phi_model()

    settings.save(model)

    # Saved under the pinned one key/group.
    assert transport.data[f"{GROUP}|{KEY}"]
    stored = settings.load()
    assert stored["grid_type_id"] == "phi_grid"
    assert stored["params"] == model.params
    # JSON turns tuples into lists: compare the style per-field (rgba as tuple).
    assert stored["style"]["width_px"] == 2.0
    assert stored["style"]["opacity"] == model.style.opacity
    assert stored["style"]["dash"] == (
        list(model.style.dash) if model.style.dash else model.style.dash
    )
    assert tuple(stored["style"]["color_rgba"]) == model.style.color_rgba
    assert "transform" not in stored, "a seed carries values, not an element"


def test_save_serializes_stable_sorted_json():
    settings = JsonGridSettings(FakeTransport())
    settings.save(_phi_model())
    raw_1 = settings._transport.read(GROUP, KEY)

    # Same state → byte-identical JSON (sort_keys stable output).
    settings.save(_phi_model())
    raw_2 = settings._transport.read(GROUP, KEY)
    assert raw_1 == raw_2


# ----------------------------------------------------------- corrupt / shape


def test_corrupt_json_falls_back_to_defaults(caplog):
    transport = FakeTransport()
    transport.write(GROUP, KEY, "{not json")
    settings = JsonGridSettings(transport)

    with caplog.at_level(logging.WARNING, logger="composition_grid"):
        loaded = settings.load()

    assert loaded == EMPTY_SEED
    assert "JSON" in caplog.text


def test_shape_wrong_falls_back(caplog):
    # Valid JSON but of the wrong shape: a list, then a dict without keys.
    for bad in ("[1, 2]", '{"params": {}}'):
        transport = FakeTransport()
        transport.write(GROUP, KEY, bad)
        settings = JsonGridSettings(transport)
        with caplog.at_level(logging.WARNING, logger="composition_grid"):
            loaded = settings.load()
        assert loaded == EMPTY_SEED
        assert "shape" in caplog.text


def test_missing_key_is_silent_first_run(caplog):
    settings = JsonGridSettings(FakeTransport())

    with caplog.at_level(logging.WARNING, logger="composition_grid"):
        loaded = settings.load()

    assert loaded == EMPTY_SEED
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_load_answers_a_fresh_copy_every_time():
    """The seed dict is handed to the service, which may mutate it — the store
    must not keep one shared dict alive."""
    settings = JsonGridSettings(FakeTransport())
    first = settings.load()
    first["grid_type_id"] = "tampered"
    assert settings.load() == EMPTY_SEED


# ---------------------------------------------------------------- one codec


def test_kritarc_payload_is_the_annotation_codec_payload():
    """One field list for both stores: the saved JSON equals the codec's
    encoding of the same model byte for byte, version included."""
    from composition_grid.model.grid_annotation_codec import encode_model

    transport = FakeTransport()
    settings = JsonGridSettings(transport)
    model = _phi_model()
    settings.save(model)
    assert transport.data[f"{GROUP}|{KEY}"] == encode_model(model).decode("utf-8")


def test_a_saved_grid_loads_back_as_a_seed_and_seeds_the_next_grid():
    """The store's only reader is ``add_grid``: what was saved is what a new
    grid starts from (type + parameters + style, never the element)."""
    transport = FakeTransport()
    settings = JsonGridSettings(transport)
    service = _phi_service()
    service.set_param("show_horizontals", False)
    settings.save(service.selected())

    other = GridStateService(seed_provider=settings.load)
    other.ensure("doc2", DOC_W, DOC_H)
    other.set_active("doc2")
    model = other.get(other.add_grid())
    assert model.grid_type_id == "phi_grid"
    assert model.params["show_horizontals"] is False
    assert model.style.width_px == 2.0
    assert model.transform == service.selected().transform, (
        "both documents are the same size: the element is the default one"
    )


def test_an_annotation_container_stored_as_the_seed_is_read_by_grid():
    """A payload of the container shape (a document's annotation) still seeds:
    the decoder answers the selected grid's values."""
    from composition_grid.model.grid_annotation_codec import encode_state

    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    service.add_grid()
    service.set_type("center_cross")
    service.add_grid()
    service.set_type("uniform_grid")
    transport = FakeTransport()
    transport.data[f"{GROUP}|{KEY}"] = encode_state(
        service.grids(), service.selected_grid_id()
    ).decode("utf-8")

    assert JsonGridSettings(transport).load()["grid_type_id"] == "uniform_grid"


def test_load_of_a_v1_kritarc_payload_seeds_from_the_stored_grid():
    transport = FakeTransport()
    transport.write(
        GROUP,
        KEY,
        json.dumps(
            {
                "version": 1,
                "present": True,
                "visible": True,
                "grid_type_id": "phi_grid",
                "params": {"show_horizontals": False},
                "style": {"color_rgba": [0.5, 0.5, 0.5, 1.0], "width_px": 1.0,
                          "opacity": 0.8, "dash": None},
                "transform": {"cx": 1.0, "cy": 2.0, "width": 3.0, "height": 4.0},
            }
        ),
    )
    loaded = JsonGridSettings(transport).load()
    assert loaded["grid_type_id"] == "phi_grid"
    assert loaded["params"]["show_horizontals"] is False
    assert loaded["style"]["width_px"] == 1.0


def test_a_removed_grid_stored_by_an_older_build_seeds_nothing():
    """v1 ``present: false`` means "no grid": the migrated container is empty
    and an empty container is no seed."""
    transport = FakeTransport()
    transport.write(
        GROUP,
        KEY,
        json.dumps(
            {
                "version": 1,
                "present": False,
                "visible": False,
                "grid_type_id": "phi_grid",
                "params": {},
                "style": {},
                "transform": {},
            }
        ),
    )
    assert JsonGridSettings(transport).load() == EMPTY_SEED


# ---------------------------------------------------------------- overwrite


def test_save_overwrites_previous():
    transport = FakeTransport()
    settings = JsonGridSettings(transport)
    service = _phi_service()

    settings.save(service.selected())
    assert settings.load()["params"]["show_horizontals"] is True

    service.set_param("show_horizontals", False)
    settings.save(service.selected())

    loaded = settings.load()
    assert loaded["params"]["show_horizontals"] is False
    assert loaded["grid_type_id"] == "phi_grid"


def test_the_style_shape_survives_the_json_boundary():
    settings = JsonGridSettings(FakeTransport())
    service = _phi_service()
    service.set_style(dash=(4.0, 2.0))
    settings.save(service.selected())
    loaded = settings.load()
    assert loaded["style"]["dash"] == [4.0, 2.0]
    assert dataclasses.asdict(service.selected().style)["dash"] == (4.0, 2.0)
