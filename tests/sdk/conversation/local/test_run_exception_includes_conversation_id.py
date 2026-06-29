import tempfile

import pytest

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.exceptions import ISSUE_URL, ConversationRunError
from openhands.sdk.conversation.impl.local_conversation import (
    _agent_already_surfaced_error,
)
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent


class FailingAgent(AgentBase):
    def step(
        self,
        conversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ):  # noqa: D401, ARG002
        """Intentionally fail to simulate an unexpected runtime error."""
        raise ValueError("boom")


class SelfEmittingFailingAgent(AgentBase):
    """Mimics ACPAgent: surfaces its own typed ConversationErrorEvent, then raises.

    ACPAgent._emit_turn_error emits a detailed, classified
    ``ConversationErrorEvent(source="agent")`` and re-raises so the run loop tears
    down. The run loop must not then append a second, generic duplicate.
    """

    def step(
        self,
        conversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ):  # noqa: D401, ARG002
        """Surface a rich typed error, then raise the underlying exception."""
        on_event(
            ConversationErrorEvent(
                source="agent",
                code="ACPPromptError",
                detail="[-32603] Internal error: model 'x' not found",
            )
        )
        raise ValueError("Internal error")


def test_run_raises_conversation_run_error_with_id():
    llm = LLM(model="gpt-4o-mini", api_key=None, usage_id="test-llm")
    agent = FailingAgent(llm=llm, tools=[])

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        with pytest.raises(ConversationRunError) as excinfo:
            conv.run()

        err = excinfo.value
        # carries the conversation id
        assert getattr(err, "conversation_id", None) == conv.id
        # message should include the id for visibility in logs/tracebacks
        assert str(conv.id) in str(err)
        # original exception preserved via chaining
        assert isinstance(getattr(err, "original_exception", None), ValueError)


def test_run_error_includes_persistence_dir_and_issue_url():
    """Test that ConversationRunError includes persistence_dir and issue URL."""
    llm = LLM(model="gpt-4o-mini", api_key=None, usage_id="test-llm")
    agent = FailingAgent(llm=llm, tools=[])

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        with pytest.raises(ConversationRunError) as excinfo:
            conv.run()

        err = excinfo.value
        error_message = str(err)

        # persistence_dir should be set
        assert err.persistence_dir is not None
        # persistence_dir should include the conversation ID (as hex)
        assert conv.id.hex in err.persistence_dir
        # persistence_dir should be in the error message
        assert err.persistence_dir in error_message
        # issue URL should be in the error message
        assert ISSUE_URL in error_message
        # should mention conversation logs
        assert "Conversation logs are stored at:" in error_message
        # should mention filing a bug report
        assert "file a bug report" in error_message


class TestAgentAlreadySurfacedError:
    """Unit tests for the run-loop dedup guard."""

    def test_empty_events(self):
        assert _agent_already_surfaced_error([]) is False

    def test_latest_error_is_agent_sourced(self):
        events = [
            ConversationErrorEvent(source="agent", code="ACPPromptError", detail="d"),
        ]
        assert _agent_already_surfaced_error(events) is True

    def test_latest_error_is_environment_sourced(self):
        events = [
            ConversationErrorEvent(source="environment", code="ValueError", detail="d"),
        ]
        assert _agent_already_surfaced_error(events) is False

    def test_uses_most_recent_error_event(self):
        # If a later environment error follows an agent one, the most recent wins.
        events = [
            ConversationErrorEvent(source="agent", code="ACPPromptError", detail="a"),
            ConversationErrorEvent(source="environment", code="ValueError", detail="b"),
        ]
        assert _agent_already_surfaced_error(events) is False

    def test_scans_past_trailing_non_error_events(self):
        # A non-error event after the agent's error must not hide it.
        events = [
            ConversationErrorEvent(source="agent", code="ACPPromptError", detail="a"),
            MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text="trailing")]
                ),
            ),
        ]
        assert _agent_already_surfaced_error(events) is True

    def test_since_excludes_stale_prior_run_events(self):
        # A source="agent" error from a prior run must not suppress the generic
        # event for a new, unrelated exception in a subsequent run on the same
        # conversation (state.events accumulates across runs).
        stale_agent_event = ConversationErrorEvent(
            source="agent", code="ACPPromptError", detail="from prior run"
        )
        events = [stale_agent_event]
        since = len(events)  # start of the new run — no events added yet
        assert _agent_already_surfaced_error(events, since) is False

    def test_since_still_finds_current_run_agent_event(self):
        stale_agent_event = ConversationErrorEvent(
            source="agent", code="ACPPromptError", detail="from prior run"
        )
        current_run_agent_event = ConversationErrorEvent(
            source="agent", code="ACPAuthRequired", detail="from current run"
        )
        events = [stale_agent_event, current_run_agent_event]
        since = 1  # new run started after the stale event
        assert _agent_already_surfaced_error(events, since) is True


def test_run_does_not_duplicate_agent_emitted_error():
    """When the agent surfaces its own typed error and re-raises, the run loop
    must not append a generic str(exc) duplicate that would clobber it in clients
    showing the most recent error."""
    llm = LLM(model="gpt-4o-mini", api_key=None, usage_id="test-llm")
    agent = SelfEmittingFailingAgent(llm=llm, tools=[])

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, workspace=tmpdir)
        with pytest.raises(ConversationRunError):
            conv.run()

        errors = [e for e in conv.state.events if isinstance(e, ConversationErrorEvent)]
        assert len(errors) == 1, f"expected one error event, got {errors}"
        assert errors[0].source == "agent"
        assert errors[0].code == "ACPPromptError"
        assert "model 'x' not found" in errors[0].detail


def test_run_emits_generic_error_when_agent_did_not():
    """Control: an agent that raises WITHOUT self-emitting still gets the run
    loop's generic ConversationErrorEvent (regular-agent behaviour unchanged)."""
    llm = LLM(model="gpt-4o-mini", api_key=None, usage_id="test-llm")
    agent = FailingAgent(llm=llm, tools=[])

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, workspace=tmpdir)
        with pytest.raises(ConversationRunError):
            conv.run()

        errors = [e for e in conv.state.events if isinstance(e, ConversationErrorEvent)]
        assert len(errors) == 1
        assert errors[0].source == "environment"
        assert errors[0].code == "ValueError"


def test_run_error_without_persistence_dir():
    """Test that ConversationRunError works without persistence_dir."""
    llm = LLM(model="gpt-4o-mini", api_key=None, usage_id="test-llm")
    agent = FailingAgent(llm=llm, tools=[])

    with tempfile.TemporaryDirectory() as tmpdir:
        # No persistence_dir set
        conv = Conversation(agent=agent, workspace=tmpdir)

        with pytest.raises(ConversationRunError) as excinfo:
            conv.run()

        err = excinfo.value
        error_message = str(err)

        # persistence_dir should be None
        assert err.persistence_dir is None
        # issue URL should NOT be in the error message when no persistence_dir
        assert ISSUE_URL not in error_message
        # should still have conversation id
        assert str(conv.id) in error_message
