"""run() ends when a step sets ERROR without raising (#6645).

ACPAgent handles an idle prompt timeout that way: it emits the timeout message
and sets ERROR. run() used to loop and call step() again, which sent the same
prompt to the ACP server until stuck detection or max_iteration_per_run.
"""

import tempfile

import pytest

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.llm import LLM


class ErrorStatusAgent(AgentBase):
    def step(
        self,
        conversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ):  # noqa: ARG002
        conversation.state.agent_state = {
            **conversation.state.agent_state,
            "steps": conversation.state.agent_state.get("steps", 0) + 1,
        }
        conversation.state.execution_status = ConversationExecutionStatus.ERROR


@pytest.mark.parametrize("stuck_detection", [True, False])
def test_run_stops_after_a_step_that_sets_error(stuck_detection: bool):
    llm = LLM(model="gpt-4o-mini", api_key=None, usage_id="test-llm")
    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=ErrorStatusAgent(llm=llm, tools=[]),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            stuck_detection=stuck_detection,
            max_iteration_per_run=5,
        )
        conversation.send_message("go")
        conversation.run()

        assert conversation.state.agent_state["steps"] == 1
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

        # A later run() starts again from ERROR, as before.
        conversation.run()
        assert conversation.state.agent_state["steps"] == 2
