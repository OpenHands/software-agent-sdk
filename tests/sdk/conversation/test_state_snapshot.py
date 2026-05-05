"""Tests for ConversationState.snapshot() method."""

import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.workspace.local import LocalWorkspace


def _agent() -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test"),
        tools=[],
    )


def _msg(event_id: str, text: str = "hi") -> MessageEvent:
    return MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=text)]),
        source="user",
    )


def _make_state(
    tags: dict[str, str] | None = None,
) -> ConversationState:
    """Create a ConversationState with sensible defaults for testing."""
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=_agent(),
        workspace=LocalWorkspace(working_dir="/tmp"),
        tags=tags,
    )


def test_snapshot_creates_new_id():
    """Snapshot must have a distinct ID from the source."""
    state = _make_state()
    snap = state.snapshot()

    assert snap.id != state.id
    assert isinstance(snap.id, uuid.UUID)


def test_snapshot_with_explicit_id():
    """Explicit conversation_id is honoured."""
    custom_id = uuid.uuid4()
    state = _make_state()
    snap = state.snapshot(conversation_id=custom_id)

    assert snap.id == custom_id


def test_snapshot_deep_copies_events():
    """Events in the snapshot should be deep copies, not shared references."""
    state = _make_state()
    state.events.append(_msg("evt-1", "hello"))
    state.events.append(_msg("evt-2", "world"))

    snap = state.snapshot()

    snap_ids = [e.id for e in snap.events]
    assert "evt-1" in snap_ids
    assert "evt-2" in snap_ids

    # Objects should not be shared
    assert snap.events[0] is not state.events[0]


def test_snapshot_event_mutation_isolation():
    """Mutating an event in the snapshot must not affect the source."""
    state = _make_state()
    state.events.append(_msg("deep-evt", "original"))

    snap = state.snapshot()

    snap.events[0].llm_message.content[0].text = "mutated"  # type: ignore[union-attr]
    assert state.events[0].llm_message.content[0].text == "original"  # type: ignore[union-attr]


def test_snapshot_source_events_unmodified():
    """Appending to the snapshot must not affect the source."""
    state = _make_state()
    state.events.append(_msg("src-evt"))
    src_count = len(state.events)

    snap = state.snapshot()
    snap.events.append(_msg("snap-only"))

    assert len(state.events) == src_count


def test_snapshot_deep_copies_agent_state():
    """agent_state should be deep-copied."""
    state = _make_state()
    state.agent_state = {"key": "value", "nested": {"a": 1}}

    snap = state.snapshot()

    assert snap.agent_state == {"key": "value", "nested": {"a": 1}}
    # Mutation on snapshot should not affect source
    snap.agent_state["nested"]["a"] = 99
    assert state.agent_state["nested"]["a"] == 1


def test_snapshot_shallow_copies_skills():
    """activated_knowledge_skills and invoked_skills should be shallow copies."""
    state = _make_state()
    state.activated_knowledge_skills = ["skill-a", "skill-b"]
    state.invoked_skills = ["invoke-x"]

    snap = state.snapshot()

    assert snap.activated_knowledge_skills == ["skill-a", "skill-b"]
    assert snap.invoked_skills == ["invoke-x"]

    # Lists should be independent
    snap.activated_knowledge_skills.append("skill-c")
    assert "skill-c" not in state.activated_knowledge_skills

    snap.invoked_skills.append("invoke-y")
    assert "invoke-y" not in state.invoked_skills


def test_snapshot_shallow_copies_tags():
    """tags should be a shallow dict copy."""
    state = _make_state(tags={"env": "prod", "owner": "alice"})

    snap = state.snapshot()

    assert snap.tags == {"env": "prod", "owner": "alice"}
    # Mutation on snapshot should not affect source
    snap.tags = {**snap.tags, "new": "tag"}
    assert "new" not in state.tags


def test_snapshot_copies_stats_by_default():
    """By default, stats should be deep-copied."""
    state = _make_state()
    m = Metrics()
    m.accumulated_cost = 2.5
    state.stats.usage_to_metrics["test"] = m

    snap = state.snapshot()

    combined = snap.stats.get_combined_metrics()
    assert combined.accumulated_cost == pytest.approx(2.5)

    # Mutation on snapshot should not affect source
    snap.stats.usage_to_metrics["test"].accumulated_cost = 99.0
    assert state.stats.usage_to_metrics["test"].accumulated_cost == pytest.approx(2.5)


def test_snapshot_reset_metrics():
    """When reset_metrics=True, stats should be fresh."""
    state = _make_state()
    m = Metrics()
    m.accumulated_cost = 5.0
    state.stats.usage_to_metrics["test"] = m

    snap = state.snapshot(reset_metrics=True)

    combined = snap.stats.get_combined_metrics()
    assert combined.accumulated_cost == 0


def test_snapshot_execution_status_is_idle():
    """Snapshot should always start in IDLE status."""
    state = _make_state()
    state.execution_status = ConversationExecutionStatus.RUNNING

    snap = state.snapshot()

    assert snap.execution_status == ConversationExecutionStatus.IDLE


def test_snapshot_shares_agent_reference():
    """Agent should be a shared reference (immutable Pydantic model)."""
    state = _make_state()

    snap = state.snapshot()

    assert snap.agent is state.agent


def test_snapshot_shares_workspace_reference():
    """Workspace should be a shared reference."""
    state = _make_state()

    snap = state.snapshot()

    assert snap.workspace is state.workspace


def test_snapshot_uses_provided_file_store():
    """When file_store is provided, it should back the snapshot's event log."""
    state = _make_state()
    state.events.append(_msg("evt-1"))

    custom_fs = InMemoryFileStore()
    snap = state.snapshot(file_store=custom_fs)

    assert snap._fs is custom_fs
    assert len(snap.events) == 1


def test_snapshot_preserves_blocked_actions():
    """blocked_actions should be copied."""
    state = _make_state()
    state.block_action("act-1", "blocked by hook")

    snap = state.snapshot()

    assert snap.blocked_actions == {"act-1": "blocked by hook"}
    # Should be independent
    snap.block_action("act-2", "another block")
    assert "act-2" not in state.blocked_actions


def test_snapshot_preserves_last_user_message_id():
    """last_user_message_id should carry over."""
    state = _make_state()
    state.last_user_message_id = "msg-42"

    snap = state.snapshot()

    assert snap.last_user_message_id == "msg-42"


def test_snapshot_has_fresh_lock():
    """The snapshot should have its own lock, not the source's."""
    state = _make_state()

    snap = state.snapshot()

    assert snap._lock is not state._lock
    assert not snap.locked()


def test_snapshot_has_no_state_change_callback():
    """The snapshot should not inherit the source's on_state_change callback."""
    state = _make_state()
    state.set_on_state_change(lambda _: None)

    snap = state.snapshot()

    assert snap._on_state_change is None


def test_snapshot_overrides_agent():
    """Passing agent= replaces the agent on the copy."""
    state = _make_state()
    alt = Agent(
        llm=LLM(model="gpt-4o", api_key=SecretStr("k2"), usage_id="alt"),
        tools=[],
    )

    snap = state.snapshot(agent=alt)

    assert snap.agent is alt
    assert state.agent is not alt


def test_snapshot_overrides_persistence_dir():
    """Passing persistence_dir= replaces it on the copy."""
    state = _make_state()

    snap = state.snapshot(persistence_dir="/tmp/other")

    assert snap.persistence_dir == "/tmp/other"
    assert state.persistence_dir != "/tmp/other"


def test_snapshot_overrides_tags():
    """Passing tags= replaces the tags dict on the copy."""
    state = _make_state()
    state.tags = {"old": "val"}

    snap = state.snapshot(tags={"env": "test"})

    assert snap.tags == {"env": "test"}
    assert state.tags == {"old": "val"}
