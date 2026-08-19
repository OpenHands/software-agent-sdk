"""Integration tests for the conversation tree: stamping, leaf, view, navigate,
and branch-slice fork through a real LocalConversation (no LLM). (#3747, #3748)
"""

import json
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import Function
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context.view import View
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.event.types import ROOT_PARENT_ID, SourceType
from openhands.sdk.event.user_action import PauseEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool.schema import Action, Observation


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


def _by_id(events, event_id: str) -> Event:
    """Look up a stored event by id."""
    return next(e for e in events if e.id == event_id)


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
        assert _by_id(conv.state.events, e3.id).parent_id == e0.id

        # Abandoned branch A is still on disk...
        assert e1.id in conv.state.events
        assert e2.id in conv.state.events
        # ...but absent from the active view.
        view_ids = _view_ids(conv)
        assert e1.id not in view_ids and e2.id not in view_ids
        assert view_ids == [e0.id, e3.id]

        # Both branches hang off the root as siblings.
        assert _by_id(conv.state.events, e1.id).parent_id == e0.id
        assert _by_id(conv.state.events, e3.id).parent_id == e0.id


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
        stored = _by_id(events, fresh.id)
        # Effective parent is None: a genuine root, not chained to a1.
        assert events._effective_parent_id(events.get_index(fresh.id), stored) is None
        # Active branch is exactly the fresh root; branch A stays off-view.
        assert _view_ids(conv) == [fresh.id]
        # Both roots hang off None as siblings: e0 is a genuine root, and the
        # fresh event carries the explicit ROOT_PARENT_ID sentinel.
        assert _by_id(events, e0.id).parent_id is None
        assert _by_id(events, fresh.id).parent_id == ROOT_PARENT_ID


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
        assert _by_id(fork.state.events, e3.id).parent_id == e1.id


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
        assert _by_id(resumed.state.events, new.id).parent_id == legacy[-1].id
        assert resumed.state.leaf_event_id == new.id
        assert [e.id for e in resumed.state.events.path_to_root(new.id)] == [
            *(e.id for e in legacy),
            new.id,
        ]


@pytest.mark.parametrize(
    "make_tail",
    [
        pytest.param(
            lambda parent_id: ConversationErrorEvent(
                source="environment",
                parent_id=parent_id,
                code="RuntimeError",
                detail="interrupted startup",
            ),
            id="conversation-error-tail",
        ),
        pytest.param(
            lambda parent_id: ConversationStateUpdateEvent(
                parent_id=parent_id,
                key="execution_status",
                value="idle",
            ),
            id="state-update-tail",
        ),
    ],
)
def test_legacy_resume_with_non_tree_artifact_tail_keeps_history(make_tail):
    """A legacy log whose stored tail is a non-tree artifact still resumes whole.

    A newer agent-server can write a ``ConversationStateUpdateEvent`` or
    ``ConversationErrorEvent`` onto a legacy tail during an interrupted startup.
    That artifact carries a ``parent_id``, but it is not a real HEAD: the first
    post-resume event must chain onto the last real legacy event, not be stamped
    a false ``__root__`` that strands all prior history (#4057).
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp, delete_on_close=False)
        # Legacy flat log (no parent_id, leaf never advances) — pre-tree shape.
        legacy = [_msg(f"legacy-{i}") for i in range(3)]
        for ev in legacy:
            conv.state.events.append(ev)
        # Trailing artifact chained onto the legacy tail, as a newer server writes
        # it: carries a parent_id but is not a tree node.
        conv.state.events.append(make_tail(legacy[-1].id))
        conv_id = conv.id
        conv.close()

        resumed = _conversation(tmp, conversation_id=conv_id, delete_on_close=False)
        assert resumed.state.leaf_event_id is None
        # The artifact is skipped; the active branch is the full legacy history.
        assert _view_ids(resumed) == [e.id for e in legacy]
        assert _ground_truth_view_ids(resumed) == [e.id for e in legacy]

        # The first new event chains onto the last legacy event, NOT a false root.
        new = _emit(resumed, _msg("after-resume"))
        assert _by_id(resumed.state.events, new.id).parent_id == legacy[-1].id
        assert resumed.state.leaf_event_id == new.id
        assert [e.id for e in resumed.state.events.path_to_root(new.id)] == [
            *(e.id for e in legacy),
            new.id,
        ]


@pytest.mark.parametrize(
    "make_tail",
    [
        pytest.param(
            lambda pid: [
                ConversationStateUpdateEvent(parent_id=pid, key="k", value="v"),
                ConversationErrorEvent(
                    source="environment", parent_id=pid, code="E", detail="d"
                ),
                ConversationStateUpdateEvent(parent_id=pid, key="k2", value="v2"),
            ],
            id="multiple-stacked-artifacts",
        ),
        pytest.param(
            lambda pid: [PauseEvent(parent_id=pid)],
            id="tail-type-not-special-cased",
        ),
    ],
)
def test_legacy_resume_never_orphans_history_regardless_of_tail(make_tail):
    """No trailing non-tree noise on a legacy log can orphan prior history.

    Whatever a newer server appended onto the legacy tail during an interrupted
    startup — several stacked artifacts, or an event type ``_resolve_active_leaf``
    does not special-case — the first post-resume event must not be stamped a
    false ``__root__``: every legacy event stays reachable from the new leaf
    (#4057).
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp, delete_on_close=False)
        legacy = [_msg(f"legacy-{i}") for i in range(3)]
        for ev in legacy:
            conv.state.events.append(ev)
        for ev in make_tail(legacy[-1].id):
            conv.state.events.append(ev)
        conv_id = conv.id
        conv.close()

        resumed = _conversation(tmp, conversation_id=conv_id, delete_on_close=False)
        new = _emit(resumed, _msg("after-resume"))
        stored = _by_id(resumed.state.events, new.id)
        reachable = [e.id for e in resumed.state.events.path_to_root(new.id)]

        legacy_ids = [e.id for e in legacy]
        assert stored.parent_id != ROOT_PARENT_ID  # not a false root
        # Every legacy event is still reachable, in order (nothing orphaned).
        assert [i for i in reachable if i in set(legacy_ids)] == legacy_ids
        assert reachable[-1] == new.id


def _make_kind_unregistered(tmpdir: str, event_id: str) -> None:
    """Rewrite the stored event so its ``kind`` cannot be resolved in this process.

    What a custom tool's event looks like on disk when the module that defines it
    was never imported: the envelope and its ``parent_id`` are intact, the ``kind``
    naming the payload type is unknown (#4080).
    """
    path = next(Path(tmpdir).rglob(f"*{event_id}.json"))
    payload = json.loads(path.read_text())
    # For an observation the unknown kind is the nested one; the envelope is fine.
    target = payload.get("observation", payload)
    target["kind"] = "CanvasUIObservation"
    path.write_text(json.dumps(payload))


def test_legacy_resume_with_unreadable_tail_keeps_history():
    """A legacy log whose stored tail cannot be deserialized still resumes (#4080).

    With no persisted leaf, ``_resolve_active_leaf`` scans back from the tail for
    the last real event. Reading that tail strictly would fail the entire load on
    one event whose kind this process does not know, and the agent-server drops
    the whole conversation on any load exception.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp, delete_on_close=False)
        # Legacy flat log (no parent_id, leaf never advances) — pre-tree shape.
        legacy = [_msg(f"legacy-{i}") for i in range(3)]
        for ev in legacy:
            conv.state.events.append(ev)
        conv_id = conv.id
        conv.close()
        _make_kind_unregistered(tmp, legacy[-1].id)

        resumed = _conversation(tmp, conversation_id=conv_id, delete_on_close=False)

        # The unreadable tail is skipped; the rest of the history survives.
        assert resumed.state.leaf_event_id is None
        assert _view_ids(resumed) == [e.id for e in legacy[:-1]]


def test_resume_drops_action_left_unpaired_by_an_unreadable_observation():
    """Skipping an observation must not leave its action unpaired in the view.

    The view is what is sent to the LLM, and an action with no matching
    observation is rejected by the API, so dropping only the observation would
    trade a dead conversation for a failing one. ``ToolCallMatchingProperty``
    drops the orphaned action (#4080).
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp, delete_on_close=False)
        first = _emit(conv, _msg("draw a chart"))
        action = _action_event()
        _emit(conv, action)  # emitting preserves the id, so `action` stays valid
        observation = _emit(conv, _observation_event(action))
        last = _emit(conv, _msg("done", source="agent"))
        conv_id = conv.id
        conv.close()
        _make_kind_unregistered(tmp, observation.id)

        resumed = _conversation(tmp, conversation_id=conv_id, delete_on_close=False)

        # All four events are on disk; the view keeps the two that still pair up.
        assert len(resumed.state.events) == 4
        assert _view_ids(resumed) == [first.id, last.id]


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


def test_append_event_stamps_swapped_event_to_active_leaf_not_storage_tail():
    """An unstamped event (as a hook swaps in downstream of _tree_stamping) is
    stamped to the active leaf at append_event — not left to the idx-1 fallback,
    which after a navigate would chain it onto the abandoned branch.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        _emit(conv, _msg("a1"))
        _emit(conv, _msg("a2"))  # branch A (abandoned); a2 is the storage tail

        conv.navigate_to(e0.id)  # active leaf is e0, but storage tail is a2

        swapped = _msg("swapped")  # brand-new event, parent_id is None
        with conv._state:
            conv._state.append_event(swapped)

        stored = _by_id(conv.state.events, swapped.id)
        assert stored.parent_id == e0.id  # active leaf, not a2 (idx - 1)
        assert conv.state.leaf_event_id == swapped.id
        assert _view_ids(conv) == [e0.id, swapped.id]  # branch A stays off-view


class _MockAction(Action):
    command: str


class _MockObservation(Observation):
    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text=self.result)]


def _action_event(call_id: str = "call_1") -> ActionEvent:
    """A minimal executable ActionEvent — pending until a matching observation."""
    tool_call = ChatCompletionMessageToolCall(
        id=call_id,
        type="function",
        function=Function(name="test_tool", arguments='{"command": "x"}'),
    )
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="t")],
        action=_MockAction(command="x"),
        tool_name="test_tool",
        tool_call_id=call_id,
        tool_call=MessageToolCall.from_chat_tool_call(tool_call),
        llm_response_id="resp-1",
    )


def _observation_event(action: ActionEvent) -> ObservationEvent:
    """The observation that pairs with ``action`` and settles it."""
    return ObservationEvent(
        source="environment",
        observation=_MockObservation(result="ok"),
        action_id=action.id,
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
    )


def test_active_branch_returns_live_path_and_tail():
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        _emit(conv, _msg("a1"))
        _emit(conv, _msg("a2"))  # branch A, to be abandoned
        conv.navigate_to(e0.id)
        e3 = _emit(conv, _msg("b1"))  # branch B

        # Only the live path; abandoned a1/a2 excluded.
        assert [e.id for e in conv.state.active_branch()] == [e0.id, e3.id]
        # limit walks back from the leaf.
        assert [e.id for e in conv.state.active_branch(limit=1)] == [e3.id]


def test_pending_actions_ignore_abandoned_branch():
    """get_unmatched_actions over the active branch drops an abandoned branch's
    orphaned action — so navigating away can't leave phantom pending actions.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        e0 = _emit(conv, _msg("root"))
        pending = _emit(conv, _action_event("call_A"))  # unmatched on branch A

        conv.navigate_to(e0.id)  # abandon branch A

        # The full log still shows the orphaned action...
        assert [
            a.id
            for a in ConversationState.get_unmatched_actions(list(conv.state.events))
        ] == [pending.id]
        # ...but the active branch (what the consumers now read) does not.
        assert ConversationState.get_unmatched_actions(conv.state.active_branch()) == []


def test_navigate_to_none_empties_single_root_tree():
    """navigate_to(None) must empty even a one-event tree, and the next event must
    be a fresh root — not chained onto the abandoned one. (The legacy fallback in
    _resolve_active_leaf used to resurrect a lone root.)
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        _emit(conv, _msg("only"))
        conv.navigate_to(None)
        assert conv.state.leaf_event_id is None
        assert conv.state.head_is_empty is True
        assert _view_ids(conv) == []

        fresh = _emit(conv, _msg("fresh"))
        assert _by_id(conv.state.events, fresh.id).parent_id == ROOT_PARENT_ID
        assert _view_ids(conv) == [fresh.id]


def test_empty_head_survives_reload():
    """A deliberate empty HEAD persists across save/reload (head_is_empty), instead
    of being resurrected by the legacy fallback on cold load.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp, delete_on_close=False)
        _emit(conv, _msg("only"))
        conv.navigate_to(None)
        cid = conv.id
        conv.close()

        resumed = _conversation(tmp, conversation_id=cid, delete_on_close=False)
        assert resumed.state.head_is_empty is True
        assert resumed.state.leaf_event_id is None
        assert _view_ids(resumed) == []


def test_generate_title_reads_active_branch(monkeypatch):
    """generate_title() extracts the first user message from the active branch, not
    an abandoned branch's message.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        _emit(conv, _msg("old root"))
        _emit(conv, _msg("reply", "agent"))  # 2-event prefix so navigate empties
        conv.navigate_to(None)
        _emit(conv, _msg("fresh root"))

        captured: dict = {}

        def _fake_generate(events, llm, max_length=50):
            captured["events"] = list(events)
            return "title"

        monkeypatch.setattr(
            "openhands.sdk.conversation.impl.local_conversation."
            "generate_conversation_title",
            _fake_generate,
        )
        conv.generate_title()

        texts = [
            c.text
            for e in captured["events"]
            if isinstance(e, MessageEvent)
            for c in e.llm_message.content
            if isinstance(c, TextContent)
        ]
        assert texts == ["fresh root"]


def test_fork_preserves_empty_head():
    """A full fork of an empty-HEAD conversation stays empty (head_is_empty is
    copied), instead of the legacy fallback resurrecting the last event.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conv = _conversation(tmp)
        _emit(conv, _msg("only"))
        conv.navigate_to(None)

        fork = conv.fork()
        assert fork.state.head_is_empty is True
        assert _view_ids(fork) == []
