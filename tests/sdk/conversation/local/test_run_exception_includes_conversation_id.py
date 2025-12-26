import tempfile

import pytest

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.exceptions import ISSUE_URL, ConversationRunError
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.llm import LLM


class FailingAgent(AgentBase):
    def step(
        self,
        conversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ):  # noqa: D401, ARG002
        """Intentionally fail to simulate an unexpected runtime error."""
        raise ValueError("boom")


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
