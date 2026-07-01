"""Integration tests for the conversation tree: stamping, leaf, view, navigate,
and branch-slice fork through a real LocalConversation (no LLM). (#3747, #3748)
"""

import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context.view import View
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm import LLM, Message, TextContent


def _agent() -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test"),
        tools=[],
    )


def _conversation(tmpdir: str, **kwargs) -> LocalConversation:
    conv = Conversation(
        agent=_agent(),
        persistence_dir=tmpdir,
        workspace=tmpdir,
        visualizer=None,
        **kwargs,
    )
    assert isinstance(conv, LocalConversation)
    return conv


def _emit(conv: LocalConversation, event: Event) -> Event:
    """Emit through the stamping pipeline; return the event (id is preserved)."""
    with conv._state:
        conv._on_event(event)
    return event


def _msg(text: str, source: SourceType = "user") -> MessageEvent:
    role = "user" if source == "user" else "assistant"
    return MessageEvent(
        source=source,
        llm_message=Message(role=role, content=[TextContent(text=text)]),
    )


def _view_ids(conv: LocalConversation) -> list[str]:
    return [e.id for e in conv.state.view.events]


def _ground_truth_view_ids(conv: LocalConversation) -> list[str]:
    """The view computed from scratch — independent of the cached/incremental one."""
    leaf = conv.state._resolve_active_leaf()
    branch = conv.state.events.path_to_root(leaf)
    return [e.id for e in View.from_events(branch).events]


def test_parent_id_stamped_and_leaf_advances():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("first"))
        e1 = _emit(conv, _msg("second"))
        e2 = _emit(conv, _msg("third"))

        stored = {e.id: e for e in conv.state.events}
        assert stored[e0.id].parent_id is None  # root
        assert stored[e1.id].parent_id == e0.id  # chains to previous leaf
        assert stored[e2.id].parent_id == e1.id
        assert conv.state.leaf_event_id == e2.id


def test_leaf_event_id_round_trips_through_base_state():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        _emit(conv, _msg("a"))
        e1 = _emit(conv, _msg("b"))
        conv_id = conv.id
        conv.close()

        resumed = _conversation(tmp, conversation_id=conv_id)
        assert resumed.state.leaf_event_id == e1.id
        # Active branch is restored intact.
        assert _view_ids(resumed) == [e.id for e in resumed.state.events]


def test_view_reflects_only_the_active_branch():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        e1 = _emit(conv, _msg("a1"))
        e2 = _emit(conv, _msg("a2"))
        assert _view_ids(conv) == [e0.id, e1.id, e2.id]


def test_navigate_then_emit_creates_sibling_branch():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        e1 = _emit(conv, _msg("a1"))
        e2 = _emit(conv, _msg("a2"))  # branch A: root -> a1 -> a2

        conv.navigate_to(e0.id)  # move HEAD back to the root
        assert conv.state.leaf_event_id == e0.id
        assert _view_ids(conv) == [e0.id]

        e3 = _emit(conv, _msg("b1"))  # branch B forks off the root
        assert conv.state.events.get_by_id(e3.id).parent_id == e0.id

        # Abandoned branch A is still on disk...
        assert e1.id in conv.state.events
        assert e2.id in conv.state.events
        # ...but absent from the active view.
        view_ids = _view_ids(conv)
        assert e1.id not in view_ids and e2.id not in view_ids
        assert view_ids == [e0.id, e3.id]

        # Both branches hang off the root as siblings.
        assert set(conv.state.events.children_of(e0.id)) == {e1.id, e3.id}


@pytest.mark.parametrize(
    "operation",
    [
        lambda conv: conv.navigate_to("nope"),
        lambda conv: conv.fork(from_event_id="nope"),
    ],
    ids=["navigate_to", "fork"],
)
def test_unknown_event_id_raises(operation: Callable[[LocalConversation], object]):
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        _emit(conv, _msg("root"))
        with pytest.raises(ValueError, match="nope"):
            operation(conv)


def test_navigate_to_none_empties_the_active_branch():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        _emit(conv, _msg("root"))
        _emit(conv, _msg("a1"))

        conv.navigate_to(None)
        assert conv.state.leaf_event_id is None
        assert _view_ids(conv) == []


def test_navigate_to_none_then_emit_starts_a_fresh_root():
    """After navigate_to(None), the next event is a genuine root — not silently
    re-parented onto the abandoned branch's leaf.

    A stamped root landing at a non-zero storage index has parent_id=None, the
    same shape as a legacy event; without an explicit marker the effective-parent
    rule would treat it as a legacy child (idx-1) and resurrect the whole
    abandoned branch into the active view.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        _emit(conv, _msg("a1"))  # branch A: root -> a1, then abandoned

        conv.navigate_to(None)  # deliberate empty HEAD
        assert _view_ids(conv) == []

        fresh = _emit(conv, _msg("fresh"))  # a new root over a non-empty log

        events = conv.state.events
        stored = events.get_by_id(fresh.id)
        # Effective parent is None: a genuine root, not chained to a1.
        assert events._effective_parent_id(events.get_index(fresh.id), stored) is None
        # Active branch is exactly the fresh root; branch A stays off-view.
        assert _view_ids(conv) == [fresh.id]
        # Both roots hang off None as siblings.
        assert set(events.children_of(None)) == {e0.id, fresh.id}


def test_fork_from_event_slices_the_branch():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        e1 = _emit(conv, _msg("a"))
        _emit(conv, _msg("b"))  # e2, not on the sliced branch

        fork = conv.fork(from_event_id=e1.id)

        # Exactly path_to_root(e1) is copied, HEAD set at the cut point.
        assert [e.id for e in fork.state.events] == [e0.id, e1.id]
        assert fork.state.leaf_event_id == e1.id
        assert _view_ids(fork) == [e0.id, e1.id]

        # Source conversation is untouched.
        assert len(conv.state.events) == 3

        # Running the fork continues from the cut point.
        e3 = _emit(fork, _msg("c"))
        assert fork.state.events.get_by_id(e3.id).parent_id == e1.id


def test_fork_after_condensation_replays_correctly():
    """Forking from a condensation event replays it on the sliced branch."""
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("m0"))
        e1 = _emit(conv, _msg("m1"))
        e2 = _emit(conv, _msg("m2"))

        cond = Condensation(
            forgotten_event_ids={e0.id},
            summary="dropped m0",
            llm_response_id="resp-1",
        )
        _emit(conv, cond)

        # On the source, the active view already reflects the condensation.
        assert e0.id not in _view_ids(conv)

        fork = conv.fork(from_event_id=cond.id)

        # The whole branch up to and including the condensation is copied...
        assert len(fork.state.events) == 4
        # ...and replaying it drops the forgotten event while keeping the rest.
        fork_view_ids = _view_ids(fork)
        assert e0.id not in fork_view_ids
        assert e1.id in fork_view_ids and e2.id in fork_view_ids


def test_default_fork_is_unchanged_full_copy():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        e1 = _emit(conv, _msg("a1"))
        _emit(conv, _msg("a2"))
        conv.navigate_to(e0.id)  # diverge the HEAD before forking

        fork = conv.fork()  # no from_event_id -> full copy, HEAD preserved

        assert len(fork.state.events) == len(conv.state.events) == 3
        assert fork.state.leaf_event_id == e0.id  # source HEAD is inherited
        assert _view_ids(fork) == [e0.id]
        assert e1.id in fork.state.events  # abandoned branch copied too


def test_incremental_view_matches_ground_truth_across_branch_switches():
    """Cached ``state.view`` must equal a from-scratch rebuild after every step.

    Reading the view between mutations pins the incremental fast path and the
    branch-switch rebuild against ``View.from_events``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)

        e0 = _emit(conv, _msg("root"))
        assert _view_ids(conv) == _ground_truth_view_ids(conv)  # first populate
        e1 = _emit(conv, _msg("a1"))
        e2 = _emit(conv, _msg("a2"))
        assert _view_ids(conv) == _ground_truth_view_ids(conv)  # linear fast path

        conv.navigate_to(e0.id)  # branch switch -> full rebuild
        assert _view_ids(conv) == _ground_truth_view_ids(conv) == [e0.id]

        _emit(conv, _msg("b1"))  # extend sibling branch via fast path off e0
        assert _view_ids(conv) == _ground_truth_view_ids(conv)

        conv.navigate_to(e2.id)  # back to the abandoned branch's leaf
        assert _view_ids(conv) == _ground_truth_view_ids(conv) == [e0.id, e1.id, e2.id]


def test_legacy_conversation_resumes_and_continues():
    """Events persisted without parent_id load as one branch and continue (resume)."""
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp, delete_on_close=False)
        # Append directly to the log: bypasses the stamping chokepoint, so these
        # events get no parent_id and never advance the leaf — exactly the shape
        # of data written before the tree feature existed.
        legacy = [_msg(f"legacy-{i}") for i in range(3)]
        for ev in legacy:
            conv.state.events.append(ev)
        conv_id = conv.id
        conv.close()

        resumed = _conversation(tmp, conversation_id=conv_id, delete_on_close=False)
        # No persisted leaf, no parent_ids on disk...
        assert resumed.state.leaf_event_id is None
        assert all(e.parent_id is None for e in resumed.state.events)
        # ...yet the full linear history is the active branch.
        assert _view_ids(resumed) == [e.id for e in legacy]
        assert _ground_truth_view_ids(resumed) == [e.id for e in legacy]

        # A new event seamlessly continues the chain off the last legacy event.
        new = _emit(resumed, _msg("after-resume"))
        assert resumed.state.events.get_by_id(new.id).parent_id == legacy[-1].id
        assert resumed.state.leaf_event_id == new.id
        assert [e.id for e in resumed.state.events.path_to_root(new.id)] == [
            *(e.id for e in legacy),
            new.id,
        ]


def test_parent_id_round_trips_on_disk_and_root_omits_it():
    """parent_id survives persistence; the root event's JSON omits it (additive)."""
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp, delete_on_close=False)
        e0 = _emit(conv, _msg("root"))
        e1 = _emit(conv, _msg("child"))
        conv_id = conv.id
        persist = Path(conv.state.persistence_dir)  # type: ignore[arg-type]
        conv.close()

        # Byte-additive: a root event (parent_id None) is serialized without the
        # key at all; a child event carries parent_id pointing at its parent.
        root_json = next(persist.rglob(f"event-*-{e0.id}.json")).read_text()
        child_json = next(persist.rglob(f"event-*-{e1.id}.json")).read_text()
        assert '"parent_id"' not in root_json
        assert '"parent_id"' in child_json
        assert e0.id in child_json

        # And it survives the round-trip back into memory.
        resumed = _conversation(tmp, conversation_id=conv_id, delete_on_close=False)
        stored = {e.id: e for e in resumed.state.events}
        assert stored[e0.id].parent_id is None
        assert stored[e1.id].parent_id == e0.id


def test_fork_from_event_on_an_abandoned_branch():
    """from_event_id slices by lineage, even off a branch that is not the HEAD."""
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        e1 = _emit(conv, _msg("a1"))
        e2 = _emit(conv, _msg("a2"))  # branch A (will be abandoned)

        conv.navigate_to(e0.id)
        _emit(conv, _msg("b1"))  # HEAD now on branch B, A is abandoned

        # Fork from a2 (on the abandoned branch) — independent of the live HEAD.
        fork = conv.fork(from_event_id=e2.id)
        assert [e.id for e in fork.state.events] == [e0.id, e1.id, e2.id]
        assert fork.state.leaf_event_id == e2.id
        assert _view_ids(fork) == _ground_truth_view_ids(fork) == [e0.id, e1.id, e2.id]
