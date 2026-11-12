"""Lifecycle flows that must keep working through ConversationState.transition."""

from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent


def _llm() -> LLM:
    return LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")


class StatusFlowErrorThenFinishAgent(AgentBase):
    """Reports a failed turn without raising, then finishes on the retry.

    ACPAgent does this on a prompt timeout: it sets ERROR and returns, and the
    sync run loop steps again.
    """

    def __init__(self) -> None:
        super().__init__(llm=_llm(), tools=[])

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        on_event(
            SystemPromptEvent(
                source="agent", system_prompt=TextContent(text="dummy"), tools=[]
            )
        )

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        state = conversation.state
        if not any(
            isinstance(e, MessageEvent) and e.source == "agent" for e in state.events
        ):
            on_event(
                MessageEvent(
                    source="agent",
                    llm_message=Message(
                        role="assistant", content=[TextContent(text="timed out")]
                    ),
                )
            )
            state.transition(ConversationExecutionStatus.ERROR, "turn timed out")
            return
        state.transition(ConversationExecutionStatus.FINISHED, "turn finished")


def test_sync_run_finishes_after_a_turn_reported_error_without_raising():
    conversation = Conversation(agent=StatusFlowErrorThenFinishAgent())
    conversation.send_message("hi")

    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_direct_step_on_errored_conversation_finishes_blocked_message():
    conversation = Conversation(agent=Agent(llm=_llm(), tools=[]))
    assert isinstance(conversation, LocalConversation)
    conversation._ensure_agent_ready()
    conversation.send_message("blocked by a hook")
    state = conversation.state
    assert state.last_user_message_id is not None
    state.block_message(state.last_user_message_id, "blocked")
    state.execution_status = ConversationExecutionStatus.ERROR

    with state:
        conversation.agent.step(conversation, on_event=conversation._on_event)

    assert state.execution_status == ConversationExecutionStatus.FINISHED
