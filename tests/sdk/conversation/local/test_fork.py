"""Tests for Conversation.fork() primitive."""

import tempfile
import uuid

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
