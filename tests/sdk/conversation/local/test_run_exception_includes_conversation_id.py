import tempfile

import pytest

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.exceptions import ConversationRunError
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
