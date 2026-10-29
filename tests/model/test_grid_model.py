"""Tests for the model layer: GridModel + the per-document multi-grid service.

Every document owns an ORDERED LIST of grids plus the selected id, so the
fixtures route a document, add a grid and address the selection.
"""

from __future__ import annotations

import logging
from typing import Callable

import pytest

from composition_grid.core.grids import UniformGrid, all_types, default_params
from composition_grid.core.styles import Style
from composition_grid.core.transform_state import TransformState, def_element
from composition_grid.model import GridModel, GridStateService

DOC_W, DOC_H = 800.0, 600.0


def expected_default_element(doc_w: float = DOC_W, doc_h: float = DOC_H) -> TransformState:
    return def_element(None, doc_w, doc_h)


def _counter() -> tuple[list, Callable[[], None]]:
    """A zero-initialized mutable counter plus a callback incrementing it."""
    calls = [0]

    def cb() -> None:
        calls[0] += 1

    return calls, cb


def _routed(service: GridStateService, doc_id: str = "doc") -> GridModel:
    """Ensure + activate + add one grid: the state every "selected grid"
    mutation needs. Returns the added grid."""
    service.ensure(doc_id, DOC_W, DOC_H)
    service.set_active(doc_id)
    grid_id = service.add_grid()
    assert grid_id is not None
    return service.get(grid_id)


# ---------------------------------------------------------------- ensure


def test_ensure_creates_an_empty_container_and_no_grid():
    """The multi-grid rule: routing never invents a grid — the artist adds one."""
    service = GridStateService()
    models = service.ensure("doc", DOC_W, DOC_H)

    assert models == ()
    assert service.grids("doc") == ()
    assert service.selected_grid_id("doc") is None
    assert service.selected("doc") is None
    assert service.is_routed("doc") is True

    # Existing doc: returned untouched (same empty list, no second notification).
    again = service.ensure("doc", DOC_W, DOC_H)
    assert again == ()


def test_ensure_notifies_once_for_a_new_container_only():
    service = GridStateService()
    calls, cb = _counter()
    service.on_change(cb)
    service.ensure("doc", DOC_W, DOC_H)
    assert calls[0] == 1
    service.ensure("doc", DOC_W, DOC_H)
    assert calls[0] == 1  # existing -> untouched, no notify
    service.ensure("other", DOC_W, DOC_H)
    assert calls[0] == 2


def test_a_freshly_routed_document_has_no_grid_until_add():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    assert service.grids() == ()
    first = service.add_grid()
    assert isinstance(first, str) and first
    assert len(service.grids()) == 1


# ---------------------------------------------------------------- add_grid


def test_add_grid_builds_a_default_seeded_visible_grid():
    service = GridStateService()
    grid_id = _routed(service)
    del grid_id
    model = service.selected()

    assert isinstance(model, GridModel)
    assert model.grid_type_id == all_types()[0].id
    assert model.params == default_params(all_types()[0])
    assert model.style == Style()
    assert model.transform == expected_default_element()
    assert model.visible is True
    assert model.edit_session is False
    assert model.grid_id == service.selected_grid_id()
    assert model.label == "1"


def test_add_grid_appends_in_order_and_selects_the_new_grid():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    first = service.add_grid()
    second = service.add_grid()

    assert [model.grid_id for model in service.grids()] == [first, second]
    assert service.selected_grid_id() == second
    assert service.grids() == service.grids("doc")


def test_add_grid_ends_the_edit_session_of_the_other_grids():
    service = GridStateService()
    first = _routed(service).grid_id
    service.start_edit_session()
    service.select_grid(first)
    service.start_edit_session()
    calls, cb = _counter()
    service.on_change(cb)

    second = service.add_grid()
    assert service.get(first).edit_session is False
    assert service.get(second).edit_session is False
    assert calls[0] == 1, "one notification for the whole addition"


def test_add_grid_seeds_from_the_injected_store():
    seed = {"grid_type_id": "uniform_grid", "params": {"columns": 7}, "style": Style(width_px=3.0)}
    service = GridStateService(seed_provider=lambda: seed)
    model = _routed(service)

    assert model.grid_type_id == "uniform_grid"
    assert model.params["columns"] == 7
    assert model.style.width_px == 3.0


def test_add_grid_argument_beats_the_provider():
    service = GridStateService(seed_provider=lambda: {"grid_type_id": "uniform_grid"})
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    model = service.get(service.add_grid({"grid_type_id": "center_cross"}))
    assert model.grid_type_id == "center_cross"


def test_add_grid_unusable_seed_falls_back_to_the_defaults(caplog):
    service = GridStateService(seed_provider=lambda: {"grid_type_id": "no_such_grid"})
    with caplog.at_level(logging.WARNING):
        model = _routed(service)
    assert model.grid_type_id == all_types()[0].id
    assert "no_such_grid" in caplog.text


def test_add_grid_without_an_active_document_is_a_noop():
    service = GridStateService()
    assert service.add_grid() is None
    service.set_active("ghost")  # pointer without a container
    assert service.add_grid() is None
    assert service.grids("ghost") == ()


# ---------------------------------------------------------------- labels


def test_label_numbering_rule():
    """The four pinned cases: smallest integer >= len+1 nobody else carries."""
    cases = [([], "1"), (["1", "2"], "3"), (["1", "2", "sky"], "4"), (["1", "3"], "4")]
    for existing_labels, wanted in cases:
        service = GridStateService()
        service.ensure("doc", DOC_W, DOC_H)
        service.set_active("doc")
        for label in existing_labels:
            model = service.get(service.add_grid())
            model.label = label
        assert service.get(service.add_grid()).label == wanted, existing_labels


def test_set_label_strips_whitespace_and_rejects_empty():
    service = GridStateService()
    grid_id = _routed(service).grid_id
    calls, cb = _counter()
    service.on_change(cb)

    assert service.set_label(grid_id, "  sky  ") is True
    assert service.get(grid_id).label == "sky"
    assert calls[0] == 1

    assert service.set_label(grid_id, "   ") is False
    assert service.get(grid_id).label == "sky"
    assert service.set_label(grid_id, "sky") is False
    assert calls[0] == 1, "a kept or unchanged label notifies nobody"


def test_set_label_allows_duplicates_and_unknown_ids():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    first = service.add_grid()
    second = service.add_grid()

    assert service.set_label(second, "1") is True  # duplicates are the user's business
    assert service.grids()[0].label == "1" and service.grids()[1].label == "1"
    assert service.set_label("no-such-grid", "x") is False


def test_a_third_grid_is_numbered_around_the_user_labels():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    first = service.add_grid()
    service.set_label(first, "3")
    assert service.get(service.add_grid()).label == "2"
    assert service.get(service.add_grid()).label == "4"


# ---------------------------------------------------------------- selection


def test_select_grid_moves_the_selection_and_ends_the_previous_session():
    service = GridStateService()
    first = _routed(service).grid_id
    second = service.add_grid()
    service.select_grid(first)
    service.start_edit_session()
    calls, cb = _counter()
    service.on_change(cb)

    assert service.select_grid(second) is True
    assert service.selected_grid_id() == second
    assert service.get(first).edit_session is False, (
        "the canvas edits one grid at a time"
    )
    assert calls[0] == 1


def test_select_grid_unknown_or_empty_id_is_a_noop():
    service = GridStateService()
    grid_id = _routed(service).grid_id
    calls, cb = _counter()
    service.on_change(cb)
    assert service.select_grid("no-such-grid") is False
    assert service.select_grid("") is False
    assert service.select_grid(None) is False  # type: ignore[arg-type]
    assert service.selected_grid_id() == grid_id
    assert calls[0] == 0


def test_select_grid_of_the_already_selected_id_notifies_nobody():
    service = GridStateService()
    grid_id = _routed(service).grid_id
    calls, cb = _counter()
    service.on_change(cb)
    assert service.select_grid(grid_id) is False
    assert calls[0] == 0


def test_selection_is_per_document():
    service = GridStateService()
    service.ensure("a", DOC_W, DOC_H)
    service.ensure("b", DOC_W, DOC_H)
    service.set_active("a")
    in_a = service.add_grid()
    service.set_active("b")
    assert service.selected_grid_id() is None
    assert service.grids("a") == service.grids("a")
    in_b = service.add_grid()
    assert service.selected_grid_id("a") == in_a
    assert service.selected_grid_id("b") == in_b
    assert service.selected_grid_id("ghost") is None


# ---------------------------------------------------------------- remove_grid


def test_remove_grid_deletes_it_and_selects_the_next():
    service = GridStateService()
    first = _routed(service).grid_id
    second = service.add_grid()
    third = service.add_grid()
    service.select_grid(first)
    calls, cb = _counter()
    service.on_change(cb)

    assert service.remove_grid(first) is True
    assert service.grids() == tuple(service.grids())
    assert [model.grid_id for model in service.grids()] == [second, third]
    assert service.selected_grid_id() == second, "selection moves to the next grid"
    assert calls[0] == 1


def test_remove_grid_falls_back_to_the_previous_then_none():
    service = GridStateService()
    first = _routed(service).grid_id
    second = service.add_grid()
    service.select_grid(second)
    assert service.remove_grid(second) is True
    assert service.selected_grid_id() == first, "no next grid -> the previous one"
    assert service.remove_grid() is True
    assert service.grids() == ()
    assert service.selected_grid_id() is None
    assert service.remove_grid() is False, "nothing left to remove"


def test_remove_grid_defaults_to_the_selected_one():
    service = GridStateService()
    _routed(service)
    second = service.add_grid()
    assert service.remove_grid() is True  # the selection was the second grid
    assert [model.grid_id for model in service.grids()] != [second]


def test_remove_grid_of_another_grid_leaves_the_selection_alone():
    service = GridStateService()
    first = _routed(service).grid_id
    second = service.add_grid()
    assert service.remove_grid(first) is True
    assert service.selected_grid_id() == second


def test_remove_grid_unknown_id_is_a_noop():
    service = GridStateService()
    _routed(service)
    calls, cb = _counter()
    service.on_change(cb)
    assert service.remove_grid("no-such-grid") is False
    assert len(service.grids()) == 1
    assert calls[0] == 0


# ---------------------------------------------------------------- visibility


def test_set_visible_addresses_a_grid_by_id():
    service = GridStateService()
    first = _routed(service).grid_id
    second = service.add_grid()
    calls, cb = _counter()
    service.on_change(cb)

    assert service.set_visible(first, False) is True
    assert service.get(first).visible is False
    assert service.get(second).visible is True
    assert calls[0] == 1
    assert service.set_visible(first, False) is False, "idempotent"
    assert service.set_visible("no-such-grid", True) is False
    assert calls[0] == 1


def test_toggle_visible_keeps_acting_on_the_selected_grid():
    service = GridStateService()
    first = _routed(service).grid_id
    second = service.add_grid()
    # toggle_visible answers the NEW flag of the grid it flipped.
    assert service.toggle_visible() is False
    assert service.get(second).visible is False
    assert service.get(first).visible is True

    service.select_grid(first)
    assert service.toggle_visible() is False
    assert service.get(first).visible is False
    assert service.get(second).visible is False, "per-grid visibility stays per grid"


# ---------------------------------------------------------------- set_type


def test_set_type_keeps_transform_and_style():
    service = GridStateService()
    _routed(service)
    service.set_style(width_px=2.0)
    before_params = dict(service.selected().params)

    service.set_type("center_cross")
    model = service.selected()
    assert model.grid_type_id == "center_cross"
    assert model.transform == expected_default_element()
    assert model.style.width_px == 2.0
    # params replaced by the new type's defaults...
    assert model.params == default_params(
        next(t for t in all_types() if t.id == "center_cross")
    )
    assert model.params != before_params  # rule_of_thirds had no params


def test_set_type_changes_only_the_selected_grid():
    service = GridStateService()
    first = _routed(service).grid_id
    service.add_grid()
    service.set_type("center_cross")
    assert service.get(first).grid_type_id == all_types()[0].id
    assert service.selected().grid_type_id == "center_cross"


def test_set_type_idempotent_no_notify():
    service = GridStateService()
    _routed(service)
    calls, cb = _counter()
    service.on_change(cb)

    service.set_type("center_cross")
    assert calls[0] == 1  # the set_type change
    service.set_type("center_cross")  # same id
    assert calls[0] == 1


def test_set_type_unknown_id_raises():
    service = GridStateService()
    _routed(service)
    with pytest.raises(ValueError):
        service.set_type("no_such_grid")


# ---------------------------------------------------------------- set_param


def test_set_param_type_coercion():
    service = GridStateService()
    _routed(service)

    # bool kind: truthy coercion via the resolve_bool rule.
    service.set_type("center_cross")
    service.set_param("show_center_dot", 1)
    assert service.selected().params["show_center_dot"] is True

    # number kind with integer default resolves like resolve_int: "5" -> 5.
    service.set_type("uniform_grid")
    service.set_param("columns", "5")
    assert service.selected().params["columns"] == 5
    # number kind with float default resolves like resolve_number: "0.2" -> 0.2.
    service.set_param("margin", "0.2")
    assert service.selected().params["margin"] == pytest.approx(0.2)
    # ... and clamps to the schema range like the resolvers do.
    service.set_param("margin", 5)
    assert service.selected().params["margin"] == pytest.approx(0.45)

    # choice kind: valid choice stored, invalid value falls back to default.
    service.set_type("golden_triangles")
    service.set_param("diagonal", "tr_bl")
    assert service.selected().params["diagonal"] == "tr_bl"
    service.set_param("diagonal", "not_a_choice")
    assert service.selected().params["diagonal"] == "tl_br"


def test_set_param_unknown_name_raises():
    service = GridStateService()
    _routed(service)
    service.set_type("center_cross")
    with pytest.raises(ValueError):
        service.set_param("no_such_param", 1)


def test_set_param_no_change_no_notify():
    service = GridStateService()
    _routed(service)
    service.set_type("center_cross")
    calls, cb = _counter()
    service.on_change(cb)

    service.set_param("show_center_dot", False)  # default is False already
    assert calls[0] == 0
    service.set_param("show_center_dot", True)
    assert calls[0] == 1


# ---------------------------------------------------------------- reset param


def test_reset_param_restores_the_schema_default_with_one_notify():
    service = GridStateService()
    _routed(service)
    service.set_type("uniform_grid")
    service.set_param("columns", 7)
    service.set_param("margin", 0.3)
    calls, cb = _counter()
    service.on_change(cb)

    assert service.reset_param("columns") is True
    assert calls[0] == 1, "a reset is one ordinary value change"
    assert service.selected().params["columns"] == default_params(UniformGrid)["columns"]
    assert service.selected().params["margin"] == pytest.approx(
        0.3
    ), "reset_param touches only its own parameter"


def test_reset_param_already_at_default_is_a_false_no_op():
    service = GridStateService()
    _routed(service)
    service.set_type("uniform_grid")
    calls, cb = _counter()
    service.on_change(cb)

    assert service.reset_param("columns") is False
    assert calls[0] == 0


def test_reset_param_unknown_name_raises():
    service = GridStateService()
    _routed(service)
    service.set_type("center_cross")
    with pytest.raises(ValueError):
        service.reset_param("no_such_param")


def test_reset_params_restores_the_whole_schema_with_one_notify():
    service = GridStateService()
    model = _routed(service)
    service.set_type("uniform_grid")
    service.set_param("columns", 9)
    service.set_param("margin", 0.4)
    service.set_style(width_px=5.0)
    service.set_rotation(30.0)
    service.toggle_visible()
    calls, cb = _counter()
    service.on_change(cb)

    assert service.reset_params() is True
    assert calls[0] == 1, "the whole group reset notifies exactly once"
    assert model.params == default_params(UniformGrid)
    # The reset is a PARAMETER reset: nothing else on the grid moves.
    assert model.style.width_px == pytest.approx(5.0)
    assert model.transform.rotation_deg == pytest.approx(30.0)
    assert model.visible is False


def test_reset_params_at_default_or_without_parameters_is_a_false_no_op():
    service = GridStateService()
    _routed(service)
    service.set_type("uniform_grid")
    calls, cb = _counter()
    service.on_change(cb)

    assert service.reset_params() is False, "already at the defaults"

    service.set_type("rule_of_thirds")  # a type with no parameter at all
    calls[0] = 0
    assert service.reset_params() is False
    assert calls[0] == 0


def test_reset_params_targets_the_selected_grid_only():
    service = GridStateService()
    first = _routed(service)
    service.set_type("uniform_grid")
    service.set_param("columns", 8)
    second_id = service.add_grid()
    service.set_type("uniform_grid")
    service.set_param("columns", 4)
    second = service.get(second_id)
    service.select_grid(first.grid_id)

    assert service.reset_params() is True
    assert first.params == default_params(UniformGrid)
    assert second.params["columns"] == 4, "a sibling grid keeps its own values"


@pytest.mark.parametrize("method", ["reset_param", "reset_params"])
def test_param_reset_without_a_selected_grid_is_a_false_no_op(method):
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")  # routed, but no grid added: nothing selected
    calls, cb = _counter()
    service.on_change(cb)

    reset = getattr(service, method)
    assert (reset("columns") if method == "reset_param" else reset()) is False
    assert calls[0] == 0


# ---------------------------------------------------------------- style


def test_set_style_patch_clamps_via_style_validation():
    service = GridStateService()
    _routed(service)
    new = service.set_style(width_px=99.0, opacity=5.0)
    model = service.selected()
    assert model.style.width_px == 12.0  # Style clamps 0.25..12.0
    assert model.style.opacity == 1.0
    assert new is model.style


# ---------------------------------------------------------------- edit session


def test_edit_session_gates():
    service = GridStateService()
    _routed(service)
    calls, cb = _counter()
    service.on_change(cb)

    service.start_edit_session()
    assert service.selected().edit_session is True
    assert calls[0] == 1
    service.start_edit_session()  # idempotent: already active
    assert calls[0] == 1

    service.end_edit_session()
    assert service.selected().edit_session is False
    assert calls[0] == 2
    service.end_edit_session()  # idempotent: not active
    assert calls[0] == 2


def test_start_edit_session_clears_the_other_grids_of_the_document():
    service = GridStateService()
    first = _routed(service).grid_id
    service.select_grid(first)
    service.start_edit_session()
    second = service.add_grid()
    service.start_edit_session()
    assert service.get(first).edit_session is False
    assert service.get(second).edit_session is True


def test_end_edit_session_targets_a_document_directly():
    service = GridStateService()
    service.ensure("a", DOC_W, DOC_H)
    service.ensure("b", DOC_W, DOC_H)
    service.set_active("a")
    a_grid = service.add_grid()
    service.set_active("b")
    b_grid = service.add_grid()
    service.start_edit_session()
    service.set_active("a")
    service.start_edit_session()
    calls, cb = _counter()
    service.on_change(cb)

    assert service.end_edit_session("a") is True
    assert service.get(a_grid, "a").edit_session is False
    assert service.get(b_grid, "b").edit_session is True, (
        "targeted end never touches other docs"
    )
    assert calls[0] == 1, "a targeted end notifies exactly once"

    assert service.end_edit_session("a") is False, "idempotent: already off, no notify"
    assert service.end_edit_session("ghost") is False, "unknown doc: no-op, no notify"
    assert calls[0] == 1

    service.set_active("b")
    assert calls[0] == 2, "the pointer change itself is the only extra notification"
    assert service.end_edit_session() is True, "no-arg keeps the active behaviour"
    assert service.get(b_grid, "b").edit_session is False
    assert calls[0] == 3

    assert service.end_edit_session(None) is False, "explicit None = active, already off"
    assert calls[0] == 3


# ---------------------------------------------------------------- transform


def test_reset_transform_back_to_default():
    service = GridStateService()
    _routed(service)
    service.set_style(width_px=3.0)
    service.selected().transform = TransformState(cx=10.0, cy=20.0, width=5.0, height=5.0)

    state = service.reset_transform(DOC_W, DOC_H)
    assert state == expected_default_element()
    assert service.selected().transform == expected_default_element()
    # reset_transform is transform-only: style is kept.
    assert service.selected().style.width_px == 3.0


# ---------------------------------------------------------------- ratio lock


def test_set_ratio_lock_mutates_transform_and_notifies_once():
    service = GridStateService()
    _routed(service)
    calls, cb = _counter()
    service.on_change(cb)

    # def_element starts locked: unlocking is a real change -> one notify.
    assert service.set_ratio_lock(False) is True
    assert service.selected().transform.ratio_locked is False
    assert calls[0] == 1

    # Idempotent: same flag again -> no notify, returns False.
    assert service.set_ratio_lock(False) is False
    assert calls[0] == 1

    # Re-locking is a change again.
    assert service.set_ratio_lock(True) is True
    assert service.selected().transform.ratio_locked is True
    assert calls[0] == 2


def test_set_ratio_lock_leaves_other_transform_fields_untouched():
    service = GridStateService()
    _routed(service)
    before = service.selected().transform

    service.set_ratio_lock(False)
    after = service.selected().transform
    assert after.ratio_locked is False
    # Only the flag changes; geometry is preserved.
    assert (after.cx, after.cy, after.width, after.height, after.rotation_deg) == (
        before.cx, before.cy, before.width, before.height, before.rotation_deg
    )


# ---------------------------------------------------------------- rotation


def test_set_rotation_stores_absolute_angle():
    service = GridStateService()
    _routed(service)
    calls, cb = _counter()
    service.on_change(cb)

    state = service.set_rotation(45.0)
    assert state is not None
    assert service.selected().transform.rotation_deg == pytest.approx(45.0)
    assert calls[0] == 1


def test_set_rotation_normalizes_into_minus_180_180():
    """The wrap matches core.transform_state.rotate's (-180, 180] convention."""
    service = GridStateService()
    _routed(service)

    assert service.set_rotation(270.0).rotation_deg == pytest.approx(-90.0)
    assert service.set_rotation(-180.0).rotation_deg == pytest.approx(180.0)
    assert service.set_rotation(360.0).rotation_deg == pytest.approx(0.0)
    assert service.set_rotation(180.0).rotation_deg == pytest.approx(180.0)


def test_set_rotation_idempotent_no_notify():
    service = GridStateService()
    _routed(service)
    service.set_rotation(30.0)
    calls, cb = _counter()
    service.on_change(cb)

    # Same angle -> no notify (returns the current state).
    assert service.set_rotation(30.0) is service.selected().transform
    assert calls[0] == 0
    # 390 wraps to 30 == stored -> also idempotent (no notify).
    assert service.set_rotation(390.0).rotation_deg == pytest.approx(30.0)
    assert calls[0] == 0
    # A different angle -> one notify.
    assert service.set_rotation(60.0).rotation_deg == pytest.approx(60.0)
    assert calls[0] == 1


def test_set_rotation_keeps_other_transform_fields():
    service = GridStateService()
    _routed(service)
    before = service.selected().transform

    service.set_rotation(12.0)
    after = service.selected().transform
    assert after.rotation_deg == pytest.approx(12.0)
    assert (after.cx, after.cy, after.width, after.height, after.ratio_locked) == (
        before.cx, before.cy, before.width, before.height, before.ratio_locked
    )


# ---------------------------------------------------------------- callbacks


def test_change_callback_fired_once_per_mutation():
    service = GridStateService()
    first = _routed(service).grid_id
    calls, cb = _counter()
    service.on_change(cb)

    service.grids()  # reads never notify
    assert calls[0] == 0
    _ = service.active_doc_id
    assert calls[0] == 0

    service.set_type("phi_grid")  # one for a NEW type
    assert calls[0] == 1
    service.set_type("phi_grid")  # same id: no notify
    assert calls[0] == 1

    service.set_param("show_verticals", False)  # default is True: a change
    assert calls[0] == 2
    service.toggle_visible()
    assert calls[0] == 3
    service.remove("doc")
    assert calls[0] == 4
    assert service.grids("doc") == ()
    assert first


def test_callback_error_is_logged_not_raised(caplog):
    service = GridStateService()

    def bad_callback() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        service.on_change(bad_callback)
        service.ensure("doc", DOC_W, DOC_H)  # must not raise
    assert "boom" in caplog.text


# ---------------------------------------------------------------- documents


def test_document_removal_clears_state():
    service = GridStateService()
    service.ensure("doc_a", DOC_W, DOC_H)
    service.ensure("doc_b", DOC_W, DOC_H)
    service.set_active("doc_a")
    service.add_grid()

    service.remove("doc_a")
    assert service.grids("doc_a") == ()
    assert service.is_routed("doc_a") is False
    assert service.selected("doc_a") is None
    assert service.active_doc_id is None  # removed doc was active

    # doc_b is untouched and can be made active again.
    service.set_active("doc_b")
    assert service.active_doc_id == "doc_b"

    service.remove("doc_b")
    assert service.grids("doc_b") == ()
    assert service.active_doc_id is None


def test_remove_unknown_document_is_a_noop():
    service = GridStateService()
    calls, cb = _counter()
    service.on_change(cb)
    service.remove("ghost")
    assert calls[0] == 0


def test_set_active_documents():
    service = GridStateService()
    assert service.active_doc_id is None

    # set_active may target a doc that is not ensured yet: pointer only.
    service.set_active("doc")
    assert service.active_doc_id == "doc"
    assert service.selected("doc") is None

    service.ensure("other", DOC_W, DOC_H)
    assert service.active_doc_id == "doc"  # ensure did not steal the pointer

    service.ensure("doc", DOC_W, DOC_H)
    service.add_grid()
    assert service.selected().transform == expected_default_element()

    # Idempotent: same doc again -> no notify.
    calls, cb = _counter()
    service.on_change(cb)
    service.set_active("doc")
    assert calls[0] == 0


# ---------------------------------------------------------------- no active doc


def test_no_active_doc_makes_mutations_noops():
    service = GridStateService()  # nothing ensured, nothing active
    calls, cb = _counter()
    service.on_change(cb)

    assert service.set_type("center_cross") is False
    assert service.set_param("show_center_dot", 1) is False
    assert service.set_style(width_px=2.0) is None
    assert service.toggle_visible() is False
    assert service.set_visible("x", True) is False
    assert service.select_grid("x") is False
    assert service.set_label("x", "y") is False
    assert service.remove_grid() is False
    assert service.add_grid() is None
    assert service.start_edit_session() is False
    assert service.end_edit_session() is False
    assert service.reset_transform(DOC_W, DOC_H) is None
    assert service.set_ratio_lock(False) is False
    assert service.set_rotation(90.0) is None
    assert calls[0] == 0

    # Active pointer with no (yet) ensured container behaves the same.
    service.set_active("ghost")
    assert service.selected("ghost") is None
    assert service.toggle_visible() is False
    assert service.set_type("center_cross") is False
    assert calls[0] == 1  # only the set_active pointer change


def test_an_empty_routed_document_makes_the_mutators_noop():
    """The multi-grid degradation: an ensured document with no grids answers
    every mutator with its documented no-op and never raises."""
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    calls, cb = _counter()
    service.on_change(cb)

    assert service.set_type("center_cross") is False
    assert service.set_param("show_center_dot", 1) is False
    assert service.set_style(width_px=2.0) is None
    assert service.toggle_visible() is False
    assert service.start_edit_session() is False
    assert service.reset_transform(DOC_W, DOC_H) is None
    assert service.set_ratio_lock(False) is False
    assert service.set_rotation(45.0) is None
    assert service.set_transform(TransformState(cx=1.0, cy=2.0, width=3.0, height=4.0)) is None
    assert service.remove_grid() is False
    assert service.selected() is None
    assert calls[0] == 0, "a refused mutation notifies nobody"


def test_grid_ids_are_unique_across_documents():
    service = GridStateService()
    service.ensure("a", DOC_W, DOC_H)
    service.ensure("b", DOC_W, DOC_H)
    service.set_active("a")
    in_a = service.add_grid()
    service.set_active("b")
    in_b = service.add_grid()
    assert in_a != in_b
    assert service.get(in_a, "a") is not None
    assert service.get(in_a, "b") is None, "a grid id resolves inside its own document"


# ------------------------------------------------- service as the single hub
#
# The defect this closes: the canvas drag controller writes ``model.transform``
# / ``model.params`` and calls ``model.emit()``, which only ever notified the
# model's OWN ``callbacks`` — the service (and with it the docker refresh and
# the persistence subscriber) never saw the change. The fix makes the service
# the single notification hub: every grid it owns subscribes the service's own
# ``_notify`` at creation, so one ``emit`` == one ``_notify`` == one fan-out.


def test_add_grid_subscribes_the_service_notify_to_each_model_once():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    grid_id = service.add_grid()
    model = service.get(grid_id)

    # Exactly one per-model observer: the service's own ``_notify``, installed
    # once at creation (never double-subscribed, never a per-model repaint bridge).
    assert model.callbacks == [service._notify]

    # ... and neither may any later service mutation (notify storms included).
    service.set_type("phi_grid")
    service.toggle_visible()
    service.add_grid()
    assert model.callbacks == [service._notify]


def test_model_emit_forwards_to_the_service_exactly_once():
    """The drag path's contract: one ``model.emit()`` == exactly ONE service
    notification (forwarded to the docker + persistence), never zero (the stale
    field / no-save defect) and never two (the double-notify regression)."""
    service = GridStateService()
    model = _routed(service)

    calls, cb = _counter()
    service.on_change(cb)

    model.emit()
    assert calls[0] == 1, "emit forwards to the owning service once"

    model.emit()
    assert calls[0] == 2, "each emit is exactly one notification, no accumulation"


def test_service_mutation_notifies_once_without_re_emitting_the_model():
    """The other half of "exactly one per mutation": a service-level mutation
    fires the hub ONCE and must not also call ``model.emit()`` (which, now that
    ``_notify`` rides on the model, would double-notify)."""
    service = GridStateService()
    model = _routed(service)

    emits, emit_cb = _counter()
    model.subscribe(emit_cb)  # an independent per-model observer

    calls, cb = _counter()
    service.on_change(cb)

    service.set_rotation(45.0)
    assert calls[0] == 1, "the service mutation notified the hub once"
    assert emits[0] == 0, "a service mutation must NOT route back through model.emit"


def test_no_recursion_through_the_emit_notify_bridge():
    """The forward is one-way: ``emit`` -> ``_notify`` -> subscribers; a
    subscriber that only reads state never re-triggers the chain, so a drag step
    cannot loop."""
    service = GridStateService()
    model = _routed(service)

    calls, cb = _counter()
    service.on_change(cb)

    model.emit()
    assert calls[0] == 1, "one emit -> one notify, and the notify does not re-emit"

    # A second emit is still exactly one notify (no accumulation, no loop).
    model.emit()
    assert calls[0] == 2


# ---------------------------------------------------- batched notifications


def test_batch_delivers_one_final_notification_only():
    service = GridStateService()
    calls, cb = _counter()
    service.on_change(cb)
    with service.batched_notifications():
        service.set_active("doc")
        service.ensure("doc", DOC_W, DOC_H)
        service.add_grid()
        service.set_type("uniform_grid")
        service.set_param("columns", 4)
        assert calls[0] == 0, "nothing is delivered while the batch is open"
    assert calls[0] == 1, "one final notification for the whole batch"
    model = service.selected()
    assert model.grid_type_id == "uniform_grid"
    assert model.params["columns"] == 4


def test_batch_with_no_changes_delivers_nothing():
    service = GridStateService()
    calls, cb = _counter()
    service.on_change(cb)
    with service.batched_notifications():
        service.toggle_visible()
    assert calls[0] == 0


def test_nested_batches_deliver_once_at_the_outer_exit():
    service = GridStateService()
    calls, cb = _counter()
    service.on_change(cb)
    with service.batched_notifications():
        with service.batched_notifications():
            service.set_active("doc")
            service.ensure("doc", DOC_W, DOC_H)
        assert calls[0] == 0, "the inner exit must not deliver while nested"
        service.add_grid()
    assert calls[0] == 1


def test_batch_exception_flushes_pending_and_restores_delivery():
    service = GridStateService()
    calls, cb = _counter()
    service.on_change(cb)
    with pytest.raises(RuntimeError):
        with service.batched_notifications():
            service.set_active("doc")
            raise RuntimeError("boom")
    assert calls[0] == 1, "the pending change is delivered as the batch unwinds"
    service.ensure("doc", DOC_W, DOC_H)
    assert calls[0] == 2, "delivery outside the batch is immediate again"


# ---------------------------------------------------------------- transform


def test_set_transform_routes_through_the_service():
    service = GridStateService()
    _routed(service)
    calls, cb = _counter()
    service.on_change(cb)
    wanted = TransformState(cx=10.0, cy=20.0, width=30.0, height=40.0, rotation_deg=5.0)
    assert service.set_transform(wanted) is wanted
    assert service.selected().transform == wanted
    assert calls[0] == 1
    assert service.set_transform(wanted) is wanted, "idempotent"
    assert calls[0] == 1, "an equal transform does not notify"


# ------------------------------------------------------------- install_grids


def test_install_grids_replaces_the_list_and_restores_the_selection():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    stale = service.add_grid()
    entries = [
        {
            "grid_id": "g11",
            "label": "sky",
            "grid_type_id": "uniform_grid",
            "params": {"columns": 5},
            "style": {"color_rgba": [0.1, 0.2, 0.3, 0.4], "width_px": 2.0,
                      "opacity": 0.5, "dash": None},
            "transform": {"cx": 10.0, "cy": 20.0, "width": 30.0, "height": 40.0},
            "visible": False,
        },
        {"grid_id": "g12", "label": "2", "grid_type_id": "center_cross",
         "params": {}, "style": {}, "transform": {}, "visible": True},
    ]
    calls, cb = _counter()
    service.on_change(cb)

    assert service.install_grids("doc", entries, "g11") == 2
    assert calls[0] == 1, "one notification for the whole restore"
    models = service.grids("doc")
    assert [model.grid_id for model in models] == ["g11", "g12"]
    assert [model.grid_id for model in models] != [stale]
    assert service.selected_grid_id() == "g11"
    assert models[0].label == "sky"
    assert models[0].visible is False
    assert models[0].params["columns"] == 5
    assert models[0].transform.cx == 10.0
    assert models[0].style.width_px == 2.0
    assert models[0].edit_session is False, "the edit session is never restored"


def test_install_grids_selection_unknown_falls_back_to_the_first_grid():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    service.install_grids("doc", [{"grid_id": "g1", "grid_type_id": "center_cross"}], "nope")
    assert service.selected_grid_id() == "g1"
    assert service.install_grids("doc", [{"grid_id": "g2", "grid_type_id": "center_cross"}]) == 1
    assert service.selected_grid_id() == "g2", "no stored selection -> the first grid"


def test_install_grids_generates_missing_ids_and_labels():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    service.install_grids("doc", [{"grid_type_id": "center_cross"}, {"grid_type_id": "phi_grid"}])
    models = service.grids("doc")
    assert all(model.grid_id for model in models)
    assert [model.label for model in models] == ["1", "2"]


def test_install_grids_counter_bump_keeps_later_generated_ids_clear():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    service.set_active("doc")
    service.install_grids("doc", [{"grid_id": "g40", "grid_type_id": "center_cross"}])
    assert service.add_grid() != "g40"
    assert service.grids()[1].grid_id.lstrip("g").isdigit()


def test_install_grids_skips_unusable_entries_and_keeps_the_rest(caplog):
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    with caplog.at_level(logging.WARNING):
        installed = service.install_grids(
            "doc",
            [
                {"grid_id": "g1", "grid_type_id": "no_such_grid"},
                "not a mapping",
                {"grid_id": "g2", "grid_type_id": "center_cross"},
            ],
        )
    assert installed == 1
    assert [model.grid_id for model in service.grids("doc")] == ["g2"]
    assert "no_such_grid" in caplog.text


def test_install_grids_of_an_unrouted_document_is_a_noop(caplog):
    service = GridStateService()
    calls, cb = _counter()
    service.on_change(cb)
    with caplog.at_level(logging.WARNING):
        assert service.install_grids("ghost", [{"grid_type_id": "center_cross"}]) == 0
    assert calls[0] == 0


def test_install_grids_empty_payload_changes_nothing():
    service = GridStateService()
    service.ensure("doc", DOC_W, DOC_H)
    calls, cb = _counter()
    service.on_change(cb)
    assert service.install_grids("doc", []) == 0
    assert service.grids("doc") == ()
    assert calls[0] == 0
