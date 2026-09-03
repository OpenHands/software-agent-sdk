"""A user message sent while a step is in flight must be picked up."""

import asyncio
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
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent


SECOND_MESSAGE = "sent while the step was in flight"


class _MessageDuringStepAgent(AgentBase):
    """Finishes on every step; sends a user message from inside the first.

    Sent from a separate thread and joined, so the test never sleeps: if intake
    were serialized behind the step, the join would time out.
    """

    def __init__(self) -> None:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        super().__init__(llm=llm, tools=[])
        self._steps: list[list[str]] = []
        self._intake_completed_during_step = False

    @property
    def steps(self) -> list[list[str]]:
        """User-message texts seen at the start of each step."""
        return self._steps

    @property
    def intake_completed_during_step(self) -> bool:
        return self._intake_completed_during_step

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        on_event(
            SystemPromptEvent(
                source="agent", system_prompt=TextContent(text="dummy"), tools=[]
            )
        )

    def _record_step(self, conversation: LocalConversation) -> None:
        self._steps.append(
            [
                event.llm_message.content[0].text  # type: ignore[union-attr]
                for event in conversation.state.events
                if isinstance(event, MessageEvent) and event.source == "user"
            ]
        )

    def _send_from_another_thread(self, conversation: LocalConversation) -> None:
        thread = threading.Thread(
            target=conversation.send_message, args=(SECOND_MESSAGE,)
        )
        thread.start()
        thread.join(timeout=10.0)
        self._intake_completed_during_step = not thread.is_alive()

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        self._record_step(conversation)
        if len(self._steps) == 1:
            self._send_from_another_thread(conversation)
        on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text="ok")]),
            )
        )
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED

    async def astep(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
        prompt_message: MessageEvent | None = None,
    ) -> None:
        await asyncio.to_thread(self.step, conversation, on_event, on_token)


class _MessageDuringLLMCallAgent(_MessageDuringStepAgent):
    """Sends the message from the released-lock window ``Agent.astep`` uses."""

    async def astep(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
        prompt_message: MessageEvent | None = None,
    ) -> None:
        self._record_step(conversation)
        async with conversation._released_state_lock_during_io():
            if len(self._steps) == 1:
                await asyncio.to_thread(self._send_from_another_thread, conversation)
        on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text="ok")]),
            )
        )
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED


def _make_conversation(
    tmp_path, agent_cls: type[_MessageDuringStepAgent] = _MessageDuringStepAgent
) -> tuple[LocalConversation, _MessageDuringStepAgent]:
    agent = agent_cls()
    conversation = Conversation(
        agent=agent, workspace=str(tmp_path), max_iteration_per_run=5
    )
    assert isinstance(conversation, LocalConversation)
    conversation.send_message("first")
    return conversation, agent


def _assert_picked_up(agent: _MessageDuringStepAgent) -> None:
    assert agent.intake_completed_during_step, (
        "send_message() blocked until the step finished; intake is still "
        "serialized behind the run loop"
    )
    assert len(agent.steps) == 2, (
        f"expected a second step for the mid-step message, saw {agent.steps}"
    )
    assert agent.steps[0] == ["first"]
    assert agent.steps[1] == ["first", SECOND_MESSAGE]


def test_sync_run_picks_up_message_sent_during_step(tmp_path):
    conversation, agent = _make_conversation(tmp_path)
    conversation.run()
    _assert_picked_up(agent)
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


@pytest.mark.asyncio
async def test_arun_picks_up_message_sent_during_step(tmp_path):
    conversation, agent = _make_conversation(tmp_path)
    await conversation.arun()
    _assert_picked_up(agent)
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_state_lock_is_free_while_a_step_runs(tmp_path):
    """``agent_step()`` hands the state lock back for the step's duration."""
    conversation, _ = _make_conversation(tmp_path)
    state = conversation._state
    with state:
        assert state.owned()
        with state.agent_step():
            assert not state.owned()
            assert state._agent_lock.owned()
        assert state.owned()
    assert not state.locked()


@pytest.mark.asyncio
async def test_arun_picks_up_message_sent_during_the_llm_call(tmp_path):
    """A message landing during the LLM round-trip must not be dropped."""
    conversation, agent = _make_conversation(tmp_path, _MessageDuringLLMCallAgent)
    await conversation.arun()
    _assert_picked_up(agent)
