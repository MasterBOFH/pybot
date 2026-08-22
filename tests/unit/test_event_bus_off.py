"""Unit tests for EventBus.off() owner/handler removal semantics.

Regression coverage for a bug found while reviewing the hubstats module: a
module registering several handlers for the same event under one owner
must be able to remove a single handler by identity without also dropping
its other handlers that share the same owner.
"""

from __future__ import annotations

from pybot.core.events import EventBus


def test_off_with_handler_and_owner_removes_only_that_pair() -> None:
    bus = EventBus()
    kept_calls: list[str] = []
    removed_calls: list[str] = []

    def kept(**_kw: object) -> None:
        kept_calls.append("kept")

    def removed(**_kw: object) -> None:
        removed_calls.append("removed")

    bus.on("notice", kept, owner="module:hubstats")
    bus.on("notice", removed, owner="module:hubstats")

    bus.off("notice", removed, owner="module:hubstats")

    remaining = bus._handlers.get("notice", [])
    assert remaining == [("module:hubstats", kept)]


def test_off_with_only_handler_removes_by_identity_regardless_of_owner() -> None:
    bus = EventBus()

    def h(**_kw: object) -> None:
        pass

    bus.on("notice", h, owner="module:a")
    bus.off("notice", h)

    assert "notice" not in bus._handlers


def test_off_with_only_owner_removes_every_handler_for_that_owner() -> None:
    bus = EventBus()

    def h1(**_kw: object) -> None:
        pass

    def h2(**_kw: object) -> None:
        pass

    bus.on("notice", h1, owner="module:a")
    bus.on("notice", h2, owner="module:a")
    bus.on("notice", h1, owner="module:b")

    bus.off("notice", owner="module:a")

    remaining = bus._handlers.get("notice", [])
    assert remaining == [("module:b", h1)]


def test_off_with_neither_arg_clears_the_whole_event() -> None:
    bus = EventBus()

    def h(**_kw: object) -> None:
        pass

    bus.on("notice", h, owner="module:a")
    bus.off("notice")

    assert "notice" not in bus._handlers


def test_off_unknown_event_is_a_noop() -> None:
    bus = EventBus()
    bus.off("nonexistent", owner="module:a")  # must not raise
