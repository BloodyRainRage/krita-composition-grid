"""Tests for the GridType interface (Param schema) and the grid registry."""

import pytest

import composition_grid.core.grids.grid_registry as grid_registry
from composition_grid.core.geometry import Bounds
from composition_grid.core.grids import (
    GridType,
    Param,
    all_types,
    default_params,
    get,
    register,
)
from composition_grid.core.grids.base import resolve_choice


@pytest.fixture(autouse=True)
def isolated_registry():
    """Run each test against an empty registry, restore afterwards.

    The backing store is the module-level private list in grid_registry;
    snapshotting it keeps tests independent without adding test hooks.
    """
    snapshot = list(grid_registry._REGISTRY)
    grid_registry._REGISTRY.clear()
    yield
    grid_registry._REGISTRY[:] = snapshot


def make_grid_type(
    grid_id="dummy",
    name="Dummy Grid",
    params=(),
    escapes_bounds=False,
    build_result=(),
):
    """Create a concrete GridType subclass for registry/UI tests."""

    class DummyGrid(GridType):
        def build(self, bounds, params):  # noqa: ARG002 - signature fixed by base
            self.last_bounds = bounds
            self.last_params = dict(params)
            return build_result

    DummyGrid.id = grid_id
    DummyGrid.name = name
    DummyGrid.params = params
    DummyGrid.escapes_bounds = escapes_bounds
    return DummyGrid


BOUNDS = Bounds(x=0.0, y=0.0, w=2.0, h=1.0)


class TestParam:
    def test_fields_and_defaults(self):
        p = Param(name="spacing", kind="number", default=0.5)
        assert p.name == "spacing"
        assert p.kind == "number"
        assert p.default == 0.5
        assert p.minimum is None
        assert p.maximum is None
        assert p.choices is None

    def test_frozen(self):
        p = Param(name="x", kind="bool", default=True)
        with pytest.raises(Exception):
            p.kind = "choice"  # frozen dataclass forbids attribute assignment

    def test_optional_limits(self):
        p = Param(
            name="rotation",
            kind="number",
            default=0.0,
            minimum=-360.0,
            maximum=360.0,
        )
        assert p.minimum == -360.0
        assert p.maximum == 360.0

    def test_choices(self):
        p = Param(
            name="mode",
            kind="choice",
            default="spokes",
            choices=("spokes", "rings", "both"),
        )
        assert p.choices == ("spokes", "rings", "both")


class TestResolveChoice:
    """resolve_choice: schema-default fallback, never raises for bad values."""

    def _choice_type(self):
        class Choicer(GridType):
            id = "choicer"
            name = "Choicer"
            params = (
                Param(
                    name="mode",
                    kind="choice",
                    default="spokes",
                    choices=("spokes", "rings", "both"),
                ),
            )

            def build(self, bounds, params):  # noqa: ARG002
                return ()

        return Choicer

    def test_missing_value_falls_back_to_schema_default(self):
        assert resolve_choice(self._choice_type(), {}, "mode") == "spokes"

    def test_valid_choice_passes_through(self):
        assert resolve_choice(self._choice_type(), {"mode": "rings"}, "mode") == "rings"

    def test_invalid_choice_falls_back_to_schema_default(self):
        assert resolve_choice(self._choice_type(), {"mode": "nope"}, "mode") == "spokes"


class TestGridType:
    def test_class_attribute_defaults(self):
        class Bare(GridType):
            def build(self, bounds, params):
                return ()

        assert Bare.params == ()
        assert Bare.escapes_bounds is False

    def test_cannot_instantiate_without_build(self):
        class NoBuild(GridType):
            pass

        with pytest.raises(TypeError):
            NoBuild()

    def test_build_roundtrip_via_dummy(self):
        DummyGrid = make_grid_type()
        inst = DummyGrid()
        result = inst.build(BOUNDS, {})
        assert result == ()
        assert inst.last_bounds == BOUNDS
        assert inst.last_params == {}


class TestRegistry:
    def test_register_and_get_roundtrip(self):
        DummyGrid = make_grid_type(grid_id="dummy")

        register(DummyGrid)

        assert get("dummy") is DummyGrid
        assert all_types() == (DummyGrid,)

    def test_register_same_id_twice_raises_value_error(self):
        First = make_grid_type(grid_id="dup", name="First")
        Second = make_grid_type(grid_id="dup", name="Second")

        register(First)
        with pytest.raises(ValueError):
            register(Second)
        # first registration wins
        assert get("dup") is First

    def test_all_types_preserves_registration_order(self):
        A = make_grid_type(grid_id="a")
        B = make_grid_type(grid_id="b")
        C = make_grid_type(grid_id="c")

        register(A)
        register(B)
        register(C)

        assert all_types() == (A, B, C)

    def test_get_unknown_raises_keyerror(self):
        A = make_grid_type(grid_id="a")
        register(A)

        with pytest.raises(KeyError) as excinfo:
            get("nope")
        assert "nope" in str(excinfo.value)
        assert "a" in str(excinfo.value)  # lists known ids in the message

    def test_get_empty_registry_raises_keyerror(self):
        with pytest.raises(KeyError):
            get("anything")

    def test_default_params_fills_from_param_schema(self):
        schema = (
            Param(name="density", kind="number", default=8),
            Param(name="alpha", kind="bool", default=False),
            Param(name="shape", kind="choice", default="tri", choices=("tri", "hex")),
        )
        DummyGrid = make_grid_type(grid_id="schema", params=schema)

        params = default_params(DummyGrid)

        assert params == {
            "density": 8,
            "alpha": False,
            "shape": "tri",
        }

    def test_default_params_empty_when_no_schema(self):
        DummyGrid = make_grid_type(grid_id="noschema")
        assert default_params(DummyGrid) == {}
