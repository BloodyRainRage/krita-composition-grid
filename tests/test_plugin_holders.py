"""The per-window holder registry (pure, no Qt).

``WindowHolders`` keeps strong references keyed by
the STABLE window identity, plus the one release operation that keeps the
holders bounded by the windows Krita HAS instead of the windows it has had. It
owns no canvas or grid semantics, so it is testable without Qt or libkis — this
module runs in the pure suite (and must stay error-free without PyQt).
"""

from __future__ import annotations

from composition_grid.plugin_holders import WindowHolders


class _Signal:
    """Real Qt signal semantics: slots are connected, disconnected and called."""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _Source:
    """A host object carrying two optional signals of the shape the registry
    expects."""

    def __init__(self):
        self.activeViewChanged = _Signal()
        self.destroyed = _Signal()


class _Closable:
    """A source whose attribute access raises once its C++ half is gone."""

    def __init__(self):
        self.activeViewChanged = _Signal()

    def delete(self):
        del self.activeViewChanged

    def __getattr__(self, name):
        raise RuntimeError("wrapped C/C++ object of type Window has been deleted")


def _wired():
    """Registry with TWO windows, each holding one context, two connections (the
    libkis wrapper's ``activeViewChanged`` and its qwindow's ``destroyed``, both
    recorded under the same key) and one action pair. Answers
    ``(holders, key_a, key_b, context_a, context_b)``."""
    holders = WindowHolders()
    key_a, key_b = object(), object()
    context_a, context_b = object(), object()
    for key, context, action in ((key_a, context_a, "a"), (key_b, context_b, "b")):
        holders.add_context(key, context)
        holders.connect(_Source(), "activeViewChanged", lambda *_: None, key)
        holders.connect(_Source(), "destroyed", lambda *_: None, key)
        holders.hold_actions(key, [(action, f"{action}-slot")])
    return holders, key_a, key_b, context_a, context_b


def test_everything_held_is_visible_for_introspection():
    """Nothing connected may be reachable only through the host object,
    so the registry exposes the contexts, the connections (with their owning key)
    and the action pairs."""
    holders, key_a, key_b, context_a, context_b = _wired()
    assert holders.contexts() == (context_a, context_b)
    assert holders.has_window(key_a) and holders.window_count() == 2
    assert holders.actions() == [("a", "a-slot"), ("b", "b-slot")]
    assert [entry[3] for entry in holders.connections()] == [key_a, key_a, key_b, key_b]


def test_release_drops_exactly_one_windows_share():
    """The leak fix: releasing window A must remove A's context, A's two
    connections and A's actions while leaving EVERY part of window B's state
    referenced — a prune that released the whole session would break the still
    open window, and one that kept A's partials would keep the dead window alive
    for the rest of the session."""
    holders, key_a, key_b, _context_a, context_b = _wired()

    released = holders.release(key_a)

    assert released is not None and holders.contexts() == (context_b,)
    assert not holders.has_window(key_a)
    assert [entry[3] for entry in holders.connections()] == [key_b, key_b]
    assert holders.actions() == [("b", "b-slot")]


def test_release_is_idempotent():
    """A Qt ``destroyed`` emission can arrive twice (and the plugin connects the
    prune without knowing the host's teardown order): the second release answers
    ``None`` and must not disturb the other windows."""
    holders, key_a, key_b, _context_a, context_b = _wired()
    assert holders.release(key_a) is not None
    assert holders.release(key_a) is None
    assert holders.release(key_b) is context_b
    assert holders.contexts() == ()
    assert holders.connections() == ()
    assert holders.actions() == []


def test_connect_treats_an_absent_signal_as_skipped(caplog):
    """Version drift is data, not a crash: a source without the
    optional signal is skipped with a DEBUG note, nothing is held for it, and no
    exception leaves the registry."""
    from composition_grid.plugin_holders import logger as holders_logger

    holders = WindowHolders()
    key = object()
    with caplog.at_level("DEBUG", logger="composition_grid"):
        assert holders.connect(object(), "activeViewChanged", lambda *_: None, key) is False
    assert holders.connections() == ()
    assert any(
        record.levelno == 10 and "activeViewChanged" in record.getMessage()
        for record in caplog.records
        if record.name == holders_logger.name
    )


def test_release_disconnects_the_released_window_and_spares_the_other():
    """RED (proven on the host): bookkeeping alone does not silence an emitter."""
    holders = WindowHolders()
    key_a, key_b = object(), object()
    holders.add_context(key_a, object())
    holders.add_context(key_b, object())
    source_a, source_b = _Source(), _Source()
    calls = []
    holders.connect(source_a, "activeViewChanged", lambda *_: calls.append("a"), key_a)
    holders.connect(source_b, "activeViewChanged", lambda *_: calls.append("b"), key_b)

    source_a.activeViewChanged.emit()
    assert calls == ["a"], "the fixture really holds a live connection"

    holders.release(key_a)
    holders.release(key_a)  # a destroyed emission can arrive twice

    source_a.activeViewChanged.emit()
    assert calls == ["a"], "the released window must no longer be callable"
    assert source_a.activeViewChanged.slots == [], "the emitter itself is left clean"
    source_b.activeViewChanged.emit()
    assert calls == ["a", "b"], "the other window keeps working"


def test_release_finishes_the_live_disconnects_through_a_deleted_emitter():
    """RED: one deleted source may not strand the live slots of the same key."""
    holders = WindowHolders()
    key = object()
    holders.add_context(key, object())
    dead, live = _Closable(), _Source()
    calls = []
    assert holders.connect(dead, "activeViewChanged", lambda *_: calls.append("dead"), key)
    assert holders.connect(live, "activeViewChanged", lambda *_: calls.append("live"), key)
    live.activeViewChanged.emit()
    assert calls == ["live"]
    dead.delete()

    released = holders.release(key)

    assert released is not None and holders.connections() == ()
    live.activeViewChanged.emit()
    assert calls == ["live"], "the live emitter was disconnected, not abandoned"
    assert live.activeViewChanged.slots == []


def test_connect_skips_a_source_whose_attribute_lookup_already_raises(caplog):
    """A deleted wrapper must not raise into the caller of ``connect``."""
    holders = WindowHolders()
    source = _Closable()
    source.delete()
    with caplog.at_level("DEBUG", logger="composition_grid"):
        assert holders.connect(source, "activeViewChanged", lambda *_: None, object()) is False
    assert holders.connections() == ()
