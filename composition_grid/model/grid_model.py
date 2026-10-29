"""GridModel + GridStateService: the per-document multi-grid state.

Model layer: no Qt, no krita — stdlib plus the plugin's own pure core only.

A document owns an ORDERED LIST of :class:`GridModel` grids plus the id of the
one SELECTED grid (or ``None`` when the document has no grids). Each grid
carries its own type, parameter values, style, transformable element,
visibility and edit session, a stable ``grid_id`` (unique within the document,
persisted) and a human ``label``. Grids of different types coexist and are all
painted at once; the selection decides which grid the docker, the actions, the
transform controller and the canvas handles operate on.

A freshly created or freshly opened document has NO grid: :meth:`
GridStateService.ensure` only creates the empty container, and the first grid
appears through the explicit user command :meth:`GridStateService.add_grid`
(seed from the global "last used" settings) or through the restore path
:meth:`GridStateService.install_grids` (a document's own stored annotation).

All mutations route through the service, which is the SINGLE change-notification
hub: every grid subscribes the owning service's ``_notify`` at creation, so a
service mutation and a canvas drag (``TransformController.drag_to`` writing the
model and calling :meth:`GridModel.emit`) produce exactly one fan-out to the
repaint, docker and persistence subscribers.

Mutators address the SELECTED grid of the ACTIVE document (that is what keeps
the transform controller, the actions and the docker working unchanged); with
no active document or no selected grid every mutator degrades to its documented
``False``/``None`` no-op and never raises.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Tuple

from composition_grid.core.grids.base import (
    GridType,
    Param,
    resolve_bool,
    resolve_choice,
    resolve_int,
    resolve_number,
)
from composition_grid.core.grids.grid_registry import all_types, default_params, get
from composition_grid.core.styles import Style
from composition_grid.core.transform_state import (
    TransformState,
    def_element,
    normalize_angle_degrees,
)

logger = logging.getLogger(__name__)


@dataclass
class GridModel:
    """Mutable state of ONE grid of one document."""

    grid_type_id: str
    params: Dict[str, object]
    style: Style
    transform: TransformState
    visible: bool
    edit_session: bool
    # Stable identity within the document, persisted with the grid. Assigned by
    # the service when left empty here, so callers constructing a model by hand
    # (tests, restore entries without an id) still get a usable one.
    grid_id: str = ""
    # Display name, persisted; "" means the service numbers it on creation.
    label: str = ""
    # Observer hooks for direct per-model notifications; not part of the
    # model's *state* identity (excluded from repr/eq).
    callbacks: List[Callable[[], None]] = field(
        default_factory=list, repr=False, compare=False
    )

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Register ``callback`` to be invoked by :meth:`emit`.

        In production the owning :class:`GridStateService` subscribes its own
        ``_notify`` exactly once, at grid creation. There is no unsubscribe, so
        callers must never re-subscribe a model they already own — a second
        subscription would double-notify every emit."""
        self.callbacks.append(callback)

    def emit(self) -> None:
        """Notify every subscribed callback (one call per emit).

        This is the canvas drag path's mutation signal: the
        :class:`~composition_grid.ui.transform_controller.TransformController`
        writes ``transform``/``params`` and calls ``emit()``, which forwards to
        the owning service's ``_notify`` (the single hub) — so one drag step is
        one notification, reaching the overlay repaint, the docker refresh and
        the persistence subscriber together."""
        for callback in list(self.callbacks):
            callback()


def _default_first_element(doc_w: float, doc_h: float) -> TransformState:
    """Default ``first_geometry_factory`` body: the doc-centered def element."""
    return def_element(None, doc_w, doc_h)


def _schema_param(cls: type[GridType], name: str) -> Param:
    """The schema :class:`Param` named ``name``, or the shared ValueError listing
    what the type does declare (the one error source of the param setters)."""
    param = next((p for p in cls.params if p.name == name), None)
    if param is None:
        known = ", ".join(p.name for p in cls.params) or "<no parameters>"
        raise ValueError(f"unknown parameter {name!r} for {cls.id!r}; known: {known}")
    return param


def _coerce_param(
    cls: type[GridType], param: Param, name: str, value: object
) -> object:
    """Coerce ``value`` per the target type's schema using the base resolvers.

    Dispatch on the :class:`Param` kind:
    - ``"bool"``: :func:`resolve_bool` truthiness (invalid values fall back
      to the schema default — the sanctioned rule).
    - ``"choice"``: :func:`resolve_choice` membership check with schema
      default fallback.
    - ``"number"``: :func:`resolve_int` when the schema default is an int
      (whole-count parameters like columns/rows), :func:`resolve_number`
      otherwise; both clamp to the optional minimum/maximum.
    """
    one_value = {name: value}
    if param.kind == "bool":
        return resolve_bool(cls, one_value, name)
    if param.kind == "choice":
        return resolve_choice(cls, one_value, name)
    if isinstance(param.default, int) and not isinstance(param.default, bool):
        return resolve_int(cls, one_value, name)
    return resolve_number(cls, one_value, name)


_SEED_FIELDS = ("grid_type_id", "params", "style")

_GRID_ID_SUFFIX = re.compile(r"(\d+)$")


def _default_seed() -> Dict[str, object]:
    """The no-store seed: first registered type, its schema defaults, plain style."""
    first_type = all_types()[0]  # registration order: rule_of_thirds first
    return {
        "grid_type_id": first_type.id,
        "params": default_params(first_type),
        "style": Style(),
    }


class GridStateService:
    """Per-document ordered grids + selection, and the SINGLE notification hub.

    The service owns one grid LIST (and one selected id) per open document and
    an "active document" pointer. All value mutations route through the
    *selected* grid of the *active* document; the grid-level commands
    (:meth:`add_grid`, :meth:`remove_grid`, :meth:`select_grid`,
    :meth:`set_label`, :meth:`set_visible`) address a grid by id, defaulting to
    the selection. With no active document (or an active pointer whose
    container was not ensured yet, or no selection) the mutations are safe
    no-ops. Every actual state mutation notifies the :meth:`on_change`
    subscribers exactly once.
    """

    def __init__(
        self,
        first_geometry_factory: Callable[[float, float], TransformState] | None = None,
        seed_provider: Callable[[], Dict[str, object] | None] | None = None,
    ) -> None:
        """``first_geometry_factory`` produces the default element geometry.

        Signature ``(doc_w, doc_h) -> TransformState``; it defaults to a
        document-centered square built by :func:`def_element`.

        ``seed_provider`` supplies the values a NEW grid starts from (the
        global "last used" settings, read as ``{"grid_type_id", "params",
        "style"}`` — any field may be missing or ``None``). It is consulted by
        :meth:`add_grid` only; without it (or when it answers nothing usable) a
        new grid takes the registry defaults. The provider is injected rather
        than imported because the kritarc store lives on the plugin side.
        """
        if first_geometry_factory is None:
            first_geometry_factory = _default_first_element
        self._first_geometry_factory = first_geometry_factory
        self._seed_provider = seed_provider
        self._grids: Dict[str, List[GridModel]] = {}
        self._selected: Dict[str, str | None] = {}
        # The extent each document was last ensured with: the default element of
        # a NEW grid is built from it (the canvas drag path has no size argument).
        self._doc_sizes: Dict[str, "tuple[float, float]"] = {}
        self._id_counter = 0
        self._active_doc_id: str | None = None
        self._listeners: List[Callable[[], None]] = []
        self._batch_depth = 0
        self._batch_pending = False

    # ------------------------------------------------------------ Look up

    def set_seed_provider(
        self, seed_provider: Callable[[], Dict[str, object] | None] | None
    ) -> None:
        """Install the "last used" seed provider after construction.

        The docker seam's service is a process-wide singleton created before the
        plugin entry knows its settings store, so the entry wires the provider
        here. An already installed provider is kept unless ``force``: calling
        this twice with two providers would silently re-point the seed source,
        so the FIRST one wins and the second is a logged no-op.
        """
        if seed_provider is None or self._seed_provider is not None:
            if seed_provider is not None and self._seed_provider is not None:
                logger.debug("model: a seed provider is already installed; keeping it")
            return
        self._seed_provider = seed_provider

    @property
    def active_doc_id(self) -> str | None:
        """The active document id, or ``None`` before any activation."""
        return self._active_doc_id

    def is_routed(self, doc_id: str) -> bool:
        """``True`` when this document's container exists (grid list or not)."""
        return doc_id in self._grids

    def grids(self, doc_id: str | None = None) -> Tuple[GridModel, ...]:
        """The active (or given) document's grids, in list order.

        An unknown or not-yet-ensured document answers the empty tuple, as does
        a document whose grids were all removed."""
        if doc_id is None:
            doc_id = self._active_doc_id
        if doc_id is None:
            return ()
        return tuple(self._grids.get(doc_id, ()))

    def selected_grid_id(self, doc_id: str | None = None) -> str | None:
        """The selected grid id of the active (or given) document, else ``None``."""
        if doc_id is None:
            doc_id = self._active_doc_id
        if doc_id is None:
            return None
        return self._selected.get(doc_id)

    def get(self, grid_id: str, doc_id: str | None = None) -> GridModel | None:
        """One grid by id within the active (or given) document; ``None`` if absent."""
        for model in self.grids(doc_id):
            if model.grid_id == grid_id:
                return model
        return None

    def selected(self, doc_id: str | None = None) -> GridModel | None:
        """The selected grid object, or ``None`` without a routed selection."""
        grid_id = self.selected_grid_id(doc_id)
        if grid_id is None:
            return None
        return self.get(grid_id, doc_id)

    def _mutable_model(self) -> GridModel | None:
        """The selected grid when user mutations may land on it.

        Every value mutation degrades to its documented no-op answer through
        this gate: no active document, no ensured container or no selected grid
        all mean "nothing to mutate"."""
        return self.selected()

    # --------------------------------------------------------- Doc routing

    def ensure(self, doc_id: str, doc_w: float, doc_h: float) -> Tuple[GridModel, ...]:
        """The document's grid list — created EMPTY on the first call.

        A document gets NO grid from routing: the artist adds one explicitly,
        or the restore path fills the container from the document's own
        annotation. ``doc_w``/``doc_h`` are remembered as this document's
        extent: the default element of a grid added later is built from it,
        because the canvas paths carry no size argument. An already routed
        document only refreshes that remembered size and is returned untouched
        — no notification at all.
        """
        self._doc_sizes[doc_id] = (doc_w, doc_h)
        existing = self._grids.get(doc_id)
        if existing is not None:
            return tuple(existing)
        self._grids[doc_id] = []
        self._selected.setdefault(doc_id, None)
        self._notify()
        return ()

    def set_active(self, doc_id: str) -> None:
        """Point ``doc_id`` as active; it may precede its :meth:`ensure`.

        Only the pointer switches (a later ensure fills the container); the
        pointer change itself notifies exactly once, redundantly not at all.
        """
        if self._active_doc_id != doc_id:
            self._active_doc_id = doc_id
            self._notify()

    def remove(self, doc_id: str) -> None:
        """Drop ``doc_id``'s grids and selection; notify once when something was dropped.

        When the removed document was active, ``active_doc_id`` becomes
        ``None`` (the caller chooses the next active document himself).
        """
        if doc_id not in self._grids:
            return
        del self._grids[doc_id]
        self._selected.pop(doc_id, None)
        if self._active_doc_id == doc_id:
            self._active_doc_id = None
        self._notify()

    # -------------------------------------------------------- Grid commands

    def new_grid_id(self) -> str:
        """A fresh grid id, unique across this service's documents.

        Ids persist in a document's annotation, so they only ever need to be
        unique *within* a document; the service-wide counter keeps them unique
        everywhere, which also makes a copied list self-consistent. The
        :meth:`install_grids` counter bump keeps generated ids clear of the
        restored ones."""
        self._id_counter += 1
        return f"g{self._id_counter}"

    def _bump_id_counter(self, grid_id: str) -> None:
        """Keep generated ids clear of a restored ``grid_id`` (its numeric suffix)."""
        match = _GRID_ID_SUFFIX.search(grid_id or "")
        if match is None:
            return
        try:
            self._id_counter = max(self._id_counter, int(match.group(1)))
        except ValueError:  # pragma: no cover — the regex already pinned the digits
            pass

    def _next_label(self, models: List[GridModel]) -> str:
        """The smallest integer label ``>= len(models) + 1`` that no grid in the
        document carries.

        Examples: no grids -> ``"1"``; ``["1", "2"]`` -> ``"3"``; ``["1", "3"]``
        -> 3 is taken, so ``"4"``; ``["1", "2", "sky"]`` -> ``"4"``. A user
        label may duplicate another grid's — only generated ones avoid clashes."""
        taken = {model.label for model in models}
        candidate = len(models) + 1
        while str(candidate) in taken:
            candidate += 1
        return str(candidate)

    def _new_model(self, seed: Dict[str, object] | None = None) -> GridModel:
        """Build one seeded grid (not yet inserted): ``_notify`` subscribed at
        creation, so its ``emit`` (the drag path) is a service notification."""
        prepared = self._prepare_seed(seed)
        model = GridModel(
            grid_type_id=prepared["grid_type_id"],
            params=prepared["params"],
            style=prepared["style"],
            transform=self._default_element(),
            visible=True,
            edit_session=False,
            grid_id=self.new_grid_id(),
            label="",
        )
        model.subscribe(self._notify)
        return model

    def _default_element(self) -> TransformState:
        """The default element for the size the last :meth:`ensure` saw."""
        doc_w, doc_h = self._last_doc_size()
        return self._first_geometry_factory(doc_w, doc_h)

    def _last_doc_size(self) -> "tuple[float, float]":
        """The size remembered per document by :meth:`ensure` (a new grid takes
        the active document's element); ``0, 0`` makes the factory degrade to
        its own default rather than inventing a placement."""
        return self._doc_sizes.get(self._active_doc_id or "", (0.0, 0.0))

    def _prepare_seed(self, seed: Dict[str, object] | None) -> Dict[str, object]:
        """Normalize a seed into real ``grid_type_id`` / ``params`` / ``style``.

        Each field may be missing or of the wrong shape (a store written by
        another build); such a field falls back to its registry default, and an
        unregistered or invalid type id falls back to the whole default seed.
        Parameters are coerced through the target type's schema, so a stored
        value outside the schema simply becomes that parameter's default."""
        fallback = _default_seed()
        if not isinstance(seed, dict):
            return fallback
        grid_type_id = seed.get("grid_type_id")
        if not isinstance(grid_type_id, str):
            return fallback
        try:
            cls = get(grid_type_id)
        except KeyError:
            logger.warning(
                "model: seed grid type %r is not registered; using the defaults",
                grid_type_id,
            )
            return fallback
        params = default_params(cls)
        stored = seed.get("params")
        if isinstance(stored, dict):
            for param in cls.params:
                if param.name in stored:
                    params[param.name] = _coerce_param(
                        cls, param, param.name, stored[param.name]
                    )
        style = seed.get("style")
        if isinstance(style, Style):
            resolved_style = style
        elif isinstance(style, dict):
            try:
                resolved_style = Style(**style)
            except (TypeError, ValueError) as exc:
                logger.warning("model: seed style %r unusable: %r", style, exc)
                resolved_style = Style()
        else:
            resolved_style = Style()
        return {"grid_type_id": grid_type_id, "params": params, "style": resolved_style}

    def _seed_from_store(self) -> Dict[str, object] | None:
        """The injected store's seed, or ``None`` (and a log) when it fails."""
        if self._seed_provider is None:
            return None
        try:
            return self._seed_provider()
        except Exception as exc:  # noqa: BLE001 — a broken store never blocks a grid
            logger.warning("model: the seed provider failed: %r; using the defaults", exc)
            return None

    def add_grid(
        self, seed: Dict[str, object] | None = None
    ) -> str | None:
        """Add a NEW grid to the active document and select it; answers its id.

        The grid is seeded from ``seed`` when given, else from the injected
        "last used" provider, else from the registry defaults; it gets the
        default document-centered element, ``visible=True`` and no edit
        session, and its label is the next free number (see :meth:`_next_label`).
        Any edit session on the document's other grids ends with the addition —
        one grid is edited on the canvas at a time. Without an active document
        (or a container that was never ensured) nothing is created and
        ``None`` is answered. One notification for the whole addition.
        """
        if self._active_doc_id is None:
            return None
        models = self._grids.get(self._active_doc_id)
        if models is None:
            return None
        for model in models:
            model.edit_session = False
        model = self._new_model(seed if seed is not None else self._seed_from_store())
        model.label = self._next_label(models)
        models.append(model)
        self._selected[self._active_doc_id] = model.grid_id
        self._notify()
        return model.grid_id

    def remove_grid(self, grid_id: str | None = None) -> bool:
        """Delete one grid (the selected one by default); ``True`` when removed.

        Removal DELETES the grid — there is no "removed but remembered" flag
        any more; the document simply keeps the grids that stay. When the
        removed grid was the selected one, the selection moves to the next grid
        in the list, else to the previous one, else becomes ``None``; removing
        another grid leaves the selection where it is. One notification either
        way is not sent when nothing was removed.
        """
        if grid_id is None:
            grid_id = self.selected_grid_id()
            if grid_id is None:
                return False
        doc_id = self._doc_of_grid(grid_id)
        if doc_id is None:
            return False
        models = self._grids[doc_id]
        index = next(
            (i for i, model in enumerate(models) if model.grid_id == grid_id), None
        )
        if index is None:
            return False
        was_selected = self._selected.get(doc_id) == grid_id
        models.pop(index)
        if was_selected:
            following = models[index : index + 1] or models[index - 1 : index]
            self._selected[doc_id] = following[0].grid_id if following else None
        self._notify()
        return True

    def _doc_of_grid(self, grid_id: str | None) -> str | None:
        """The document owning ``grid_id``; the ACTIVE document when it is
        ``None`` and no grid is selected there (i.e. the default target)."""
        if grid_id is None:
            grid_id = self.selected_grid_id()
        if grid_id is None:
            return None
        for doc_id, models in self._grids.items():
            if any(model.grid_id == grid_id for model in models):
                return doc_id
        logger.debug("model: no grid %r in any document", grid_id)
        return None

    def select_grid(self, grid_id: str) -> bool:
        """Make ``grid_id`` the selected grid of its document; ``True`` when changed.

        An edit session on the previously selected grid ends (the canvas edits
        one grid at a time). Unknown or ``None`` id: a ``False`` no-op;
        selecting the already selected grid changes nothing and notifies nobody.
        """
        if not grid_id:
            return False
        doc_id = self._doc_of_grid(grid_id)
        if doc_id is None:
            return False
        previous = self._selected.get(doc_id)
        if previous == grid_id:
            return False
        for model in self._grids[doc_id]:
            if model.grid_id == previous:
                model.edit_session = False
        self._selected[doc_id] = grid_id
        self._notify()
        return True

    def set_label(self, grid_id: str, text: str) -> bool:
        """Rename one grid; ``True`` when the label changed.

        Whitespace is stripped and an empty result keeps the old label (a user
        cannot blank a label). Duplicate labels are allowed — the ids, not the
        labels, identify a grid. Unknown id: ``False`` no-op."""
        model = self._find_grid(grid_id)
        if model is None:
            return False
        wanted = (text or "").strip()
        if not wanted or model.label == wanted:
            return False
        model.label = wanted
        self._notify()
        return True

    def set_visible(self, grid_id: str, visible: bool) -> bool:
        """Per-grid visibility (the selected grid for :meth:`toggle_visible`);
        ``True`` when this changed the flag."""
        model = self._find_grid(grid_id)
        if model is None:
            return False
        want = bool(visible)
        if model.visible == want:
            return False
        model.visible = want
        self._notify()
        return True

    def _find_grid(self, grid_id: str) -> GridModel | None:
        """A grid by id in the document that owns it (any document — ids are
        service-wide unique), or ``None``."""
        if not grid_id:
            return None
        for models in self._grids.values():
            for model in models:
                if model.grid_id == grid_id:
                    return model
        return None

    def install_grids(
        self,
        doc_id: str,
        entries,
        selected_grid_id: str | None = None,
    ) -> int:
        """Replace the document's grid list with restored ``entries`` (the
        annotation-restore route; ``plugin_settings`` owns the payload policy).

        ``entries`` is a sequence of ``{"grid_id", "label", "grid_type_id",
        "params", "style", "transform", "visible"}`` mappings in list order; an
        entry whose grid type is not registered or whose style/transform shape
        is unusable is skipped with a WARNING, the rest still come back. Missing
        ids/labels are regenerated, and a stored id always wins over a generated
        one, so a document keeps its own grid identities. ``selected_grid_id``
        restores the selection when it names a restored grid, else falls back to
        the first installed grid (or ``None`` for an empty list). Answers the
        number of installed grids; ONE notification for the whole restore,
        nothing emitted when the payload installs nothing.
        """
        if doc_id not in self._grids:
            logger.warning("model: document %r is not routed; nothing to restore", doc_id)
            return 0
        models: List[GridModel] = []
        for entry in entries or ():
            model = self._restored_model(entry)
            if model is None:
                continue
            models.append(model)
        if not models:
            return 0
        for model in models:
            self._bump_id_counter(model.grid_id)
        labels = {model.label for model in models}
        for model in models:
            if not model.label:
                model.label = self._unique_restored_label(models, labels)
                labels.add(model.label)
        self._grids[doc_id] = models
        first = models[0].grid_id
        chosen = selected_grid_id if any(m.grid_id == selected_grid_id for m in models) else first
        self._selected[doc_id] = chosen
        self._notify()
        return len(models)

    def _restored_model(self, entry) -> GridModel | None:
        """One validated restore entry into a live grid (transient edit
        session off); ``None`` when the entry cannot be used."""
        if not isinstance(entry, dict):
            logger.warning("model: restored grid entry %r is not a mapping; skipped", entry)
            return None
        stored_type = entry.get("grid_type_id")
        if not isinstance(stored_type, str) or stored_type not in {
            cls.id for cls in all_types()
        }:
            logger.warning(
                "model: restored grid type %r is not registered; the grid is skipped",
                stored_type,
            )
            return None
        prepared = self._prepare_seed(entry)
        grid_type_id = prepared["grid_type_id"]
        if grid_type_id is None or not isinstance(grid_type_id, str):
            return None
        stored_id = entry.get("grid_id")
        transform = entry.get("transform")
        if isinstance(transform, TransformState):
            element = transform
        elif isinstance(transform, dict):
            try:
                element = TransformState(**transform)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "model: restored transform %r unusable: %r; using the default element",
                    transform,
                    exc,
                )
                element = self._default_element()
        else:
            element = self._default_element()
        model = GridModel(
            grid_type_id=grid_type_id,
            params=prepared["params"],
            style=prepared["style"],
            transform=element,
            visible=bool(entry.get("visible", True)),
            edit_session=False,
            grid_id=stored_id if isinstance(stored_id, str) and stored_id else "",
            label=(entry.get("label") or "") if isinstance(entry.get("label"), str) else "",
        )
        if not model.grid_id:
            model.grid_id = self.new_grid_id()
        model.subscribe(self._notify)
        return model

    def _unique_restored_label(self, models: List[GridModel], taken: set) -> str:
        """A numeric label for a restored grid that has none (a version-1
        document's single grid gets ``"1"``), avoiding clashes inside the list.

        A restore numbers from 1 rather than from the list length: the stored
        labels are the artist's own, and a missing one is filled with the first
        number still free."""
        candidate = 1
        while str(candidate) in taken:
            candidate += 1
        return str(candidate)

    # ---------------------------------------------------- Active mutations

    def set_type(self, grid_id: str) -> bool:
        """Switch the SELECTED grid's type; ``True`` when changed.

        The transform and style are kept; ``params`` are replaced by the
        chosen type's schema defaults. Same id: idempotent (no notify).
        Unknown id: :class:`ValueError`. No selected grid: ``False`` no-op.
        """
        model = self._mutable_model()
        if model is None:
            return False
        if grid_id == model.grid_type_id:
            return False
        try:
            cls = get(grid_id)
        except KeyError as exc:
            raise ValueError(f"unknown grid id: {grid_id!r}") from exc
        model.grid_type_id = grid_id
        model.params = default_params(cls)
        self._notify()
        return True

    def set_param(self, name: str, value: object) -> bool:
        """Set one grid parameter on the SELECTED grid; ``True`` when changed.

        The value is coerced through the target type's schema (see
        :func:`_coerce_param`). Unknown parameter name: :class:`ValueError`;
        a value the resolvers deem invalid falls back to the schema default
        (their pinned rule). A coerced value equal to the current one does
        not notify.
        """
        model = self._mutable_model()
        if model is None:
            return False
        cls = get(model.grid_type_id)
        param = _schema_param(cls, name)
        coerced = _coerce_param(cls, param, name, value)
        if model.params.get(name) == coerced:
            return False
        model.params[name] = coerced
        self._notify()
        return True

    def reset_param(self, name: str) -> bool:
        """Restore ONE parameter of the SELECTED grid to its schema default.

        The write routes through :meth:`set_param`, so a reset is an ordinary
        value change: same coercion rule, one notification, and the persistence
        subscribers that follow it. Unknown parameter name:
        :class:`ValueError`; already at the default, or no selected grid: a
        ``False`` no-op that notifies nobody.
        """
        model = self._mutable_model()
        if model is None:
            return False
        param = _schema_param(get(model.grid_type_id), name)
        return self.set_param(name, param.default)

    def reset_params(self) -> bool:
        """Restore EVERY parameter of the SELECTED grid to the schema defaults.

        ``params`` is replaced by :func:`default_params` of the grid's own type
        (the same seed :meth:`set_type` installs); style, transform and
        visibility are untouched. ONE notification for the whole reset; a grid
        already entirely at its defaults, a type with no parameters and the
        no-selection case all answer ``False`` and notify nobody.
        """
        model = self._mutable_model()
        if model is None:
            return False
        defaults = default_params(get(model.grid_type_id))
        if all(model.params.get(name) == value for name, value in defaults.items()):
            return False
        model.params = defaults
        self._notify()
        return True

    def set_style(
        self, style: Style | None = None, **patch: object
    ) -> Style | None:
        """Patch the SELECTED grid's style (or adopt ``style`` wholesale).

        A new :class:`Style` is built from the current one with the patched
        fields replaced (``Style`` is frozen); its ``__post_init__`` clamps
        the values. Returns the resulting style, or ``None`` when there is
        nothing selected to edit.
        """
        model = self._mutable_model()
        if model is None:
            return None
        base = style if style is not None else model.style
        new_style = replace(base, **patch) if patch else base
        if new_style == model.style:
            return model.style
        model.style = new_style
        self._notify()
        return new_style

    def toggle_visible(self) -> bool:
        """Flip the SELECTED grid's visibility; ``False`` without a selection."""
        model = self._mutable_model()
        if model is None:
            return False
        model.visible = not model.visible
        self._notify()
        return model.visible

    def start_edit_session(self) -> bool:
        """Turn the SELECTED grid's edit-session flag on; ``True`` when changed.

        The document's other grids lose theirs: exactly one grid is editable on
        the canvas at a time, and that grid is the one the overlay's handles
        belong to.
        """
        model = self._mutable_model()
        if model is None or model.edit_session:
            return False
        for other in self.grids():
            if other is not model:
                other.edit_session = False
        model.edit_session = True
        self._notify()
        return True

    def end_edit_session(self, doc_id: str | None = None) -> bool:
        """End *doc_id*'s edit session (the active document when the arg is omitted).

        Every grid of that document loses the flag — the transient "editing on
        the canvas" state belongs to the document's overlay, not to a grid the
        artist happens to have selected.
        """
        doc_id = doc_id if doc_id is not None else self._active_doc_id
        if doc_id is None:
            return False
        changed = False
        for model in self._grids.get(doc_id, ()):
            if model.edit_session:
                model.edit_session = False
                changed = True
        if not changed:
            return False
        self._notify()
        return True

    def reset_transform(self, doc_w: float, doc_h: float) -> TransformState | None:
        """Reset the SELECTED grid's element to the default for this size."""
        model = self._mutable_model()
        if model is None:
            return None
        model.transform = self._first_geometry_factory(doc_w, doc_h)
        self._notify()
        return model.transform

    def set_ratio_lock(self, checked: bool) -> bool:
        """Set the ratio-lock flag on the selected element; ``True`` when changed.

        The service owns the mutation and the notify (the docker
        never writes ``model.transform`` itself), so a lock/unlock click also
        triggers the persistence subscriber. Idempotent: an equal flag does not
        notify. Without a selected grid: a ``False`` no-op.
        """
        model = self._mutable_model()
        if model is None:
            return False
        want = bool(checked)
        if model.transform.ratio_locked == want:
            return False
        model.transform = replace(model.transform, ratio_locked=want)
        self._notify()
        return True

    def set_rotation(self, rotation_deg: float) -> TransformState | None:
        """Set the selected element's absolute rotation; normalize to (-180, 180].

        Mirrors :meth:`reset_transform`'s shape (mutate + ``_notify`` and
        return the resulting state, or ``None`` without a selected grid).
        The angle is wrapped through the single source of truth
        :func:`composition_grid.core.transform_state.normalize_angle_degrees`
        — the SAME rule the canvas rotate handle uses — so the docker field and
        the drag can never disagree. Idempotent: an angle that normalizes to
        the stored value does not notify.
        """
        model = self._mutable_model()
        if model is None:
            return None
        wrapped = normalize_angle_degrees(rotation_deg)
        if model.transform.rotation_deg == wrapped:
            return model.transform
        model.transform = replace(model.transform, rotation_deg=wrapped)
        self._notify()
        return model.transform

    def set_transform(self, transform: TransformState) -> TransformState | None:
        """Replace the selected element wholesale; ``None`` without a selected grid.

        The wholesale counterpart of :meth:`set_rotation` (same mutate + notify
        + return-the-result shape); idempotent like the other setters.
        """
        model = self._mutable_model()
        if model is None:
            return None
        if model.transform == transform:
            return model.transform
        model.transform = transform
        self._notify()
        return model.transform

    # ------------------------------------------------------- Notification

    def on_change(self, callback: Callable[[], None]) -> None:
        """Subscribe ``callback``; every state mutation notifies it once."""
        self._listeners.append(callback)

    @contextmanager
    def batched_notifications(self):
        """Defer notification delivery until the outermost batch exits (one final)."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._batch_pending:
                self._batch_pending = False
                self._notify()

    def _notify(self) -> None:
        """Call each subscriber once; while batching, the call is deferred."""
        if self._batch_depth > 0:
            self._batch_pending = True
            return
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                logger.exception("grid state change callback failed")
