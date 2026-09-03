"""A user message sent while a step is in flight must be picked up."""

import asyncio
import json
import threading

import pytest
from pydantic import SecretStr

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
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool import Action, Observation


MID_STEP_MESSAGE = "sent while the step was in flight"


def _llm() -> LLM:
    return LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")


def _user_messages(conversation: LocalConversation) -> list[str]:
    return [
        event.llm_message.content[0].text  # type: ignore[union-attr]
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "user"
    ]


def _send_and_wait(conversation: LocalConversation, text: str) -> bool:
    """Send from another thread and join; True if it completed."""
    thread = threading.Thread(target=conversation.send_message, args=(text,))
    thread.start()
    thread.join(timeout=10.0)
    return not thread.is_alive()


class _LLMWindowAgent(AgentBase):
    """Sends a user message from the window ``Agent.astep`` releases the lock in.

    ``Agent.astep`` hands the state lock back around the provider round-trip, so
    a message can be appended there. It lands while the status is still RUNNING,
    which is why nothing resets the FINISHED the step goes on to set.
    """

    def __init__(self) -> None:
        super().__init__(llm=_llm(), tools=[])
        self._steps: list[list[str]] = []

    @property
    def steps(self) -> list[list[str]]:
        return self._steps

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
        raise NotImplementedError

    async def astep(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
        prompt_message: MessageEvent | None = None,
    ) -> None:
        self._steps.append(_user_messages(conversation))
        async with conversation._released_state_lock_during_io():
            if len(self._steps) == 1:
                assert await asyncio.to_thread(
                    _send_and_wait, conversation, MID_STEP_MESSAGE
                )
        on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text="ok")]),
            )
        )
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED


@pytest.mark.asyncio
async def test_arun_picks_up_message_sent_during_the_llm_call(tmp_path):
    """A message landing during the LLM round-trip must not be dropped."""
    agent = _LLMWindowAgent()
    conversation = Conversation(
        agent=agent, workspace=str(tmp_path), max_iteration_per_run=5
    )
    assert isinstance(conversation, LocalConversation)
    conversation.send_message("first")

    await conversation.arun()

    assert len(agent.steps) == 2, (
        f"expected a second step for the mid-step message, saw {agent.steps}"
    )
    assert agent.steps[0] == ["first"]
    assert agent.steps[1] == ["first", MID_STEP_MESSAGE]
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


class _ToolCallAction(Action):
    command: str


class _ToolCallObservation(Observation):
    result: str

    @property
    def to_llm_content(self):
        return [TextContent(text=self.result)]


class _ToolCallAgent(AgentBase):
    """Emits one action, waits as a tool would, then emits its observation."""

    def __init__(self) -> None:
        super().__init__(llm=_llm(), tools=[])
        self._intake_completed_mid_tool_call = False

    @property
    def intake_completed_mid_tool_call(self) -> bool:
        return self._intake_completed_mid_tool_call

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
        action = ActionEvent(
            source="agent",
            thought=[TextContent(text="t")],
            action=_ToolCallAction(command="sleep"),
            tool_name="bash",
            tool_call_id="call_1",
            tool_call=MessageToolCall(
                id="call_1",
                name="bash",
                arguments=json.dumps({"command": "sleep"}),
                origin="completion",
            ),
            llm_response_id="resp_1",
        )
        on_event(action)
        # The tool is "executing" here.
        self._intake_completed_mid_tool_call = _send_and_wait(
            conversation, MID_STEP_MESSAGE
        )
        on_event(
            ObservationEvent(
                source="environment",
                observation=_ToolCallObservation(result="done"),
                action_id=action.id,
                tool_name="bash",
                tool_call_id="call_1",
            )
        )
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED


def test_user_message_never_splits_a_tool_call(tmp_path):
    """Intake must not append between an action and its observation.

    Providers require a tool result to follow its tool call directly. A user
    message spliced in between yields assistant(tool_calls) -> user -> tool.
    """
    agent = _ToolCallAgent()
    conversation = Conversation(
        agent=agent, workspace=str(tmp_path), max_iteration_per_run=1
    )
    assert isinstance(conversation, LocalConversation)
    conversation.send_message("go")

    conversation.run()

    assert not agent.intake_completed_mid_tool_call, (
        "send_message() completed while a tool call was in flight; it can now "
        "split the action/observation pair"
    )
    order = [type(event).__name__ for event in conversation.state.events]
    action_at = order.index("ActionEvent")
    observation_at = order.index("ObservationEvent")
    assert observation_at == action_at + 1, (
        f"an event was appended between the action and its observation: {order}"
    )
