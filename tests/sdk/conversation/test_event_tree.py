"""Unit tests for the EventLog tree helpers and back-compat rule (#3747)."""

import pytest
from pydantic import ValidationError

from openhands.sdk.conversation.event_store import ROOT_PARENT_ID, EventLog
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import Message, TextContent


def test_event_id_cannot_equal_reserved_root_sentinel():
    """No event may take the reserved ROOT_PARENT_ID as its id, else its children
    would be read as parentless (parent_id == ROOT_PARENT_ID means "root")."""
    with pytest.raises(ValidationError):
        _event(ROOT_PARENT_ID)


def _event(event_id: str, parent_id: str | None = None) -> MessageEvent:
    return MessageEvent(
        id=event_id,
        parent_id=parent_id,
        llm_message=Message(role="user", content=[TextContent(text=event_id)]),
        source="user",
    )


def _log(*events: MessageEvent) -> EventLog:
    log = EventLog(InMemoryFileStore())
    for event in events:
        log.append(event)
    return log


def _branched_log() -> EventLog:
    """Shared tree:  a -> b -> c   and   a -> d -> e  (b/d are siblings)."""
    return _log(
        _event("a"),
        _event("b", parent_id="a"),
        _event("c", parent_id="b"),
        _event("d", parent_id="a"),
        _event("e", parent_id="d"),
    )


@pytest.mark.parametrize("as_event", [False, True], ids=["by-id", "by-event"])
@pytest.mark.parametrize("target, expected", [("a", True), ("missing", False)])
def test_contains_accepts_event_id_or_event(as_event, target, expected):
    log = _log(_event("a"))
    item = _event(target) if as_event else target
    assert (item in log) is expected


@pytest.mark.parametrize(
    "leaf, expected",
    [
        (None, []),
        ("a", ["a"]),
        ("c", ["a", "b", "c"]),
        ("e", ["a", "d", "e"]),  # the b->c sibling branch is excluded
    ],
)
def test_path_to_root(leaf, expected):
    assert [e.id for e in _branched_log().path_to_root(leaf)] == expected


def test_path_to_root_cycle_raises():
    # a -> b -> a : a malformed cycle must be detected, not loop forever.
    log = _log(_event("a", parent_id="b"), _event("b", parent_id="a"))
    with pytest.raises(ValueError, match="Cycle in event tree"):
        log.path_to_root("a")


def test_path_to_root_unknown_leaf_raises():
    with pytest.raises(KeyError):
        _log(_event("a")).path_to_root("nope")


def test_legacy_events_form_a_single_linear_branch():
    """Events without parent_id resolve to the linear idx chain (no rewrite)."""
    # All parent_id default to None -> the effective-parent rule walks idx-1.
    log = _log(_event("a"), _event("b"), _event("c"))

    assert [e.id for e in log.path_to_root("c")] == ["a", "b", "c"]


@pytest.mark.parametrize(
    "idx, expected",
    [
        (0, None),  # genuine root
        (1, "a"),  # legacy linear chain (idx - 1)
        (2, "a"),  # explicit parent wins over idx - 1
    ],
)
def test_effective_parent_id_rule(idx, expected):
    # idx 0, 1 are legacy (no explicit parent); idx 2 carries parent_id="a".
    log = _log(_event("a"), _event("b"), _event("c", parent_id="a"))
    assert log._effective_parent_id(idx, log[idx]) == expected
