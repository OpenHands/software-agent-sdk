"""Unit tests for the EventLog tree helpers and back-compat rule (#3747)."""

import json
import logging

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


# Reloading a store from disk re-indexes by filename, and EVENT_NAME_RE only
# accepts hex-ish ids, so the #4080 tests below need id-shaped ids.
A, B, C, D, E = "aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd", "eeeeeeee"


def _unregistered_payload(event_id: str, parent_id: str | None = None) -> str:
    """A stored event whose ``kind`` is not registered in this process (#4080).

    What a custom tool's observation looks like on disk when the module that
    defines it was never imported, or an event written by a newer version.
    """
    payload: dict[str, object] = {"kind": "CanvasUIObservation", "id": event_id}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    return json.dumps(payload)


def _store(*events: MessageEvent) -> InMemoryFileStore:
    """Persist ``events``, then hand back the bare store for a cold reload."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    for event in events:
        log.append(event)
    return fs


def test_path_to_root_skips_event_with_unregistered_kind():
    """One unloadable event must not strand the branch it sits on (#4080)."""
    fs = _store(_event(A), _event(B, parent_id=A), _event(C, parent_id=B))
    fs.write(f"events/event-00001-{B}.json", _unregistered_payload(B, parent_id=A))

    # A cold load, so nothing is served from the in-process event cache.
    log = EventLog(fs)

    # B is skipped, and A is still reached, so the walk stepped *through* B.
    assert [e.id for e in log.path_to_root(C)] == [A, C]


def test_path_to_root_stays_on_its_branch_across_an_unreadable_event():
    """Skipping must follow the stored parent_id, not drift onto a sibling branch."""
    # A -> B -> C  and  A -> D -> E ; D is unreadable but still points at A.
    fs = _store(
        _event(A),
        _event(B, parent_id=A),
        _event(C, parent_id=B),
        _event(D, parent_id=A),
        _event(E, parent_id=D),
    )
    fs.write(f"events/event-00003-{D}.json", _unregistered_payload(D, parent_id=A))

    log = EventLog(fs)

    # Without the raw parent_id, the idx-1 fallback would land on C and splice
    # the sibling branch into this one.
    assert [e.id for e in log.path_to_root(E)] == [A, E]


def test_path_to_root_skips_unreadable_legacy_event():
    """A legacy (parent-less) event that cannot load falls back to the idx chain."""
    fs = _store(_event(A), _event(B), _event(C))
    fs.write(f"events/event-00001-{B}.json", _unregistered_payload(B))

    log = EventLog(fs)

    assert [e.id for e in log.path_to_root(C)] == [A, C]


def test_path_to_root_skips_unreadable_leaf():
    """The unreadable event may be the leaf itself, not only an interior node."""
    fs = _store(_event(A), _event(B, parent_id=A))
    fs.write(f"events/event-00001-{B}.json", _unregistered_payload(B, parent_id=A))

    log = EventLog(fs)

    assert [e.id for e in log.path_to_root(B)] == [A]


def test_unreadable_event_with_unknown_parent_falls_back_to_linear_chain():
    """A recovered parent_id is only trusted if it names an event we hold.

    The raw payload failed validation, so its parent_id is not to be trusted
    blindly: an id that is not in the log would raise KeyError out of the walk
    and strand the conversation all over again.
    """
    fs = _store(_event(A), _event(B, parent_id=A), _event(C, parent_id=B))
    fs.write(
        f"events/event-00001-{B}.json",
        _unregistered_payload(B, parent_id="ffffffff"),  # no such event
    )

    log = EventLog(fs)

    assert [e.id for e in log.path_to_root(C)] == [A, C]


def test_unreadable_event_warns_once_across_repeated_walks(caplog):
    """``active_branch()`` re-walks the branch every agent step, so one bad event
    must not warn on every walk for the life of the conversation."""
    fs = _store(_event(A), _event(B, parent_id=A), _event(C, parent_id=B))
    fs.write(f"events/event-00001-{B}.json", _unregistered_payload(B, parent_id=A))

    log = EventLog(fs)
    with caplog.at_level(logging.WARNING):
        log.path_to_root(C)
        log.path_to_root(C)

    warnings = [r for r in caplog.records if "Skipping unreadable event" in r.message]
    assert len(warnings) == 1
    assert log._unreadable_parents == {1: A}  # parent recovered once, then reused
