"""Tests for Conversation.fork() primitive."""

import tempfile
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent


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


def test_fork_creates_new_id():
    """Forked conversation must have a distinct ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert fork.id != src.id
        assert isinstance(fork.id, uuid.UUID)


def test_fork_with_explicit_id():
    """Explicit conversation_id is honoured."""
    custom_id = uuid.uuid4()
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork(conversation_id=custom_id)

        assert fork.id == custom_id


def test_fork_copies_events():
    """Events from the source must appear in the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg("evt-1", "hello"))
        src.state.events.append(_msg("evt-2", "world"))

        fork = src.fork()

        # The fork should have at least the events we added
        fork_ids = [e.id for e in fork.state.events]
        assert "evt-1" in fork_ids
        assert "evt-2" in fork_ids


def test_fork_source_unmodified():
    """Appending to the fork must not affect the source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg("src-evt"))
        src_event_count = len(src.state.events)

        fork = src.fork()
        fork.state.events.append(_msg("fork-only"))

        # Source should not grow
        assert len(src.state.events) == src_event_count


def test_fork_execution_status_is_idle():
    """Forked conversation starts in idle status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert fork.state.execution_status == ConversationExecutionStatus.IDLE


def test_fork_resets_metrics_by_default():
    """By default, metrics on the fork should be fresh (empty)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        combined = fork.state.stats.get_combined_metrics()
        assert combined.accumulated_cost == 0


def test_fork_preserves_metrics_when_requested():
    """When reset_metrics=False the fork should carry over stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        # Inject a non-zero metric
        from openhands.sdk.llm.utils.metrics import Metrics

        m = Metrics()
        m.accumulated_cost = 1.5
        src._state.stats.usage_to_metrics["test"] = m

        fork = src.fork(reset_metrics=False)

        combined = fork.state.stats.get_combined_metrics()
        assert combined.accumulated_cost == pytest.approx(1.5)


def test_fork_copies_agent_state():
    """agent_state dict should be carried over to the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src._state.agent_state = {"key": "value"}

        fork = src.fork()

        assert fork.state.agent_state == {"key": "value"}
        # Mutation on fork should not affect source
        fork._state.agent_state = {**fork._state.agent_state, "new": True}
        assert "new" not in src._state.agent_state


def test_fork_copies_invoked_skills():
    """invoked_skills should be carried over and isolated in the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src._state.invoked_skills = ["skill-a", "skill-b"]

        fork = src.fork()

        assert fork._state.invoked_skills == ["skill-a", "skill-b"]
        # Mutation on fork should not affect source
        fork._state.invoked_skills.append("skill-c")
        assert "skill-c" not in src._state.invoked_skills


def test_fork_stuck_detector_references_fork_state():
    """After fork, stuck_detector must point to the fork's state, not the source's."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            stuck_detection=True,
        )
        fork = src.fork()

        assert fork.stuck_detector is not None
        assert fork.stuck_detector.state is fork._state


def test_fork_accepts_replacement_agent():
    """Providing an agent kwarg replaces the source agent in the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        alt_agent = Agent(
            llm=LLM(
                model="gpt-4o",
                api_key=SecretStr("other-key"),
                usage_id="alt",
            ),
            tools=[],
        )

        fork = src.fork(agent=alt_agent)

        assert fork.agent.llm.model == "gpt-4o"
        # Source should keep its original agent
        assert src.agent.llm.model == "gpt-4o-mini"


def test_fork_with_tags():
    """Tags should be passed through to the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork(tags={"env": "test"})

        assert fork.state.tags.get("env") == "test"


def test_fork_with_title_sets_tag():
    """Title is stored as a 'title' tag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork(title="My Fork")

        assert fork.state.tags.get("title") == "My Fork"


def test_fork_shares_workspace():
    """Fork should reuse the same workspace as the source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert fork.workspace.working_dir == src.workspace.working_dir


def test_fork_event_deep_copy_isolation():
    """Mutating an event object in the fork must not affect the source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg("deep-evt", "original"))

        fork = src.fork()

        # The fork event is a different object
        src_evt = src.state.events[0]
        fork_evt = fork.state.events[0]
        assert src_evt is not fork_evt

        # Mutating the fork event should not change the source
        assert fork_evt.llm_message.content[0].text == "original"  # type: ignore[union-attr]
        fork_evt.llm_message.content[0].text = "mutated"  # type: ignore[union-attr]
        assert src_evt.llm_message.content[0].text == "original"  # type: ignore[union-attr]


def test_fork_persistence_path_no_doubling():
    """Fork persistence dir must be a sibling of source, not nested inside it.

    Regression test: fork() previously computed the persistence path with
    the conversation hex appended, but __init__ also appends it via
    get_persistence_dir(), leading to /base/FORK_HEX/FORK_HEX.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert src._state.persistence_dir is not None
        assert fork._state.persistence_dir is not None
        src_path = Path(src._state.persistence_dir)
        fork_path = Path(fork._state.persistence_dir)

        # Both should live directly under the same base directory
        assert src_path.parent == fork_path.parent
        # The fork dir should be <base>/<fork_id_hex>, not doubled
        assert fork_path.name == fork.id.hex


def test_fork_persisted_events_survive_reload():
    """Events persisted by fork() should be loadable from the fork dir.

    This validates the path-doubling fix end-to-end: if the fork wrote
    events to the wrong directory, resuming from the correct path would
    see zero events.
    """
    # Event IDs must be hex+dash, ≥8 chars to match EVENT_NAME_RE.
    evt_id_1 = uuid.uuid4().hex
    evt_id_2 = uuid.uuid4().hex

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg(evt_id_1, "hello"))
        src.state.events.append(_msg(evt_id_2, "world"))

        fork = src.fork()
        fork_id = fork.id

        # The fork should have the events in-memory
        assert len(fork.state.events) == 2

        # Close the fork to flush persistence, then reopen from disk
        fork.close()

        resumed = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            conversation_id=fork_id,
        )
        resumed_ids = [e.id for e in resumed.state.events]
        assert evt_id_1 in resumed_ids
        assert evt_id_2 in resumed_ids


def test_fork_default_does_not_clobber_source_cache_key():
    """Default fork() must leave the source's prompt_cache_key intact (#2917)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src_key_before = src.agent.llm._prompt_cache_key

        fork = src.fork()

        assert src.agent.llm._prompt_cache_key == src_key_before == str(src.id)
        assert fork.agent.llm._prompt_cache_key == str(fork.id)
        assert fork.agent.llm._prompt_cache_key != src.agent.llm._prompt_cache_key


def test_fork_with_aliased_agent_does_not_clobber_source_cache_key():
    """fork(agent=source.agent) must not repin the source LLM's cache key (#2917)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src_key_before = src.agent.llm._prompt_cache_key

        fork = src.fork(agent=src.agent)

        assert src.agent.llm._prompt_cache_key == src_key_before == str(src.id)
        assert fork.agent.llm._prompt_cache_key == str(fork.id)
        assert fork.agent.llm is not src.agent.llm
